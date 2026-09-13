"""
Query Planner Agent implementation for the first investigation stage.

This module owns only planning. It does not retrieve documents or generate
reports. The planner returns a typed InvestigationPlan and falls back to a
deterministic local plan when model output is unavailable or invalid.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from agents.json_utils import validate_no_forbidden_language
from agents.model_client import BaseModelClient, CachedModelClient, MockModelClient, build_model_client
from agents.prompt_loader import load_prompt
from config import get_settings
from models.investigation import InvestigationPlan, InvestigationPlanTimeWindow

logger = logging.getLogger(__name__)

_REPAIR_SYSTEM = (
    "You are a JSON repair assistant. The previous response did not match the required schema. "
    "Return ONLY a valid JSON object with no markdown, no explanation, and no preamble. "
    "All required keys must be present. Use only the original context provided."
)

_DEFAULT_OUTPUTS = ["timeline", "counter_narratives", "source_diversity"]
_DEFAULT_SOURCE_TYPES = [
    "forum",
    "blog",
    "local_news",
    "national_news",
    "commentary",
    "speech_transcript",
]
_INTENT_OUTPUTS = {
    "origin": ["timeline", "counter_narratives", "source_diversity"],
    "spread": ["timeline", "counter_narratives", "source_diversity"],
    "counter-narrative": ["timeline", "counter_narratives", "source_diversity"],
    "source-ecosystem": ["source_diversity", "timeline"],
    "general investigation": ["timeline", "counter_narratives", "source_diversity"],
}
_SOURCE_TYPE_HINTS: list[tuple[tuple[str, ...], list[str]]] = [
    (("speech", "transcript", "remarks", "hearing", "debate"), ["speech_transcript"]),
    (("official", "government", "agency", "press release"), ["official_statement"]),
    (("community", "forum", "reddit", "thread"), ["forum"]),
    (("blog", "newsletter", "substack"), ["blog"]),
    (("local", "city", "county", "state"), ["local_news"]),
    (("national", "mainstream", "media"), ["national_news"]),
]
_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019"
_PHRASE_SUFFIXES = (" come from",)
_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "any",
    "are",
    "around",
    "as",
    "at",
    "be",
    "did",
    "does",
    "everyone",
    "find",
    "for",
    "forming",
    "from",
    "general",
    "how",
    "i",
    "in",
    "into",
    "is",
    "issue",
    "it",
    "me",
    "mode",
    "narrative",
    "narratives",
    "of",
    "on",
    "or",
    "story",
    "suddenly",
    "that",
    "the",
    "this",
    "through",
    "to",
    "trace",
    "what",
    "when",
    "where",
    "why",
}


def plan_investigation(
    query_text: str,
    prior_context: dict[str, Any] | None = None,
    model_client: BaseModelClient | None = None,
) -> InvestigationPlan:
    """
    Return a typed investigation plan.

    In demo mode, or when a model call fails validation, the planner falls back
    to a deterministic seeded plan derived directly from the query.
    """
    query_text = query_text.strip()
    baseline = _build_baseline_plan(query_text, prior_context)

    if model_client is None:
        settings = get_settings()
        if settings.DEMO_MODE:
            return baseline
        model_client = build_model_client("gemini")

    if isinstance(model_client, (MockModelClient, CachedModelClient)):
        return baseline

    system_prompt = load_prompt("planner")
    user_prompt = _build_user_prompt(query_text, prior_context, baseline)

    try:
        return _call_with_retry(model_client, system_prompt, user_prompt, baseline)
    except Exception as exc:
        logger.warning("Planner failed and fell back to baseline: %s", exc)
        return baseline


def _build_user_prompt(
    query_text: str,
    prior_context: dict[str, Any] | None,
    baseline: InvestigationPlan,
) -> str:
    payload = {
        "query_text": query_text,
        "prior_context": prior_context or {},
        "baseline_plan": baseline.model_dump(mode="json"),
    }
    return (
        "Create an InvestigationPlan JSON object for this user query.\n"
        "Use the baseline only as a hint if it helps, but you may improve it.\n\n"
        + json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    )


def _call_with_retry(
    model_client: BaseModelClient,
    system_prompt: str,
    user_prompt: str,
    baseline: InvestigationPlan,
) -> InvestigationPlan:
    def _validate(raw: dict[str, Any]) -> InvestigationPlan:
        validate_no_forbidden_language(raw)
        plan = InvestigationPlan.model_validate(raw)
        return _normalize_plan(plan, baseline)

    try:
        return _validate(model_client.generate_json(system_prompt, user_prompt, "planner"))
    except (ValidationError, ValueError, RuntimeError, Exception) as exc:
        logger.warning("Planner LLM primary attempt failed: %s", exc)

    repair_user = (
        "Your previous response was invalid for the InvestigationPlan schema. "
        f"Original context:\n{user_prompt[:3000]}"
    )
    try:
        return _validate(model_client.generate_json(_REPAIR_SYSTEM, repair_user, "planner"))
    except (ValidationError, ValueError, RuntimeError, Exception) as exc:
        logger.warning("Planner LLM repair attempt failed: %s", exc)
        return baseline


def _normalize_plan(plan: InvestigationPlan, baseline: InvestigationPlan) -> InvestigationPlan:
    data = plan.model_dump()
    data["query_text"] = baseline.query_text

    if not data.get("requested_outputs"):
        data["requested_outputs"] = list(baseline.requested_outputs)
    if not data.get("target_source_types"):
        data["target_source_types"] = list(baseline.target_source_types)
    if not data.get("risk_notes"):
        data["risk_notes"] = list(baseline.risk_notes)
    if not data.get("uncertainty_requirements"):
        data["uncertainty_requirements"] = list(baseline.uncertainty_requirements)
    if not data.get("search_queries"):
        data["search_queries"] = list(baseline.search_queries)
    if not data.get("semantic_queries"):
        data["semantic_queries"] = list(baseline.semantic_queries)

    return InvestigationPlan.model_validate(data)


def _build_baseline_plan(
    query_text: str,
    prior_context: dict[str, Any] | None = None,
) -> InvestigationPlan:
    canonical_phrase = _extract_canonical_phrase(query_text)
    intent = _classify_intent(query_text)
    entities = _extract_entities(query_text, canonical_phrase)
    time_window = _derive_time_window(query_text)
    retrieval_mode = _derive_retrieval_mode(query_text, prior_context)
    topic = _derive_topic(query_text, canonical_phrase, entities)
    target_source_types = _derive_target_source_types(query_text, intent)
    requested_outputs = _derive_requested_outputs(intent, query_text)

    search_queries = _build_search_queries(query_text, canonical_phrase, entities)
    semantic_queries = _build_semantic_queries(topic, canonical_phrase, intent)

    return InvestigationPlan(
        query_text=query_text,
        topic=topic,
        primary_question=query_text,
        canonical_phrase=canonical_phrase,
        intent=intent,
        entities=entities,
        subquestions=_build_subquestions(query_text, canonical_phrase, intent),
        rival_hypotheses=_build_rival_hypotheses(canonical_phrase or topic, intent),
        disconfirming_evidence_criteria=_build_disconfirming_criteria(intent),
        must_have_source_classes=list(target_source_types),
        retrieval_lanes=_derive_retrieval_lanes(intent),
        search_queries=search_queries,
        semantic_queries=semantic_queries,
        target_source_types=target_source_types,
        requested_outputs=requested_outputs,
        time_window=time_window,
        retrieval_mode=retrieval_mode,
        risk_notes=[
            "Use 'first observed in our dataset' instead of claiming a definitive origin.",
            "Do not infer coordination or intent from timing alone.",
            "Return an evidence-seeking plan, not a user-facing answer.",
        ],
        uncertainty_requirements=[
            "State clearly when earlier sources may exist outside the dataset.",
            "Preserve uncertainty when the query is broad or underspecified.",
        ],
    )


def _extract_canonical_phrase(query_text: str) -> str | None:
    quoted = re.findall(
        rf"[{re.escape(_QUOTE_CHARS)}]([^{re.escape(_QUOTE_CHARS)}]{{3,120}})[{re.escape(_QUOTE_CHARS)}]",
        query_text,
    )
    if quoted:
        return _normalize_phrase_candidate(quoted[0])

    lowered = query_text.lower()
    phrase_patterns = [
        r"where did the ([a-z0-9][a-z0-9\s\-'’]{3,80}?) (?:narrative|claim|phrase) come from",
        r"trace the ([a-z0-9][a-z0-9\s\-'’]{3,80}?) (?:narrative|story|claim)",
        r"trace the (?:narrative|story|claim)\s+(?:behind|around|of|about)\s+([a-z0-9][a-z0-9\s\-'’]{3,80})",
        r"why is everyone suddenly talking about ([a-z0-9][a-z0-9\s\-'’]{3,80})",
    ]
    for pattern in phrase_patterns:
        match = re.search(pattern, lowered)
        if not match:
            continue
        candidate = _normalize_phrase_candidate(match.group(1))
        if candidate:
            return candidate

    match = re.search(
        r"(?:phrase|narrative|claim)\s+(?:behind|around|of|about)?\s*(?:the\s+)?([a-z0-9][a-z0-9\s\-'’]{3,80})",
        lowered,
    )
    if match:
        return _normalize_phrase_candidate(match.group(1))

    return None


def _classify_intent(query_text: str) -> str:
    lowered = query_text.lower()
    if any(
        term in lowered
        for term in ("counter-narrative", "counter narrative", "opposing frame", "competing frame")
    ):
        return "counter-narrative"
    if any(term in lowered for term in ("source diversity", "source ecosystem", "what kinds of sources")):
        return "source-ecosystem"
    if any(
        term in lowered
        for term in ("where did", "come from", "origin", "first appeared", "first appear", "trace the")
    ):
        return "origin"
    if any(
        term in lowered
        for term in ("spread", "evolve", "amplify", "moved from", "everyone talking", "talking about")
    ):
        return "spread"
    return "general investigation"


def _extract_entities(query_text: str, canonical_phrase: str | None) -> list[str]:
    cleaned = query_text
    if canonical_phrase:
        cleaned = re.sub(re.escape(canonical_phrase), " ", cleaned, flags=re.IGNORECASE)

    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", cleaned)
    entities: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in _STOPWORDS:
            continue
        if lowered not in entities:
            entities.append(lowered)

    if canonical_phrase:
        phrase_words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", canonical_phrase)]
        for word in phrase_words:
            if word not in entities and word not in _STOPWORDS:
                entities.append(word)

    return entities[:8]


def _derive_time_window(query_text: str) -> InvestigationPlanTimeWindow:
    lowered = query_text.lower()
    if "today" in lowered:
        return InvestigationPlanTimeWindow(label="today")
    if "this week" in lowered or "this month" in lowered:
        return InvestigationPlanTimeWindow(
            label="this_week" if "this week" in lowered else "this_month"
        )
    if any(term in lowered for term in ("recent", "recently", "suddenly", "right now")):
        return InvestigationPlanTimeWindow(label="recent")
    return InvestigationPlanTimeWindow(label="all_time")


def _derive_retrieval_mode(query_text: str, prior_context: dict[str, Any] | None) -> str:
    lowered = query_text.lower()
    if prior_context and any(
        prior_context.get(key)
        for key in ("cluster_id", "investigation_id", "report_id", "canonical_phrase")
    ):
        return "narrow"
    if any(
        term in lowered
        for term in ("this narrative", "this claim", "same story", "follow up", "update on")
    ) and prior_context:
        return "narrow"
    return "broad"


def _derive_topic(query_text: str, canonical_phrase: str | None, entities: list[str]) -> str:
    if canonical_phrase:
        return canonical_phrase
    if entities:
        return " ".join(entities[:3])
    return re.sub(r"\s+", " ", query_text.strip().lower())


def _derive_target_source_types(query_text: str, intent: str) -> list[str]:
    lowered = query_text.lower()
    source_types = list(_DEFAULT_SOURCE_TYPES)

    if intent in {"origin", "spread", "counter-narrative", "source-ecosystem"}:
        source_types.extend(["official_statement", "community_post"])

    for terms, hinted_types in _SOURCE_TYPE_HINTS:
        if any(term in lowered for term in terms):
            source_types.extend(hinted_types)

    return _dedupe_preserve_order(source_types)


def _derive_requested_outputs(intent: str, query_text: str) -> list[str]:
    lowered = query_text.lower()
    outputs = list(_INTENT_OUTPUTS.get(intent, _DEFAULT_OUTPUTS))

    if any(term in lowered for term in ("report", "summary", "brief", "investigation")):
        outputs.append("report")
    if any(term in lowered for term in ("receipt", "receipts", "evidence", "citation", "citations")):
        outputs.append("receipts")
    if any(term in lowered for term in ("graph", "network", "map")):
        outputs.append("graph")
    if any(term in lowered for term in ("family", "related narratives", "branch")):
        outputs.append("narrative_family")

    return _dedupe_preserve_order(outputs)


def _build_search_queries(query_text: str, canonical_phrase: str | None, entities: list[str]) -> list[str]:
    queries: list[str] = []
    if canonical_phrase:
        queries.append(f"\"{canonical_phrase}\"")
    queries.append(query_text.strip())
    if canonical_phrase and entities:
        queries.append(f"\"{canonical_phrase}\" {' '.join(entities[:3])}")
    elif entities:
        queries.append(" ".join(entities[:4]))
    return _dedupe_preserve_order(queries)


def _build_semantic_queries(topic: str, canonical_phrase: str | None, intent: str) -> list[str]:
    queries = [
        f"Investigate the narrative around {topic}",
        f"Find evidence about origin and spread of {canonical_phrase or topic}",
    ]
    if intent == "counter-narrative":
        queries.append(f"Find competing frames or counter-narratives about {canonical_phrase or topic}")
    elif intent == "source-ecosystem":
        queries.append(f"Find source ecosystem and source diversity around {canonical_phrase or topic}")
    else:
        queries.append(f"Find counter-narratives and source diversity around {canonical_phrase or topic}")
    return _dedupe_preserve_order(queries)


def _build_subquestions(query_text: str, canonical_phrase: str | None, intent: str) -> list[str]:
    subject = canonical_phrase or query_text
    questions = [
        f"What is the strongest evidence directly about {subject}?",
        f"What competing or contradictory framing exists around {subject}?",
    ]
    if intent in {"origin", "spread"}:
        questions.append(f"What is the earliest anchored appearance of {subject} in the retrieved corpus?")
    if intent == "source-ecosystem":
        questions.append(f"What source classes are amplifying or independently covering {subject}?")
    return _dedupe_preserve_order(questions)[:5]


def _build_rival_hypotheses(subject: str, intent: str) -> list[dict[str, str]]:
    if intent == "origin":
        hypotheses = [
            {"id": "origin_local", "hypothesis": f"{subject} first emerged in niche or local commentary.", "rationale": "Origin questions often begin in niche channels before pickup."},
            {"id": "origin_official", "hypothesis": f"{subject} traces back to an official or institutional anchor.", "rationale": "Some narratives are downstream reactions to official material."},
        ]
    elif intent == "counter-narrative":
        hypotheses = [
            {"id": "counter_direct", "hypothesis": f"A direct rebuttal to {subject} exists in the corpus.", "rationale": "Counter-frame questions require a same-claim opposition check."},
            {"id": "counter_adjacent", "hypothesis": f"Only adjacent context exists rather than a direct rebuttal to {subject}.", "rationale": "Nearby coverage can be mistaken for a true counter-frame."},
        ]
    else:
        hypotheses = [
            {"id": "spread_independent", "hypothesis": f"{subject} spread through multiple relatively independent sources.", "rationale": "Independent pickup is materially different from repetition of one source chain."},
            {"id": "spread_syndicated", "hypothesis": f"{subject} appears broad but mainly derives from one upstream source chain.", "rationale": "Duplicate or syndicated coverage can mimic consensus."},
        ]
    return hypotheses


def _build_disconfirming_criteria(intent: str) -> list[str]:
    criteria = [
        "If apparent plurality is mostly duplicate or syndicated copy, downgrade confidence.",
        "If counter-evidence was not meaningfully searched, do not treat the packet as complete.",
    ]
    if intent == "origin":
        criteria.append("If earlier dated sources are missing, describe findings as earliest in retrieved corpus rather than a definitive origin.")
    if intent == "counter-narrative":
        criteria.append("If the counter-frame does not address the same claim, do not count it as a true counter-narrative.")
    return criteria


def _derive_retrieval_lanes(intent: str) -> list[str]:
    lanes = ["discovery", "corroboration", "contradiction"]
    if intent in {"origin", "spread"}:
        lanes.append("provenance")
    if intent in {"origin", "spread", "source-ecosystem"}:
        lanes.append("official")
    if intent in {"spread", "source-ecosystem", "general investigation"}:
        lanes.append("community")
    return _dedupe_preserve_order(lanes)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _normalize_phrase_candidate(value: str) -> str | None:
    candidate = re.sub(r"\s+", " ", value).strip(" .?!,:;")
    lowered = candidate.lower()
    for suffix in _PHRASE_SUFFIXES:
        if lowered.endswith(suffix):
            candidate = candidate[: -len(suffix)].strip(" .?!,:;")
            lowered = candidate.lower()
    words = candidate.split(" ")
    while words and words[0].lower() in _STOPWORDS:
        words.pop(0)
    candidate = " ".join(words)
    lowered = candidate.lower()
    if not candidate or lowered in {"this", "that", "it"}:
        return None
    return candidate
