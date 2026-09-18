# Research event streams

The SSE endpoint uses a process-local shared reader for each research run. It
reads durable events directly rather than constructing a full research trail.
Workspace summaries and the research-trail API retain their existing behavior.

## Database and delivery behavior

- Each steady live polling cycle uses one indexed SELECT per run per API process,
  regardless of viewer count. Subscription validation and historical catch-up
  use additional bounded reads.
- Database reads, event validation, and SSE serialization run in a dedicated
  executor. PostgreSQL uses a separate read-only connection pool; SQLite
  connections are created and closed inside worker operations.
- Subscribers replay history through the producer's published watermark before
  continuing live. Cache eviction and oversized events fall back to durable
  reads. Network sends hold no database connection or hub lock.
- Terminal status and terminal events are committed together. Streams drain the
  committed backlog before closing. Delivery is at least once across reconnects;
  consumers should deduplicate by run ID and sequence.
- Transient failures preserve cursors and use jittered backoff of 1–10 seconds.
  Non-retryable failures close the affected stream without fabricating completion.

## Endpoint contract

    GET /api/investigations/{investigation_id}/events?run_id={run_id}

The run query parameter is optional. Fresh unbound requests select the latest
run; every connection remains bound to that run. New event IDs use
run_id:sequence. A compound Last-Event-ID selects its original run on
reconnection. Legacy numeric IDs remain accepted against the selected run;
legacy unbound numeric reconnects cannot identify a previous run unambiguously.

Event names and ResearchEvent JSON fields are unchanged. After a terminal
backlog drains, the server sends stream.closed with {run_id, status} and no
ID. Both frontend callers close EventSource, clear fallback timers, and refresh
final state on this event. They bind a new stream when the run changes.

| Response | Meaning |
| --- | --- |
| 200 | SSE stream with cache and proxy buffering disabled |
| 204 | Run is terminal and the cursor consumed its backlog; EventSource stops reconnecting |
| 400 | Malformed cursor/run ID or conflicting run identifiers |
| 404 | Run missing or not part of the investigation |
| 409 | Cursor beyond the run's current high-water mark |
| 503 | Capacity, storage availability, startup, or shutdown prevents subscription; Retry-After is 5 seconds |

## Resource defaults

Limits apply per API process. Multiple API workers each own a hub and pool.

| Setting | Default |
| --- | --- |
| RESEARCH_STREAM_WORKERS | 4 database workers and at most 4 PostgreSQL pool connections |
| RESEARCH_STREAM_MAX_RUNS | 64 run readers |
| RESEARCH_STREAM_MAX_SUBSCRIBERS | 256 subscribers, including pending validations |
| RESEARCH_STREAM_CACHE_EVENTS | 512 cached events per run |
| RESEARCH_STREAM_CACHE_RUN_BYTES | 1 MiB per run |
| RESEARCH_STREAM_CACHE_TOTAL_BYTES | 16 MiB across cached frames |
| RESEARCH_STREAM_POLL_SECONDS | 1 second |
| RESEARCH_STREAM_HEARTBEAT_SECONDS | 15 seconds |
| RESEARCH_STREAM_SEND_TIMEOUT_SECONDS | 15 seconds per body send |

Settings must be positive and finite. Pages contain at most 100 events.
Oversized events bypass the cache and are delivered intact; byte limits describe
cached frames, not an individual stored event or in-flight page. The PostgreSQL
pool has no minimum connections, a 2-second checkout timeout, a 2-second statement
timeout, and a 5-second connection timeout. Existing URL options such as
search_path are preserved. Resources are opened during app lifespan, and worker
operations are tracked through cancellation and shutdown.

## Verification and monitoring

The rq.research.stream logger reports aggregate counters at most once a minute
during successful polling and at shutdown: active readers, subscribers, cached
bytes, poll count and accumulated duration, historical reads, retries, and pool
timeouts. Read failures log exception type and SQLSTATE, without connection
strings or exception messages.

Run focused checks from backend:

    pytest tests/test_research_stream.py
    pytest tests/test_research_stream_postgres.py

PostgreSQL tests require POSTGRES_TEST_DATABASE_URL and create/drop only their
own randomly named test schema. CI runs them against its disposable PostgreSQL
service. Tests cover indexed reads, fan-out, cache limits, durable catch-up,
reconnect IDs, terminal transactions, backoff, disconnects, send timeouts, and
shutdown. Frontend lifecycle tests are in researchStream.test.ts.

A controlled SQLite check compares the existing full-trail path's at least 14
SELECTs with the stream reader's single SELECT. The 20-viewer test verifies one
producer and shared live reads. These are query-cost checks, not production
latency or savings measurements.

The independent audit-trail pagination issue (first 500 events) is still pending.
The backend explicitly pins psycopg-pool==3.3.2. During local implementation,
the installed pool was 3.3.1 and network restrictions prevented upgrading it;
real PostgreSQL qualification and the pinned pool version are pending CI.
