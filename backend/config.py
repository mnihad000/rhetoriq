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

    # Investigation runtime
    INVESTIGATION_DB_PATH: str = "investigations.sqlite3"
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
    RESEARCH_EXECUTION_MODE: str = "embedded"
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
    RESEARCH_MAX_DOMAIN_REQUESTS: int = 4
    RESEARCH_MAX_RETRIES: int = 2
    RESEARCH_SEARCH_RESULTS_PER_ACTION: int = 8
    SEARXNG_BASE_URL: str = "http://127.0.0.1:8080"
    BROWSER_SERVICE_URL: str = "http://127.0.0.1:8010"
    BROWSER_SERVICE_TOKEN: str = ""
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
        db_path = Path(self.INVESTIGATION_DB_PATH)
        if self.INVESTIGATION_DB_PATH != ":memory:" and not db_path.is_absolute():
            self.INVESTIGATION_DB_PATH = str(BACKEND_DIR / db_path)
        checkpoint_path = Path(self.RESEARCH_CHECKPOINT_DB_PATH)
        if self.RESEARCH_CHECKPOINT_DB_PATH != ":memory:" and not checkpoint_path.is_absolute():
            self.RESEARCH_CHECKPOINT_DB_PATH = str(BACKEND_DIR / checkpoint_path)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
