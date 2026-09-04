"""Confidence-gated homepage ranking recommendations for controlled production rollout."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RANKING_RECOMMENDATION_VERSION = "1.13.7.0-shadow"
RANKING_MODE = "recommend"
RANKING_SCHEMA_VERSION = 4
RANKING_DECK_LIMIT = 12
RANKING_FRESH_WINDOW_HOURS = 48.0
RANKING_EXTENDED_WINDOW_HOURS = 60.0
RANKING_EXTENDED_URGENCY_MIN = 8
RANKING_TRANSIENT_MAX_HOURS = 24.0
RANKING_SPORTS_MAX_HOURS = 24.0




_TITLE_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "is", "of", "on", "or", "the", "to", "with", "after", "before",
    "new", "says", "say", "said",
}


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) > 2 and token not in _TITLE_STOPWORDS
    }


def _titles_strongly_overlap(left: str, right: str) -> bool:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = left_tokens & right_tokens
    if len(shared) < 4:
        return False
    containment = len(shared) / min(len(left_tokens), len(right_tokens))
    union = left_tokens | right_tokens
    jaccard = len(shared) / len(union) if union else 0.0
    return containment >= 0.70 or jaccard >= 0.55


def _story_titles(story: Mapping[str, Any]) -> list[str]:
    values = [story.get("canonical_title", ""), story.get("title", "")]
    values.extend(story.get("titles") or [])
    values.extend(
        candidate.get("title", "")
        for candidate in story.get("title_candidates", [])
        if isinstance(candidate, Mapping)
    )
    values.extend(
        entry.get("title", "")
        for entry in story.get("timeline", [])
        if isinstance(entry, Mapping)
    )
    return [str(value or "") for value in values if str(value or "").strip()]


def _same_story(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if left is None or right is None:
        return False
    left_id = str(left.get("story_id") or "").strip()
    right_id = str(right.get("story_id") or "").strip()
    return bool(left_id and left_id == right_id)


def _norm_title(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return " ".join(value.split())


def _norm_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower().rstrip("/")
    query = [
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"oc", "gclid", "fbclid"}
    ]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _iter_stories(registry: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    stories = registry.get("stories") or {}
    if isinstance(stories, Mapping):
        return [story for story in stories.values() if isinstance(story, Mapping)]
    if isinstance(stories, Sequence) and not isinstance(stories, (str, bytes)):
        return [story for story in stories if isinstance(story, Mapping)]
    return []


def _story_indexes(
    registry: Mapping[str, Any],
) -> tuple[
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
    dict[str, Mapping[str, Any]],
]:
    by_id: dict[str, Mapping[str, Any]] = {}
    by_url: dict[str, Mapping[str, Any]] = {}
    by_title: dict[str, Mapping[str, Any]] = {}
    by_slug: dict[str, Mapping[str, Any]] = {}

    for story in _iter_stories(registry):
        story_id = str(story.get("story_id") or "").strip()
        if story_id:
            by_id[story_id] = story

        titles = [story.get("canonical_title", ""), story.get("title", "")]
        titles.extend(candidate.get("title", "") for candidate in story.get("title_candidates", []) if isinstance(candidate, Mapping))
        titles.extend(entry.get("title", "") for entry in story.get("timeline", []) if isinstance(entry, Mapping))
        for title in titles:
            normalized = _norm_title(str(title or ""))
            if normalized:
                by_title.setdefault(normalized, story)

        urls = list(story.get("source_urls") or [])
        urls.extend(story.get("sources") or [])
        urls.extend(entry.get("url", "") for entry in story.get("timeline", []) if isinstance(entry, Mapping))
        for url in urls:
            normalized = _norm_url(str(url or ""))
            if normalized:
                by_url.setdefault(normalized, story)
            try:
                path = urlsplit(str(url or "")).path
                if path.endswith(".html"):
                    slug = path.rsplit("/", 1)[-1][:-5]
                    if slug:
                        by_slug.setdefault(slug, story)
            except ValueError:
                pass

        slugs = [story.get("canonical_slug", ""), story.get("slug", "")]
        slugs.extend(story.get("article_slugs") or [])
        for slug in slugs:
            slug = str(slug or "").strip()
            if slug:
                by_slug.setdefault(slug, story)

    return by_id, by_url, by_title, by_slug


def _archive_indexes(
    archive: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    by_slug: dict[str, Mapping[str, Any]] = {}
    by_title: dict[str, Mapping[str, Any]] = {}
    for entry in archive:
        slug = str(entry.get("slug") or "").strip()
        if slug:
            by_slug[slug] = entry
        title = _norm_title(str(entry.get("headline") or ""))
        if title:
            by_title.setdefault(title, entry)
    return by_slug, by_title


def _explicit_story_id(item: Mapping[str, Any] | None) -> str:
    if not item:
        return ""
    for key in ("editorial_story_id", "_editorial_story_id", "story_id", "_story_id", "persistent_story_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _resolve_story(
    card: Mapping[str, Any],
    *,
    by_id: Mapping[str, Mapping[str, Any]],
    by_url: Mapping[str, Mapping[str, Any]],
    by_title: Mapping[str, Mapping[str, Any]],
    by_slug: Mapping[str, Mapping[str, Any]],
    archive_by_slug: Mapping[str, Mapping[str, Any]],
    archive_by_title: Mapping[str, Mapping[str, Any]],
) -> tuple[
    Mapping[str, Any] | None,
    str,
    Mapping[str, Any] | None,
    str,
    list[str],
    str,
]:
    """Resolve a card to a story only when identity evidence corroborates it.

    Live placements can carry stale persistent story IDs after archive recovery or
    earlier grouping mistakes. A bare ID is therefore not sufficient for ranking
    enforcement. Exact slug, source URL, exact title, or strong title overlap must
    corroborate the relationship. Conflicts remain observable and lock the card in
    place rather than silently influencing deterministic order.
    """

    archive_entry = None
    slug = str(card.get("_archived_slug") or card.get("slug") or "").strip()
    if slug:
        archive_entry = archive_by_slug.get(slug)
    if archive_entry is None:
        archive_entry = archive_by_title.get(_norm_title(str(card.get("headline") or "")))

    candidate_urls = [card.get("link", ""), card.get("source_url", ""), card.get("original_url", "")]
    if archive_entry:
        candidate_urls.extend([
            archive_entry.get("source_url", ""),
            archive_entry.get("link", ""),
            archive_entry.get("original_url", ""),
        ])

    candidate_titles = [card.get("headline", "")]
    if archive_entry:
        candidate_titles.append(archive_entry.get("headline", ""))

    explicit_warning = ""
    explicit_ids = []
    for candidate in (card, archive_entry):
        story_id = _explicit_story_id(candidate)
        if story_id and story_id not in explicit_ids:
            explicit_ids.append(story_id)

    for story_id in explicit_ids:
        story = by_id.get(story_id)
        if story is None:
            explicit_warning = f"persistent_story_id_not_found:{story_id}"
            continue

        evidence: list[str] = []
        if slug and _same_story(by_slug.get(slug), story):
            evidence.append("canonical_slug")
        for url in candidate_urls:
            resolved = by_url.get(_norm_url(str(url or "")))
            if _same_story(resolved, story):
                evidence.append("source_url")
                break
        for title in candidate_titles:
            resolved = by_title.get(_norm_title(str(title or "")))
            if _same_story(resolved, story):
                evidence.append("exact_title")
                break
        if not evidence:
            story_titles = _story_titles(story)
            if any(
                _titles_strongly_overlap(str(title or ""), story_title)
                for title in candidate_titles
                for story_title in story_titles
            ):
                evidence.append("strong_title_overlap")

        if evidence:
            confidence = "high" if evidence[0] != "strong_title_overlap" else "medium"
            return (
                story,
                f"persistent_story_id+{evidence[0]}",
                archive_entry,
                confidence,
                evidence,
                explicit_warning,
            )
        explicit_warning = f"uncorroborated_persistent_story_id:{story_id}"

    if slug and slug in by_slug:
        story = by_slug[slug]
        warning = explicit_warning
        if explicit_ids and str(story.get("story_id") or "") not in explicit_ids:
            warning = (warning + ";" if warning else "") + "persistent_story_id_conflicts_with_slug"
        return story, "canonical_slug", archive_entry, "high", ["canonical_slug"], warning

    for url in candidate_urls:
        story = by_url.get(_norm_url(str(url or "")))
        if story is not None:
            warning = explicit_warning
            if explicit_ids and str(story.get("story_id") or "") not in explicit_ids:
                warning = (warning + ";" if warning else "") + "persistent_story_id_conflicts_with_source"
            return story, "source_url", archive_entry, "high", ["source_url"], warning

    for title in candidate_titles:
        story = by_title.get(_norm_title(str(title or "")))
        if story is not None:
            warning = explicit_warning
            if explicit_ids and str(story.get("story_id") or "") not in explicit_ids:
                warning = (warning + ";" if warning else "") + "persistent_story_id_conflicts_with_title"
            return story, "title", archive_entry, "high", ["exact_title"], warning

    if explicit_warning:
        return None, "uncorroborated_persistent_story_id", archive_entry, "low", [], explicit_warning
    return None, "unmatched", archive_entry, "unmatched", [], ""


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                try:
                    parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
                except ValueError:
                    return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reference_datetime(generated_at: str | None) -> datetime:
    parsed = _parse_datetime(generated_at)
    return parsed or datetime.now(timezone.utc)


def _effective_publication_datetime(
    card: Mapping[str, Any],
    story: Mapping[str, Any] | None,
    archive_entry: Mapping[str, Any] | None,
) -> tuple[datetime | None, str]:
    """Return the newsroom-freshness timestamp without letting routine edits revive old news."""
    for source_name, source in (("archive", archive_entry), ("card", card)):
        if not isinstance(source, Mapping):
            continue
        if source.get("meaningful_update_validated"):
            for key in ("canonical_last_material_update_at", "last_meaningful_update_at"):
                parsed = _parse_datetime(source.get(key))
                if parsed is not None:
                    return parsed, f"{source_name}:{key}"

    if isinstance(archive_entry, Mapping):
        for key in ("first_published", "date"):
            parsed = _parse_datetime(archive_entry.get(key))
            if parsed is not None:
                return parsed, f"archive:{key}"

    if isinstance(story, Mapping):
        latest: datetime | None = None
        for entry in story.get("timeline") or []:
            if not isinstance(entry, Mapping):
                continue
            parsed = _parse_datetime(entry.get("published_at"))
            if parsed is not None and (latest is None or parsed > latest):
                latest = parsed
        if latest is not None:
            return latest, "registry:latest_timeline"

    for key in ("first_published", "published_raw", "published", "date"):
        parsed = _parse_datetime(card.get(key))
        if parsed is not None:
            return parsed, f"card:{key}"
    return None, "missing"


def _age_hours(published_at: datetime | None, reference: datetime) -> float | None:
    if published_at is None:
        return None
    return max(0.0, (reference - published_at).total_seconds() / 3600.0)


def _freshness_score(age_hours: float | None) -> float:
    if age_hours is None:
        return 20.0
    return round(max(0.0, 100.0 * (1.0 - min(age_hours, RANKING_EXTENDED_WINDOW_HOURS) / RANKING_EXTENDED_WINDOW_HOURS)), 2)


def _transient_story(card: Mapping[str, Any]) -> bool:
    text = " ".join((str(card.get("headline") or ""), str(card.get("teaser") or ""))).lower()
    phrases = (
        "flood advisory", "flood warning", "weather advisory", "tornado warning",
        "severe thunderstorm warning", "closed in both directions", "road closed",
        "road closure", "lane closure", "bridge closure", "boil water notice",
    )
    return any(phrase in text for phrase in phrases)


def _importance_signal(story: Mapping[str, Any] | None) -> tuple[int, str, list[dict[str, Any]]]:
    if not isinstance(story, Mapping):
        return 0, "unmatched_story", []
    importance = story.get("importance") or {}
    try:
        score = max(0, min(100, int(importance.get("score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0
    reasons = [dict(row) for row in (importance.get("reasons") or []) if isinstance(row, Mapping)]
    if score:
        return score, "persistent_story_importance", reasons
    breakdown = story.get("score_breakdown") or {}
    try:
        score = max(0, min(100, int(breakdown.get("importance", 0) or 0)))
    except (TypeError, ValueError):
        score = 0
    return score, "persistent_score_breakdown", reasons


def _relevance_signal(story: Mapping[str, Any] | None, category_key: str) -> tuple[int, list[str]]:
    if isinstance(story, Mapping):
        relevance = story.get("audience_relevance") or {}
        try:
            score = max(0, min(100, int(relevance.get("score", 0) or 0)))
        except (TypeError, ValueError):
            score = 0
        regions = [str(value) for value in relevance.get("regions") or [] if str(value).strip()]
        if score:
            return score, regions
    # Category fallback is intentionally broad: all Plain categories are first-class
    # parts of the national product. World is slightly lower only because the core
    # audience is U.S.-based, not because it is ineligible.
    category_key = str(category_key or "").strip().lower()
    fallback = {
        "us": 100, "politics": 100, "business": 92, "tech": 90,
        "world": 82, "sports": 78, "entertainment": 76,
    }
    return fallback.get(category_key, 80), []


def _source_trust_signal(story: Mapping[str, Any] | None) -> int:
    if isinstance(story, Mapping):
        breakdown = story.get("score_breakdown") or {}
        try:
            value = int(breakdown.get("source_trust", 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return max(0, min(100, value))
        values = []
        for candidate in story.get("title_candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            try:
                values.append(max(0, min(100, int(candidate.get("source_trust", 50) or 50))))
            except (TypeError, ValueError):
                pass
        if values:
            return max(values)
    return 50


def _urgency_signal(card: Mapping[str, Any]) -> int:
    try:
        return max(0, min(10, int(card.get("urgency_score", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _ranking_eligibility(card: Mapping[str, Any], age_hours: float | None, urgency: int, category_key: str) -> tuple[bool, str]:
    if card.get("pin_position"):
        return True, "manual_pin_override"
    if age_hours is None:
        return (urgency >= RANKING_EXTENDED_URGENCY_MIN, "undated_high_urgency" if urgency >= RANKING_EXTENDED_URGENCY_MIN else "undated")
    if age_hours > RANKING_EXTENDED_WINDOW_HOURS:
        return False, "older_than_60_hours"
    if _transient_story(card) and age_hours > RANKING_TRANSIENT_MAX_HOURS:
        return False, "expired_transient_story"
    if str(category_key or "") == "sports" and age_hours > RANKING_SPORTS_MAX_HOURS and urgency < 9:
        return False, "routine_sports_older_than_24_hours"
    if age_hours > RANKING_FRESH_WINDOW_HOURS and urgency < RANKING_EXTENDED_URGENCY_MIN:
        return False, "older_than_48_hours_not_urgent_enough"
    if age_hours > RANKING_FRESH_WINDOW_HOURS:
        return True, "high_urgency_extended_window"
    return True, "fresh_window"


def _score_card(
    card: Mapping[str, Any],
    story: Mapping[str, Any] | None,
    archive_entry: Mapping[str, Any] | None,
    *,
    reference_time: datetime,
) -> tuple[int, dict[str, Any]]:
    """Build the v1.13.7.0 shadow editorial score from independent newsroom signals."""
    category_key = str(card.get("cat_key") or card.get("category_key") or "")
    urgency = _urgency_signal(card)
    importance, importance_basis, importance_reasons = _importance_signal(story)
    relevance, regions = _relevance_signal(story, category_key)
    source_trust = _source_trust_signal(story)
    published_at, timestamp_basis = _effective_publication_datetime(card, story, archive_entry)
    age = _age_hours(published_at, reference_time)
    freshness = _freshness_score(age)

    breaking_bonus = 8.0 if card.get("is_breaking") else 0.0
    material_update_bonus = 6.0 if (
        card.get("meaningful_update_validated")
        or (archive_entry or {}).get("meaningful_update_validated")
    ) else 0.0
    weighted = (
        0.34 * importance
        + 0.24 * freshness
        + 0.22 * (urgency * 10)
        + 0.12 * relevance
        + 0.08 * source_trust
        + breaking_bonus
        + material_update_bonus
    )
    score = max(0, min(100, round(weighted)))
    eligible, eligibility_reason = _ranking_eligibility(card, age, urgency, category_key)
    return score, {
        "score": score,
        "basis": "homepage_editorial_shadow_v2",
        "importance": importance,
        "importance_basis": importance_basis,
        "importance_reasons": importance_reasons,
        "freshness": freshness,
        "urgency": urgency,
        "relevance": relevance,
        "regions": regions,
        "source_trust": source_trust,
        "breaking_bonus": breaking_bonus,
        "material_update_bonus": material_update_bonus,
        "age_hours": round(age, 2) if age is not None else None,
        "timestamp_basis": timestamp_basis,
        "deck_eligible": eligible,
        "eligibility_reason": eligibility_reason,
    }


def _identity_key(
    card: Mapping[str, Any],
    story: Mapping[str, Any] | None,
    archive_entry: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """Return a safe canonical identity for recommendation-only deduplication."""
    custom = bool(card.get("is_custom") or card.get("authoritative_custom"))
    slug = str(card.get("_archived_slug") or card.get("slug") or (archive_entry or {}).get("slug") or "").strip()
    if custom:
        return (f"custom:{slug or _norm_title(str(card.get('headline') or ''))}", "custom_article")

    story_id = str((story or {}).get("story_id") or "").strip()
    if story_id:
        return (f"story:{story_id}", "persistent_story_id")

    for candidate in (card, archive_entry or {}):
        for key in ("source_url", "original_url", "link"):
            normalized = _norm_url(str(candidate.get(key) or ""))
            if normalized:
                return (f"source:{normalized}", "source_url")
    if slug:
        return (f"slug:{slug}", "canonical_slug")
    return (f"title:{_norm_title(str(card.get('headline') or ''))}", "normalized_title")


def _category_saturation_penalty(category_key: str, selected: Sequence[Mapping[str, Any]]) -> float:
    if not category_key:
        return 0.0
    count = sum(1 for row in selected if str(row.get("category_key") or "") == category_key)
    if count < 2:
        return 0.0
    if count == 2:
        return 6.0
    if count == 3:
        return 12.0
    return min(24.0, 18.0 + (count - 4) * 3.0)


def _region_saturation_penalty(regions: Sequence[str], selected: Sequence[Mapping[str, Any]]) -> float:
    unique = sorted({str(value) for value in regions if str(value).strip()})
    if len(unique) != 1:
        return 0.0
    region = unique[0]
    count = sum(1 for row in selected if region in (row.get("score_breakdown", {}).get("regions") or []))
    # National coverage should not over-penalize repeated attention to a state during
    # a genuinely major event, so this is deliberately softer than the prior local-news county cap.
    return min(8.0, max(0, count - 3) * 2.0)


def _selection_reason(row: Mapping[str, Any], category_penalty: float, region_penalty: float) -> str:
    breakdown = row.get("score_breakdown") or {}
    pieces = [
        f"importance {breakdown.get('importance', 0)}",
        f"freshness {breakdown.get('freshness', 0)}",
        f"urgency {breakdown.get('urgency', 0)}/10",
    ]
    if breakdown.get("material_update_bonus"):
        pieces.append("validated material update")
    if breakdown.get("breaking_bonus"):
        pieces.append("breaking")
    if category_penalty:
        pieces.append(f"-{category_penalty:g} category saturation")
    if region_penalty:
        pieces.append(f"-{region_penalty:g} regional saturation")
    return "; ".join(pieces)


def _select_editorial_deck(rows: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Greedy shadow deck with soft diversity penalties and no representation quotas."""
    if limit <= 0:
        return []
    eligible = [row for row in rows if row.get("deck_eligible") or row.get("pinned")]
    selected: list[dict[str, Any]] = []
    remaining = list(eligible)

    for position in range(1, min(limit, len(eligible)) + 1):
        fixed = next(
            (
                row for row in remaining
                if row.get("position_locked") and int(row.get("current_position") or 0) == position
            ),
            None,
        )
        if fixed is not None:
            fixed["selection_score"] = float(fixed["score"])
            fixed["diversity_penalty"] = 0.0
            fixed["category_saturation_penalty"] = 0.0
            fixed["region_saturation_penalty"] = 0.0
            fixed["selection_reason"] = f"position locked: {fixed.get('position_lock_reason') or 'manual authority'}"
            selected.append(fixed)
            remaining.remove(fixed)
            continue

        movable = [row for row in remaining if not row.get("position_locked")]
        if not movable:
            break
        scored = []
        for row in movable:
            category_penalty = _category_saturation_penalty(str(row.get("category_key") or ""), selected)
            region_penalty = _region_saturation_penalty(
                row.get("score_breakdown", {}).get("regions") or [], selected
            )
            effective = float(row.get("score") or 0) - category_penalty - region_penalty
            scored.append((effective, category_penalty, region_penalty, row))
        scored.sort(key=lambda value: (
            -value[0],
            -float(value[3].get("score") or 0),
            float(value[3].get("score_breakdown", {}).get("age_hours") or 999999.0),
            int(value[3].get("current_position") or 999999),
            str(value[3].get("headline") or "").lower(),
        ))
        effective, category_penalty, region_penalty, chosen = scored[0]
        chosen["selection_score"] = round(effective, 2)
        chosen["category_saturation_penalty"] = category_penalty
        chosen["region_saturation_penalty"] = region_penalty
        chosen["diversity_penalty"] = round(category_penalty + region_penalty, 2)
        chosen["selection_reason"] = _selection_reason(chosen, category_penalty, region_penalty)
        selected.append(chosen)
        remaining.remove(chosen)

    # Explicit pin positions are presentation authority even in shadow mode.
    pinned = [row for row in selected if row.get("pin_position")]
    if pinned:
        unpinned = [row for row in selected if not row.get("pin_position")]
        for row in sorted(pinned, key=lambda item: int(item.get("pin_position") or 999)):
            position = max(1, int(row.get("pin_position") or 1))
            unpinned.insert(min(position - 1, len(unpinned)), row)
        selected = unpinned[:limit]
    return selected[:limit]


def _build_review_markdown(report: Mapping[str, Any]) -> str:
    hero = report.get("hero") or {}
    lines = [
        "# Plain Homepage Editorial Ranking — Shadow Review",
        "",
        "**Publication behavior changed:** No. This is recommendation-only.",
        "",
        "## Hero",
        "",
        f"- Current: **{hero.get('current_headline') or '(none)'}**",
        f"- Recommended: **{hero.get('recommended_headline') or '(none)'}**",
        f"- Recommendation: {'CHANGE' if hero.get('change_recommended') else 'KEEP'} (not enforced)",
        "",
        "## Top Stories deck",
        "",
        "| Rank | Current | Recommended | Score | Why |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    current = list(report.get("current_deck") or [])
    recommended = list(report.get("recommended_deck") or [])
    items_by_headline = {str(row.get("headline") or ""): row for row in report.get("items") or []}
    length = max(len(current), len(recommended))
    for index in range(length):
        current_headline = current[index] if index < len(current) else "—"
        recommended_headline = recommended[index] if index < len(recommended) else "—"
        row = items_by_headline.get(recommended_headline, {})
        reason = str(row.get("selection_reason") or row.get("score_breakdown", {}).get("eligibility_reason") or "")
        lines.append(
            f"| {index + 1} | {current_headline} | {recommended_headline} | {row.get('score', '')} | {reason} |"
        )
    lines.extend([
        "",
        "## Recommended moves",
        "",
    ])
    moves = list(report.get("recommendations") or [])
    if not moves:
        lines.append("No top-deck moves recommended.")
    else:
        for move in moves:
            lines.append(
                f"- **{move.get('headline')}**: {move.get('from_position')} → {move.get('to_position')} — {move.get('explanation')}"
            )
    lines.extend([
        "",
        "## Guardrails",
        "",
        "- Hero changes: disabled",
        "- Card reordering: disabled",
        "- Manual pins: preserved",
        "- Custom articles: compete normally unless manually pinned",
        "- Stories older than the Top Stories freshness window cannot be promoted back into the deck",
        "- Category/region balance uses soft saturation penalties only; there are no representation quotas",
        "- Identity conflicts remain position-locked and block enforcement readiness",
        "",
    ])
    return "\n".join(lines)


def build_homepage_ranking_recommendations(
    cards: Sequence[Mapping[str, Any]],
    hero: Mapping[str, Any] | None,
    *,
    registry: Mapping[str, Any],
    archive: Sequence[Mapping[str, Any]] = (),
    max_recommendations: int = 12,
    generated_at: str | None = None,
    excluded_candidates: Sequence[Mapping[str, Any]] = (),
    deck_limit: int = RANKING_DECK_LIMIT,
    current_deck_count: int | None = None,
) -> dict[str, Any]:
    """Return a guarded editorial-deck recommendation without mutating the live homepage."""
    original_snapshot = deepcopy(list(cards))
    hero_snapshot = deepcopy(dict(hero or {}))
    reference = _reference_datetime(generated_at)
    by_id, by_url, by_title, by_slug = _story_indexes(registry)
    archive_by_slug, archive_by_title = _archive_indexes(archive)

    unique_rows: list[dict[str, Any]] = []
    identity_to_row: dict[str, dict[str, Any]] = {}
    duplicate_placements: list[dict[str, Any]] = []
    legacy_identity_exclusions: list[dict[str, Any]] = []

    for placement_position, card in enumerate(cards, start=1):
        slug = str(card.get("_archived_slug") or card.get("slug") or "").strip()
        archive_entry_hint = archive_by_slug.get(slug) if slug else archive_by_title.get(
            _norm_title(str(card.get("headline") or ""))
        )
        legacy_status = str(card.get("legacy_identity_status") or (archive_entry_hint or {}).get("legacy_identity_status") or "")
        ranking_eligible = card.get("ranking_eligible")
        if ranking_eligible is None and archive_entry_hint is not None:
            ranking_eligible = archive_entry_hint.get("ranking_eligible")
        unresolved_archive = bool(card.get("_archive_only")) and not _explicit_story_id(card) and not _explicit_story_id(archive_entry_hint)
        if ranking_eligible is False or legacy_status in {
            "legacy_unresolved", "recent_unresolved", "quarantined_live_mismatch"
        } or unresolved_archive:
            legacy_identity_exclusions.append({
                "headline": str(card.get("headline") or ""),
                "placement_position": placement_position,
                "slug": slug,
                "legacy_identity_status": legacy_status or "archive_unresolved",
                "reason": (archive_entry_hint or {}).get("identity_quarantine_reason") or "unresolved_legacy_identity_excluded_from_ranking",
            })
            continue

        story, match_basis, archive_entry, identity_confidence, identity_evidence, identity_warning = _resolve_story(
            card,
            by_id=by_id, by_url=by_url, by_title=by_title, by_slug=by_slug,
            archive_by_slug=archive_by_slug, archive_by_title=archive_by_title,
        )
        identity_key, identity_basis = _identity_key(card, story, archive_entry)
        if identity_key in identity_to_row:
            existing = identity_to_row[identity_key]
            existing["placement_count"] += 1
            existing["placement_categories"].append(str(card.get("cat_key") or card.get("category_key") or ""))
            duplicate_placements.append({
                "headline": str(card.get("headline") or ""),
                "placement_position": placement_position,
                "canonical_position": existing["current_position"],
                "identity": identity_key,
                "identity_basis": identity_basis,
            })
            continue

        score, breakdown = _score_card(card, story, archive_entry, reference_time=reference)
        current_position = len(unique_rows) + 1
        custom = bool(card.get("is_custom") or card.get("authoritative_custom"))
        pin_position = card.get("pin_position")
        # A stable custom slug is sufficient recommendation identity. Custom work is
        # not editorially pinned merely because it was manually authored.
        if custom and str(card.get("_archived_slug") or card.get("slug") or (archive_entry or {}).get("slug") or "").strip():
            if identity_confidence in {"low", "medium", "unmatched"}:
                identity_confidence = "high"
            if not identity_evidence:
                identity_evidence = ["authoritative_custom_slug"]
            identity_warning = ""
        identity_locked = bool(identity_warning) or identity_confidence in {"low", "medium"}
        position_locked = bool(pin_position) or identity_locked
        row = {
            "current_position": current_position,
            "recommended_position": current_position,
            "first_placement_position": placement_position,
            "headline": str(card.get("headline") or ""),
            "category_key": str(card.get("cat_key") or card.get("category_key") or ""),
            "slug": str(card.get("_archived_slug") or card.get("slug") or (archive_entry or {}).get("slug") or ""),
            "story_id": str((story or {}).get("story_id") or ""),
            "score": score,
            "score_breakdown": breakdown,
            "deck_eligible": bool(breakdown.get("deck_eligible")),
            "eligibility_reason": str(breakdown.get("eligibility_reason") or ""),
            "match_basis": match_basis,
            "identity_confidence": identity_confidence,
            "identity_evidence": list(identity_evidence),
            "identity_warning": identity_warning,
            "identity": identity_key,
            "identity_basis": identity_basis,
            "placement_count": 1,
            "placement_categories": [str(card.get("cat_key") or card.get("category_key") or "")],
            "pinned": bool(pin_position),
            "pin_position": pin_position,
            "custom": custom,
            "position_locked": position_locked,
            "position_lock_reason": (
                "pin_position" if pin_position
                else "identity_conflict" if identity_warning
                else "medium_identity_confidence" if identity_confidence == "medium"
                else "low_identity_confidence" if identity_confidence == "low"
                else ""
            ),
        }
        unique_rows.append(row)
        identity_to_row[identity_key] = row

    recommended_deck_rows = _select_editorial_deck(unique_rows, deck_limit)
    recommended_deck_ids = {id(row) for row in recommended_deck_rows}
    current_sorted = sorted(unique_rows, key=lambda row: row["current_position"])
    remainder = [row for row in current_sorted if id(row) not in recommended_deck_ids]
    recommended = list(recommended_deck_rows) + remainder
    for position, row in enumerate(recommended, start=1):
        row["recommended_position"] = position

    if current_deck_count is None:
        current_deck_count = min(deck_limit, len(current_sorted))
    try:
        current_deck_count = max(0, min(deck_limit, int(current_deck_count)))
    except (TypeError, ValueError):
        current_deck_count = min(deck_limit, len(current_sorted))
    current_deck_rows = current_sorted[:current_deck_count]
    current_deck = [row["headline"] for row in current_deck_rows]
    recommended_deck = [row["headline"] for row in recommended_deck_rows]
    current_deck_set = set(current_deck)
    recommended_deck_set = set(recommended_deck)

    moves = []
    for row in recommended:
        if row["current_position"] == row["recommended_position"] or row.get("position_locked"):
            continue
        crosses_deck = (row["headline"] in current_deck_set) != (row["headline"] in recommended_deck_set)
        within_deck = row["headline"] in current_deck_set or row["headline"] in recommended_deck_set
        if not (crosses_deck or within_deck):
            continue
        if row["headline"] in recommended_deck_set and row["headline"] not in current_deck_set:
            action = "recommend_promote_to_top_deck"
        elif row["headline"] in current_deck_set and row["headline"] not in recommended_deck_set:
            action = "recommend_demote_from_top_deck"
        else:
            action = "recommend_reorder_top_deck"
        moves.append({
            "action": action,
            "headline": row["headline"],
            "story_id": row["story_id"],
            "identity": row["identity"],
            "from_position": row["current_position"],
            "to_position": row["recommended_position"],
            "score": row["score"],
            "selection_score": row.get("selection_score", row["score"]),
            "explanation": row.get("selection_reason") or _selection_reason(row, 0.0, 0.0),
            "enforced": False,
        })
    moves.sort(key=lambda move: (abs(move["from_position"] - move["to_position"]), move["score"]), reverse=True)

    # Hero is evaluated from the current hero plus the recommended deck but never changed.
    hero_candidates: list[dict[str, Any]] = []
    hero_card = dict(hero or {})
    if hero_card.get("headline"):
        hero_slug = str(hero_card.get("_archived_slug") or hero_card.get("slug") or "").strip()
        hero_archive = archive_by_slug.get(hero_slug) if hero_slug else archive_by_title.get(_norm_title(str(hero_card.get("headline") or "")))
        hero_story, hero_match, hero_archive, hero_conf, hero_evidence, hero_warning = _resolve_story(
            hero_card,
            by_id=by_id, by_url=by_url, by_title=by_title, by_slug=by_slug,
            archive_by_slug=archive_by_slug, archive_by_title=archive_by_title,
        )
        hero_score, hero_breakdown = _score_card(hero_card, hero_story, hero_archive, reference_time=reference)
        hero_candidates.append({
            "headline": str(hero_card.get("headline") or ""),
            "score": hero_score,
            "score_breakdown": hero_breakdown,
            "identity_confidence": hero_conf,
            "identity_warning": hero_warning,
            "current_hero": True,
        })
    for row in recommended_deck_rows:
        hero_candidates.append({
            "headline": row["headline"],
            "score": row["score"],
            "score_breakdown": row["score_breakdown"],
            "identity_confidence": row["identity_confidence"],
            "identity_warning": row["identity_warning"],
            "current_hero": False,
        })
    viable_hero_candidates = [
        row for row in hero_candidates
        if row.get("score_breakdown", {}).get("deck_eligible")
        and not row.get("identity_warning")
        and row.get("identity_confidence") not in {"low", "medium"}
    ]
    viable_hero_candidates.sort(key=lambda row: (-int(row.get("score") or 0), 0 if row.get("current_hero") else 1, str(row.get("headline") or "").lower()))
    hero_recommended = viable_hero_candidates[0] if viable_hero_candidates else (hero_candidates[0] if hero_candidates else {})
    hero_current = hero_candidates[0] if hero_candidates else {}
    hero_change_recommended = bool(
        hero_recommended
        and hero_current
        and hero_recommended.get("headline") != hero_current.get("headline")
        and int(hero_recommended.get("score") or 0) >= int(hero_current.get("score") or 0) + 8
    )
    hero_effective_recommendation = hero_recommended if hero_change_recommended else hero_current

    assert list(cards) == original_snapshot, "ranking recommendation builder mutated live cards"
    assert dict(hero or {}) == hero_snapshot, "ranking recommendation builder mutated live hero"

    matched = sum(1 for row in unique_rows if row["story_id"])
    high_confidence_matches = sum(1 for row in unique_rows if row["story_id"] and row.get("identity_confidence") == "high")
    fallback = len(unique_rows) - matched
    identity_warnings = [
        {
            "headline": row["headline"], "current_position": row["current_position"],
            "story_id": row["story_id"], "match_basis": row["match_basis"],
            "identity_confidence": row.get("identity_confidence", ""),
            "identity_warning": row.get("identity_warning", ""),
            "position_lock_reason": row.get("position_lock_reason", ""),
        }
        for row in unique_rows
        if row.get("identity_warning") or row.get("identity_confidence") in {"low", "medium"}
    ]
    excluded_candidates = [dict(row) for row in excluded_candidates]
    recent_high_urgency_exclusions = [
        row for row in excluded_candidates
        if int(row.get("urgency_score", 0) or 0) >= 8
        and (row.get("age_hours") is None or float(row.get("age_hours") or 0) < 24)
    ]
    stale_deck_candidates_excluded = [row for row in unique_rows if not row.get("deck_eligible")]
    match_ready = bool(len(unique_rows) > 0 and matched / len(unique_rows) >= 0.8)
    confidence_ready = not identity_warnings
    exclusion_ready = not recent_high_urgency_exclusions
    enforcement_ready = match_ready and confidence_ready and exclusion_ready

    report = {
        "schema_version": RANKING_SCHEMA_VERSION,
        "version": RANKING_RECOMMENDATION_VERSION,
        "mode": RANKING_MODE,
        "generated_at": reference.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "publication_behavior_changed": False,
        "hero": {
            "current_headline": str(hero_current.get("headline") or ""),
            "recommended_headline": str(hero_effective_recommendation.get("headline") or ""),
            "current_score": int(hero_current.get("score") or 0),
            "recommended_score": int(hero_effective_recommendation.get("score") or 0),
            "top_scoring_candidate_headline": str(hero_recommended.get("headline") or ""),
            "top_scoring_candidate_score": int(hero_recommended.get("score") or 0),
            "score_margin": int(hero_recommended.get("score") or 0) - int(hero_current.get("score") or 0),
            "change_threshold": 8,
            "change_recommended": hero_change_recommended,
            "observe_only": True,
            "changed": False,
        },
        "controls": {
            "hero_changes_enabled": False,
            "card_reordering_enabled": False,
            "custom_articles_position_locked": False,
            "custom_articles_compete_normally": True,
            "custom_pin_positions_preserved": True,
            "deduplicate_cross_category_placements": True,
            "unresolved_legacy_archive_excluded": True,
            "uncorroborated_story_ids_position_locked": True,
            "identity_conflicts_block_enforcement": True,
            "freshness_contract_applied_to_recommendations": True,
            "soft_category_saturation": True,
            "soft_region_saturation": True,
            "region_representation_quotas": False,
            "deck_limit": deck_limit,
            "max_reported_recommendations": max_recommendations,
        },
        "summary": {
            "input_placements": len(cards),
            "legacy_identity_placements_excluded": len(legacy_identity_exclusions),
            "unique_cards_observed": len(unique_rows),
            "cards_observed": len(unique_rows),
            "duplicate_placements_excluded": len(duplicate_placements),
            "registry_matches": matched,
            "high_confidence_registry_matches": high_confidence_matches,
            "identity_warning_count": len(identity_warnings),
            "fallback_scores": fallback,
            "registry_match_rate": round((matched / len(unique_rows)), 4) if unique_rows else 1.0,
            "high_confidence_match_rate": round((high_confidence_matches / len(unique_rows)), 4) if unique_rows else 1.0,
            "recommended_moves": len(moves),
            "unchanged_positions": sum(1 for row in unique_rows if row["current_position"] == row["recommended_position"]),
            "reported_moves": min(len(moves), max_recommendations),
            "deck_limit": deck_limit,
            "current_deck_count": len(current_deck),
            "recommended_deck_count": len(recommended_deck),
            "stale_or_ineligible_candidates_excluded_from_deck": len(stale_deck_candidates_excluded),
            "excluded_candidates": len(excluded_candidates),
            "recent_high_urgency_exclusions": len(recent_high_urgency_exclusions),
            "hero_change_recommended": hero_change_recommended,
            "enforcement_readiness": "eligible_for_review" if enforcement_ready else "not_ready",
            "enforcement_readiness_reason": (
                "At least 80% of unique cards matched persistent story IDs, every identity was corroborated, and no recent high-urgency candidate was filtered"
                if enforcement_ready
                else (
                    "Persistent story identity conflict or non-high-confidence story match detected"
                    if identity_warnings
                    else "Recent high-urgency candidate was excluded before ranking"
                    if recent_high_urgency_exclusions
                    else "Fewer than 80% of unique cards matched persistent story IDs"
                )
            ),
        },
        "current_order": [row["headline"] for row in current_sorted],
        "recommended_order": [row["headline"] for row in recommended],
        "current_deck": current_deck,
        "recommended_deck": recommended_deck,
        "recommendations": moves[:max_recommendations],
        "items": recommended,
        "stale_or_ineligible_deck_candidates": [
            {
                "headline": row["headline"], "current_position": row["current_position"],
                "age_hours": row.get("score_breakdown", {}).get("age_hours"),
                "eligibility_reason": row.get("eligibility_reason"), "score": row.get("score"),
            }
            for row in stale_deck_candidates_excluded
        ],
        "excluded_duplicate_placements": duplicate_placements,
        "excluded_legacy_identity_placements": legacy_identity_exclusions,
        "identity_warnings": identity_warnings,
        "excluded_candidates": excluded_candidates,
        "recent_high_urgency_exclusions": recent_high_urgency_exclusions,
    }
    return report


def write_homepage_ranking_recommendations(
    cards: Sequence[Mapping[str, Any]],
    hero: Mapping[str, Any] | None,
    *,
    registry_path: Path,
    archive: Sequence[Mapping[str, Any]],
    output_path: Path,
    max_recommendations: int = 12,
    excluded_candidates: Sequence[Mapping[str, Any]] = (),
    review_path: Path | None = None,
    deck_limit: int = RANKING_DECK_LIMIT,
    current_deck_count: int | None = None,
) -> dict[str, Any]:
    registry = _load_json(Path(registry_path), {})
    report = build_homepage_ranking_recommendations(
        cards,
        hero,
        registry=registry,
        archive=archive,
        max_recommendations=max_recommendations,
        excluded_candidates=excluded_candidates,
        deck_limit=deck_limit,
        current_deck_count=current_deck_count,
    )
    _atomic_write_json(Path(output_path), report)
    if review_path is not None:
        review_path = Path(review_path)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        temp = review_path.with_suffix(review_path.suffix + ".tmp")
        temp.write_text(_build_review_markdown(report), encoding="utf-8")
        temp.replace(review_path)
    return report
