# RhetoriQ — Roadmap & Progress Tracker

> You are an intermediate developer with full-stack experience and database knowledge. You have never used Docker, Kafka, or Kubernetes before. You have 5-10 hours per week. This roadmap is built around that reality. Every phase is sequenced so you are never building on a foundation you do not understand yet. Do not skip phases.

---

## Part 1 — Things To Learn Before Writing Any Code

You do not need to master these. You need to understand them well enough to not be confused when you encounter them in the codebase. At 5-10hrs/week expect **3-4 weeks** here. Do not rush this — every hour here saves 5 hours of confusion later.

---

### 1. Docker (1-2 days)
Without Docker you cannot run this project locally at all. Everything — Kafka, all four databases, every service — runs in Docker.

**What to learn:**
- What a container is and why it exists
- `docker build`, `docker run`, `docker ps`, `docker logs`
- How to write a basic `Dockerfile`
- What `docker-compose` is and how to run multiple containers together
- Volumes and environment variables in Docker

**Resource:** https://docs.docker.com/get-started/ — Parts 1 through 4 only.

**You are ready when:** You can write a Dockerfile for a Python script, run it, and spin up Postgres + Redis together with a single `docker-compose up`.

---

### 2. Kafka (2-3 days)
Kafka is the nervous system of RhetoriQ. Every service talks to every other service through Kafka. Understand this before writing a single scraper.

**What to learn:**
- What a message broker is and why you would use one instead of direct API calls
- What topics, producers, and consumers are
- What consumer groups are and why they matter
- What partitions are and why they enable parallelism
- How to produce and consume messages in Python with `kafka-python`

**Resource:** https://developer.confluent.io/courses/apache-kafka/events/ — first 6 modules only, free.

**You are ready when:** You can spin up Kafka with Docker Compose, write a Python producer that sends a message, and a Python consumer that reads it.

---

### 3. Kubernetes Basics (2-3 days)
You will not write complex K8s configs from scratch early on — the manifests are already documented. But you need to understand what Kubernetes is doing so you are not lost when pods crash.

**What to learn:**
- What Kubernetes is and why you would use it over plain Docker
- What a Pod, Deployment, and Service are
- Basic `kubectl` commands: `get pods`, `logs`, `describe`, `apply`
- What namespaces are
- How env vars and secrets work in a manifest

**Resource:** https://kubernetes.io/docs/tutorials/kubernetes-basics/ — all 6 modules, about 3 hours.

**You are ready when:** You can read a Kubernetes deployment manifest and understand every field without confusion.

---

### 4. Python Async (1 day)
The storage worker writes to 4 databases in parallel using `asyncio`. If you have never written async Python, spend one day here.

**What to learn:**
- `async def`, `await`, `asyncio.gather()`
- When to use async vs threads

**Resource:** https://realpython.com/async-io-python/ — first half only.

**You are ready when:** You can write a function that makes 3 HTTP requests in parallel with `asyncio.gather()`.

---

### 5. FastAPI (1 day)
The backend API is FastAPI. If you have only used Flask or Express before, it will feel familiar but Pydantic and dependency injection are new concepts worth learning properly.

**What to learn:**
- Routes, path params, query params
- Pydantic models for validation
- `Depends()` for dependency injection
- WebSocket endpoints
- Running with `uvicorn`

**Resource:** https://fastapi.tiangolo.com/tutorial/ — first 10 sections only.

**You are ready when:** You can build a small API with Pydantic models and a working WebSocket endpoint.

---

### 6. LangChain Basics (1-2 days)
The agent is a LangChain ReAct agent. You do not need to be an LLM expert — you need to understand how tools and the reasoning loop work.

**What to learn:**
- What an LLM agent is vs a plain LLM call
- The ReAct (Reason + Act) loop
- How to define a tool with `@tool`
- How to create and run an `AgentExecutor`

**Resource:** https://python.langchain.com/docs/tutorials/agents/ — quickstart and tools sections only.

**You are ready when:** You can build a LangChain agent with 2 custom tools and watch it reason step by step in your terminal.

---

## Part 2 — The Build Phases

Each phase has a clear goal, task checklist, which files you are working in, and a "you are done when" condition. Do not move to the next phase until that condition is met.

---

## ✅ Phase 0 — Project Setup
**Goal:** Repo exists, folder structure is correct, local infrastructure runs.
**Estimated time:** 1 week

- [ ] Create GitHub repo named `rhetoriq`
- [ ] Create the full folder structure from README.md
- [ ] Place all `.md` documentation files in their correct locations
- [ ] Install local tools: Docker Desktop, Python 3.11+, Node.js 18+, kubectl
- [ ] Create `.env.example` with all environment variable keys (no values yet)
- [ ] Write `docker-compose.yml` that starts Kafka, Zookeeper, PostgreSQL, Elasticsearch, Neo4j, Redis
- [ ] Verify all containers start cleanly with `docker-compose up -d`
- [ ] Run the Kafka topic creation script from KAFKA.md
- [ ] Send a test message to `raw.reddit` and consume it to confirm Kafka works end to end

**Files:** `docker-compose.yml`, `.env.example`, `scripts/create_topics.sh`

**You are done when:** `docker-compose up -d` starts cleanly and you can send and receive a Kafka test message.

---

## 🔲 Phase 1 — Reddit Scraper
**Goal:** Real Reddit data is flowing into Kafka.
**Estimated time:** 1-2 weeks

- [ ] Get Reddit API credentials at reddit.com/prefs/apps
- [ ] Install: `pip install praw kafka-python redis`
- [ ] Write `backend/scrapers/reddit_scraper.py` following SERVICES.md
- [ ] Implement the exact message schema from KAFKA.md
- [ ] Add Redis bloom filter for deduplication
- [ ] Run the scraper and verify posts appear in Kafka UI at localhost:8080
- [ ] Write `Dockerfile.reddit-scraper`
- [ ] Verify the containerized version still produces messages

**Files:** `backend/scrapers/reddit_scraper.py`, `backend/Dockerfile.reddit-scraper`, `backend/scrapers/requirements.txt`

**You are done when:** Real Reddit posts appear on `raw.reddit` in Kafka UI in real time.

---

## 🔲 Phase 2 — Remaining Scrapers
**Goal:** All 4 remaining data sources flowing into Kafka.
**Estimated time:** 2 weeks

Build in this order — simplest to most complex:

- [ ] `rss_scraper.py` — feedparser, publishes to `raw.news`
- [ ] `news_scraper.py` — NewsAPI, publishes to `raw.news`
- [ ] `gdelt_scraper.py` — GDELT CSV polling every 15 minutes, publishes to `raw.gdelt`
- [ ] `cspan_scraper.py` — C-SPAN API, publishes to `raw.speeches`
- [ ] `run_all.py` — starts all scrapers in parallel using `multiprocessing`
- [ ] Write a Dockerfile for each scraper
- [ ] Test each scraper individually in Kafka UI before moving to the next

**Files:** `backend/scrapers/*.py`, `backend/scrapers/run_all.py`

**You are done when:** All 4 raw Kafka topics are receiving live messages simultaneously.

---

## 🔲 Phase 3 — Flink Processor
**Goal:** Raw messages are cleaned, embedded, and anomalies are detected.
**Estimated time:** 2-3 weeks

This is the most technically complex phase. Take your time and test each step individually before wiring them together.

- [ ] Install: `pip install apache-flink transformers torch sentence-transformers beautifulsoup4`
- [ ] Download HuggingFace models locally (prevents re-downloading on every run):
  - `sentence-transformers/all-MiniLM-L6-v2`
  - `dslim/bert-base-NER`
- [ ] Write and test the cleaning step in isolation — strip HTML, normalize timestamps, truncate
- [ ] Write and test the NER entity extraction step — run it on a sample Reddit post and verify it returns persons/orgs/locations
- [ ] Write and test the embedding step — verify it outputs a list of 384 floats
- [ ] Write and test the anomaly detection step — tumbling window, baseline comparison, spike threshold
- [ ] Wire all steps together in `flink_job.py`
- [ ] Send a test message to `raw.reddit`, verify a processed document appears on `documents.processed` with an embedding
- [ ] Manually publish a fake anomaly payload to `anomalies.detected` and verify the format is correct

**Files:** `backend/processors/flink_job.py`, `backend/Dockerfile.flink-processor`

**You are done when:** A message on `raw.reddit` results in a processed document with a 384-float embedding on `documents.processed`, and spike detection publishes to `anomalies.detected`.

---

## 🔲 Phase 4 — Storage Layer
**Goal:** Processed documents written to all 4 databases.
**Estimated time:** 2 weeks

- [ ] Run the full PostgreSQL DDL from BACKEND.md
- [ ] Run `CREATE EXTENSION vector;` to enable pgvector
- [ ] Create the Elasticsearch index with the mapping from BACKEND.md
- [ ] Run the Neo4j constraints and indexes from BACKEND.md
- [ ] Write `storage_worker.py` that consumes `documents.processed`
- [ ] Implement parallel writes to all 4 databases with `asyncio.gather()`
- [ ] Implement Neo4j edge creation between documents sharing entities within a 24hr window
- [ ] Write a test script `scripts/verify_storage.py` that queries all 4 databases and prints document counts
- [ ] Run the full pipeline for 30 minutes and verify documents appear everywhere

**Files:** `backend/processors/storage_worker.py`, `backend/Dockerfile.storage-worker`, `scripts/setup_databases.sql`, `scripts/verify_storage.py`

**You are done when:** After 30 minutes of running the full pipeline, `verify_storage.py` shows documents in all 4 databases with embeddings, full text, and graph edges present.

---

## 🔲 Phase 5 — The Agent
**Goal:** The agent autonomously investigates anomalies end to end.
**Estimated time:** 2-3 weeks

This is the most exciting phase. The project becomes alive here. Build and test each tool individually before combining them.

- [ ] Get OpenAI API key at platform.openai.com
- [ ] Install: `pip install langchain langchain-openai`
- [ ] Build and test each tool in isolation with a simple test script:
  - [ ] `semantic_search` — verify it returns semantically relevant docs from pgvector
  - [ ] `graph_trace` — verify it returns a spread path from Neo4j
  - [ ] `full_text_search` — verify it returns phrase matches with timestamps from Elasticsearch
  - [ ] `get_source_profile` — verify it returns source metadata from Redis/Postgres
  - [ ] `synthesize_report` — verify GPT-4o returns a coherent markdown report given fake findings
- [ ] Wire all 5 tools into a LangChain ReAct AgentExecutor using the prompts from AGENT.md
- [ ] Publish a fake anomaly to `anomalies.detected` and watch the full reasoning trace in your terminal
- [ ] Verify the completed report appears on `investigations.complete` in the correct schema from KAFKA.md
- [ ] Read the reasoning trace — does the agent make sensible decisions? Tweak the system prompt if not

**Files:** `backend/agent/agent.py`, `backend/agent/tools.py`, `backend/Dockerfile.agent`

**You are done when:** A fake anomaly on Kafka results in a complete GPT-4o synthesized investigation report on `investigations.complete` within 2 minutes.

---

## 🔲 Phase 6 — Backend API
**Goal:** A fully working REST API and WebSocket server.
**Estimated time:** 1-2 weeks

Build endpoints in this order — health first so you can always verify DB connectivity:

- [ ] Set up FastAPI project structure from BACKEND.md
- [ ] Set up database connection dependencies
- [ ] Implement JWT authentication
- [ ] `GET /health` — verifies all 4 database connections are live
- [ ] `GET /investigations` and `GET /investigations/{id}`
- [ ] `GET /narratives/active` and `GET /narratives/trending`
- [ ] `GET /search/phrases` and `GET /search/semantic`
- [ ] `GET /graph/{investigation_id}`
- [ ] Kafka consumer for `investigations.complete` topic
- [ ] WebSocket endpoint with ConnectionManager
- [ ] Redis caching layer for all cacheable endpoints
- [ ] Test every single endpoint in Swagger UI at localhost:8000/docs before moving on

**Files:** `backend/api/main.py`, `backend/api/routers/*.py`, `backend/api/services/*.py`

**You are done when:** Every endpoint in BACKEND.md returns correct data in Swagger UI and the WebSocket pushes a live event when you publish a test message to `investigations.complete`.

---

## 🔲 Phase 7 — Frontend
**Goal:** A working dashboard you can demo to anyone.
**Estimated time:** 2-3 weeks

Always build static/hardcoded first, then wire up real data. Never try to build the component and integrate the API at the same time.

- [ ] Set up React + TypeScript + Vite project
- [ ] Install all dependencies from FRONTEND.md
- [ ] Set up TailwindCSS
- [ ] Layout component — sidebar + header with hardcoded nav
- [ ] Dashboard page with hardcoded placeholder data
- [ ] Wire Dashboard to real API with React Query
- [ ] LiveFeed with hardcoded events, then wire WebSocket
- [ ] TrendingCard and TimelineChart with Recharts
- [ ] Investigation page — ReportViewer, SpreadTimeline, AmplifierList
- [ ] SpreadGraph with Sigma.js — allocate the most time here, it is the hardest component
- [ ] Search page
- [ ] Loading states, error states, empty states for every component
- [ ] Final polish — spacing, colors, responsiveness

**Files:** `frontend/src/**`

**You are done when:** You open the dashboard, see live narratives in the feed, click into a real investigation, and the Sigma.js spread graph renders with real nodes and edges.

---

## 🔲 Phase 8 — Kubernetes Local
**Goal:** The entire system runs on Kubernetes locally with minikube.
**Estimated time:** 1-2 weeks

- [ ] Install minikube: `brew install minikube`
- [ ] Start cluster: `minikube start --memory=8192 --cpus=4`
- [ ] Write K8s manifests for all 10 services following SERVICES.md and INFRASTRUCTURE.md
- [ ] Write ConfigMap for shared non-secret env vars
- [ ] Write Secrets manifest for API keys
- [ ] Deploy: `kubectl apply -f k8s/manifests/ -n rhetoriq`
- [ ] Verify all pods running: `kubectl get pods -n rhetoriq`
- [ ] Test the full pipeline end to end on K8s — does data still flow?
- [ ] Set up HPA for Flink processor
- [ ] Verify liveness and readiness probes are passing on all pods

**Files:** `k8s/manifests/**`

**You are done when:** The entire pipeline runs on minikube and you can reach the frontend at the minikube service URL.

---

## 🔲 Phase 9 — CI/CD
**Goal:** Every push to main automatically tests and deploys.
**Estimated time:** 1 week

- [ ] Write GitHub Actions CI workflow — runs pytest on every PR
- [ ] Write Docker build and push workflow for merges to main
- [ ] Install ArgoCD on minikube following INFRASTRUCTURE.md
- [ ] Create ArgoCD Application pointing at `k8s/manifests/`
- [ ] Push a test commit and verify the full flow: GitHub Actions runs → Docker image built → ArgoCD deploys → no manual commands needed

**Files:** `.github/workflows/ci.yml`, `argocd-app.yaml`

**You are done when:** A git push triggers automatic testing, Docker build, and ArgoCD deployment without you running a single command manually.

---

## 🔲 Phase 10 — Observability
**Goal:** You can see what every part of the system is doing in Grafana.
**Estimated time:** 1 week

- [ ] Install Prometheus + Grafana via Helm following INFRASTRUCTURE.md
- [ ] Add `prometheus_client` metrics to every Python service
- [ ] Build Kafka Health dashboard: consumer lag, messages/sec, broker disk
- [ ] Build Agent dashboard: investigations/hr, duration p95, OpenAI cost/day, failed investigations
- [ ] Build Infrastructure dashboard: pod CPU/memory per node group
- [ ] Set up alert for Kafka consumer lag exceeding 1000 messages

**Files:** `k8s/manifests/monitoring/`, metrics instrumentation in each service

**You are done when:** Grafana shows live metrics for every service, database, and Kafka topic.

---

## 🔲 Phase 11 — AWS Deployment (Optional)
**Goal:** RhetoriQ runs in production on AWS with a live URL.
**Estimated time:** 2-3 weeks

Only do this if you want to go the extra mile. A live URL on your resume is a significant signal.

- [ ] Create AWS account, configure CLI
- [ ] Write Terraform modules from INFRASTRUCTURE.md
- [ ] `terraform apply` — provisions EKS, MSK, RDS, ElastiCache
- [ ] Push Docker images to AWS ECR
- [ ] Deploy to EKS
- [ ] Set up AWS Secrets Manager
- [ ] Configure domain + SSL
- [ ] ArgoCD on EKS for production GitOps

**Files:** `infra/terraform/**`

---

## Full Timeline Estimate

| Phase | Time at 5-10hrs/week |
|---|---|
| Pre-work | 3-4 weeks |
| Phase 0 — Setup | 1 week |
| Phase 1 — Reddit scraper | 1-2 weeks |
| Phase 2 — All scrapers | 2 weeks |
| Phase 3 — Flink processor | 2-3 weeks |
| Phase 4 — Storage layer | 2 weeks |
| Phase 5 — Agent | 2-3 weeks |
| Phase 6 — Backend API | 1-2 weeks |
| Phase 7 — Frontend | 2-3 weeks |
| Phase 8 — Kubernetes | 1-2 weeks |
| Phase 9 — CI/CD | 1 week |
| Phase 10 — Observability | 1 week |
| **Total** | **~5-6 months** |

5-6 months is not a long time for a project of this caliber. Most developers never build something this architecturally complex at all.

---

## Rules

**1. Never skip a phase.** Every phase is a foundation for the next. Kafka must work before Flink. Flink must work before the agent.

**2. Meet the "you are done when" condition before moving on.** It is not optional.

**3. Commit after every completed task.** Format: `feat(phase-2): add GDELT scraper with 15min polling`

**4. Two hour rule.** If you are stuck on one thing for more than 2 hours, ask for help. Stack Overflow, Reddit, or Claude. Burning a full day on one bug is demoralizing and unnecessary.

**5. Keep a devlog.** Create `DEVLOG.md` and write one paragraph after every session — what you built, what broke, what you learned. When an interviewer asks "walk me through building this," your devlog is your cheat sheet. It also proves you actually built it yourself.
