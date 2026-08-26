"""Regression (deployment-blocking bug found on Render): the AUTO_CREATE_TABLES
startup block used `import app.infrastructure.models`, which rebinds the local
name `app` to the module and shadows the FastAPI instance — crashing with
`module 'app' has no attribute 'middleware'` ONLY when AUTO_CREATE_TABLES=true."""
from __future__ import annotations


def test_auto_create_tables_does_not_shadow_the_fastapi_app(tmp_path, monkeypatch):
    from fastapi import FastAPI

    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.chdir(tmp_path)  # SQLite file lands in tmp, not the repo
    monkeypatch.setattr(get_settings(), "AUTO_CREATE_TABLES", True, raising=False)

    application = create_app()  # crashed here before the fix (Render-only path)

    assert isinstance(application, FastAPI)
    assert hasattr(application, "middleware")
    assert application.title == "REVORA"