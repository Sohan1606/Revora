"""Create all tables from the ORM models against DATABASE_URL.

Run from backend/:  python -m app.infrastructure.scripts.init_db
"""
from __future__ import annotations

import sys

from sqlalchemy import inspect

from app.infrastructure.database import Base, SessionLocal, engine
import app.infrastructure.models  # noqa: F401 — registers all tables on Base


def main() -> int:
    Base.metadata.create_all(engine)
    tables = sorted(inspect(engine).get_table_names())
    print(f"OK — {len(tables)} tables ensured on {engine.url.render_as_string(hide_password=True)}:")
    for t in tables:
        print(f"  - {t}")
    SessionLocal().close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
