# RhetoriQ Lakehouse and Databricks Decision

RhetoriQ does not currently use Databricks, Spark, Delta Lake, or MLflow. The MVP uses FastAPI, SQLite, optional Redis, and a React/Vite frontend.

Track B's planned processing path is Kafka and Flink, as documented in [ROADMAP.md](ROADMAP.md). A lakehouse evaluation may be revisited only if real workload, retention, analytics, or model-evaluation needs justify its cost and operational complexity. Until then, Databricks must not appear in startup, deployment, or implemented-architecture documentation.
