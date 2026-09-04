from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional


class StoryState(str, Enum):
    DEVELOPING = "developing"
    ACTIVE = "active"
    WATCHING = "watching"
    RESOLVED = "resolved"
    ARCHIVED = "archived"


class EditorialDecision(str, Enum):
    NEW_STORY = "new_story"
    NEW_STAGE = "new_stage"
    MAJOR_UPDATE = "major_update"
    MINOR_UPDATE = "minor_update"
    SUPPRESS = "suppress"
    NO_ACTION = "no_action"


@dataclass
class StoryStage:

    id: str

    name: str

    first_seen: str

    last_seen: str

    canonical_article: Optional[str] = None

    importance: int = 0


@dataclass
class StoryTimelineEvent:

    timestamp: str

    headline: str

    article_slug: str

    stage: Optional[str] = None

    source: Optional[str] = None


@dataclass
class Story:

    story_id: str

    title: str

    slug: str

    state: StoryState = StoryState.ACTIVE

    importance: int = 0

    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    canonical_article: Optional[str] = None

    canonical_stage: Optional[str] = None

    next_expected_event: Optional[str] = None

    watch_until: Optional[str] = None

    revision: int = 1

    aliases: List[str] = field(default_factory=list)

    related_story_ids: List[str] = field(default_factory=list)

    article_slugs: List[str] = field(default_factory=list)

    timeline: List[StoryTimelineEvent] = field(default_factory=list)

    stages: List[StoryStage] = field(default_factory=list)

    confidence_history: List[float] = field(default_factory=list)

    metadata: Dict = field(default_factory=dict)
