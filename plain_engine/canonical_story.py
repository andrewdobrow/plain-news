"""Canonical story selection for Plain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True, slots=True)
class StoryCandidate:
    """One article associated with a recognized real-world event."""

    article_id: str
    event_key: str
    title: str
    source: str
    url: str = ""
    is_custom: bool = False
    published_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if not self.article_id.strip():
            raise ValueError("article_id cannot be empty")

        if not self.event_key.strip():
            raise ValueError("event_key cannot be empty")

        if not self.title.strip():
            raise ValueError("title cannot be empty")

        if not self.source.strip():
            raise ValueError("source cannot be empty")


@dataclass(frozen=True, slots=True)
class CanonicalStory:
    """The selected lead article and its supporting source articles."""

    event_key: str
    canonical: StoryCandidate
    supporting: tuple[StoryCandidate, ...] = ()


def _candidate_rank(candidate: StoryCandidate) -> tuple[int, float, str]:
    """
    Return a deterministic ranking value.

    Priority:
    1. Plain custom stories
    2. Earlier publication time
    3. Stable article ID tie-breaker
    """

    custom_priority = 1 if candidate.is_custom else 0

    return (
        custom_priority,
        -candidate.published_at.timestamp(),
        candidate.article_id,
    )


def select_canonical_story(
    candidates: Iterable[StoryCandidate],
) -> CanonicalStory:
    """
    Select the canonical article for one event.

    All candidates must share the same event key.
    """

    candidate_list = list(candidates)

    if not candidate_list:
        raise ValueError("At least one story candidate is required")

    event_keys = {candidate.event_key for candidate in candidate_list}

    if len(event_keys) != 1:
        raise ValueError(
            "All story candidates must belong to the same event"
        )

    canonical = max(candidate_list, key=_candidate_rank)

    supporting = tuple(
        sorted(
            (
                candidate
                for candidate in candidate_list
                if candidate.article_id != canonical.article_id
            ),
            key=lambda candidate: (
                candidate.published_at,
                candidate.article_id,
            ),
        )
    )

    return CanonicalStory(
        event_key=canonical.event_key,
        canonical=canonical,
        supporting=supporting,
    )


class CanonicalStoryManager:
    """Maintains canonical selections across multiple events."""

    def __init__(self) -> None:
        self._candidates: dict[str, dict[str, StoryCandidate]] = {}

    def add(self, candidate: StoryCandidate) -> CanonicalStory:
        """Add or replace a candidate and return the event's current state."""

        event_candidates = self._candidates.setdefault(
            candidate.event_key,
            {},
        )

        event_candidates[candidate.article_id] = candidate

        return select_canonical_story(event_candidates.values())

    def get(self, event_key: str) -> CanonicalStory | None:
        """Return the current canonical story for an event."""

        event_candidates = self._candidates.get(event_key)

        if not event_candidates:
            return None

        return select_canonical_story(event_candidates.values())

    def all(self) -> tuple[CanonicalStory, ...]:
        """Return all canonical events in deterministic order."""

        return tuple(
            self.get(event_key)
            for event_key in sorted(self._candidates)
            if self.get(event_key) is not None
        )