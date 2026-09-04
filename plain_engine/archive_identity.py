"""Stable mapping from live stories to Plain's permanent article archive."""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Any

ARCHIVE_STOPS = {
    "the", "a", "an", "in", "of", "for", "to", "and", "or", "on", "at",
    "is", "was", "are", "were", "that", "this", "with", "from", "have",
    "been", "after", "over", "into", "says", "said", "will", "than", "more",
    "also", "when", "s", "us", "u", "news", "report", "reports", "new",
    "latest", "update", "updates",
}


def signature_tokens(text: str) -> frozenset[str]:
    return frozenset(
        word.lower().strip(".,;:()")
        for word in str(text or "").split()
        if len(word) > 3 and word.lower() not in ARCHIVE_STOPS
    )


def is_duplicate_headline(headline: str, existing_token_sets: Iterable[Iterable[str]]) -> bool:
    new_tokens = signature_tokens(headline)
    if len(new_tokens) < 3:
        return False
    return any(len(new_tokens & frozenset(tokens)) >= 4 for tokens in existing_token_sets)


def find_matching_entry(
    headline: str,
    archive: Iterable[Mapping[str, Any]],
    source_url: str = "",
    story_id: str = "",
):
    """Return the permanent archive entry for a live story, if known.

    Persistent ``story_id`` is authoritative for migrated stories. Exact source
    URL and fuzzy headline matching remain compatibility fallbacks for entries
    created before the registry existed.
    """
    entries = list(archive)
    if story_id:
        for entry in entries:
            if str(entry.get("story_id") or "") == story_id:
                return entry

    if source_url:
        def norm_url(value: str) -> str:
            return re.sub(r"[?#].*$", "", str(value or "").strip().rstrip("/").lower())

        normalized = norm_url(source_url)
        path_part = re.sub(r"^https?://[^/]+", "", normalized)
        if len(path_part) > 10:
            for entry in entries:
                if entry.get("source_url") and norm_url(str(entry["source_url"])) == normalized:
                    return entry

    tokens = signature_tokens(headline)
    if len(tokens) < 3:
        return None
    for entry in entries:
        if len(tokens & signature_tokens(str(entry.get("headline") or ""))) >= 4:
            return entry
    return None
