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
    GROQ_MODEL: str = "llama-3.1-8b-instant"

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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
