from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    DEMO_MODE: bool = True

    # Real integration keys — unused in demo mode, swapped in later
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # Redis configuration
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # Redis features
    ENABLE_VECTOR_SEARCH: bool = True
    ENABLE_INVESTIGATION_CACHE: bool = True
    CACHE_TTL_SECONDS: int = 3600

    # Embedding configuration
    EMBEDDING_MODEL: str = ""
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384
    BATCH_EMBED_SIZE: int = 32
    EMBEDDING_LOCAL_ONLY: bool = False
    EMBEDDING_CACHE_TTL_SECONDS: int = 86400

    # Neon/PostgreSQL semantic retrieval. Kept opt-in until the corpus has
    # been migrated and backfilled; the legacy internal corpus remains the
    # default path.
    ENABLE_POSTGRES_VECTOR_SEARCH: bool = False
    POSTGRES_VECTOR_SEARCH_TOP_K: int = 8
    POSTGRES_VECTOR_BACKFILL_BATCH_SIZE: int = 100

    # Investigation runtime
    DEPLOYMENT_ENV: str = "development"
    DATABASE_URL: str = ""
    INVESTIGATION_DB_PATH: str = "investigations.sqlite3"
    CORS_ALLOW_ORIGINS: str = ""
    # The first production deployment has one API instance, so an in-process
    # limiter is sufficient. A multi-instance deployment must replace it with
    # a shared limiter before scaling out.
    REQUEST_RATE_LIMIT_PER_MINUTE: int = 120
    INVESTIGATION_START_LIMIT_PER_HOUR: int = 5
    REQUEST_RATE_LIMIT_MAX_CLIENTS: int = 10_000
    TRUST_PROXY_HEADERS: bool = False
    RETRIEVER_MAX_ROUNDS: int = 3
    RETRIEVER_MAX_RESULTS_PER_QUERY: int = 5
    RESEARCH_LOOP_MAX_PASSES: int = 2
    RESEARCH_RUNTIME: str = "auto"
    RESEARCH_PLAN_VERSION: str = "a2-v1"
    # A3 is enabled explicitly for new runs; existing persisted reports are never re-written.
    CLAIM_VERIFIER_ENABLED: bool = False
    CLAIM_VERIFIER_VERSION: str = "a3-v1"
    CLAIM_VERIFIER_NLI_MODEL: str = "cross-encoder/nli-deberta-v3-base"
    CLAIM_VERIFIER_LOCAL_ONLY: bool = False
    CLAIM_VERIFIER_ALLOW_HOSTED_JUDGE: bool = True
    CLAIM_VERIFIER_SUPPORT_THRESHOLD: float = 0.58
    CLAIM_VERIFIER_AMBIGUOUS_LOW: float = 0.46
    CLAIM_VERIFIER_MAX_SPANS_PER_DOCUMENT: int = 3
    # Retained as a deployment label for older configuration files. Execution
    # is Kafka-only; no embedded or synchronous runtime is selected from this value.
    RESEARCH_EXECUTION_MODE: str = "kafka"
    RESEARCH_CHECKPOINT_DB_PATH: str = "langgraph.sqlite3"
    RESEARCH_WORKER_CONCURRENCY: int = 2
    RESEARCH_LEASE_RENEW_SECONDS: int = 15
    RESEARCH_LEASE_SECONDS: int = 45
    RESEARCH_MAX_WALL_SECONDS: int = 300
    RESEARCH_MAX_TOOL_CALLS: int = 24
    RESEARCH_MAX_MODEL_CALLS: int = 12
    RESEARCH_MAX_MODEL_TOKENS: int = 60_000
    RESEARCH_MAX_SPEND_USD: float = 0.50
    RESEARCH_MAX_SEARCH_RESULTS: int = 60
    RESEARCH_MAX_CANONICAL_FETCHES: int = 20
    RESEARCH_MAX_BROWSER_RENDERS: int = 3
    RESEARCH_MAX_INTERNAL_SEARCHES: int = 4
    RESEARCH_MAX_PRIMARY_SOURCE_QUERIES: int = 4
    RESEARCH_MAX_DOMAIN_REQUESTS: int = 4
    RESEARCH_MAX_RETRIES: int = 2
    RESEARCH_SEARCH_RESULTS_PER_ACTION: int = 8
    SEARXNG_BASE_URL: str = "http://127.0.0.1:8080"
    SEARCH_PROVIDER_TIMEOUT_SECONDS: float = 12.0
    SEARCH_PROVIDER_MAX_RETRIES: int = 2
    SEARXNG_MIN_INTERVAL_SECONDS: float = 0.0
    PROVIDER_RETRY_BACKOFF_SECONDS: float = 0.5
    PROVIDER_MAX_BACKOFF_SECONDS: float = 5.0
    FEDERAL_REGISTER_BASE_URL: str = "https://www.federalregister.gov/api/v1"
    FEDERAL_REGISTER_TIMEOUT_SECONDS: float = 15.0
    FEDERAL_REGISTER_MAX_RETRIES: int = 2
    FEDERAL_REGISTER_PAGE_SIZE: int = 20
    FEDERAL_REGISTER_MAX_PAGES: int = 2
    FEDERAL_REGISTER_MIN_INTERVAL_SECONDS: float = 0.25
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_SCHEMA_REGISTRY_URL: str = "http://localhost:8081/apis/ccompat/v7"
    KAFKA_CLIENT_ID: str = "rhetoriq-api"
    KAFKA_CONSUMER_GROUP_PREFIX: str = "rhetoriq"
    KAFKA_TOPIC_PREFIX: str = ""
    KAFKA_SECURITY_PROTOCOL: str = "PLAINTEXT"
    KAFKA_SASL_MECHANISM: str = "PLAIN"
    KAFKA_SASL_USERNAME: str = ""
    KAFKA_SASL_PASSWORD: str = ""
    KAFKA_RETRY_MAX_ATTEMPTS: int = 3
    KAFKA_RETRY_BACKOFF_SECONDS: float = 0.5
    KAFKA_REQUEST_TIMEOUT_SECONDS: float = 10.0
    KAFKA_OUTBOX_BATCH_SIZE: int = 100
    KAFKA_OUTBOX_POLL_SECONDS: float = 0.25
    # Two transactional Flink branches become visible at checkpoint commit,
    # with a bounded hosted enrichment step between them.
    KAFKA_PROCESSED_WAIT_SECONDS: float = 180.0
    KAFKA_WORKER_ROLE: str = "all"
    ENABLE_FLINK_TRENDING: bool = False
    FLINK_REST_URL: str = "http://localhost:8082"
    FLINK_HEARTBEAT_MAX_AGE_SECONDS: int = 1800
    FLINK_AUTO_INVESTIGATE: bool = False
    BROWSER_SERVICE_URL: str = "http://127.0.0.1:8010"
    BROWSER_SERVICE_TOKEN: str = ""
    BROWSER_RENDERING_ENABLED: bool = False
    FETCH_MAX_RESPONSE_BYTES: int = 2_000_000
    FETCH_MAX_REDIRECTS: int = 5
    DOCUMENT_PARSER_VERSION: str = "a2-visible-text-v1"
    GEMINI_INPUT_COST_PER_MTOK: float = 0.30
    GEMINI_OUTPUT_COST_PER_MTOK: float = 2.50
    GROQ_INPUT_COST_PER_MTOK: float = 0.075
    GROQ_OUTPUT_COST_PER_MTOK: float = 0.30
    RESEARCH_ALLOW_CUSTOM_MODEL_PRICING: bool = False
    FETCH_TIMEOUT_SECONDS: int = 20
    TRENDING_REFRESH_HOURS: int = 6
    TRENDING_RESEED_HOURS: int = 24
    TRENDING_MIN_DOCS: int = 4
    TRENDING_MIN_PUBLISHERS: int = 3
    TRENDING_MIN_SOURCE_TYPES: int = 2
    TRENDING_MAX_TOPICS: int = 6

    # GDELT — no key required, free public API
    GDELT_MAX_RECORDS: int = 50
    GDELT_BASE_URL: str = "https://api.gdeltproject.org/api/v2/doc/doc"

    # Hacker News (Algolia) — no key required, free public API
    HN_SEARCH_URL: str = "https://hn.algolia.com/api/v1/search"
    HN_DEFAULT_RESULTS: int = 50

    SPIKE_WINDOW_DAYS: int = 6
    MUTATION_SIMILARITY_LOW: float = 0.40
    MUTATION_SIMILARITY_HIGH: float = 0.85
    ENTITY_OVERLAP_WINDOW_HOURS: int = 72

    @model_validator(mode="after")
    def resolve_repo_relative_paths(self) -> "Settings":
        if self.DEPLOYMENT_ENV.lower() == "production" and not self.DATABASE_URL:
            raise ValueError("DATABASE_URL is required when DEPLOYMENT_ENV=production")
        if self.REQUEST_RATE_LIMIT_PER_MINUTE < 0:
            raise ValueError("REQUEST_RATE_LIMIT_PER_MINUTE cannot be negative")
        if self.INVESTIGATION_START_LIMIT_PER_HOUR < 0:
            raise ValueError("INVESTIGATION_START_LIMIT_PER_HOUR cannot be negative")
        if self.REQUEST_RATE_LIMIT_MAX_CLIENTS < 1:
            raise ValueError("REQUEST_RATE_LIMIT_MAX_CLIENTS must be at least 1")
        if self.SEARCH_PROVIDER_MAX_RETRIES < 0 or self.FEDERAL_REGISTER_MAX_RETRIES < 0:
            raise ValueError("provider retry counts cannot be negative")
        if self.FEDERAL_REGISTER_PAGE_SIZE < 1 or self.FEDERAL_REGISTER_MAX_PAGES < 1:
            raise ValueError("Federal Register pagination limits must be positive")
        if self.PROVIDER_RETRY_BACKOFF_SECONDS < 0 or self.PROVIDER_MAX_BACKOFF_SECONDS < 0:
            raise ValueError("provider backoff values cannot be negative")
        if self.KAFKA_RETRY_MAX_ATTEMPTS < 1:
            raise ValueError("KAFKA_RETRY_MAX_ATTEMPTS must be at least 1")
        if self.KAFKA_OUTBOX_BATCH_SIZE < 1:
            raise ValueError("KAFKA_OUTBOX_BATCH_SIZE must be at least 1")
        if not 1 <= self.FLINK_HEARTBEAT_MAX_AGE_SECONDS <= 1800:
            raise ValueError("FLINK_HEARTBEAT_MAX_AGE_SECONDS must be between 1 and 1800")
        if self.KAFKA_PROCESSED_WAIT_SECONDS < 0:
            raise ValueError("KAFKA_PROCESSED_WAIT_SECONDS cannot be negative")
        if self.DATABASE_URL:
            return self
        db_path = Path(self.INVESTIGATION_DB_PATH)
        if self.INVESTIGATION_DB_PATH != ":memory:" and not db_path.is_absolute():
            self.INVESTIGATION_DB_PATH = str(BACKEND_DIR / db_path)
        checkpoint_path = Path(self.RESEARCH_CHECKPOINT_DB_PATH)
        if self.RESEARCH_CHECKPOINT_DB_PATH != ":memory:" and not checkpoint_path.is_absolute():
            self.RESEARCH_CHECKPOINT_DB_PATH = str(BACKEND_DIR / checkpoint_path)
        return self

    @property
    def persistence_target(self) -> str:
        return self.DATABASE_URL or self.INVESTIGATION_DB_PATH

    @property
    def cors_allow_origins(self) -> list[str]:
        return [item.strip() for item in self.CORS_ALLOW_ORIGINS.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
