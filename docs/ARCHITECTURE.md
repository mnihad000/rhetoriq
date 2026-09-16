# RhetoriQ Architecture

This document defines the collection, processing, evidence, and product boundaries for RhetoriQ. B2 research acquisition and the B3 Kafka backbone are implemented; Flink, Elasticsearch, Neo4j, Kubernetes, and managed-cloud operations remain later phases.

## Architecture status

### Implemented in this repository

- FastAPI application and React frontend.
- GDELT DOC 2.0, Hacker News/Algolia ingestion, SearXNG discovery, and Federal Register primary-source retrieval.
- A normalized `Document` model and document normalizer.
- Direct HTTP page retrieval with timeout, content-type validation, redirect handling, and optional caching.
- A pluggable `SearchProvider` boundary with SearXNG and explicit internal-corpus fallback diagnostics.
- Trending detection, retrieval, timeline, graph, source-diversity, mutation, receipts, debate, and report services.
- PostgreSQL/pgvector and SQLite repository support, plus optional Redis-backed capabilities.
- Apache Kafka KRaft, Apicurio Registry, six versioned topics and DLQs, transactional outbox, idempotent consumers, controlled replay, and Kafka-only investigation execution.

### Target production architecture

- Durable connector checkpoints and idempotent ingestion.
- Stream processing and durable production stores.
- Independently scalable connector workers and processing services.
- Production secrets, observability, and compliance controls.
- A self-hosted LangGraph investigative workflow that chooses approved browser and search tools, with RhetoriQ-controlled tracing and evaluation.

Flink, Kubernetes, Elasticsearch, and Neo4j should not be described as implemented until their roadmap phases are complete.

## Core research rule

RhetoriQ is **agent-led and source-policy-first**. A new user question should
be answerable through bounded live research; recurring collection is a later
monitoring capability, not a dependency of the investigation workflow.

Acquisition priority is:

1. An approved search API for broad discovery.
2. A first-party API or official dataset when it directly addresses an evidence gap.
3. Direct retrieval of a canonical public page for evidence enrichment.
4. An isolated browser for public, JavaScript-rendered, or interactive sources when it improves evidence coverage.
5. RSS, Atom, JSON Feed, webhooks, or public event streams for later recurring monitoring.

“Scraper” is not used as the umbrella term. The umbrella is **source connector**. A connector may use an API, feed, stream, file download, or controlled page fetch.

## High-level flow

Kafka is the production event backbone. The diagram shows the implemented B2/B3 flow; dashed sources remain future monitoring connectors.

```mermaid
flowchart TD
    subgraph Sources[Public sources]
      G[GDELT DOC 2.0]
      H[Hacker News via Algolia]
      R[RSS and Atom feeds]
      W[Broad web-search API]
      O[Federal Register API]
      P[Approved platform APIs and public streams]
    end

    subgraph Collection[Collection layer]
      C[Source connectors]
      F[Canonical-page fetcher]
      K[(Kafka and Apicurio)]
      N[Document worker]
    end

    subgraph Processing[Processing layer]
      D[Deduplication and classification]
      T[Phrase and trend detection]
      V[Retrieval and investigation stages]
    end

    subgraph Product[Product layer]
      S[(PostgreSQL / pgvector and optional Redis)]
      A[FastAPI]
      U[React frontend]
    end

    G --> C
    H --> C
    R -. planned .-> C
    W --> C
    O --> C
    P -. planned .-> C
    C --> K
    C -->|candidate URL requires evidence| F
    F --> K
    K --> N
    N --> D
    D --> T
    D --> V
    T --> S
    V --> S
    S --> A
    A --> U
```

### Scaled production topology

```mermaid
flowchart LR
    S[APIs, feeds, streams, and approved pages] --> C[Checkpointed source connectors]
    C --> K[(Kafka)]
    K --> P[Normalization and stream processing]
    P --> K
    K --> D[(Durable document, search, graph, and cache stores)]
    K --> I[Investigation workers]
    D --> I
    I --> K
    D --> A[FastAPI]
    K --> A
    A --> U[React frontend]
```

Kafka provides durable replay, back-pressure, failure isolation, and independent scaling. Source connectors do not call processing or investigation workers directly in this topology.

## Collection responsibilities

### Discovery versus evidence

Discovery APIs answer “which records or URLs might matter?” They are not automatically the evidence used in a report.

When a provider returns only a title, URL, or snippet, RhetoriQ should retrieve the canonical source where permitted and store:

- provider and query;
- provider rank or score;
- original and final canonical URL;
- source-native identifier;
- title, author, and publication timestamp;
- collection timestamp;
- extracted text or permitted excerpt;
- retrieval status and content type;
- content hash and parser version;
- applicable retention or deletion policy.

If the canonical page cannot be retrieved, the provider result may remain a discovery record, but the limitation must be visible and its evidentiary weight reduced.

### Connector contract

Every production connector should expose equivalent behavior even when transports differ:

```python
class SourceConnector(Protocol):
    name: str
    capabilities: ConnectorCapabilities

    def collect(self, request: CollectionRequest) -> CollectionBatch: ...
    def checkpoint(self) -> ConnectorCheckpoint: ...
```

Capabilities should include:

- `supports_history`;
- `supports_incremental_sync`;
- `supports_full_text`;
- `supports_streaming`;
- `requires_canonical_fetch`;
- `rate_limit_policy`;
- `retention_policy`;
- `deletion_sync_policy`.

### Failure isolation

Connectors fail independently. A Reddit authorization failure, dead RSS feed, GDELT throttle, or canonical-page timeout must not stop other collection lanes. Each batch reports partial success, provider-specific errors, retryability, and the last committed checkpoint.

## Current connectors

| Connector | Transport | Current role | Full text |
|---|---|---|---|
| GDELT | DOC 2.0 JSON API | News discovery and trend signals | No; current mapping uses title/snippet and canonical URL. |
| Hacker News | Algolia HN Search API | Community/forum discovery | Story metadata and title; linked pages require canonical fetch. |
| Canonical page | Direct HTTP GET | Evidence enrichment after discovery | Extracted from retrievable HTML. |
| SearXNG search adapter | Implemented in A2 | Agent-selected broad discovery and coverage fallback | Discovery receipts; canonical fetch normally required. |
| Isolated browser adapter | Implemented in A2 | Agent-selected public-web rendering | Controlled navigation; normalized pages and retrieval receipts are the evidence. |

The current trending detector polls a fixed seed-topic list. Production discovery should add broader query generation and connector-specific incremental collection rather than treating those seeds as complete coverage.

## Delivery order for research tools and connectors

1. Strengthen the self-hosted LangGraph investigator's receipt-preserving broad search, canonical fetch, source policy, and visible fallback behavior.
2. Add one first-party public-record API such as Federal Register or Congress.gov as an agent-selected research tool.
3. Persist normalized research documents and receipts for later corpus reuse, without requiring a scheduled collector.
4. Add RSS/Atom monitoring with ETag, `Last-Modified`, and item-ID checkpoints when continuous coverage is a product requirement.
5. Add Bluesky Jetstream or an equivalent public event stream only after its retention and deletion policies fit the product.
6. Add Reddit only through approved official API access, with deletion and retention handling.
7. Add video or speech metadata through first-party APIs; use official transcripts or licensed caption access rather than assuming a public C-SPAN transcript API.

NewsAPI may be evaluated as a supplemental discovery provider, but it is not the default evidence store and must not be described as a production dependency without a suitable paid license and content-use review.

## Normalized document boundary

All transports map into the shared `Document` contract before analysis. Important fields include source identity, source type, canonical URL, publication and collection timestamps, text/snippet, entities, phrases, and provider metadata.

Source types describe the nature of the evidence (`national_news`, `local_news`, `forum`, `blog`, `commentary`, `speech_transcript`), not the transport used to acquire it. Provider names such as `gdelt` or `brave_search` belong in metadata.

## Processing and investigation

After normalization, deterministic services perform:

- deduplication and source classification;
- phrase extraction and spike scoring;
- semantic retrieval and source-diversity measurement;
- timeline and provenance construction;
- narrative-family and mutation analysis;
- counter-frame and skeptic analysis;
- receipt generation and final report assembly.

The system must preserve caveats from collection through synthesis. Provider coverage, failed fetches, missing official sources, uncertain dates, and duplicate syndication all affect confidence.

## Kafka event architecture

Kafka is the implemented durable production boundary, not the definition of a source connector:

```mermaid
flowchart LR
    C[Connector workers] --> R[raw.documents.v1]
    R --> P[Normalization and enrichment]
    P --> D[documents.processed.v1]
    D --> X[Indexes and durable stores]
    D --> Q[Signal detection]
    Q --> E[signals.detected.v1]
    E --> I[Investigation workflow]
    I --> O[investigations.completed.v1]
```

A single versioned raw-document topic is preferred unless volume, retention, security, or ordering requirements justify source-specific topics. The event includes `provider`, `transport`, and `source_type`, so downstream consumers do not depend on connector deployment names.

See [KAFKA.md](KAFKA.md) for the implemented event contracts, retention, workers, DLQs, and replay commands.

## Storage evolution

Development uses lightweight persistence so the product can run locally. The production target separates access patterns:

- PostgreSQL for durable documents and investigation artifacts;
- pgvector or an equivalent vector index for semantic retrieval;
- a full-text index when PostgreSQL search is insufficient;
- a graph store only when graph queries justify its operational cost;
- Redis for caches, ephemeral state, rate-limit coordination, and optional vector/memory features.

These stores are implementation choices, not evidence sources. Canonical provenance remains in the normalized document and receipt contracts.

## Security, compliance, and retention

Before enabling a connector in production:

- review provider terms and intended commercial use;
- document allowed storage, display, and redistribution;
- implement credential isolation and rotation;
- respect robots and access controls for direct retrieval;
- identify personal-data and deletion obligations;
- define retention and revalidation rules;
- record parser and connector versions for auditability.

No connector may bypass authentication, paywalls, technical access controls, or provider rate limits.

## Technology decisions

| Decision | Choice | Reason |
|---|---|---|
| Collection abstraction | Capability-based source connectors | Keeps APIs, feeds, streams, and fetches behind one normalized boundary. |
| Default acquisition | APIs and feeds | More stable metadata, clearer identifiers, and lower operational fragility. |
| Investigation orchestration | LangGraph | Supports a durable, bounded workflow that combines deterministic evidence stages with autonomous tool selection. |
| Broad discovery | Pluggable self-operated search adapters | Maintains provider independence and avoids a managed-SaaS dependency. |
| Evidence enrichment | Canonical HTTP retrieval | Preserves the source page behind aggregator metadata when permitted. |
| Browser automation | Self-operated browser adapter selected by the investigator | Covers public rendered and interactive pages without making browser use mandatory. |
| Agent observability | RhetoriQ-controlled traces and evaluations | Keeps investigation telemetry under project control without a managed-SaaS dependency. |
| Production messaging | Apache Kafka KRaft plus versioned JSON Schema events | Enables replay and independent scaling without leaking deployment names into schemas. |
| Evidence language | “First observed in our dataset” | Coverage cannot prove true origin. |
