"""Observability reporting for the Plain editorial engine and activation layer.

This module owns the diagnostics schema and version labels so production
orchestration does not need to know the engine's internal data model.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .source_identity import normalize_source_identity_url
from .story_relationship import detect_advisory_follow_up_evidence

ENGINE_NAME = "plain-editorial-engine"
ENGINE_VERSION = "1.0.0"
ENGINE_RELEASE = "national-architecture-port"
OBSERVABILITY_SCHEMA_VERSION = 44
RESOLVER_VERSION = "3.0"
RELATIONSHIP_ENGINE_VERSION = "1.5"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _story_title(story: Mapping[str, Any]) -> str:
    canonical = str(story.get("canonical_title") or "").strip()
    if canonical:
        return canonical
    titles = story.get("titles") or []
    return str(titles[-1]).strip() if titles else ""


_RETROSPECTIVE_STOP_WORDS = {
    "the", "and", "for", "with", "from", "after", "before", "into",
    "news", "update", "says", "said", "earlier", "reported",
}
_RETROSPECTIVE_SOCIAL_MARKERS = (
    "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
    "reddit.com", "youtube.com", "youtu.be", "threads.net", "nextdoor.com",
)
_RETROSPECTIVE_LOW_VALUE_TITLE_PATTERNS = (
    r"^expert (?:breaks down|explains)\b",
    r"^what to know\b",
    r"^watch(?: below|:)\b",
    r"^video:\s*",
    r"^photos?:\s*",
    r"^opinion:\s*",
    r"^analysis:\s*",
    r"^live updates?\b",
    r"^a happy ending\b",
)
_RETROSPECTIVE_HIGH_CONFIDENCE = 0.80


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _retrospective_tokens(value: object) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", _normalized_text(value))
        if len(token) >= 3 and token not in _RETROSPECTIVE_STOP_WORDS
    }


def _retrospective_overlap(left: object, right: object) -> float:
    left_tokens = _retrospective_tokens(left)
    right_tokens = _retrospective_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def _retrospective_event_family(value: object) -> str:
    key = _normalized_text(value).replace(" ", "-")
    if not key:
        return ""
    if key == "unknown-event" or key.startswith("unknown-event-"):
        return "unknown-event"
    for family in ("animal-rescue", "traffic-crash", "missing-person", "fire"):
        if key == family or key.startswith(f"{family}-"):
            return family
    return re.sub(r"-[0-9a-f]{8,}$", "", key)


def _retrospective_source_identity(entry: Mapping[str, Any]) -> str:
    for field in ("url", "source"):
        normalized = normalize_source_identity_url(entry.get(field))
        if normalized:
            return normalized
    return ""


def _retrospective_pair_identity(
    prior: Mapping[str, Any],
    newer: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate identity independently of the persisted story assignment.

    A shared persistent story ID is not evidence that two timeline entries belong
    together. Retrospective transitions require an exact article/event anchor or
    strong pairwise title continuity. Canonical-title similarity is intentionally
    excluded because a corrupted story can simply adopt the newer unrelated title.
    """

    prior_title = prior.get("title")
    newer_title = newer.get("title")
    prior_tokens = _retrospective_tokens(prior_title)
    newer_tokens = _retrospective_tokens(newer_title)
    shared_tokens = prior_tokens & newer_tokens
    title_overlap = _retrospective_overlap(prior_title, newer_title)

    prior_event_key = _normalized_text(prior.get("event_key")).replace(" ", "-")
    newer_event_key = _normalized_text(newer.get("event_key")).replace(" ", "-")
    exact_event_key = bool(prior_event_key and prior_event_key == newer_event_key)
    prior_family = _retrospective_event_family(prior_event_key)
    newer_family = _retrospective_event_family(newer_event_key)
    event_family_match = bool(
        prior_family
        and newer_family
        and prior_family == newer_family
        and prior_family != "unknown-event"
    )

    prior_source = _retrospective_source_identity(prior)
    newer_source = _retrospective_source_identity(newer)
    exact_source_identity = bool(prior_source and prior_source == newer_source)

    strong_title_identity = title_overlap >= 0.45 and len(shared_tokens) >= 3
    corroborated_family_identity = (
        event_family_match
        and title_overlap >= 0.20
        and len(shared_tokens) >= 2
    )
    qualified = bool(
        exact_source_identity
        or exact_event_key
        or strong_title_identity
        or corroborated_family_identity
    )

    codes: list[str] = []
    if exact_source_identity:
        codes.append("exact_source_article_identity")
    if exact_event_key:
        codes.append("exact_event_key")
    if event_family_match:
        codes.append("event_family_match")
    elif (
        prior_family
        and newer_family
        and prior_family != "unknown-event"
        and newer_family != "unknown-event"
        and prior_family != newer_family
    ):
        codes.append("event_family_conflict")
    if strong_title_identity:
        codes.append("strong_pair_title_continuity")
    elif title_overlap >= 0.35:
        codes.append("moderate_pair_title_continuity")
    if len(shared_tokens) >= 3:
        codes.append("shared_discriminative_title_tokens")
    codes.append("identity_anchor_qualified" if qualified else "identity_anchor_missing")

    return {
        "qualified": qualified,
        "codes": codes,
        "title_overlap": title_overlap,
        "shared_title_tokens": sorted(shared_tokens),
        "exact_source_identity": exact_source_identity,
        "exact_event_key": exact_event_key,
        "event_family_match": event_family_match,
        "event_family_conflict": bool(
            prior_family
            and newer_family
            and prior_family != "unknown-event"
            and newer_family != "unknown-event"
            and prior_family != newer_family
        ),
        "prior_event_family": prior_family,
        "newer_event_family": newer_family,
    }


def _timeline_datetime(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timeline_entry_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "article_id": str(entry.get("article_id") or ""),
        "event_key": str(entry.get("event_key") or ""),
        "published_at": str(entry.get("published_at") or ""),
        "title": str(entry.get("title") or ""),
        "source": str(entry.get("source") or ""),
        "url": str(entry.get("url") or ""),
    }


def _retrospective_exclusion_reasons(entry: Mapping[str, Any]) -> tuple[str, ...]:
    title = _normalized_text(entry.get("title"))
    source_blob = " ".join((
        title,
        _normalized_text(entry.get("source")),
        _normalized_text(entry.get("url")),
    ))
    reasons: list[str] = []
    if any(marker in source_blob for marker in _RETROSPECTIVE_SOCIAL_MARKERS):
        reasons.append("social_source")
    if len(_retrospective_tokens(title)) < 5:
        reasons.append("insufficient_title_detail")
    if any(re.search(pattern, title) for pattern in _RETROSPECTIVE_LOW_VALUE_TITLE_PATTERNS):
        reasons.append("low_value_title")
    return tuple(reasons)


def _build_retrospective_follow_up_observability(
    stories: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Inspect persisted timelines for milestone transitions without changing them.

    Every candidate transition must independently prove article/event continuity.
    Persisted story membership and canonical-title overlap remain useful context,
    but cannot qualify a transition because incoherent historical timelines may
    contain unrelated entries.
    """

    candidates: list[dict[str, Any]] = []
    milestone_counts: Counter[str] = Counter()
    same_source_evolution_milestones: Counter[str] = Counter()
    same_source_evolution_examples: list[dict[str, Any]] = []
    same_source_evolution_count = 0
    blocking_conflicts: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    incoherent_stories: dict[str, dict[str, Any]] = {}
    excluded_entry_count = 0
    transitions_examined = 0
    timeline_entries_examined = 0
    stories_with_timelines = 0
    identity_anchor_rejected_count = 0

    for story in stories:
        raw_timeline = story.get("timeline") or []
        timeline = [dict(entry) for entry in raw_timeline if isinstance(entry, Mapping)]
        if len(timeline) < 2:
            continue
        timeline.sort(key=lambda entry: (
            _timeline_datetime(entry.get("published_at")),
            str(entry.get("article_id") or ""),
        ))
        stories_with_timelines += 1
        known_milestones: set[str] = set()
        previous_eligible: dict[str, Any] | None = None

        for entry in timeline:
            timeline_entries_examined += 1
            excluded = _retrospective_exclusion_reasons(entry)
            if excluded:
                excluded_entry_count += 1
                exclusion_reasons.update(excluded)
                continue

            evidence = detect_advisory_follow_up_evidence(
                entry.get("title"), entry.get("event_key")
            )
            milestones = set(evidence)
            if previous_eligible is not None:
                transitions_examined += 1
                novel_milestones = milestones - known_milestones
                if novel_milestones:
                    pair_identity = _retrospective_pair_identity(previous_eligible, entry)
                    prior_title_overlap = float(pair_identity["title_overlap"])
                    canonical_title_overlap = _retrospective_overlap(
                        story.get("canonical_title"), entry.get("title")
                    )

                    # An exact normalized source-article identity means the publisher
                    # evolved one article in place. That is valuable update evidence,
                    # but it is not evidence of a distinct follow-up publication and
                    # must never inflate follow-up activation readiness.
                    if pair_identity["exact_source_identity"]:
                        same_source_evolution_count += 1
                        same_source_evolution_milestones.update(novel_milestones)
                        if len(same_source_evolution_examples) < 25:
                            same_source_evolution_examples.append({
                                "story_id": str(story.get("story_id") or ""),
                                "story_title": _story_title(story),
                                "milestones": sorted(novel_milestones),
                                "matched_phrases": {
                                    milestone: list(evidence.get(milestone, ()))
                                    for milestone in sorted(novel_milestones)
                                },
                                "classification": "same_source_article_evolution",
                                "identity_anchor_codes": list(pair_identity["codes"]),
                                "prior_article": _timeline_entry_payload(previous_eligible),
                                "newer_article": _timeline_entry_payload(entry),
                            })
                        known_milestones.update(milestones)
                        previous_eligible = entry
                        continue

                    same_timestamp = (
                        _timeline_datetime(previous_eligible.get("published_at"))
                        == _timeline_datetime(entry.get("published_at"))
                    )
                    conflicts: list[str] = []
                    if same_timestamp:
                        conflicts.append("same_timestamp_order_uncertain")
                    if max(prior_title_overlap, canonical_title_overlap) < 0.22:
                        conflicts.append("weak_title_continuity")
                    if not pair_identity["qualified"]:
                        conflicts.append("timeline_identity_unanchored")
                        identity_anchor_rejected_count += 1
                    if pair_identity["event_family_conflict"]:
                        conflicts.append("event_family_conflict")
                    terminal = novel_milestones & {
                        "death", "resolution", "opening", "closure"
                    }
                    if len(terminal) > 1:
                        conflicts.append("multiple_terminal_milestones")

                    confidence = (
                        0.58
                        + 0.16 * min(1.0, prior_title_overlap)
                        + 0.14 * min(1.0, canonical_title_overlap)
                        + 0.08 * float(not same_timestamp)
                        + 0.08 * float(len(novel_milestones) == 1)
                        - 0.16 * float("weak_title_continuity" in conflicts)
                    )
                    if not pair_identity["qualified"]:
                        confidence = min(confidence, 0.74)
                    confidence = max(0.0, min(1.0, confidence))

                    reason_codes = [
                        "retrospective_timeline_transition",
                        "novel_milestone",
                        "persistent_story_identity_untrusted",
                        *pair_identity["codes"],
                    ]
                    if prior_title_overlap >= 0.35:
                        reason_codes.append("prior_title_continuity")
                    if canonical_title_overlap >= 0.35:
                        reason_codes.append("canonical_title_continuity_context_only")
                    if not same_timestamp:
                        reason_codes.append("chronology_supported")
                    if not pair_identity["qualified"]:
                        reason_codes.append("timeline_incoherent")
                    if pair_identity["event_family_conflict"]:
                        reason_codes.append("activation_blocked_event_family_conflict")

                    activation_eligible = (
                        pair_identity["qualified"]
                        and confidence >= _RETROSPECTIVE_HIGH_CONFIDENCE
                        and not conflicts
                    )
                    if activation_eligible:
                        reason_codes.append("activation_evidence_candidate")

                    prior_payload = _timeline_entry_payload(previous_eligible)
                    newer_payload = _timeline_entry_payload(entry)
                    if not pair_identity["qualified"]:
                        story_id = str(story.get("story_id") or "")
                        review = incoherent_stories.setdefault(story_id, {
                            "story_id": story_id,
                            "story_title": _story_title(story),
                            "transition_count": 0,
                            "blocking_reasons": Counter(),
                            "examples": [],
                        })
                        review["transition_count"] += 1
                        review["blocking_reasons"].update(["timeline_identity_unanchored"])
                        if len(review["examples"]) < 5:
                            review["examples"].append({
                                "prior_article": prior_payload,
                                "newer_article": newer_payload,
                                "milestones": sorted(novel_milestones),
                                "identity_anchor_codes": list(pair_identity["codes"]),
                                "prior_title_overlap": round(prior_title_overlap, 6),
                            })

                    milestone_counts.update(novel_milestones)
                    blocking_conflicts.update(conflicts)
                    candidates.append({
                        "story_id": str(story.get("story_id") or ""),
                        "story_title": _story_title(story),
                        "milestones": sorted(novel_milestones),
                        "matched_phrases": {
                            milestone: list(evidence.get(milestone, ()))
                            for milestone in sorted(novel_milestones)
                        },
                        "confidence": round(confidence, 6),
                        "activation_eligible": activation_eligible,
                        "timeline_incoherent": not pair_identity["qualified"],
                        "identity_anchor_qualified": bool(pair_identity["qualified"]),
                        "identity_anchor_codes": list(pair_identity["codes"]),
                        "shared_title_tokens": list(pair_identity["shared_title_tokens"]),
                        "blocking_conflicts": conflicts,
                        "reason_codes": reason_codes,
                        "prior_title_overlap": round(prior_title_overlap, 6),
                        "canonical_title_overlap": round(canonical_title_overlap, 6),
                        "prior_article": prior_payload,
                        "newer_article": newer_payload,
                        "candidate_trace": [
                            "Follow-up candidate mode: retrospective_observe_only",
                            f"Story: {story.get('story_id') or ''}",
                            f"Novel milestones: {', '.join(sorted(novel_milestones))}",
                            f"Identity anchor qualified: {pair_identity['qualified']}",
                            f"Identity anchor codes: {', '.join(pair_identity['codes'])}",
                            f"Prior title overlap: {prior_title_overlap:.2f}",
                            f"Canonical title overlap (context only): {canonical_title_overlap:.2f}",
                            f"Same timestamp: {same_timestamp}",
                            f"Blocking conflicts: {', '.join(conflicts) or 'none'}",
                            f"Candidate confidence: {confidence:.2f}",
                            f"Activation eligible: {activation_eligible}",
                        ],
                    })

            known_milestones.update(milestones)
            previous_eligible = entry

    candidates.sort(key=lambda candidate: (
        bool(candidate.get("timeline_incoherent")),
        not bool(candidate.get("activation_eligible")),
        -float(candidate.get("confidence") or 0.0),
        str(candidate.get("story_id") or ""),
        str((candidate.get("newer_article") or {}).get("published_at") or ""),
    ))
    high_confidence_count = sum(
        1 for candidate in candidates
        if candidate.get("identity_anchor_qualified")
        and float(candidate.get("confidence") or 0.0) >= _RETROSPECTIVE_HIGH_CONFIDENCE
    )
    activation_eligible_count = sum(
        1 for candidate in candidates if candidate.get("activation_eligible")
    )

    incoherent_story_rows: list[dict[str, Any]] = []
    for row in incoherent_stories.values():
        incoherent_story_rows.append({
            "story_id": row["story_id"],
            "story_title": row["story_title"],
            "transition_count": row["transition_count"],
            "blocking_reasons": dict(sorted(row["blocking_reasons"].items())),
            "examples": row["examples"],
        })
    incoherent_story_rows.sort(key=lambda row: (
        -int(row.get("transition_count") or 0),
        str(row.get("story_id") or ""),
    ))

    return {
        "mode": "retrospective_observe_only",
        "publication_behavior_changed": False,
        "stories_with_timelines": stories_with_timelines,
        "timeline_entries_examined": timeline_entries_examined,
        "transitions_examined": transitions_examined,
        "candidate_scope": "distinct_article_transitions",
        "candidate_count": len(candidates),
        "high_confidence_candidate_count": high_confidence_count,
        "activation_eligible_candidate_count": activation_eligible_count,
        "same_source_evolution_count": same_source_evolution_count,
        "same_source_evolution_milestones": dict(
            sorted(same_source_evolution_milestones.items())
        ),
        "same_source_evolution_examples": same_source_evolution_examples,
        "identity_anchor_rejected_count": identity_anchor_rejected_count,
        "incoherent_transition_count": identity_anchor_rejected_count,
        "incoherent_story_count": len(incoherent_story_rows),
        "incoherent_stories": incoherent_story_rows[:50],
        "milestones": dict(sorted(milestone_counts.items())),
        "blocking_conflicts": dict(sorted(blocking_conflicts.items())),
        "excluded_entry_count": excluded_entry_count,
        "exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "examples": candidates[:50],
        "review_ready": bool(candidates or incoherent_story_rows),
        "enforcement_ready": False,
        "enforcement_readiness_reason": (
            "Retrospective candidates are evidence for manual review only. "
            "Same-source article evolution is measured separately from distinct "
            "follow-up publications, event-family conflicts cannot become activation "
            "evidence, and incoherent timelines are quarantined from high-confidence "
            "evidence; no relationship, grouping, ranking or publication behavior changes."
        ),
    }


def build_editorial_observability(
    engine: Any,
    audit_rows: Iterable[Mapping[str, Any]],
    *,
    registry_path: str = "data/editorial_story_registry.json",
    mode: str = "shadow",
    activation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, JSON-safe diagnostics report.

    The function intentionally uses only the engine's public story query plus
    persisted story dictionaries. Missing future fields degrade to safe defaults.
    """

    rows = [dict(row) for row in audit_rows]
    stories = list(engine.get_top_stories(limit=100000))
    registry_health = (
        dict(engine.get_registry_health())
        if hasattr(engine, "get_registry_health")
        else {}
    )

    route_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    source_class_counts: Counter[str] = Counter()
    rejected_examples: list[dict[str, Any]] = []
    relationship_counts: Counter[str] = Counter()
    decision_trace_examples: list[dict[str, Any]] = []
    relationship_examples: list[dict[str, Any]] = []
    follow_up_candidate_count = 0
    high_confidence_follow_up_candidate_count = 0
    unanchored_follow_up_candidate_count = 0
    follow_up_candidate_milestones: Counter[str] = Counter()
    follow_up_candidate_current_relationships: Counter[str] = Counter()
    follow_up_candidate_reason_codes: Counter[str] = Counter()
    follow_up_candidate_examples: list[dict[str, Any]] = []

    has_current_relationships = any(bool(row.get("relationship")) for row in rows)

    for row in rows:
        route_counts[str(row.get("route") or "unknown")] += 1
        eligibility_counts[str(row.get("eligibility_status") or "publishable")] += 1
        source_class_counts[str(row.get("source_class") or "unknown")] += 1
        relationship = str(row.get("relationship") or "")
        if relationship:
            relationship_counts[relationship] += 1
            if len(relationship_examples) < 20:
                relationship_examples.append({
                    "story_id": row.get("story_id", ""),
                    "title": row.get("headline", ""),
                    "event_key": row.get("event_key", ""),
                    "relationship": relationship,
                    "confidence": row.get("relationship_confidence", 0),
                    "reason": row.get("relationship_reason", ""),
                    "decision_trace": list(row.get("decision_trace") or []),
                })
            trace = list(row.get("decision_trace") or [])
            if trace and len(decision_trace_examples) < 20:
                decision_trace_examples.append({
                    "story_id": row.get("story_id", ""),
                    "title": row.get("headline", ""),
                    "event_key": row.get("event_key", ""),
                    "relationship": relationship,
                    "confidence": row.get("relationship_confidence", 0),
                    "matched_existing": relationship in {"same_event", "follow_up"},
                    "reason": row.get("relationship_reason", ""),
                    "decision_trace": trace,
                })

        candidate_story_id = str(row.get("follow_up_candidate_story_id") or "").strip()
        candidate_confidence = float(row.get("follow_up_candidate_confidence") or 0.0)
        candidate_milestones = [
            str(value).strip()
            for value in (row.get("follow_up_candidate_milestones") or [])
            if str(value).strip()
        ]
        candidate_reason_codes = [
            str(value).strip()
            for value in (row.get("follow_up_candidate_reason_codes") or [])
            if str(value).strip()
        ]
        identity_anchor_qualified = (
            "identity_anchor_qualified" in candidate_reason_codes
        )
        if candidate_story_id and not identity_anchor_qualified:
            unanchored_follow_up_candidate_count += 1
        if candidate_story_id and identity_anchor_qualified:
            follow_up_candidate_count += 1
            if candidate_confidence >= 0.75:
                high_confidence_follow_up_candidate_count += 1
            follow_up_candidate_current_relationships[relationship or "unknown"] += 1
            follow_up_candidate_milestones.update(candidate_milestones)
            follow_up_candidate_reason_codes.update(candidate_reason_codes)
            if len(follow_up_candidate_examples) < 25:
                follow_up_candidate_examples.append({
                    "headline": row.get("headline", ""),
                    "event_key": row.get("event_key", ""),
                    "current_story_id": row.get("story_id", ""),
                    "current_relationship": relationship or "unknown",
                    "candidate_story_id": candidate_story_id,
                    "candidate_confidence": round(candidate_confidence, 6),
                    "candidate_milestones": candidate_milestones,
                    "candidate_reason_codes": candidate_reason_codes,
                    "candidate_trace": list(row.get("follow_up_candidate_trace") or []),
                })

        if not bool(row.get("eligible", True)) and len(rejected_examples) < 20:
            rejected_examples.append(
                {
                    "headline": row.get("headline", ""),
                    "source_url": row.get("source_url", ""),
                    "status": row.get("eligibility_status", "non_news"),
                    "reasons": list(row.get("eligibility_reasons") or []),
                    "source_class": row.get("source_class", "unknown"),
                }
            )

    importance_levels: Counter[str] = Counter()
    relevance_scopes: Counter[str] = Counter()
    proximity_scopes: Counter[str] = Counter()
    scores: list[int] = []
    priority_scores: list[int] = []
    relevance_scores: list[int] = []
    lifecycle_counts: Counter[str] = Counter()

    for story in stories:
        importance = story.get("importance") or {}
        level = str(importance.get("level") or "low").lower()
        if level not in {"breaking", "high", "normal", "low", "archived"}:
            level = "low"
        importance_levels[level] += 1
        scores.append(_safe_int(importance.get("score")))

        relevance = story.get("audience_relevance") or {}
        scope = str(relevance.get("scope") or "unknown")
        relevance_scopes[scope] += 1
        relevance_scores.append(_safe_int(relevance.get("score"), 60))
        proximity = story.get("editorial_proximity") or {}
        proximity_scopes[str(proximity.get("scope") or "unknown")] += 1
        priority_scores.append(_safe_int(story.get("editorial_score", story.get("editorial_priority"))))
        lifecycle = story.get("lifecycle") or {}
        lifecycle_counts[str(lifecycle.get("state") or story.get("status") or "unknown")] += 1

        for relation in story.get("relationship_history") or []:
            relationship = str(relation.get("relationship") or "unknown")
            if not has_current_relationships:
                relationship_counts[relationship] += 1
            if len(relationship_examples) < 20:
                relationship_examples.append(
                    {
                        "story_id": story.get("story_id", ""),
                        "title": _story_title(story),
                        "event_key": relation.get("event_key", ""),
                        "relationship": relationship,
                        "confidence": relation.get("confidence", 0),
                        "reason": relation.get("reason", ""),
                        "decision_trace": list(relation.get("decision_trace") or []),
                    }
                )

        for resolution in story.get("resolution_history") or []:
            relationship = str(resolution.get("relationship") or "unknown")
            # Resolution records include SAME_EVENT and NEW_STORY decisions that may
            # not have a corresponding relationship_history record.
            if not has_current_relationships and relationship in {"same_event", "new_story"}:
                relationship_counts[relationship] += 1
            trace = list(resolution.get("decision_trace") or [])
            if trace and len(decision_trace_examples) < 20:
                decision_trace_examples.append(
                    {
                        "story_id": story.get("story_id", ""),
                        "title": _story_title(story),
                        "event_key": resolution.get("event_key", ""),
                        "relationship": relationship,
                        "confidence": resolution.get("confidence", 0),
                        "matched_existing": bool(resolution.get("matched_existing", False)),
                        "reason": resolution.get("reason", ""),
                        "decision_trace": trace,
                    }
                )

    retrospective_follow_up = _build_retrospective_follow_up_observability(stories)

    top_stories: list[dict[str, Any]] = []
    for story in stories[:20]:
        importance = story.get("importance") or {}
        relevance = story.get("audience_relevance") or {}
        top_stories.append(
            {
                "story_id": story.get("story_id", ""),
                "title": _story_title(story),
                "score": _safe_int(importance.get("score")),
                "editorial_priority": _safe_int(story.get("editorial_priority")),
                "editorial_score": _safe_int(story.get("editorial_score", story.get("editorial_priority"))),
                "score_breakdown": dict(story.get("score_breakdown") or {}),
                "level": importance.get("level", "low"),
                "importance_reasons": list(importance.get("reasons") or []),
                "audience_relevance": {
                    "scope": relevance.get("scope", "unknown"),
                    "score": _safe_int(relevance.get("score"), 60),
                    "regions": list(relevance.get("regions") or []),
                    "places": list(relevance.get("places") or []),
                },
                "editorial_proximity": dict(story.get("editorial_proximity") or {}),
                "event_count": len(story.get("events") or []),
                "timeline_entries": len(story.get("timeline") or []),
                "relationship_decisions": len(story.get("relationship_history") or []),
                "canonical_source_candidates": len(story.get("title_candidates") or []),
                "lifecycle": dict(story.get("lifecycle") or {}),
            }
        )

    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "engine": {
            "name": ENGINE_NAME,
            "version": ENGINE_VERSION,
            "release": ENGINE_RELEASE,
            "resolver_version": RESOLVER_VERSION,
            "relationship_engine_version": RELATIONSHIP_ENGINE_VERSION,
        },
        "generated_at": _utc_now(),
        "mode": mode,
        "publication_behavior_changed": bool((activation or {}).get("publication_behavior_changed", False)),
        "activation": dict(activation or {}),
        "registry_path": registry_path,
        "audit": {
            "candidates_processed": len(rows),
            "routes": dict(sorted(route_counts.items())),
            "eligibility": dict(sorted(eligibility_counts.items())),
            "source_classes": dict(sorted(source_class_counts.items())),
            "rejected_count": sum(1 for row in rows if not bool(row.get("eligible", True))),
            "rejected_examples": rejected_examples,
        },
        "relationships": {
            "counts": dict(sorted(relationship_counts.items())),
            "examples": relationship_examples,
            "decision_trace_examples": decision_trace_examples,
        },
        "follow_up_detection": {
            "mode": "observe_only",
            "publication_behavior_changed": False,
            "candidate_count": follow_up_candidate_count,
            "high_confidence_candidate_count": high_confidence_follow_up_candidate_count,
            "unanchored_candidate_suppressed_count": unanchored_follow_up_candidate_count,
            "current_relationships": dict(
                sorted(follow_up_candidate_current_relationships.items())
            ),
            "milestones": dict(sorted(follow_up_candidate_milestones.items())),
            "reason_codes": dict(sorted(follow_up_candidate_reason_codes.items())),
            "examples": follow_up_candidate_examples,
            "retrospective_candidate_count": retrospective_follow_up["candidate_count"],
            "retrospective_high_confidence_candidate_count": retrospective_follow_up[
                "high_confidence_candidate_count"
            ],
            "retrospective_activation_eligible_candidate_count": retrospective_follow_up[
                "activation_eligible_candidate_count"
            ],
            "retrospective": retrospective_follow_up,
            "enforcement_ready": False,
            "enforcement_readiness_reason": (
                "Current-run and retrospective candidate evidence must be manually reviewed "
                "across production runs before broader follow-up grouping is activated."
            ),
        },
        "audience_relevance": {
            "scopes": dict(sorted(relevance_scopes.items())),
            "average_score": round(sum(relevance_scores) / len(relevance_scores), 2)
            if relevance_scores
            else 0.0,
        },
        "editorial_proximity": {
            "scopes": dict(sorted(proximity_scopes.items())),
            "average_priority": round(sum(priority_scores) / len(priority_scores), 2)
            if priority_scores else 0.0,
        },
        "story_lifecycle": {
            "counts": dict(sorted(lifecycle_counts.items())),
        },
        "registry_health": registry_health,
        "stories": {
            "total": len(stories),
            "importance_levels": {
                key: importance_levels.get(key, 0)
                for key in ("breaking", "high", "normal", "low", "archived")
            },
            "average_importance": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "top": top_stories,
        },
        "status": "healthy",
    }


def write_editorial_observability(
    engine: Any,
    audit_rows: Iterable[Mapping[str, Any]],
    output_path: str | Path,
    *,
    registry_path: str = "data/editorial_story_registry.json",
    mode: str = "shadow",
    activation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically write the diagnostics report and return its payload."""

    report = build_editorial_observability(
        engine,
        audit_rows,
        registry_path=registry_path,
        mode=mode,
        activation=activation,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return report
