"""Health endpoint — verifies app boots and DB connectivity check works."""
from __future__ import annotations


def test_health_ok(client) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "REVORA"
    assert body["checks"]["database"] == "connected"
    # Secrets must never leak through health — only booleans about configuration.
    assert isinstance(body["checks"]["razorpay_configured"], bool)


def test_openapi_served(client) -> None:
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    assert "/api/health" in r.json()["paths"]
