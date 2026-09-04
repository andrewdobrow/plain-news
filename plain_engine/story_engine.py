"""
Persistent Story Engine

Builds long-running stories from the event-centric EditorialEngine.

EditorialEngine answers:

    "What event is this article describing?"

StoryEngine answers:

    "Which ongoing story does this event belong to?"
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .editorial_engine import EditorialEngine, EditorialEngineResult
from .models import (
    Story,
    StoryTimelineEvent,
    StoryState,
)


@dataclass(slots=True)
class StoryResult:
    editorial: EditorialEngineResult
    story_id: str
    story_title: str
    created_story: bool
    state: StoryState


class StoryEngine:

    def __init__(
        self,
        registry_path: str | Path = "story-registry.json",
    ):

        self.editor = EditorialEngine()

        self.registry_path = Path(registry_path)

        self.registry = self._load_registry()

    def process(
        self,
        entry: Mapping[str, Any],
        *,
        source: str,
        county: str | None = None,
        is_custom: bool | None = None,
    ) -> StoryResult:

        result = self.editor.process(
            entry,
            source=source,
            county=county,
            is_custom=is_custom,
        )

        story = self._get_or_create_story(
            result.event_key,
            entry,
            result,
        )

        self._append_timeline(
            story,
            entry,
            result,
        )

        story.last_updated = datetime.utcnow().isoformat()

        self._save_registry()

        return StoryResult(
            editorial=result,
            story_id=story.story_id,
            story_title=story.title,
            created_story=len(story.timeline) == 1,
            state=story.state,
        )

    # ------------------------------------------------------------

    def _load_registry(self):

        if not self.registry_path.exists():

            return {
                "stories": {},
                "event_map": {}
            }

        with open(self.registry_path, "r", encoding="utf8") as f:
            return json.load(f)

    def _save_registry(self):

        with open(self.registry_path, "w", encoding="utf8") as f:
            json.dump(
                self.registry,
                f,
                indent=2,
                ensure_ascii=False,
            )

    # ------------------------------------------------------------

    def _get_or_create_story(
        self,
        event_key,
        entry,
        result,
    ):

        event_map = self.registry["event_map"]

        stories = self.registry["stories"]

        if event_key in event_map:

            return Story(**stories[event_map[event_key]])

        story_id = f"story_{len(stories)+1:06d}"

        story = Story(
            story_id=story_id,
            title=entry.get("title","Untitled"),
            slug=story_id,
            state=StoryState.DEVELOPING,
            canonical_article=result.canonical_article_id,
        )

        stories[story_id] = story.__dict__

        event_map[event_key] = story_id

        return story

    # ------------------------------------------------------------

    def _append_timeline(
        self,
        story,
        entry,
        result,
    ):

        timeline = story.timeline

        timeline.append(
            StoryTimelineEvent(
                timestamp=datetime.utcnow().isoformat(),
                headline=entry.get("title",""),
                article_slug=result.article_id,
                source=entry.get("source"),
            )
        )

        if len(timeline) > 1:
            story.state = StoryState.ACTIVE

        self.registry["stories"][story.story_id] = story.__dict__
