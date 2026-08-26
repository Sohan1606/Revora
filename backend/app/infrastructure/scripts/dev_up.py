"""One-command local launcher: .env secret → schema → seed → API server.

Run from backend/:  python -m app.infrastructure.scripts.dev_up
Ctrl+C exits cleanly (no reloader, so no shutdown hangs).
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


def ensure_local_dev_secret() -> bool:
    """Local dev only: generate + persist DEV_JWT_SECRET if missing so sign-in
    never 503s. Production/staging stay fail-closed (no auto-secrets)."""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.DEV_JWT_SECRET:
        return True
    if settings.ENV != "local":
        return False  # fail-closed outside local

    secret = secrets.token_urlsafe(48)
    env_path = Path(".env")
    try:
        if env_path.exists():
            body = env_path.read_text(encoding="utf-8")
            # drop any existing/empty DEV_JWT_SECRET lines so exactly one remains
            lines = [l for l in body.splitlines()
                     if not l.startswith("DEV_JWT_SECRET=")]
            lines.append(f"DEV_JWT_SECRET={secret}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            example = Path(".env.example")
            base = example.read_text(encoding="utf-8") if example.exists() else ""
            lines = [l for l in base.splitlines()
                     if not l.startswith("DEV_JWT_SECRET=")]
            lines.append(f"DEV_JWT_SECRET={secret}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        settings.DEV_JWT_SECRET = secret  # live instance picks it up
        print("[OK] Generated DEV_JWT_SECRET and saved it to backend/.env (local dev only)")
        return True
    except OSError as exc:
        print(f"[!] Could not write .env ({exc}) — set DEV_JWT_SECRET manually.")
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-serve", action="store_true", help="set up everything, don't start the server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()
    if settings.ENV != "local":
        print("REFUSED: dev_up is local-only (ENV != local).")
        return 1

    print("REVORA local launcher")
    print("====================")
    ensure_local_dev_secret()

    from app.infrastructure.database import Base, SessionLocal, engine
    import app.infrastructure.models  # noqa: F401

    Base.metadata.create_all(engine)
    print("[OK] Database schema ensured (SQLite at ./revora_local.db by default)")

    from app.infrastructure.scripts.seed_dev import seed_dev_users

    db = SessionLocal()
    try:
        seed_dev_users(db)
    finally:
        db.close()
    print("[OK] Demo accounts ready: dev-owner@ / dev-admin@ / dev-operator@ / dev-viewer@revora.local")

    if args.no_serve:
        print("\nSetup complete (no-serve mode).")
        return 0

    import uvicorn

    print()
    print("Starting API at http://localhost:8000  (Ctrl+C stops it cleanly)")
    print("Then start the frontend and open http://localhost:5173")
    print("============================================================")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
