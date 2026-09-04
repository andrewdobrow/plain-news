"""Deterministic integrity checks for persistent-story timelines.

The persistent story registry may legitimately contain several articles about one
continuing event.  This module identifies only high-confidence contamination:
separate timeline clusters with incompatible event families, no exact source
identity, and almost no headline continuity.

The detector is intentionally narrower than retrospective follow-up diagnostics.
It is used as a fail-closed integrity boundary and as a conservative repair input;
it does not infer new follow-up relationships or merge stories.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from .source_identity import normalize_source_identity_url

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "become", "before",
        "by", "could", "for", "from", "in", "including", "into", "is",
        "local", "man", "more", "new", "news", "of", "on", "out", "over",
        "said", "says", "that", "the", "than", "this", "to", "was", "were",
        "with", "woman",
    }
)


def _stem(token: str) -> str:
    token = token.casefold()
    for suffix in ("ies", "ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            if suffix == "ies":
                return token[:-3] + "y"
            return token[: -len(suffix)]
    return token


def timeline_title_tokens(value: object) -> set[str]:
    return {
        _stem(token)
        for token in _WORD_RE.findall(str(value or "").casefold())
        if len(token) >= 2 and token not in _STOPWORDS
    }


def timeline_title_overlap(left: object, right: object) -> tuple[float, set[str]]:
    left_tokens = timeline_title_tokens(left)
    right_tokens = timeline_title_tokens(right)
    shared = left_tokens & right_tokens
    overlap = (
        len(shared) / min(len(left_tokens), len(right_tokens))
        if left_tokens and right_tokens
        else 0.0
    )
    return overlap, shared


def _normalized_title(value: object) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").casefold()))


def timeline_entry_source_identity(entry: Mapping[str, Any]) -> str:
    for field in ("url", "source"):
        normalized = normalize_source_identity_url(entry.get(field))
        if normalized:
            return normalized
    return ""


def infer_timeline_event_family(entry: Mapping[str, Any]) -> str:
    """Infer a broad event family from the headline before trusting event_key.

    Legacy event keys are often the source of contamination.  Phrase-level title
    evidence therefore takes precedence, while the key is only a fallback.
    """

    text = " ".join(_WORD_RE.findall(str(entry.get("title") or "").casefold()))
    patterns: tuple[tuple[str, str], ...] = (
        ("road_rage", r"\broad rage\b|\b(?:pit|police) maneuver\b|\b(?:run|running|chased|forced) .{0,45} off (?:the )?road\b"),
        ("wildfire_arson", r"\bwildfire\b|\bbrush fire\b.{0,45}\b(?:arson|set|setting|charged)\b|\b(?:arson|set|setting)\b.{0,45}\b(?:wildfire|brush fire)\b"),
        ("execution", r"\b(?:execution|executions|executes|executed|death row)\b|\bputs? .{0,30} to death\b"),
        ("hazing", r"\bhazing\b"),
        ("government_finance", r"\b(?:property tax|tax reform|millage|budget|job cuts?|police positions?)\b"),
        ("dui", r"\b(?:dui|driving under the influence)\b"),
        ("animal_cruelty", r"\b(?:cat|cats|animal|animals|dog|dogs|pet|pets)\b.{0,55}\b(?:abuse|abused|abusing|cruelty|kick|kicked|kicking|beat|beaten|harmed|hurt)\b|\b(?:abuse|abused|abusing|cruelty|kick|kicked|kicking|beat|beaten|harmed|hurt)\b.{0,55}\b(?:cat|cats|animal|animals|dog|dogs|pet|pets)\b"),
        ("animal_rescue", r"\b(?:cat|cats|hamster|animal|animals|dog|dogs|pet|pets)\b.{0,45}\b(?:rescue|rescued|saved)\b|\b(?:rescue|rescued|saved)\b.{0,45}\b(?:cat|cats|hamster|animal|animals|dog|dogs|pet|pets)\b"),
        ("traffic_crash", r"\b(?:crash|collision|wreck|semi truck|vehicle)\b"),
        ("shooting", r"\b(?:shooting|shot|gunfire)\b"),
        ("death", r"\b(?:killed|dead|dies|died|death|murder)\b"),
        ("fire", r"\b(?:fire|blaze)\b"),
    )
    for family, pattern in patterns:
        if re.search(pattern, text):
            return family

    event_key = str(entry.get("event_key") or "").casefold()
    if event_key.startswith("animal-rescue-"):
        return "animal_rescue"
    if event_key.startswith("traffic-crash-"):
        return "traffic_crash"
    if event_key.startswith("named-person-death:"):
        return "death"
    if event_key.startswith("fire-"):
        return "fire"
    return "unknown"


def timeline_entries_have_continuity(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    left_article = str(left.get("article_id") or "").strip()
    right_article = str(right.get("article_id") or "").strip()
    if left_article and left_article == right_article:
        return True

    left_event = str(left.get("event_key") or "").strip()
    right_event = str(right.get("event_key") or "").strip()
    if left_event and left_event == right_event:
        return True

    left_source = timeline_entry_source_identity(left)
    right_source = timeline_entry_source_identity(right)
    if left_source and left_source == right_source:
        return True

    left_title = _normalized_title(left.get("title"))
    right_title = _normalized_title(right.get("title"))
    if left_title and left_title == right_title:
        return True

    overlap, shared = timeline_title_overlap(left.get("title"), right.get("title"))
    return bool((len(shared) >= 2 and overlap >= 0.30) or overlap >= 0.50)


def timeline_entries_are_hard_conflict(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    left_family = infer_timeline_event_family(left)
    right_family = infer_timeline_event_family(right)
    if "unknown" in {left_family, right_family} or left_family == right_family:
        return False

    left_source = timeline_entry_source_identity(left)
    right_source = timeline_entry_source_identity(right)
    if left_source and left_source == right_source:
        return False

    overlap, shared = timeline_title_overlap(left.get("title"), right.get("title"))
    return overlap < 0.20 and len(shared) < 2


def _timeline_components(
    entries: Sequence[Mapping[str, Any]],
) -> list[list[dict[str, Any]]]:
    parent = list(range(len(entries)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for left_index in range(len(entries)):
        for right_index in range(left_index + 1, len(entries)):
            if timeline_entries_have_continuity(
                entries[left_index], entries[right_index]
            ):
                union(left_index, right_index)

    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, entry in enumerate(entries):
        grouped.setdefault(find(index), []).append(dict(entry))
    return list(grouped.values())


@dataclass(frozen=True, slots=True)
class TimelineCoherenceAnalysis:
    story_id: str
    coherent: bool
    components: tuple[tuple[dict[str, Any], ...], ...]
    conflict_pairs: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "coherent": self.coherent,
            "component_count": len(self.components),
            "components": [
                {
                    "entry_count": len(component),
                    "article_ids": [
                        str(entry.get("article_id") or "") for entry in component
                    ],
                    "event_keys": [
                        str(entry.get("event_key") or "") for entry in component
                    ],
                    "titles": [str(entry.get("title") or "") for entry in component],
                    "families": sorted(
                        {infer_timeline_event_family(entry) for entry in component}
                    ),
                }
                for component in self.components
            ],
            "conflict_pairs": [dict(pair) for pair in self.conflict_pairs],
            "reason_codes": list(self.reason_codes),
        }


def analyze_story_timeline_coherence(
    story: Mapping[str, Any], *, story_id: str = ""
) -> TimelineCoherenceAnalysis:
    entries = [
        dict(entry)
        for entry in (story.get("timeline", ()) or ())
        if isinstance(entry, Mapping) and str(entry.get("title") or "").strip()
    ]
    resolved_story_id = str(story_id or story.get("story_id") or "").strip()
    if len(entries) < 2:
        return TimelineCoherenceAnalysis(
            resolved_story_id, True, (tuple(entries),) if entries else (), (), ()
        )

    components = _timeline_components(entries)
    conflict_pairs: list[dict[str, Any]] = []
    for left_index in range(len(components)):
        for right_index in range(left_index + 1, len(components)):
            hard_pair = next(
                (
                    (left, right)
                    for left in components[left_index]
                    for right in components[right_index]
                    if timeline_entries_are_hard_conflict(left, right)
                ),
                None,
            )
            if hard_pair is None:
                continue
            left, right = hard_pair
            overlap, shared = timeline_title_overlap(
                left.get("title"), right.get("title")
            )
            conflict_pairs.append(
                {
                    "left_component": left_index,
                    "right_component": right_index,
                    "left_title": str(left.get("title") or ""),
                    "right_title": str(right.get("title") or ""),
                    "left_family": infer_timeline_event_family(left),
                    "right_family": infer_timeline_event_family(right),
                    "title_overlap": round(overlap, 6),
                    "shared_title_tokens": sorted(shared),
                    "reason": "incompatible_event_families_without_identity_continuity",
                }
            )

    coherent = not conflict_pairs
    return TimelineCoherenceAnalysis(
        resolved_story_id,
        coherent,
        tuple(tuple(component) for component in components),
        tuple(conflict_pairs),
        ()
        if coherent
        else (
            "timeline_component_split",
            "event_family_conflict",
            "identity_continuity_missing",
        ),
    )


def registry_timeline_coherence_violations(
    stories: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(stories, Mapping):
        rows = ((str(story_id), story) for story_id, story in stories.items())
    else:
        rows = (
            (str(story.get("story_id") or ""), story)
            for story in stories
            if isinstance(story, Mapping)
        )
    violations: list[dict[str, Any]] = []
    for story_id, story in rows:
        if not isinstance(story, Mapping):
            continue
        analysis = analyze_story_timeline_coherence(story, story_id=story_id)
        if not analysis.coherent:
            row = analysis.to_dict()
            row["canonical_title"] = str(story.get("canonical_title") or "")
            violations.append(row)
    violations.sort(key=lambda row: str(row.get("story_id") or ""))
    return violations


def incoming_entry_conflicts_with_story(
    story: Mapping[str, Any],
    *,
    event_key: str,
    title: str,
    source: str = "",
) -> dict[str, Any] | None:
    """Return a hard-conflict explanation for a proposed live attachment."""

    incoming = {
        "event_key": str(event_key or ""),
        "title": str(title or ""),
        "source": str(source or ""),
        "url": str(source or ""),
    }
    existing = [
        entry
        for entry in (story.get("timeline", ()) or ())
        if isinstance(entry, Mapping) and str(entry.get("title") or "").strip()
    ]
    if not existing:
        return None
    if any(timeline_entries_have_continuity(incoming, entry) for entry in existing):
        return None
    conflict = next(
        (
            entry
            for entry in existing
            if timeline_entries_are_hard_conflict(incoming, entry)
        ),
        None,
    )
    if conflict is None:
        return None
    overlap, shared = timeline_title_overlap(title, conflict.get("title"))
    return {
        "incoming_family": infer_timeline_event_family(incoming),
        "existing_family": infer_timeline_event_family(conflict),
        "incoming_title": str(title or ""),
        "existing_title": str(conflict.get("title") or ""),
        "title_overlap": round(overlap, 6),
        "shared_title_tokens": sorted(shared),
        "reason": "timeline_coherence_hard_conflict",
    }
