"""Razorpay webhook signature verification — real HMAC-SHA256, timing-safe."""
from __future__ import annotations

import hashlib
import hmac

from app.integrations.razorpay.webhook import verify_webhook_signature

SECRET = "test_webhook_secret"
BODY = b'{"event":"payment.failed","payload":{"payment":{"entity":{"id":"pay_TEST123"}}}}'


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_accepted() -> None:
    assert verify_webhook_signature(BODY, _sign(BODY, SECRET), SECRET) is True


def test_tampered_body_rejected() -> None:
    sig = _sign(BODY, SECRET)
    tampered = BODY.replace(b"pay_TEST123", b"pay_EVIL999")
    assert verify_webhook_signature(tampered, sig, SECRET) is False


def test_wrong_secret_rejected() -> None:
    assert verify_webhook_signature(BODY, _sign(BODY, "other_secret"), SECRET) is False


def test_empty_inputs_rejected() -> None:
    assert verify_webhook_signature(BODY, "", SECRET) is False
    assert verify_webhook_signature(BODY, _sign(BODY, SECRET), "") is False


def test_signature_not_reused_across_bodies() -> None:
    # A signature for one payload must not validate a different payload (replay hardening).
    other = b'{"event":"payment.captured"}'
    assert verify_webhook_signature(other, _sign(BODY, SECRET), SECRET) is False
