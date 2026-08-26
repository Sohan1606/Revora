"""Razorpay webhook signature verification.

Razorpay signs webhook payloads with HMAC-SHA256 over the RAW request body using
the webhook secret; the signature arrives in the `X-Razorpay-Signature` header.
Verification must be timing-safe and must consume the raw bytes (not re-serialized JSON).

Ref: https://razorpay.com/docs/webhooks/validate-test/
"""
from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(raw_body: bytes, signature: str, webhook_secret: str) -> bool:
    """Return True iff `signature` is the valid HMAC-SHA256 of `raw_body`."""
    if not webhook_secret or not signature:
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
