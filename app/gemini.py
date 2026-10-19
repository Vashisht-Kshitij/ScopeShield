"""Official Gemini REST adapter with bounded, tool-free structured extraction."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

from app.extraction import ExtractionError, UnavailableProvider, build_messages
from app.resources import setting

DEFAULT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
DEFAULT_MODEL = DEFAULT_MODELS[0]
VERIFIED_MODELS = (*DEFAULT_MODELS, "gemini-3.8-flash")
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_INPUT_CHARACTERS = 20_000
MAX_PROMPT_BYTES = 32_000
MAX_RESPONSE_BYTES = 512 * 1024
MAX_OUTPUT_TOKENS = 8192
MAX_ELAPSED_SECONDS = 45
logger = logging.getLogger(__name__)


def input_eligibility(text):
    size = sum(len(item["content"].encode("utf-8")) for item in build_messages(text))
    eligible = len(text) <= MAX_INPUT_CHARACTERS and size <= MAX_PROMPT_BYTES and size + 128 <= setting("AI_MAX_INPUT_TOKENS", 32768, maximum=32768)
    return eligible, ("Eligible for configured extraction input limits; live access and usage allowances are checked separately" if eligible else
        "Stored successfully, but configured extraction exceeds its 20,000-character, 32,000-byte prompt or configured input reservation limit. Upload a shorter complete agreement; text is never truncated automatically")


class GeminiRequestError(ExtractionError):
    def __init__(self, code, message, status_code=502, *, fallback_allowed=False):
        super().__init__(code, message, status_code)
        self.fallback_allowed = fallback_allowed


class GeminiProvider:
    name = "google_gemini"
    live_ai = True

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, allow_live: bool = False, max_attempts: int = 1,
                 timeout_seconds: int = 30, deadline: float | None = None):
        self._api_key = api_key.strip()
        self.model = model
        self._allow_live = allow_live
        if type(max_attempts) is not int or max_attempts not in {1, 2}:
            raise ExtractionError("AI_CONFIGURATION_INVALID", "AI request-attempt limit is invalid", 503)
        # Legacy callers may still supply 2; it never authorizes a repeated POST.
        self.max_attempts = 1
        if type(timeout_seconds) is not int or not 5 <= timeout_seconds <= 30:
            raise ExtractionError("AI_CONFIGURATION_INVALID", "AI timeout is invalid", 503)
        self.timeout_seconds = timeout_seconds
        self._deadline = deadline
        self.last_usage = None
        self.response_model = None
        self.last_attempt = None
        self.budget = None
        if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]{1,70}", model):
            raise ExtractionError("AI_CONFIGURATION_INVALID", "AI provider configuration is invalid", 503)

    def extract(self, text: str) -> str:
        return self._generate(build_messages(text), len(text))

    def compare_scope(self, contract_text: str, requested_changes: list[str]) -> str:
        from app.scope_comparison import build_scope_messages
        return self._generate(
            build_scope_messages(contract_text, requested_changes),
            len(contract_text) + sum(map(len, requested_changes)),
        )

    def compare_conversation(self, contract_text, conversation):
        from app.scope_comparison import build_scope_messages
        from app.conversations import requested_work
        return self._generate(build_scope_messages(contract_text, requested_work(conversation), conversation=conversation),
                              len(contract_text) + sum(len(item["content"]) for item in conversation))

    def _generate(self, messages: list[dict[str, str]], input_characters: int) -> str:
        self.last_usage = None
        self.response_model = None
        self.last_attempt = None
        if not self._api_key:
            raise ExtractionError("AI_PROVIDER_UNAVAILABLE", "No AI API key is configured", 503)
        if not self._allow_live:
            raise ExtractionError("AI_LIVE_REQUESTS_DISABLED", "Live AI requests require local opt-in after quota and billing review", 503)
        if input_characters > MAX_INPUT_CHARACTERS or sum(len(item["content"].encode("utf-8")) for item in messages) > MAX_PROMPT_BYTES:
            raise ExtractionError("AI_INPUT_TOO_LARGE", "Contract exceeds the bounded live AI input budget", 413)
        input_tokens = sum(len(item["content"].encode("utf-8")) for item in messages) + 128
        output_tokens = setting("AI_MAX_OUTPUT_TOKENS", MAX_OUTPUT_TOKENS, maximum=MAX_OUTPUT_TOKENS)
        if input_tokens > setting("AI_MAX_INPUT_TOKENS", 32768, maximum=32768):
            raise ExtractionError("AI_INPUT_TOO_LARGE", "Input exceeds the conservative token reservation", 413)
        payload = {
            "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
            "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
            # Match the user's verified JSON-mode REST request. The full schema
            # stays in the trusted prompt and is enforced by local Pydantic and
            # grounding validators; it is NOT sent as responseJsonSchema.
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": output_tokens},
        }
        started = time.monotonic()
        deadline = min(started + MAX_ELAPSED_SECONDS, self._deadline if self._deadline is not None else float("inf"))
        self.last_attempt = dict(model=self.model, response_model=None, http_status=None,
                                 elapsed_ms=0, error_code=None, token_usage=None,
                                 provider_status=None, diagnostic_code=None)
        attempt_id = self.budget.start(self.model, input_tokens, output_tokens) if self.budget else None
        self.attempt_id = attempt_id
        try:
            # requests' default HTTPAdapter has max_retries=0. No SDK, retry loop,
            # redirect, timeout retry or fallback exists at this transport layer.
            response = requests.request(
                "POST", f"{API_BASE}/{self.model}:generateContent",
                headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                json=payload, timeout=(min(5, max(.1, deadline - started)), min(self.timeout_seconds, max(.1, deadline - started))),
                allow_redirects=False, stream=True,
            )
            try:
                self.last_attempt["http_status"] = response.status_code
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=8192):
                    if time.monotonic() > deadline:
                        raise ExtractionError("AI_TIMEOUT", "AI provider exceeded its request time budget", 504)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ExtractionError("AI_INVALID_OUTPUT", "AI provider response exceeded the output limit")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                if response.status_code != 200:
                    self._http_failure(response.status_code, raw)
                envelope = json.loads(raw)
                usage = envelope.get("usageMetadata") if isinstance(envelope, dict) else None
                allowed = {"promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "totalTokenCount", "cachedContentTokenCount"}
                if isinstance(usage, dict):
                    self.last_usage = {key: value for key, value in usage.items()
                                       if key in allowed and type(value) is int and 0 <= value <= 100_000_000} or None
                model = envelope.get("modelVersion") if isinstance(envelope, dict) else None
                if (isinstance(model, str) and re.fullmatch(r"gemini-[a-zA-Z0-9.-]{1,100}", model)
                        and self._api_key not in model):
                    self.response_model = model
                return self._extract_text(raw)
            finally:
                response.close()
        except requests.Timeout:
            self.last_attempt["error_code"] = "AI_TIMEOUT"
            raise ExtractionError("AI_TIMEOUT", "AI provider timed out; no retry was made", 504) from None
        except requests.RequestException:
            self.last_attempt["error_code"] = "AI_PROVIDER_FAILED"
            raise ExtractionError("AI_PROVIDER_FAILED", "AI provider could not be reached") from None
        except ExtractionError as exc:
            self.last_attempt["error_code"] = exc.code
            raise
        except (ValueError, TypeError, RecursionError):
            self.last_attempt["error_code"] = "AI_INVALID_OUTPUT"
            raise ExtractionError("AI_INVALID_OUTPUT", "AI provider returned invalid JSON") from None
        except Exception:
            self.last_attempt["error_code"] = "AI_PROVIDER_FAILED"
            raise ExtractionError("AI_PROVIDER_FAILED", "AI provider failed without a completed result") from None
        finally:
            self.last_attempt.update(elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
                                     response_model=self.response_model, token_usage=self.last_usage)
            logger.info("gemini_request %s", json.dumps(self.last_attempt, sort_keys=True))
            if attempt_id:
                self.budget.finish(attempt_id, self.last_attempt)

    def _http_failure(self, status, raw):
        try:
            error = json.loads(raw).get("error", {})
            message = error.get("message", "")
            reasons = {row.get("reason") for row in error.get("details", []) if isinstance(row, dict)}
        except (ValueError, AttributeError, TypeError):
            error, message, reasons = {}, "", set()
        # Never retain arbitrary provider text. Save only known RPC statuses and
        # static diagnostic categories, even when a body echoes source or a key.
        provider_status = error.get("status")
        if provider_status in {"INVALID_ARGUMENT", "FAILED_PRECONDITION", "NOT_FOUND", "UNAUTHENTICATED",
                               "PERMISSION_DENIED", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED"}:
            self.last_attempt["provider_status"] = provider_status
        message_lower = message.lower() if isinstance(message, str) else ""
        if (status in {401, 403} or provider_status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}
                or reasons & {"API_KEY_INVALID", "API_KEY_EXPIRED", "API_KEY_SERVICE_BLOCKED"}
                or ("api key" in message_lower and any(word in message_lower for word in ("invalid", "not valid", "expired")))):
            self.last_attempt["diagnostic_code"] = "INVALID_CREDENTIALS"
            raise ExtractionError("AI_AUTH_FAILED", "AI provider authentication failed", 503)
        if status == 429 or provider_status == "RESOURCE_EXHAUSTED":
            self.last_attempt["diagnostic_code"] = "PROJECT_QUOTA_EXHAUSTED"
            raise ExtractionError("AI_QUOTA_EXCEEDED", "AI provider quota is exhausted", 429)
        if provider_status == "FAILED_PRECONDITION" or "billing" in message_lower:
            self.last_attempt["diagnostic_code"] = "BILLING_OR_PROJECT_PRECONDITION"
            raise ExtractionError("AI_REQUEST_INVALID", "AI project precondition rejected; no billing change or fallback")
        if status == 400 or provider_status == "INVALID_ARGUMENT":
            self.last_attempt["diagnostic_code"] = ("UNKNOWN_REQUEST_FIELD" if "unknown name" in message_lower
                else "RESPONSE_SCHEMA_REJECTED" if "schema" in message_lower else "INVALID_REQUEST_ARGUMENT")
            raise ExtractionError("AI_REQUEST_INVALID", "AI request or project configuration was rejected")
        if status in {502, 503, 504}:
            self.last_attempt["diagnostic_code"] = "PROVIDER_TEMPORARY"
            raise GeminiRequestError("AI_PROVIDER_TEMPORARY", "AI provider temporarily unavailable", fallback_allowed=True)
        if status == 404:
            model_missing = (error.get("status") == "NOT_FOUND" and isinstance(message, str)
                             and self.model in message and any(word in message.lower()
                             for word in ("not found", "not supported", "not available", "unavailable")))
            raise GeminiRequestError("AI_MODEL_UNAVAILABLE", "Model or endpoint unavailable; check model metadata and API version",
                                     fallback_allowed=model_missing)
        raise ExtractionError("AI_PROVIDER_FAILED", "AI provider rejected the request")

    @staticmethod
    def _extract_text(raw: bytes) -> str:
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError
            if body.get("promptFeedback", {}).get("blockReason"):
                raise ExtractionError("AI_CONTENT_BLOCKED", "AI provider blocked this input", 422)
            candidates = body.get("candidates")
            if not isinstance(candidates, list) or len(candidates) != 1:
                raise ValueError
            candidate = candidates[0]
            if candidate.get("finishReason") == "MAX_TOKENS":
                raise ExtractionError("AI_OUTPUT_TRUNCATED", "AI output reached its token limit")
            if candidate.get("finishReason") != "STOP":
                raise ExtractionError("AI_CONTENT_BLOCKED", "AI provider did not produce a completed result", 422)
            parts = candidate["content"]["parts"]
            if not isinstance(parts, list) or not parts:
                raise ValueError
            if any(not isinstance(part, dict) or set(part) - {"text", "thought", "thoughtSignature"} for part in parts):
                raise ValueError
            texts = [part["text"] for part in parts if part.get("thought") is not True]
            if not texts or any(not isinstance(value, str) for value in texts):
                raise ValueError
            return "".join(texts)
        except ExtractionError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
            raise ExtractionError("AI_INVALID_OUTPUT", "AI provider returned an invalid response") from None


class GeminiModelRouter:
    """One attempt per confirmed model; only definite availability failures fall back.

    Configuration is an operator assertion of project access/Free tier, not a claim
    that public pricing or a metadata response proves project-specific eligibility.
    """
    name = "google_gemini"
    live_ai = True

    def __init__(self, api_key: str, *, models=DEFAULT_MODELS, free_tier_models=(),
                 allow_live=False, max_model_calls=2, timeout_seconds=30):
        if (not isinstance(models, (tuple, list)) or not 1 <= len(models) <= 3
                or any(model not in VERIFIED_MODELS for model in models)
                or not isinstance(free_tier_models, (tuple, list))
                or any(model not in VERIFIED_MODELS for model in free_tier_models)
                or type(max_model_calls) is not int or not 1 <= max_model_calls <= 3):
            raise ExtractionError("AI_CONFIGURATION_INVALID", "AI model routing configuration is invalid", 503)
        # A duplicated priority entry cannot cause the same model to be tried twice.
        self.priority = tuple(dict.fromkeys(models))
        self.models = tuple(model for model in self.priority if model in free_tier_models)
        self.excluded_models = tuple(model for model in self.priority if model not in self.models)
        self._api_key, self._allow_live = api_key, allow_live
        self.max_model_calls, self.timeout_seconds = max_model_calls, timeout_seconds
        # Validate timeout and key/model format without making any request.
        GeminiProvider(api_key, model=self.priority[0], timeout_seconds=timeout_seconds)
        self.model = None
        self.response_model = None
        self.last_usage = None
        self.last_attempts = []
        self.budget = None

    def extract(self, text):
        from app.extraction import validate_extraction
        def validate(raw, provider):
            result = validate_extraction(raw, text, provider=provider, mode="configured")
            if any(issue.code in {"UNSUPPORTED_BY_SOURCE", "MILESTONE_EVIDENCE_MISMATCH"} for issue in result.review_issues):
                raise ExtractionError("AI_UNGROUNDED_OUTPUT", "Non-null extraction facts lack verified source evidence")
        return self._run(lambda provider: provider.extract(text), validate)

    def compare_scope(self, contract_text, requested_changes):
        from app.scope_comparison import validate_comparison
        def validate(raw, provider):
            result = validate_comparison(raw, contract_text, requested_changes, provider=provider, mode="configured")
            if any(flag.endswith((":UNSUPPORTED_EXCERPT", ":INSUFFICIENT_EVIDENCE")) for flag in result.review_flags):
                raise ExtractionError("AI_UNGROUNDED_OUTPUT", "Scope findings lack verified source evidence")
        return self._run(lambda provider: provider.compare_scope(contract_text, requested_changes), validate)

    def compare_conversation(self, contract_text, conversation):
        from app.scope_comparison import validate_comparison
        from app.conversations import requested_work
        def validate(raw, provider):
            result = validate_comparison(raw, contract_text, requested_work(conversation), provider=provider,
                                         mode="configured", conversation=conversation)
            if any(flag.endswith((":UNSUPPORTED_EXCERPT", ":INSUFFICIENT_EVIDENCE")) for flag in result.review_flags):
                raise ExtractionError("AI_UNGROUNDED_OUTPUT", "Scope findings lack verified source evidence")
        return self._run(lambda provider: provider.compare_conversation(contract_text, conversation), validate)

    def _run(self, operation, validate):
        self.last_attempts, self.last_usage, self.response_model, self.model = [], None, None, None
        if not self._allow_live:
            raise ExtractionError("AI_LIVE_REQUESTS_DISABLED", "Live AI requests require explicit local opt-in", 503)
        if not self.models:
            raise ExtractionError("AI_MODEL_ACCESS_UNCONFIRMED", "No model has confirmed project Free-tier access", 503)
        started = time.monotonic()
        for model in self.models[:self.max_model_calls]:
            if time.monotonic() - started >= 90:
                raise ExtractionError("AI_TIMEOUT", "AI model routing time budget exhausted", 504)
            provider = GeminiProvider(self._api_key, model=model, allow_live=True, max_attempts=1,
                                      timeout_seconds=self.timeout_seconds, deadline=started + 90)
            self.model = model
            provider.budget = self.budget
            try:
                raw = operation(provider)
                validate(raw, provider)
            except ExtractionError as exc:
                if provider.last_attempt:
                    self.last_attempts.append({**provider.last_attempt, "error_code": exc.code})
                    logger.info("gemini_route %s", json.dumps(self.last_attempts[-1], sort_keys=True))
                    if self.budget and getattr(provider, "attempt_id", None):
                        self.budget.finish(provider.attempt_id, self.last_attempts[-1])
                if isinstance(exc, GeminiRequestError) and exc.fallback_allowed:
                    continue
                raise
            self.last_attempts.append(provider.last_attempt)
            self.last_usage, self.response_model = provider.last_usage, provider.response_model
            return raw
        raise ExtractionError("AI_MODELS_UNAVAILABLE", "All eligible models were unavailable within the request budget", 503)


def configured_provider():
    local = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    def setting(name, default=""):
        return os.environ.get(name, local.get(name) or default).strip()
    selected = setting("SCOPESHIELD_AI_PROVIDER", "gemini")
    if selected == "disabled":
        return UnavailableProvider()
    if selected != "gemini":
        raise ExtractionError("AI_CONFIGURATION_INVALID", "Unknown AI provider", 503)
    key = setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")
    if not key:
        return UnavailableProvider()
    try:
        max_model_calls = int(setting("SCOPESHIELD_AI_MAX_MODEL_CALLS", "2"))
        timeout_seconds = int(setting("SCOPESHIELD_AI_MODEL_TIMEOUT_SECONDS", "30"))
    except ValueError:
        raise ExtractionError("AI_CONFIGURATION_INVALID", "AI request-attempt limit is invalid", 503) from None
    priority = setting("SCOPESHIELD_AI_MODELS")
    legacy = setting("SCOPESHIELD_AI_MODEL")
    models = tuple(item.strip() for item in priority.split(",")) if priority else (legacy,) if legacy else DEFAULT_MODELS
    free_tier = tuple(item.strip() for item in setting("SCOPESHIELD_AI_FREE_TIER_MODELS").split(",") if item.strip())
    return GeminiModelRouter(key, models=models, free_tier_models=free_tier,
                             allow_live=setting("SCOPESHIELD_AI_ALLOW_LIVE").lower() == "true",
                             max_model_calls=max_model_calls, timeout_seconds=timeout_seconds)
