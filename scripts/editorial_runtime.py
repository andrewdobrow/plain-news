"""Bridge Plain's generated presentation data into the persistent editorial engine.

The live site may continue displaying a recognized/unchanged current story. The
engine's job here is to establish durable story identity, reject non-news,
prevent duplicate same-story slots inside a category, and emit an audit trail
for publication/archive decisions.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from plain_engine import EditorialEngine, write_editorial_observability


VALID_MODES = {"off", "shadow", "enforce"}


def _source_name(item: dict[str, Any]) -> str:
    explicit = str(item.get("source_name") or "").strip()
    if explicit:
        return explicit
    link = str(item.get("link") or "").strip()
    domain = urlparse(link).netloc.casefold().split(":", 1)[0].removeprefix("www.")
    return domain or "Unknown"


def _entry(item: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "title": str(item.get("source_title") or item.get("headline") or "").strip(),
        "link": str(item.get("link") or "").strip(),
        # Ground identity/materiality in recovered source evidence, never model copy.
        "body": str(item.get("article_text") or item.get("source_summary") or item.get("body") or item.get("teaser") or "").strip(),
        "summary": str(item.get("source_summary") or item.get("teaser") or "").strip(),
    }
    raw_pub = str(item.get("source_published_raw") or "").strip()
    if raw_pub:
        parsed = parsedate(raw_pub)
        if parsed:
            entry["published_parsed"] = parsed
    return entry


def _decision_payload(result: Any, *, mode: str, slot: str) -> dict[str, Any]:
    action = (getattr(result.action, "name", "") or str(result.action)).casefold()
    route = {
        "publish_new": "generate_new",
        "update_existing": "update_existing",
        "replace_canonical": "replace_canonical",
        "ignore": "skip",
        "hold_for_review": "hold",
    }.get(action, "unknown")
    return {
        "mode": mode,
        "slot": slot,
        "action": action,
        "route": route,
        "story_id": result.story_id,
        "event_key": result.event_key,
        "eligible": bool(result.eligible),
        "eligibility_status": result.eligibility_status,
        "eligibility_reasons": list(result.eligibility_reasons),
        "relationship": result.relationship,
        "relationship_confidence": result.relationship_confidence,
        "relationship_reason": result.relationship_reason,
        "source_class": result.source_class,
        "source_trust": result.source_trust,
        "new_facts": list(result.new_facts),
        "canonical_title": result.canonical_title,
        "canonical_source": result.canonical_source,
        "canonical_url": result.canonical_url,
        "decision_trace": list(result.decision_trace),
        "follow_up_candidate_story_id": result.follow_up_candidate_story_id,
        "follow_up_candidate_confidence": result.follow_up_candidate_confidence,
        "follow_up_candidate_milestones": list(result.follow_up_candidate_milestones),
        "follow_up_candidate_reason_codes": list(result.follow_up_candidate_reason_codes),
        "follow_up_candidate_trace": list(result.follow_up_candidate_trace),
        "follow_up_candidate_mode": result.follow_up_candidate_mode,
    }


def apply_editorial_engine(
    all_categories: list[dict[str, Any]],
    *,
    output_dir: str | Path,
    mode: str | None = None,
) -> dict[str, Any]:
    """Annotate generated stories and enforce deterministic publication hygiene.

    ``shadow`` records decisions only. ``enforce`` additionally removes items
    rejected as non-news/listings and duplicate same-story cards *within the same
    category*. Cross-category placement is intentionally preserved because Plain
    has distinct editorial sections and the homepage already performs its own
    presentation-level deduplication.
    """
    root = Path(output_dir)
    selected_mode = (mode or os.environ.get("PLAIN_ENGINE_MODE", "enforce")).strip().lower()
    if selected_mode not in VALID_MODES:
        selected_mode = "shadow"

    audit: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": selected_mode,
        "items": [],
        "counts": {"processed": 0, "rejected": 0, "same_category_duplicates": 0, "errors": 0},
    }
    if selected_mode == "off":
        (root / "editorial-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        return audit

    state_path = root / "editorial-state.json"
    registry_path = root / "story-registry.json"
    try:
        engine = EditorialEngine.load(state_path, registry_path=registry_path)
    except Exception as exc:
        # Do not silently throw away a corrupt persistent registry/state in enforce
        # mode. Shadow mode may still run with a clean in-memory instance for
        # diagnostics without changing what gets published.
        audit["state_load_error"] = f"{type(exc).__name__}: {exc}"
        if selected_mode == "enforce":
            raise
        engine = EditorialEngine(registry_path=registry_path)

    for category in all_categories:
        cat_key = str(category.get("category_key") or "")
        seen_story_ids: set[str] = set()
        kept_story_locations: dict[str, tuple[str, int | None]] = {}

        original_hero = category.get("hero") if isinstance(category.get("hero"), dict) else None
        cards = [c for c in category.get("cards", []) if isinstance(c, dict)]
        slots: list[tuple[str, dict[str, Any]]] = []
        if original_hero:
            slots.append(("hero", original_hero))
        slots.extend((f"card:{i}", card) for i, card in enumerate(cards))

        accepted_hero: dict[str, Any] | None = None
        accepted_cards: list[dict[str, Any]] = []

        for slot, item in slots:
            audit["counts"]["processed"] += 1
            link = str(item.get("link") or "").strip()
            title = str(item.get("headline") or item.get("source_title") or "").strip()
            if item.get("_archive_recovery"):
                # This is already-published canonical copy used only as failure
                # containment. Do not re-ingest Plain's own permalink as a new
                # source observation or mutate persistent story identity.
                audit["items"].append({
                    "mode": selected_mode,
                    "slot": slot,
                    "category_key": cat_key,
                    "headline": title,
                    "link": link,
                    "action": "ignore",
                    "route": "archive_recovery",
                    "story_id": str(item.get("story_id") or ""),
                    "event_key": str(item.get("event_key") or ""),
                    "eligible": True,
                })
                if slot == "hero" and accepted_hero is None:
                    accepted_hero = item
                else:
                    accepted_cards.append(item)
                continue
            try:
                if not title or not link:
                    raise ValueError("generated item is missing headline or source link")
                result = engine.process(_entry(item), source=_source_name(item))
                decision = _decision_payload(result, mode=selected_mode, slot=slot)
                decision.update({"category_key": cat_key, "headline": title, "link": link})
                item["story_id"] = result.story_id
                item["event_key"] = result.event_key
                item["editorial_action"] = decision["action"]
                item["editorial_relationship"] = result.relationship
                item["editorial_source_trust"] = result.source_trust
                item["editorial_eligible"] = bool(result.eligible)
                audit["items"].append(decision)

                reject = not result.eligible
                duplicate_in_category = bool(result.story_id and result.story_id in seen_story_ids)
                if reject:
                    audit["counts"]["rejected"] += 1
                if duplicate_in_category:
                    audit["counts"]["same_category_duplicates"] += 1

                def _placement_priority(candidate: dict[str, Any], candidate_slot: str) -> tuple[int, int, int]:
                    protected_update = int(bool(candidate.get("pre_generation_material_update")))
                    hero_slot = int(candidate_slot == "hero")
                    urgency = int(candidate.get("urgency_score") or 0)
                    # TCT parity: a validated material update outranks an ordinary
                    # same-story clone even when the older clone occupied the hero.
                    return protected_update, hero_slot, urgency

                if selected_mode != "enforce":
                    should_keep = True
                elif reject:
                    should_keep = False
                elif duplicate_in_category and result.story_id:
                    location = kept_story_locations.get(result.story_id)
                    existing_item = None
                    existing_slot = "card"
                    if location:
                        if location[0] == "hero":
                            existing_item = accepted_hero
                            existing_slot = "hero"
                        elif location[1] is not None and 0 <= location[1] < len(accepted_cards):
                            existing_item = accepted_cards[location[1]]
                    if existing_item is not None and _placement_priority(item, slot) > _placement_priority(existing_item, existing_slot):
                        item["editorial_replaced_same_story_clone"] = True
                        existing_item["editorial_replaced_by_material_update"] = bool(item.get("pre_generation_material_update"))
                        if location and location[0] == "hero":
                            accepted_hero = item
                            kept_story_locations[result.story_id] = ("hero", None)
                        elif location and location[1] is not None:
                            accepted_cards[location[1]] = item
                            kept_story_locations[result.story_id] = ("card", location[1])
                        should_keep = False  # replacement was already committed above
                    else:
                        should_keep = False
                else:
                    should_keep = True

                if result.story_id:
                    seen_story_ids.add(result.story_id)

                if should_keep:
                    if slot == "hero" and accepted_hero is None:
                        accepted_hero = item
                        if result.story_id:
                            kept_story_locations[result.story_id] = ("hero", None)
                    else:
                        accepted_cards.append(item)
                        if result.story_id:
                            kept_story_locations[result.story_id] = ("card", len(accepted_cards) - 1)
            except Exception as exc:
                audit["counts"]["errors"] += 1
                item["editorial_error"] = f"{type(exc).__name__}: {exc}"
                audit["items"].append({
                    "mode": selected_mode,
                    "slot": slot,
                    "category_key": cat_key,
                    "headline": title,
                    "link": link,
                    "error": item["editorial_error"],
                })
                # Fail open for one malformed generated item so a temporary feed
                # problem cannot erase an entire category page.
                if slot == "hero" and accepted_hero is None:
                    accepted_hero = item
                else:
                    accepted_cards.append(item)

        if selected_mode == "enforce":
            # If the original hero was rejected/duplicate, promote the strongest
            # surviving card. Never leave a category structurally empty.
            if accepted_hero is None and accepted_cards:
                accepted_hero = accepted_cards.pop(0)
                accepted_hero["editorial_promoted_from_card"] = True
            if accepted_hero is None and original_hero is not None:
                accepted_hero = original_hero
                accepted_hero["editorial_fail_open"] = True
            if accepted_hero is not None:
                category["hero"] = accepted_hero
            category["cards"] = accepted_cards

    engine.save(state_path)
    audit["registry_health"] = engine.get_registry_health()
    observability = write_editorial_observability(
        engine,
        audit["items"],
        root / "editorial-observability.json",
        registry_path=str(registry_path),
        mode=selected_mode,
    )
    audit["observability"] = {
        "engine": observability.get("engine", {}),
        "story_count": observability.get("stories", {}).get("count", 0),
    }
    audit_path = root / "editorial-audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return audit
