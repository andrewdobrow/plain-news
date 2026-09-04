"""Publication-isolated assignment-editor shadow experiment for Plain.

This module contains only normalization and artifact-writing helpers. The generator
owns model execution. No function here participates in live publication state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .model_bakeoff import compact_category_output

ASSIGNMENT_EDITOR_SHADOW_SCHEMA_VERSION = 4
ASSIGNMENT_EDITOR_SHADOW_VERSION = "1.13.6.7s"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _coerce_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 1 else None


def normalize_assignment_plan(
    data: Any,
    *,
    source_count: int,
    max_cards: int,
    require_category_fit: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Normalize an editor plan and return deterministic selection diagnostics.

    Invalid/out-of-range/duplicate source indexes are removed rather than silently
    remapped. For topic sections, category-fit adjudication is binding: a source must
    be explicitly accepted by the assignment editor before it can be assigned.
    County sections retain the legacy selection-only contract.
    """
    source_count = max(0, int(source_count or 0))
    max_cards = max(0, int(max_cards or 0))
    raw = data if isinstance(data, dict) else {}
    raw_hero = raw.get("hero") if isinstance(raw.get("hero"), dict) else {}
    raw_cards = raw.get("cards") if isinstance(raw.get("cards"), list) else []

    fit_decisions: List[Dict[str, Any]] = []
    fit_by_index: Dict[int, bool] = {}
    fit_invalid_indexes: List[Any] = []
    fit_duplicate_indexes: List[int] = []
    raw_fit = raw.get("category_fit") if isinstance(raw.get("category_fit"), list) else []
    if require_category_fit:
        for item in raw_fit:
            if not isinstance(item, dict):
                continue
            index = _coerce_index(item.get("source_index"))
            if index is None or index > source_count:
                if item.get("source_index") not in (None, ""):
                    fit_invalid_indexes.append(item.get("source_index"))
                continue
            if index in fit_by_index:
                fit_duplicate_indexes.append(index)
                continue
            fits = item.get("fits_category")
            if not isinstance(fits, bool):
                fit_invalid_indexes.append(item.get("source_index"))
                continue
            fit_by_index[index] = fits
            fit_decisions.append({
                "source_index": index,
                "fits_category": fits,
                "reason": str(item.get("reason") or "").strip(),
            })

    invalid_indexes: List[Any] = []
    duplicate_indexes: List[int] = []
    selected_fit_rejections: List[int] = []
    selected: List[int] = []

    def accepted_for_assignment(index: int | None) -> bool:
        if index is None:
            return False
        if not require_category_fit:
            return True
        if fit_by_index.get(index) is True:
            return True
        selected_fit_rejections.append(index)
        return False

    hero_index = _coerce_index(raw_hero.get("source_index"))
    if hero_index is None or hero_index > source_count:
        if raw_hero.get("source_index") not in (None, ""):
            invalid_indexes.append(raw_hero.get("source_index"))
        hero_index = None
    elif not accepted_for_assignment(hero_index):
        hero_index = None

    hero: Dict[str, Any] = {}
    if hero_index is not None:
        selected.append(hero_index)
        hero = {
            "source_index": hero_index,
            "angle": str(raw_hero.get("angle") or "").strip(),
            "urgency_score": raw_hero.get("urgency_score"),
        }

    cards: List[Dict[str, Any]] = []
    for item in raw_cards:
        if not isinstance(item, dict):
            continue
        index = _coerce_index(item.get("source_index"))
        if index is None or index > source_count:
            if item.get("source_index") not in (None, ""):
                invalid_indexes.append(item.get("source_index"))
            continue
        if not accepted_for_assignment(index):
            continue
        if index in selected:
            duplicate_indexes.append(index)
            continue
        selected.append(index)
        cards.append({
            "source_index": index,
            "angle": str(item.get("angle") or "").strip(),
            "urgency_score": item.get("urgency_score"),
        })
        if len(cards) >= max_cards:
            break

    all_indexes = list(range(1, source_count + 1))
    fit_missing_indexes = [idx for idx in all_indexes if idx not in fit_by_index] if require_category_fit else []
    fit_accepted_indexes = [idx for idx in all_indexes if fit_by_index.get(idx) is True]
    fit_rejected_indexes = [idx for idx in all_indexes if fit_by_index.get(idx) is False]
    fit_complete = bool(
        not require_category_fit
        or (
            len(fit_by_index) == source_count
            and not fit_invalid_indexes
            and not fit_duplicate_indexes
        )
    )
    mapping_valid = bool(
        hero_index is not None
        and not invalid_indexes
        and not duplicate_indexes
        and not selected_fit_rejections
        and fit_complete
    )
    diagnostics = {
        "valid_hero": hero_index is not None,
        "source_mapping_valid": mapping_valid,
        "selected_source_indexes": selected,
        "omitted_source_indexes": [idx for idx in all_indexes if idx not in selected],
        "invalid_source_indexes": invalid_indexes,
        "duplicate_source_indexes": duplicate_indexes,
        "category_fit_required": bool(require_category_fit),
        "category_fit_complete": fit_complete,
        "category_fit_decisions": fit_decisions,
        "category_fit_accepted_source_indexes": fit_accepted_indexes,
        "category_fit_rejected_source_indexes": fit_rejected_indexes,
        "category_fit_missing_source_indexes": fit_missing_indexes,
        "category_fit_invalid_source_indexes": fit_invalid_indexes,
        "category_fit_duplicate_source_indexes": fit_duplicate_indexes,
        "category_fit_selected_rejections": sorted(set(selected_fit_rejections)),
    }
    plan: Dict[str, Any] = {"hero": hero, "cards": cards}
    if require_category_fit:
        plan["category_fit"] = fit_decisions
    return plan, diagnostics


def _ordered_variant_paths(category_key: str, blind_salt: str, paths: Iterable[str]) -> List[str]:
    """Return a deterministic blind ordering for two or more experiment paths."""
    return sorted(
        list(paths),
        key=lambda path: hashlib.sha256(
            f"assignment-editor:{blind_salt}:{category_key}:{path}".encode("utf-8")
        ).digest(),
    )


def _variant_order(category_key: str, blind_salt: str) -> bool:
    """Backward-compatible two-way helper retained for older callers/tests."""
    order = _ordered_variant_paths(
        category_key,
        blind_salt,
        ["current_production", "sonnet5_editor_sonnet45_writer"],
    )
    return order[0] == "current_production"


def _source_title_for_index(source_pool: List[Dict[str, Any]], source_index: Any) -> str:
    index = _coerce_index(source_index)
    if index is None:
        return ""
    pos = index - 1
    if 0 <= pos < len(source_pool):
        return str(source_pool[pos].get("title") or "")
    return ""


def _variant_markdown(label: str, output: Dict[str, Any], source_pool: List[Dict[str, Any]]) -> List[str]:
    lines = [f"### Variant {label}", ""]
    hero = output.get("hero") or {}
    if hero:
        lines.append(f"**Hero:** {hero.get('headline') or '(no headline)'}")
        source_index = hero.get("source_index")
        source_title = _source_title_for_index(source_pool, source_index)
        if source_index:
            note = f"Source #{source_index}"
            if source_title:
                note += f": {source_title}"
            lines.append(f"**Hero source choice:** {note}")
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
                note = f"Source #{source_index}"
                if source_title:
                    note += f": {source_title}"
                lines.append(note)
            if card.get("teaser"):
                lines.append(f"Teaser: {str(card['teaser']).strip()}")
            if card.get("body"):
                lines.extend(["", str(card["body"]).strip()])
            lines.append("")
    return lines


def _selected_sources(output: Dict[str, Any]) -> List[int]:
    indexes: List[int] = []
    hero = output.get("hero") or {}
    hero_idx = _coerce_index(hero.get("source_index"))
    if hero_idx is not None:
        indexes.append(hero_idx)
    for card in output.get("cards") or []:
        if not isinstance(card, dict):
            continue
        idx = _coerce_index(card.get("source_index"))
        if idx is not None:
            indexes.append(idx)
    return indexes


def _comparison_signals(output: Dict[str, Any], source_pool: List[Dict[str, Any]]) -> Dict[str, Any]:
    selected = _selected_sources(output)
    return {
        "selected_source_indexes": selected,
        "duplicate_source_indexes": sorted({idx for idx in selected if selected.count(idx) > 1}),
        "omitted_source_indexes": [
            idx for idx in range(1, len(source_pool) + 1) if idx not in selected
        ],
        "hero_source_index": (output.get("hero") or {}).get("source_index"),
        "hero_headline": str((output.get("hero") or {}).get("headline") or ""),
    }


def write_assignment_editor_artifacts(
    *,
    results: Iterable[Dict[str, Any]],
    report_path: Path,
    review_path: Path,
    answer_key_path: Path,
    production_model: str,
    editor_model: str,
    writer_model: str,
    blind_salt: str,
    enabled: bool,
    opus_editor_model: str | None = None,
) -> Dict[str, Any]:
    """Write final-pipeline-aligned machine, blind-review, and answer-key artifacts.

    The writer supports the historical two-way production-vs-Sonnet comparison and,
    when ``opus_editor_model`` is supplied, the v1.13.6.7s three-way comparison:
    production Sonnet 4.5 vs Sonnet 5 editor vs Opus 5 editor. Both challenger
    architectures use the same production writer and deterministic final pipeline.
    """
    report_path = Path(report_path)
    review_path = Path(review_path)
    answer_key_path = Path(answer_key_path)
    rows = list(results)

    sonnet_path = "sonnet5_editor_sonnet45_writer"
    opus_path = "opus5_editor_sonnet45_writer"
    production_path = "current_production"
    three_way = bool(opus_editor_model)

    report_rows: List[Dict[str, Any]] = []
    answer_categories: Dict[str, Any] = {}
    comparison_noun = "All displayed variants" if three_way else "Both displayed variants"
    review_lines = [
        "# Plain Assignment Editor Shadow Experiment — Final-Pipeline Blind Review",
        "",
        f"The live publisher was not changed. {comparison_noun} are final-pipeline comparison projections: the production path is captured from the actual final live category after normal deterministic corrections, and each publication-isolated shadow path is passed through the same shared eligibility, freshness, publication-quality, identity, suppression, geographic-scope, canonical-surface, and final source-integrity rules before scoring. Raw pre-alignment model outputs remain available only in the machine report.",
        "",
        "Judge the final newsroom result, not verbosity. Score: (1) hero/story choice, (2) supporting-story selection and omissions, (3) ordering, (4) angle/new-development focus, (5) source mapping, (6) headline accuracy and strength, (7) lead/context, (8) factual fidelity, (9) completeness, (10) unnecessary filler, and (11) overall publishability. Record the winning variant letter or Tie before opening the answer key.",
        "",
    ]

    completed = 0
    failed = 0
    for row in rows:
        category_key = str(row.get("category_key") or "unknown")
        category_label = str(row.get("category_label") or category_key)
        source_pool = list(row.get("source_pool") or [])

        raw_baseline = compact_category_output(
            row.get("raw_baseline_output") or row.get("baseline_output")
        )
        final_baseline = compact_category_output(
            row.get("final_baseline_output") or row.get("baseline_output")
        )
        raw_sonnet = compact_category_output(
            row.get("raw_challenger_output") or row.get("challenger_output")
        )
        final_sonnet = compact_category_output(
            row.get("final_challenger_output") or row.get("challenger_output")
        )
        sonnet_error = str(row.get("challenger_error") or "")

        raw_opus = compact_category_output(
            row.get("raw_opus_challenger_output") or row.get("opus_challenger_output")
        ) if three_way else {}
        final_opus = compact_category_output(
            row.get("final_opus_challenger_output") or row.get("opus_challenger_output")
        ) if three_way else {}
        opus_error = str(row.get("opus_challenger_error") or "") if three_way else ""

        variants: Dict[str, Dict[str, Any]] = {
            production_path: {
                "raw_output": raw_baseline,
                "final_output": final_baseline,
                "error": "",
                "editor_model": production_model,
                "writer_model": production_model,
                "assignment_plan": {},
                "assignment_diagnostics": {},
                "alignment_diagnostics": (row.get("alignment_diagnostics") or {}).get("production") or {},
            },
            sonnet_path: {
                "raw_output": raw_sonnet,
                "final_output": final_sonnet,
                "error": sonnet_error,
                "editor_model": editor_model,
                "writer_model": writer_model,
                "assignment_plan": row.get("assignment_plan") or {},
                "assignment_diagnostics": row.get("assignment_diagnostics") or {},
                "alignment_diagnostics": (row.get("alignment_diagnostics") or {}).get("shadow") or {},
                "editor_duration_seconds": row.get("editor_duration_seconds"),
                "editor_actual_model": row.get("editor_actual_model"),
                "writer_duration_seconds": row.get("writer_duration_seconds"),
                "writer_actual_models": row.get("writer_actual_models") or [],
            },
        }
        if three_way:
            variants[opus_path] = {
                "raw_output": raw_opus,
                "final_output": final_opus,
                "error": opus_error,
                "editor_model": opus_editor_model,
                "writer_model": writer_model,
                "assignment_plan": row.get("opus_assignment_plan") or {},
                "assignment_diagnostics": row.get("opus_assignment_diagnostics") or {},
                "alignment_diagnostics": (row.get("alignment_diagnostics") or {}).get("opus_shadow") or {},
                "editor_duration_seconds": row.get("opus_editor_duration_seconds"),
                "editor_actual_model": row.get("opus_editor_actual_model"),
                "writer_duration_seconds": row.get("opus_writer_duration_seconds"),
                "writer_actual_models": row.get("opus_writer_actual_models") or [],
            }

        challenger_errors = [
            variant.get("error")
            for path, variant in variants.items()
            if path != production_path and variant.get("error")
        ]
        if challenger_errors:
            failed += 1
        else:
            completed += 1

        variant_signals: Dict[str, Dict[str, Any]] = {}
        for path, variant in variants.items():
            raw_signals = _comparison_signals(variant["raw_output"], source_pool)
            final_signals = _comparison_signals(variant["final_output"], source_pool)
            alignment = variant.get("alignment_diagnostics") or {}
            final_source_mapping = alignment.get("final_source_mapping") or {}
            assignment_diag = variant.get("assignment_diagnostics") or {}
            mapping_valid = True if path == production_path else bool(
                final_source_mapping.get(
                    "source_mapping_valid", assignment_diag.get("source_mapping_valid")
                )
            )
            variant_signals[path] = {
                "raw": raw_signals,
                "final": final_signals,
                "final_source_mapping": final_source_mapping,
                "source_mapping_valid": mapping_valid,
            }

        report_row = {
            "category_key": category_key,
            "category_label": category_label,
            "source_pool": source_pool,
            "production_model": production_model,
            "editor_model": editor_model,
            "opus_editor_model": opus_editor_model if three_way else None,
            "writer_model": writer_model,
            "raw_baseline_output": raw_baseline,
            "final_baseline_output": final_baseline,
            "baseline_output": final_baseline,
            "assignment_plan": row.get("assignment_plan") or {},
            "assignment_diagnostics": row.get("assignment_diagnostics") or {},
            "raw_challenger_output": raw_sonnet,
            "final_challenger_output": final_sonnet,
            "challenger_output": final_sonnet,
            "challenger_error": sonnet_error or None,
            "editor_duration_seconds": row.get("editor_duration_seconds"),
            "editor_actual_model": row.get("editor_actual_model"),
            "writer_duration_seconds": row.get("writer_duration_seconds"),
            "writer_actual_models": row.get("writer_actual_models") or [],
            "alignment_diagnostics": row.get("alignment_diagnostics") or {},
            "variants": variants,
            "variant_comparison_signals": variant_signals,
        }
        if three_way:
            report_row.update({
                "opus_assignment_plan": row.get("opus_assignment_plan") or {},
                "opus_assignment_diagnostics": row.get("opus_assignment_diagnostics") or {},
                "raw_opus_challenger_output": raw_opus,
                "final_opus_challenger_output": final_opus,
                "opus_challenger_output": final_opus,
                "opus_challenger_error": opus_error or None,
                "opus_editor_duration_seconds": row.get("opus_editor_duration_seconds"),
                "opus_editor_actual_model": row.get("opus_editor_actual_model"),
                "opus_writer_duration_seconds": row.get("opus_writer_duration_seconds"),
                "opus_writer_actual_models": row.get("opus_writer_actual_models") or [],
            })

        # Preserve the historical Sonnet comparison keys while adding explicit
        # per-path signals for the new three-way experiment.
        sonnet_raw = variant_signals[sonnet_path]["raw"]
        sonnet_final = variant_signals[sonnet_path]["final"]
        baseline_raw = variant_signals[production_path]["raw"]
        baseline_final = variant_signals[production_path]["final"]
        report_row["raw_comparison_signals"] = {
            "baseline": baseline_raw,
            "challenger": sonnet_raw,
            "same_hero_source_index": (
                baseline_raw["hero_source_index"] == sonnet_raw["hero_source_index"]
                if baseline_raw["hero_source_index"] is not None
                and sonnet_raw["hero_source_index"] is not None
                else None
            ),
        }
        report_row["comparison_signals"] = {
            "same_hero_source_index": (
                baseline_final["hero_source_index"] == sonnet_final["hero_source_index"]
                if baseline_final["hero_source_index"] is not None
                and sonnet_final["hero_source_index"] is not None
                else None
            ),
            "baseline_selected_source_indexes": baseline_final["selected_source_indexes"],
            "challenger_selected_source_indexes": sonnet_final["selected_source_indexes"],
            "baseline_duplicate_source_indexes": baseline_final["duplicate_source_indexes"],
            "challenger_duplicate_source_indexes": sonnet_final["duplicate_source_indexes"],
            "baseline_omitted_source_indexes": baseline_final["omitted_source_indexes"],
            "challenger_omitted_source_indexes": sonnet_final["omitted_source_indexes"],
            "baseline_final_hero_headline": baseline_final["hero_headline"],
            "challenger_final_hero_headline": sonnet_final["hero_headline"],
            "challenger_source_mapping_valid": variant_signals[sonnet_path]["source_mapping_valid"],
            "challenger_final_source_mapping": variant_signals[sonnet_path]["final_source_mapping"],
        }
        if three_way:
            opus_raw_signals = variant_signals[opus_path]["raw"]
            opus_final_signals = variant_signals[opus_path]["final"]
            report_row["comparison_signals"].update({
                "opus_same_hero_source_index": (
                    baseline_final["hero_source_index"] == opus_final_signals["hero_source_index"]
                    if baseline_final["hero_source_index"] is not None
                    and opus_final_signals["hero_source_index"] is not None
                    else None
                ),
                "opus_selected_source_indexes": opus_final_signals["selected_source_indexes"],
                "opus_duplicate_source_indexes": opus_final_signals["duplicate_source_indexes"],
                "opus_omitted_source_indexes": opus_final_signals["omitted_source_indexes"],
                "opus_final_hero_headline": opus_final_signals["hero_headline"],
                "opus_source_mapping_valid": variant_signals[opus_path]["source_mapping_valid"],
                "opus_final_source_mapping": variant_signals[opus_path]["final_source_mapping"],
            })
            report_row["raw_comparison_signals"]["opus"] = opus_raw_signals
        report_rows.append(report_row)

        paths = list(variants.keys())
        ordered_paths = _ordered_variant_paths(category_key, blind_salt, paths)
        labels = [chr(ord("A") + idx) for idx in range(len(ordered_paths))]
        label_to_path = dict(zip(labels, ordered_paths))
        answer_entry = {
            "category_label": category_label,
            "current_production_model": production_model,
            "shadow_assignment_editor_model": editor_model,
            "shadow_writer_model": writer_model,
            "comparison_stage": "final_pipeline_aligned",
        }
        if three_way:
            answer_entry["opus_assignment_editor_model"] = opus_editor_model
        for label, path in label_to_path.items():
            answer_entry[f"variant_{label.lower()}_path"] = path
        answer_categories[category_key] = answer_entry

        review_lines.extend([f"## {category_label}", "", "### Source pool", ""])
        for index, source in enumerate(source_pool, 1):
            review_lines.append(f"{index}. {source.get('title') or '(untitled source)'}")
        review_lines.append("")
        if challenger_errors:
            review_lines.extend([
                "**At least one publication-isolated shadow path failed for this category. The full comparison is not scoreable.**",
                "",
            ])
        else:
            for label in labels:
                path = label_to_path[label]
                review_lines.extend(_variant_markdown(label, variants[path]["final_output"], source_pool))
            choices = " / ".join(labels + ["Tie"])
            review_lines.extend([
                "**Scorecard**",
                "",
                f"- Hero/story choice: {choices}",
                f"- Supporting-story selection/omissions: {choices}",
                f"- Story ordering: {choices}",
                f"- Angle/new-development focus: {choices}",
                f"- Source mapping: {choices}",
                f"- Headline: {choices}",
                f"- Lead and context: {choices}",
                f"- Factual fidelity: {choices}",
                f"- Completeness: {choices}",
                f"- Least filler: {choices}",
                f"- Overall publishability: {choices}",
                "",
            ])
        review_lines.extend(["---", ""])

    generated_at = _utc_now_iso()
    challenger_architectures = {
        sonnet_path: {
            "assignment_editor_model": editor_model,
            "writer_model": writer_model,
            "editor_role": "story selection, hero/supporting order, angle, urgency, exact source mapping",
            "writer_role": "write only the preassigned single source/angle; no story selection",
        }
    }
    if three_way:
        challenger_architectures[opus_path] = {
            "assignment_editor_model": opus_editor_model,
            "writer_model": writer_model,
            "editor_role": "story selection, hero/supporting order, angle, urgency, exact source mapping",
            "writer_role": "write only the preassigned single source/angle; no story selection",
        }
    report = {
        "schema_version": ASSIGNMENT_EDITOR_SHADOW_SCHEMA_VERSION,
        "experiment_version": ASSIGNMENT_EDITOR_SHADOW_VERSION,
        "generated_at": generated_at,
        "enabled": bool(enabled),
        "publication_isolation": True,
        "comparison_stage": "final_pipeline_aligned",
        "current_production_model": production_model,
        # Backward-compatible Sonnet architecture plus the authoritative map.
        "shadow_architecture": challenger_architectures[sonnet_path],
        "challenger_architectures": challenger_architectures,
        "queued_categories": len(rows),
        "completed_categories": completed,
        "failed_categories": failed,
        "categories": report_rows,
    }
    answer_key = {
        "schema_version": 3 if three_way else 2,
        "experiment_version": ASSIGNMENT_EDITOR_SHADOW_VERSION,
        "generated_at": generated_at,
        "instruction": "Open only after scoring the final-pipeline blind review.",
        "comparison_stage": "final_pipeline_aligned",
        "categories": answer_categories,
    }

    for path in (report_path, review_path, answer_key_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    answer_key_path.write_text(json.dumps(answer_key, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    review_path.write_text("\n".join(review_lines).rstrip() + "\n", encoding="utf-8")
    return report

