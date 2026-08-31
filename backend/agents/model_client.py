"""
Model adapter layer for the investigation planner.

All clients implement BaseModelClient.generate_json(system_prompt, user_prompt, schema_name) -> dict.

Priority order when building a client for production:
  GeminiModelClient  (primary — fast, structured output support)
  GroqModelClient    (backup — openai/gpt-oss-20b)
  OllamaModelClient  (local fallback — requires Ollama running locally)
  CachedModelClient  (last resort — returns pre-generated fixture JSON)
  MockModelClient    (deterministic — for tests and demo mode)
"""

import json
import logging
import os
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

_CREDIT_ERROR_MARKERS = (
    "billing",
    "credit",
    "credits",
    "exceeded your current quota",
    "insufficient_quota",
    "payment required",
    "quota",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "usage limit",
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseModelClient:
    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock — deterministic, no network calls, safe for tests
# ---------------------------------------------------------------------------

_MOCK_OUTPUTS: dict[str, dict] = {
    "planner": {
        "query_text": "Where did the 'hidden energy tax' narrative come from?",
        "topic": "hidden energy tax",
        "canonical_phrase": "hidden energy tax",
        "intent": "origin",
        "entities": ["hidden", "energy", "tax"],
        "search_queries": [
            "\"hidden energy tax\"",
            "Where did the 'hidden energy tax' narrative come from?",
            "\"hidden energy tax\" hidden energy tax",
        ],
        "semantic_queries": [
            "Investigate the narrative around hidden energy tax",
            "Find evidence about origin and spread of hidden energy tax",
            "Find counter-narratives and source diversity around hidden energy tax",
        ],
        "target_source_types": [
            "forum",
            "blog",
            "local_news",
            "national_news",
            "commentary",
            "speech_transcript",
        ],
        "requested_outputs": ["timeline", "counter_narratives", "source_diversity"],
        "time_window": {"start": None, "end": None, "label": "all_time"},
        "retrieval_mode": "broad",
        "risk_notes": [
            "Use 'first observed in our dataset' instead of claiming a definitive origin.",
            "Do not infer coordination or intent from timing alone.",
            "Return an evidence-seeking plan, not a user-facing answer.",
        ],
        "uncertainty_requirements": [
            "State clearly when earlier sources may exist outside the dataset.",
            "Preserve uncertainty when the query is broad or underspecified.",
        ],
    },
}


class MockModelClient(BaseModelClient):
    """
    Returns deterministic, schema-valid dicts keyed by schema_name.
    Used in DEMO_MODE and as the last-resort fallback in LLM functions.
    Never makes network calls.
    """

    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        if schema_name not in _MOCK_OUTPUTS:
            raise ValueError(f"MockModelClient has no output for schema_name={schema_name!r}")
        return dict(_MOCK_OUTPUTS[schema_name])


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

class GeminiModelClient(BaseModelClient):
    """
    Calls Google Gemini API (google-genai SDK).
    Requires GEMINI_API_KEY. Model defaults to GEMINI_MODEL from backend settings.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.GEMINI_API_KEY
        self._model = model or settings.GEMINI_MODEL

        if not self._api_key:
            raise RuntimeError(
                "GeminiModelClient requires GEMINI_API_KEY. "
                "Set it in your .env file or environment."
            )

    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        try:
            from google import genai  # type: ignore[import]
            from google.genai import types as genai_types  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from exc

        try:
            client = genai.Client(api_key=self._api_key)
            response = client.models.generate_content(
                model=self._model,
                contents=user_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            from agents.json_utils import safe_json_loads
            return safe_json_loads(response.text)
        except Exception as exc:
            _log_model_call_error(
                provider="gemini",
                model=self._model,
                schema_name=schema_name,
                exc=exc,
            )
            raise


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

class GroqModelClient(BaseModelClient):
    """
    Calls Groq API with JSON mode enabled.
    Requires GROQ_API_KEY. Model defaults to GROQ_MODEL from backend settings.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.GROQ_API_KEY
        self._model = model or settings.GROQ_MODEL

        if not self._api_key:
            raise RuntimeError(
                "GroqModelClient requires GROQ_API_KEY. "
                "Set it in your .env file or environment."
            )

    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        try:
            from groq import Groq  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "groq is not installed. Run: pip install groq"
            ) from exc

        try:
            client = Groq(api_key=self._api_key)
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=4096,
            )
            from agents.json_utils import safe_json_loads
            return safe_json_loads(response.choices[0].message.content or "{}")
        except Exception as exc:
            _log_model_call_error(
                provider="groq",
                model=self._model,
                schema_name=schema_name,
                exc=exc,
            )
            raise


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

class OllamaModelClient(BaseModelClient):
    """
    Calls a local Ollama instance via its HTTP API.
    Base URL defaults to 'http://localhost:11434'.
    Model defaults to 'llama3.1:8b'.
    No API key required.
    """

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        self._base_url = (base_url or "http://localhost:11434").rstrip("/")
        self._model = model or "llama3.1:8b"

    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        import httpx

        full_prompt = (
            f"{system_prompt}\n\n"
            "Return ONLY a valid JSON object. No markdown. No explanation.\n\n"
            f"{user_prompt}"
        )
        response = httpx.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": full_prompt, "stream": False, "format": "json"},
            timeout=120,
        )
        response.raise_for_status()
        text = response.json().get("response", "")
        from agents.json_utils import safe_json_loads
        return safe_json_loads(text)


# ---------------------------------------------------------------------------
# Cached — loads pre-generated fixture JSON
# ---------------------------------------------------------------------------

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


class CachedModelClient(BaseModelClient):
    """
    Loads pre-generated JSON from agents/fixtures/{schema_name}.json.
    Used only when explicitly requested in tests or local debugging.
    Falls back to MockModelClient if a fixture file does not exist.
    """

    def __init__(self) -> None:
        self._mock = MockModelClient()

    def generate_json(self, system_prompt: str, user_prompt: str, schema_name: str) -> dict:
        path = os.path.join(_FIXTURES_DIR, f"{schema_name}.json")
        if not os.path.exists(path):
            return self._mock.generate_json(system_prompt, user_prompt, schema_name)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_model_client(prefer: str = "gemini") -> BaseModelClient:
    """
    Build the best available model client in priority order.

    prefer = "gemini" | "groq" | "ollama" | "mock"
    Falls through to the next client if the preferred one is unavailable.
    Always returns a working client (MockModelClient at minimum).
    """
    chain: list[str] = _build_chain(prefer)

    inner: BaseModelClient = MockModelClient()
    for name in chain:
        try:
            if name == "gemini":
                inner = GeminiModelClient()
                break
            elif name == "groq":
                inner = GroqModelClient()
                break
            elif name == "ollama":
                client = OllamaModelClient()
                import httpx
                httpx.get(f"{client._base_url}/api/tags", timeout=3).raise_for_status()
                inner = client
                break
        except Exception:
            continue

    return inner


def _build_chain(prefer: str) -> list[str]:
    order = ["gemini", "groq", "ollama", "mock"]
    if prefer in order:
        order = [prefer] + [x for x in order if x != prefer]
    return order


def _log_model_call_error(
    *,
    provider: str,
    model: str,
    schema_name: str,
    exc: Exception,
) -> None:
    status_code = _extract_status_code(exc)
    if _is_credit_or_quota_error(exc):
        logger.error(
            "Model provider quota/credit failure: provider=%s model=%s agent_schema=%s status=%s error=%s",
            provider,
            model,
            schema_name,
            status_code or "unknown",
            _safe_error_message(exc),
        )
        return

    logger.warning(
        "Model provider call failed: provider=%s model=%s agent_schema=%s status=%s error=%s",
        provider,
        model,
        schema_name,
        status_code or "unknown",
        _safe_error_message(exc),
    )


def _is_credit_or_quota_error(exc: Exception) -> bool:
    status_code = _extract_status_code(exc)
    if status_code in {402, 429}:
        return True

    text = _safe_error_message(exc).lower()
    return any(marker in text for marker in _CREDIT_ERROR_MARKERS)


def _extract_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _safe_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    return message.replace("\n", " ")[:500]
