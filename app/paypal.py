"""PayPal OAuth and Invoicing v2, restricted to the sandbox and local buyer.

Adapted from the supplied Day 2 upgrade and the working Day 1 payload.
Never log raw provider bodies, request headers, credentials, or tokens.
"""
from __future__ import annotations

import os
import re
import threading
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import requests
from dotenv import dotenv_values

from app.models import INVOICE_ID_PATTERN

BASE_URL = "https://api-m.sandbox.paypal.com"
TIMEOUT = 25
INVOICE_STATUSES = frozenset({
    "DRAFT", "SENT", "SCHEDULED", "PAID", "MARKED_AS_PAID", "CANCELLED",
    "REFUNDED", "PARTIALLY_PAID", "PARTIALLY_REFUNDED", "UNPAID", "PAYMENT_PENDING",
    "MARKED_AS_REFUNDED", "AUTO_CANCELLED", "PAID_EXTERNAL", "REFUNDED_EXTERNAL", "SHARED",
})


class PayPalConfigurationError(Exception):
    """Missing or invalid local sandbox configuration, without secret values."""


class PayPalAPIError(Exception):
    """Only sanitized metadata may cross the API boundary."""

    def __init__(
        self, name: str, *, operation: str, upstream_status: int | None = None,
        debug_id: str | None = None, http_status: int = 502,
    ):
        self.name = name
        self.operation = operation
        self.upstream_status = upstream_status
        self.debug_id = debug_id
        self.http_status = http_status
        self.invoice_id: str | None = None
        self.completed_action: str | None = None
        super().__init__("PayPal sandbox operation failed")


def valid_invoice_id(value: object) -> bool:
    return isinstance(value, str) and 10 <= len(value) <= 85 and bool(
        re.fullmatch(INVOICE_ID_PATTERN, value)
    )


def extract_invoice_id(response: requests.Response) -> str:
    """Accept documented ID, Location, or links; never scan arbitrary body text."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and valid_invoice_id(body.get("id")):
        return body["id"]
    candidates = [response.headers.get("Location", "")]
    if isinstance(body, dict) and isinstance(body.get("links"), list):
        candidates.extend(
            link.get("href", "") for link in body["links"] if isinstance(link, dict)
        )
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        try:
            invoice_id = urlparse(candidate).path.rstrip("/").split("/")[-1]
        except ValueError:
            continue
        if valid_invoice_id(invoice_id):
            return invoice_id
    raise PayPalAPIError("INVOICE_ID_MISSING", operation="create_invoice", upstream_status=201)


class PayPalSandbox:
    mocked = False

    @property
    def recipient_email(self) -> str:
        """Approved local sandbox recipient; never expose OAuth configuration."""
        return self._buyer_email

    def __init__(self, *, client_id: str, client_secret: str, buyer_email: str):
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._buyer_email = buyer_email.strip()
        if not self._client_id or not self._client_secret or not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", self._buyer_email
        ) or self._buyer_email.lower() == "buyer@example.com":
            raise PayPalConfigurationError("Local sandbox configuration is missing or invalid")
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> PayPalSandbox:
        # Resolve beside the project, regardless of the launch working directory.
        local = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
        def value(name: str) -> str:
            return os.environ.get(name, local.get(name) or "")
        return cls(
            client_id=value("PAYPAL_CLIENT_ID"), client_secret=value("PAYPAL_CLIENT_SECRET"),
            buyer_email=value("PAYPAL_SANDBOX_BUYER_EMAIL"),
        )

    def _sensitive(self, value: str) -> bool:
        return any(secret and secret in value for secret in (
            self._client_id, self._client_secret, self._token, self._buyer_email,
        ))

    def _request(self, method: str, path: str, *, operation: str, **kwargs) -> requests.Response:
        try:
            # Do not follow redirects: credentials must stay on the sandbox API host.
            return requests.request(
                method, BASE_URL + path, timeout=TIMEOUT, allow_redirects=False, **kwargs,
            )
        except requests.Timeout:
            raise PayPalAPIError("PAYPAL_TIMEOUT", operation=operation, http_status=504) from None
        except requests.RequestException:
            raise PayPalAPIError("PAYPAL_UNAVAILABLE", operation=operation) from None

    def _check(self, response: requests.Response, accepted: set[int], operation: str) -> None:
        if response.status_code in accepted:
            return
        name, debug_id = "PAYPAL_ERROR", None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict):
            candidate = body.get("name", body.get("error"))
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", candidate) and not self._sensitive(candidate):
                name = candidate
            candidate = body.get("debug_id")
            if isinstance(candidate, str) and re.fullmatch(r"[a-fA-F0-9]{8,64}", candidate) and not self._sensitive(candidate):
                debug_id = candidate
        raise PayPalAPIError(
            name, operation=operation, upstream_status=response.status_code, debug_id=debug_id,
            http_status=404 if response.status_code == 404 and operation == "get_invoice" else 502,
        )

    def _json(self, response: requests.Response, operation: str) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise PayPalAPIError("INVALID_PAYPAL_RESPONSE", operation=operation, upstream_status=response.status_code)
        return body

    def _headers(self) -> dict[str, str]:
        with self._token_lock:
            if self._token is None or time.monotonic() >= self._token_expires_at:
                requested_at = time.monotonic()
                response = self._request(
                    "POST", "/v1/oauth2/token", operation="oauth",
                    auth=(self._client_id, self._client_secret),
                    data={"grant_type": "client_credentials"},
                    headers={"Accept": "application/json"},
                )
                self._check(response, {200}, "oauth")
                body = self._json(response, "oauth")
                token, expires_in = body.get("access_token"), body.get("expires_in")
                if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token) or isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or not 0 < expires_in <= 86400:
                    raise PayPalAPIError("INVALID_OAUTH_RESPONSE", operation="oauth", upstream_status=200)
                # Acquisition time must not extend the declared lifetime. A delayed
                # response can already be expired; never send that token to invoicing.
                if time.monotonic() >= requested_at + expires_in:
                    raise PayPalAPIError("OAUTH_TOKEN_EXPIRED", operation="oauth", upstream_status=200)
                self._token = token
                # APIMatic's auth guidance preserves half of a short token's lifetime.
                # Keep our existing 60-second margin for longer-lived tokens.
                self._token_expires_at = requested_at + expires_in - min(60, expires_in / 2)
            return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json", "Accept": "application/json"}

    def _invoice_request(self, method: str, path: str, *, operation: str, accepted: set[int], request_id: str | None = None, **kwargs) -> requests.Response:
        headers = self._headers()
        if request_id is not None:
            headers["PayPal-Request-Id"] = str(UUID(request_id))
        response = self._request(method, path, operation=operation, headers=headers, **kwargs)
        if response.status_code == 401:
            # Invalidate for the next action, but never automatically retry a mutation.
            with self._token_lock:
                if headers["Authorization"] == f"Bearer {self._token}":
                    self._token_expires_at = 0.0
        self._check(response, accepted, operation)
        return response

    def create_invoice(self, *, milestone: str, amount_usd: Decimal,
                       description: str | None = None, request_id: str | None = None,
                       invoice_number: str | None = None) -> str:
        draft = {
            "detail": {"currency_code": "USD", "note": "ScopeShield sandbox-only milestone invoice"},
            "invoicer": {"name": {"given_name": "Sandbox", "surname": "Merchant"}},
            "primary_recipients": [{"billing_info": {"email_address": self._buyer_email}}],
            "items": [{"name": milestone, "quantity": "1", "unit_amount": {
                "currency_code": "USD", "value": f"{amount_usd:.2f}",
            }}],
        }
        if description is not None:
            draft["items"][0]["description"] = description
        if invoice_number is not None:
            if not re.fullmatch(r"SS-[a-f0-9]{22}", invoice_number):
                raise PayPalAPIError("INVALID_INVOICE_NUMBER", operation="create_invoice", http_status=422)
            draft["detail"]["invoice_number"] = invoice_number
        response = self._invoice_request(
            "POST", "/v2/invoicing/invoices", operation="create_invoice", accepted={201}, json=draft, request_id=request_id,
        )
        return extract_invoice_id(response)

    def _fetch_invoice(self, invoice_id: str) -> dict:
        if not valid_invoice_id(invoice_id):
            raise PayPalAPIError("INVALID_INVOICE_ID", operation="get_invoice", http_status=422)
        response = self._invoice_request(
            "GET", f"/v2/invoicing/invoices/{invoice_id}", operation="get_invoice", accepted={200},
        )
        body = self._json(response, "get_invoice")
        if body.get("id") != invoice_id or not isinstance(body.get("status"), str) or body["status"] not in INVOICE_STATUSES:
            raise PayPalAPIError("INVALID_INVOICE_RESPONSE", operation="get_invoice", upstream_status=200)
        return body

    def get_invoice(self, invoice_id: str) -> dict:
        body = self._fetch_invoice(invoice_id)
        return self._public_invoice(body)

    def _public_invoice(self, body: dict) -> dict:
        invoice_id = body["id"]
        detail = body.get("detail")
        detail = detail if isinstance(detail, dict) else {}
        metadata = detail.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        candidate = metadata.get("recipient_view_url")
        if not candidate and isinstance(body.get("links"), list):
            candidate = next((link.get("href") for link in body["links"] if isinstance(link, dict) and link.get("rel") in {"payer-view", "recipient-view"}), None)
        recipient_url = None
        if isinstance(candidate, str) and not self._sensitive(candidate):
            try:
                parsed = urlparse(candidate)
            except ValueError:
                parsed = None
            if parsed and parsed.scheme == "https" and parsed.netloc.lower() in {"www.sandbox.paypal.com", "sandbox.paypal.com"}:
                recipient_url = candidate
        amount = body.get("amount")
        amount = amount if isinstance(amount, dict) else {}
        currency, total = amount.get("currency_code"), amount.get("value")
        return {
            "invoice_id": invoice_id, "status": body["status"],
            "currency_code": currency if isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency) else None,
            "total_amount": total if isinstance(total, str) and re.fullmatch(r"\d{1,12}(?:\.\d{1,2})?", total) else None,
            "recipient_view_url": recipient_url,
        }

    def _verify_link(self, body: dict, *, invoice_number: str, name: str, description: str, amount: Decimal,
                     recipient_email: str | None = None) -> dict:
        """Verify full merchant/recipient/reference/line-item facts before linking."""
        if recipient_email is not None and recipient_email.lower() != self._buyer_email.lower():
            raise PayPalAPIError("SANDBOX_RECIPIENT_CHANGED", operation="verify_invoice", http_status=409)
        try:
            recipients = body["primary_recipients"]
            item, = body["items"]
            total = body["amount"]
            unit = item["unit_amount"]
            valid = (
                len(recipients) == 1 and not body.get("additional_recipients")
                and recipients[0]["billing_info"]["email_address"].lower() == self._buyer_email.lower()
                and body["detail"]["invoice_number"] == invoice_number
                and item["name"] == name and item.get("description") == description
                and item["quantity"] == "1" and unit["currency_code"] == "USD"
                and isinstance(unit["value"], str) and Decimal(unit["value"]) == amount
                and total["currency_code"] == "USD" and isinstance(total["value"], str)
                and Decimal(total["value"]) == amount
            )
        except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
            valid = False
        if not valid:
            raise PayPalAPIError("INVOICE_LINK_MISMATCH", operation="verify_invoice", http_status=409)
        return self._public_invoice(body)

    def verify_milestone_invoice(self, invoice_id: str, **expected) -> dict:
        return self._verify_link(self._fetch_invoice(invoice_id), **expected)

    def find_milestone_invoice(self, **expected) -> dict | None:
        """Read-only lookup. Empty results never authorize another creation POST."""
        response = self._invoice_request(
            "POST", "/v2/invoicing/search-invoices", operation="search_invoices", accepted={200},
            params={"page": 1, "page_size": 2, "total_required": "true"},
            json={"invoice_number": expected["invoice_number"]},
        )
        body = self._json(response, "search_invoices")
        items = body.get("items")
        pages = body.get("total_pages", 1)
        if not isinstance(items, list) or len(items) > 2 or isinstance(pages, bool) or not isinstance(pages, int) or pages > 1 or pages < 0:
            raise PayPalAPIError("INCOMPLETE_INVOICE_SEARCH", operation="search_invoices")
        links = body.get("links") or []
        if not isinstance(links, list) or any(isinstance(link, dict) and link.get("rel") == "next" for link in links):
            raise PayPalAPIError("INCOMPLETE_INVOICE_SEARCH", operation="search_invoices")
        matched = []
        for item in items:
            if not isinstance(item, dict) or not valid_invoice_id(item.get("id")):
                raise PayPalAPIError("INVALID_INVOICE_SEARCH", operation="search_invoices")
            candidate = self._fetch_invoice(item["id"])
            detail = candidate.get("detail")
            if isinstance(detail, dict) and detail.get("invoice_number") == expected["invoice_number"]:
                matched.append(self._verify_link(candidate, **expected))
        if len(matched) > 1:
            raise PayPalAPIError("MULTIPLE_INVOICE_MATCHES", operation="search_invoices", http_status=409)
        return matched[0] if matched else None

    def send_invoice(self, invoice_id: str) -> None:
        # Re-check existing invoices so this endpoint cannot email another recipient.
        body = self._fetch_invoice(invoice_id)
        self._send_draft(invoice_id, body)

    def send_milestone_invoice(self, invoice_id: str, **expected) -> None:
        body = self._fetch_invoice(invoice_id)
        self._verify_link(body, **expected)
        self._send_draft(invoice_id, body)

    def _send_draft(self, invoice_id: str, body: dict) -> None:
        recipients = body.get("primary_recipients")
        if not isinstance(recipients, list) or len(recipients) != 1 or not isinstance(recipients[0], dict):
            raise PayPalAPIError("SANDBOX_RECIPIENT_MISMATCH", operation="send_invoice", http_status=409)
        billing = recipients[0].get("billing_info")
        if not isinstance(billing, dict) or str(billing.get("email_address", "")).lower() != self._buyer_email.lower() or body.get("additional_recipients"):
            raise PayPalAPIError("SANDBOX_RECIPIENT_MISMATCH", operation="send_invoice", http_status=409)
        if body["status"] != "DRAFT":
            raise PayPalAPIError("INVOICE_NOT_DRAFT", operation="send_invoice", http_status=409)
        self._invoice_request(
            "POST", f"/v2/invoicing/invoices/{invoice_id}/send", operation="send_invoice",
            accepted={200, 202, 204}, json={"send_to_recipient": True, "send_to_invoicer": False},
        )
