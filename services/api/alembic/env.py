from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers tables)
from app.config import normalize_database_url
from app.db import Base

target_metadata = Base.metadata


def _url() -> str:
    return normalize_database_url(os.environ.get("ALEMBIC_DATABASE_URL") or os.environ["LIFELINE_DATABASE_URL"])


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
