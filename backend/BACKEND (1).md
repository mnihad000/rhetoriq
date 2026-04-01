# RhetoriQ — Backend

> This document covers the backend API layer. Every endpoint, request/response schema, database schemas for all 4 databases, authentication, WebSocket implementation, and caching strategy. If you are working on anything in `backend/api/`, start here.

---

## Overview

The backend API is a **FastAPI** application that serves as the bridge between the data layer and the frontend. It reads from all four databases and the `investigations.complete` Kafka topic, and exposes data via REST endpoints and a WebSocket connection for live updates.

### Responsibilities
- Serve completed investigation reports
- Serve live narrative feed (active anomalies being investigated)
- Serve Neo4j graph data for spread visualization
- Serve historical phrase search
- WebSocket endpoint for real-time investigation updates

### Tech Stack
- **FastAPI** — async Python web framework
- **Uvicorn** — ASGI server
- **psycopg2** — PostgreSQL client
- **elasticsearch-py** — Elasticsearch client
- **neo4j** — Neo4j Python driver
- **redis-py** — Redis client
- **kafka-python** — Kafka consumer for live updates

---

## Project Structure

```
backend/api/
├── main.py                  # FastAPI app entry point, router registration
├── dependencies.py          # Shared dependencies (DB connections, auth)
├── routers/
│   ├── investigations.py    # Investigation report endpoints
│   ├── narratives.py        # Live narrative feed endpoints
│   ├── search.py            # Phrase search endpoints
│   ├── graph.py             # Neo4j graph data endpoints
│   └── health.py            # Health check endpoint
├── models/
│   ├── investigation.py     # Pydantic models for investigations
│   ├── narrative.py         # Pydantic models for narratives
│   └── graph.py             # Pydantic models for graph data
├── services/
│   ├── postgres_service.py  # PostgreSQL query logic
│   ├── es_service.py        # Elasticsearch query logic
│   ├── neo4j_service.py     # Neo4j query logic
│   ├── redis_service.py     # Redis caching logic
│   └── kafka_service.py     # Kafka consumer for live updates
├── websocket/
│   └── manager.py           # WebSocket connection manager
├── config.py                # Environment variable loading
└── requirements.txt
```

---

## Running Locally

```bash
cd backend/api
pip install -r requirements.txt

# Start the API
uvicorn main:app --reload --port 8000

# Interactive API docs
open http://localhost:8000/docs

# ReDoc documentation
open http://localhost:8000/redoc
```

### Environment Variables

```env
POSTGRES_URL=postgresql://user:password@localhost:5432/rhetoriq
ELASTICSEARCH_URL=http://localhost:9200
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=
REDIS_URL=redis://localhost:6379
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
FRONTEND_URL=http://localhost:3000
API_SECRET_KEY=your_secret_key_here
```

---

## Authentication

The API uses **JWT Bearer token** authentication on all endpoints except `/health` and `/docs`.

```python
# dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(
            credentials.credentials,
            os.getenv("API_SECRET_KEY"),
            algorithms=["HS256"]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
```

For local development, disable auth by setting `DISABLE_AUTH=true` in `.env`.

---

## REST API Endpoints

### Base URL
```
Local:      http://localhost:8000/api/v1
Production: https://api.rhetoriq.com/api/v1
```

---

### Investigations

#### GET /investigations
Returns a paginated list of completed investigation reports, sorted by most recent.

**Query Parameters**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | integer | 1 | Page number |
| `limit` | integer | 20 | Results per page (max 100) |
| `source` | string | null | Filter by origin source (reddit, newsapi, rss, gdelt, cspan) |
| `days_back` | integer | 30 | How many days back to fetch |

**Response**
```json
{
  "total": 142,
  "page": 1,
  "limit": 20,
  "results": [
    {
      "investigation_id": "a1b2c3d4",
      "phrase": "climate lockdowns",
      "completed_at": "2025-03-15T14:23:11Z",
      "duration_seconds": 47,
      "origin": {
        "source": "reddit",
        "outlet_or_subreddit": "r/conspiracy",
        "published_at": "2025-01-28T09:14:00Z",
        "confidence": 0.87
      },
      "spread_path_length": 5,
      "key_amplifier_count": 3,
      "pattern_classification": "grassroots"
    }
  ]
}
```

---

#### GET /investigations/{investigation_id}
Returns the full investigation report for a single investigation.

**Path Parameters**
| Parameter | Type | Description |
|---|---|---|
| `investigation_id` | string | UUID of the investigation |

**Response**
```json
{
  "investigation_id": "a1b2c3d4",
  "anomaly_id": "x9y8z7w6",
  "phrase": "climate lockdowns",
  "started_at": "2025-03-15T14:22:24Z",
  "completed_at": "2025-03-15T14:23:11Z",
  "duration_seconds": 47,
  "origin": {
    "document_id": "doc_abc123",
    "source": "reddit",
    "outlet_or_subreddit": "r/conspiracy",
    "published_at": "2025-01-28T09:14:00Z",
    "url": "https://reddit.com/r/conspiracy/...",
    "confidence": 0.87
  },
  "spread_path": [
    {
      "stage": 1,
      "source": "reddit",
      "outlet_or_subreddit": "r/conspiracy",
      "published_at": "2025-01-28T09:14:00Z",
      "document_count": 3
    },
    {
      "stage": 2,
      "source": "rss",
      "outlet_or_subreddit": "Breitbart",
      "published_at": "2025-01-31T16:45:00Z",
      "document_count": 1
    }
  ],
  "key_amplifiers": [
    {
      "source_id": "breitbart",
      "name": "Breitbart",
      "type": "outlet",
      "amplification_count": 4
    }
  ],
  "report": "## Narrative: climate lockdowns\n\n### Summary\n...",
  "similar_document_count": 847,
  "graph_node_count": 12
}
```

---

#### GET /investigations/search
Search investigations by phrase or keyword.

**Query Parameters**
| Parameter | Type | Description |
|---|---|---|
| `q` | string | Search query |
| `limit` | integer | Max results (default 20) |

**Response** — same shape as `GET /investigations` results array.

---

### Narratives (Live Feed)

#### GET /narratives/active
Returns currently active anomalies — narratives that have been detected but not yet fully investigated.

**Response**
```json
{
  "active_count": 3,
  "narratives": [
    {
      "anomaly_id": "p1q2r3s4",
      "phrase": "digital dollar surveillance",
      "detected_at": "2025-03-15T14:30:00Z",
      "spike_magnitude": 3.8,
      "status": "investigating",
      "investigation_started_at": "2025-03-15T14:30:05Z",
      "top_sources": [
        {"source": "reddit", "subreddit": "Libertarian", "count": 89}
      ]
    }
  ]
}
```

---

#### GET /narratives/trending
Returns the top 10 narratives by spike magnitude over the last 24 hours.

**Query Parameters**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `hours_back` | integer | 24 | Lookback window |
| `limit` | integer | 10 | Number of results |

**Response** — array of investigation summaries sorted by `spike_magnitude` descending.

---

#### GET /narratives/timeline
Returns a time-series of narrative detections for charting.

**Query Parameters**
| Parameter | Type | Description |
|---|---|---|
| `days_back` | integer | Lookback window (default 7) |
| `interval` | string | `hour` or `day` (default `hour`) |

**Response**
```json
{
  "interval": "hour",
  "data": [
    {
      "timestamp": "2025-03-15T00:00:00Z",
      "anomaly_count": 3,
      "investigation_count": 3
    }
  ]
}
```

---

### Search

#### GET /search/phrases
Full-text search across all ingested documents.

**Query Parameters**
| Parameter | Type | Description |
|---|---|---|
| `q` | string | Phrase to search for |
| `days_back` | integer | Lookback window (default 90) |
| `source` | string | Filter by source |
| `limit` | integer | Max results (default 50) |

**Response**
```json
{
  "total_hits": 234,
  "results": [
    {
      "document_id": "doc_abc123",
      "source": "reddit",
      "outlet_or_subreddit": "r/politics",
      "url": "https://...",
      "published_at": "2025-02-14T11:22:00Z",
      "excerpt": "...matched phrase highlighted here..."
    }
  ]
}
```

---

#### GET /search/semantic
Semantic similarity search — finds documents related to a concept even without exact phrase match.

**Query Parameters**
| Parameter | Type | Description |
|---|---|---|
| `q` | string | Concept or phrase to search |
| `days_back` | integer | Lookback window (default 90) |
| `limit` | integer | Max results (default 20) |
| `min_similarity` | float | Minimum similarity score 0-1 (default 0.75) |

**Response** — same shape as `/search/phrases` with added `similarity_score` field per result.

---

### Graph

#### GET /graph/{investigation_id}
Returns Neo4j graph data for a specific investigation, formatted for the frontend visualization library.

**Response**
```json
{
  "nodes": [
    {
      "id": "r_conspiracy",
      "label": "r/conspiracy",
      "type": "subreddit",
      "size": 3,
      "political_lean": "far-right",
      "first_published_at": "2025-01-28T09:14:00Z"
    }
  ],
  "edges": [
    {
      "source": "r_conspiracy",
      "target": "breitbart",
      "weight": 2,
      "first_amplified_at": "2025-01-31T16:45:00Z"
    }
  ]
}
```

---

#### GET /graph/source/{source_id}
Returns the full spread history for a specific source — all narratives it has originated or amplified.

---

### Health

#### GET /health
No authentication required. Returns service health status.

**Response**
```json
{
  "status": "healthy",
  "timestamp": "2025-03-15T14:23:11Z",
  "dependencies": {
    "postgres": "healthy",
    "elasticsearch": "healthy",
    "neo4j": "healthy",
    "redis": "healthy",
    "kafka": "healthy"
  }
}
```

---

## WebSocket

### Endpoint
```
ws://localhost:8000/ws
wss://api.rhetoriq.com/ws  (production)
```

### Connection

```javascript
// Frontend connection
const ws = new WebSocket(`${WS_URL}?token=${jwt_token}`);

ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  handleMessage(message);
};
```

### Message Types

The server pushes these message types to all connected clients:

#### anomaly_detected
Sent when Flink publishes a new anomaly.
```json
{
  "type": "anomaly_detected",
  "payload": {
    "anomaly_id": "p1q2r3s4",
    "phrase": "digital dollar surveillance",
    "detected_at": "2025-03-15T14:30:00Z",
    "spike_magnitude": 3.8,
    "top_sources": []
  }
}
```

#### investigation_started
Sent when the agent picks up an anomaly.
```json
{
  "type": "investigation_started",
  "payload": {
    "anomaly_id": "p1q2r3s4",
    "investigation_id": "a1b2c3d4",
    "phrase": "digital dollar surveillance",
    "started_at": "2025-03-15T14:30:05Z"
  }
}
```

#### investigation_complete
Sent when the agent finishes an investigation.
```json
{
  "type": "investigation_complete",
  "payload": {
    "investigation_id": "a1b2c3d4",
    "phrase": "digital dollar surveillance",
    "completed_at": "2025-03-15T14:30:52Z",
    "duration_seconds": 47,
    "pattern_classification": "grassroots",
    "origin_source": "reddit"
  }
}
```

### WebSocket Manager

```python
# websocket/manager.py
from fastapi import WebSocket
from typing import list

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.active_connections.remove(conn)

manager = ConnectionManager()
```

---

## Database Schemas

### PostgreSQL

```sql
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Documents table
CREATE TABLE documents (
    id              UUID PRIMARY KEY,
    source          VARCHAR(50) NOT NULL,
    source_id       VARCHAR(255) NOT NULL,
    url             TEXT,
    title           TEXT,
    body_clean      TEXT,
    author          VARCHAR(255),
    published_at    TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL,
    processed_at    TIMESTAMPTZ NOT NULL,
    embedding       vector(384),
    metadata        JSONB,
    UNIQUE(source, source_id)
);

-- Index for vector similarity search
CREATE INDEX ON documents 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Index for time-based queries
CREATE INDEX idx_documents_published_at ON documents(published_at DESC);
CREATE INDEX idx_documents_source ON documents(source);

-- Entities table
CREATE TABLE entities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    type        VARCHAR(50) NOT NULL,  -- PER, ORG, LOC
    value       VARCHAR(255) NOT NULL,
    confidence  FLOAT NOT NULL
);

CREATE INDEX idx_entities_document_id ON entities(document_id);
CREATE INDEX idx_entities_value ON entities(value);

-- Sources table
CREATE TABLE sources (
    id                      VARCHAR(255) PRIMARY KEY,
    name                    VARCHAR(255) NOT NULL,
    type                    VARCHAR(50) NOT NULL,  -- subreddit, outlet, politician
    political_lean          VARCHAR(50),
    audience_size_estimate  INTEGER,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- Anomalies table
CREATE TABLE anomalies (
    id                  UUID PRIMARY KEY,
    detected_at         TIMESTAMPTZ NOT NULL,
    phrase              TEXT NOT NULL,
    spike_magnitude     FLOAT NOT NULL,
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    baseline_frequency  INTEGER NOT NULL,
    window_frequency    INTEGER NOT NULL,
    top_sources         JSONB,
    status              VARCHAR(50) DEFAULT 'detected'  -- detected, investigating, complete, failed
);

CREATE INDEX idx_anomalies_detected_at ON anomalies(detected_at DESC);
CREATE INDEX idx_anomalies_phrase ON anomalies(phrase);

-- Investigations table
CREATE TABLE investigations (
    id                      UUID PRIMARY KEY,
    anomaly_id              UUID REFERENCES anomalies(id),
    phrase                  TEXT NOT NULL,
    started_at              TIMESTAMPTZ NOT NULL,
    completed_at            TIMESTAMPTZ,
    duration_seconds        INTEGER,
    origin                  JSONB,
    spread_path             JSONB,
    key_amplifiers          JSONB,
    pattern_classification  VARCHAR(50),
    report                  TEXT,
    similar_document_count  INTEGER,
    graph_node_count        INTEGER
);

CREATE INDEX idx_investigations_completed_at ON investigations(completed_at DESC);
CREATE INDEX idx_investigations_phrase ON investigations(phrase);
```

---

### Elasticsearch

```json
// Index: documents
{
  "mappings": {
    "properties": {
      "id":           { "type": "keyword" },
      "source":       { "type": "keyword" },
      "source_id":    { "type": "keyword" },
      "url":          { "type": "keyword", "index": false },
      "title":        { "type": "text", "analyzer": "english" },
      "body_clean":   { "type": "text", "analyzer": "english" },
      "author":       { "type": "keyword" },
      "published_at": { "type": "date" },
      "metadata": {
        "properties": {
          "outlet":     { "type": "keyword" },
          "subreddit":  { "type": "keyword" },
          "upvotes":    { "type": "integer" }
        }
      }
    }
  },
  "settings": {
    "number_of_shards": 3,
    "number_of_replicas": 1,
    "analysis": {
      "analyzer": {
        "english": {
          "tokenizer": "standard",
          "filter": ["lowercase", "english_stop", "english_stemmer"]
        }
      }
    }
  }
}
```

---

### Neo4j

```cypher
-- Constraints (run once on setup)
CREATE CONSTRAINT source_id_unique IF NOT EXISTS
FOR (s:Source) REQUIRE s.id IS UNIQUE;

CREATE CONSTRAINT document_id_unique IF NOT EXISTS
FOR (d:Document) REQUIRE d.id IS UNIQUE;

CREATE CONSTRAINT phrase_text_unique IF NOT EXISTS
FOR (p:Phrase) REQUIRE p.text IS UNIQUE;

-- Indexes
CREATE INDEX source_name IF NOT EXISTS FOR (s:Source) ON (s.name);
CREATE INDEX document_published_at IF NOT EXISTS FOR (d:Document) ON (d.published_at);
CREATE INDEX phrase_text IF NOT EXISTS FOR (p:Phrase) ON (p.text);

-- Node schemas (reference)

-- Source node
// {
//   id: string,
//   name: string,
//   type: "subreddit" | "outlet" | "politician",
//   political_lean: string,
//   audience_size_estimate: integer
// }

-- Document node
// {
//   id: string (uuid),
//   source: string,
//   published_at: datetime,
//   url: string
// }

-- Phrase node
// {
//   text: string,
//   first_seen: datetime,
//   total_occurrences: integer
// }

-- Relationships
// (Source)-[:PUBLISHED]->(Document)
// (Document)-[:CONTAINS]->(Phrase)
// (Source)-[:AMPLIFIED {count: integer, first_at: datetime}]->(Source)
```

---

### Redis Key Schema

| Key Pattern | Type | TTL | Description |
|---|---|---|---|
| `source_profile:{source_id}` | String (JSON) | 1 hour | Cached source metadata |
| `active_anomalies` | Set | — | Set of currently active anomaly IDs |
| `investigation:{id}:status` | String | 24 hours | Current status of an investigation |
| `phrase_baseline:{phrase}` | String (float) | 24 hours | Cached baseline frequency for anomaly detection |
| `doc_bloom_filter` | Bloom Filter | — | Document deduplication |
| `trending:24h` | Sorted Set | 1 hour | Top narratives by spike magnitude |
| `ws:connections` | Integer | — | Active WebSocket connection count |

---

## Caching Strategy

The API caches aggressively to keep response times under 100ms for common queries.

| Endpoint | Cache Key | TTL | Cache Layer |
|---|---|---|---|
| `GET /investigations` | `investigations:page:{n}:limit:{n}` | 5 minutes | Redis |
| `GET /investigations/{id}` | `investigation:{id}` | 1 hour | Redis |
| `GET /narratives/trending` | `trending:24h` | 15 minutes | Redis |
| `GET /graph/{id}` | `graph:{investigation_id}` | 1 hour | Redis |
| `GET /search/phrases` | `search:{hash(query)}` | 10 minutes | Redis |

Cache is invalidated automatically when a new investigation completes via the Kafka consumer.

---

## Error Handling

All endpoints return errors in this standard format:

```json
{
  "error": {
    "code": "INVESTIGATION_NOT_FOUND",
    "message": "No investigation found with id a1b2c3d4",
    "timestamp": "2025-03-15T14:23:11Z"
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|---|---|---|
| `INVESTIGATION_NOT_FOUND` | 404 | Investigation ID does not exist |
| `INVALID_QUERY` | 400 | Missing or malformed query parameter |
| `UNAUTHORIZED` | 401 | Missing or invalid JWT token |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Unexpected server error |
| `DATABASE_UNAVAILABLE` | 503 | One or more databases unreachable |
