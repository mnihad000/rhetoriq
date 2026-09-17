"""Pure confidence inputs shared by the legacy ranker and Flink runtime."""
from collections.abc import Mapping


def narrative_confidence(*, source_count: int, publisher_count: int,
                         source_type_count: int, persistence_runs: int,
                         velocity_score: float, provider_mix: Mapping) -> tuple[float, str]:
    score = min(source_count / 12.0, 0.25)
    score += min(publisher_count / 8.0, 0.2)
    score += min(source_type_count / 4.0, 0.15)
    score += min(persistence_runs / 4.0, 0.15)
    score += min(velocity_score / 4.0, 0.15)
    if len(provider_mix) >= 2:
        score += 0.1
    score = round(min(score, 1.0), 2)
    return score, "High" if score >= 0.7 else "Medium" if score >= 0.45 else "Low"
