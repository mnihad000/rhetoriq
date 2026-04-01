# RhetoriQ — Agent

> This document covers everything about the LangChain investigation agent. The agent is the core of RhetoriQ — read this document fully before touching anything in `backend/agent/`. It covers the investigation loop, every tool, the GPT-4o prompting strategy, edge cases, and failure handling.

---

## Overview

The agent is an autonomous LangChain ReAct agent that runs continuously as a Kubernetes deployment. It consumes anomaly alerts from the `anomalies.detected` Kafka topic and conducts a full investigation without any human input.

The agent's job is to answer one question for every anomaly:

> *"Where did this narrative come from, how did it spread, and who were the key amplifiers?"*

It answers this by autonomously deciding which tools to call, in what order, and when it has enough information to synthesize a final report.

---

## Investigation Loop

```
anomalies.detected (Kafka)
         │
         ▼
┌─────────────────────────────────────────────────┐
│              REACT AGENT LOOP                    │
│                                                  │
│  1. Parse anomaly                                │
│         │                                        │
│         ▼                                        │
│  2. Semantic search backwards (pgvector)         │
│         │                                        │
│         ▼                                        │
│  3. Trace spread graph (Neo4j)                   │
│         │                                        │
│         ▼                                        │
│  4. Full text search over time (Elasticsearch)   │
│         │                                        │
│         ▼                                        │
│  5. Enrich key sources (Postgres)                │
│         │                                        │
│         ▼                                        │
│  6. Synthesize report (GPT-4o)                   │
│                                                  │
└─────────────────────────────────────────────────┘
         │
         ▼
investigations.complete (Kafka)
```

The agent uses a **ReAct (Reason + Act)** pattern — it reasons about what to do next, calls a tool, observes the result, and decides the next step. This continues until the agent decides it has enough information to write the final report.

---

## Tools

The agent has access to 5 tools. Each tool is a Python function wrapped with LangChain's `@tool` decorator.

### Tool 1 — semantic_search

**Purpose:** Find documents that are semantically similar to the anomalous phrase. Used to trace the earliest appearances of a narrative even when exact wording differs.

```python
@tool
def semantic_search(query: str, limit: int = 20, days_back: int = 90) -> list[dict]:
    """
    Search for documents semantically similar to the query using pgvector.
    Returns documents sorted by published_at ascending (oldest first).
    Use this to find the earliest appearances of a narrative.
    
    Args:
        query: The phrase or narrative to search for
        limit: Number of results to return (default 20, max 50)
        days_back: How far back to search in days (default 90)
    
    Returns:
        List of documents with id, source, url, published_at, body_clean, similarity_score
    """
    embedding = embedder.encode(query).tolist()
    
    results = pg_client.execute("""
        SELECT 
            id, source, url, published_at, body_clean,
            1 - (embedding <=> %s::vector) AS similarity_score
        FROM documents
        WHERE published_at > NOW() - INTERVAL '%s days'
        AND 1 - (embedding <=> %s::vector) > 0.75
        ORDER BY published_at ASC
        LIMIT %s
    """, (embedding, days_back, embedding, limit))
    
    return results
```

### Tool 2 — graph_trace

**Purpose:** Traverse the Neo4j spread graph to map how a narrative traveled from source to source. Returns the full spread path and identifies key amplifier nodes.

```python
@tool
def graph_trace(phrase: str, max_depth: int = 10) -> dict:
    """
    Trace how a narrative spread across sources using Neo4j graph traversal.
    Returns the spread path from earliest source to most recent, and identifies
    key amplifier nodes that caused the narrative to jump between communities.
    
    Args:
        phrase: The narrative phrase to trace
        max_depth: Maximum graph traversal depth (default 10)
    
    Returns:
        Dict with spread_path (ordered list of sources) and key_amplifiers
    """
    result = neo4j_client.run("""
        MATCH (d:Document)-[:CONTAINS]->(p:Phrase {text: $phrase})
        WITH d ORDER BY d.published_at ASC
        MATCH path = (origin:Source)-[:AMPLIFIED*1..%d]->(amplifier:Source)
        WHERE (origin)-[:PUBLISHED]->(d)
        RETURN path, 
               [node in nodes(path) | {
                   name: node.name,
                   type: node.type,
                   published_at: node.first_published_at
               }] AS spread_nodes
        ORDER BY length(path) DESC
        LIMIT 1
    """ % max_depth, phrase=phrase)
    
    return parse_graph_result(result)
```

### Tool 3 — full_text_search

**Purpose:** Search Elasticsearch for every exact and near-exact usage of a phrase across all sources with timestamps. Used to build a precise timeline of phrase adoption.

```python
@tool
def full_text_search(phrase: str, days_back: int = 90) -> list[dict]:
    """
    Search for exact and near-exact phrase matches across all ingested documents.
    Returns results sorted by published_at ascending (oldest first).
    Use this to build a precise timeline of when and where a phrase appeared.
    
    Args:
        phrase: The exact phrase to search for
        days_back: How far back to search in days (default 90)
    
    Returns:
        List of matches with source, outlet, published_at, url, and matched excerpt
    """
    results = es_client.search(
        index="documents",
        body={
            "query": {
                "bool": {
                    "must": [
                        {"match_phrase": {"body_clean": phrase}}
                    ],
                    "filter": [
                        {"range": {"published_at": {"gte": f"now-{days_back}d"}}}
                    ]
                }
            },
            "sort": [{"published_at": "asc"}],
            "size": 100,
            "_source": ["id", "source", "url", "published_at", "metadata"],
            "highlight": {
                "fields": {"body_clean": {}},
                "number_of_fragments": 1
            }
        }
    )
    
    return parse_es_results(results)
```

### Tool 4 — get_source_profile

**Purpose:** Retrieve metadata about a specific source (subreddit, outlet, or politician). Used to understand the nature of key nodes in the spread graph — is this a fringe source or mainstream? What is its typical audience?

```python
@tool
def get_source_profile(source_id: str) -> dict:
    """
    Get metadata about a specific source to understand its role in the spread graph.
    Use this after graph_trace to understand the nature of key amplifier nodes.
    
    Args:
        source_id: The source identifier (e.g. subreddit name, outlet name)
    
    Returns:
        Dict with source type, audience size estimate, political lean, 
        typical topics, and total documents in our corpus
    """
    # Check Redis cache first
    cached = redis_client.get(f"source_profile:{source_id}")
    if cached:
        return json.loads(cached)
    
    profile = pg_client.execute("""
        SELECT 
            s.id, s.name, s.type, s.political_lean, s.audience_size_estimate,
            COUNT(d.id) AS document_count,
            MIN(d.published_at) AS first_seen,
            MAX(d.published_at) AS last_seen
        FROM sources s
        LEFT JOIN documents d ON d.source_id = s.id
        WHERE s.id = %s
        GROUP BY s.id
    """, (source_id,))
    
    # Cache for 1 hour
    redis_client.setex(f"source_profile:{source_id}", 3600, json.dumps(profile))
    return profile
```

### Tool 5 — synthesize_report

**Purpose:** The final tool. Called once the agent has gathered sufficient evidence. Passes all findings to GPT-4o with a structured prompt and returns a human-readable investigation report in markdown.

```python
@tool
def synthesize_report(findings: dict) -> str:
    """
    Synthesize all investigation findings into a human-readable report.
    Call this ONLY when you have completed all other investigation steps
    and have gathered: origin document, spread path, key amplifiers, and timeline.
    
    Args:
        findings: Dict containing all gathered evidence:
            - phrase: the anomalous phrase
            - origin: earliest document found
            - spread_path: ordered list of sources
            - key_amplifiers: nodes that caused major jumps
            - timeline: full_text_search results
            - source_profiles: profiles of key nodes
    
    Returns:
        Complete investigation report in markdown format
    """
    response = openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(findings, indent=2)}
        ],
        temperature=0.3  # Low temperature for factual synthesis
    )
    
    return response.choices[0].message.content
```

---

## GPT-4o Prompting Strategy

### System Prompt

```
You are a political narrative intelligence analyst. You will be given structured 
evidence about a detected political narrative — its origin, spread path, key 
amplifiers, and timeline. Your job is to synthesize this evidence into a clear, 
factual investigation report.

Your report must follow this exact structure:

## Narrative: [the phrase]

### Summary
2-3 sentence overview of the finding. What is the narrative, where did it start, 
and how significant is the spread?

### Origin
Where and when did this narrative first appear? Be specific — include source, date, 
and URL. State your confidence level (high/medium/low) and why.

### Spread Timeline
A chronological account of how the narrative moved from its origin to mainstream 
adoption. For each major stage, note the source, date, and what caused the jump 
to the next stage.

### Key Amplifiers
Who were the most important nodes in the spread? Rank them by impact and explain 
why each was significant — was it their audience size, their timing, or their 
credibility that mattered?

### Pattern Classification
Classify this spread pattern as one of:
- **Grassroots** — originated organically in public discourse, adopted upward
- **Top-down** — originated from official/institutional sources, spread downward  
- **Astroturfed** — shows signs of coordinated inauthentic amplification
- **Reactive** — emerged in response to a specific event
State your reasoning.

### Significance
Why does this narrative matter? What is its likely political impact?

---
Rules:
- Be factual. Only state what the evidence supports.
- Never editorialize or take political sides.
- If evidence is ambiguous, say so explicitly.
- If confidence is low on origin, present the 2-3 most likely candidates.
- Keep the report under 800 words.
```

### Why Temperature 0.3?
The synthesis step is factual reporting, not creative writing. Lower temperature keeps the model grounded in the evidence passed to it and reduces hallucination risk. We do not want GPT-4o inventing details about the spread — every claim in the report must be traceable to the findings dict.

---

## Agent System Prompt

```
You are an autonomous narrative intelligence agent. Your job is to investigate 
detected political narrative anomalies by using your available tools in sequence.

When you receive an anomaly, follow this investigation process:

1. Use semantic_search to find the earliest appearances of this narrative. 
   Search broadly first, then narrow down to the most relevant results.

2. Use graph_trace to map how the narrative spread across sources.

3. Use full_text_search to build a precise timeline with exact phrase matches.

4. Use get_source_profile on the 3-5 most important nodes from graph_trace 
   to understand their nature and audience.

5. Once you have gathered sufficient evidence from steps 1-4, call 
   synthesize_report with all your findings.

Rules:
- Always complete steps 1-4 before calling synthesize_report.
- If semantic_search returns fewer than 3 results, try broader search terms.
- If graph_trace returns an empty spread path, note this as a potential 
  emerging narrative with no established spread yet.
- You have a maximum of 10 tool calls per investigation.
- If you cannot find sufficient evidence within 10 calls, call synthesize_report 
  with whatever you have and note the evidence gaps.
- Never fabricate evidence. If data is missing, say so in the report.
```

---

## Agent Configuration

```python
from langchain.agents import create_react_agent, AgentExecutor
from langchain_openai import ChatOpenAI
from langchain import hub

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0,          # Zero temperature for consistent reasoning
    max_tokens=2000,        # Enough for reasoning traces
    timeout=30              # 30 second timeout per LLM call
)

tools = [
    semantic_search,
    graph_trace,
    full_text_search,
    get_source_profile,
    synthesize_report
]

prompt = hub.pull("hwchase17/react")  # Standard ReAct prompt template

agent = create_react_agent(llm, tools, prompt)

agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,           # Log all reasoning steps
    max_iterations=10,      # Hard cap on tool calls
    handle_parsing_errors=True,
    return_intermediate_steps=True  # Store reasoning trace in report
)
```

---

## Full Investigation Example

**Input anomaly:**
```json
{
  "anomaly_id": "a1b2c3d4",
  "phrase": "climate lockdowns",
  "spike_magnitude": 4.7,
  "window_frequency": 847,
  "baseline_frequency": 180,
  "top_sources": [
    {"source": "reddit", "subreddit": "conspiracy", "count": 312},
    {"source": "rss", "outlet": "Breitbart", "count": 201},
    {"source": "rss", "outlet": "Fox News", "count": 178}
  ]
}
```

**Agent reasoning trace:**
```
Thought: I need to investigate the spike in "climate lockdowns". 
Let me start by searching semantically for the earliest appearances.

Action: semantic_search
Action Input: {"query": "climate lockdowns", "days_back": 180, "limit": 20}
Observation: 20 results. Earliest: r/conspiracy post from 47 days ago, 
then a Breitbart article 3 days later, then Fox News 2 days after that.

Thought: Good. Now let me trace the spread graph to see how it moved.

Action: graph_trace  
Action Input: {"phrase": "climate lockdowns"}
Observation: Spread path: r/conspiracy → r/conservative → Breitbart → 
Fox News → Senator X floor speech (12 days ago)

Thought: Interesting spread pattern. Let me get a precise timeline.

Action: full_text_search
Action Input: {"phrase": "climate lockdowns", "days_back": 180}
Observation: 847 matches. First on r/conspiracy 47 days ago. 
Jumped to Breitbart 44 days ago. Fox News 42 days ago. 
Senator X speech 12 days ago. Current spike started 2 days ago.

Thought: Let me profile the key amplifier nodes.

Action: get_source_profile
Action Input: {"source_id": "breitbart"}
Observation: Far-right outlet, ~40M monthly readers, 
high political lean score, frequently cross-posts with r/conservative

Action: get_source_profile
Action Input: {"source_id": "senator_x"}
Observation: Republican senator, 2.1M Twitter followers, 
member of Senate Environment Committee

Thought: I have sufficient evidence. Time to synthesize.

Action: synthesize_report
Action Input: {all findings}
Observation: [Full markdown report generated]
```

---

## Edge Cases

| Scenario | Agent Behavior |
|---|---|
| Anomaly is breaking news (not a narrative) | Semantic search returns high similarity to news event documents — agent notes this in report as "reactive narrative" |
| Narrative has no traceable origin | Agent notes insufficient data, reports earliest found document as tentative origin with low confidence |
| Graph trace returns empty | Agent notes narrative may be too new for spread graph — relies on semantic search and full text search only |
| Duplicate anomaly (same phrase, different window) | Redis deduplication — agent skips if same phrase investigated within 24hrs |
| Investigation exceeds 10 tool calls | AgentExecutor hard stops, calls synthesize_report with partial findings |
| OpenAI rate limit hit | Exponential backoff with max 3 retries, then dead-letters the anomaly to a `anomalies.failed` Kafka topic |

---

## Cost Management

Each investigation calls GPT-4o twice — once for the reasoning loop and once for synthesis. Estimated cost per investigation:

| Call | Tokens (approx) | Cost (GPT-4o) |
|---|---|---|
| ReAct reasoning loop | ~3,000 input, ~500 output | ~$0.02 |
| Synthesis | ~2,000 input, ~800 output | ~$0.015 |
| **Total per investigation** | | **~$0.035** |

At 100 investigations per day: ~$3.50/day. Monitor with Prometheus metric `agent_openai_cost_usd_total`.

To reduce costs:
- Increase `ANOMALY_SPIKE_THRESHOLD` to reduce false positive anomalies
- Add a minimum window frequency floor (e.g. ignore anomalies with < 50 occurrences)
- Cache synthesis results for identical phrases within 24hrs

---

## Monitoring

| Metric | Description |
|---|---|
| `agent_investigations_total` | Total investigations completed |
| `agent_investigation_duration_seconds` | Time per investigation (p50, p95, p99) |
| `agent_tool_calls_per_investigation` | Average tool calls used |
| `agent_openai_cost_usd_total` | Running OpenAI spend |
| `agent_failed_investigations_total` | Investigations that hit max iterations or errored |
| `agent_kafka_lag` | Consumer lag on `anomalies.detected` |
