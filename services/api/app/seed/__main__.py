"""`python -m app.seed [scenario]` - safely repeatable demo seed (archives, never deletes).

`--if-missing` (used on every container start) only creates what does not exist yet:
the neutral demo workspace, the designated clean account (when its password is set)
and the isolation tenant. It also archives the pre-release Pearl River fixture once,
and syncs the synthetic accounts' passwords to the configured values.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Business
from app.seed.fixtures import FIXTURE_KEY, ensure_fresh_account, retire_legacy_fixture, seed_fixture, sync_demo_passwords


def main() -> None:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    scenario = positional[0] if positional else "usd_primary"  # the standard demonstration
    only_if_missing = "--if-missing" in sys.argv
    with get_sessionmaker()() as db:
        retired = retire_legacy_fixture(db)
        if retired:
            print(f"Archived {retired} pre-release demo fixture business(es); nothing was deleted.")
        if ensure_fresh_account(db) is None:
            print("LIFELINE_FRESH_ACCOUNT_PASSWORD is not set: the designated clean account is not created.")
        if only_if_missing:
            exists = db.execute(select(Business.id).where(Business.fixture_key == FIXTURE_KEY, Business.archived_at.is_(None))).first()
            if exists:
                print("Demo fixture already present; skipping (use 'python -m app.seed <scenario>' to reset).")
                n = sync_demo_passwords(db)
                if n:
                    print(f"Updated {n} synthetic account password(s) to the configured values.")
                return
        biz = seed_fixture(db, scenario)
        sync_demo_passwords(db)
        print(f"Seeded demo fixture '{scenario}' as business {biz.id}")


if __name__ == "__main__":
    main()
