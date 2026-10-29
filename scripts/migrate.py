"""Apply the SQL files in migrations/ that haven't run yet, in filename order.

Each file runs in its own transaction and is recorded in the schema_migrations table, so
running this twice is harmless. Migrations only move forward: to undo one, write a new file.

Usage (PowerShell, from the project root):

    uv run python -m scripts.migrate
"""

import asyncio
import logging
from pathlib import Path

import asyncpg

from app.config import get_database_settings
from app.db import ssl_option

logger = logging.getLogger("migrate")

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
# Any constant works; it only has to be the same for every process running migrations.
ADVISORY_LOCK_ID = 7_261_020


async def main() -> None:
    settings = get_database_settings()
    connection = await asyncpg.connect(settings.database_url, ssl=ssl_option(settings))
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        # Two deploys running migrations at once would otherwise both try to apply the same file.
        await connection.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_ID)

        rows = await connection.fetch("SELECT version FROM schema_migrations")
        applied = {row["version"] for row in rows}

        pending = [
            path for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if path.stem not in applied
        ]
        for path in pending:
            logger.info("Applying %s", path.name)
            async with connection.transaction():
                await connection.execute(path.read_text(encoding="utf-8"))
                await connection.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)", path.stem
                )
        logger.info("Applied %d migration(s)", len(pending))
    finally:
        await connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(main())
