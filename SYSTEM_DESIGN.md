# RhetoriQ System Design

RhetoriQ is an evidence-first narrative investigation system. It identifies public narrative signals, preserves the source trail behind them, and gives users a workspace for examining spread, mutation, counter-frames, and uncertainty.

## Design principles

1. **Evidence before synthesis.** Reports must link material claims to retrievable source records.
2. **Observed is not proven.** The system says “first observed in the available dataset” and never treats correlation as proof of coordination.
3. **Autonomous but bounded research.** The planned LangGraph investigator will select approved self-operated search, browser, and corpus tools per evidence gap; fixed budgets and source policy will constrain every run.
4. **Transport is not source.** A publisher, public record, or social post is the source; an API, feed, search result, or page fetch is how it is acquired.
5. **Decomposed investigation.** Retrieval, timeline building, graph analysis, source-diversity analysis, and report synthesis are separate stages.
6. **Inspectable outputs.** Intermediate artifacts, limitations, provider coverage, and unresolved gaps remain visible.
7. **Durable processing.** The production architecture will use checkpoints and replayable events so each layer can scale and recover independently.

## System flow

```mermaid
flowchart TB
    subgraph Collection
      Q[User question]
      A[First-party public-record APIs]
      B[RSS, Atom, and event streams\nlater monitoring]
      C[Live search, corpus, and isolated browser]
      D[Canonical-page retrieval]
      Q --> E[Agent-selected research tools]
      A --> E
      C --> E
      B -. later .-> E
      E --> D
      D --> F[Normalized evidence records]
      E --> F
    end
    subgraph Processing
      F --> G[Trend and investigation pipelines]
      G -. target: replayable events .-> H[Kafka and stream processing]
    end
    subgraph InvestigationData[Investigation data]
      G --> I[(Development stores)]
      H -. target .-> J[(PostgreSQL, search index, graph store, Redis)]
    end
    subgraph Product
      I --> K[Current investigation services]
      J -. target .-> K
      K --> L[FastAPI]
      L --> M[React frontend]
    end
```

## Layer responsibilities

| Layer | Responsibility |
|---|---|
| Agent research tools | Search the live web, query approved primary sources, retrieve canonical pages, preserve receipts, and return normalized discovery or evidence records for a user question. |
| Scheduled monitoring connectors | Later capability for recurring feeds/streams; checkpoint progress, preserve provider metadata, and emit normalized discovery records. |
| Evidence retrieval | Fetch canonical pages when necessary, respecting access policy, and record retrieval outcomes. |
| Processing | Deduplicate, classify, extract phrases and entities, calculate embeddings, and detect signals. |
| Storage | Preserve documents, receipts, vectors, investigation state, and source relationships. |
| Investigative workflow | Select approved research tools, preserve a structured trail, and construct evidence-limited artifacts. |
| API | Provide stable interfaces for ingestion, investigations, narratives, and graph data. |
| Frontend | Present a narrative radar and inspectable investigation workspace. |

## Investigation lifecycle

1. A user submits a research question. Later, a scheduled monitoring connector may also create a candidate signal.
2. The planner converts the question into retrieval lanes and uncertainty requirements.
3. The current retriever follows the planner's retrieval lanes, normalizes canonical evidence where required, and records actions and failures as receipts or evidence gaps.
4. The planned LangGraph supervisor will select permitted search, browser, or corpus tools based on the next evidence gap.
5. Deterministic stages build the timeline, narrative family, counter-frames, source-diversity summary, and spread graph.
6. A skeptic pass identifies unsupported conclusions and missing source classes.
7. The report synthesizer produces cautious findings and attaches receipts.
8. An evidence-threshold gate publishes a cited report or returns `insufficient_evidence`; the API persists and serves the investigation workspace.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component-level boundaries and [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) for source policy.
