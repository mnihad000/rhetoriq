"""Real stream SQL and pool checks, isolated to a disposable test schema."""
from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from services.research_stream import ResearchStreamHub, StreamOptions, StreamReader, POLL_SQL

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_store():
    target = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not target:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    schema = "sse_test_" + uuid4().hex
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.execute("""CREATE TABLE research_runs(run_id TEXT PRIMARY KEY,
            investigation_id TEXT, status TEXT, created_at TEXT)""")
        conn.execute("CREATE INDEX idx_runs ON research_runs(investigation_id, created_at)")
        conn.execute("""CREATE TABLE research_events(run_id TEXT, sequence INTEGER,
            event_type TEXT, payload_json TEXT, created_at TEXT, PRIMARY KEY(run_id, sequence))""")
        conn.execute("INSERT INTO research_runs VALUES('run_a','inv_a','running','2026-09-18')")
    isolated = make_conninfo(target, options="-c search_path=" + schema)
    try:
        yield isolated
    finally:
        with psycopg.connect(target, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def populate(target, count, terminal=False, start=1):
    with psycopg.connect(target) as conn:
        with conn.cursor() as cursor:
            cursor.executemany("INSERT INTO research_events VALUES('run_a',%s,%s,%s,%s)", [
                (sequence, "run.completed" if terminal and sequence == start + count - 1 else "node.completed",
                 json.dumps({"node": "test"}), "2026-09-18T00:00:00+00:00")
                for sequence in range(start, start + count)
            ])
        if terminal:
            conn.execute("UPDATE research_runs SET status='completed' WHERE run_id='run_a'")


def test_postgres_poll_is_indexed_and_pool_is_read_only(postgres_store):
    populate(postgres_store, 2000)
    reader = StreamReader(postgres_store)
    reader.open()
    try:
        assert reader.resolve("inv_a", "run_a").high_watermark == 2000
        assert len(reader.poll("run_a", 1900).frames) == 100
        with reader.pool.connection() as conn:
            assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
            assert conn.execute("SHOW statement_timeout").fetchone()["statement_timeout"] == "2s"
            plan = conn.execute("EXPLAIN (FORMAT JSON) " + POLL_SQL.replace("?", "%s"),
                                ("run_a", 1900, 100)).fetchone()["QUERY PLAN"]
            assert "Index" in json.dumps(plan)
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader._rows("UPDATE research_runs SET status='failed'", ())
        # Failed operations do not poison the next checkout.
        assert reader.poll("run_a", 2000).head.status == "running"
        assert reader.pool.get_stats()["pool_size"] <= 4
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_postgres_fanout_and_atomic_terminal_snapshot(postgres_store):
    reader = StreamReader(postgres_store)
    hub = ResearchStreamHub(reader, StreamOptions(poll_seconds=0.01))
    await hub.start()
    try:
        subscriptions = await asyncio.gather(*[hub.subscribe("inv_a", "run_a", 0) for _ in range(20)])
        await asyncio.to_thread(populate, postgres_store, 250, True)
        async def consume(sub):
            return [frame async for frame in sub.events()]
        batches = await asyncio.wait_for(asyncio.gather(*[consume(sub) for sub in subscriptions]), 10)
        for frames in batches:
            ids = [int(frame.split(b"\n", 1)[0].rsplit(b":", 1)[1]) for frame in frames if frame.startswith(b"id:")]
            assert ids == list(range(1, 251))
            assert frames[-1].startswith(b"event: stream.closed")
        assert hub.counters["historical_reads"] == 0
        assert hub.counters["polls"] < 20
        assert await hub.subscribe("inv_a", "run_a", 250) is None
        assert reader.pool.get_stats()["pool_size"] <= 4
    finally:
        await hub.close()


def test_postgres_replaces_closed_pool_connection(postgres_store):
    reader = StreamReader(postgres_store)
    reader.open()
    try:
        with reader.pool.connection() as conn:
            conn.close()
        assert reader.poll("run_a", 0).head.status == "running"
    finally:
        reader.close()
