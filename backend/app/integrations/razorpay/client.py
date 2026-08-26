"""Razorpay Test Mode client — real REST integration via httpx.

Rules (deliberate, non-negotiable):
- Credentials come ONLY from env vars. Never hardcoded, never logged.
- TEST MODE ONLY: keys starting with `rzp_live_` are refused with a clear error.
- Missing credentials → RazorpayNotConfiguredError with the exact user-facing
  message. Callers degrade gracefully; the rest of REVORA keeps working.
- No real-money capability exists in this client: it creates ORDERS and READS
  payments/orders. Capturing/charging APIs are intentionally absent.
"""
from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import get_settings

logger = logging.getLogger("revora.razorpay")

DEFAULT_BASE_URL = "https://api.razorpay.com/v1"
NOT_CONFIGURED_MESSAGE = (
    "Razorpay integration not configured — add Razorpay Test Mode credentials to .env."
)
TIMEOUT_SECONDS = 10.0


class RazorpayError(Exception):
    """Base error for the Razorpay integration."""


class RazorpayNotConfiguredError(RazorpayError):
    def __init__(self):
        super().__init__(NOT_CONFIGURED_MESSAGE)


class RazorpayLiveModeError(RazorpayError):
    def __init__(self):
        super().__init__(
            "Razorpay LIVE keys detected — REVORA is Test Mode only by design. "
            "Refusing to proceed."
        )


class RazorpayAPIError(RazorpayError):
    """Razorpay returned an error or the transport failed."""

    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"Razorpay API error {status}: {detail}")


class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str, *, base_url: str = DEFAULT_BASE_URL,
                 transport: httpx.BaseTransport | None = None):
        self._key_id = key_id
        self._auth = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._transport = transport  # injectable for tests
        self._base_url = base_url.rstrip("/")

    # ---- construction / configuration ----

    @classmethod
    def from_settings(cls, settings=None) -> "RazorpayClient":
        settings = settings or get_settings()
        if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
            raise RazorpayNotConfiguredError()
        if settings.RAZORPAY_KEY_ID.startswith("rzp_live_"):
            raise RazorpayLiveModeError()
        base_url = getattr(settings, "RAZORPAY_BASE_URL", "") or DEFAULT_BASE_URL
        return cls(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET,
                   base_url=base_url)

    @property
    def key_id(self) -> str:  # non-secret prefix, safe for logs/UI
        return self._key_id

    @property
    def mode(self) -> str:
        return "test" if self._key_id.startswith("rzp_test_") else "unknown"

    # ---- transport ----

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Basic {self._auth}",
                "Content-Type": "application/json",
            },
            "timeout": TIMEOUT_SECONDS,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        try:
            with httpx.Client(**kwargs) as client:
                response = client.request(method, url, json=json_body, params=params)
        except httpx.HTTPError as exc:
            raise RazorpayAPIError(0, f"transport failure: {exc}") from exc

        if response.status_code >= 400:
            # Never log auth headers or full payloads on error surfaces.
            try:
                detail = str(response.json().get("error", {}).get("description", response.text)[:300])
            except Exception:
                detail = response.text[:300]
            raise RazorpayAPIError(response.status_code, detail)
        return response.json()

    # ---- API surface (intentionally minimal + read/create only) ----

    def create_order(self, *, amount_paise: int, currency: str = "INR",
                     receipt: str | None = None, notes: dict | None = None) -> dict[str, Any]:
        """Create an order (test mode). A 'retry' in REVORA = a fresh order the
        customer can complete — Razorpay never lets a server re-charge a card
        without customer authorization, and neither do we."""
        if amount_paise <= 0:
            raise ValueError("amount_paise must be positive")
        return self._request("POST", "/orders", json_body={
            "amount": amount_paise, "currency": currency,
            "receipt": receipt, "notes": notes or {},
        })

    def get_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/orders/{quote(order_id, safe='')}")

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{quote(payment_id, safe='')}")

    def ping(self) -> dict[str, Any]:
        """Light credential check used by the verify endpoint (read-only)."""
        return self._request("GET", "/payments", params={"count": 1})


_client: RazorpayClient | None = None


def get_client() -> RazorpayClient:
    """Cached client factory. Raises RazorpayNotConfiguredError when unset."""
    global _client
    if _client is None:
        _client = RazorpayClient.from_settings()
    return _client


def reset_client_cache() -> None:
    """Used by tests and when credentials change at runtime."""
    global _client
    _client = None
