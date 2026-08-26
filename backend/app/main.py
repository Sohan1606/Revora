"""Application factory."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.http_hardening import install_hardening
from app.core.logging import setup_logging

_settings = get_settings()


def create_app() -> FastAPI:
    setup_logging(_settings.LOG_LEVEL)
    app = FastAPI(
        title=_settings.APP_NAME,
        version=_settings.APP_VERSION,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
        if _settings.AUTO_CREATE_TABLES:
        # from-imports only: a bare `import app.infrastructure.models` rebinds
        # the local name `app` to the module and shadows the FastAPI instance
        # (deployment-only crash found on Render — fixed + regression-tested).
        from app.infrastructure import database as _database
        from app.infrastructure import models as _models  # noqa: F401 — registers tables
        _database.Base.metadata.create_all(_database.engine)
    install_hardening(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
