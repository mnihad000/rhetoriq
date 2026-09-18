# B5 sprint: search, provenance, and recoverable projections

Status: **In Progress**. Implementation and offline verification are available. B3/B4 and B5 actual-stack acceptance remain required. Kubernetes manifests belong to B6.

## Outcome

One persisted investigation must expose MiniLM/pgvector semantic recall, Elasticsearch literal phrase and lexical retrieval, and Neo4j provenance paths; reload after restarts; and report pending or degraded dependencies accurately. PostgreSQL owns citation eligibility, artifact identity, revisions, withdrawals, and the active deployment generation.

## Implemented boundaries

| Boundary | Implementation |
| --- | --- |
| Immutable inputs and revisions | Migration `008_b5_projections.sql`; `services/b5_repository.py` |
| Atomic document, lineage, corpus and dispatch | `EventHandlers.persist_processed_document`; shared database connections |
| Atomic terminal publication, snapshot, completion and dispatch | `AutonomousResearchRuntime._finish_terminal`; generic report saves cannot publish snapshots |
| Explicit A3 artifact revision | Verification route records a separate operation identity and investigation snapshot |
| Independent consumers | `events.b5_worker`, distinct ES/Neo4j/MiniLM groups and consumption ledgers |
| Versioned contracts | `corpus.projections.v1`, `investigation.projections.v1`, their DLQs and committed schemas |
| Lexical projection | Explicit Elasticsearch mapping, external revision guards, literal verification after phrase candidate retrieval |
| Semantic projection | Pinned Hugging Face MiniLM; recorded finite 384-dimensional vectors; separate B4 Gemini table |
| Graph | Typed node families, independently identified observed/contextual/inferred edges, scope-bound pair analysis, explained simple paths |
| References | Deterministic HTML parsing, final-text offset validation, 100-reference cap, unresolved targets, no automatic crawl |
| Product | Evidence search/filter/highlighting; React Flow Narrative, timeline context, relationship details and explained paths |
| Recovery | Audited withdrawal/restore, bounded bootstrap, drift checks/repair, isolated rebuild, manifest cutover and rollback |
| Cache | Redis TTL and item bounds, generation/revision keys, canonical validation, failure fallthrough and counters |
| Runtime | Authenticated encrypted stores, image/model locks, private store ports, initialization jobs, probes and memory caps |
| Qualification tooling | Recorded-provider 10,000-document runner, exact paced windows, warm-up and scoped query probes, batch delivery/ES visibility traces, host memory/restart/OOM supervisor |

## Authoritative write rules

The processed-document transaction stores the immutable projection input, current canonical pointer, processed lineage, optional Gemini artifact, MiniLM corpus row in pending state, research membership, and outbox event together. A worker failure cannot roll back a canonical acquisition. The terminal transaction stores final publication state, immutable investigation evidence, the run completion event, and graph dispatch together.

Document IDs survive every projection. Claims are scoped by investigation. Source IDs are preserved; a missing ID is derived from the URL domain. B4's stable phrase identity is reused. Event/operation mappings survive later revisions, so replay of an old event returns its original recorded revision. Receipted evidence outranks leads; acquisition time and a deterministic identity tie-break select canonical content. Explicit re-enrichment may supersede only the matching current input. Withdrawal remains effective until an explicit restore.

Snapshots exclude presentation-only cache and projection timestamps. Material text, provenance, spans, terminal decisions, artifact and model identities remain hash inputs. Immutable snapshot records are rebuild inputs, rather than enrichment instructions.

## Projection and read rules

Each consumer uses its own Kafka group and ledger. Lower revisions cannot overwrite newer writes. Equal revisions with unequal hashes fail. Bulk-item failures remain retryable; a successful HTTP bulk response alone does not complete a delivery. Owned obsolete graph edges/nodes are removed when an artifact revision changes. Withheld runs preserve evidence and a terminal publication decision, while removing active published-report relationships.

Investigation graph construction uses its pinned document snapshots and resolves source links only to acquired documents. Unknown targets remain `ReferenceTarget` nodes. Validated mentions preserve surface forms and offsets; invalid spans carry limitations. Chronology and entity overlap are contextual. Mutation and amplification remain versioned hypotheses. Hyperlinks record references, never agreement or support. Product language says “first observed in the available dataset.”

Search rehydrates candidates from PostgreSQL and checks scope, eligibility, withdrawal, revision/hash and model identity. Phrase candidates must match a case-insensitive, whitespace-normalized literal span. Hybrid retrieval combines independent lexical/semantic lanes with reciprocal-rank fusion, `k=60`, at most 50 candidates per lane. Graph expansion adds at most 20 already stored contextual neighbors within the research document budget; it cannot increase claim support or bypass publication checks.

Every cache read is subject to current canonical checks. Graph/path reads cannot traverse a withdrawn document. An empty healthy result differs from pending projection and service failure. Canonical fallbacks are explicitly limited and labeled with their origin.

## Gates

| Gate | Passing evidence required | Current state |
| --- | --- | --- |
| 1 Contracts/runtime | Transaction/schema checks and actual authenticated startup/probes | Offline checks pass; startup pending |
| 2 Document projections | A real B4 event reaches PostgreSQL, ES, Neo4j and MiniLM with matching identities | Offline adapter/repository checks pass; actual delivery pending |
| 3 Evidence/product | Persisted scoped search, citations, references, hypotheses, paths and browser reload | Product tests/build; actual browser acceptance pending |
| 4 Recovery/consistency | Duplicate/stale replay, crashes, withdrawal, drift repair, concurrent rebuild, cutover, rollback and cache invalidation | Offline boundary checks; actual failures/recovery pending |
| 5 Qualification/closeout | Full rate/latency/memory qualification, bounded live canary, regressions and B6 handoff | Regressions/tooling implemented; runtime qualification pending |

See [acceptance evidence](B5_ACCEPTANCE.md) for exact checks and [operations](B5_OPERATIONS.md) for commands and the B6 handoff. Gate completion requires recorded passing evidence, not an implementation checkbox or a calendar estimate.

## Remaining acceptance work

Run the disposable PostgreSQL tests, actual B3/B4 Kafka/Flink delivery/recovery checks, B5 smoke and failure drills, browser acceptance, 10,000-document load qualification and at-most-20-document live canary. Record resource measurements, raw-to-search and projection latency, cached/uncached query distributions, drift reports and service restarts. Resolve any failures before marking B5 complete.

The existing agent runtime is self-hosted LangGraph. A qualified 100/minute rate equals 144,000/day arithmetically; it does not establish 100,000 live documents processed daily. Local Kubernetes remains B6.
