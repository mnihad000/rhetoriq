from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent


def _migration_names() -> list[str]:
    return [migration.name for migration in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))]


def verify_migrations(database_url: str) -> None:
    """Verify the committed PostgreSQL migrations without mutating the target."""
    if not database_url.startswith(("postgres://", "postgresql://")):
        return
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("PostgreSQL migrations require psycopg.") from exc

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.schema_migrations')")
            if cursor.fetchone()[0] is None:
                raise RuntimeError("Database schema is not initialized")
            cursor.execute("SELECT version FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}
    missing = [name for name in _migration_names() if name not in applied]
    if missing:
        raise RuntimeError("Database migrations are missing: " + ", ".join(missing))


@lru_cache(maxsize=16)
def run_migrations(database_url: str, mode: str | None = None) -> None:
    """Apply or verify committed migrations according to the deployment mode."""
    if not database_url.startswith(("postgres://", "postgresql://")):
        return
    selected = (mode or os.getenv("DATABASE_MIGRATION_MODE", "auto")).lower()
    if selected == "verify":
        verify_migrations(database_url)
        return
    if selected not in {"auto", "apply"}:
        raise ValueError("DATABASE_MIGRATION_MODE must be auto, apply, or verify")
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("PostgreSQL migrations require psycopg.") from exc

    with psycopg.connect(database_url, autocommit=False) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(728946103)")
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
