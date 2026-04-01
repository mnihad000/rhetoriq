# RhetoriQ — Kafka

> This document covers every Kafka topic, message schema, partition strategy, consumer group configuration, and local setup. If you are working on anything that produces or consumes Kafka messages, start here.

---

## Overview

Kafka is the central nervous system of RhetoriQ. Every service communicates exclusively through Kafka — no service ever calls another service directly. This means:

- Any single service can die without cascading failures
- Every message is persisted and replayable
- New consumers can be added without touching producers
- The entire ingestion history can be replayed from scratch

---

## Local Setup

### Running Kafka Locally with Docker Compose

```yaml
# docker-compose.yml
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    ports:
      - "2181:2181"

  kafka:
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"

  kafka-ui:
    image: provectuslabs/kafka-ui:latest
    depends_on:
      - kafka
    ports:
      - "8080:8080"
    environment:
      KAFKA_CLUSTERS_0_NAME: local
      KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS: kafka:9092
```

```bash
docker-compose up -d
# Kafka UI available at http://localhost:8080
```

### Creating Topics Locally

```bash
# Create all RhetoriQ topics
docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic raw.reddit \
  --partitions 6 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic raw.news \
  --partitions 6 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic raw.speeches \
  --partitions 3 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic raw.gdelt \
  --partitions 6 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic documents.processed \
  --partitions 12 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic anomalies.detected \
  --partitions 3 \
  --replication-factor 1

docker exec -it rhetoriq-kafka-1 kafka-topics --create \
  --bootstrap-server localhost:9092 \
  --topic investigations.complete \
  --partitions 3 \
  --replication-factor 1
```

---

## Topics

### Topic Summary

| Topic | Producer | Consumer | Partitions | Retention | Description |
|---|---|---|---|---|---|
| `raw.reddit` | reddit-scraper | Flink | 6 | 7 days | Raw Reddit posts |
| `raw.news` | news-scraper, rss-scraper | Flink | 6 | 7 days | Raw news articles |
| `raw.speeches` | cspan-scraper | Flink | 3 | 30 days | Raw C-SPAN transcripts |
| `raw.gdelt` | gdelt-scraper | Flink | 6 | 7 days | Raw GDELT events |
| `documents.processed` | Flink | Storage workers | 12 | 14 days | Cleaned, embedded documents |
| `anomalies.detected` | Flink | LangChain agent | 3 | 3 days | Detected narrative spikes |
| `investigations.complete` | LangChain agent | REST API | 3 | 30 days | Finished investigation reports |

---

## Message Schemas

### raw.reddit

```json
{
  "id": "string (uuid-v4)",
  "source": "reddit",
  "source_id": "string (reddit submission id)",
  "url": "string",
  "title": "string",
  "body": "string (selftext, empty string if link post)",
  "author": "string or null",
  "published_at": "string (ISO 8601 UTC)",
  "ingested_at": "string (ISO 8601 UTC)",
  "metadata": {
    "subreddit": "string",
    "upvotes": "integer",
    "num_comments": "integer",
    "upvote_ratio": "float"
  }
}
```

### raw.news

```json
{
  "id": "string (uuid-v4)",
  "source": "newsapi | rss",
  "source_id": "string (url md5 hash)",
  "url": "string",
  "title": "string",
  "body": "string (may be truncated on free tier)",
  "author": "string or null",
  "published_at": "string (ISO 8601 UTC)",
  "ingested_at": "string (ISO 8601 UTC)",
  "metadata": {
    "outlet": "string (e.g. Fox News, Reuters)",
    "outlet_id": "string or null",
    "description": "string or null",
    "tags": ["array of strings"]
  }
}
```

### raw.speeches

```json
{
  "id": "string (uuid-v4)",
  "source": "cspan",
  "source_id": "string (cspan program id)",
  "url": "string",
  "title": "string",
  "body": "string (full transcript)",
  "author": "string (primary speaker)",
  "published_at": "string (ISO 8601 UTC)",
  "ingested_at": "string (ISO 8601 UTC)",
  "metadata": {
    "program_type": "speech | hearing | pressconference",
    "speakers": ["array of speaker names"],
    "committee": "string or null",
    "duration_seconds": "integer"
  }
}
```

### raw.gdelt

```json
{
  "id": "string (uuid-v4)",
  "source": "gdelt",
  "source_id": "string (GKGRECORDID)",
  "url": "string (DOCUMENTIDENTIFIER)",
  "title": "null",
  "body": "string or null (fetched from URL)",
  "author": "null",
  "published_at": "string (ISO 8601 UTC, parsed from YYYYMMDDHHMMSS)",
  "ingested_at": "string (ISO 8601 UTC)",
  "metadata": {
    "themes": ["array of GDELT theme strings"],
    "persons": ["array of person names"],
    "organizations": ["array of org names"],
    "locations": ["array of location names"],
    "tone": "float (overall tone score -100 to +100)"
  }
}
```

### documents.processed

```json
{
  "id": "string (same uuid from raw message)",
  "source": "string (reddit | newsapi | rss | gdelt | cspan)",
  "source_id": "string",
  "url": "string",
  "title": "string or null",
  "body_clean": "string (cleaned, stripped body)",
  "author": "string or null",
  "published_at": "string (ISO 8601 UTC)",
  "ingested_at": "string (ISO 8601 UTC)",
  "processed_at": "string (ISO 8601 UTC)",
  "embedding": "[array of 384 floats (sentence-transformers/all-MiniLM-L6-v2)]",
  "entities": {
    "persons": ["array of extracted person names"],
    "organizations": ["array of extracted org names"],
    "locations": ["array of extracted locations"],
    "key_phrases": ["array of noun chunks > 3 words"]
  },
  "metadata": "object (passthrough from raw message metadata)"
}
```

### anomalies.detected

```json
{
  "anomaly_id": "string (uuid-v4)",
  "detected_at": "string (ISO 8601 UTC)",
  "phrase": "string (the anomalous phrase or topic)",
  "spike_magnitude": "float (e.g. 4.2 = 420% of baseline)",
  "window_start": "string (ISO 8601 UTC)",
  "window_end": "string (ISO 8601 UTC)",
  "baseline_frequency": "integer (avg occurrences per 10min window over 7 days)",
  "window_frequency": "integer (occurrences in current window)",
  "top_sources": [
    {
      "source": "string",
      "outlet": "string or null",
      "subreddit": "string or null",
      "count": "integer"
    }
  ],
  "sample_document_ids": ["array of up to 5 document ids from this window"]
}
```

### investigations.complete

```json
{
  "investigation_id": "string (uuid-v4)",
  "anomaly_id": "string (reference to triggering anomaly)",
  "phrase": "string",
  "started_at": "string (ISO 8601 UTC)",
  "completed_at": "string (ISO 8601 UTC)",
  "duration_seconds": "integer",
  "origin": {
    "document_id": "string",
    "source": "string",
    "outlet_or_subreddit": "string",
    "published_at": "string (ISO 8601 UTC)",
    "url": "string",
    "confidence": "float (0-1)"
  },
  "spread_path": [
    {
      "stage": "integer (1 = origin, 2 = first amplification, etc.)",
      "source": "string",
      "outlet_or_subreddit": "string",
      "published_at": "string (ISO 8601 UTC)",
      "document_count": "integer"
    }
  ],
  "key_amplifiers": [
    {
      "source_id": "string",
      "name": "string",
      "type": "subreddit | outlet | politician",
      "amplification_count": "integer"
    }
  ],
  "report": "string (full GPT-4o synthesized narrative report in markdown)",
  "similar_document_count": "integer (total documents semantically related to this narrative)",
  "graph_node_count": "integer (nodes in Neo4j spread graph)"
}
```

---

## Partition Strategy

### Why These Partition Counts?

**`raw.*` topics — 6 partitions**
Six scrapers can run in parallel, one per partition, giving full parallelism across data sources. Flink consumes with a consumer group of 6 workers.

**`documents.processed` — 12 partitions**
Highest volume topic in the system. 12 partitions allows 12 parallel storage workers writing to PostgreSQL, Elasticsearch, and Neo4j simultaneously without bottlenecking.

**`anomalies.detected` — 3 partitions**
Low volume but high priority. 3 partitions is sufficient. The agent consumer group has 3 workers so each partition has a dedicated agent instance.

**`investigations.complete` — 3 partitions**
Low volume. The REST API consumes this with a single consumer — 3 partitions gives redundancy without over-engineering.

### Partition Key Strategy

| Topic | Partition Key | Reason |
|---|---|---|
| `raw.reddit` | `subreddit` | Groups posts by subreddit for ordered processing |
| `raw.news` | `outlet` | Groups articles by outlet |
| `raw.speeches` | `null (round-robin)` | Low volume, ordering not critical |
| `raw.gdelt` | `null (round-robin)` | High volume, even distribution preferred |
| `documents.processed` | `source` | Consistent routing to storage workers |
| `anomalies.detected` | `phrase` | Same phrase always hits same agent partition |
| `investigations.complete` | `investigation_id` | Even distribution to API consumers |

---

## Consumer Groups

| Consumer Group | Topic(s) | Members | Description |
|---|---|---|---|
| `flink-raw-consumer` | `raw.*` | 6 | Flink ingests all raw topics |
| `storage-worker` | `documents.processed` | 12 | Writes to all 4 databases |
| `agent-consumer` | `anomalies.detected` | 3 | Triggers investigation per anomaly |
| `api-consumer` | `investigations.complete` | 1 | REST API reads completed reports |

---

## Producer Configuration

All scrapers use these producer settings:

```python
from kafka import KafkaProducer
import json

producer = KafkaProducer(
    bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    key_serializer=lambda k: k.encode("utf-8") if k else None,
    acks="all",                    # Wait for all replicas to acknowledge
    retries=5,                     # Retry up to 5 times on failure
    retry_backoff_ms=500,          # Wait 500ms between retries
    compression_type="gzip",       # Compress messages
    batch_size=16384,              # Batch up to 16KB before sending
    linger_ms=10                   # Wait up to 10ms to fill a batch
)
```

---

## Consumer Configuration

All consumers use these base settings:

```python
from kafka import KafkaConsumer
import json

consumer = KafkaConsumer(
    "topic.name",
    bootstrap_servers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    group_id="your-consumer-group",
    value_deserializer=lambda v: json.loads(v.decode("utf-8")),
    key_deserializer=lambda k: k.decode("utf-8") if k else None,
    auto_offset_reset="earliest",  # Start from beginning if no committed offset
    enable_auto_commit=False,      # Manual commit for exactly-once processing
    max_poll_records=100,          # Process up to 100 messages per poll
    session_timeout_ms=30000,      # 30 second session timeout
    heartbeat_interval_ms=10000    # Heartbeat every 10 seconds
)

# Always commit manually after successful processing
for message in consumer:
    try:
        process(message.value)
        consumer.commit()
    except Exception as e:
        logger.error(f"Failed to process message: {e}")
        # Do not commit — message will be reprocessed
```

---

## Monitoring Kafka Health

### Key Metrics to Watch in Grafana

| Metric | Warning Threshold | Critical Threshold | Description |
|---|---|---|---|
| Consumer lag | > 1000 messages | > 10000 messages | How far behind a consumer is |
| Messages per second | < 10 msg/s | < 1 msg/s | Ingestion rate dropping |
| Producer error rate | > 0.1% | > 1% | Failed message deliveries |
| Broker disk usage | > 70% | > 90% | Retention filling disk |

### Checking Consumer Lag Manually

```bash
# Check lag for all consumer groups
docker exec -it rhetoriq-kafka-1 kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe \
  --all-groups
```

---

## Retention Policy

| Topic | Retention | Reason |
|---|---|---|
| `raw.*` | 7 days | Raw data is large — 7 days is enough to replay recent ingestion |
| `raw.speeches` | 30 days | Speeches are low volume and high value — keep longer |
| `documents.processed` | 14 days | Processed docs are in databases — Kafka copy kept for replay |
| `anomalies.detected` | 3 days | Anomalies are acted on immediately — short retention fine |
| `investigations.complete` | 30 days | Reports are valuable — keep for a month |
