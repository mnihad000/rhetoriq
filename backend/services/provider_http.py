from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from typing import Any, Callable

import httpx


@dataclass(frozen=True)
class ProviderRequestDiagnostics:
    provider: str
    outcome: str
    attempts: int
    retry_count: int
    checked_at: str
    http_status: int | None = None
    failure_category: str | None = None
    detail: str | None = None
    rate_limited: bool = False
    retry_after_seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "retry_count": self.retry_count,
            "checked_at": self.checked_at,
            "http_status": self.http_status,
            "failure_category": self.failure_category,
            "detail": self.detail,
            "rate_limited": self.rate_limited,
            "retry_after_seconds": self.retry_after_seconds,
        }


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, diagnostics: ProviderRequestDiagnostics) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class RetryingJsonClient:
    """Small bounded JSON client shared by synchronous research providers."""

    def __init__(
        self,
        provider: str,
        *,
        timeout_seconds: float,
        max_retries: int,
        backoff_seconds: float,
        max_backoff_seconds: float,
        min_interval_seconds: float = 0.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.max_backoff_seconds = max(0.0, max_backoff_seconds)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.transport = transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[dict[str, Any], ProviderRequestDiagnostics]:
        last_status: int | None = None
        last_category = "provider_unavailable"
        last_detail = "Provider request did not complete."
        was_rate_limited = False
        retry_after: float | None = None

        with httpx.Client(
            timeout=self.timeout_seconds,
            headers=headers,
            transport=self.transport,
        ) as client:
            for attempt in range(1, self.max_retries + 2):
                self._wait_for_rate_slot()
                try:
                    response = client.get(url, params=params)
                    last_status = response.status_code
                    retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                    if response.status_code == 429:
                        was_rate_limited = True
                        last_category = "rate_limited"
                        last_detail = "Provider returned HTTP 429."
                        if attempt <= self.max_retries:
                            self._sleep(self._delay(attempt, retry_after))
                            continue
                        break
                    elif response.status_code >= 500:
                        last_category = "provider_server_error"
                        last_detail = f"Provider returned HTTP {response.status_code}."
                        if attempt <= self.max_retries:
                            self._sleep(self._delay(attempt, retry_after))
                            continue
                        break
                    response.raise_for_status()
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        last_category = "malformed_json"
                        last_detail = "Provider returned malformed JSON."
                        if attempt <= self.max_retries:
                            self._sleep(self._delay(attempt, None))
                            continue
                        raise exc
                    if not isinstance(payload, dict):
                        last_category = "malformed_response"
                        last_detail = "Provider response root must be a JSON object."
                        if attempt <= self.max_retries:
                            self._sleep(self._delay(attempt, None))
                            continue
                        raise ValueError(last_detail)
                    return payload, ProviderRequestDiagnostics(
                        provider=self.provider,
                        outcome="success",
                        attempts=attempt,
                        retry_count=attempt - 1,
                        checked_at=_now_iso(),
                        http_status=response.status_code,
                        rate_limited=was_rate_limited,
                        retry_after_seconds=retry_after,
                    )
                except httpx.TimeoutException as exc:
                    last_category = "timeout"
                    last_detail = str(exc) or "Provider request timed out."
                except httpx.HTTPStatusError as exc:
                    last_status = exc.response.status_code
                    last_category = "http_status"
                    last_detail = f"Provider returned HTTP {last_status}."
                except httpx.HTTPError as exc:
                    last_category = "transport_error"
                    last_detail = str(exc) or "Provider transport failed."
                except ValueError as exc:
                    last_detail = str(exc) or last_detail

                if attempt <= self.max_retries and last_category in {
                    "timeout", "transport_error", "malformed_json", "malformed_response"
                }:
                    self._sleep(self._delay(attempt, retry_after))
                    continue
                break

        diagnostics = ProviderRequestDiagnostics(
            provider=self.provider,
            outcome="unavailable",
            attempts=attempt,
            retry_count=max(0, attempt - 1),
            checked_at=_now_iso(),
            http_status=last_status,
            failure_category=last_category,
            detail=last_detail[:300],
            rate_limited=was_rate_limited,
            retry_after_seconds=retry_after,
        )
        raise ProviderRequestError(
            f"{self.provider} unavailable: {last_category}",
            diagnostics,
        )

    def _wait_for_rate_slot(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self.min_interval_seconds - (now - self._last_request_at)
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._monotonic()
            self._last_request_at = now

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        requested = retry_after if retry_after is not None else self.backoff_seconds * (2 ** (attempt - 1))
        return min(self.max_backoff_seconds, max(0.0, requested))


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
