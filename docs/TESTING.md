# RhetoriQ Testing Strategy

Testing follows the architecture boundaries so a failure can be localized to collection, processing, storage, investigation, API, or presentation.

## Test Layers

| Layer | Required coverage |
|---|---|
| Source connectors | Authentication failures, pagination, rate limits, source normalization, and duplicate input. |
| Kafka contracts | Schema validation, compatibility, idempotent consumption, partition key behavior, and dead-letter routing. |
| Flink processing | Text normalization, entity extraction, embeddings, windowed anomaly detection, late events, and replay. |
| Storage | Migrations, retrieval correctness, vector similarity, full-text filters, graph traversal, and cache expiry. |
| Agent workflow | Tool selection, evidence attribution, uncertainty handling, unsupported-claim rejection, and receipt completeness. |
| API | Request validation, authorization, pagination, error responses, and contract snapshots. |
| Frontend | Loading, error, accessibility, route behavior, and rendering of evidence versus generated interpretation. |

## Environments

Unit tests use fixtures and local fakes. Integration tests run against disposable Kafka, PostgreSQL, Elasticsearch, Neo4j, and Redis containers. End-to-end tests start the API and frontend against a seeded corpus, then verify a complete narrative-to-report flow.

## Acceptance Checks

An investigation is acceptable only when every report claim is either linked to evidence, explicitly marked as an inference, or omitted. Tests must assert that source URLs, timestamps, and limitations survive the pipeline and are visible in the API response.
