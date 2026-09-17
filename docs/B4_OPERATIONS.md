# B4 operations and remaining acceptance

Keep `ENABLE_FLINK_TRENDING=false` until the runtime gates in
[B4_ACCEPTANCE.md](B4_ACCEPTANCE.md) pass. Automatic investigation stays off.
The cluster uses Flink 2.3.0, Python 3.12, two parallel tasks, four slots,
60-second checkpoints, and the UI at `http://localhost:8082`.

## Recorded-provider stack

Use a separate disposable Compose project. The override supplies a fixed
provider receipt without making hosted calls. From the repository root in
PowerShell:

```powershell
$B4ComposeArgs = @('--project-name', 'rhetoriq-b4-acceptance', '--env-file', '.env.production.example', '-f', 'compose.yml', '-f', 'infra/flink/compose.acceptance.yml')
docker compose @B4ComposeArgs build flink-jobmanager enrichment-worker
docker compose @B4ComposeArgs up -d flink-jobmanager flink-taskmanager enrichment-worker document-worker signal-worker projection-worker
docker compose @B4ComposeArgs run --rm --no-deps enrichment-worker python -m events.b4_acceptance --disposable-stack
```

The smoke probe publishes 20 framed raw documents, consumes committed outputs,
checks every document arrives, and rejects changed semantic payloads. The full
paced probe publishes 100/minute for 30 minutes followed by 500/minute for five
minutes, then drains the pipeline and requires a signal within 20 minutes:

```powershell
docker compose @B4ComposeArgs run --rm --no-deps enrichment-worker python -m events.b4_acceptance --disposable-stack --mode load --drain-seconds 360
```

Record probe JSON, image/connector hashes, checkpoint IDs, consumer lag over
time, and projection counts. The probe verifies delivery; operators still
must assess backlog growth, projection integrity, and signal revisions.
While the probe runs, kill and restart each Flink service separately:

```powershell
docker compose @B4ComposeArgs kill -s SIGKILL flink-taskmanager
docker compose @B4ComposeArgs up -d flink-taskmanager
docker compose @B4ComposeArgs kill -s SIGKILL flink-jobmanager
docker compose @B4ComposeArgs up -d flink-jobmanager
```

The JobManager entrypoint restores the latest completed checkpoint metadata
from its persistent volume. Preserve the volume during recovery checks.
CI includes a real recorded-provider smoke and failure probe; its result is
pending until the workflow runs successfully.

## Hosted worker and replay

Production uses `python -m events.enrichment_worker`. Set explicit positive
`ENRICHMENT_REQUEST_BUDGET` and `ENRICHMENT_SPEND_BUDGET_MICROS` for each
60-second budget window. Spending is in micro-USD. Also set positive
`ENRICHMENT_INPUT_PRICE_MICROS_PER_MILLION`,
`ENRICHMENT_OUTPUT_PRICE_MICROS_PER_MILLION`, and
`ENRICHMENT_EMBEDDING_PRICE_MICROS_PER_MILLION` at current provider price
ceilings. Extraction ceilings must cover both configured Gemini/Groq models.
Reservations conservatively cover both extractions and the mandatory Gemini
embedding; byte counts bound input tokens. No guessed provider price is used.

Gemini embeddings use a deterministic UTF-8 prefix of at most 2048 bytes.
Truncation is recorded in the receipt and coverage limitations. Hosted vectors
remain in the receipt and `hosted_document_embeddings`, separate from MiniLM.

`python -m events.enrichment_worker --replay` reads recorded artifacts only and
fails closed when the configured model/version artifact is missing. Stop the
ordinary worker before switching modes. To backfill, use an isolated consumer
group and checkpoint state with retained raw/enriched events; do not run the
projection replay command against stream-owned inputs.

`--reenrich` explicitly creates artifacts under a new pipeline identity.
Set `ENRICHMENT_REENRICH_PIPELINE_VERSION` to a unique reviewed version and
retain the original artifacts. Never overwrite receipts to repair replay.
Run a separately bounded live canary with real credentials and budgets;
record artifact IDs, usage, vector model/dimension, and sanitized failures.

## Cutover and rollback

`/api/trending/status` and `/api/research/health` expose the named Flink job,
checkpoint age/failures, TaskManagers, evaluation freshness, Kafka lag,
enrichment backlog/DLQ, worker budget pauses, artifact failures, and late audit
counts. Cutover requires the job running, a completed fresh checkpoint,
a registered TaskManager, and a healthy heartbeat no older than 30 minutes.

After acceptance, enable `ENABLE_FLINK_TRENDING=true`. A failed readiness gate
serves the last valid legacy snapshot with a visible warning. Set the flag
back to false to roll back. `POST /api/trending/refresh` refreshes that legacy
feed; users investigate stream cards through the existing manual action.

## Connector qualification

Apache's [Flink 2.3 Kafka connector documentation](https://nightlies.apache.org/flink/flink-docs-release-2.3/docs/connectors/datastream/kafka/)
states that no connector release is published for this runtime. The image
builds the pinned Kafka connector `v4.0.1` source against `flink.version=2.3.0`
and packages its shaded SQL connector. This is a candidate build, not a claim
of supported compatibility. Build, delivery, replay, and recovery must pass
before production cutover. See `infra/flink/connector-compatibility.txt`.
