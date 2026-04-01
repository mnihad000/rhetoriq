# RhetoriQ — Services

> This document covers every microservice in RhetoriQ. Each service is documented individually — what it does, what it consumes and produces, its dependencies, environment variables, how to run it in isolation, and common failure modes.

---

## Overview

RhetoriQ is composed of 10 microservices. Each runs as an independent Kubernetes deployment and communicates exclusively through Kafka.

| Service | Language | Kafka Consumes | Kafka Produces | Description |
|---|---|---|---|---|
| `reddit-scraper` | Python | — | `raw.reddit` | Streams Reddit posts |
| `news-scraper` | Python | — | `raw.news` | Polls NewsAPI |
| `rss-scraper` | Python | — | `raw.news` | Polls RSS feeds |
| `gdelt-scraper` | Python | — | `raw.gdelt` | Polls GDELT every 15min |
| `cspan-scraper` | Python | — | `raw.speeches` | Polls C-SPAN API |
| `flink-processor` | Python | `raw.*` | `documents.processed`, `anomalies.detected` | Cleans, embeds, detects anomalies |
| `storage-worker` | Python | `documents.processed` | — | Writes to all 4 databases |
| `agent` | Python | `anomalies.detected` | `investigations.complete` | Autonomous investigation loop |
| `api` | Python | `investigations.complete` | — | REST API + WebSocket server |
| `frontend` | TypeScript | — | — | React dashboard |

---

## 1. reddit-scraper

### What It Does
Streams new posts from monitored subreddits in real time using PRAW's streaming API. Publishes each post to `raw.reddit` immediately upon ingestion.

### Location
```
backend/scrapers/reddit_scraper.py
```

### Kafka
- **Produces:** `raw.reddit`
- **Partition key:** subreddit name

### Dependencies
- PRAW (Python Reddit API Wrapper)
- kafka-python
- Redis (bloom filter for deduplication)

### Environment Variables
```env
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=rhetoriq:v1.0 (by /u/yourusername)
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
```

### Running Locally
```bash
cd backend/scrapers
pip install -r requirements.txt
python reddit_scraper.py
```

### Key Implementation Notes
- Uses `subreddit.stream.submissions(skip_existing=True)` — only processes new posts, not historical backfill on startup
- Checks Redis bloom filter before publishing — drops duplicates silently
- Monitors these subreddits: `politics`, `worldnews`, `news`, `conspiracy`, `conservatives`, `progressive`, `Libertarian`, `PoliticalDiscussion`
- Reconnects automatically on PRAW stream timeout (common after ~16 hours)

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| `prawcore.exceptions.ResponseException: 401` | Invalid credentials | Check `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` |
| Stream silently stops | PRAW stream timeout | Implement watchdog thread that restarts stream if no message in 60s |
| `KafkaTimeoutError` | Kafka not running | Start Kafka with `docker-compose up -d` |

---

## 2. news-scraper

### What It Does
Polls NewsAPI every 60 seconds for the latest US political news articles. Publishes each article to `raw.news`.

### Location
```
backend/scrapers/news_scraper.py
```

### Kafka
- **Produces:** `raw.news`
- **Partition key:** outlet name

### Dependencies
- requests
- kafka-python
- Redis (deduplication)

### Environment Variables
```env
NEWS_API_KEY=
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
```

### Running Locally
```bash
cd backend/scrapers
python news_scraper.py
```

### Key Implementation Notes
- Polls `/v2/everything` with query `politics OR congress OR senate` sorted by `publishedAt`
- Deduplicates by URL hash stored in Redis with 24hr TTL
- On free tier, `content` is truncated — stores truncated content and flags `is_truncated: true` in metadata
- Backs off exponentially on 429 rate limit responses

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| `426 Upgrade Required` | Hit free tier limit | Switch to GDELT as primary, use NewsAPI as supplement |
| Empty results | Query too narrow | Broaden query terms |
| Duplicate articles | Redis TTL expired | Increase Redis TTL to 48hrs |

---

## 3. rss-scraper

### What It Does
Polls 8 RSS feeds from politically diverse outlets every 60 seconds. Publishes each article to `raw.news`.

### Location
```
backend/scrapers/rss_scraper.py
```

### Kafka
- **Produces:** `raw.news`
- **Partition key:** outlet name

### Dependencies
- feedparser
- requests
- kafka-python
- Redis (deduplication)

### Environment Variables
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
```

### Running Locally
```bash
cd backend/scrapers
python rss_scraper.py
```

### Key Implementation Notes
- Polls all 8 feeds concurrently using `ThreadPoolExecutor`
- Feed URLs are defined in `config/rss_feeds.json` — update there without touching code
- Uses `dateutil.parser.parse()` for robust date parsing across outlet formats
- Fetches full article body for non-paywalled outlets — implements 5 second request timeout

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| Feed returns 404 | Outlet changed RSS URL | Update `config/rss_feeds.json` |
| Empty body | Paywalled article | Use `summary` field as fallback, flag `is_paywalled: true` |
| Malformed date | Outlet using non-standard format | `dateutil.parser.parse()` handles most cases — add manual override if needed |

---

## 4. gdelt-scraper

### What It Does
Fetches the GDELT Global Knowledge Graph (GKG) update every 15 minutes. Parses the CSV and publishes each event to `raw.gdelt`. GDELT is the highest volume source — each update contains thousands of events.

### Location
```
backend/scrapers/gdelt_scraper.py
```

### Kafka
- **Produces:** `raw.gdelt`
- **Partition key:** null (round-robin)

### Dependencies
- requests
- pandas
- kafka-python

### Environment Variables
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
```

### Running Locally
```bash
cd backend/scrapers
python gdelt_scraper.py
```

### Key Implementation Notes
- Fetches manifest from `http://data.gdeltproject.org/gdeltv2/lastupdate.txt` every 15 minutes
- Caches last fetched manifest URL in memory — skips if unchanged
- Filters GKG records to English-language US political content using `SOURCECOUNTRY` and theme filters
- Parses GDELT's `YYYYMMDDHHMMSS` timestamp format explicitly — never rely on pandas auto-parsing here
- Publishes in batches of 500 using Kafka producer batching

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| Empty dataframe | GDELT CSV malformed | Use `on_bad_lines='skip'` in pandas read_csv |
| Stale data | Polling too infrequently | Ensure poll interval is exactly 15 minutes |
| High Kafka lag | Too many events per update | Increase Flink consumer parallelism |

---

## 5. cspan-scraper

### What It Does
Polls the C-SPAN API every hour for new program transcripts. Downloads and publishes full transcript text to `raw.speeches`. This is the lowest volume but highest signal source — a narrative in a C-SPAN transcript means it has reached formal political adoption.

### Location
```
backend/scrapers/cspan_scraper.py
```

### Kafka
- **Produces:** `raw.speeches`
- **Partition key:** null (round-robin)

### Dependencies
- requests
- kafka-python
- Redis (deduplication)

### Environment Variables
```env
CSPAN_API_KEY=
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
```

### Running Locally
```bash
cd backend/scrapers
python cspan_scraper.py
```

### Key Implementation Notes
- Polls with 1 hour delay after program air time — transcripts are not immediately available
- Chunks transcripts over 50,000 words into overlapping segments before publishing
- Parses speaker attribution from `>>` prefix in raw transcript text
- Stores `program_id` in Redis to avoid reprocessing

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| Transcript not available | Too soon after air time | Increase poll delay to 2 hours |
| Malformed transcript | Auto-generated captions | Flag `is_autocaption: true` — downstream NER should lower confidence |
| Missing speakers | No `>>` prefix in transcript | Fall back to `program.speakers` metadata field |

---

## 6. flink-processor

### What It Does
The most complex service in RhetoriQ. Consumes from all `raw.*` Kafka topics, runs a 4-step processing pipeline on every document, and outputs to `documents.processed` and `anomalies.detected`.

### Location
```
backend/processors/flink_job.py
```

### Kafka
- **Consumes:** `raw.reddit`, `raw.news`, `raw.speeches`, `raw.gdelt`
- **Produces:** `documents.processed`, `anomalies.detected`
- **Consumer group:** `flink-raw-consumer`

### Dependencies
- apache-flink
- kafka-python
- transformers (HuggingFace)
- torch
- Redis (bloom filter)

### Environment Variables
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
REDIS_URL=redis://localhost:6379
HUGGINGFACE_MODEL=sentence-transformers/all-MiniLM-L6-v2
HUGGINGFACE_NER_MODEL=dslim/bert-base-NER
ANOMALY_SPIKE_THRESHOLD=3.0
ANOMALY_WINDOW_MINUTES=10
```

### Running Locally
```bash
cd backend/processors
pip install -r requirements.txt
python flink_job.py
```

### Pipeline Steps

#### Step 1 — Deduplication
```python
# Check Redis bloom filter
if redis_client.bf().exists("doc_bloom_filter", document["id"]):
    return  # Drop duplicate
redis_client.bf().add("doc_bloom_filter", document["id"])
```

#### Step 2 — Cleaning
- Strip HTML with `BeautifulSoup`
- Normalize whitespace
- Truncate to 10,000 characters
- Convert timestamp to UTC

#### Step 3 — Entity Extraction
```python
# Uses dslim/bert-base-NER
ner_pipeline = pipeline("ner", model="dslim/bert-base-NER", aggregation_strategy="simple")
entities = ner_pipeline(document["body_clean"])
# Groups: PER (persons), ORG (organizations), LOC (locations)
```

#### Step 4 — Embedding
```python
# Uses sentence-transformers/all-MiniLM-L6-v2
# Produces 384-dimension vector
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
embedding = embedder.encode(document["body_clean"]).tolist()
```

#### Step 5 — Anomaly Detection
```python
# Tumbling window of 10 minutes
# Count phrase frequency per window
# Compare against 7-day rolling baseline stored in Redis
# If frequency > ANOMALY_SPIKE_THRESHOLD * baseline: publish anomaly
```

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| OOM error | HuggingFace models too large for pod | Increase pod memory limit in K8s manifest to 4Gi |
| High processing latency | Embedding bottleneck | Run embedding on GPU node or reduce model size |
| False positive anomalies | Breaking news causes legitimate spike | Add news event filter — check if phrase is in NewsAPI top headlines |

---

## 7. storage-worker

### What It Does
Consumes from `documents.processed` and writes each document to all four databases simultaneously: PostgreSQL (with pgvector embedding), Elasticsearch (full text), Neo4j (graph edges), and Redis (cache).

### Location
```
backend/processors/storage_worker.py
```

### Kafka
- **Consumes:** `documents.processed`
- **Consumer group:** `storage-worker`

### Dependencies
- psycopg2 (PostgreSQL)
- elasticsearch
- neo4j
- redis
- kafka-python

### Environment Variables
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
POSTGRES_URL=postgresql://user:password@localhost:5432/rhetoriq
ELASTICSEARCH_URL=http://localhost:9200
NEO4J_URI=bolt://localhost:7687
NEO4J_PASSWORD=
REDIS_URL=redis://localhost:6379
```

### Running Locally
```bash
cd backend/processors
python storage_worker.py
```

### Key Implementation Notes
- Writes to all 4 databases in parallel using `asyncio.gather()`
- If any single database write fails, logs the error and retries up to 3 times before dead-lettering
- Creates Neo4j edges between documents that share entities (persons, organizations) within a 24hr window — this builds the spread graph organically
- Uses connection pooling for all databases — never opens a new connection per message

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| `pgvector` extension missing | Fresh Postgres install | Run `CREATE EXTENSION vector;` in Postgres |
| Neo4j constraint violation | Duplicate node creation | Use `MERGE` instead of `CREATE` in all Cypher queries |
| Elasticsearch index mapping error | Schema mismatch | Delete and recreate index with correct mapping |

---

## 8. agent

### What It Does
The core of RhetoriQ. Consumes anomaly alerts from `anomalies.detected`, autonomously conducts a full investigation using LangChain tools, and publishes a complete investigation report to `investigations.complete`. See [AGENT.md](./AGENT.md) for the full investigation loop documentation.

### Location
```
backend/agent/agent.py
```

### Kafka
- **Consumes:** `anomalies.detected`
- **Produces:** `investigations.complete`
- **Consumer group:** `agent-consumer`

### Dependencies
- langchain
- langchain-openai
- psycopg2
- elasticsearch
- neo4j
- redis
- kafka-python

### Environment Variables
```env
OPENAI_API_KEY=
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
POSTGRES_URL=
ELASTICSEARCH_URL=
NEO4J_URI=
NEO4J_PASSWORD=
REDIS_URL=
MAX_INVESTIGATION_STEPS=10
INVESTIGATION_TIMEOUT_SECONDS=120
```

### Running Locally
```bash
cd backend/agent
python agent.py
```

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| `openai.RateLimitError` | Too many concurrent investigations | Limit agent consumer group to 3 workers |
| Investigation timeout | GPT-4o slow response | Increase `INVESTIGATION_TIMEOUT_SECONDS` to 180 |
| Empty spread path | Neo4j graph not populated yet | Ensure storage-worker has been running for at least 24hrs before agent |

---

## 9. api

### What It Does
FastAPI service that serves the frontend. Reads completed investigations from `investigations.complete`, exposes REST endpoints for the dashboard, and maintains a WebSocket connection for live investigation updates.

### Location
```
backend/api/main.py
```

### Kafka
- **Consumes:** `investigations.complete`
- **Consumer group:** `api-consumer`

### Dependencies
- fastapi
- uvicorn
- kafka-python
- psycopg2
- neo4j
- redis

### Environment Variables
```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
POSTGRES_URL=
NEO4J_URI=
NEO4J_PASSWORD=
REDIS_URL=
FRONTEND_URL=http://localhost:3000
```

### Running Locally
```bash
cd backend/api
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
# API docs at http://localhost:8000/docs
```

### Common Failures
| Failure | Cause | Fix |
|---|---|---|
| CORS error | Frontend URL not whitelisted | Add frontend URL to `FRONTEND_URL` env var |
| WebSocket drops | Client timeout | Implement ping/pong keepalive every 30 seconds |
| Slow graph queries | Neo4j missing indexes | Add indexes on `Source.name` and `Phrase.text` nodes |

---

## 10. frontend

### What It Does
React + TypeScript dashboard with three views: live narrative feed, investigation report viewer, and interactive spread graph visualization.

### Location
```
frontend/
```

### Dependencies
- React 18
- TypeScript
- Vite
- Neo4j graph visualization library
- WebSocket client

### Environment Variables
```env
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000/ws
```

### Running Locally
```bash
cd frontend
npm install
npm run dev
# Available at http://localhost:3000
```

See [frontend/FRONTEND.md](./frontend/FRONTEND.md) for full component documentation.

---

## Running All Services Locally

```bash
# 1. Start infrastructure (Kafka, databases)
docker-compose up -d

# 2. Create Kafka topics
./scripts/create_topics.sh

# 3. Start all scrapers
cd backend/scrapers && python run_all.py &

# 4. Start Flink processor
cd backend/processors && python flink_job.py &

# 5. Start storage worker
cd backend/processors && python storage_worker.py &

# 6. Start agent
cd backend/agent && python agent.py &

# 7. Start API
cd backend/api && uvicorn main:app --reload &

# 8. Start frontend
cd frontend && npm run dev
```

Or use the convenience script:
```bash
./scripts/start_local.sh
```

---

## Service Health Checks

Every service exposes a `/health` endpoint on port `8090`:

```python
# Standard health check — add to every service
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

def start_health_server():
    server = HTTPServer(("0.0.0.0", 8090), HealthHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
```

Kubernetes liveness and readiness probes hit this endpoint every 10 seconds.
