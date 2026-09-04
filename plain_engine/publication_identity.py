"""Bridge persistent editorial stories into publication-time canonical identity.

The shadow registry reasons about raw source articles.  The production generator may
rewrite those headlines before creating Plain permalinks, so headline-only archive
matching can lose the registry decision and publish several URLs for one story.
This module builds a conservative source/title index from the persistent registry so
publication can retain that identity without enabling broad semantic enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Iterable

from .registry_repair import normalize_identity_title, is_broad_event_class_key, story_quarantine_reasons
from .source_identity import (
    normalize_source_identity_url,
    source_identity_requires_title_continuity,
    source_identity_title_compatible,
    story_identity_titles,
)

PUBLICATION_IDENTITY_VERSION = "1.1"


@dataclass(frozen=True)
class PublicationIdentityIndex:
    url_to_story: Mapping[str, str]
    title_to_story: Mapping[str, str]
    slug_to_story: Mapping[str, str]
    all_story_ids: frozenset[str]
    safe_story_ids: frozenset[str]
    quarantined_story_ids: frozenset[str]
    canonical_titles: Mapping[str, str]
    story_titles: Mapping[str, tuple[str, ...]]


    def _source_title_supported(
        self, item: Mapping[str, Any], story_id: str, raw_url: object
    ) -> bool:
        title = (
            item.get("source_headline")
            or item.get("headline")
            or item.get("title")
            or ""
        )
        existing_titles = self.story_titles.get(story_id, ())
        if not source_identity_requires_title_continuity(
            raw_url, title=title, existing_titles=existing_titles
        ):
            return True
        return source_identity_title_compatible(title, existing_titles)
    def resolve_source(self, item: Mapping[str, Any] | None) -> str:
        """Resolve only an exact external source-article URL.

        This intentionally excludes title and Plain-slug recovery. It is the safe
        method for limited recent-archive migration and forward publication.
        """
        if not isinstance(item, Mapping):
            return ""
        for key in ("source_url", "original_url", "canonical_source", "link", "url"):
            raw_url = str(item.get(key) or "")
            if not raw_url or "/articles/" in raw_url:
                continue
            normalized = normalize_source_identity_url(raw_url)
            story_id = self.url_to_story.get(normalized, "") if normalized else ""
            if (
                story_id
                and story_id in self.safe_story_ids
                and self._source_title_supported(item, story_id, raw_url)
            ):
                return story_id
        return ""

    def resolve(self, item: Mapping[str, Any] | None) -> str:
        if not isinstance(item, Mapping):
            return ""
        for key in ("source_url", "link", "url"):
            raw_url = str(item.get(key) or "")
            normalized = normalize_source_identity_url(raw_url)
            story_id = self.url_to_story.get(normalized, "") if normalized else ""
            if (
                story_id
                and story_id in self.safe_story_ids
                and self._source_title_supported(item, story_id, raw_url)
            ):
                return story_id
            # Plain permalinks carry the canonical article slug even when the original
            # source URL and rewritten headline no longer match the raw registry row.
            if "/articles/" in raw_url:
                slug = raw_url.split("/articles/", 1)[1].split("?", 1)[0].split("#", 1)[0]
                if slug.endswith(".html"):
                    slug = slug[:-5]
                story_id = self.slug_to_story.get(slug.strip(), "")
                if story_id and story_id in self.safe_story_ids:
                    return story_id

        for key in ("_archived_slug", "slug", "canonical_slug"):
            slug = str(item.get(key) or "").strip()
            story_id = self.slug_to_story.get(slug, "") if slug else ""
            if story_id and story_id in self.safe_story_ids:
                return story_id

        identity_title = normalize_identity_title(
            item.get("source_headline") or item.get("headline") or item.get("title")
        )
        story_id = self.title_to_story.get(identity_title, "") if identity_title else ""
        return story_id if story_id in self.safe_story_ids else ""


def _story_items(payload: Mapping[str, Any] | None) -> Iterable[tuple[str, Mapping[str, Any]]]:
    if not isinstance(payload, Mapping):
        return ()
    stories = payload.get("stories", {})
    if isinstance(stories, Mapping):
        return (
            (str(story_id), story)
            for story_id, story in stories.items()
            if isinstance(story, Mapping)
        )
    if isinstance(stories, list):
        return (
            (str(story.get("story_id") or ""), story)
            for story in stories
            if isinstance(story, Mapping) and story.get("story_id")
        )
    return ()


def _is_safe_duplicate_story(story: Mapping[str, Any]) -> bool:
    """Keep this bridge narrower than general story identity.

    Follow-ups and related-event relationships remain outside publication enforcement.
    Duplicate/source consolidation stories are safe because they represent parallel
    coverage of the same stage, not a new editorial milestone.
    """
    if story_quarantine_reasons(story):
        return False
    if any(is_broad_event_class_key(key) for key in story.get("events", ())):
        return False
    relationships = {
        str(row.get("relationship") or "").strip().lower()
        for row in (story.get("relationship_history") or [])
        if isinstance(row, Mapping)
    }
    if relationships & {"follow_up", "follow-up", "related"}:
        return False
    return True


def build_publication_identity_index(
    payload: Mapping[str, Any] | None,
) -> PublicationIdentityIndex:
    url_candidates: dict[str, set[str]] = {}
    title_candidates: dict[str, set[str]] = {}
    slug_candidates: dict[str, set[str]] = {}
    safe_story_ids: set[str] = set()
    all_story_ids: set[str] = set()
    canonical_titles: dict[str, str] = {}
    story_titles_by_id: dict[str, tuple[str, ...]] = {}
    quarantined_story_ids = frozenset(
        str(story_id)
        for story_id in (payload.get("quarantined_stories", {}) if isinstance(payload, Mapping) else {})
    )

    for story_id, story in _story_items(payload):
        if not story_id:
            continue
        all_story_ids.add(story_id)
        canonical_titles[story_id] = str(story.get("canonical_title") or "")
        story_titles_by_id[story_id] = story_identity_titles(story)
        if _is_safe_duplicate_story(story):
            safe_story_ids.add(story_id)

        titles: list[Any] = list(story.get("titles") or [])
        for candidate in story.get("title_candidates") or []:
            if isinstance(candidate, Mapping):
                titles.append(candidate.get("title"))
        for title in titles:
            normalized = normalize_identity_title(title)
            if normalized:
                title_candidates.setdefault(normalized, set()).add(story_id)

        slugs: list[Any] = [story.get("canonical_slug"), story.get("slug")]
        slugs.extend(story.get("article_slugs") or [])
        for slug in slugs:
            slug = str(slug or "").strip()
            if slug:
                slug_candidates.setdefault(slug, set()).add(story_id)

        urls: list[Any] = []
        urls.extend(story.get("sources") or [])
        for row in story.get("timeline") or []:
            if isinstance(row, Mapping):
                urls.extend((row.get("url"), row.get("source")))
        for candidate in story.get("title_candidates") or []:
            if isinstance(candidate, Mapping):
                urls.append(candidate.get("source"))
        for value in urls:
            normalized = normalize_source_identity_url(value)
            if normalized:
                url_candidates.setdefault(normalized, set()).add(story_id)

    url_to_story = {
        value: next(iter(story_ids))
        for value, story_ids in url_candidates.items()
        if len(story_ids) == 1
    }
    title_to_story = {
        value: next(iter(story_ids))
        for value, story_ids in title_candidates.items()
        if len(story_ids) == 1
    }
    slug_to_story = {
        value: next(iter(story_ids))
        for value, story_ids in slug_candidates.items()
        if len(story_ids) == 1
    }
    return PublicationIdentityIndex(
        url_to_story=url_to_story,
        title_to_story=title_to_story,
        slug_to_story=slug_to_story,
        all_story_ids=frozenset(all_story_ids),
        safe_story_ids=frozenset(safe_story_ids),
        quarantined_story_ids=quarantined_story_ids,
        canonical_titles=canonical_titles,
        story_titles=story_titles_by_id,
    )
