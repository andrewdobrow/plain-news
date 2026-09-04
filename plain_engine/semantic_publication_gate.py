"""Bounded semantic final gate for public-permalink identity.

Deterministic identity remains the first publication authority.  This module is a
last-mile backstop for the narrow case where a fully generated article strongly
resembles a recent canonical story but exact source/story/incident keys did not
survive source extraction.  It performs two separate jobs:

1. retrieve only a small, recent, high-similarity candidate set; and
2. ask a model to distinguish same-event duplication from a material update.

The model never searches the archive and never writes files.  Its output is parsed,
validated, and converted into one of four deterministic actions by the caller.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence

SEMANTIC_PUBLICATION_GATE_VERSION = "1.9"
SEMANTIC_PUBLICATION_GATE_PROMPT_VERSION = "1.2"
SEMANTIC_PUBLICATION_RESOLUTION_PROMPT_VERSION = "1.0"
DEFAULT_RECENT_WINDOW_DAYS = 7
DEFAULT_MAX_CANDIDATES = 4
DEFAULT_MIN_CONFIDENCE = 0.82

ACTION_DUPLICATE = "duplicate_use_existing_canonical"
ACTION_UPDATE = "update_existing_canonical"
ACTION_NEW = "new_story"
ACTION_HOLD = "hold"
ALLOWED_ACTIONS = frozenset({ACTION_DUPLICATE, ACTION_UPDATE, ACTION_NEW, ACTION_HOLD})

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "has", "have", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "this", "to", "was", "were", "will", "with",
    "after", "before", "over", "under", "near", "about", "following",
}

_TOKEN_CANONICAL = {
    "died": "death", "dies": "death", "dead": "death", "fatal": "death",
    "killed": "death", "kills": "death",
    "crashes": "crash", "crashed": "crash", "collision": "crash",
    "collisions": "crash", "wreck": "crash", "wrecks": "crash",
    "woman": "woman", "women": "woman", "man": "man", "men": "man",
    "charged": "charge", "charges": "charge", "charging": "charge",
    "arrested": "arrest", "arrests": "arrest",
    "approves": "approve", "approved": "approve", "approval": "approve",
    "votes": "vote", "voted": "vote", "voting": "vote",
    "resigned": "resign", "resigns": "resign", "resigning": "resign",
    "left": "leave", "leaves": "leave", "leaving": "leave",
    # Public-policy outlets frequently alternate among rule, law, and ordinance
    # while describing the same local action. Normalize those surface forms so
    # the semantic gate can nominate the pair for Claude without treating the
    # normalization itself as proof of identity.
    "rule": "regulation", "rules": "regulation",
    "law": "regulation", "laws": "regulation",
    "ordinance": "regulation", "ordinances": "regulation",
    "fishes": "fishing", "fished": "fishing",
    "move": "revise", "moves": "revise", "moved": "revise",
    "rewrite": "revise", "rewrites": "revise", "rewriting": "revise",
    "review": "revise", "reviews": "revise", "reviewed": "revise",
    "reviewing": "revise",
    "change": "revise", "changes": "revise", "changed": "revise",
    "changing": "revise",
    "revise": "revise", "revises": "revise", "revised": "revise",
    "revising": "revise",
    "commissioner": "commission", "commissioners": "commission",
}

# These tokens are useful context but are too generic to establish that two
# public-policy headlines concern the same regulated subject. The policy-subject
# override below requires at least three shared topical tokens after removing
# this context vocabulary.
_GENERIC_CANDIDATE_CONTEXT_TOKENS = frozenset({
    "city", "county", "commission", "council", "board", "local", "state",
    "federal", "government", "official", "officials", "agency", "department",
    "revise", "propose", "approve", "vote", "says", "must",
})


# Content continuity is candidate-recall evidence only. These generic newsroom and
# geography words are intentionally removed so two unrelated stories from the same
# place/day cannot become candidates merely because both mention police, residents,
# officials, or a state name. Concrete event vocabulary (tornado, waterspout, roof,
# intersection, defendant names, street names, amounts, etc.) remains available.
_GENERIC_CONTENT_CONTEXT_TOKENS = _GENERIC_CANDIDATE_CONTEXT_TOKENS | frozenset({
    "according", "official", "officials", "resident", "residents", "people",
    "news", "story", "report", "reported", "reporting", "said", "saying",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "today", "yesterday", "morning", "afternoon", "evening",
    "area", "community", "communities", "department", "office", "agency",
    "authorities", "home", "homes", "nearby", "including", "also", "new",
    "update", "updates", "latest", "still", "first", "another", "several",
})


def content_tokens(article: Mapping[str, Any]) -> frozenset[str]:
    """Return distinctive article-level tokens for bounded candidate recall.

    This is deliberately broader than headline similarity but weaker than identity.
    It never merges stories. It only allows the final semantic adjudicator to see
    angle-shifted coverage of a likely shared event.
    """
    text = " ".join(
        str(article.get(key) or "")
        for key in ("headline", "source_headline", "lead", "teaser", "body")
    )[:12000]
    tokens = set()
    for raw in _clean_text(text).split():
        if raw in _STOP_WORDS:
            continue
        token = _TOKEN_CANONICAL.get(raw, raw)
        if token in _GENERIC_CONTENT_CONTEXT_TOKENS:
            continue
        if len(token) < 3 and not token.isdigit():
            continue
        tokens.add(token)
    return frozenset(tokens)


def content_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_tokens = content_tokens(left)
    right_tokens = content_tokens(right)
    if not left_tokens or not right_tokens:
        return {
            "score": 0.0, "overlap": 0.0, "jaccard": 0.0,
            "shared_token_count": 0, "shared_tokens": [],
        }
    shared = left_tokens & right_tokens
    overlap = len(shared) / min(len(left_tokens), len(right_tokens))
    jaccard = len(shared) / len(left_tokens | right_tokens)
    # Favor containment because a short follow-up often repeats a compact subset of
    # the original incident facts while focusing its headline on one new angle.
    score = 0.72 * overlap + 0.28 * jaccard
    return {
        "score": round(min(1.0, score), 4),
        "overlap": round(overlap, 4),
        "jaccard": round(jaccard, 4),
        "shared_token_count": len(shared),
        "shared_tokens": sorted(shared)[:60],
    }


def _clean_text(value: object) -> str:
    text = str(value or "").casefold()
    text = text.replace("st.", "st")
    text = re.sub(r"(?<=\d)[–—-](?=\d)", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def headline_tokens(value: object) -> tuple[str, ...]:
    """Return stable, order-independent headline tokens.

    Ages and place-name words are intentionally retained.  Those are unusually useful
    in local-news duplicate retrieval, while generic function words are discarded.
    """
    tokens: list[str] = []
    for token in _clean_text(value).split():
        if token in _STOP_WORDS:
            continue
        token = _TOKEN_CANONICAL.get(token, token)
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.append(token)
    return tuple(tokens)


def normalized_headline(value: object) -> str:
    return " ".join(headline_tokens(value))


def headline_similarity(left: object, right: object) -> dict[str, float]:
    """Measure order-insensitive similarity without treating it as identity proof."""
    left_tokens = headline_tokens(left)
    right_tokens = headline_tokens(right)
    left_set, right_set = set(left_tokens), set(right_tokens)
    if not left_set or not right_set:
        return {
            "score": 0.0,
            "overlap": 0.0,
            "jaccard": 0.0,
            "sorted_sequence": 0.0,
            "ordered_sequence": 0.0,
            "shared_token_count": 0.0,
        }
    shared = left_set & right_set
    overlap = len(shared) / min(len(left_set), len(right_set))
    jaccard = len(shared) / len(left_set | right_set)
    sorted_sequence = SequenceMatcher(
        None, " ".join(sorted(left_set)), " ".join(sorted(right_set))
    ).ratio()
    ordered_sequence = SequenceMatcher(
        None, " ".join(left_tokens), " ".join(right_tokens)
    ).ratio()
    score = max(
        0.50 * overlap + 0.30 * jaccard + 0.20 * sorted_sequence,
        0.88 * ordered_sequence,
    )
    return {
        "score": round(min(1.0, score), 4),
        "overlap": round(overlap, 4),
        "jaccard": round(jaccard, 4),
        "sorted_sequence": round(sorted_sequence, 4),
        "ordered_sequence": round(ordered_sequence, 4),
        "shared_token_count": float(len(shared)),
    }


def _string_set(article: Mapping[str, Any], key: str) -> set[str]:
    values = article.get(key) or ()
    if isinstance(values, str):
        values = (values,)
    return {str(value).strip().casefold() for value in values if str(value).strip()}


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        dt = None
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(text)
        except Exception:
            pass
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt = datetime.strptime(text[:10], "%Y-%m-%d")
                except Exception:
                    return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _article_datetime(article: Mapping[str, Any]) -> datetime | None:
    for key in (
        "published_at", "first_published", "published", "source_published",
        "date", "lastmod",
    ):
        dt = _parse_datetime(article.get(key))
        if dt is not None:
            return dt
    return None


def _arrest_counts(article: Mapping[str, Any]) -> set[str]:
    text = " ".join(
        str(article.get(key) or "")
        for key in ("headline", "source_headline", "lead", "teaser", "body")
    )
    return {
        match.group(1)
        for match in re.finditer(
            r"\b(\d{1,3})\s+(?:people?\s+)?(?:were\s+)?arrest(?:ed|s)?\b",
            text,
            re.IGNORECASE,
        )
    }


def _drug_terms(article: Mapping[str, Any]) -> set[str]:
    text = " ".join(
        str(article.get(key) or "")
        for key in ("headline", "source_headline", "lead", "teaser", "body")
    ).casefold()
    aliases = {
        "cocaine": r"\bcocaine\b",
        "fentanyl": r"\bfentanyl\b",
        "methamphetamine": r"\b(?:methamphetamine|meth)\b",
        "marijuana": r"\b(?:marijuana|cannabis)\b",
        "heroin": r"\bheroin\b",
    }
    return {name for name, pattern in aliases.items() if re.search(pattern, text, re.IGNORECASE)}


def _shared_feature_evidence(
    incoming: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    shared_geography = _string_set(incoming, "geography") & _string_set(candidate, "geography")
    shared_families = _string_set(incoming, "event_families") & _string_set(candidate, "event_families")
    shared_people = _string_set(incoming, "people") & _string_set(candidate, "people")
    shared_precise = _string_set(incoming, "precise_locations") & _string_set(candidate, "precise_locations")
    shared_agencies = _string_set(incoming, "agencies") & _string_set(candidate, "agencies")
    left_anchor = str(incoming.get("incident_anchor") or "").strip().casefold()
    right_anchor = str(candidate.get("incident_anchor") or "").strip().casefold()
    left_known = str(incoming.get("known_event_key") or "").strip().casefold()
    right_known = str(candidate.get("known_event_key") or "").strip().casefold()
    exact_anchor = bool(left_anchor and left_anchor == right_anchor)
    exact_known = bool(left_known and left_known == right_known)
    anchor_conflict = bool(left_anchor and right_anchor and left_anchor != right_anchor)
    known_conflict = bool(left_known and right_known and left_known != right_known)
    return {
        "shared_geography": sorted(shared_geography),
        "shared_event_families": sorted(shared_families),
        "shared_people": sorted(shared_people),
        "shared_precise_locations": sorted(shared_precise),
        "shared_agencies": sorted(shared_agencies),
        "exact_incident_anchor": exact_anchor,
        "exact_known_event_key": exact_known,
        "incident_anchor_conflict": anchor_conflict,
        "known_event_key_conflict": known_conflict,
    }


def candidate_evidence(
    incoming: Mapping[str, Any], candidate: Mapping[str, Any], *, window_days: int
) -> dict[str, Any]:
    incoming_headline = incoming.get("headline") or incoming.get("title") or ""
    candidate_headline = candidate.get("headline") or candidate.get("title") or ""
    final_similarity = headline_similarity(incoming_headline, candidate_headline)
    incoming_source_headline = str(incoming.get("source_headline") or "").strip()
    candidate_source_headline = str(candidate.get("source_headline") or "").strip()
    source_similarity = headline_similarity(
        incoming_source_headline, candidate_source_headline
    )
    source_generic = bool(
        re.search(r"\bweek of\b|\bnews roundup\b|\blocal briefs?\b", incoming_source_headline, re.I)
        or re.search(r"\bweek of\b|\bnews roundup\b|\blocal briefs?\b", candidate_source_headline, re.I)
        or len(headline_tokens(incoming_source_headline)) < 5
        or len(headline_tokens(candidate_source_headline)) < 5
    )
    if source_generic:
        source_similarity = {**source_similarity, "score": 0.0}
    if final_similarity["score"] >= source_similarity["score"]:
        similarity = final_similarity
        similarity_basis = "final_headline"
        left_similarity_tokens = set(headline_tokens(incoming_headline))
        right_similarity_tokens = set(headline_tokens(candidate_headline))
    else:
        similarity = source_similarity
        similarity_basis = "source_headline"
        left_similarity_tokens = set(headline_tokens(incoming_source_headline))
        right_similarity_tokens = set(headline_tokens(candidate_source_headline))
    shared_headline_tokens = left_similarity_tokens & right_similarity_tokens
    shared_topic_tokens = shared_headline_tokens - _GENERIC_CANDIDATE_CONTEXT_TOKENS
    features = _shared_feature_evidence(incoming, candidate)
    content = content_similarity(incoming, candidate)
    incoming_dt = _article_datetime(incoming)
    candidate_dt = _article_datetime(candidate)
    day_gap = None
    if incoming_dt is not None and candidate_dt is not None:
        day_gap = abs((incoming_dt.date() - candidate_dt.date()).days)
    time_safe = day_gap is None or day_gap <= max(1, int(window_days))

    score = float(similarity["score"])
    if features["shared_geography"]:
        score += 0.06
    if features["shared_event_families"]:
        score += 0.06
    if features["shared_people"]:
        score += 0.10
    if features["shared_precise_locations"]:
        score += 0.10
    if features["shared_agencies"]:
        score += 0.03
    if features["exact_incident_anchor"] or features["exact_known_event_key"]:
        score += 0.15
    # Content continuity only nudges ranking after eligibility is established.
    score += min(0.18, float(content.get("score") or 0.0) * 0.22)
    score = min(1.0, score)

    shared_count = int(similarity["shared_token_count"])
    contextual_anchor = bool(
        features["shared_people"]
        or features["shared_precise_locations"]
        or features["exact_incident_anchor"]
        or features["exact_known_event_key"]
    )
    strong_source_identity_anchor = bool(
        contextual_anchor or features["shared_agencies"]
    )
    context_compatible = bool(
        features["shared_geography"] and features["shared_event_families"]
    )
    content_shared_count = int(content.get("shared_token_count") or 0)
    content_score = float(content.get("score") or 0.0)
    # Angle-shifted same-event coverage can have weak headline overlap. Candidate
    # recall is allowed only with a dense bundle of shared article facts plus
    # independent local/event context. This still requires model adjudication.
    dense_shared_fact_continuity = bool(
        day_gap is not None
        and day_gap <= 2
        and content_shared_count >= 12
        and content_score >= 0.20
        and context_compatible
    )
    strong_content_event_continuity = bool(
        (
            day_gap is not None
            and day_gap <= 2
            and content_shared_count >= 8
            and content_score >= 0.24
            and (
                context_compatible
                or bool(features["shared_precise_locations"])
                or bool(features["shared_people"])
                or bool(features["exact_incident_anchor"])
                or bool(features["exact_known_event_key"])
                or (
                    bool(features["shared_geography"])
                    and bool(features["shared_agencies"])
                    and content_shared_count >= 10
                    and content_score >= 0.30
                )
            )
        )
        or dense_shared_fact_continuity
    )
    left_anchor_value = str(incoming.get("incident_anchor") or "").strip().casefold()
    right_anchor_value = str(candidate.get("incident_anchor") or "").strip().casefold()
    exact_named_operation_anchor = bool(
        features["exact_incident_anchor"]
        and left_anchor_value.startswith("law-enforcement-operation:")
        and right_anchor_value == left_anchor_value
    )
    shared_numeric_tokens = {
        token for token in shared_headline_tokens if token.isdigit()
    }
    shared_arrest_counts = _arrest_counts(incoming) & _arrest_counts(candidate)
    shared_drug_terms = _drug_terms(incoming) & _drug_terms(candidate)
    drug_family_continuity = bool(
        "drug-case" in features["shared_event_families"]
        and features["shared_geography"]
        and features["shared_agencies"]
        and shared_arrest_counts
        and shared_drug_terms
        and shared_count >= 3
    )

    # The generated Plain headline is not an independent identity source. A model can
    # occasionally elevate a secondary paragraph and rewrite a story so that it
    # resembles an unrelated canonical article even though the publisher headline
    # still describes a different primary event. When the source headlines have
    # weak continuity and no strong structured anchor independently corroborates the
    # pair, fail closed before semantic adjudication.
    source_headline_drift_conflict = bool(
        not source_generic
        and similarity_basis == "final_headline"
        and float(final_similarity.get("score") or 0.0) >= 0.64
        and float(source_similarity.get("score") or 0.0) < 0.38
        and int(source_similarity.get("shared_token_count") or 0) <= 3
        and not strong_source_identity_anchor
    )
    conflict = bool(
        features["incident_anchor_conflict"] or features["known_event_key_conflict"]
    )

    # The semantic gate exists specifically because deterministic event keys can be
    # incomplete or contradictory across publishers.  A generated generic event key
    # (for example ``traffic-crash-port-st-lucie-*``) must not veto a near-identical
    # finished headline before Claude is allowed to compare the stories.  Preserve
    # the conflict as evidence, but permit a bounded candidate when the finished
    # headlines are strongly similar and share enough distinctive vocabulary.
    #
    # Two conservative override tiers are allowed. One requires very high fuzzy
    # similarity; the other requires a larger bundle of shared canonical tokens.
    # Both only nominate a pair for adjudication and never merge it directly.
    conflict_override_tier = ""
    if conflict and exact_named_operation_anchor:
        conflict_override_tier = "exact_named_law_enforcement_operation"
    elif conflict and drug_family_continuity:
        conflict_override_tier = "law_enforcement_drug_operation_continuity"
    elif conflict and similarity["score"] >= 0.74 and shared_count >= 6:
        conflict_override_tier = "strong_headline_similarity"
    elif conflict and similarity["score"] >= 0.56 and shared_count >= 8:
        # Some publishers describe the same incident from different narrative
        # angles, which lowers the aggregate fuzzy score even when the pair
        # shares a highly distinctive bundle of actors, action, place, and
        # outcome. This lower-score path requires more shared canonical tokens
        # and still only nominates the pair for Claude adjudication.
        conflict_override_tier = "distinctive_token_overlap"
    elif (
        conflict
        and similarity["score"] >= 0.56
        and shared_count >= 6
        and context_compatible
        and any(
            "policy" in family or "regulat" in family or "government" in family
            for family in features["shared_event_families"]
        )
        and "regulation" in shared_topic_tokens
        and len(shared_topic_tokens - {"regulation"}) >= 2
    ):
        # Policy coverage often changes vocabulary across outlets: rules, laws,
        # and ordinances may all describe one proceeding, while rewrite, review,
        # and change describe the same action. Require a shared geography/event
        # family plus the regulation concept and two additional subject tokens.
        # This only lets Claude see the pair; it never merges automatically.
        conflict_override_tier = "policy_subject_continuity"
    if conflict and not conflict_override_tier and strong_content_event_continuity:
        conflict_override_tier = "strong_content_event_continuity"
    strong_conflict_override = bool(conflict_override_tier)

    similarity_gate = bool(
        similarity["score"] >= 0.64
        or (similarity["score"] >= 0.50 and context_compatible and contextual_anchor)
        or (similarity["score"] >= 0.56 and context_compatible and shared_count >= 6)
        # A formally named police/sheriff operation is a concrete incident anchor.
        # This only nominates the pair for adjudication; it never authorizes a merge.
        or exact_named_operation_anchor
        # Drug-bust headlines often split the same facts across "ring", "bust",
        # "operation" and "investigation" wording. Same agency + geography + drug
        # family + arrest + shared count is enough to let the final gate compare them.
        or drug_family_continuity
        or strong_content_event_continuity
        or strong_conflict_override
    )
    eligible = bool(
        time_safe
        and (
            shared_count >= 4
            or exact_named_operation_anchor
            or drug_family_continuity
            or strong_content_event_continuity
        )
        and similarity_gate
        and (not conflict or strong_conflict_override)
        and (not source_headline_drift_conflict or strong_content_event_continuity)
    )
    reasons: list[str] = []
    if similarity["score"] >= 0.64:
        reasons.append("strong_fuzzy_headline")
    elif similarity["score"] >= 0.50:
        reasons.append("moderate_fuzzy_headline")
    if context_compatible:
        reasons.append("shared_geography_and_event_family")
    if contextual_anchor:
        reasons.append("shared_incident_anchor")
    if exact_named_operation_anchor:
        reasons.append("exact_named_law_enforcement_operation")
    if drug_family_continuity:
        reasons.append("law_enforcement_drug_operation_continuity")
    if strong_content_event_continuity:
        reasons.append("strong_content_event_continuity")
    if day_gap is not None:
        reasons.append(f"publication_gap_{day_gap}_days")
    if conflict:
        reasons.append("structured_identity_conflict")
    if conflict_override_tier == "strong_headline_similarity":
        reasons.append("structured_identity_conflict_overridden_by_strong_headline")
    elif conflict_override_tier == "distinctive_token_overlap":
        reasons.append("structured_identity_conflict_overridden_by_distinctive_overlap")
    elif conflict_override_tier == "policy_subject_continuity":
        reasons.append("structured_identity_conflict_overridden_by_policy_subject")
    elif conflict_override_tier == "exact_named_law_enforcement_operation":
        reasons.append("structured_identity_conflict_overridden_by_named_operation")
    elif conflict_override_tier == "law_enforcement_drug_operation_continuity":
        reasons.append("structured_identity_conflict_overridden_by_drug_operation")
    elif conflict_override_tier == "strong_content_event_continuity":
        reasons.append("structured_identity_conflict_overridden_by_content_continuity")
    if source_headline_drift_conflict:
        reasons.append("source_headline_drift_conflict")
    if not time_safe:
        reasons.append("outside_recent_window")

    return {
        "eligible": eligible,
        "structured_conflict_override": strong_conflict_override,
        "structured_conflict_override_tier": conflict_override_tier,
        "source_headline_drift_conflict": source_headline_drift_conflict,
        "retrieval_score": round(score, 4),
        "headline_similarity": similarity,
        "similarity_basis": similarity_basis,
        "shared_headline_tokens": sorted(shared_headline_tokens),
        "shared_topic_tokens": sorted(shared_topic_tokens),
        "shared_arrest_counts": sorted(shared_arrest_counts),
        "shared_drug_terms": sorted(shared_drug_terms),
        "shared_numeric_tokens": sorted(shared_numeric_tokens),
        "content_similarity": content,
        "strong_content_event_continuity": strong_content_event_continuity,
        "dense_shared_fact_continuity": dense_shared_fact_continuity,
        "final_headline_similarity": final_similarity,
        "source_headline_similarity": source_similarity,
        "day_gap": day_gap,
        "time_safe": time_safe,
        "reasons": reasons,
        **features,
    }


def retrieve_recent_candidates(
    incoming: Mapping[str, Any],
    archive_articles: Iterable[Mapping[str, Any]],
    *,
    window_days: int = DEFAULT_RECENT_WINDOW_DAYS,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return a bounded candidate set; fuzzy similarity never makes the decision."""
    candidates: list[dict[str, Any]] = []
    incoming_slug = str(incoming.get("slug") or "").strip()
    for candidate in archive_articles:
        if not isinstance(candidate, Mapping):
            continue
        slug = str(candidate.get("slug") or "").strip()
        if not slug or slug == incoming_slug:
            continue
        evidence = candidate_evidence(incoming, candidate, window_days=window_days)
        if not evidence.get("eligible"):
            continue
        candidates.append({
            "slug": slug,
            "headline": str(candidate.get("headline") or candidate.get("title") or ""),
            "article": dict(candidate),
            "evidence": evidence,
        })
    candidates.sort(
        key=lambda row: (
            float(row["evidence"].get("retrieval_score") or 0.0),
            -int(row["evidence"].get("day_gap") or 0),
            row.get("slug", ""),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(max_candidates))]


def _compact_article(article: Mapping[str, Any]) -> dict[str, Any]:
    body = str(article.get("body") or article.get("article_text") or "").strip()
    body = re.sub(r"\s+", " ", body)
    return {
        "slug": str(article.get("slug") or ""),
        "headline": str(article.get("headline") or article.get("title") or ""),
        "source_headline": str(article.get("source_headline") or ""),
        "published_at": str(
            article.get("published_at")
            or article.get("first_published")
            or article.get("published")
            or article.get("date")
            or ""
        ),
        "source_url": str(article.get("source_url") or ""),
        "lead": str(article.get("lead") or article.get("teaser") or "")[:800],
        "body": body[:4200],
        "geography": sorted(_string_set(article, "geography")),
        "event_families": sorted(_string_set(article, "event_families")),
        "people": sorted(_string_set(article, "people")),
        "precise_locations": sorted(_string_set(article, "precise_locations")),
        "agencies": sorted(_string_set(article, "agencies")),
        "incident_anchor": str(article.get("incident_anchor") or ""),
        "known_event_key": str(article.get("known_event_key") or ""),
    }


def decision_cache_key(
    incoming: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], *, model: str
) -> str:
    payload = {
        "gate_version": SEMANTIC_PUBLICATION_GATE_VERSION,
        "prompt_version": SEMANTIC_PUBLICATION_GATE_PROMPT_VERSION,
        "model": str(model or ""),
        "incoming": _compact_article(incoming),
        "candidates": [
            {
                "article": _compact_article(row.get("article") or row),
                "evidence": row.get("evidence") or {},
            }
            for row in candidates
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()



def resolution_decision_cache_key(
    incoming: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    initial_decision: Mapping[str, Any],
    *,
    model: str,
) -> str:
    """Cache a single focused HOLD-resolution decision separately from first pass."""
    payload = {
        "gate_version": SEMANTIC_PUBLICATION_GATE_VERSION,
        "prompt_version": SEMANTIC_PUBLICATION_RESOLUTION_PROMPT_VERSION,
        "model": str(model or ""),
        "incoming": _compact_article(incoming),
        "candidates": [
            {
                "article": _compact_article(row.get("article") or row),
                "evidence": row.get("evidence") or {},
            }
            for row in candidates
        ],
        "initial_decision": {
            key: initial_decision.get(key)
            for key in (
                "action", "selected_candidate_slug", "same_real_world_event",
                "material_new_update", "independently_newsworthy_followup",
                "confidence", "shared_anchors", "novel_facts", "reason",
            )
        },
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        from json_repair import repair_json

        parsed = json.loads(repair_json(text))
    except Exception:
        try:
            parsed = json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("semantic gate model returned no JSON object")
            parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("semantic gate model response was not a JSON object")
    return parsed


def _response_text(response: Any) -> str:
    """Return textual message content while safely ignoring thinking/tool blocks."""
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        value = getattr(block, "text", None)
        if value:
            chunks.append(str(value))
    return "\n".join(chunks).strip()


def _clamp_confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return min(1.0, max(0.0, number))


def validate_model_decision(
    raw_decision: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    candidate_slugs = {
        str(row.get("slug") or (row.get("article") or {}).get("slug") or "").strip()
        for row in candidates
    }
    candidate_slugs.discard("")
    selected = str(raw_decision.get("selected_candidate_slug") or "").strip()
    same_event = raw_decision.get("same_real_world_event") is True
    material_update = raw_decision.get("material_new_update") is True
    independent_followup = raw_decision.get("independently_newsworthy_followup") is True
    confidence = _clamp_confidence(raw_decision.get("confidence"))
    requested_action = str(raw_decision.get("recommended_action") or "").strip()
    reason = str(raw_decision.get("reason") or "").strip()
    shared_anchors = [str(v).strip() for v in (raw_decision.get("shared_anchors") or []) if str(v).strip()]
    novel_facts = [str(v).strip() for v in (raw_decision.get("novel_facts") or []) if str(v).strip()]
    consistency_repairs: list[str] = []

    # The model sometimes reaches the correct structured identity/materiality
    # conclusion but truncates before the final recommended_action field (it is
    # intentionally last in older prompts). When the core booleans provide an
    # unambiguous policy action, recover that missing trailing field instead of
    # turning a high-confidence same-event update into a terminal HOLD.
    if not requested_action:
        if same_event and selected in candidate_slugs:
            requested_action = ACTION_UPDATE if material_update else ACTION_DUPLICATE
            consistency_repairs.append("recommended_action_inferred_from_same_event_flags")
        elif not same_event and confidence >= 0.65:
            requested_action = ACTION_NEW
            consistency_repairs.append("recommended_action_inferred_from_new_story_flags")

    # recommended_action is the model's explicit final policy choice. If it says
    # to update an established same-event canonical and supplies concrete novel
    # facts, treat a contradictory false material_new_update flag as a schema
    # inconsistency rather than silently discarding the requested canonical
    # refresh. The downstream composer/context contract still has to validate
    # the actual update before any article can be rewritten.
    if (
        same_event
        and selected in candidate_slugs
        and requested_action == ACTION_UPDATE
        and not material_update
        and novel_facts
    ):
        material_update = True
        consistency_repairs.append("material_update_inferred_from_explicit_update_action")

    validation_errors: list[str] = []
    if requested_action not in ALLOWED_ACTIONS:
        validation_errors.append("unknown_recommended_action")
    if (
        same_event
        and requested_action == ACTION_UPDATE
        and not material_update
    ):
        validation_errors.append("update_action_without_material_evidence")
    if same_event and selected not in candidate_slugs:
        validation_errors.append("same_event_without_valid_candidate")
    if not same_event and requested_action in {ACTION_DUPLICATE, ACTION_UPDATE}:
        validation_errors.append("same_event_action_without_same_event")
    if same_event and confidence < float(min_confidence):
        validation_errors.append("same_event_confidence_below_threshold")

    if validation_errors:
        action = ACTION_HOLD
    elif same_event:
        # Different angle alone is not a new story. A second permalink is allowed
        # only when the model explicitly finds an independently newsworthy
        # accountability/consequence/policy question that stands on its own.
        if independent_followup and requested_action == ACTION_NEW and material_update:
            action = ACTION_NEW
        else:
            action = ACTION_UPDATE if material_update else ACTION_DUPLICATE
    elif requested_action == ACTION_HOLD or confidence < 0.65:
        action = ACTION_HOLD
    else:
        action = ACTION_NEW

    return {
        "status": "validated" if not validation_errors else "invalid_model_response",
        "action": action,
        "recommended_action": requested_action,
        "selected_candidate_slug": selected if selected in candidate_slugs else "",
        "same_real_world_event": same_event,
        "material_new_update": material_update,
        "independently_newsworthy_followup": independent_followup,
        "confidence": confidence,
        "shared_anchors": shared_anchors[:20],
        "novel_facts": novel_facts[:20],
        "reason": reason,
        "validation_errors": validation_errors,
        "consistency_repairs": consistency_repairs,
    }


def _prompt(
    incoming: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> str:
    candidate_payload = []
    for row in candidates:
        article = row.get("article") or row
        candidate_payload.append({
            "article": _compact_article(article),
            "retrieval_evidence": row.get("evidence") or {},
        })
    return f"""You are the final duplicate-publication gate for a U.S.-wide general-news site.

Compare the fully written INCOMING article with the RECENT CANONICAL CANDIDATES. Decide whether the incoming article covers the exact same real-world incident, case, meeting, game, government action, business event, or continuing proceeding as one candidate.

Do not merge merely because two stories share a city, agency, topic, crime type, road, team, or generic headline vocabulary. The same real-world event requires concrete shared anchors such as the same named participant, exact location, date/time, vehicle, case, governing action, victim, business, or distinctive fact pattern.

Then separately decide whether the incoming article contains a MATERIAL NEW UPDATE. Material updates include a victim being identified, a death after an earlier injury report, an arrest or charge, an official cause or finding, a court ruling or sentence, a consequential government vote, a meaningful casualty revision, or another development that changes what readers need to know. Another outlet repeating the same facts, reordered wording, an additional photograph, routine scene detail, witness color, cleanup detail, anniversary/color framing, or background explanation is NOT by itself a reason for a new URL.

For the SAME EVENT, also decide whether the incoming article is an INDEPENDENTLY NEWSWORTHY FOLLOW-UP. This is rare. It must introduce a distinct accountability, consequence, policy, investigation, or public-interest question that would still merit its own headline even if the reader already knew the underlying event. Merely changing the angle, adding resident reactions, adding official classification/damage totals, explaining mechanics, or reporting cleanup should normally update the existing canonical instead. A technical explanation should be folded into the related accountability story when it mainly explains that question.

Choose exactly one action:
- duplicate_use_existing_canonical: same event, no material update; preserve the candidate page without rewriting it.
- update_existing_canonical: same event and material update that belongs in the existing canonical; update it rather than minting a new URL.
- new_story: either no candidate is the same event, OR the same event has a material, independently newsworthy follow-up as defined above.
- hold: evidence is ambiguous or insufficient.

Return ONLY one JSON object with this exact shape. Put recommended_action first and make every field agree with that action:
{{
  "recommended_action": "duplicate_use_existing_canonical",
  "selected_candidate_slug": "candidate slug or null",
  "same_real_world_event": true,
  "material_new_update": false,
  "independently_newsworthy_followup": false,
  "confidence": 0.0,
  "shared_anchors": ["specific shared facts"],
  "novel_facts": ["specific genuinely new facts"],
  "reason": "brief evidence-based explanation"
}}

Consistency is mandatory: update_existing_canonical requires same_real_world_event=true and material_new_update=true. duplicate_use_existing_canonical requires same_real_world_event=true and material_new_update=false.

INCOMING ARTICLE:
{json.dumps(_compact_article(incoming), ensure_ascii=False, indent=2)}

RECENT CANONICAL CANDIDATES:
{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}
"""


def adjudicate_candidates(
    client: Any,
    *,
    model: str,
    incoming: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    timeout_seconds: float = 45.0,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Call the model once and fail closed whenever suspicious candidates exist."""
    if not candidates:
        return {
            "status": "no_candidates",
            "action": ACTION_NEW,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 1.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": "No recent fuzzy candidates crossed the retrieval threshold.",
            "validation_errors": [],
        }
    if client is None or not getattr(client, "messages", None):
        return {
            "status": "model_unavailable",
            "action": ACTION_HOLD,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 0.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": "Semantic gate model client unavailable; fail-closed hold.",
            "validation_errors": ["model_unavailable"],
        }

    request_client = client
    kwargs = {
        "model": model,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": _prompt(incoming, candidates)}],
    }
    try:
        if hasattr(client, "with_options"):
            request_client = client.with_options(
                timeout=max(1.0, float(timeout_seconds)), max_retries=0
            )
        else:
            kwargs["timeout"] = max(1.0, float(timeout_seconds))
        response = request_client.messages.create(**kwargs)
        raw_text = _response_text(response)
        parsed = _json_object(raw_text)
        validated = validate_model_decision(
            parsed, candidates, min_confidence=min_confidence
        )
        validated["raw_model_decision"] = parsed
        return validated
    except Exception as exc:
        return {
            "status": "model_error",
            "action": ACTION_HOLD,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 0.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": f"Semantic gate model failure; fail-closed hold: {exc}",
            "validation_errors": ["model_error"],
        }

def _resolution_prompt(
    incoming: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    initial_decision: Mapping[str, Any],
) -> str:
    candidate_payload = []
    for row in candidates:
        article = row.get("article") or row
        candidate_payload.append({
            "article": _compact_article(article),
            "retrieval_evidence": row.get("evidence") or {},
        })
    initial = {
        key: initial_decision.get(key)
        for key in (
            "selected_candidate_slug", "same_real_world_event",
            "material_new_update", "independently_newsworthy_followup",
            "confidence", "shared_anchors", "novel_facts", "reason",
        )
    }
    return f"""You are the second and FINAL identity-resolution pass for a national general-news publication gate.

The first pass returned HOLD. That HOLD is not evidence that the incoming story matches any candidate. Re-read the FULL incoming article and only the strongest bounded canonical shortlist below. Resolve the identity now whenever the facts support it.

Use concrete event anchors: named people, exact incident or case, location, governing action, business/event, date/time, distinctive facts, and chronology. Shared state, agency, broad topic, crime type, political office, or generic wording is not enough.

Decision rules:
- If NO candidate is the same concrete real-world event, choose new_story. Do not hold merely because candidates are topically similar.
- If one candidate is the same event with no material new development, choose duplicate_use_existing_canonical.
- If one candidate is the same event and the new facts belong in that living canonical, choose update_existing_canonical.
- A separate new_story for the same event is rare and requires a material independently newsworthy accountability, consequence, policy, investigation, or public-interest question.
- Use hold only when the supplied evidence is genuinely contradictory or too sparse to distinguish identity safely. There will be no third pass.

Return ONLY one JSON object with this exact shape. Put recommended_action first and make every field agree with that action:
{{
  "recommended_action": "new_story",
  "selected_candidate_slug": "candidate slug or null",
  "same_real_world_event": true,
  "material_new_update": false,
  "independently_newsworthy_followup": false,
  "confidence": 0.0,
  "shared_anchors": ["specific shared facts"],
  "novel_facts": ["specific genuinely new facts"],
  "reason": "brief evidence-based explanation"
}}

Consistency is mandatory: update_existing_canonical requires same_real_world_event=true and material_new_update=true. duplicate_use_existing_canonical requires same_real_world_event=true and material_new_update=false.

INITIAL HOLD (context only; do not defer to it):
{json.dumps(initial, ensure_ascii=False, indent=2)}

INCOMING ARTICLE:
{json.dumps(_compact_article(incoming), ensure_ascii=False, indent=2)}

STRONGEST CANONICAL CANDIDATES:
{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}
"""


def adjudicate_resolution(
    client: Any,
    *,
    model: str,
    incoming: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    initial_decision: Mapping[str, Any],
    timeout_seconds: float = 45.0,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, Any]:
    """Run exactly one focused second pass for a validated first-pass HOLD."""
    if not candidates:
        return {
            "status": "no_candidates",
            "action": ACTION_NEW,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 1.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": "No canonical candidates remain in the focused resolution shortlist.",
            "validation_errors": [],
        }
    if client is None or not getattr(client, "messages", None):
        return {
            "status": "model_unavailable",
            "action": ACTION_HOLD,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 0.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": "Terminal resolution model unavailable; fail-closed hold.",
            "validation_errors": ["model_unavailable"],
        }

    request_client = client
    kwargs = {
        "model": model,
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": _resolution_prompt(incoming, candidates, initial_decision)}],
    }
    try:
        if hasattr(client, "with_options"):
            request_client = client.with_options(
                timeout=max(1.0, float(timeout_seconds)), max_retries=0
            )
        else:
            kwargs["timeout"] = max(1.0, float(timeout_seconds))
        response = request_client.messages.create(**kwargs)
        raw_text = _response_text(response)
        parsed = _json_object(raw_text)
        validated = validate_model_decision(
            parsed, candidates, min_confidence=min_confidence
        )
        validated["raw_model_decision"] = parsed
        validated["resolution_pass"] = True
        return validated
    except Exception as exc:
        return {
            "status": "model_error",
            "action": ACTION_HOLD,
            "selected_candidate_slug": "",
            "same_real_world_event": False,
            "material_new_update": False,
            "independently_newsworthy_followup": False,
            "confidence": 0.0,
            "shared_anchors": [],
            "novel_facts": [],
            "reason": f"Terminal resolution model failure; fail-closed hold: {exc}",
            "validation_errors": ["model_error"],
            "resolution_pass": True,
        }

