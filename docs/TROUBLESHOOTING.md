# RhetoriQ Troubleshooting

## Source Collection Is Stale

Check connector health, credentials, rate-limit responses, last successful poll time, and Kafka producer errors. A source outage must not stop other connectors from publishing.

## Kafka Consumer Lag Grows

Inspect partition skew, consumer-group membership, processor errors, and downstream storage latency. Scale the affected consumer or processor only after confirming that partitioning and retries are correct.

## No Anomalies Are Detected

Verify the baseline window, phrase-extraction output, event timestamps, and threshold configuration. Reprocess retained source events to separate data-quality issues from detector logic.

## Investigation Has Weak Evidence

Review retrieval lanes, source coverage, date range, semantic-query quality, and graph context. The system should surface an evidence gap or limitation rather than generate a confident conclusion.

## Dashboard Is Incomplete

Check the API health response, WebSocket connection state, graph-query latency, and browser console errors. The interface must display partial-stage and unavailable-dependency states clearly.
