"""Bounded, process-local fan-out of durable research events.

The database remains authoritative. Slow readers catch up from storage rather
than retaining queues or blocking the shared producer.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import logging
import random
import re
import sqlite3
import time
from typing import AsyncIterator, Callable

import anyio
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from services.database import is_postgres_database, postgres_query

logger = logging.getLogger("rq.research.stream")
ACTIVE_STATUSES = frozenset({"queued", "running"})
RUN_PATTERN = re.compile(r"run_[A-Za-z0-9_-]{1,128}\Z")
MAX_SEQUENCE = 2**63 - 1
PAGE_SIZE = 100

HEAD_SQL = """
SELECT r.run_id, r.status,
       COALESCE((SELECT MAX(e.sequence) FROM research_events e
                 WHERE e.run_id=r.run_id), 0) AS high_watermark
FROM research_runs r
WHERE r.investigation_id=? {run_filter}
ORDER BY r.created_at DESC, r.run_id DESC LIMIT 1
"""
POLL_SQL = """
WITH head AS (
    SELECT r.run_id, r.status,
           COALESCE((SELECT MAX(e.sequence) FROM research_events e
                     WHERE e.run_id=r.run_id), 0) AS high_watermark
    FROM research_runs r WHERE r.run_id=?
), page AS (
    SELECT sequence, event_type, payload_json, created_at
    FROM research_events
    WHERE run_id=(SELECT run_id FROM head) AND sequence>?
      AND sequence<=(SELECT high_watermark FROM head)
    ORDER BY sequence LIMIT ?
)
SELECT h.run_id, h.status, h.high_watermark,
       p.sequence, p.event_type, p.payload_json, p.created_at
FROM head h LEFT JOIN page p ON 1=1 ORDER BY p.sequence
"""
HISTORY_SQL = """
SELECT run_id, sequence, event_type, payload_json, created_at
FROM research_events WHERE run_id=? AND sequence>? AND sequence<=?
ORDER BY sequence LIMIT ?
"""


@dataclass(frozen=True)
class StreamOptions:
    workers: int = 4
    max_runs: int = 64
    max_subscribers: int = 256
    cache_events: int = 512
    cache_run_bytes: int = 1024 * 1024
    cache_total_bytes: int = 16 * 1024 * 1024
    poll_seconds: float = 1.0
    heartbeat_seconds: float = 15.0
    send_timeout_seconds: float = 15.0

    @classmethod
    def from_settings(cls, settings):
        return cls(**{name: getattr(settings, "RESEARCH_STREAM_" + name.upper())
                      for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class RunHead:
    run_id: str
    status: str
    high_watermark: int


@dataclass(frozen=True)
class EventFrame:
    sequence: int
    data: bytes


@dataclass(frozen=True)
class EventPage:
    head: RunHead
    frames: tuple[EventFrame, ...]


def parse_cursor(last_event_id: str | None, run_id: str | None) -> tuple[str | None, int]:
    if run_id is not None and not RUN_PATTERN.fullmatch(run_id):
        raise HTTPException(400, "Invalid research run identifier.")
    if last_event_id is None or last_event_id == "":
        return run_id, 0
    token = last_event_id
    if ":" in token:
        cursor_run, token = token.split(":", 1)
        if not RUN_PATTERN.fullmatch(cursor_run) or (run_id and run_id != cursor_run):
            raise HTTPException(400, "Conflicting or invalid research cursor.")
        run_id = cursor_run
    if not re.fullmatch(r"[0-9]{1,19}", token) or int(token) > MAX_SEQUENCE:
        raise HTTPException(400, "Invalid research cursor.")
    return run_id, int(token)


def _frame(row) -> EventFrame:
    # Keep the existing ResearchEvent JSON fields and datetime representation.
    from models.research import ResearchEvent
    event = ResearchEvent(run_id=row["run_id"], sequence=row["sequence"],
                          event_type=row["event_type"], payload=json.loads(row["payload_json"]),
                          created_at=row["created_at"])
    data = (f"id: {event.run_id}:{event.sequence}\nevent: {event.event_type}\n"
            f"data: {json.dumps(event.model_dump(mode='json'))}\n\n").encode()
    return EventFrame(event.sequence, data)


class StreamReader:
    """Synchronous reads; called exclusively through the hub's bounded executor."""

    def __init__(self, target: str, workers: int = 4):
        self.target = target
        self.workers = workers
        self.pool = None

    def open(self):
        if is_postgres_database(self.target):
            from psycopg.rows import dict_row
            from psycopg.conninfo import conninfo_to_dict
            from psycopg_pool import ConnectionPool
            existing_options = conninfo_to_dict(self.target).get("options", "")
            self.pool = ConnectionPool(
                self.target, open=False, min_size=0, max_size=self.workers,
                timeout=2, max_waiting=self.workers,
                kwargs={"row_factory": dict_row, "autocommit": True,
                        "connect_timeout": 5, "prepare_threshold": None,
                        "options": existing_options + " -c statement_timeout=2000 -c default_transaction_read_only=on"},
            )
            self.pool.open(wait=False)

    def close(self):
        if self.pool is not None:
            self.pool.close()

    @contextmanager
    def _connection(self):
        if self.pool is not None:
            with self.pool.connection() as conn:
                yield conn
        else:
            conn = sqlite3.connect(self.target, timeout=2)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                yield conn
            finally:
                conn.close()

    def _rows(self, sql, parameters):
        with self._connection() as conn:
            query = postgres_query(sql) if self.pool is not None else sql
            return conn.execute(query, parameters).fetchall()

    def resolve(self, investigation_id: str, run_id: str | None) -> RunHead | None:
        rows = self._rows(HEAD_SQL.format(run_filter="AND r.run_id=?" if run_id else ""),
                          (investigation_id, run_id) if run_id else (investigation_id,))
        return RunHead(**dict(rows[0])) if rows else None

    def poll(self, run_id: str, after: int) -> EventPage:
        rows = self._rows(POLL_SQL, (run_id, after, PAGE_SIZE))
        if not rows:
            raise LookupError("Research run disappeared")
        head = RunHead(rows[0]["run_id"], rows[0]["status"], rows[0]["high_watermark"])
        return EventPage(head, tuple(_frame(row) for row in rows if row["sequence"] is not None))

    def history(self, run_id: str, after: int, through: int) -> tuple[EventFrame, ...]:
        return tuple(_frame(row) for row in self._rows(HISTORY_SQL, (run_id, after, through, PAGE_SIZE)))


def retryable(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        return "locked" in str(exc).lower() or "busy" in str(exc).lower()
    try:
        import psycopg
        from psycopg_pool import PoolTimeout, TooManyRequests
        return isinstance(exc, (psycopg.OperationalError, PoolTimeout, TooManyRequests)) or getattr(exc, "sqlstate", None) == "57014"
    except ImportError:
        return False


@dataclass(eq=False)
class RunFeed:
    head: RunHead
    published: int
    subscribers: int = 0
    frames: OrderedDict[int, EventFrame] = field(default_factory=OrderedDict)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task | None = None
    terminal: bool = False
    failed: bool = False


class ResearchStreamHub:
    def __init__(self, reader: StreamReader, options: StreamOptions | None = None):
        self.options = options or StreamOptions()
        self.reader = reader
        self.executor = ThreadPoolExecutor(max_workers=self.options.workers, thread_name_prefix="rq-sse-db")
        self.slots = asyncio.Semaphore(self.options.workers)
        self.inflight: set[asyncio.Future] = set()
        self.lock = asyncio.Lock()
        self.feeds: dict[str, RunFeed] = {}
        self.subscribers = 0
        self.closed = False
        self.shutdown_task: asyncio.Task | None = None
        self.cache: OrderedDict[tuple[str, int], EventFrame] = OrderedDict()
        self.cache_bytes = 0
        self.run_bytes: dict[str, int] = {}
        self.counters = {"polls": 0, "poll_seconds": 0.0, "historical_reads": 0,
                         "retries": 0, "pool_timeouts": 0}
        self.last_metrics = time.monotonic()

    async def _execute(self, fn: Callable, *args, allow_closed=False):
        await self.slots.acquire()
        try:
            if self.closed and not allow_closed:
                raise RuntimeError("Research stream hub closed")
            future = asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)
        except BaseException:
            self.slots.release()
            raise
        self.inflight.add(future)
        def finished(result):
            self.inflight.discard(result)
            self.slots.release()
            # A cancelled caller must not leak the worker slot or its exception.
            if not result.cancelled():
                result.exception()
        future.add_done_callback(finished)
        return await asyncio.shield(future)

    async def start(self):
        await self._execute(self.reader.open)

    def metrics(self):
        return {**self.counters, "active_readers": len(self.feeds),
                "subscribers": self.subscribers, "cache_bytes": self.cache_bytes}

    async def subscribe(self, investigation_id: str, run_id: str | None, after: int):
        # Count pending validations too; otherwise admission could be bypassed.
        async with self.lock:
            if self.closed or self.subscribers >= self.options.max_subscribers:
                raise HTTPException(503, "Research stream capacity reached.", headers={"Retry-After": "5"})
            self.subscribers += 1
        attached = False
        try:
            try:
                head = await self._execute(self.reader.resolve, investigation_id, run_id)
            except Exception as exc:
                self._log_error(exc)
                raise HTTPException(503, "Research event storage unavailable.", headers={"Retry-After": "5"}) from None
            if head is None:
                raise HTTPException(404, "Research run not found.")
            if after > head.high_watermark:
                raise HTTPException(409, "Research cursor exceeds the run's event sequence.")
            if head.status not in ACTIVE_STATUSES and after == head.high_watermark:
                return None
            async with self.lock:
                if self.closed:
                    raise HTTPException(503, "Research streams shutting down.", headers={"Retry-After": "5"})
                feed = self.feeds.get(head.run_id)
                if feed is None:
                    if len(self.feeds) >= self.options.max_runs:
                        raise HTTPException(503, "Research stream capacity reached.", headers={"Retry-After": "5"})
                    feed = RunFeed(head, head.high_watermark, terminal=head.status not in ACTIVE_STATUSES)
                    self.feeds[head.run_id] = feed
                    if not feed.terminal:
                        feed.task = asyncio.create_task(self._produce(feed), name="sse-" + head.run_id)
                feed.subscribers += 1
                attached = True
                return StreamSubscription(self, feed, after)
        finally:
            if not attached:
                async with self.lock:
                    self.subscribers -= 1

    def _evict(self, key):
        frame = self.cache.pop(key)
        run_id, sequence = key
        self.cache_bytes -= len(frame.data)
        self.run_bytes[run_id] -= len(frame.data)
        feed = self.feeds.get(run_id)
        if feed is not None:
            feed.frames.pop(sequence, None)

    def _cache_frames(self, feed, frames):
        # Mutated only on the event-loop thread, with no await in this operation.
        for frame in frames:
            if len(frame.data) > min(self.options.cache_run_bytes, self.options.cache_total_bytes):
                continue
            key = (feed.head.run_id, frame.sequence)
            self.cache[key] = frame
            feed.frames[frame.sequence] = frame
            self.cache_bytes += len(frame.data)
            self.run_bytes[feed.head.run_id] = self.run_bytes.get(feed.head.run_id, 0) + len(frame.data)
            while (len(feed.frames) > self.options.cache_events or
                   self.run_bytes[feed.head.run_id] > self.options.cache_run_bytes):
                self._evict((feed.head.run_id, next(iter(feed.frames))))
            while self.cache_bytes > self.options.cache_total_bytes:
                self._evict(next(iter(self.cache)))

    def _log_error(self, exc):
        if type(exc).__name__ == "PoolTimeout":
            self.counters["pool_timeouts"] += 1
        logger.warning("Research stream read failed type=%s sqlstate=%s", type(exc).__name__, getattr(exc, "sqlstate", None))

    async def _produce(self, feed):
        backoff = 1.0
        try:
            while not self.closed:
                started = time.monotonic()
                try:
                    page = await self._execute(self.reader.poll, feed.head.run_id, feed.published)
                except Exception as exc:
                    self._log_error(exc)
                    if not retryable(exc):
                        async with feed.condition:
                            feed.failed = True
                            feed.condition.notify_all()
                        return
                    self.counters["retries"] += 1
                    await asyncio.sleep(random.uniform(backoff * 0.8, backoff))
                    backoff = min(backoff * 2, 10.0)
                    continue
                backoff = 1.0
                self.counters["polls"] += 1
                self.counters["poll_seconds"] += time.monotonic() - started
                async with feed.condition:
                    feed.head = page.head
                    self._cache_frames(feed, page.frames)
                    if page.frames:
                        feed.published = page.frames[-1].sequence
                    feed.terminal = page.head.status not in ACTIVE_STATUSES and feed.published >= page.head.high_watermark
                    feed.condition.notify_all()
                if time.monotonic() - self.last_metrics >= 60:
                    logger.info("Research stream metrics %s", self.metrics())
                    self.last_metrics = time.monotonic()
                if feed.terminal:
                    return
                if feed.published < page.head.high_watermark:
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(self.options.poll_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            async with feed.condition:
                feed.condition.notify_all()

    async def release(self, feed):
        task = None
        async with self.lock:
            feed.subscribers -= 1
            self.subscribers -= 1
            if feed.subscribers == 0 and self.feeds.get(feed.head.run_id) is feed:
                # Evict before removing the feed so both cache indexes are cleared.
                for sequence in list(feed.frames):
                    self._evict((feed.head.run_id, sequence))
                self.run_bytes.pop(feed.head.run_id, None)
                del self.feeds[feed.head.run_id]
                task = feed.task
                if task is not None:
                    task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        if self.shutdown_task is None:
            self.shutdown_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self.shutdown_task)

    async def _shutdown(self):
        async with self.lock:
            self.closed = True
            tasks = [feed.task for feed in self.feeds.values() if feed.task is not None]
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for feed in list(self.feeds.values()):
            async with feed.condition:
                feed.condition.notify_all()
        # Shielded worker futures remain tracked even when their callers cancel.
        while self.inflight:
            await asyncio.gather(*tuple(self.inflight), return_exceptions=True)
        await self._execute(self.reader.close, allow_closed=True)
        await asyncio.to_thread(self.executor.shutdown, wait=True)
        logger.info("Research stream shutdown metrics %s", self.metrics())


class StreamSubscription:
    def __init__(self, hub, feed, after):
        self.hub = hub
        self.feed = feed
        self.cursor = after
        self.release_task: asyncio.Task | None = None

    async def close(self):
        with anyio.CancelScope(shield=True):
            if self.release_task is None:
                self.release_task = asyncio.create_task(self.hub.release(self.feed))
            await asyncio.shield(self.release_task)

    async def events(self) -> AsyncIterator[bytes]:
        feed, hub = self.feed, self.hub
        heartbeat_at = time.monotonic() + hub.options.heartbeat_seconds
        backoff = 1.0
        try:
            while not hub.closed:
                frames = ()
                through = None
                async with feed.condition:
                    if feed.failed:
                        return
                    if self.cursor < feed.published:
                        # Cache hits must be contiguous; an oversized or evicted
                        # event is retrieved from durable storage instead.
                        cached = []
                        next_sequence = self.cursor + 1
                        while next_sequence in feed.frames and len(cached) < PAGE_SIZE:
                            frame = feed.frames[next_sequence]
                            cached.append(frame)
                            next_sequence += 1
                        frames = tuple(cached)
                        if not frames:
                            through = feed.published
                    elif feed.terminal:
                        data = json.dumps({"run_id": feed.head.run_id, "status": feed.head.status})
                        frames = (EventFrame(self.cursor, f"event: stream.closed\ndata: {data}\n\n".encode()),)
                    else:
                        try:
                            await asyncio.wait_for(feed.condition.wait(), max(0.001, heartbeat_at - time.monotonic()))
                        except asyncio.TimeoutError:
                            pass
                if through is not None:
                    try:
                        hub.counters["historical_reads"] += 1
                        frames = await hub._execute(hub.reader.history, feed.head.run_id, self.cursor, through)
                        if not frames:
                            raise LookupError("Committed event history unavailable")
                        backoff = 1.0
                    except Exception as exc:
                        hub._log_error(exc)
                        if not retryable(exc):
                            return
                        hub.counters["retries"] += 1
                        # Send a heartbeat before delaying, so recovery doesn't
                        # silently exhaust proxy idle timeouts.
                        yield b": heartbeat\n\n"
                        await asyncio.sleep(random.uniform(backoff * 0.8, backoff))
                        backoff = min(backoff * 2, 10.0)
                        continue
                for frame in frames:
                    yield frame.data
                    self.cursor = frame.sequence
                    heartbeat_at = time.monotonic() + hub.options.heartbeat_seconds
                    if frame.data.startswith(b"event: stream.closed\n"):
                        return
                if time.monotonic() >= heartbeat_at:
                    yield b": heartbeat\n\n"
                    heartbeat_at = time.monotonic() + hub.options.heartbeat_seconds
        finally:
            await self.close()


class ResearchStreamResponse(StreamingResponse):
    """Apply a body-send deadline and release even an unstarted generator."""

    def __init__(self, subscription: StreamSubscription):
        self.subscription = subscription
        super().__init__(subscription.events(), media_type="text/event-stream",
                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def __call__(self, scope, receive, send):
        async def bounded_send(message):
            if message["type"] == "http.response.body":
                with anyio.fail_after(self.subscription.hub.options.send_timeout_seconds):
                    await send(message)
            else:
                await send(message)
        try:
            await super().__call__(scope, receive, bounded_send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                await self.subscription.close()
