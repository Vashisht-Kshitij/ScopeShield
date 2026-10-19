"""Read-only official Gemini model metadata. Never calls generateContent or billing.

Uses the existing local key in a header, does not log headers or raw bodies, and
does not change configuration. Result files contain only sanitized metadata.
"""
from datetime import datetime, timezone
import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import re

import requests
from dotenv import dotenv_values
from app.gemini import API_BASE, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parents[1]
FALLBACK_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fallback-models", action="store_true", help="Read metadata for the three proposed fallback models, without inference")
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    def setting(name, fallback=""):
        return (os.getenv(name) if os.getenv(name) is not None else local.get(name) or fallback).strip()
    key = setting("GEMINI_API_KEY") or setting("GOOGLE_API_KEY")
    if not key: raise SystemExit("No local Gemini key is configured")
    try: sdk_version = metadata.version("google-genai")
    except metadata.PackageNotFoundError: sdk_version = None
    configured_model = setting("SCOPESHIELD_AI_MODEL", DEFAULT_MODEL)
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]{1,70}", configured_model):
        configured_model = "invalid_model_configuration"
    selected = setting("SCOPESHIELD_AI_PROVIDER", "gemini")
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "google_genai_sdk_version": sdk_version,
        "transport": "requests REST", "requests_version": metadata.version("requests"),
        "api_base": API_BASE, "configured_provider": selected if selected in {"gemini", "disabled"} else "invalid",
        "configured_model": configured_model, "inference_requests": 0, "metadata_requests": 0}
    models = FALLBACK_MODELS if args.fallback_models else ("gemini-2.5-flash-lite",)
    def read(url, **params):
        if url not in {API_BASE, *(API_BASE + "/" + model for model in models)}:
            raise ValueError("Only the fixed model-list and metadata GET endpoints are permitted")
        result["metadata_requests"] += 1
        with requests.get(url, headers={"x-goog-api-key": key}, params=params, timeout=(5, 20), allow_redirects=False, stream=True) as response:
            chunks, size = [], 0
            for chunk in response.iter_content(8192):
                size += len(chunk)
                if size > 1024 * 1024: raise ValueError("Model metadata exceeds its size bound")
                chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            if response.status_code != 200:
                error = data.get("error", {}) if isinstance(data, dict) else {}
                return response.status_code, {"code": error.get("code"), "status": str(error.get("status", ""))[:60],
                    "message": str(error.get("message", "")).replace(key, "[redacted]")[:1000]}
            return response.status_code, data
    status, data = read(API_BASE, pageSize=1000)
    result["list_http_status"] = status
    if status == 200:
        names = [row.get("name", "") for row in data.get("models", []) if "generateContent" in row.get("supportedGenerationMethods", [])]
        result["generate_content_models"] = [name for name in names if re.fullmatch(r"models/[a-zA-Z0-9._-]{1,100}", name)]
        result["list_complete"] = not bool(data.get("nextPageToken"))
    else: result["list_error"] = data
    result["models"] = {}
    for model in models:
        status, data = read(API_BASE + "/" + model)
        result["models"][model] = {"http_status": status}
        if status != 200: result["models"][model]["error"] = data
        else:
            result["models"][model].update(methods=data.get("supportedGenerationMethods", []),
                input_token_limit=data.get("inputTokenLimit"), output_token_limit=data.get("outputTokenLimit"))
        if not args.fallback_models:
            result["day8_model_metadata_http_status"] = status
            if status != 200: result["day8_model_metadata_error"] = data
            else: result["day8_model_methods"] = data.get("supportedGenerationMethods", [])
    output = ROOT / (".local/gemini-fallback/model-metadata.json" if args.fallback_models else ".local/day9/gemini-diagnostics.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
