"""Guarded national front-page selection for Plain.

Unlike per-section assignment, the front page considers verified section heroes
AND eligible supporting stories. Scoring is deterministic and auditable. A card
must be rewritten/validated as a full article before it can lead the homepage.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re

from .article_quality import MIN_SOURCE_WORDS, publication_quality, word_count
from .national_relevance import classify_national_relevance
from .story_importance import StoryImportanceEngine

BLOCKED_CATEGORIES = frozenset({"sports", "entertainment"})
FRESH_HOURS = 48
BREAKING_HOURS = 60

# These rules apply to concrete DEVELOPMENTS in the headline, not incidental
# background mentions deep in the article. They intentionally do not use
# category alone to determine significance.
_GOV_ACTION = re.compile(
    r"\b(?:president|white house|congress|senate|house|supreme court|"
    r"federal court|federal reserve|fed|pentagon|justice department|"
    r"state department|treasury|administration|trump|u\.s\.|us)\b"
    r".{0,90}\b(?:rules?|overturns?|strikes down|blocks?|orders?|"
    r"allows?|upholds?|permits?|authorizes?|reinstates?|invalidates?|"
    r"signs?|passes?|approves?|vetoes?|halts?|suspends?|"
    r"announces?|launches?|replaces?|fires?|resigns?|"
    r"cuts?|raises?|sanctions?|deploys?|attacks?|"
    r"releases?|declares?|shuts? down|agrees?|votes?)\b",
    re.I,
)
_GOV_REVERSE = re.compile(
    r"\b(?:rules?|overturns?|strikes down|blocks?|signs?|passes?|"
    r"allows?|upholds?|permits?|authorizes?|reinstates?|invalidates?|"
    r"approves?|vetoes?|halts?|orders?|cuts?|raises?|"
    r"announces?|replaces?|launches?)\b.{0,75}\b"
    r"(?:supreme court|congress|senate|white house|president|"
    r"federal reserve|trump|federal government)\b", re.I,
)
_WAR_ACTION = re.compile(
    r"\b(?:strikes?|airstrikes?|invades?|invaded|launches? missiles?|"
    r"missile strikes?|deploys? troops?|ceasefire|peace deal|"
    r"peace agreement|signs? treaty|sanctions?|attacks? (?:nato|us|u\.s\.|"
    r"military|ukraine|russia|iran|israel)|nuclear deal|"
    r"nuclear strike|war begins|war ends|military offensive|"
    r"drone strikes?|drones? (?:hit|strike|attack)|"
    r"launches? \d+ drones?|drone attacks?|"
    r"major offensive|bombards?|shells? (?:capital|city))\b", re.I,
)
_ECON_ACTION = re.compile(
    r"\b(?:federal reserve|fed|interest rates?|inflation|jobs report|"
    r"unemployment|recession|tariffs?|national economy|stock market|"
    r"banking crisis|markets? crash|government shutdown)\b", re.I,
)
_SECURITY_ACTION = re.compile(
    r"\b(?:terrorist attack|terror attack|national security|"
    r"critical infrastructure|cyberattack|data breach|"
    r"nationwide outage|mass shooting|federal emergency|"
    r"major hurricane|earthquake|tsunami)\b", re.I,
)
# A scientific explanation of background grid-frequency behavior is not a
# nationwide power emergency. A power-grid headline needs a concrete outage,
# attack or significant interruption before it receives crisis significance.
_GRID_EMERGENCY = re.compile(
    r"\b(?:grid|power network|electricity network)\b.{0,90}\b"
    r"(?:blackout|outage|shut(?:s|ting)? down|failure|failed|"
    r"attack|hacked|cyberattack|sabotage|widespread disruption|collapse)\b"
    r"|\b(?:blackout|outage|shutdown|failure|attack|hacked|"
    r"cyberattack|sabotage|disruption|collapse)\b.{0,90}\b"
    r"(?:grid|power network|electricity network)\b", re.I,
)

_ANALYSIS = re.compile(
    r"\b(?:could|might|may|opinion|analysis|commentary|"
    r"expert says|experts say|warns?|predicts?|"
    r"enabling|what to know|how (?:to|the)|"
    r"future of|raises questions|explainer)\b", re.I,
)
_SPORTS_EVENT = re.compile(
    r"\b(?:grand prix|formula 1|formula one|premier league|"
    r"nba|nfl|mlb|nhl|ncaa|football game|basketball game|"
    r"tennis match|world cup qualifier|playoff game|"
    r"golf tournament|world series game|championship bout|"
    r"boxing match|soccer match|motorsport race)\b", re.I,
)
_ENTERTAINMENT_EVENT = re.compile(
    r"\b(?:box office|opening weekend|film festival|"
    r"movie premiere|album release|grammy awards|"
    r"reality television|celebrity wedding)\b", re.I,
)
_FOREIGN_GOVERNMENT_CRISIS = re.compile(
    r"\b(?:president|prime minister|parliament|government|"
    r"military junta|opposition leader)\b.{0,80}\b"
    r"(?:impeached|ousted|overthrown|arrested|collapses?|"
    r"resigns?|coup|dissolved)\b", re.I,
)

# A federal enforcement action against one named firm/platform matters, but
# is not automatically equal to an economy-wide rule or nationwide court order.
_TARGETED_ENTITY_ENFORCEMENT = re.compile(
    r"\b(?:crypto(?:currency)? exchange|single (?:bank|company|firm|platform)|"
    r"individual (?:bank|company|firm|platform)|"
    r"specific (?:bank|firm|company|exchange|platform)|"
    r"one (?:bank|company|firm|exchange|platform))\b", re.I,
)
_BROAD_REGULATORY_SCOPE = re.compile(
    r"\b(?:nationwide|industrywide|across the (?:industry|sector)|"
    r"all (?:banks|exchanges|platforms|companies|financial institutions)|"
    r"new federal law|economy.wide)\b", re.I,
)

# Localized foreign shootings can be important World stories, but an
# investigation or suspect update abroad is not automatically a U.S.-national
# security emergency simply because its title contains "mass shooting".
_FOREIGN_LOCAL_SHOOTING = re.compile(
    r"\b(?:mass shooting|shooting suspect|gunman|shooting)\b", re.I,
)
_DIRECT_US_INVOLVEMENT = re.compile(
    r"\b(?:united states|u\.s\.|american(?:s)?|us troops|us forces|"
    r"us government|white house|pentagon|nato)\b", re.I,
)

_SENATE_ELECTION = re.compile(
    r"\b(?:senate race|presidential election|electoral votes?|"
    r"congressional election|national election|"
    r"wins? (?:the )?presidency|enters? (?:the )?senate race)\b", re.I,
)


def parse_publication_timestamp(value):
    """Only real dated timestamps; never use user-facing '11:00 AM ET' strings."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        value = value.strip()
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
            except (ValueError, TypeError, OverflowError):
                return None
    else:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _archive_indexes(archive):
    by_slug, by_story, by_url = {}, {}, {}
    for row in archive or ():
        if not isinstance(row, dict):
            continue
        if row.get("slug"):
            by_slug[str(row["slug"])] = row
        if row.get("story_id"):
            by_story[str(row["story_id"])] = row
        if row.get("source_url"):
            by_url[str(row["source_url"])] = row
    return by_slug, by_story, by_url


def canonical_timestamp(item, archive_row=None):
    """A refreshed lastmod or display label must not resurrect an old event."""
    canonical = archive_row or {}
    if item.get("pre_generation_material_update"):
        stamp = parse_publication_timestamp(item.get("source_published_raw"))
        if stamp:
            return stamp, "validated_source_development"
    for row, prefix in ((canonical, "archive"), (item, "item")):
        if row.get("meaningful_update_validated"):
            for field in ("canonical_last_material_update_at", "last_meaningful_update_at"):
                stamp = parse_publication_timestamp(row.get(field))
                if stamp:
                    return stamp, prefix + ":" + field
    for field in ("first_published", "canonical_first_published_at", "date"):
        stamp = parse_publication_timestamp(canonical.get(field))
        if stamp:
            return stamp, "archive:" + field
    for field in ("first_published", "canonical_first_published_at", "source_published_raw", "published_raw", "date"):
        stamp = parse_publication_timestamp(item.get(field))
        if stamp:
            return stamp, "item:" + field
    return None, "missing_or_display_only"


def _major_event_tier(category_key, headline):
    """Concrete national consequence takes priority over commentary and category."""
    title = str(headline or "")
    if category_key in BLOCKED_CATEGORIES:
        return 0, "category_never_auto_hero"
    # Classification is an advisory signal upstream: a mislabeled F1 race in
    # the U.S. section is still sports and must not become the general hero.
    if _SPORTS_EVENT.search(title) or _ENTERTAINMENT_EVENT.search(title):
        return 0, "sports_entertainment_content_outside_its_category"

    government = bool(_GOV_ACTION.search(title) or _GOV_REVERSE.search(title))
    conflict = bool(_WAR_ACTION.search(title))
    economy = bool(_ECON_ACTION.search(title))
    security = bool(_SECURITY_ACTION.search(title) or _GRID_EMERGENCY.search(title))
    election = bool(_SENATE_ELECTION.search(title))
    foreign_crisis = bool(_FOREIGN_GOVERNMENT_CRISIS.search(title))
    analysis = bool(_ANALYSIS.search(title))
    title_lower = title.casefold()

    if government and not analysis:
        # A one-company permission or export license is not equivalent to a
        # nationwide court ruling or executive action affecting millions.
        if category_key in {"tech", "business"} and (
            re.search(
                r"\b(?:chip sales?|nvidia|tesla|apple|google|microsoft|"
                r"company|startup|export license|single firm)\b", title, re.I
            ) or _TARGETED_ENTITY_ENFORCEMENT.search(title)
        ) and not _BROAD_REGULATORY_SCOPE.search(title):
            return 3, "targeted_federal_entity_action"
        return 5, "concrete_federal_action"
    if foreign_crisis and not analysis:
        return 4, "major_foreign_government_crisis"
    if conflict and not analysis:
        return 5 if re.search(r"\b(?:nato|u\.s\.|us military|united states|american forces)\b", title, re.I) else 4, "major_international_development"
    if economy and not analysis:
        return 4, "macroeconomic_development"
    if security and not analysis:
        if category_key == "world" and _FOREIGN_LOCAL_SHOOTING.search(title) and not _DIRECT_US_INVOLVEMENT.search(title):
            return 2, "foreign_public_safety_without_direct_us_impact"
        return 4 if re.search(r"\b(?:nationwide|national|mass|major|multiple states)\b", title, re.I) or _GRID_EMERGENCY.search(title) else 3, "security_emergency"
    if election and not analysis:
        return 3, "significant_electoral_development"

    # An announced state or company change is news, but not a default national lead.
    if analysis:
        return 1, "analysis_or_speculation"
    if category_key == "politics" and re.search(r"\b(?:election|vote|appoint|senator|cabinet|congress|senate|governor)\b", title_lower):
        return 3, "political_development"
    if category_key == "world":
        return 2, "international_development_without_broad_consequence"
    if category_key == "us":
        return 2, "domestic_development"
    return 1, "limited_national_reach"


def _archive_match(item, by_slug, by_story, by_url):
    slug = str(item.get("_archived_slug") or "").strip()
    sid = str(item.get("story_id") or "").strip()
    url = str(item.get("link") or item.get("source_url") or "").strip()
    return (by_slug.get(slug) if slug else None) or (by_story.get(sid) if sid else None) or (by_url.get(url) if url else None)


def evaluate_front_page_candidates(all_categories, archive=None, *, now=None, heroes_only=False):
    """Return ranked safe candidates and explicit rejected reasons.

    No editorial AI can override the hard exclusion, provenance or age checks.
    Both category heroes and supporting stories compete, including world news.
    """
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    reference = reference.astimezone(timezone.utc)
    by_slug, by_story, by_url = _archive_indexes(archive)
    candidates, rejected = [], []
    importance_engine = StoryImportanceEngine()
    for cat in all_categories or ():
        key = str(cat.get("category_key") or "")
        label = str(cat.get("category_label") or key)
        entries = [("hero", cat.get("hero") or {})]
        if not heroes_only:
            entries.extend(("card", card) for card in cat.get("cards") or [])
        for role, item in entries:
            if not isinstance(item, dict) or not str(item.get("headline") or "").strip():
                continue
            reason = ""
            if key in BLOCKED_CATEGORIES:
                reason = "sports_entertainment_not_eligible"
            elif _SPORTS_EVENT.search(str(item.get("headline") or "")) or _ENTERTAINMENT_EVENT.search(str(item.get("headline") or "")):
                reason = "misclassified_sports_entertainment_content"
            elif item.get("editorial_eligible") is False or item.get("ranking_eligible") is False:
                reason = "editorial_or_ranking_ineligible"
            elif item.get("publication_quality_ok") is False:
                reason = "publication_quality_rejected"
            elif role == "card" and item.get("pre_generation_material_update"):
                reason = "protected_update_not_promoted_from_card"
            elif role == "hero":
                ok, errors = publication_quality(item, hero=True)
                if not ok:
                    reason = "invalid_full_article:" + ",".join(errors)
            if role == "card" and not reason:
                evidence = str(item.get("article_text") or item.get("source_summary") or "")
                if word_count(evidence) < MIN_SOURCE_WORDS:
                    reason = "insufficient_source_evidence_for_full_article"
                elif str(item.get("source_quality") or "").casefold() in {"brief", "thin", "discovery_only"}:
                    reason = "source_not_publication_ready"

            archived = _archive_match(item, by_slug, by_story, by_url)
            stamp, date_basis = canonical_timestamp(item, archived)
            age = None if stamp is None else (reference - stamp).total_seconds() / 3600
            urgency = max(0, min(10, int(item.get("urgency_score") or 0)))
            max_hours = BREAKING_HOURS if urgency >= 8 else FRESH_HOURS
            if not reason and (age is None or age < -1 or age > max_hours):
                reason = "missing_or_stale_canonical_timestamp"
            if reason:
                rejected.append({"headline":item.get("headline"), "category_key":key, "role":role,
                                 "reason":reason, "age_hours":round(age, 2) if age is not None else None,
                                 "timestamp_basis":date_basis})
                continue

            headline = str(item["headline"])
            tier, tier_reason = _major_event_tier(key, headline)
            if not tier:
                rejected.append({"headline":headline, "category_key":key, "role":role, "reason":tier_reason})
                continue
            relevance = classify_national_relevance(text=headline)
            importance = importance_engine.score({
                "titles": [headline],
                "audience_relevance": {"scope": relevance.scope},
            })
            # National significance still matters, but no single keyword
            # group gets an insurmountable 100-point tier advantage. The front
            # page is a time-sensitive news product, not an archive index.
            # An aging story loses prominence on a continuous slope; we still
            # permit a truly major older event when the new pool is weak.
            tier_points = 36.0 * tier
            urgency_points = 4.0 * urgency
            relevance_points = 0.15 * relevance.score
            importance_points = 0.15 * importance.score
            freshness_points = max(-45.0, 44.0 - max(0.0, age) * 2.5)
            # Plain is a U.S. general-news site: a verified U.S.-national
            # development receives a modest audience-fit bonus. This does NOT
            # use the section label as a proxy (foreign stories occasionally
            # arrive misfiled in U.S.), and cannot outrank a much larger world
            # event by itself.
            audience_points = 22.0 if relevance.scope == "us_national" else 0.0
            score = round(
                tier_points + urgency_points + relevance_points
                + importance_points + freshness_points + audience_points, 2
            )
            candidates.append({
                "category": cat, "category_key": key, "category_label": label,
                "item": item, "role": role,
                "headline": headline, "tier": tier, "tier_reason": tier_reason,
                "score": score, "urgency_score": urgency, "importance_score": importance.score,
                "score_breakdown": {
                    "significance": round(tier_points, 2),
                    "urgency": round(urgency_points, 2),
                    "national_relevance": round(relevance_points, 2),
                    "independent_importance": round(importance_points, 2),
                    "freshness": round(freshness_points, 2),
                    "us_audience_fit": round(audience_points, 2),
                },
                "national_relevance": relevance.scope,
                "age_hours": round(age, 2), "timestamp_basis": date_basis,
                "source_timestamp": stamp.isoformat() if stamp else "",
            })
    candidates.sort(key=lambda c: (-c["score"], -c["tier"], c["age_hours"], c["headline"].casefold()))
    return candidates, rejected


def candidate_audit_row(candidate):
    """Keep reports serializable; never put copied full source documents in the audit."""
    fields = ("headline", "category_key", "category_label", "role", "score", "tier",
              "tier_reason", "urgency_score", "importance_score", "national_relevance",
              "age_hours", "timestamp_basis", "source_timestamp", "score_breakdown")
    return {field: candidate.get(field) for field in fields}
