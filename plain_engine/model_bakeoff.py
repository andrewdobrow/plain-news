"""Blind, publication-isolated model comparison artifacts for Plain.

The generator owns API execution and passes only completed category outputs here.
This module writes review artifacts; it never participates in live editorial logic.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

MODEL_BAKEOFF_SCHEMA_VERSION = 1
MODEL_BAKEOFF_VERSION = "1.13.6.5"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    keep = (
        "headline",
        "teaser",
        "body",
        "urgency_score",
        "published",
        "source_index",
    )
    return {key: item.get(key) for key in keep if item.get(key) not in (None, "")}


def compact_category_output(data: Any) -> Dict[str, Any]:
    """Keep only editorial output fields needed for blind review."""
    if not isinstance(data, dict):
        return {"hero": {}, "cards": []}
    return {
        "hero": _clean_item(data.get("hero")),
        "cards": [_clean_item(card) for card in (data.get("cards") or []) if isinstance(card, dict)],
    }


def _variant_order(category_key: str, blind_salt: str) -> bool:
    """Return True when baseline should be Variant A; vary assignment by category."""
    digest = hashlib.sha256(f"{blind_salt}:{category_key}".encode("utf-8")).digest()
    return bool(digest[0] & 1)


def _source_title_for_index(source_pool: List[Dict[str, Any]], source_index: Any) -> str:
    try:
        index = int(source_index) - 1
    except (TypeError, ValueError):
        return ""
    if 0 <= index < len(source_pool):
        return str(source_pool[index].get("title") or "")
    return ""


def _variant_markdown(label: str, output: Dict[str, Any], source_pool: List[Dict[str, Any]]) -> List[str]:
    lines = [f"### Variant {label}", ""]
    hero = output.get("hero") or {}
    if hero:
        lines.append(f"**Hero:** {hero.get('headline') or '(no headline)'}")
        source_index = hero.get("source_index")
        source_title = _source_title_for_index(source_pool, source_index)
        if source_index:
            source_note = f"Source #{source_index}"
            if source_title:
                source_note += f": {source_title}"
            lines.append(f"**Hero source choice:** {source_note}")
        if hero.get("urgency_score") not in (None, ""):
            lines.append(f"**Urgency:** {hero.get('urgency_score')}")
        lines.append("")
        if hero.get("body"):
            lines.extend([str(hero["body"]).strip(), ""])
    else:
        lines.extend(["**Hero:** No usable hero returned.", ""])

    cards = output.get("cards") or []
    if cards:
        lines.extend(["**Additional stories**", ""])
        for idx, card in enumerate(cards, 1):
            lines.append(f"**{idx}. {card.get('headline') or '(no headline)'}**")
            source_index = card.get("source_index")
            source_title = _source_title_for_index(source_pool, source_index)
            if source_index:
                source_note = f"Source #{source_index}"
                if source_title:
                    source_note += f": {source_title}"
                lines.append(source_note)
            if card.get("teaser"):
                lines.append(f"Teaser: {str(card['teaser']).strip()}")
            if card.get("body"):
                lines.extend(["", str(card["body"]).strip()])
            lines.append("")
    return lines


def write_bakeoff_artifacts(
    *,
    results: Iterable[Dict[str, Any]],
    report_path: Path,
    review_path: Path,
    answer_key_path: Path,
    baseline_model: str,
    challenger_model: str,
    blind_salt: str,
    enabled: bool,
) -> Dict[str, Any]:
    """Write machine report, blind review, and a separate answer key."""
    report_path = Path(report_path)
    review_path = Path(review_path)
    answer_key_path = Path(answer_key_path)
    rows = list(results)

    report_rows: List[Dict[str, Any]] = []
    answer_categories: Dict[str, Any] = {}
    review_lines = [
        "# Plain Model Bake-Off — Blind Review",
        "",
        "The live publisher was not changed by this comparison. Each category below compares the current production model output with a challenger generated from the same category source packet. Model identities are intentionally omitted here.",
        "",
        "For each category, judge: (1) hero/story choice, (2) headline accuracy and strength, (3) lead/context, (4) factual fidelity, (5) completeness, (6) unnecessary filler, and (7) overall publishability. Record A, B, or Tie before opening the answer key.",
        "",
    ]

    completed = 0
    failed = 0
    for row in rows:
        category_key = str(row.get("category_key") or "unknown")
        category_label = str(row.get("category_label") or category_key)
        source_pool = list(row.get("source_pool") or [])
        baseline = compact_category_output(row.get("baseline_output"))
        challenger = compact_category_output(row.get("challenger_output"))
        error = str(row.get("challenger_error") or "")
        baseline_is_a = _variant_order(category_key, blind_salt)

        variant_a = baseline if baseline_is_a else challenger
        variant_b = challenger if baseline_is_a else baseline
        variant_a_model = baseline_model if baseline_is_a else challenger_model
        variant_b_model = challenger_model if baseline_is_a else baseline_model

        if error:
            failed += 1
        else:
            completed += 1

        baseline_hero = baseline.get("hero") or {}
        challenger_hero = challenger.get("hero") or {}
        baseline_card_sources = [card.get("source_index") for card in baseline.get("cards", []) if card.get("source_index")]
        challenger_card_sources = [card.get("source_index") for card in challenger.get("cards", []) if card.get("source_index")]
        report_rows.append({
            "category_key": category_key,
            "category_label": category_label,
            "source_pool": source_pool,
            "baseline_model": baseline_model,
            "challenger_model": challenger_model,
            "baseline_output": baseline,
            "challenger_output": challenger,
            "challenger_error": error or None,
            "challenger_duration_seconds": row.get("challenger_duration_seconds"),
            "comparison_signals": {
                "same_hero_source_index": (
                    baseline_hero.get("source_index") == challenger_hero.get("source_index")
                    if baseline_hero.get("source_index") is not None and challenger_hero.get("source_index") is not None
                    else None
                ),
                "baseline_hero_word_count": len(str(baseline_hero.get("body") or "").split()),
                "challenger_hero_word_count": len(str(challenger_hero.get("body") or "").split()),
                "baseline_card_source_indexes": baseline_card_sources,
                "challenger_card_source_indexes": challenger_card_sources,
            },
        })

        answer_categories[category_key] = {
            "category_label": category_label,
            "variant_a_model": variant_a_model,
            "variant_b_model": variant_b_model,
        }

        review_lines.extend([f"## {category_label}", "", "### Source pool", ""])
        for index, source in enumerate(source_pool, 1):
            review_lines.append(f"{index}. {source.get('title') or '(untitled source)'}")
        review_lines.append("")
        if error:
            review_lines.extend([
                "**Challenger generation failed for this category. This category is not scoreable.**",
                "",
            ])
        else:
            review_lines.extend(_variant_markdown("A", variant_a, source_pool))
            review_lines.extend(_variant_markdown("B", variant_b, source_pool))
            review_lines.extend([
                "**Scorecard**",
                "",
                "- Hero/story choice: A / B / Tie",
                "- Headline: A / B / Tie",
                "- Lead and context: A / B / Tie",
                "- Factual fidelity: A / B / Tie",
                "- Completeness: A / B / Tie",
                "- Least filler: A / B / Tie",
                "- Overall publishability: A / B / Tie",
                "",
            ])
        review_lines.extend(["---", ""])

    report = {
        "schema_version": MODEL_BAKEOFF_SCHEMA_VERSION,
        "bakeoff_version": MODEL_BAKEOFF_VERSION,
        "generated_at": _utc_now_iso(),
        "enabled": bool(enabled),
        "publication_isolation": True,
        "baseline_model": baseline_model,
        "challenger_model": challenger_model,
        "challenger_configuration": {
            "thinking": "disabled",
            "max_tokens": 8000,
            "note": "Thinking is disabled for the first bake-off to compare direct structured writing/selection against the non-thinking production baseline; the larger max_tokens accommodates Sonnet 5's newer tokenizer.",
        },
        "queued_categories": len(rows),
        "completed_categories": completed,
        "failed_categories": failed,
        "categories": report_rows,
    }
    answer_key = {
        "schema_version": 1,
        "bakeoff_version": MODEL_BAKEOFF_VERSION,
        "generated_at": report["generated_at"],
        "instruction": "Open only after scoring the blind review.",
        "categories": answer_categories,
    }

    for path in (report_path, review_path, answer_key_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    answer_key_path.write_text(json.dumps(answer_key, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_path.write_text("\n".join(review_lines).rstrip() + "\n", encoding="utf-8")
    return report
