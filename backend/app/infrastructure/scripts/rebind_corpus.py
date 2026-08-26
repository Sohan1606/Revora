"""One-time repair: move synthetic-corpus cases from the legacy hidden corpus
merchant into the dev merchant so the console can display them.

Why: corpus generated before this fix landed under its own merchant; tenant
isolation then (correctly) hid it from the dev login. Rows keep is_synthetic=true,
so live/synthetic metric separation is unaffected.

Run from backend/:  python -m app.infrastructure.scripts.rebind_corpus
"""
from __future__ import annotations

import sys

from app.core.config import get_settings
from app.domain.recovery.corpus import CORPUS_MERCHANT_NAME
from app.infrastructure.database import Base, SessionLocal, engine
import app.infrastructure.models  # noqa: F401
from app.infrastructure.models import Merchant, RecoveryCase
from sqlalchemy import update


def main() -> int:
    settings = get_settings()
    if settings.ENV != "local":
        print("REFUSED: rebind_corpus is local-only.")
        return 1

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        dev = db.query(Merchant).filter(Merchant.name == "REVORA Dev Merchant").one_or_none()
        if dev is None:
            print("No 'REVORA Dev Merchant' found — run seed_dev first.")
            return 1
        corpus_merchants = db.query(Merchant).filter(
            Merchant.name == CORPUS_MERCHANT_NAME).all()
        if not corpus_merchants:
            print("No corpus merchant found — nothing to move. "
                  "Run generate_corpus to create evaluation data.")
            return 0

        moved = 0
        for cm in corpus_merchants:
            result = db.execute(
                update(RecoveryCase)
                .where(RecoveryCase.merchant_id == cm.id)
                .values(merchant_id=dev.id)
            )
            moved += result.rowcount or 0
        db.commit()
        print(f"[OK] Moved {moved} synthetic corpus cases into the dev merchant "
              f"(still labeled is_synthetic=true).")
        print("Refresh the console — Control Center / Recovery Cases (synthetic filter) "
              "now show the corpus.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
