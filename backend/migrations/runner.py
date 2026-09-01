from __future__ import annotations

from functools import lru_cache
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=8)
def run_migrations(database_url: str) -> None:
    """Apply every committed SQL migration exactly once to a PostgreSQL target."""
    if not database_url.startswith(("postgres://", "postgresql://")):
        return
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("PostgreSQL migrations require psycopg.") from exc

    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for migration in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
                cursor.execute("SELECT 1 FROM schema_migrations WHERE version = %s", (migration.name,))
                if cursor.fetchone():
                    continue
                cursor.execute(migration.read_text(encoding="utf-8"))
                cursor.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (migration.name,))
        connection.commit()
