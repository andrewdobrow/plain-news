"""Deterministic lifecycle classification for persistent newsroom stories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


class StoryLifecycleState(str, Enum):
    BREAKING = "breaking"
    DEVELOPING = "developing"
    ACTIVE = "active"
    ONGOING = "ongoing"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class StoryLifecycle:
    state: StoryLifecycleState
    reason: str
    last_updated: str | None
    hours_since_update: float | None
    event_count: int
    timeline_entries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "last_updated": self.last_updated,
            "hours_since_update": self.hours_since_update,
            "event_count": self.event_count,
            "timeline_entries": self.timeline_entries,
        }


_RESOLUTION_PHRASES = (
    "all clear",
    "fully contained",
    "100% contained",
    "road reopened",
    "lanes reopened",
    "bridge reopened",
    "evacuation lifted",
    "evacuations lifted",
    "warning lifted",
    "alert canceled",
    "alert cancelled",
    "found safe",
    "located safe",
    "missing person found",
    "search ended",
    "case closed",
)


def classify_story_lifecycle(
    story: Mapping[str, Any],
    *,
    reference_time: datetime | None = None,
) -> StoryLifecycle:
    """Classify a story without changing publication behavior.

    The classifier uses durable registry data only: timeline timestamps, event
    count, relationship history, importance, and explicit resolution language.
    A reference time can be supplied to keep tests and retrospective analysis
    deterministic.
    """

    reference = _as_utc(reference_time or datetime.now(timezone.utc))
    timeline = story.get("timeline") or []
    timeline_entries = len(timeline) if isinstance(timeline, Sequence) and not isinstance(timeline, (str, bytes)) else 0
    events = story.get("events") or []
    event_count = len(events) if isinstance(events, Sequence) and not isinstance(events, (str, bytes)) else 0

    last_updated_dt, last_updated = _latest_timeline_timestamp(timeline)
    age_hours = None
    if last_updated_dt is not None:
        age_hours = max(0.0, (reference - last_updated_dt).total_seconds() / 3600)
        age_hours = round(age_hours, 2)

    text = " ".join(
        str(value)
        for value in [
            story.get("canonical_title", ""),
            *(story.get("titles") or []),
            *(story.get("facts") or []),
        ]
    ).lower()

    if age_hours is not None and age_hours >= 24 * 30:
        state = StoryLifecycleState.ARCHIVED
        reason = "No recorded update for at least 30 days"
    elif any(phrase in text for phrase in _RESOLUTION_PHRASES):
        state = StoryLifecycleState.RESOLVED
        reason = "Explicit resolution language appears in the story record"
    else:
        importance = story.get("importance") or {}
        importance_level = str(importance.get("level") or "").lower()
        if importance_level == "breaking" and (age_hours is None or age_hours <= 12):
            state = StoryLifecycleState.BREAKING
            reason = "Breaking importance with a current or undated update"
        elif _has_follow_up(story) or event_count >= 2 or timeline_entries >= 2:
            if age_hours is None or age_hours <= 72:
                state = StoryLifecycleState.DEVELOPING
                reason = "Multiple events or follow-up decisions show the story is evolving"
            elif age_hours <= 24 * 7:
                state = StoryLifecycleState.ONGOING
                reason = "Established multi-event story has not updated in the past 72 hours"
            else:
                state = StoryLifecycleState.ACTIVE
                reason = "Multi-event story remains open but is no longer rapidly developing"
        elif age_hours is None or age_hours <= 48:
            state = StoryLifecycleState.ACTIVE
            reason = "Single-event story is current"
        elif age_hours <= 24 * 7:
            state = StoryLifecycleState.ONGOING
            reason = "Story is older than 48 hours but remains within the active news window"
        else:
            state = StoryLifecycleState.ACTIVE
            reason = "Story remains open pending a resolution or archival threshold"

    return StoryLifecycle(
        state=state,
        reason=reason,
        last_updated=last_updated,
        hours_since_update=age_hours,
        event_count=event_count,
        timeline_entries=timeline_entries,
    )


def _has_follow_up(story: Mapping[str, Any]) -> bool:
    history = story.get("relationship_history") or []
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes)):
        return False
    return any(
        isinstance(item, Mapping) and str(item.get("relationship") or "") == "follow_up"
        for item in history
    )


def _latest_timeline_timestamp(timeline: object) -> tuple[datetime | None, str | None]:
    if not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)):
        return None, None

    latest: datetime | None = None
    latest_raw: str | None = None
    for entry in timeline:
        if not isinstance(entry, Mapping):
            continue
        raw = str(entry.get("published_at") or "").strip()
        parsed = _parse_datetime(raw)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
            latest_raw = raw
    return latest, latest_raw


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
