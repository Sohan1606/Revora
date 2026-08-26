"""CLI: generate the synthetic evaluation corpus.

Run from backend/:  python -m app.infrastructure.scripts.generate_corpus --n 3000 --seed 42
Uses DATABASE_URL (falls back to local SQLite). Every row is labeled is_synthetic.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.domain.recovery.corpus import CORPUS_MERCHANT_NAME, generate_corpus
from app.infrastructure.database import Base, SessionLocal, engine
import app.infrastructure.models  # noqa: F401
from app.infrastructure.models import Merchant

REPO_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--export-csv", action="store_true",
                        help="also write database/corpus_export.csv (git-ignored)")
    parser.add_argument("--isolated", action="store_true",
                        help="put corpus under its own hidden merchant (legacy behavior). "
                             "Default: attach to the dev merchant so the console can see it "
                             "(rows stay labeled is_synthetic=true).")
    args = parser.parse_args()

    settings = get_settings()
    if settings.ENV not in ("local", "staging"):
        print("REFUSED: corpus generation is for local/staging evaluation only.")
        return 1

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        merchant = None
        if not args.isolated:
            merchant = db.query(Merchant).filter(
                Merchant.name == "REVORA Dev Merchant").one_or_none()
            if merchant is not None:
                print(f"Attaching corpus to dev merchant {merchant.id} "
                      f"(rows stay labeled is_synthetic=true)")
        if merchant is None:
            merchant = db.query(Merchant).filter(
                Merchant.name == CORPUS_MERCHANT_NAME).one_or_none()
            if merchant is None:
                merchant = Merchant(name=CORPUS_MERCHANT_NAME,
                                    business_type="synthetic_evaluation")
                db.add(merchant)
                db.flush()

        stats = generate_corpus(db, n_cases=args.n, seed=args.seed, merchant=merchant)

        if args.export_csv:
            from app.infrastructure.models import (
                Decision, FailureClassification, Outcome, RecoveryCase,
            )
            path = REPO_ROOT / "database" / "corpus_export.csv"
            path.parent.mkdir(exist_ok=True)
            rows = db.execute(
                select(RecoveryCase, Decision, Outcome, FailureClassification)
                .join(Decision, Decision.case_id == RecoveryCase.id)
                .join(Outcome, Outcome.case_id == RecoveryCase.id)
                .join(FailureClassification,
                      FailureClassification.risk_event_id == RecoveryCase.risk_event_id)
                .where(RecoveryCase.is_synthetic.is_(True))
            ).all()
            with path.open("w", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["case_id", "cause", "action", "amount_paise",
                                 "outcome", "amount_recovered_paise", "is_synthetic"])
                for case, decision, outcome, classification in rows:
                    writer.writerow([str(case.id), classification.primary_cause,
                                     decision.chosen_action, case.amount_paise,
                                     outcome.outcome, outcome.amount_recovered_paise, True])
            print(f"CSV export: {path}")

        print(f"CORPUS OK — {stats['cases']} synthetic cases, "
              f"{stats['recovered']} recovered ({stats['recovered']/max(stats['cases'],1):.1%})")
        for cause, count in sorted(stats["by_cause"].items(), key=lambda kv: -kv[1]):
            print(f"  {cause:<34} {count}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
