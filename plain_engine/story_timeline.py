"""Typed timeline models for persistent stories."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One durable milestone in a story's chronology."""

    event_key: str
    article_id: str
    published_at: datetime
    title: str
    source: str
    url: str = ""
    editorial_action: str = ""
    canonical_article_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_key.strip():
            raise ValueError("Timeline entry event_key cannot be empty")
        if not self.article_id.strip():
            raise ValueError("Timeline entry article_id cannot be empty")

        object.__setattr__(
            self,
            "published_at",
            _parse_datetime(self.published_at),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "article_id": self.article_id,
            "published_at": self.published_at.isoformat(),
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "editorial_action": self.editorial_action,
            "canonical_article_id": self.canonical_article_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TimelineEntry":
        return cls(
            event_key=str(payload.get("event_key", "")),
            article_id=str(payload.get("article_id", "")),
            published_at=_parse_datetime(payload.get("published_at")),
            title=str(payload.get("title", "")),
            source=str(payload.get("source", "")),
            url=str(payload.get("url", "")),
            editorial_action=str(payload.get("editorial_action", "")),
            canonical_article_id=str(
                payload.get("canonical_article_id", "")
            ),
        )


@dataclass(slots=True)
class StoryTimeline:
    """Chronological, de-duplicated milestones for one persistent story."""

    entries: list[TimelineEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._normalize()

    def _normalize(self) -> None:
        by_event: dict[str, TimelineEntry] = {}
        seen_articles: set[str] = set()

        for entry in sorted(
            self.entries,
            key=lambda item: (item.published_at, item.article_id),
        ):
            if entry.article_id in seen_articles:
                continue
            if entry.event_key in by_event:
                continue
            by_event[entry.event_key] = entry
            seen_articles.add(entry.article_id)

        self.entries = sorted(
            by_event.values(),
            key=lambda item: (item.published_at, item.article_id),
        )

    def add(self, entry: TimelineEntry) -> bool:
        """Add a new event milestone; return False for duplicates."""

        if any(
            existing.article_id == entry.article_id
            or existing.event_key == entry.event_key
            for existing in self.entries
        ):
            return False

        self.entries.append(entry)
        self.entries.sort(
            key=lambda item: (item.published_at, item.article_id)
        )
        return True

    def extend(self, entries: Iterable[TimelineEntry]) -> None:
        for entry in entries:
            self.add(entry)

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    @classmethod
    def from_list(cls, payload: object) -> "StoryTimeline":
        if not isinstance(payload, list):
            return cls()

        entries: list[TimelineEntry] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            try:
                entries.append(TimelineEntry.from_dict(item))
            except (TypeError, ValueError):
                continue
        return cls(entries=entries)
