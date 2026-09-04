"""Mission-aligned ranking for Plain's U.S.-wide newsroom."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class EditorialProximity:
    score: int
    scope: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "scope": self.scope, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class EditorialScore:
    """Explainable final ranking score for one story."""

    score: int
    importance: int
    proximity_multiplier: float
    freshness_multiplier: float
    source_multiplier: float
    source_trust: int
    age_hours: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "importance": self.importance,
            "proximity_multiplier": self.proximity_multiplier,
            "freshness_multiplier": self.freshness_multiplier,
            "source_multiplier": self.source_multiplier,
            "source_trust": self.source_trust,
            "age_hours": self.age_hours,
        }


_SCOPE_SCORES = {
    "us_national": 100,
    "us_multistate": 95,
    "us_state_regional": 82,
    "global_major": 88,
    "international": 72,
    "unknown": 60,
}


def classify_editorial_proximity(story: Mapping[str, Any]) -> EditorialProximity:
    relevance = story.get("audience_relevance") or {}
    scope = str(relevance.get("scope") or "unknown")
    score = _SCOPE_SCORES.get(scope, 60)

    if int(story.get("custom_article_count", 0) or 0) > 0:
        score = max(score, 100)
        reason = "Plain original coverage receives maximum mission relevance"
    elif scope == "us_national":
        reason = "Nationwide U.S. consequence directly matches Plain's core audience"
    elif scope == "us_multistate":
        reason = "Multi-state U.S. consequence has broad national relevance"
    elif scope == "us_state_regional":
        reason = "State or regional U.S. story remains relevant but has narrower reach"
    elif scope == "global_major":
        reason = "Major world development has broad consequence for a U.S. general-news audience"
    elif scope == "international":
        reason = "International story is eligible with lower U.S.-audience proximity"
    else:
        reason = "Audience scope is uncertain; retain neutral baseline relevance"

    return EditorialProximity(score=score, scope=scope, reason=reason)


def calculate_editorial_priority(importance_score: int, proximity_score: int) -> int:
    """Backward-compatible consequence × mission-proximity score."""
    importance = max(0, min(100, int(importance_score)))
    proximity = max(0, min(100, int(proximity_score)))
    return round(importance * proximity / 100)


def calculate_editorial_score(
    importance_score: int,
    proximity_score: int,
    *,
    source_trust: int = 50,
    published_at: datetime | str | None = None,
    reference_time: datetime | None = None,
) -> EditorialScore:
    """Return a transparent 0-100 editorial ranking score.

    The score preserves importance as the base, then applies mission proximity,
    freshness, and source trust. ``reference_time`` is injectable so tests and
    retrospective analysis remain deterministic.
    """

    importance = max(0, min(100, int(importance_score)))
    proximity = max(0, min(100, int(proximity_score)))
    trust = max(0, min(100, int(source_trust)))

    proximity_multiplier = round(proximity / 100, 4)
    age_hours = _age_hours(published_at, reference_time=reference_time)
    freshness_multiplier = _freshness_multiplier(age_hours)
    source_multiplier = round(0.85 + (trust / 100) * 0.30, 4)

    raw_score = (
        importance
        * proximity_multiplier
        * freshness_multiplier
        * source_multiplier
    )
    score = max(0, min(100, round(raw_score)))

    return EditorialScore(
        score=score,
        importance=importance,
        proximity_multiplier=proximity_multiplier,
        freshness_multiplier=freshness_multiplier,
        source_multiplier=source_multiplier,
        source_trust=trust,
        age_hours=round(age_hours, 2) if age_hours is not None else None,
    )


def story_source_trust(story: Mapping[str, Any]) -> int:
    """Use the strongest known source candidate, with a neutral default."""

    candidates = story.get("title_candidates") or []
    values: list[int] = []
    if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes)):
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            try:
                values.append(max(0, min(100, int(candidate.get("source_trust", 50)))))
            except (TypeError, ValueError):
                continue
    return max(values, default=50)


def latest_story_timestamp(story: Mapping[str, Any]) -> str | None:
    """Return the newest parseable timeline timestamp, if one exists."""

    latest: datetime | None = None
    latest_raw: str | None = None
    timeline = story.get("timeline") or []
    if not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)):
        return None

    for entry in timeline:
        if not isinstance(entry, Mapping):
            continue
        raw = str(entry.get("published_at") or "").strip()
        parsed = _parse_datetime(raw)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
            latest_raw = raw
    return latest_raw


def _age_hours(
    published_at: datetime | str | None,
    *,
    reference_time: datetime | None,
) -> float | None:
    published = _parse_datetime(published_at)
    if published is None:
        return None

    reference = reference_time or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    else:
        reference = reference.astimezone(timezone.utc)

    return max(0.0, (reference - published).total_seconds() / 3600)


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _freshness_multiplier(age_hours: float | None) -> float:
    if age_hours is None:
        return 0.80
    if age_hours <= 6:
        return 1.00
    if age_hours <= 24:
        return 0.95
    if age_hours <= 72:
        return 0.85
    if age_hours <= 168:
        return 0.70
    return 0.55
