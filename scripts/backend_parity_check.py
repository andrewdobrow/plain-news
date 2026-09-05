#!/usr/bin/env python3
"""Guard the reusable TCT -> Plain backend parity contract.

This is intentionally a structural/live-wiring check, not a claim that Plain and
TCT have identical editorial policy. Plain nationalizes local rules and omits
TCT product features such as membership/paywall and county relevance.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ENGINE = ROOT / "plain_engine"
GENERATOR = ROOT / "scripts" / "generate.py"
RUNTIME = ROOT / "scripts" / "editorial_runtime.py"
WORKFLOW = ROOT / ".github" / "workflows" / "update.yml"
ASSIGNMENT_PIPELINE = ENGINE / "assignment_pipeline.py"

# Reusable engine modules present in the TCT reference backend. TCT's
# local_relevance and membership_paywall are intentionally replaced/omitted.
REQUIRED_ENGINE_MODULES = {
    "activation", "assignment_editor_shadow", "canonical_story",
    "editorial_decision", "editorial_eligibility", "editorial_engine",
    "editorial_pipeline", "editorial_policy", "editorial_proximity",
    "event_identity", "event_identity_authority", "event_key",
    "fact_extraction", "incident_identity", "model_bakeoff", "model_usage",
    "models", "observability", "production_router", "publication_identity",
    "ranking_recommendations", "registry_repair", "rss_adapter",
    "semantic_material_update", "semantic_publication_gate", "source_identity",
    "story_engine", "story_evolution", "story_importance", "story_lifecycle",
    "story_registry", "story_relationship", "story_resolver", "story_timeline",
    "timeline_coherence", "unified_incident_identity",
}

# Plain-specific production adapters that make the reusable engine actually
# useful for a nationwide aggregation/news-writing pipeline.
REQUIRED_PLAIN_MODULES = {
    "archive_identity", "article_quality", "assignment_pipeline",
    "category_classifier", "editorial_rules", "generation_cache",
    "image_authority", "model_response", "national_relevance", "source_recovery",
}

# These tokens must occur in the live generator. Their presence prevents a
# future refactor from leaving major protections as dormant library code.
REQUIRED_LIVE_GENERATOR_HOOKS = {
    "prefetch_feed_documents": "shared RSS prefetch",
    "build_recovered_content_bank": "full-source recovery/content bank",
    "fetch_guardian_match": "Guardian alternate-source recovery",
    "classify_stories": "national category-fit classifier",
    "apply_pre_generation_materiality": "pre-write duplicate/update gate",
    "run_live_assignment_category": "assignment editor -> exact-source writer",
    "enforce_category_quality": "article quality contract",
    "build_authoritative_image_bank": "source image authority",
    "ensure_item_image": "validated image/fallback selection",
    "apply_editorial_engine": "persistent identity/editorial engine",
    "selected_material_update_commit_entries": "no-silent-loss update commit queue",
    "_semantic_publication_decision": "terminal semantic permalink gate",
    "compose_material_update": "canonical material-update composer",
    "write_homepage_ranking_recommendations": "deterministic ranking observability",
    "recover_category_from_archive": "category failure containment",
    "_write_publication_integrity_report": "final publication integrity report",
    "write_model_usage_report": "model cost/token observability",
    "protected_material_update_pending_recomposition": "protected update canonical recomposition barrier",
}

REQUIRED_ASSIGNMENT_PIPELINE_HOOKS = {
    "ASSIGNMENT_EDITOR_THINKING={'type':'disabled'}": "Sonnet 5 adaptive-thinking disablement",
    "thinking=ASSIGNMENT_EDITOR_THINKING": "assignment request applies non-thinking mode",
    "defer_protected_material_update_quality_failure": "repairable material-update quality deferral",
}

REQUIRED_RUNTIME_HOOKS = {
    "pre_generation_material_update": "validated-update placement priority",
    "story_id": "persistent identity propagation",
    "editorial-audit.json": "per-run audit artifact",
    "editorial-observability.json": "observability artifact",
}

REQUIRED_WORKFLOW_TOKENS = {
    "actions/cache/restore@v4": "persistent generation-cache restore",
    "actions/cache/save@v4": "cache save even after generation",
    "backend_parity_check.py": "parity preflight",
    "validate_package.py": "package validation",
    "pytest": "regression tests",
    "compileall": "production compile check",
    "actions/upload-artifact@v4": "generation diagnostics upload",
    "-size +90M": "repository file-size guard",
}


def _missing_tokens(path: Path, expected: dict[str, str]) -> list[str]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return [f"{token} ({label})" for token, label in expected.items() if token not in text]


def main() -> int:
    failures: list[str] = []
    for name in sorted(REQUIRED_ENGINE_MODULES | REQUIRED_PLAIN_MODULES):
        path = ENGINE / f"{name}.py"
        if not path.exists():
            failures.append(f"missing module: plain_engine/{name}.py")
            continue
        try:
            importlib.import_module(f"plain_engine.{name}")
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(f"module import failed: plain_engine.{name}: {exc}")

    if (ENGINE / "local_relevance.py").exists():
        failures.append("TCT local_relevance.py should be replaced by national_relevance.py")
    if (ENGINE / "membership_paywall.py").exists():
        failures.append("TCT membership_paywall.py should not be part of Plain newsroom parity")

    failures += [f"generator missing live hook: {x}" for x in _missing_tokens(GENERATOR, REQUIRED_LIVE_GENERATOR_HOOKS)]
    failures += [f"assignment pipeline missing hardening: {x}" for x in _missing_tokens(ASSIGNMENT_PIPELINE, REQUIRED_ASSIGNMENT_PIPELINE_HOOKS)]
    failures += [f"runtime missing live hook: {x}" for x in _missing_tokens(RUNTIME, REQUIRED_RUNTIME_HOOKS)]
    failures += [f"workflow missing hardening: {x}" for x in _missing_tokens(WORKFLOW, REQUIRED_WORKFLOW_TOKENS)]

    if failures:
        print("Plain backend parity check FAILED:")
        for failure in failures:
            print(f" - {failure}")
        return 1

    print(
        "Plain backend parity check passed: "
        f"{len(REQUIRED_ENGINE_MODULES)} reusable TCT engine modules, "
        f"{len(REQUIRED_PLAIN_MODULES)} Plain national adapters, "
        f"{len(REQUIRED_LIVE_GENERATOR_HOOKS)} live production hooks, "
        "and CI hardening verified."
    )
    print("Intentional exclusions: TCT local_relevance (replaced) and membership_paywall (product-specific).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
