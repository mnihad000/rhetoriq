# RhetoriQ — Architecture

> This document covers the full system design of RhetoriQ. Every layer, every technology choice, and the reasoning behind each decision. Read this before touching any code.

---

## System Philosophy

RhetoriQ is built around three core principles:

1. **Nothing talks to anything directly.** Every service communicates through Kafka. This means any single service can die without cascading failures across the system.
2. **Each database has exactly one job.** We use four databases and each one does something the others cannot. There is no redundancy — each is chosen for a specific access pattern.
3. **The agent is fully autonomous.** Once deployed, RhetoriQ requires zero human prompting. It detects, investigates, and reports by itself.

---

## High Level Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                             │
│   Reddit API │ GDELT │ NewsAPI │ C-SPAN │ RSS Feeds             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SCRAPER MICROSERVICES                         │
│   One Python service per data source, running on Kubernetes     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ publishes raw documents
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                         KAFKA                                    │
│   raw.reddit │ raw.news │ raw.speeches │ raw.gdelt              │
└──────────────────────────┬──────────────────────────────────────┘
                           │ consumes raw topics
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      APACHE FLINK                                │
│   Clean → Extract Entities → Detect Anomalies → Embed           │
└────────────┬──────────────────────────────┬─────────────────────┘
             │ publishes                    │ publishes
             ▼                              ▼
    documents.processed            anomalies.detected
             │                              │
             ▼                              ▼
┌────────────────────────┐    ┌─────────────────────────────────┐
│      STORAGE LAYER     │    │       LANGCHAIN AGENT            │
│                        │    │                                  │
│  PostgreSQL + pgvector │    │  Autonomous investigation loop   │
│  Elasticsearch         │◄───│  Queries all 4 databases         │
│  Neo4j                 │    │  Calls GPT-4o for synthesis      │
│  Redis                 │    │                                  │
└────────────────────────┘    └──────────────┬────────────────────┘
                                             │ publishes
                                             ▼
                                  investigations.complete
                                             │
                                             ▼
                              ┌──────────────────────────┐
                              │        REST API           │
                              │   FastAPI serving         │
                              │   the frontend            │
                              └──────────────┬────────────┘
                                             │
                                             ▼
                              ┌──────────────────────────┐
                              │        FRONTEND           │
                              │   React + TypeScript      │
                              │   Neo4j graph viz         │
                              │   WebSocket live updates  │
                              └──────────────────────────┘
```

---

## Layer 1 — Data Ingestion

### Overview
Five independent scraper microservices run continuously on Kubernetes. Each scraper is responsible for exactly one data source. They do zero processing — their only job is to fetch raw content and publish it to Kafka as fast as possible.

### Scrapers

| Scraper | Source | Kafka Topic | Poll Interval |
|---|---|---|---|
| `reddit-scraper` | Reddit API (PRAW) | `raw.reddit` | 30 seconds |
| `news-scraper` | NewsAPI + RSS Feeds | `raw.news` | 60 seconds |
| `gdelt-scraper` | GDELT Project | `raw.gdelt` | 15 minutes |
| `cspan-scraper` | C-SPAN API | `raw.speeches` | 1 hour |
| `rss-scraper` | NYT, BBC, Fox, Reuters, Breitbart | `raw.news` | 60 seconds |

### Raw Message Schema
Every scraper publishes documents in this standard format:
```json
{
  "id": "uuid-v4",
  "source": "reddit",
  "source_id": "original_id_from_source",
  "url": "https://...",
  "title": "string or null",
  "body": "full text content",
  "author": "string or null",
  "published_at": "ISO 8601 timestamp",
  "metadata": {
    "subreddit": "politics",
    "upvotes": 1200,
    "outlet": null
  },
  "ingested_at": "ISO 8601 timestamp"
}
```

### Why separate scrapers per source?
If Reddit's API goes down, the news scraper keeps running. If we want to add a new source, we add one new microservice without touching anything else. Each scraper can also be scaled independently based on volume.

---

## Layer 2 — Kafka

### Overview
Kafka is the central nervous system of RhetoriQ. Every service communicates exclusively through Kafka topics. No service calls another service directly.

### Topics

| Topic | Producer | Consumer | Description |
|---|---|---|---|
| `raw.reddit` | reddit-scraper | Flink | Raw Reddit posts |
| `raw.news` | news-scraper, rss-scraper | Flink | Raw news articles |
| `raw.speeches` | cspan-scraper | Flink | Raw speech transcripts |
| `raw.gdelt` | gdelt-scraper | Flink | Raw GDELT events |
| `documents.processed` | Flink | Storage workers | Cleaned, embedded documents |
| `anomalies.detected` | Flink | LangChain agent | Detected narrative spikes |
| `investigations.complete` | LangChain agent | REST API | Finished investigation reports |

### Partition Strategy
- `raw.*` topics: 6 partitions each (parallelizes ingestion)
- `documents.processed`: 12 partitions (highest volume topic)
- `anomalies.detected`: 3 partitions (low volume, high priority)
- `investigations.complete`: 3 partitions (low volume)

See [KAFKA.md](./KAFKA.md) for full topic configuration and consumer group details.

---

## Layer 3 — Apache Flink

### Overview
Flink sits between raw Kafka topics and the storage layer. It consumes raw documents, processes them in real time, and outputs to two destinations: processed documents for storage, and anomaly alerts for the agent.

### Flink Pipeline Steps

#### Step 1 — Clean & Normalize
- Strip HTML tags
- Remove duplicates (using Redis bloom filter)
- Normalize timestamps to UTC
- Truncate documents over 10,000 characters

#### Step 2 — Entity Extraction
Using a HuggingFace NER (Named Entity Recognition) model:
- Extract politician names
- Extract organization names
- Extract locations
- Extract key phrases (noun chunks over 3 words)

#### Step 3 — Anomaly Detection
Using a Flink tumbling window (10 minutes):
- Count phrase frequency per window
- Compare against rolling 7-day baseline
- If frequency > 3x baseline: publish to `anomalies.detected`
- Anomaly payload includes the phrase, spike magnitude, and top sources

#### Step 4 — Embedding Generation
Using HuggingFace `sentence-transformers/all-MiniLM-L6-v2`:
- Generate a 384-dimension vector embedding for each document
- Attach embedding to the processed document payload
- Publish to `documents.processed`

### Why Flink over Kafka Streams?
Flink has superior windowing capabilities for anomaly detection and handles stateful stream processing more cleanly at scale. Kafka Streams would work but Flink's exactly-once semantics and richer windowing API make it the right tool for the anomaly detection step specifically.

---

## Layer 4 — Storage

### Overview
Four databases, each with a single responsibility. Never use one where another is the correct tool.

### PostgreSQL + pgvector
**Job: Semantic similarity search**

Stores every processed document alongside its vector embedding. When the agent needs to find documents that are semantically similar to a detected phrase, it queries pgvector.

```sql
-- Example: find the 20 most semantically similar documents to a given embedding
SELECT id, body, source, published_at
FROM documents
ORDER BY embedding <-> $1  -- pgvector cosine distance operator
LIMIT 20;
```

Key tables: `documents`, `entities`, `phrases`

### Elasticsearch
**Job: Full-text search and phrase tracking over time**

Stores the full text of every document. Used to find exact and near-exact phrase matches, track how a specific phrase has appeared over time, and power the search feature in the frontend.

Key indices: `documents`, `phrases`

### Neo4j
**Job: Spread mapping**

Every time source A publishes something and source B republishes or references it, a directed edge is created in the graph. This builds a live map of how narratives travel.

```cypher
// Example: find the origin of a narrative and all nodes it passed through
MATCH path = (origin:Source)-[:AMPLIFIED*]->(mainstream:Source {type: 'politician'})
WHERE origin.type = 'fringe'
RETURN path
ORDER BY length(path)
```

Key nodes: `Source`, `Document`, `Phrase`
Key relationships: `PUBLISHED`, `AMPLIFIED`, `REFERENCED`

### Redis
**Job: Caching and deduplication**

- Caches active investigation states so the agent doesn't re-query Postgres on every step
- Stores a bloom filter for document deduplication in Flink
- Caches politician and source profiles
- TTL of 1 hour on most keys

---

## Layer 5 — The LangChain Agent

### Overview
The agent is the core of RhetoriQ. It runs as a Kubernetes deployment, continuously consuming from `anomalies.detected`. When an anomaly arrives, it autonomously conducts a full investigation without any human input.

### Investigation Loop

```
anomalies.detected
        │
        ▼
1. Parse anomaly — extract phrase, spike data, top sources
        │
        ▼
2. Search backwards — pgvector semantic search for earliest occurrences
        │
        ▼
3. Trace graph — Neo4j traversal to map spread network
        │
        ▼
4. Full text search — Elasticsearch for every exact/near-exact usage with timestamps
        │
        ▼
5. Synthesize — GPT-4o receives all findings, generates human-readable report
        │
        ▼
6. Publish — report pushed to investigations.complete Kafka topic
```

### Tools Available to the Agent
The LangChain agent has access to these tools:
- `semantic_search(query, limit)` — queries pgvector
- `graph_trace(phrase)` — queries Neo4j for spread path
- `full_text_search(phrase, date_range)` — queries Elasticsearch
- `get_source_profile(source_id)` — queries Redis/Postgres for source metadata
- `synthesize_report(findings)` — calls GPT-4o with structured findings

See [AGENT.md](./AGENT.md) for the full agent prompt strategy and tool implementations.

---

## Layer 6 — REST API

### Overview
A FastAPI service that sits between the storage layer and the frontend. It reads from the databases and the `investigations.complete` Kafka topic and serves data to the frontend via REST and WebSockets.

### Key Responsibilities
- Serve completed investigation reports
- Serve live narrative feed (active anomalies being investigated)
- Serve graph data for Neo4j visualization
- WebSocket endpoint for live investigation updates

See [backend/BACKEND.md](../backend/BACKEND.md) for full endpoint documentation.

---

## Layer 7 — Frontend

### Overview
A React + TypeScript dashboard with three main views:

1. **Live Feed** — real-time stream of detected anomalies and active investigations
2. **Investigation Report** — full provenance report for a completed investigation
3. **Spread Graph** — interactive Neo4j graph visualization showing how a narrative traveled

See [frontend/FRONTEND.md](../frontend/FRONTEND.md) for full component documentation.

---

## Infrastructure

### Kubernetes
Every service runs as a Kubernetes deployment. Scrapers, Flink jobs, the agent, and the API are all separate deployments with independent scaling rules.

### Terraform
All infrastructure is provisioned as code. The entire system can be torn down and rebuilt with:
```bash
terraform destroy
terraform apply
```

### ArgoCD
GitOps continuous deployment. Every merge to `main` automatically triggers a deployment. No manual `kubectl apply` ever.

### Observability
- **Prometheus** scrapes metrics from every service
- **Grafana** dashboards for Kafka consumer lag, Flink throughput, agent investigation latency, and database query times

See [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) for full setup instructions.

---

## Technology Decision Log

| Decision | Chosen | Alternatives Considered | Reason |
|---|---|---|---|
| Message broker | Kafka | RabbitMQ, AWS SQS | Kafka's durability and replay capability is essential for a system processing historical data |
| Stream processor | Flink | Kafka Streams, Spark Streaming | Flink's stateful windowing is superior for anomaly detection |
| Vector DB | pgvector | Pinecone, Weaviate | Keeps vector search inside Postgres, reduces operational complexity |
| Graph DB | Neo4j | Amazon Neptune, ArangoDB | Best-in-class graph query language (Cypher), strong visualization ecosystem |
| Agent framework | LangChain | LlamaIndex, raw OpenAI | LangChain's tool-use abstraction maps cleanly to our investigation steps |
| LLM | GPT-4o | Claude, Gemini | Best reasoning capability for synthesis tasks at the time of build |
| Infra as code | Terraform | Pulumi, CDK | Most widely used, best documentation, most recruiter-recognizable |
| CD | ArgoCD | FluxCD, Jenkins X | GitOps model fits cleanly with our Kubernetes-first architecture |
