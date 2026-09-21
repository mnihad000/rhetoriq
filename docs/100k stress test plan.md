# 100K Documents-per-Day Stress-Test Plan

## Purpose

This experiment supports a narrowly worded capacity claim: that RhetoriQ
processed at least 100,000 documents per day under a recorded workload. It is
separate from the B5 acceptance gate in [Testing](TESTING.md), which uses a
10,000-document corpus and its own ingestion, query, recovery, and integrity
criteria.

Do not describe the system as continuously operating at production scale, or as
“low latency,” unless the recorded environment and latency percentiles support
those statements.

## Corpus

Use realistic article text, timestamps, publishers, source types, URLs, and
stable source identifiers. A properly licensed public dataset may supply the
corpus when its license permits the intended use and provenance is retained.

- Keep every document traceable to its source dataset and license.
- Use enough distinct documents to avoid measuring only cache behavior.
- If a corpus must be replayed, vary document IDs and event timestamps while
  preserving source text and provenance.
- Do not treat generated or metadata-only records as equivalent to real article
  documents without reporting that limitation.

## Traffic model

Replay documents through the real connector or API boundary so the test covers
validation, persistence, the transactional outbox, Kafka, Flink, and downstream
projections. Do not insert rows directly into PostgreSQL or publish a simplified
Kafka payload that bypasses the path being claimed.

100,000 documents per day is approximately 1.16 documents per second. Test:

1. A sustained run at 2–5 documents per second for long enough to exceed
   100,000 accepted documents.
2. Separate bursts at 25–100 documents per second to demonstrate Kafka
   buffering and Flink catch-up.
3. Concurrent representative lexical, semantic, graph, and investigation reads
   if the claim includes query availability during ingestion.

Warm model and service caches before the measured interval, but exclude the
warm-up records from the result. Record the exact image digests, model revision,
machine allocation, configuration, corpus identity, and start/end timestamps.

## Measurements

Capture at minimum:

- offered, accepted, persisted, and projected document counts;
- end-to-end completion rate and rejected/failed counts by reason;
- p50, p95, and p99 raw-to-persisted and raw-to-searchable latency;
- Kafka producer errors, consumer lag, retries, and DLQ counts;
- Flink throughput, checkpoint duration/failures, restarts, and recovery time;
- projection backlog, freshness, drift, and final searchable/graph counts;
- CPU, memory, disk, network, container restarts, and OOM events;
- query latency and error rate when concurrent reads are enabled.

Verify final canonical counts and hashes rather than relying only on producer
acknowledgements. Preserve the raw measurement output and a short sanitized
summary alongside the tested commit.

## Pass and claim discipline

A passing run must process at least 100,000 accepted unique documents without
unexplained loss, duplicate canonical records, unbounded backlog growth, or
unrecovered service failure. Kafka and Flink must drain the burst backlog within
the recorded recovery window.

Report the measured throughput and percentiles exactly. The acceptable claim is
“processed 100K+ documents in a recorded daily-capacity test” with a link to the
evidence. Use “low latency” only when a stated latency objective was selected in
advance and the measured percentiles met it.
