from __future__ import annotations

import os

import pytest

from migrations.runner import run_migrations
from services.database import connect
from services.investigation_repository import InvestigationRepository
from services.research_repository import ResearchRepository
from services.trending_repository import TrendingRepository


@pytest.mark.integration
def test_postgres_migration_creates_all_repository_tables() -> None:
    database_url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")

    run_migrations.cache_clear()
    run_migrations(database_url)
    InvestigationRepository(database_url)
    ResearchRepository(database_url)
    TrendingRepository(database_url)
    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()

    with connect(database_url) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    tables = {row["tablename"] for row in rows}
    assert {
        "schema_migrations",
        "investigations",
        "research_runs",
        "discovery_runs",
        "checkpoints",
    }.issubset(tables)
