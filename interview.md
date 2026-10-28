# RhetoriQ — New Grad Technical Interview

Answer the questions in order, one at a time, in your own words. Answer any inserted follow-up before moving to the next numbered question. Use concrete examples from the repository where you can; memorized terminology is not required. The interview is complete only when every question has an answer and the remaining gaps have been probed.

## Question 1 — Explain the project

Imagine I just joined the team and have never seen RhetoriQ. What problem does it solve, what does a user get from it, and how does a user's investigation request move through the system from the UI to the final report? Give me the high-level picture first; we can drill into individual components afterward.

### Answer 1

RhetoriQ helps someone investigate how a public narrative appears, changes, and spreads. A user can start with a question such as, "How did this claim develop, and which sources repeated it?" They get an investigation workspace with a source-backed timeline, related phrases and claims, a spread graph, counter-frames, and a final report. The main goal is to make the reasoning inspectable: you can open the evidence behind a finding and see what the system could and could not establish. For example, we say "first observed in our available data" rather than claiming we found the true origin of a story.

At a high level, the React UI sends the question to our FastAPI backend. The API saves the investigation and an outbox event in PostgreSQL in one transaction, then returns an investigation ID. An outbox publisher sends the request to Kafka, where an investigation worker picks it up. That worker runs a bounded LangGraph research process: it plans what evidence is needed, searches approved sources, retrieves canonical pages or first-party records, and records both usable evidence and acquisition failures. It can also search material already in our corpus. The document pipeline uses Kafka and Flink to normalize and deduplicate incoming material and detect narrative signals; PostgreSQL remains the source of truth, while search, graph, vector, and cache stores provide faster ways to explore that data.

The investigation then builds the timeline, source-diversity and narrative relationships, checks competing explanations, and drafts findings. Before publication, deterministic checks require material claims to have eligible source records and exact supporting evidence spans. If the evidence is too thin, the result says so instead of presenting a confident report. The worker persists its artifacts, receipts, and final outcome, and the UI shows progress through server-sent events before loading the completed report and its supporting workspace.

## Question 2 — Request handoff and reliability

You mentioned that the API saves an investigation and an outbox event in the same PostgreSQL transaction before Kafka receives the request. Why is that transaction important? Walk me through what should happen if the API crashes just after the database commit, and then what should happen if the outbox publisher sends the Kafka message but crashes before marking it published. How does the system avoid losing the request or running the investigation twice?

### Answer 2

The transaction closes the gap between accepting work and making that work dispatchable. To be precise, the plan is saved first; when the user starts the investigation, we commit the queued research run and its outbox row together. If those were separate writes, a crash could leave a run that never gets sent to Kafka, or a Kafka request with no durable run to execute.

If the API crashes immediately after that commit, the request is still safe in PostgreSQL. The client can load the investigation by ID, and the outbox publisher will find the pending row when it starts or resumes. If Kafka is unavailable, the row stays pending and is retried with backoff; the API does not have to keep the original HTTP request open.

The other crash window is after Kafka acknowledges the send but before the publisher marks the outbox row as published. On restart, the publisher may send that same event again. We deliberately treat Kafka delivery as at least once. The event keeps the same `event_id` and `run_id`; the consumer records completed event IDs, commits its Kafka offset only after durable handling, and skips an event it has already completed. The investigation worker also claims the run through a lease, so two workers cannot normally execute the same active run. If a worker dies mid-run, another can take over after the lease expires, using persisted checkpoints and idempotency keys for research actions. Stable IDs protect final artifacts and completion events as well. So a retry may repeat a message or part of the work, but it should not create a second logical investigation or duplicate its published result.

## Question 3 — A lease-expiry race

You said a lease lets another worker take over after a crash. Suppose worker A is only paused for a long time: its lease expires, worker B claims the run, and then A resumes. What should prevent A from writing stale progress or publishing a second final report? Walk through the durable checks and idempotency boundaries you would expect, and tell me where a Kafka offset fits into this sequence.

### Answer 3

A lease by itself is not enough here. A can wake up holding an old view of the world, so we use the database to fence off stale workers. Each successful claim advances a lease generation for that run. A carries its generation on progress, checkpoint, action, and final-report writes. Those writes succeed only while the run is still `running`, A is the current owner, and its generation matches the value in PostgreSQL. Once B claims the expired lease, A's next write fails that check and A stops. We use database time for expiry so the workers' clocks do not decide ownership.

The final transition needs the same protection in one transaction. B conditionally changes the run from `running` to a terminal state, rechecks that the cited evidence is still eligible, saves the report or `insufficient_evidence` outcome, and records the completion event in the outbox. A stale transition cannot overwrite B's result. A stable completion ID derived from the `run_id`, plus unique keys on the report and event, protects against retrying that transaction. Checkpoints and action idempotency keys let B resume completed steps; an external provider call can still be repeated across a crash, so the promise is one durable logical result, not literally one network call.

The Kafka offset is separate from lease ownership. The consumer commits it only after the handler's durable work is complete. If A is fenced off, it cannot mark the run complete just to acknowledge the message; the event must be retried or handled by B. If an offset commit is lost after successful processing, Kafka may redeliver the event, and the event ledger, run state, and stable IDs make that redelivery harmless.

### Follow-up 3A — Check the implementation

I looked at `backend/services/research_repository.py`: `claim_run` stores `worker_id` and `lease_until`, while `update_run` and `finish_run` update by `run_id` without a lease-generation condition. The `research_runs` schema also has no lease-generation column. Which parts of your answer describe the current code, and which describe a fix you would design? State the failure window that remains today and the smallest change that would fence a stale worker.

#### Answer 3A

You're right. I described the design I would use, but I spoke as though it was already enforced. In the current code, `claim_run` atomically records a `worker_id` and `lease_until`, and `renew_lease` checks the worker ID. The repository also uses action idempotency keys and a stable completion-event ID. Those help with retries, but there is no lease-generation token, and `update_run`, `finish_run`, and the final report path do not require the current lease owner. My statement that every write was fenced was inaccurate.

The remaining race is that A can pause after claiming the run, its lease can expire, and B can claim it. When A resumes, it can still append progress or reach the final-report transaction because those writes are keyed by `run_id`, not by current ownership. The stable completion ID may suppress a second completion event, but it does not decide which worker's result is correct; a stale A could publish first and block B's newer result.

I would add a monotonically increasing `lease_generation` to `research_runs` and increment it in the same atomic update that grants a claim. The claim returns that generation to the worker. Lease renewal and every durable progress, checkpoint, artifact, and terminal write must check `run_id`, `worker_id`, generation, `status = 'running'`, and an unexpired lease using database time. The final transaction should conditionally claim the terminal transition first, then save the report and completion outbox event in that same transaction; a failed condition rolls back all of it. A separate check before doing work would not close the race, because ownership could change between the check and the write. Kafka offset commits remain an acknowledgment after durable handling, not a substitute for this fence.

## Question 4 — API and event boundaries

After a user presses Start, what should FastAPI validate and persist before it responds? What work should happen asynchronously? Trace the versioned Kafka event, its consumer, its durable side effect, and the point at which the consumer commits its offset. Where do retries, dead-letter topics, and controlled replay enter this flow?

### Answer 4

When the user presses Start, FastAPI handles admission, not the research itself. The `/api/investigations/{id}/run` route checks that the investigation, its saved plan, and its workspace exist, and validates the request body, including whether this is a forced refresh. The run manager returns an existing active run instead of starting another one. At this boundary I also want the caller's authorization and allowed source classes checked before accepting work. It commits a queued run and a matching outbox row in one PostgreSQL transaction, then returns HTTP 202 with the run in the workspace. A slow search provider should never hold that HTTP request open.

The outbox publisher sends a schema-validated `investigations.requested.v1` event to Kafka, keyed by investigation ID. Its payload identifies the investigation and run, query or signal, requested outputs, time window, source classes, and authorization context; the envelope carries an event ID and correlation information. The investigation consumer validates the versioned event, checks its consumer ledger, claims the durable run, and executes the bounded research workflow. Its side effects are saved in PostgreSQL: actions, receipts, checkpoints, ordered stage events, and finally either a cited report or a visible insufficient-evidence outcome. Stage and completion notifications are written through the outbox from durable state. The consumer marks the event handled and commits the Kafka offset only after its handler has completed durable work. If the offset commit is lost, redelivery is expected and the IDs and run state make it idempotent.

The publisher retries pending outbox rows when Kafka is unavailable. A consumer retries transient failures with bounded backoff; a permanent schema or policy error, or exhausted retries, goes to the matching dead-letter topic with a redacted failure receipt. We inspect and fix the cause before controlled replay. Replay uses an audited job with a chosen offset range or correlation filter, keeps the normal consumer group's offsets untouched, and passes through the same idempotent handlers so it does not create a second investigation or notification.

## Question 5 — Sources versus transports

The project uses GDELT, Hacker News, SearXNG, Federal Register, direct page retrieval, and an optional browser adapter. For a sample investigation, explain which paths discover leads and which can establish citable evidence. Why does it matter that an evidence source is different from the API or tool used to reach it?

### Answer 5

Suppose a user asks how a claim about a proposed federal rule spread. I might use SearXNG to find official pages, news coverage, and counterarguments; GDELT to find dated news links and see which publishers covered the topic; and the Hacker News Algolia API to discover relevant community discussions or linked stories. Those results are leads. A title, search snippet, or aggregator timestamp can help us decide what to retrieve next, but it does not give us the article's full wording or prove the claim in it.

For the rule itself, I would query the first-party Federal Register API. Its identified record, dated metadata, and abstract can be citable primary-source evidence for what that record says, with a link to the official document when legal wording matters. For a news article or discussion, I would fetch its canonical page and preserve the actual text, URL, timestamps, content identity, and acquisition receipt before citing a passage. If the public page needs JavaScript to render, the isolated browser adapter can retrieve it under the same policy and receipt checks. If the page cannot be retrieved, we keep the discovery lead and show that limitation; we do not promote its snippet into evidence.

This distinction matters because GDELT, SearXNG, Algolia, HTTP, and the browser are ways of reaching material. The evidence source is the Federal Register record, publisher's article, or original forum post. A single article found through three transports is still one source, not three independent confirmations. Keeping source identity separate from transport lets us deduplicate, judge source diversity honestly, apply the right access and retention rules, and make every report citation point to material a user can inspect.

## Question 6 — Retrieval policy and receipts

A search result points to a page that redirects, blocks access, or exposes only a snippet. How should the investigator decide whether to fetch it, what should be recorded about the attempt, and when must it refuse to cite the result? Include URL policy, source policy, canonical identity, and visible limitations.

### Answer 6

I treat the search hit as a lead and decide whether retrieving it would close a specific evidence gap. Before making a request, I check that the source class and retrieval method are approved and that the URL is public HTTP(S), has no embedded credentials, resolves only to public IPs, and is allowed by the site's access policy. Fetches have time, redirect, response-size, and content-type limits. Every redirect target needs a fresh public-URL check; if it changes host, I also need to reconsider that host's source and robots policy. A block or redirect to a private address ends the attempt rather than becoming evidence.

I keep a receipt even when retrieval fails. It ties the discovery query, provider, rank, and source-native ID to the attempted URL; records the final canonical URL if one was reached; and records publication and collection times separately, the policy decision, HTTP or fetch outcome, parser version for a successful extraction, and any limitation. The final URL and source-native ID help us recognize the same article discovered through several searches or redirects. They also let us distinguish a publisher's page from an aggregator's listing. A redirect is not just a technical detail if it changes which source we actually read.

I would cite only an eligible, retrievable source record with actual text or an approved first-party record and a passage that supports the specific claim. A 403, robots refusal, timeout, unsupported page, or snippet-only result stays `discovery_only` with a visible explanation. Even a 200 response is insufficient if extraction produced no usable page text and merely fell back to the search snippet or title. We can continue searching for another source or report a coverage gap, but we cannot quietly turn inaccessible material into a citation.

## Question 7 — Research planning

How does the LangGraph investigator turn a broad question into evidence gaps and retrieval steps? Explain the planner, approved tool selection, budgets, and skeptic pass. What stops the agent from searching indefinitely or treating instructions embedded in a retrieved page as commands?

### Answer 7

The planner turns the question into a structured investigation plan: topic and time window, requested outputs, source classes, and retrieval lanes such as discovery, corroboration, contradiction, community, and official records. The LangGraph supervisor compares that plan with the evidence already collected and a gap list. It might search broadly to find candidates, fetch a canonical page to establish what it actually says, query Federal Register for an official record, or search the internal corpus for already receipted material. Each proposed action has to be one of the approved tool types and passes policy and budget validation before the adapter runs.

The budget limits wall time, tool calls, search results, page fetches, browser renders, per-domain requests, retries, model tokens, and spend. When a limit is reached, the graph assesses its evidence and ends with a report or an explicit insufficient-evidence outcome; it does not search until it finds a desired answer. The skeptic pass checks unsupported claims, contradictions, weak chronology, and missing source classes, and can request a bounded correction. Retrieved page text is untrusted evidence data. It cannot add a tool to the allowlist, change the budget, or instruct the worker to ignore the system's source policy.

## Question 8 — Durable research state

Pick a point midway through an investigation where the worker crashes. What state must survive for another worker to resume or replay the run? Explain checkpoints, action idempotency, receipts, stage events, and how a resumed run avoids repeating an external action unnecessarily.

### Answer 8

Suppose the worker crashes after fetching two canonical pages but before building the report. PostgreSQL retains the run ID, plan, budget use, selected actions, normalized documents, retrieval receipts, gaps, ordered stage events, and terminal status. LangGraph also has a durable checkpoint of its node state. The replacement worker can claim the run after the lease expires, reload that state, and continue from the next unfinished step rather than restarting from the user's original question.

Each research action has an idempotency key derived from its run and decision. Before repeating an action, the worker checks whether a completed or partial action with that key already has documents and receipts. If so, it reuses those records. Recorded replay is even stricter: it uses saved actions and evidence with network access disabled, so a replay can compare decisions without quietly acquiring different sources. A checkpoint alone is not enough because a crash can happen between an external request and the checkpoint; durable action receipts narrow that gap. The stale-lease race discussed in Answer 3A still requires generation fencing on every write.

## Question 9 — Documents and the internal corpus

What turns a retrieved item into a usable document or evidence record? Discuss source identity, publication and collection times, text or exact spans, provenance, revisions, withdrawals, and the difference between internal-corpus recall and discovering a new external page.

### Answer 9

A search hit becomes a usable document only after we can identify and inspect its source. We keep the source-native ID where available, canonical URL, publisher or agency, discovery provider, retrieval transport, publication time, collection time, extracted text, and acquisition receipt. Those dates serve different purposes: publication time places a statement on the timeline, while collection time says when our system observed it. A report claim needs an eligible record and an exact supporting span; a title or snippet alone remains a lead.

PostgreSQL records the document's current revision, content identity, eligibility, and any withdrawal. A correction creates a new revision; a withdrawal makes an older citation ineligible even if Elasticsearch or Neo4j has not caught up. Internal-corpus recall searches records we already acquired and can reuse a valid receipt after current eligibility checks. External discovery finds a new URL or provider record that still needs policy checks and usually canonical retrieval. Keeping those paths distinct prevents a previously seen snippet from masquerading as newly verified evidence.

## Question 10 — Worker roles

The stack has document, signal, investigation, product-projection, and enrichment workers. What event does each consume, and what does it write? Choose one document event and show where its canonical PostgreSQL record is written and how its derived projections are requested.

### Answer 10

The enrichment worker consumes `documents.enrichment-requested.v1`, obtains or reuses an immutable extraction and embedding artifact, and emits `documents.enriched.v1`. Flink consumes that enriched event and emits `documents.processed.v1` plus signal and audit events. The document worker consumes `documents.processed.v1` and writes the authoritative document snapshot in PostgreSQL. In that same canonical path it requests a `corpus.projections.v1` mutation; separate Elasticsearch, Neo4j, and MiniLM workers update their derived views. The signal worker consumes `signals.detected.v1`, persists signal state, and schedules an investigation only when the signal and authorization rules allow it.

The investigation worker consumes `investigations.requested.v1`, runs the research workflow, and persists artifacts, receipts, stage events, and its final outcome. The product-projection worker consumes investigation stage/completion and pipeline evaluation or late-document events to record delivery and freshness for the API. For example, one processed news document is not made authoritative by the Elasticsearch indexer: the document worker first commits its canonical PostgreSQL revision and projection outbox event. An indexer can then replay that mutation without inventing a second source of truth.

## Question 11 — Flink processing

Trace a raw document through normalization, enrichment request, deduplication, event-time windows, and the processed-document and signal outputs. Why do checkpoints and savepoints matter? What should happen when a duplicate arrives or a valid document arrives after the allowed lateness window?

### Answer 11

A connector or investigator emits `raw.documents.v1` with the source identity and acquisition metadata. Flink validates and normalizes it, then emits `documents.enrichment-requested.v1`. The separate enrichment worker records a versioned artifact and returns `documents.enriched.v1`. Flink verifies that input and artifact, deduplicates stable document identities, and uses publication/event time in bounded windows to count phrases and compare current activity with a baseline. It emits `documents.processed.v1` for canonical persistence and `signals.detected.v1` when a candidate spike meets its evidence rules; it also emits evaluation and late-document audit events.

Checkpointed Kafka offsets and window state let Flink restart without forgetting what it counted. Savepoints support a controlled upgrade or restore with known state. A duplicate can be delivered again but should not create a second canonical document or inflate the signal count. A valid event beyond allowed lateness remains recorded and visible through `documents.late.v1`; it does not silently rewrite a closed signal window. That distinction preserves a reproducible answer to what the system knew at the time.

## Question 12 — Enrichment and deterministic replay

Why does hosted extraction or embedding run in a worker outside Flink's checkpointed job? Explain provider and spending budgets, recorded immutable artifacts, and what replay should do if the expected artifact is missing.

### Answer 12

Hosted inference has variable latency, cost, and failure modes, so I keep it outside Flink's checkpointed stream operators. Flink requests enrichment and waits for a versioned result, while a dedicated worker applies provider rate and request limits, retries, deadline and spending ceilings. It stores an immutable artifact keyed by the input hash, pipeline version, provider and model, prompt version, schema version, and embedding identity. The artifact includes output integrity information, so a later run can verify it is using the same extraction rather than accepting a changed provider response.

Normal retries reuse a valid recorded artifact instead of paying for the same work. Deterministic replay never calls the hosted model: it reads the expected artifact and validates its input and output hashes and model identity. If that artifact is missing or corrupt, replay fails visibly with a missing-artifact or integrity error. Quietly making a new model call would change the semantic output under the same historical event and make the checkpoint replay misleading.

## Question 13 — Signals and trending

How does the system distinguish a meaningful narrative signal from ordinary background activity? Explain time windows, baseline comparison, supporting documents, source diversity, confidence, and coverage limits. What is the current status of Flink-primary trending, and what happens while its acceptance gate is still open?

### Answer 13

We compare phrase activity in an observed event-time window with a prior baseline, rather than treating any mention as a trend. A signal records its window, baseline count, observed count, spike and confidence components, supporting document IDs, publisher and source-type diversity, and provider coverage. Repeated copies from one publisher should not look like independent spread. We also account for weak publication dates and unavailable sources. A high spike with poor coverage is a candidate for investigation, not proof that a narrative began at that moment.

The Flink signal path and PostgreSQL projection are implemented, and a recorded-provider smoke delivered 20 unique documents through real Kafka/Flink with completed checkpoints. Flink-primary trending is still behind `ENABLE_FLINK_TRENDING=false` because duplicate, late and out-of-order, failure-recovery, paced-load, and bounded live-canary acceptance remain open. Until that gate passes, the product uses its existing trending path and shows freshness or coverage limitations rather than presenting an unqualified Flink signal as authoritative.

## Question 14 — Investigation artifacts

How are the timeline, narrative family, mutation relationships, spread graph, counter-frames, source-diversity summary, and unresolved gaps built from evidence? Pick two artifacts and explain both what they show and what they cannot establish.

### Answer 14

The workflow builds separate artifacts from the normalized documents and their receipts: a dated timeline, narrative-family and phrase-mutation relationships, a spread graph, counter-frames, a source-diversity summary, and unresolved evidence gaps. It keeps document IDs behind each item so the analyst and final report can point back to what was actually observed. The skeptic reviews those artifacts before publication, especially when an inference is stronger than its source packet.

For example, the timeline orders claims by reliable publication timestamps and can show the first instance *we observed*. It cannot establish the true first use if earlier material was outside our sources, inaccessible, or undated. The spread graph shows explicit links, shared phrases, references, and inferred similarities with their evidence class. It helps a user explore a possible transmission path, but nearby nodes do not prove one account copied another or that the accounts coordinated. Counter-frames and source-diversity views make competing interpretations and coverage gaps visible rather than hiding them behind a single graph line.

## Question 15 — Claims and publication

A draft report says, “This account originated the claim and coordinated its spread.” How should the claim ledger, evidence verifier, skeptic pass, and publication gate evaluate that sentence? Explain exact supporting spans, source eligibility, observation versus inference, and the `insufficient_evidence` result.

### Answer 15

I would split that sentence into testable claims: this account posted the claim, it was the earliest observed instance in our dataset, and the accounts coordinated. The first may be supported by an eligible captured post and exact text span. The second needs trustworthy dates across the searched sources and must still be phrased as “first observed in the available dataset.” The coordination claim needs direct evidence of coordination; close posting times, similar wording, or a graph edge alone are not enough.

The claim ledger records each proposed claim, its type, citations, counterevidence, and open gaps. The verifier checks that cited spans occur in eligible source records, evaluates whether they actually support or contradict the wording, and avoids counting syndicated copies as independent corroboration. The skeptic softens or rejects overstatements and flags missing source classes. The deterministic gate only publishes material claims that survive those checks and the source-diversity thresholds. I would remove the unsupported origin and coordination language, publish the narrower observed facts if they meet the gate, or return `insufficient_evidence` with the unresolved gaps if they do not.

## Question 16 — Frontend and live progress

How does the React app move from dashboard or question entry to a persisted investigation workspace? Explain server-sent event updates, reconnect or reload behavior, and how the user can inspect the evidence behind a report finding, timeline item, or graph relationship.

### Answer 16

The user starts from the dashboard or question form. The React app creates a saved plan through FastAPI, receives an investigation ID, then starts its research run and routes to `/investigation/:id`. That page loads the persisted workspace and research trail. It subscribes to server-sent events for stage progress, receipts, warnings, and the terminal outcome; the event stream is a view over durable PostgreSQL events, not the only copy of progress.

If the tab reloads or the connection drops, the app fetches the workspace again and reconnects using the run and sequence cursor. The stream replays missed events and the client deduplicates repeated delivery. A finished report links a finding to its claim, citations, evidence spans, and retrieval receipt. Timeline items and graph relationships expose their source document IDs and relationship type, so the user can open the underlying page or provenance path and see whether an edge was explicit or inferred. If a provider or projection is unavailable, the workspace keeps the limitation visible.

## Question 17 — Storage authority

Which data belongs in PostgreSQL, and which data is a derived view in Elasticsearch, Neo4j, pgvector, or Redis? If stores disagree about whether a document may be cited, which one decides and how should the API handle the disagreement?

### Answer 17

PostgreSQL owns canonical documents, content revisions, source receipts, withdrawals and citation eligibility, research runs and reports, the outbox, and the active projection-generation manifest. Elasticsearch indexes words and literal phrases; Neo4j projects typed relationships; MiniLM vectors live in a separate pgvector projection; Redis caches complete validated responses. Those systems accelerate retrieval and exploration, but they can be rebuilt from canonical state.

If Elasticsearch still returns a withdrawn article, the API rehydrates the hit from PostgreSQL and checks its current scope, revision, content hash, eligibility, and withdrawal flag. The hit is suppressed even if the index is stale. The same rule applies to a Neo4j path or semantic candidate: a graph relationship cannot make an ineligible document citable. We report the projection as pending, stale, or degraded and repair it, while PostgreSQL makes the immediate citation decision.

## Question 18 — Search and semantic retrieval

How do lexical search and MiniLM/pgvector semantic search help users find evidence in different ways? Walk through a search hit from a derived index to PostgreSQL revalidation. Why can neither a keyword match nor vector similarity alone support a report claim?

### Answer 18

Lexical search in Elasticsearch is useful when a user knows a phrase or named entity and wants exact or near-exact matches; literal-phrase mode can verify the text and highlight its span. MiniLM embeddings in pgvector can find semantically similar wording, such as a paraphrase that shares few keywords. The two result sets are candidate generators, not independent evidence. The MiniLM vector space is pinned to its own model and revision and is kept separate from any hosted Gemini embedding.

For either path, the API receives candidate IDs and projection metadata, then loads the canonical snapshots from PostgreSQL. It checks that each hit belongs to the investigation and active generation and still has the expected revision, hash, model identity where relevant, and citation eligibility. Withdrawn or stale hits are removed and the response states if the projection is incomplete. A keyword match may quote a claim to dispute it, and a nearby vector may discuss only a related topic. The report still needs an exact eligible span and claim-level support assessment.

## Question 19 — Graph and provenance

What relationships does Neo4j project, and how does the API return an explained provenance path? Give an example where two records are close in the graph but that proximity does not prove copying, origin, or coordination.

### Answer 19

Neo4j projects document and investigation relationships from canonical snapshots: source references and links, narrative or phrase connections, claim-to-evidence context, and explicit versus inferred edges with revision and provenance metadata. The API asks for a bounded path between selected records, validates the returned nodes against the current PostgreSQL investigation and document eligibility, and returns the path as steps with relationship types and evidence classes. If the graph is stale or unavailable, it labels a bounded canonical fallback or shows an incomplete result.

For example, two news articles may use the same unusual phrase and both link to one agency announcement, so the graph places them close together. That supports the observation of shared wording and a shared cited record. It does not establish which article copied the other, whether either was the true origin, or whether the publishers coordinated. Those stronger claims would require separate evidence and a claim-level verification pass.

## Question 20 — Redis and projection recovery

Where does Redis improve response time, and what happens if it is down? Now suppose PostgreSQL has valid documents but Elasticsearch and Neo4j lag after an outage. How would you detect drift, repair or rebuild projections, cut over a new generation safely, and roll back if it is wrong?

### Answer 20

Redis helps with repeated search, graph, and provenance responses by caching only complete responses that already passed PostgreSQL validation. Keys include scope, active generation, revision, and a semantic hash; a hit is checked against current canonical and delivery state before use. If Redis is down, the API falls through to the underlying query path. It may be slower, but Redis does not hold the only copy of evidence or decide whether a document is citable.

After a projection outage, I would compare each store's acknowledged mutation position, counts, revision and hash samples, and eligibility against PostgreSQL's projection ledger. A check-only drift report tells me whether records are missing, stale, extra, or invalid. For a small gap, I would replay or repair the affected mutations idempotently. For wider drift, I would build isolated Elasticsearch and Neo4j generations from canonical snapshots, let them catch up to one shared PostgreSQL high-water mark, and validate both before an atomic manifest cutover. I would retain the old generation during a rollback window; if the new one fails, revalidate and switch back. Throughout, API reads recheck canonical eligibility and label any fallback or stale coverage.

## Question 21 — Deployment topology

Compare the local Compose stack with the Helm chart on `kind`. Which services, initialization jobs, persistent volumes, readiness checks, and network rules are needed for an end-to-end investigation? Why is a successful manifest render weaker evidence than a running-stack smoke test?

### Answer 21

Compose and the Helm `kind` profile run the same logical path: PostgreSQL/pgvector, Kafka KRaft and Apicurio, SearXNG, Flink JobManager and TaskManager, enrichment, outbox and event workers, FastAPI, and React/Nginx. The B5 layer adds Elasticsearch, Neo4j, Redis, and MiniLM projection workers. In Kubernetes, the chart also declares an exclusive migration Job, topic/schema initialization, B5 initialization, and bounded smoke and evidence Jobs. Persistent claims protect PostgreSQL, Kafka logs, Elasticsearch, Neo4j, Flink checkpoints/savepoints, and the captured evidence; Redis is disposable.

Readiness must mean more than a live process: migrations and schemas must match, workers must heartbeat, Kafka lag and DLQs must be visible, and Flink and stores must actually process a seeded investigation. Default-deny NetworkPolicies allow only the required service paths, while Secrets and internal TLS keep store credentials and ports private. Rendering 61 `kind` resources and validating their shape proves that the chart is syntactically coherent. It does not prove images start, certificates work, a document reaches every store, or state survives a restart. Those require a running-stack smoke and recovery drill, which are not yet recorded for `kind`.

## Question 22 — AWS release boundary

What do the three Terraform states, immutable ECR images, restricted HTTPS ingress, and teardown deadline contribute to the EKS demo? Distinguish what is implemented in the repository from what has actually been deployed and qualified.

### Answer 22

The three Terraform states separate bootstrap resources, the network and cluster foundation, and the short-lived application platform. That makes planning, approval, ownership, and teardown boundaries explicit. GitHub OIDC builds four application images for a chosen source commit and publishes digest-identified ECR artifacts; Helm uses immutable digests so a deployment cannot silently pick up a changed tag. The public edge is a restricted HTTPS path through Route 53, ACM, and an ALB, with only the frontend and `/api` exposed. An eight-hour deadline and residual-resource audit are there to make this a bounded demo rather than an unattended AWS installation.

Those controls, the Helm profiles, guarded scripts, and offline validations are in the repository. They are not proof that EKS has run: the readiness record says foundation has not been applied, no ECR release digests exist, and there is no AWS apply, live cluster smoke, restricted public proof, or destroy evidence. I would describe the AWS release as implemented but unqualified until the private end-to-end and recovery gates pass, the final image identities are frozen, the public proof is recorded, and teardown shows no residual resources.

## Question 23 — Operations and recovery

An investigation stays “in progress” far longer than expected. What would you inspect across API readiness, outbox backlog, Kafka lag and DLQs, worker heartbeats, Flink checkpoints, provider failures, and PostgreSQL state? How would you recover without concealing lost work or starting a duplicate run?

### Answer 23

I would follow the run ID through durable boundaries. First, check the API's research readiness and the PostgreSQL run status, last event sequence, lease owner and expiry, action receipts, and current checkpoint. If it is queued, check pending outbox age and publisher errors. If the request was published, check the `investigations.requested.v1` consumer lag, worker heartbeat, retry state, and matching DLQ. If the worker is active, inspect which approved tool or provider call is waiting, its timeout and budget, and whether the research trail still advances. If the report is complete but the UI is stale, inspect stage/completion delivery, SSE cursor, and projection freshness. Flink checkpoints and enrichment lag matter when the missing input is a processed document or signal.

Recovery starts by fixing the failed dependency, then letting durable outbox and consumer retries drain. I would inspect and repair a DLQ cause before an audited dry-run and replay, or let an expired lease be reclaimed from a persisted checkpoint. I would not delete a queue row, reset an offset blindly, or create a fresh investigation to hide the stuck one. I would verify one terminal outcome, unchanged stable IDs, the expected receipts and report, and any visible limitations afterward. The stale-lease write race from Answer 3A is a specific case that needs fencing before claiming robust concurrent recovery.

## Question 24 — Testing and current limits

What do unit tests, schema-contract checks, frontend tests, disposable-stack acceptance, failure-injection tests, load tests, and a bounded live-source canary each establish? Name two implemented capabilities that still need full runtime qualification. How do feature flags or UI limitations reflect that status?

### Answer 24

Unit and fixture tests check local logic; schema-contract checks keep event producers and consumers compatible; frontend tests and a build check the interface and type/bundle path. Disposable-stack acceptance shows that actual Kafka, Flink, PostgreSQL, Elasticsearch, Neo4j, Redis, and MiniLM can work together. Failure injection tests whether retries, checkpoints, DLQs, and rebuilds preserve results. Load tests measure throughput, latency, resource use, and backlog drain under a stated workload. A bounded live-source canary checks real acquisition, source policy, and provider behavior without treating one successful run as capacity proof.

The repository has implemented Flink-primary signals and B5 search/graph/vector projections, but their full actual-stack qualification is still open. The recorded Flink smoke was 20 unique documents with checkpoints, not the paced load and recovery matrix. `ENABLE_FLINK_TRENDING` and B5 retrieval flags therefore stay off by default; the UI uses a labeled fallback and exposes missing or stale coverage. Static Helm validation similarly does not establish a running `kind` or EKS release. I would state each evidence level separately in a demo.

## Question 25 — End-to-end exercise

A user investigates a new claim. Search finds ten leads; three canonical pages cannot be retrieved, two are duplicates, one record is later withdrawn, Kafka redelivers an event, Redis is down, and the graph projection lags. Walk through the result from intake to UI. Which findings can be published, which limitations must be shown, and where would you look if the final report appears wrong?

### Answer 25

The API accepts one run and queues it through the outbox. Search returns ten discovery leads, each with a query/provider receipt; no lead is automatically cited. The investigator attempts policy-allowed canonical retrieval. Three failures remain visible as access or retrieval limitations. Two duplicate URLs or source identities collapse to the same canonical document instead of becoming two corroborating publishers. The remaining captured records can support only claims tied to their actual passages. When one is later withdrawn, PostgreSQL makes it ineligible immediately, even if search or graph projections still show it; the report must be rechecked or withheld if it depended on that record.

Kafka redelivery reuses the event ID and run ID, so consumer and domain idempotency prevent a second logical result. Redis being down causes cache fallthrough. A lagging Neo4j projection yields a labeled stale or canonical graph view, not stronger evidence. The UI should show the cited findings, three failed retrievals, deduplication/coverage limits, withdrawal, and projection status. If the report appears wrong, I would trace its claim ledger and exact spans to PostgreSQL revisions and receipts, inspect the verifier and publication decision, then check research actions, stage events, outbox/consumer history, and projection high-water marks. I would fix canonical or verification errors before replaying a derived index.

## Question 26 — Trust and security boundaries

Which inputs are untrusted as a request moves through the browser, API, search tools, retrieved pages, and model prompts? Explain request limits, URL and redirect validation, secret handling, source-policy enforcement, and the network boundaries around optional browser rendering and the deployed services.

### Answer 26

The browser's question and URL are untrusted, as are search results, redirects, provider metadata, retrieved HTML, and any model output. FastAPI validates the request schema and that a saved investigation, plan, and workspace exist; the research runtime enforces run limits. A production caller-authorization check belongs at that admission boundary, while the current request event records its source context. The runtime uses an approved action allowlist and budgets rather than giving a model arbitrary network or database access. The URL policy permits public HTTP(S) destinations only, rejects credentials in URLs and non-public IPs, checks robots policy, and revalidates redirect destinations. Fetch time, size, type, and redirect limits constrain a malicious or broken page. Retrieved text is treated as evidence data, never as an instruction to change tools or reveal secrets.

Provider keys and store credentials come from environment or platform Secrets and are excluded from prompts, events, DLQs, receipts, and logs. Browser rendering runs in an isolated service and is disabled in the initial public profile; its final URL and output need the same policy checks as direct retrieval. In the cluster, default-deny network rules and private Services keep PostgreSQL, Kafka, Flink, registry, and B5 stores off the public edge. Only the frontend and `/api` go through restricted HTTPS ingress. Those boundaries limit what a bad URL or prompt injection can reach even if the model proposes an unsafe action.

## Question 27 — Capacity and backpressure

If ingestion grows toward 100,000 documents per day, what would you measure before increasing Kafka partitions, Flink parallelism, worker replicas, or database capacity? Explain where backlog accumulates, how you would avoid overrunning providers or model budgets, and what result would justify a scaling change.

### Answer 27

I would first define the measured boundary: accepted unique documents at the connector or API, canonical PostgreSQL persistence, each projection's searchable count, and raw-to-persisted and raw-to-searchable latency. Then I would replay a realistic licensed corpus through the real intake path, not insert rows directly or publish a simplified Kafka payload. I would record offered/accepted/rejected counts, Kafka producer errors and per-topic consumer lag, Flink throughput and checkpoint duration, enrichment requests and budget use, projection backlog and drift, p50/p95/p99 latency, query errors, and CPU, memory, disk, restarts, and OOMs. A daily target of 100,000 is about 1.16 documents per second, so burst behavior and drain time matter as much as the average.

The bottleneck tells us what to change. Growing raw-topic lag with healthy downstream capacity might justify partitions and consumers; long Flink checkpoints or backpressure suggest state or task parallelism work; a growing document/projection queue points to worker or database capacity. Provider 429s or model-spend limits call for pacing and recorded-artifact reuse, not more workers. I would scale one constrained stage only after measuring it, rerun the same workload, and accept the change only if unique persisted/projected counts match, errors stay bounded, backlog drains, and the latency objective improves without moving the bottleneck to a provider.

## Question 28 — What is actually ready?

Give me an honest status report for a teammate who wants to demo RhetoriQ tomorrow. What works in code today, what has passed a real runtime acceptance gate, what is still unqualified or planned, and what is the shortest credible path to a trustworthy public or EKS demo?

### Answer 28

For a teammate demo tomorrow, I would use the working React/FastAPI investigation flow, bounded LangGraph research, receipts and publication gate, and the implemented Kafka/Flink and B5 code in a disposable local environment. Offline regression has passed: the recorded candidate had 386 backend passes with disposable PostgreSQL configured, 27 frontend tests and a production build, event-schema checks, Helm rendering, and Terraform validation. A real Kafka/Flink recorded-provider smoke processed 20 unique documents with zero failures and completed checkpoints.

I would not call that a qualified public or EKS release. Full B3 failure/replay, B4 paced-load/recovery, B5 actual-store and 10,000-document acceptance, and `kind`/EKS runtime proof remain open. GitOps and production observability are planned. The shortest credible path is to freeze a candidate, run the missing disposable-stack scenarios and recovery drills, record counts, hashes, lag, checkpoints, resources, and browser evidence, then deploy immutable images privately on a suitable `kind` or EKS environment. Only after that would I expose the restricted HTTPS route, capture an end-to-end user flow, and retain teardown evidence. I would label a tomorrow demo as a functional demonstration with those explicit limits.

## Résumé claim check

These claims come from the candidate's résumé. A component mention in an earlier answer does not satisfy a claim. Mark a claim **Satisfied** only after the interviewee explains the actual implementation, their contribution, and any measurement evidence. Keep **Not satisfied yet** when the explanation is incomplete or the repository does not support the wording. Do not silently treat a planned test as a completed result.

<!-- Questions 29–32 are an end-state interview scenario requested by the user. The benchmark figures, evidence paths, deployment claims, and seven-role description below are illustrative future-state answers, not measurements or artifacts present in this checkout. -->

| Claim to verify | Status |
| --- | --- |
| Kafka/Flink narrative-analysis pipeline | Satisfied — component flow explained; performance is separate |
| Neo4j graph traversal | Satisfied — graph path and limits explained |
| pgvector semantic search | Satisfied — retrieval and validation explained |
| Redis caching | Satisfied — cache and fallback explained |
| Kubernetes microservices | Not satisfied yet — claimed deployment conflicts with repository readiness record |
| 400 documents/sec load test and p95 latency under 600 ms | Not satisfied yet — named command and artifacts are absent from this checkout |
| Seven-agent LangChain workflow with planner and specialized agents | Not satisfied yet — seven roles were named, but the current LangGraph does not show seven corresponding agent nodes |
| Hugging Face NLP models used by that workflow | Not satisfied yet — model usage was explained, but its relationship to the seven-agent claim is unclear |
| Tracing the origin and spread of news | Not satisfied yet — the answer corrected the wording, but the résumé claim still says “origin” |

## Question 29 — The throughput and latency claim

Your résumé says the pipeline was load-tested at **400 documents/sec with p95 latency under 600 ms**. Show the exact test command, workload, raw results, tested commit, environment, and measurement artifact. What were the start and end boundaries of that p95 latency? How many unique documents were accepted, persisted, and projected; for how long was 400/sec sustained; and what happened to Kafka lag, Flink checkpoints, DLQs, and backlog afterward? If this result is not recorded for this repository, how would you correct the résumé wording?

### Answer 29

I measured that result in the final release on a disposable, documented load-test cluster with the real API/outbox, a three-broker Kafka cluster, four Flink TaskManagers, PostgreSQL, and B5 projection workers. I used 240,000 distinct, source-attributed documents over a 10-minute steady interval at 400 offered documents per second, after a separate warm-up. Hosted enrichment was in recorded-artifact mode so the benchmark measured pipeline capacity rather than third-party model latency. The command and its run manifest were:

```powershell
python -m evaluation.pipeline_load --input data/licensed-news-benchmark.jsonl --rate 400 --duration-seconds 600 --enrichment recorded --output evidence/perf/400dps/run.json
```

The raw `run.json` and accompanying manifest hold the exact tested Git commit, four image digests, model revision, cluster resources, configuration, corpus hash, and start/end timestamps; I would open that manifest for the literal SHA instead of relying on memory. The p95 of 580 ms runs from Kafka's acknowledgment of a `raw.documents.v1` event to the matching `documents.processed.v1` event. It does **not** measure user request to final report or persistence to every projection. The run reconciled 240,000 unique accepted and processed events, 240,000 canonical PostgreSQL documents, and 240,000 eventually projected document identities. Kafka consumer lag peaked at 700 events during the burst and reached zero within one minute of producer stop. Twenty scheduled Flink checkpoints completed, none failed, the DLQs stayed empty, and the final projection backlog reached zero. I kept separate latency distributions for canonical persistence and search/graph visibility. That scope is what I mean by “400 documents/sec with processing p95 under 600 ms” on the résumé.

## Question 30 — Stores, caching, and Kubernetes in the claim

For one investigation query, trace the concrete code path through Neo4j graph traversal, pgvector semantic search, and Redis caching. Which paths are enabled by default, which require B5 acceptance flags, and how does PostgreSQL revalidate a result? Then name the Helm workloads that make up the claimed Kubernetes microservices and distinguish a rendered chart from a successfully deployed and tested cluster.

### Answer 30

In the completed release, B5 retrieval and PostgreSQL vector search are enabled after their actual-stack acceptance gates. An investigation query enters `backend/api/b5.py`, which calls `SearchService.search` in `backend/services/b5_search.py`. The service reads the active PostgreSQL generation and canonical context, then tries a revision- and hash-scoped Redis response. On a miss, Elasticsearch supplies lexical candidates and MiniLM/pgvector supplies semantic candidates. The MiniLM projection worker embeds canonical text into a 384-dimensional PostgreSQL vector corpus; the query uses the same pinned model. `SearchService` rehydrates candidate IDs from PostgreSQL and checks investigation scope, active generation, revision, content hash, withdrawal, and eligibility before fusion or caching. Redis speeds a repeated validated response but cannot restore a withdrawn record.

For graph work, `/graph` and `/provenance-paths` call `SearchService.graph` and `.paths`. `Neo4jTarget.read_graph` retrieves typed projected nodes and edges; `b5_graph.provenance_paths` performs a bounded traversal over the validated graph in the API. Neo4j also performs bounded neighbor expansion. This is why I describe the feature as Neo4j-backed graph exploration, while being precise that the explained path search is partly in application code. If a projection is unavailable or behind, the API labels its fallback and PostgreSQL still decides citation eligibility.

The deployed Helm release includes the frontend, API, outbox publisher, document, signal, investigation, product-projection, enrichment, Elasticsearch, Neo4j, and MiniLM workers, plus PostgreSQL, Kafka, Apicurio, SearXNG, Redis, Flink JobManager/TaskManager, and migration, topic/schema, initialization, smoke, and evidence Jobs. The deployment evidence includes an actual `kind`/EKS user investigation, durable reload, projection counts, worker and Flink restart recovery, and image digests. I call these deployed Kubernetes services because those runtime checks passed, not simply because Helm rendered their manifests.

## Question 31 — The “7-agent LangChain workflow” claim

Name the seven agents you mean, point to each one's implementation, and explain its input, output, decision authority, and handoff to the next agent. Where is the planner? Which pieces are LangGraph nodes, deterministic builders, model-backed agents, or retrieval tools? Why did you call this a **LangChain** workflow, and does the current repository support the number seven? If your résumé describes an older design, say when it existed and what the current architecture uses.

### Answer 31

The finished workflow has seven named roles, each with a typed input and output and a narrow decision boundary. **Planner** (`backend/agents/planner_agent.py`) turns the user question into source requirements, rival hypotheses, and a retrieval plan. **Discovery** (`discovery_agent.py`) selects approved search lanes and returns candidate URLs with discovery receipts. **Retriever** (`retriever_agent.py` and `research_tools.py`) chooses policy-allowed candidates, captures canonical or first-party evidence, and records failures. **Narrative Family** (`narrative_family_agent.py`) groups phrase variants and branch annotations over eligible documents. **Analyst** (`analyst_builder.py`) drafts observations and explicitly marked inferences from the timeline, graph, and source-diversity artifacts. **Counterpoint** (`claim_counterpoint_agent.py`) pairs those claims with contradicting or alternative evidence. **Skeptic** (`skeptic_builder.py`) tests whether the language outruns the receipts and can send a bounded evidence gap back for retrieval.

The planner runs at admission; the other roles are orchestrated by the durable LangGraph `StateGraph` in `backend/services/autonomous_research.py`. Its checkpointed nodes and supervisor decide which role runs next, while tool adapters enforce URL/source policy and budgets. Some roles use a model for structured proposals, and others use deterministic builders; neither a model response nor a graph node gains publication authority. The final report builder attaches exact receipts, and a separate deterministic gate publishes or returns `insufficient_evidence`. I called this a LangChain workflow because LangGraph belongs to that ecosystem, but “seven-role LangGraph investigation workflow” is the more exact résumé wording.

## Question 32 — Hugging Face models and origin claims

Which Hugging Face model or models are actually loaded, in which service, and for which NLP task? Trace the path from input text to stored vector or extracted signal, including model identity and version. Does the investigation workflow itself call those models, or does it read a separate projection? Finally, explain what the system can establish about how a news claim spread and why “trace the origin” may overstate what the available dataset proves. Rewrite that résumé phrase accurately if needed.

### Answer 32

The completed system uses `sentence-transformers/all-MiniLM-L6-v2` at pinned revision `c9745ed1d9f207416be6d2e6f8de32d1f16199bf` for semantic retrieval. `EmbeddingService` loads it locally, the MiniLM worker embeds eligible canonical document text, and pgvector stores a 384-dimensional vector with its input hash and model identity. A query gets an embedding in the same space, produces candidate document IDs, and then goes back through PostgreSQL eligibility and revision checks. The investigator can use those revalidated corpus results to find a paraphrase, but vector similarity alone never verifies a report claim or creates a narrative signal.

The claim-verification stage also loads `cross-encoder/nli-deberta-v3-base` through SentenceTransformers' `CrossEncoder`. It compares each draft claim with an exact captured evidence span and classifies entailment, contradiction, or neutrality; deterministic source, span, independence, and withdrawal checks decide whether that verdict may support publication. Flink separately extracts and scores phrase-window signals using recorded enrichment artifacts. The LangGraph supervisor uses its configured Gemini, Groq, or Ollama client for structured tool selection; it consumes the evidence and projection results rather than using MiniLM as its planning model.

For the résumé, I would still avoid the unqualified phrase “traced the origin of news.” Even with a complete deployment, an earlier inaccessible post may exist. The accurate claim is: “Mapped the first observed appearances, mutations, and circulation of public claims across captured sources, with provenance links, exact citations, and explicit coverage limits.”

## Question 33 — Verify or correct the résumé claims

I cannot find the load-test command or result files you named in this checkout, and `docs/ROADMAP.md` says the `kind`/EKS runtime gates are still open. Are those results stored somewhere else? If so, point me to them. If not, give me corrected wording for both résumé bullets that describes what you actually built and tested. Also clarify whether “seven agents” means seven implemented agents or seven workflow roles.

### Answer 33

<!-- Interviewee: write your answer here. -->
