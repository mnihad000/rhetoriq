from __future__ import annotations

from dataclasses import dataclass

from config import get_settings
from models.research import ResearchActionDecision, ResearchBudgetLimits, ResearchBudgetUsage


class BudgetExceeded(RuntimeError):
    pass


def configured_limits() -> ResearchBudgetLimits:
    settings = get_settings()
    return ResearchBudgetLimits(
        wall_seconds=settings.RESEARCH_MAX_WALL_SECONDS,
        tool_calls=settings.RESEARCH_MAX_TOOL_CALLS,
        model_calls=settings.RESEARCH_MAX_MODEL_CALLS,
        model_tokens=settings.RESEARCH_MAX_MODEL_TOKENS,
        spend_usd=settings.RESEARCH_MAX_SPEND_USD,
        search_results=settings.RESEARCH_MAX_SEARCH_RESULTS,
        canonical_fetches=settings.RESEARCH_MAX_CANONICAL_FETCHES,
        browser_renders=settings.RESEARCH_MAX_BROWSER_RENDERS,
        internal_searches=settings.RESEARCH_MAX_INTERNAL_SEARCHES,
        domain_requests=settings.RESEARCH_MAX_DOMAIN_REQUESTS,
        retries=settings.RESEARCH_MAX_RETRIES,
    )


@dataclass
class ResearchBudget:
    limits: ResearchBudgetLimits
    usage: ResearchBudgetUsage

    def require_action(self, decision: ResearchActionDecision, domain: str | None = None) -> None:
        if decision.action_type == "assess_evidence":
            return
        if self.usage.tool_calls >= self.limits.tool_calls:
            raise BudgetExceeded("tool-call budget exhausted")
        if self.usage.active_seconds >= self.limits.wall_seconds:
            raise BudgetExceeded("wall-clock budget exhausted")
        if (
            decision.action_type in {"web_search", "gdelt_search", "hacker_news_search"}
            and self.usage.search_results >= self.limits.search_results
        ):
            raise BudgetExceeded("accepted-search-result budget exhausted")
        if decision.action_type == "canonical_fetch" and self.usage.canonical_fetches >= self.limits.canonical_fetches:
            raise BudgetExceeded("canonical-fetch budget exhausted")
        if decision.action_type == "browser_fetch" and self.usage.browser_renders >= self.limits.browser_renders:
            raise BudgetExceeded("browser-render budget exhausted")
        if decision.action_type == "internal_search" and self.usage.internal_searches >= self.limits.internal_searches:
            raise BudgetExceeded("internal-search budget exhausted")
        if domain and self.usage.domain_requests.get(domain, 0) >= self.limits.domain_requests:
            raise BudgetExceeded(f"per-domain budget exhausted for {domain}")

    def charge_action(self, decision: ResearchActionDecision, *, domain: str | None = None, results: int = 0) -> None:
        if decision.action_type == "assess_evidence":
            return
        self.usage.tool_calls += 1
        if decision.action_type == "canonical_fetch":
            self.usage.canonical_fetches += 1
        elif decision.action_type == "browser_fetch":
            self.usage.browser_renders += 1
        elif decision.action_type == "internal_search":
            self.usage.internal_searches += 1
        if decision.action_type in {"web_search", "gdelt_search", "hacker_news_search"}:
            accepted = min(results, max(0, self.limits.search_results - self.usage.search_results))
            self.usage.search_results += accepted
        if domain:
            self.usage.domain_requests[domain] = self.usage.domain_requests.get(domain, 0) + 1

    def reserve_model_call(
        self,
        estimated_input_tokens: int,
        max_output_tokens: int,
        provider: str,
        model: str | None = None,
    ) -> float:
        if self.usage.model_calls >= self.limits.model_calls:
            raise BudgetExceeded("model-call budget exhausted")
        reserved_tokens = estimated_input_tokens + max_output_tokens
        if self.usage.model_tokens + reserved_tokens > self.limits.model_tokens:
            raise BudgetExceeded("model-token budget exhausted")
        settings = get_settings()
        known_models = {
            "gemini": {"gemini-2.5-flash"},
            "groq": {"openai/gpt-oss-20b"},
        }
        if (
            provider in known_models
            and model not in known_models[provider]
            and not settings.RESEARCH_ALLOW_CUSTOM_MODEL_PRICING
        ):
            raise BudgetExceeded(
                f"unknown hosted model {model!r}; configure explicit pricing and enable custom model pricing"
            )
        if provider == "gemini":
            cost = estimated_input_tokens / 1_000_000 * settings.GEMINI_INPUT_COST_PER_MTOK
            cost += max_output_tokens / 1_000_000 * settings.GEMINI_OUTPUT_COST_PER_MTOK
        elif provider == "groq":
            cost = estimated_input_tokens / 1_000_000 * settings.GROQ_INPUT_COST_PER_MTOK
            cost += max_output_tokens / 1_000_000 * settings.GROQ_OUTPUT_COST_PER_MTOK
        else:
            cost = 0.0
        if self.usage.spend_usd + cost > self.limits.spend_usd:
            raise BudgetExceeded("model-spend budget exhausted")
        self.usage.model_calls += 1
        self.usage.model_tokens += reserved_tokens
        self.usage.spend_usd = round(self.usage.spend_usd + cost, 6)
        return cost
