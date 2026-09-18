from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sqlite3
import threading

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from services.research_stream import (
    HEAD_SQL, POLL_SQL, ResearchStreamHub, ResearchStreamResponse,
    StreamOptions, StreamReader, parse_cursor,
)


@pytest.fixture
def store(tmp_path):
    path = str(tmp_path / "stream.sqlite3")
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE research_runs(run_id TEXT PRIMARY KEY, investigation_id TEXT,
                                       status TEXT, created_at TEXT);
            CREATE INDEX idx_runs ON research_runs(investigation_id, created_at);
            CREATE TABLE research_events(run_id TEXT, sequence INTEGER, event_type TEXT,
                payload_json TEXT, created_at TEXT, PRIMARY KEY(run_id, sequence));
            INSERT INTO research_runs VALUES('run_a', 'inv_a', 'running', '2026-09-18');
        """)
    return path


def write(path, count, *, start=1, terminal=False, run_id="run_a", payload=None):
    with sqlite3.connect(path) as conn:
        conn.executemany("INSERT INTO research_events VALUES(?,?,?,?,?)", [
            (run_id, sequence, "run.completed" if terminal and sequence == start + count - 1 else "node.completed",
             json.dumps(payload or {"node": "test"}), "2026-09-18T00:00:00+00:00")
            for sequence in range(start, start + count)
        ])
        if terminal:
            conn.execute("UPDATE research_runs SET status='completed' WHERE run_id=?", (run_id,))


class CountingReader(StreamReader):
    def __init__(self, target):
        super().__init__(target)
        self.queries = []

    def _rows(self, sql, parameters):
        self.queries.append(sql)
        return super()._rows(sql, parameters)


@asynccontextmanager
async def serving(path, **overrides):
    options = StreamOptions(poll_seconds=0.01, heartbeat_seconds=0.03, **overrides)
    reader = CountingReader(path)
    hub = ResearchStreamHub(reader, options)
    await hub.start()
    try:
        yield hub
    finally:
        await hub.close()


async def collect(subscription):
    async def consume():
        return [frame async for frame in subscription.events()]
    return await asyncio.wait_for(consume(), 3)


def sequences(frames):
    return [int(frame.split(b"\n", 1)[0].rsplit(b":", 1)[1])
            for frame in frames if frame.startswith(b"id:")]


@pytest.mark.parametrize("token,run,expected", [
    (None, None, (None, 0)), ("12", "run_a", ("run_a", 12)),
    ("run_a:12", None, ("run_a", 12)), ("0", None, (None, 0)),
])
def test_cursor_compatibility(token, run, expected):
    assert parse_cursor(token, run) == expected


@pytest.mark.parametrize("token,run", [
    ("-1", None), ("x", None), (" 1", None), (str(2**63), None),
    ("run_a:2", "run_b"), ("run_a:1:2", None), (None, "bad\nrun"),
])
def test_invalid_cursors(token, run):
    with pytest.raises(HTTPException) as caught:
        parse_cursor(token, run)
    assert caught.value.status_code == 400


def test_poll_is_one_indexed_select_and_preserves_payload(store):
    write(store, 2)
    reader = CountingReader(store)
    page = reader.poll("run_a", 1)
    assert len(reader.queries) == 1
    assert page.head.high_watermark == 2
    assert [frame.sequence for frame in page.frames] == [2]
    assert page.frames[0].data.startswith(b"id: run_a:2\nevent: node.completed\n")
    payload = json.loads(page.frames[0].data.split(b"data: ")[1])
    assert payload["run_id"] == "run_a" and payload["payload"] == {"node": "test"}
    assert set(payload) == {"run_id", "sequence", "event_type", "payload", "created_at"}
    with sqlite3.connect(store) as conn:
        plan = " ".join(row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + POLL_SQL, ("run_a", 1, 100)))
        head_plan = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN " + HEAD_SQL.format(run_filter=""), ("inv_a",)))
    assert "SEARCH research_events USING INDEX" in plan
    assert "SEARCH e USING COVERING INDEX" in plan
    assert "idx_runs" in head_plan
    assert not any(name in reader.queries[0] for name in ("research_documents", "research_actions", "research_evaluations"))


@pytest.mark.asyncio
async def test_empty_poll_keeps_run_metadata(store):
    async with serving(store) as hub:
        page = await hub._execute(hub.reader.poll, "run_a", 0)
        assert page.head.status == "running" and page.frames == ()


@pytest.mark.asyncio
async def test_many_viewers_share_live_polls(store):
    async with serving(store) as hub:
        subscriptions = await asyncio.gather(*[hub.subscribe("inv_a", "run_a", 0) for _ in range(20)])
        assert len({id(sub.feed) for sub in subscriptions}) == 1
        assert len(hub.feeds) == 1
        write(store, 3, terminal=True)
        frames = await asyncio.gather(*[collect(sub) for sub in subscriptions])
        assert all(sequences(items) == [1, 2, 3] for items in frames)
        assert all(items[-1].startswith(b"event: stream.closed\n") for items in frames)
        assert hub.counters["polls"] < 10
        assert hub.counters["historical_reads"] == 0
        assert hub.metrics()["active_readers"] == 0
        assert hub.subscribers == 0 and hub.cache_bytes == 0


@pytest.mark.asyncio
async def test_catchup_and_live_handoff_survive_eviction(store):
    write(store, 250)
    async with serving(store, cache_events=2, cache_run_bytes=512) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        write(store, 150, start=251, terminal=True)
        frames = await collect(sub)
        assert sequences(frames) == list(range(1, 401))
        assert hub.counters["historical_reads"] >= 3
        assert frames[-1].startswith(b"event: stream.closed")


@pytest.mark.asyncio
async def test_large_event_bypasses_cache_without_truncation(store):
    async with serving(store, cache_run_bytes=512) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        write(store, 1, terminal=True, payload={"text": "x" * 5000})
        frames = await collect(sub)
        assert b"x" * 5000 in frames[0]
        assert sequences(frames) == [1]
        assert hub.cache_bytes == 0


@pytest.mark.asyncio
async def test_completed_resume_and_run_binding(store):
    write(store, 4, terminal=True)
    with sqlite3.connect(store) as conn:
        conn.execute("INSERT INTO research_runs VALUES('run_b','inv_a','running','2026-09-19')")
    async with serving(store) as hub:
        selected, cursor = parse_cursor("run_a:2", None)
        sub = await hub.subscribe("inv_a", selected, cursor)
        assert sequences(await collect(sub)) == [3, 4]
        assert await hub.subscribe("inv_a", "run_a", 4) is None
        latest = await hub.subscribe("inv_a", None, 0)
        assert latest.feed.head.run_id == "run_b"
        await latest.close()
        assert hub.subscribers == 0
        for investigation, run, after, status in [
            ("inv_other", "run_a", 0, 404), ("inv_a", "run_a", 5, 409),
        ]:
            with pytest.raises(HTTPException) as caught:
                await hub.subscribe(investigation, run, after)
            assert caught.value.status_code == status
        assert hub.subscribers == 0


@pytest.mark.asyncio
async def test_capacity_limits_and_two_run_isolation(store):
    with sqlite3.connect(store) as conn:
        conn.execute("INSERT INTO research_runs VALUES('run_b','inv_b','running','2026-09-18')")
    async with serving(store, max_runs=1, max_subscribers=2) as hub:
        first = await hub.subscribe("inv_a", "run_a", 0)
        second = await hub.subscribe("inv_a", "run_a", 0)
        with pytest.raises(HTTPException) as caught:
            await hub.subscribe("inv_a", "run_a", 0)
        assert caught.value.status_code == 503
        assert caught.value.headers["Retry-After"] == "5"
        await second.close()
        with pytest.raises(HTTPException):
            await hub.subscribe("inv_b", "run_b", 0)
        await first.close()
        other = await hub.subscribe("inv_b", "run_b", 0)
        assert other.feed.head.run_id == "run_b"
        await other.close()


@pytest.mark.asyncio
async def test_global_cache_budget(store):
    with sqlite3.connect(store) as conn:
        conn.execute("INSERT INTO research_runs VALUES('run_b','inv_b','running','2026-09-18')")
    async with serving(store, cache_total_bytes=600) as hub:
        a = await hub.subscribe("inv_a", "run_a", 0)
        b = await hub.subscribe("inv_b", "run_b", 0)
        write(store, 3, terminal=True)
        write(store, 3, terminal=True, run_id="run_b")
        frames = await asyncio.gather(collect(a), collect(b))
        assert all(sequences(items) == [1, 2, 3] for items in frames)
        assert hub.cache_bytes <= 600


@pytest.mark.asyncio
async def test_worker_cancellation_does_not_release_admission_early(store):
    started, finish = threading.Event(), threading.Event()
    async with serving(store, workers=1) as hub:
        def blocked():
            started.set()
            finish.wait(2)
        task = asyncio.create_task(hub._execute(blocked))
        while not started.is_set():
            await asyncio.sleep(0.001)
        # The event loop continues while the database worker is blocked.
        assert await asyncio.wait_for(asyncio.sleep(0, result="responsive"), 0.1) == "responsive"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        waiting = asyncio.create_task(hub._execute(lambda: "second"))
        await asyncio.sleep(0.01)
        assert not waiting.done()
        finish.set()
        assert await waiting == "second"


@pytest.mark.asyncio
async def test_retry_preserves_cursor_and_heartbeat(store, monkeypatch):
    async with serving(store) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        original = hub.reader.poll
        failures = 0
        def flaky(*args):
            nonlocal failures
            if failures == 0:
                failures += 1
                raise sqlite3.OperationalError("database is locked")
            return original(*args)
        monkeypatch.setattr(hub.reader, "poll", flaky)
        monkeypatch.setattr("services.research_stream.random.uniform", lambda *args: 0.01)
        write(store, 2, terminal=True)
        assert sequences(await collect(sub)) == [1, 2]
        assert hub.counters["retries"] == 1


@pytest.mark.asyncio
async def test_heartbeat_disconnect_and_shutdown(store):
    async with serving(store) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        events = sub.events()
        assert await asyncio.wait_for(anext(events), 0.5) == b": heartbeat\n\n"
        await events.aclose()
        assert hub.subscribers == 0 and not hub.feeds
        sub = await hub.subscribe("inv_a", "run_a", 0)
        pending = asyncio.create_task(collect(sub))
        await asyncio.sleep(0)
        await hub.close()
        assert await pending == []
        assert not hub.inflight and not hub.feeds
        # Context cleanup can safely close again.


@pytest.mark.asyncio
async def test_nonretryable_error_closes_without_fake_terminal(store, monkeypatch):
    async with serving(store) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        def broken(*args):
            raise ValueError("malformed payload")
        monkeypatch.setattr(hub.reader, "poll", broken)
        frames = await collect(sub)
        assert not sequences(frames)
        assert not any(b"stream.closed" in frame for frame in frames)
        assert not hub.feeds and hub.subscribers == 0


@pytest.mark.asyncio
async def test_send_timeout_releases_even_unstarted_stream(store):
    async with serving(store, send_timeout_seconds=0.01) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        write(store, 1, terminal=True)
        response = ResearchStreamResponse(sub)
        async def receive():
            await asyncio.sleep(10)
        async def send(message):
            if message["type"] == "http.response.body":
                await asyncio.sleep(10)
        scope = {"type": "http", "asgi": {"spec_version": "2.0"}}
        with pytest.raises(BaseExceptionGroup):
            await asyncio.wait_for(response(scope, receive, send), 0.5)
        assert hub.subscribers == 0 and not hub.feeds


def test_terminal_status_and_event_rollback_together(tmp_path, monkeypatch):
    from models.research import ResearchBudgetLimits
    from services.research_repository import ResearchRepository
    audit = ResearchRepository(str(tmp_path / "audit.sqlite3"))
    run = audit.create_run("inv_test", ResearchBudgetLimits())
    append = audit.append_event
    def fail(*args, **kwargs):
        raise RuntimeError("injected append failure")
    monkeypatch.setattr(audit, "append_event", fail)
    with pytest.raises(RuntimeError):
        audit.finish_run(run.run_id, status="failed", terminal_decision="failed",
                         warnings=[], event_type="run.failed", payload={})
    assert audit.get_run(run.run_id).status == "queued"
    monkeypatch.setattr(audit, "append_event", append)
    audit.finish_run(run.run_id, status="failed", terminal_decision="failed",
                     warnings=[], event_type="run.failed", payload={})
    page = StreamReader(audit.db_path).poll(run.run_id, 0)
    assert page.head.status == "failed"
    assert page.frames[-1].data.split(b"\n")[1] == b"event: run.failed"


def test_stream_endpoint_contract(store):
    from api.research import router
    @asynccontextmanager
    async def lifespan(app):
        hub = ResearchStreamHub(StreamReader(store))
        await hub.start()
        app.state.research_stream_hub = hub
        try:
            yield
        finally:
            await hub.close()
    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    write(store, 2, terminal=True)
    with TestClient(app) as client:
        response = client.get("/api/investigations/inv_a/events?run_id=run_a")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        assert "id: run_a:2" in response.text and "event: stream.closed" in response.text
        assert client.get("/api/investigations/inv_a/events", headers={"Last-Event-ID": "run_a:2"}).status_code == 204
        assert client.get("/api/investigations/inv_a/events?run_id=run_a", headers={"Last-Event-ID": "run_b:1"}).status_code == 400
        assert client.get("/api/investigations/inv_a/events", headers={"Last-Event-ID": "-1"}).status_code == 400


@pytest.mark.asyncio
async def test_concurrent_subscription_cleanup_is_idempotent(store):
    async with serving(store) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        await asyncio.gather(sub.close(), sub.close(), sub.close())
        assert hub.subscribers == 0 and not hub.feeds


@pytest.mark.asyncio
async def test_disconnect_before_body_iteration_releases_subscription(store):
    async with serving(store) as hub:
        sub = await hub.subscribe("inv_a", "run_a", 0)
        response = ResearchStreamResponse(sub)
        async def receive():
            return {"type": "http.disconnect"}
        async def send(message):
            await asyncio.sleep(0)
        await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
        assert hub.subscribers == 0 and not hub.feeds


def test_stream_config_requires_positive_finite_limits():
    from config import Settings
    for name in ("RESEARCH_STREAM_WORKERS", "RESEARCH_STREAM_POLL_SECONDS",
                 "RESEARCH_STREAM_CACHE_TOTAL_BYTES"):
        with pytest.raises(ValueError):
            Settings(**{name: 0})
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            Settings(RESEARCH_STREAM_POLL_SECONDS=value)



def test_poll_avoids_full_trail_query_amplification(tmp_path):
    from models.research import ResearchBudgetLimits
    from services.research_repository import ResearchRepository
    audit = ResearchRepository(str(tmp_path / "cost.sqlite3"))
    run = audit.create_run("inv_cost", ResearchBudgetLimits())
    queries = []
    original_connect = audit._connect
    def counted_connect():
        conn = original_connect()
        conn.set_trace_callback(lambda query: queries.append(query)
                                if query.lstrip().upper().startswith("SELECT") else None)
        return conn
    audit._connect = counted_connect
    audit.get_trail("inv_cost")
    full_trail_reads = len(queries)
    reader = CountingReader(audit.db_path)
    reader.poll(run.run_id, 0)
    assert len(reader.queries) == 1
    assert full_trail_reads >= 14


def test_reader_pool_preserves_url_options_and_bounds_checkout(monkeypatch):
    import psycopg_pool
    pools = []
    class FakePool:
        def __init__(self, target, **kwargs):
            self.kwargs = kwargs
            pools.append(self)
        def open(self, **kwargs):
            self.open_options = kwargs
        def close(self):
            pass
    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    reader = StreamReader("postgresql://localhost/test?options=-c%20search_path%3Dexpected", workers=2)
    reader.open()
    assert pools[0].kwargs["min_size"] == 0
    assert pools[0].kwargs["max_size"] == 2
    assert pools[0].kwargs["timeout"] == 2
    options = pools[0].kwargs["kwargs"]["options"]
    assert "search_path=expected" in options and "statement_timeout=2000" in options
    assert "default_transaction_read_only=on" in options
    assert pools[0].kwargs["open"] is False
    reader.close()
