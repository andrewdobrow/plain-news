from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(slots=True)
class EventIdentity:
    """
    Canonical description of whether two articles cover the same real-world
    event.
    """

    same_event: bool = False
    confidence: float = 0.0
    event_key: Optional[str] = None
    reason: str = ""
    shared_entities: list[str] = field(default_factory=list)
    shared_locations: list[str] = field(default_factory=list)
    stage_transition: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ArticleIdentityInput:
    """
    Minimal normalized article representation used by the resolver.

    The resolver intentionally accepts a small, predictable schema rather than
    depending directly on the full production article object.
    """

    title: str = ""
    body: str = ""
    location: str = ""
    county: str = ""
    entities: list[str] = field(default_factory=list)
    event_type: str = ""
    event_key: Optional[str] = None
    source_priority: int = 0
    is_custom: bool = False


def _normalize_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^\w\s-]", " ", value)
    value = re.sub(r"[_-]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _normalize_collection(values: list[str]) -> set[str]:
    return {
        normalized
        for value in values
        if (normalized := _normalize_text(value))
    }


def _coerce_article(
    article: ArticleIdentityInput | Mapping[str, Any],
) -> ArticleIdentityInput:
    if isinstance(article, ArticleIdentityInput):
        return article

    return ArticleIdentityInput(
        title=str(article.get("title") or ""),
        body=str(
            article.get("body")
            or article.get("content")
            or article.get("summary")
            or ""
        ),
        location=str(article.get("location") or ""),
        county=str(article.get("county") or ""),
        entities=list(article.get("entities") or []),
        event_type=str(article.get("event_type") or ""),
        event_key=article.get("event_key"),
        source_priority=int(article.get("source_priority") or 0),
        is_custom=bool(article.get("is_custom") or False),
    )


def _shared_values(left: list[str], right: list[str]) -> list[str]:
    left_normalized = _normalize_collection(left)
    right_normalized = _normalize_collection(right)
    return sorted(left_normalized & right_normalized)


def _word_tokens(value: str) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "the",
        "to",
        "with",
    }

    return {
        token
        for token in _normalize_text(value).split()
        if len(token) >= 3 and token not in stop_words
    }


def _title_similarity(left: str, right: str) -> float:
    left_tokens = _word_tokens(left)
    right_tokens = _word_tokens(right)

    if not left_tokens or not right_tokens:
        return 0.0

    intersection = left_tokens & right_tokens
    union = left_tokens | right_tokens

    return len(intersection) / len(union)


def _canonical_event_key(
    left: ArticleIdentityInput,
    right: ArticleIdentityInput,
) -> Optional[str]:
    if left.event_key and right.event_key:
        if _normalize_text(left.event_key) == _normalize_text(right.event_key):
            return left.event_key

    if left.event_key:
        return left.event_key

    if right.event_key:
        return right.event_key

    return None


def resolve_event_identity(
    left_article: ArticleIdentityInput | Mapping[str, Any],
    right_article: ArticleIdentityInput | Mapping[str, Any],
) -> EventIdentity:
    """
    Determine whether two article records describe the same real-world event.

    This first version uses deterministic evidence only:

    - matching canonical event keys
    - shared named entities
    - matching locations
    - matching counties
    - matching event types
    - title token similarity

    It deliberately favors false negatives over dangerous false positives.
    """

    left = _coerce_article(left_article)
    right = _coerce_article(right_article)

    left_key = _normalize_text(left.event_key or "")
    right_key = _normalize_text(right.event_key or "")

    if left_key and right_key and left_key == right_key:
        return EventIdentity(
            same_event=True,
            confidence=100.0,
            event_key=left.event_key or right.event_key,
            reason="Both articles use the same canonical event key.",
            metadata={
                "matched_by": "event_key",
                "custom_article_present": left.is_custom or right.is_custom,
            },
        )

    shared_entities = _shared_values(left.entities, right.entities)

    left_location = _normalize_text(left.location)
    right_location = _normalize_text(right.location)
    shared_locations: list[str] = []

    location_match = bool(
        left_location
        and right_location
        and left_location == right_location
    )

    if location_match:
        shared_locations.append(left_location)

    county_match = bool(
        _normalize_text(left.county)
        and _normalize_text(left.county) == _normalize_text(right.county)
    )

    event_type_match = bool(
        _normalize_text(left.event_type)
        and _normalize_text(left.event_type)
        == _normalize_text(right.event_type)
    )

    title_similarity = _title_similarity(left.title, right.title)

    score = 0.0
    evidence: list[str] = []

    if shared_entities:
        entity_score = min(50.0, 25.0 * len(shared_entities))
        score += entity_score
        evidence.append(f"{len(shared_entities)} shared named entity or entities")

    if location_match:
        score += 25.0
        evidence.append("same normalized location")

    if county_match:
        score += 10.0
        evidence.append("same county")

    if event_type_match:
        score += 20.0
        evidence.append("same event type")

    if title_similarity >= 0.60:
        score += 25.0
        evidence.append("strong title similarity")
    elif title_similarity >= 0.35:
        score += 12.0
        evidence.append("moderate title similarity")

    score = min(score, 99.0)

    hard_identity_signal = bool(
        shared_entities
        and (location_match or event_type_match)
    )

    strong_contextual_signal = bool(
        location_match
        and event_type_match
        and title_similarity >= 0.35
    )

    same_event = bool(
        score >= 70.0
        and (hard_identity_signal or strong_contextual_signal)
    )

    if same_event:
        reason = "Same event supported by " + ", ".join(evidence) + "."
    elif evidence:
        reason = (
            "Insufficient evidence to declare the same event; observed "
            + ", ".join(evidence)
            + "."
        )
    else:
        reason = "No meaningful identity evidence was shared."

    return EventIdentity(
        same_event=same_event,
        confidence=round(score, 2),
        event_key=_canonical_event_key(left, right) if same_event else None,
        reason=reason,
        shared_entities=shared_entities,
        shared_locations=shared_locations,
        metadata={
            "matched_by": "deterministic_evidence",
            "title_similarity": round(title_similarity, 4),
            "county_match": county_match,
            "event_type_match": event_type_match,
            "location_match": location_match,
            "custom_article_present": left.is_custom or right.is_custom,
        },
    )