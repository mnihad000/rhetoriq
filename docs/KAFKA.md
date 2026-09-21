# RhetoriQ Kafka Contracts

Kafka is the required event backbone for RhetoriQ ingestion and investigation execution. The implementation uses Apache Kafka 4.3.1 in single-node KRaft mode locally, Apicurio Registry 3.3.0 through its Confluent-compatible API, `confluent-kafka`, and JSON Schema generated from committed Pydantic models in `backend/models/events.py`.

There is no synchronous production fallback. Accepted source records, research evidence, and investigation requests are written to the transactional outbox and published asynchronously. Broker or registry failure degrades readiness and leaves durable work queued.

## Design principles

1. **Replayable ingestion.** A connector can republish from a checkpoint and downstream state can be rebuilt.
2. **Transport-neutral events.** Schemas identify provider and transport without creating a topic for every vendor.
3. **Versioned contracts.** Breaking schema changes use a new event version and, when necessary, a new topic.
4. **Idempotent consumers.** Every event has stable IDs; consumers tolerate redelivery.
5. **Evidence continuity.** Provider metadata, canonical URLs, timestamps, and retrieval limitations survive every stage.
6. **Partial failure.** One connector or consumer failure does not stop unrelated sources.
7. **No hidden state.** Connector checkpoints, retry state, and dead-letter outcomes are observable.

## Event flow

```mermaid
flowchart LR
    C[Source connectors] --> R[raw.documents.v1]
    R --> N[Flink normalization]
    N --> Q[documents.enrichment-requested.v1]
    Q --> W[Enrichment worker]
    W --> E[documents.enriched.v1]
    E --> N
    N --> D[documents.processed.v1]
    N --> L[documents.late.v1]
    N --> H[pipeline.evaluated.v1]
    D --> S[Persistence and indexes]
    D --> V[Signal detector]
    V --> G[signals.detected.v1]
    G --> I[Investigation worker]
    Q[investigations.requested.v1] --> I
    I --> ST[investigations.stage-events.v1]
    I --> O[investigations.completed.v1]
    D --> CP[corpus.projections.v1]
    O --> IP[investigation.projections.v1]
    R --> X[raw.documents.dlq.v1]
    D --> Y[documents.processed.dlq.v1]
```

## Topic summary

| Topic | Producer | Primary consumers | Purpose |
|---|---|---|---|
| `raw.documents.v1` | Source connector workers | Normalization/enrichment | Provider records and canonical retrieval results before shared enrichment. |
| `documents.enrichment-requested.v1` | Flink normalization | Hosted enrichment worker | Normalized input requiring a recorded extraction/embedding artifact. |
| `documents.enriched.v1` | Hosted enrichment worker | Flink | Validated immutable enrichment result returned to stream processing. |
| `documents.late.v1` | Flink | Projection/audit worker | Valid records too late to revise the active signal windows. |
| `pipeline.evaluated.v1` | Flink | Projection/health worker | Evaluation heartbeat and pipeline freshness state. |
| `documents.processed.v1` | Normalization/enrichment | Persistence, indexing, signal detection | Validated normalized documents with provenance metadata. |
| `signals.detected.v1` | Signal detector | Investigation scheduler, API updates | Candidate narrative spikes with supporting document IDs and coverage context. |
| `investigations.requested.v1` | API or scheduler | Investigation workers | User-requested and signal-triggered investigation jobs. |
| `investigations.stage-events.v1` | Investigation workers | API, observability, persistence | Progress and inspectable intermediate-stage events. |
| `investigations.completed.v1` | Investigation workers | Persistence and API | Completed evidence-limited reports and artifact references. |
| `corpus.projections.v1` | Canonical persistence | Elasticsearch, Neo4j, and MiniLM workers | Versioned document projection mutations. |
| `investigation.projections.v1` | Investigation persistence | Neo4j worker | Versioned investigation graph projection mutations. |
| `*.dlq.v1` | Failing consumers | Operations/replay tooling | Records requiring inspection or controlled replay. |

Use additional source-specific topics only when security, retention, ordering, or extreme volume requires isolation. `raw.reddit`, `raw.gdelt`, and `raw.cspan` are not default contracts because they couple downstream consumers to provider deployment names.

## Common event envelope

All events use a shared envelope:

```json
{
  "event_id": "01J...",
  "event_type": "raw.document.collected",
  "schema_version": 1,
  "occurred_at": "2026-07-16T20:10:00Z",
  "produced_at": "2026-07-16T20:10:02Z",
  "producer": "connector.gdelt",
  "correlation_id": "collection_01J...",
  "causation_id": null,
  "partition_key": "domain:example.com",
  "payload": {}
}
```

Requirements:

- `event_id` is globally unique and stable across producer retries.
- `occurred_at` is source/event time; `produced_at` is Kafka publication time.
- `correlation_id` connects a collection or investigation across stages.
- `causation_id` references the event that caused this event when applicable.
- `schema_version` is validated at producer and consumer boundaries.

## `raw.documents.v1`

This topic carries source-native records and canonical fetch results with enough metadata to normalize and audit them.

```json
{
  "event_id": "01J...",
  "event_type": "raw.document.collected",
  "schema_version": 1,
  "occurred_at": "2026-07-16T20:00:00Z",
  "produced_at": "2026-07-16T20:00:03Z",
  "producer": "connector.gdelt",
  "correlation_id": "collection_01J...",
  "causation_id": null,
  "partition_key": "https://example.com/article",
  "payload": {
    "provider": "gdelt",
    "transport": "api",
    "provider_record_id": "provider-native-id",
    "provider_query": "energy policy",
    "provider_cursor": null,
    "source_url": "https://example.com/article",
    "canonical_url": "https://example.com/article",
    "title": "Example title",
    "body": null,
    "snippet": "Example title",
    "author": null,
    "published_at": "2026-07-16T20:00:00Z",
    "collected_at": "2026-07-16T20:00:02Z",
    "language": "english",
    "content_type": "article",
    "retrieval": {
      "status": "provider_metadata_only",
      "http_status": null,
      "parser_version": null,
      "content_hash": null
    },
    "provider_metadata": {}
  }
}
```

`provider_metadata_only` explicitly marks records that are useful for discovery but do not contain canonical full text.

## `documents.processed.v1`

The processed event embeds the shared `Document` contract plus processing receipts:

```json
{
  "event_id": "01J...",
  "event_type": "document.processed",
  "schema_version": 1,
  "occurred_at": "2026-07-16T20:00:00Z",
  "produced_at": "2026-07-16T20:00:05Z",
  "producer": "document-normalizer",
  "correlation_id": "collection_01J...",
  "causation_id": "01J_RAW...",
  "partition_key": "doc_01J...",
  "payload": {
    "document": {
      "id": "doc_01J...",
      "source_id": "domain:example.com",
      "source_name": "example.com",
      "source_type": "national_news",
      "url": "https://example.com/article",
      "title": "Example title",
      "published_at": "2026-07-16T20:00:00Z",
      "collected_at": "2026-07-16T20:00:02Z",
      "text": "...",
      "snippet": "...",
      "entities": [],
      "phrases": [],
      "metadata": {
        "provider": "gdelt",
        "transport": "api"
      }
    },
    "processing": {
      "normalizer_version": "1",
      "duplicate_of_document_id": null,
      "quality_flags": []
    }
  }
}
```

Provider is metadata; `source_type` describes the evidence, not how it was transported.

## Signal and investigation events

`signals.detected.v1` must include:

- canonical phrase and related phrase IDs;
- observed time window and baseline window;
- spike and confidence components;
- supporting document IDs;
- source and publisher diversity;
- provider coverage and failed-source limitations;
- language stating that origin is not proven.

`investigations.requested.v1` must include the user query or signal ID, requested outputs, time window, source classes, and authorization context.

Stage and completion events should reference persisted artifacts rather than duplicating large bodies of evidence in every event.

## Partitioning

Recommended initial keys:

| Topic | Partition key |
|---|---|
| `raw.documents.v1` | Canonical URL hash or provider plus native record ID. |
| `documents.processed.v1` | Document ID. |
| `signals.detected.v1` | Canonical phrase/narrative ID. |
| Investigation topics | Investigation ID. |

Begin with conservative partition counts based on measured throughput. Do not preserve the old claim that “six scrapers require six partitions”; connector count and partition count are unrelated.

## Delivery and idempotency

- Producers enable idempotence and use acknowledgements appropriate for production durability.
- Consumers commit offsets only after their durable side effect succeeds.
- Persistence uses `event_id` or stable domain IDs as uniqueness keys.
- Transient failures use bounded retries with jitter.
- Permanent schema, policy, or parsing failures go to a dead-letter topic with a redacted error receipt.
- Replays must not send duplicate user notifications or launch duplicate investigations.

Exactly-once business behavior comes from idempotent application writes, not from assuming transport-level exactly-once semantics solve every side effect.

## Retention

Initial topic defaults are configured by `backend/events/topics.py`:

- raw documents: 14 days;
- enrichment-requested, enriched, late, and processed documents: 30 days;
- pipeline evaluations and corpus projections: 90 days;
- signals, investigation requests, and stage events: 90 days;
- completed investigations and investigation projections: 180 days;
- all dead-letter topics: 14 days.

User-generated platform content may require deletion synchronization. A Kafka retention policy must never preserve content longer than the applicable provider agreement allows.

## Runtime implementation

- `backend/services/event_store.py`: transactional outbox, scoped consumer ledger, payload-hash verification, retry state, and replay audit jobs.
- `backend/services/kafka_runtime.py`: Apicurio schema registration/framing, idempotent producer, outbox publisher, broker health, group lag, and DLQ depth.
- `backend/events/worker.py`: role-scoped consumers, manual offset commits, retry classification, sanitized DLQ publication, and durable idempotency.
- `backend/events/replay.py`: explicit topic/partition/offset or correlation-filtered replay under an audited replay job ID.
- `backend/events/schemas/`: committed schemas for all 12 primary and 12 DLQ topics.

Flink exclusively consumes the raw/enriched processing topics. The document
worker persists processed documents to the canonical corpus and emits projection
mutations. The signal worker schedules only explicitly authorized automatic
investigations. The investigation worker uses lease/recovery rules and emits
ordered stage events plus one stable completion event. Product projection
consumers persist stage/completion and pipeline/late-event state; B5 consumers
update derived stores without rewriting authoritative business state.

## Local operation

Set `POSTGRES_PASSWORD` and `SEARXNG_SECRET`, then start the stack:

```powershell
docker compose up --build -d
docker compose ps
```

`topic-init` creates topics, applies retention, registers every schema subject as `<topic>-value`, and configures `BACKWARD_TRANSITIVE` compatibility. Useful commands:

```powershell
docker compose logs topic-init outbox-publisher document-worker investigation-worker
cd backend
..\.venv\Scripts\python.exe -m events.replay --topic raw.documents.v1 --partition 0 --start-offset 0 --end-offset 10 --dry-run
```

Replays never commit the normal consumer group’s offsets. Normal handlers and stable domain IDs prevent duplicate investigations, notifications, documents, and completion events.

## Security

- Kafka events never contain provider credentials or authorization headers.
- Sensitive topics use least-privilege ACLs and encryption in transit.
- Logs and dead-letter events redact personal or secret values.
- Connector policy metadata remains available to deletion and retention workers.
- Schema registry access and topic creation are controlled in production.

## Observability

Track producer error rate, publish latency, consumer lag, event age, retries, dead-letter volume, checkpoint age, duplicate rate, and end-to-end collection-to-investigation latency. Metrics should be labeled by connector/provider without putting high-cardinality URLs or document IDs in metric labels.
