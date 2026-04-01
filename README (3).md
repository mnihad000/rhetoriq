# RhetoriQ

> An autonomous narrative intelligence agent that detects emerging political talking points in real time, traces their origin, and maps how they spread across platforms — without any human prompting.

---

## What Is RhetoriQ?

Every political narrative starts somewhere. A phrase coined on a fringe subreddit. A talking point buried in a think tank report. A line tested at a small rally. Within days it can be on every news channel, in every politician's mouth.

RhetoriQ watches all of it, continuously. The moment a narrative starts gaining unusual traction, RhetoriQ autonomously investigates — tracing it back to its origin, mapping every node that amplified it, and delivering a full provenance report before the narrative even hits mainstream media.

It is not a dashboard you query. It is a detective that never sleeps.

---

## What RhetoriQ Does

- **Detects** emerging political narratives in real time across Reddit, news outlets, and political speeches
- **Investigates autonomously** — no human prompting required
- **Traces origins** — finds where a narrative first appeared, down to the source and timestamp
- **Maps spread** — builds a graph of every node (subreddit, outlet, politician) that amplified the narrative
- **Delivers reports** — synthesizes findings into a clean, human-readable investigation report
- **Visualizes** — live graph visualization of how narratives travel from fringe to mainstream

---

## Architecture Overview

```
Data Sources → Scrapers → Kafka → Flink → Databases → LangChain Agent → GPT-4o → Report
                                                ↑
                                     PostgreSQL / pgvector
                                     Elasticsearch
                                     Neo4j
                                     Redis
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full deep dive.

---

## Tech Stack

### Data Ingestion
- Reddit API (PRAW)
- GDELT Project
- NewsAPI
- C-SPAN API
- RSS Feeds (NYT, BBC, Fox, Reuters, Breitbart)

### Stream Processing
- Apache Kafka
- Apache Flink

### Databases
- PostgreSQL + pgvector (vector embeddings + semantic search)
- Elasticsearch (full-text search)
- Neo4j (graph — spread mapping)
- Redis (caching)

### AI / ML
- LangChain (agentic investigation loop)
- OpenAI GPT-4o (narrative synthesis and reasoning)
- HuggingFace (NER, entity extraction, sentence embeddings)

### Infrastructure
- Kubernetes (container orchestration)
- Docker (containerization)
- Terraform (infrastructure as code)
- ArgoCD (GitOps continuous deployment)
- GitHub Actions (CI/CD)

### Observability
- Prometheus (metrics)
- Grafana (dashboards)

### Frontend
- React + TypeScript
- Neo4j graph visualization
- WebSockets (live updates)

---

## Folder Structure

```
rhetoriq/
├── README.md
├── ARCHITECTURE.md
├── ROADMAP.md
├── KAFKA.md
├── SERVICES.md
├── AGENT.md
├── INFRASTRUCTURE.md
├── DATA_SOURCES.md
├── TESTING.md
├── CONTRIBUTING.md
├── TROUBLESHOOTING.md
├── backend/
│   ├── BACKEND.md
│   ├── scrapers/          # One scraper microservice per data source
│   ├── processors/        # Flink stream processing jobs
│   ├── agent/             # LangChain agent and investigation loop
│   └── api/               # REST API serving the frontend
├── frontend/
│   ├── FRONTEND.md
│   └── src/
├── infra/
│   └── terraform/         # All infrastructure as code
└── k8s/
    └── manifests/         # Kubernetes deployment manifests
```

---

## Quick Start (Local Development)

### Prerequisites
- Docker Desktop
- Python 3.11+
- Node.js 18+
- kubectl
- Terraform CLI

### 1. Clone the repo
```bash
git clone https://github.com/yourusername/rhetoriq.git
cd rhetoriq
```

### 2. Set up environment variables
```bash
cp .env.example .env
# Fill in your API keys — see DATA_SOURCES.md for how to get each one
```

### 3. Start core infrastructure locally
```bash
docker-compose up -d
# Starts Kafka, Zookeeper, PostgreSQL, Elasticsearch, Neo4j, Redis
```

### 4. Run the scrapers
```bash
cd backend/scrapers
pip install -r requirements.txt
python run_all.py
```

### 5. Start the Flink processor
```bash
cd backend/processors
python flink_job.py
```

### 6. Start the agent
```bash
cd backend/agent
python agent.py
```

### 7. Start the API
```bash
cd backend/api
uvicorn main:app --reload
```

### 8. Start the frontend
```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000` to see RhetoriQ running.

---

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o |
| `REDDIT_CLIENT_ID` | Reddit API client ID |
| `REDDIT_CLIENT_SECRET` | Reddit API client secret |
| `NEWS_API_KEY` | NewsAPI key |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address |
| `POSTGRES_URL` | PostgreSQL connection string |
| `ELASTICSEARCH_URL` | Elasticsearch connection string |
| `NEO4J_URI` | Neo4j connection URI |
| `NEO4J_PASSWORD` | Neo4j password |
| `REDIS_URL` | Redis connection string |

See [DATA_SOURCES.md](./DATA_SOURCES.md) for full details on obtaining each API key.

---

## Documentation

| Document | Description |
|---|---|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Full system design and data flow |
| [KAFKA.md](./KAFKA.md) | Kafka topics, schemas, and partition strategy |
| [SERVICES.md](./SERVICES.md) | Every microservice documented individually |
| [AGENT.md](./AGENT.md) | LangChain agent investigation loop |
| [DATA_SOURCES.md](./DATA_SOURCES.md) | APIs, credentials, rate limits |
| [INFRASTRUCTURE.md](./INFRASTRUCTURE.md) | Kubernetes, Terraform, ArgoCD setup |
| [TESTING.md](./TESTING.md) | Testing strategy per layer |
| [CONTRIBUTING.md](./CONTRIBUTING.md) | Coding standards and PR process |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Common failure modes and fixes |
| [backend/BACKEND.md](./backend/BACKEND.md) | API endpoints and database schemas |
| [frontend/FRONTEND.md](./frontend/FRONTEND.md) | Frontend components and architecture |

---

## Why RhetoriQ?

Political narratives shape elections, policy, and public opinion. Understanding where they come from and how they spread is one of the most important open problems in political science and journalism. RhetoriQ operationalizes that problem as a real-time autonomous system — turning a question philosophers and researchers have argued about for decades into something measurable, traceable, and visible.

---

*Built with the conviction that information has a history, and that history matters.*
