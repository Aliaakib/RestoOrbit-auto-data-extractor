"""Idempotent SQL migration runner for the Auto-Data-Extractor microservice."""

import asyncio
from pathlib import Path

import pg8000.dbapi

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.pool import parse_postgres_dsn

logger = get_logger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _run_migrations_sync(dsn: str) -> list[str]:
    dsn_kwargs = parse_postgres_dsn(dsn)
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied: list[str] = []

    conn = pg8000.dbapi.connect(**dsn_kwargs)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        try:
            for sql_file in sql_files:
                sql_text = sql_file.read_text(encoding="utf-8")
                cur.execute(sql_text)
                applied.append(sql_file.name)
                logger.info("migration_applied", file=sql_file.name)
        finally:
            cur.close()
    finally:
        conn.close()

    return applied


async def run_migrations(database_url: str | None = None) -> list[str]:
    """Execute all .sql files in app/db/migrations in sorted order."""
    settings = get_settings()
    dsn = database_url or settings.DATABASE_URL
    return await asyncio.to_thread(_run_migrations_sync, dsn)


if __name__ == "__main__":
    cfg = get_settings()
    configure_logging(cfg.LOG_LEVEL, cfg.ENV)
    executed = asyncio.run(run_migrations(cfg.DATABASE_URL))
    print(f"Applied migrations: {executed}")
