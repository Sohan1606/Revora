"""Application settings — every secret comes from environment variables only."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    APP_NAME: str = "REVORA"
    APP_VERSION: str = "0.1.0"
    ENV: Literal["local", "staging", "production"] = "local"

    # Database. If empty -> local SQLite file (development convenience only).
    DATABASE_URL: str = ""

    CORS_ORIGINS: str = "http://localhost:5173"
    LOG_LEVEL: str = "INFO"

    # Razorpay TEST MODE credentials (Phase 11). Empty = integration disabled.
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""
    # Optional override (tests/internal point-to-point). Defaults to Razorpay Cloud.
    RAZORPAY_BASE_URL: str = ""

    # Supabase (Phase 5).
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""

    # Local-dev JWT signing secret — used only in dev auth mode (fail-closed if empty).
    DEV_JWT_SECRET: str = ""

    # Messaging (Phase 11).
    MESSAGING_MODE: Literal["simulated", "real"] = "simulated"
    RESEND_API_KEY: str = ""

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _split_origins(cls, v: str) -> str:
        return ",".join(o.strip() for o in v.split(",") if o.strip())

    @field_validator("AUTO_CREATE_TABLES", "RATE_LIMIT_ENABLED", mode="before")
    @classmethod
    def _empty_bool(cls, v):
        """Empty env lines ('KEY=') must parse as False, not crash."""
        if v is None or v == "":
            return False
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o for o in self.CORS_ORIGINS.split(",") if o]

    @property
    def effective_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return "sqlite:///./revora_local.db"

    @property
    def is_razorpay_configured(self) -> bool:
        return bool(self.RAZORPAY_KEY_ID and self.RAZORPAY_KEY_SECRET)

    @property
    def is_razorpay_webhook_configured(self) -> bool:
        return bool(self.RAZORPAY_WEBHOOK_SECRET)

    @property
    def auth_mode(self) -> str:
        """'supabase' when any Supabase verification path is configured (local
        HS256 secret and/or provider-side via URL+service key); else 'dev'."""
        if self.SUPABASE_JWT_SECRET:
            return "supabase"
        if self.SUPABASE_URL and self.SUPABASE_SERVICE_ROLE_KEY:
            return "supabase"
        return "dev"

    # Hardening (Phase 13). Tests disable this via autouse fixture; default ON.
    RATE_LIMIT_ENABLED: bool = True

    # Production convenience (Render): create tables on first boot. Idempotent.
    AUTO_CREATE_TABLES: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
