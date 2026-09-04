"""Deterministic source-article URL identity.

The same publisher article can appear under multiple evolving headlines while
retaining one stable article URL.  Exact article-URL identity is therefore a
strong persistent-story anchor, but feed and search URLs are explicitly
excluded because many unrelated articles share them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

SOURCE_IDENTITY_VERSION = "1.1"

_TRACKING_QUERY_KEYS = frozenset(
    {
        "oc", "output", "utm_campaign", "utm_content", "utm_medium",
        "utm_source", "utm_term", "fbclid", "gclid", "mc_cid", "mc_eid",
    }
)
_FEED_FILE_RE = re.compile(r"\.(?:rss|xml|atom)(?:/)?$", re.IGNORECASE)
_FEED_SEGMENTS = frozenset({"feed", "feeds", "rss"})

_TITLE_WORD_RE = re.compile(r"[a-z0-9]+")
_TITLE_NOISE = frozenset({
    "the", "and", "for", "with", "from", "after", "before", "into", "over",
    "this", "that", "today", "friday", "saturday", "sunday", "monday",
    "tuesday", "wednesday", "thursday", "news", "report", "reports",
    "update", "updates", "live", "com",
})
_ROLLING_SOURCE_PATH_TOKENS = frozenset({
    "weather", "forecast", "radar", "traffic", "live", "livestream",
    "updates", "update", "blog", "alerts", "alert",
})
_ROLLING_TITLE_TOKENS = frozenset({
    "weather", "forecast", "radar", "advisory", "showers", "thunderstorms",
    "storm", "storms", "rain", "temperature", "temperatures", "temps",
    "heat", "hurricane", "tropical", "alert", "alerts", "watch", "warning",
})


def _source_title_tokens(value: object) -> set[str]:
    title = str(value or "").strip()
    # Remove a short trailing publisher attribution. This intentionally remains
    # local to source identity so registry_repair can import this module without
    # creating a circular dependency.
    parts = re.split(r"\s+(?:-|–|—|\|)\s+", title)
    if len(parts) > 1 and len(_TITLE_WORD_RE.findall(parts[-1])) <= 6:
        title = " ".join(parts[:-1])
    return {
        token for token in _TITLE_WORD_RE.findall(title.casefold())
        if (len(token) >= 3 or token.isdigit()) and token not in _TITLE_NOISE
    }


def source_identity_requires_title_continuity(
    value: object,
    *,
    title: object = "",
    existing_titles: Iterable[object] = (),
) -> bool:
    """Return True for article URLs that may represent a rolling content slot.

    A publisher may reuse one weather/traffic/live page while changing the
    real-world event and headline behind it. Google News URLs are treated as
    rolling only when the incoming or persisted title carries a rolling-weather
    signal; ordinary one-off Google News articles retain exact-source identity.
    """

    normalized = normalize_source_identity_url(value)
    if not normalized:
        return False
    parsed = urlsplit(normalized)
    path_tokens = {
        token for segment in (parsed.path or "").casefold().split("/")
        for token in re.split(r"[-_.]+", segment) if token
    }
    if path_tokens & _ROLLING_SOURCE_PATH_TOKENS:
        return True
    if (parsed.hostname or "").casefold() != "news.google.com":
        return False
    title_tokens = set(_TITLE_WORD_RE.findall(str(title or "").casefold()))
    for existing in existing_titles or ():
        title_tokens.update(_TITLE_WORD_RE.findall(str(existing or "").casefold()))
    return bool(title_tokens & _ROLLING_TITLE_TOKENS)


def source_identity_title_compatible(
    incoming_title: object,
    existing_titles: Iterable[object],
) -> bool:
    """Require event-level title continuity for mutable/rolling source URLs."""

    incoming_raw = str(incoming_title or "").strip()
    if not incoming_raw:
        return False
    incoming_norm = " ".join(_TITLE_WORD_RE.findall(incoming_raw.casefold()))
    incoming_tokens = _source_title_tokens(incoming_raw)
    for existing in existing_titles or ():
        existing_raw = str(existing or "").strip()
        if not existing_raw:
            continue
        existing_norm = " ".join(_TITLE_WORD_RE.findall(existing_raw.casefold()))
        if incoming_norm and incoming_norm == existing_norm:
            return True
        existing_tokens = _source_title_tokens(existing_raw)
        if not incoming_tokens or not existing_tokens:
            continue
        shared = incoming_tokens & existing_tokens
        overlap = len(shared) / min(len(incoming_tokens), len(existing_tokens))
        # Two specific shared concepts plus meaningful proportional overlap allow
        # ordinary headline evolution while rejecting a reused rolling page whose
        # subject changed entirely.
        if len(shared) >= 2 and overlap >= 0.35:
            return True
    return False


def story_identity_titles(story: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = [str(v) for v in (story.get("titles", ()) or ()) if str(v).strip()]
    canonical = str(story.get("canonical_title") or "").strip()
    if canonical:
        values.append(canonical)
    values.extend(
        str(candidate.get("title") or "").strip()
        for candidate in (story.get("title_candidates", ()) or ())
        if isinstance(candidate, Mapping) and str(candidate.get("title") or "").strip()
    )
    return tuple(dict.fromkeys(values))


def normalize_source_identity_url(value: object) -> str:
    """Return a stable article identity URL or an empty string for non-articles."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return ""

    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    folded_path = path.casefold()
    segments = [segment for segment in folded_path.split("/") if segment]

    # Google News article URLs are stable article identities. Search/feed URLs
    # are shared containers and must never join stories.
    is_google_article = host == "news.google.com" and folded_path.startswith("/rss/articles/")
    if host == "news.google.com" and not is_google_article:
        return ""

    if not is_google_article:
        if _FEED_FILE_RE.search(folded_path):
            return ""
        if any(segment in _FEED_SEGMENTS for segment in segments):
            return ""
        # A bare homepage/domain is not an article identity.
        if len(segments) < 2:
            return ""

    kept_query = [] if is_google_article else [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(kept_query))
    normalized_path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, normalized_path, query, ""))


def story_source_identity_urls(story: Mapping[str, Any]) -> frozenset[str]:
    """Collect safe article identities from a persisted story record."""

    values: list[object] = list(story.get("source_urls", ()) or ())
    values.extend(story.get("sources", ()) or ())
    values.extend(
        candidate.get("source", "")
        for candidate in story.get("title_candidates", ()) or ()
        if isinstance(candidate, Mapping)
    )
    # Plain stores the actual article URL in unified incident evidence while
    # ``source`` is the publisher name/domain. Treat those persisted article
    # URLs as the authoritative same-source identity anchors.
    values.extend(
        evidence.get("source_url", "")
        for evidence in story.get("unified_incident_evidence", ()) or ()
        if isinstance(evidence, Mapping)
    )
    return frozenset(
        normalized
        for normalized in (normalize_source_identity_url(value) for value in values)
        if normalized
    )


@dataclass(frozen=True, slots=True)
class SourceIdentityMatch:
    matched: bool
    story_id: str | None
    normalized_url: str
    confidence: float
    reason: str
    decision_trace: tuple[str, ...]


def find_matching_source_story(
    source: object,
    stories: Iterable[Mapping[str, Any]],
    *,
    title: object = "",
) -> SourceIdentityMatch:
    """Find a story containing the same safe article identity URL.

    Rolling source URLs require independent title continuity. Exact URL equality
    remains a strong candidate signal, but cannot join materially different events.
    """

    normalized = normalize_source_identity_url(source)
    if not normalized:
        return SourceIdentityMatch(
            False, None, "", 0.0,
            "Source URL is not a safe article identity",
            ("Exact source article identity: false",),
        )

    candidate_stories = [
        story for story in stories
        if normalized in story_source_identity_urls(story)
        and str(story.get("story_id", "")).strip()
    ]
    rolling_candidates = [
        story for story in candidate_stories
        if source_identity_requires_title_continuity(
            normalized,
            title=title,
            existing_titles=story_identity_titles(story),
        )
    ]
    if rolling_candidates and title:
        supported = [
            story for story in candidate_stories
            if story not in rolling_candidates
            or source_identity_title_compatible(title, story_identity_titles(story))
        ]
        if candidate_stories and not supported:
            return SourceIdentityMatch(
                False, None, normalized, 0.0,
                "Rolling source URL changed event identity; title continuity was not established",
                (
                    "Exact source article identity: candidate_only",
                    "Rolling source title continuity: false",
                    f"Normalized source URL: {normalized}",
                ),
            )
        candidate_stories = supported

    matches = sorted({
        str(story.get("story_id", "")).strip()
        for story in candidate_stories
        if str(story.get("story_id", "")).strip()
    })
    if not matches:
        return SourceIdentityMatch(
            False, None, normalized, 0.0,
            "No existing story contains this article URL",
            (
                "Exact source article identity: false",
                f"Normalized source URL: {normalized}",
            ),
        )

    if len(matches) > 1:
        return SourceIdentityMatch(
            False, None, normalized, 0.0,
            "Source URL identity is ambiguous across multiple stories",
            (
                "Exact source article identity: ambiguous",
                f"Normalized source URL: {normalized}",
                f"Candidate story count: {len(matches)}",
            ),
        )

    story_id = matches[0]
    trace = [
        "Exact source article identity: true",
        f"Normalized source URL: {normalized}",
    ]
    matched_story = next(
        (story for story in candidate_stories if str(story.get("story_id", "")).strip() == story_id),
        None,
    )
    if matched_story and source_identity_requires_title_continuity(
        normalized,
        title=title,
        existing_titles=story_identity_titles(matched_story),
    ):
        trace.append("Rolling source title continuity: true")
    trace.append("Confidence: 1.00")
    return SourceIdentityMatch(
        True, story_id, normalized, 1.0,
        "Exact source article URL already belongs to this story",
        tuple(trace),
    )
