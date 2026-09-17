"""Keep the module-level API TestClients independent across tests."""

import sys
import os
import tempfile
from pathlib import Path

import pytest

from config import Settings

# Apply isolation before test collection imports module-level API repositories.
# Tests may explicitly pass a temporary env file, or opt into the separate
# POSTGRES_TEST_DATABASE_URL integration target; developer secrets stay unused.
Settings.model_config["env_file"] = None
os.environ["DATABASE_URL"] = ""
os.environ["DEPLOYMENT_ENV"] = "development"
_test_state = Path(tempfile.mkdtemp(prefix="rhetoriq-tests-"))
os.environ["INVESTIGATION_DB_PATH"] = str(_test_state / "investigations.sqlite3")
os.environ["RESEARCH_CHECKPOINT_DB_PATH"] = str(_test_state / "checkpoints.sqlite3")
os.environ["KAFKA_PROCESSED_WAIT_SECONDS"] = "0"
os.environ["REDIS_URL"] = ""
for _key in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[_key] = ""


@pytest.fixture(autouse=True)
def reset_application_request_limits():
    # Importing main here would initialize settings before some tests set DEMO_MODE.
    main_module = sys.modules.get("main")
    limiter = getattr(main_module, "request_limiter", None)
    if limiter is not None:
        limiter.clear()
    yield
    if limiter is not None:
        limiter.clear()
