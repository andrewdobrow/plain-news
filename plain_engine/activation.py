"""Controlled production activation for proven editorial decisions.

The activation layer intentionally enforces only deterministic duplicate
identity decisions. Broader semantic relationships, ranking, hero selection,
follow-up publishing, and lifecycle presentation remain observe-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, MutableMapping
import copy
import os

from .registry_repair import normalize_identity_title
from .source_identity import normalize_source_identity_url

ACTIVATION_VERSION = "1.1"
DEFAULT_MAX_ACTIONS = 8


class EngineMode(str, Enum):
    SHADOW = "shadow"
    RECOMMEND = "recommend"
    ENFORCE = "enforce"


class ActivationAction(str, Enum):
    NONE = "none"
    SUPPRESS_DUPLICATE = "suppress_duplicate"
    PROTECT_CUSTOM_CANONICAL = "protect_custom_canonical"


@dataclass(frozen=True, slots=True)
class ActivationConfig:
    requested_mode: EngineMode = EngineMode.SHADOW
    max_actions_per_run: int = DEFAULT_MAX_ACTIONS
    kill_switch: bool = False
    minimum_confidence: float = 1.0

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "ActivationConfig":
        values = environ if environ is not None else os.environ
        raw_mode = str(values.get("PLAIN_ENGINE_MODE", "shadow") or "shadow").strip().casefold()
        try:
            mode = EngineMode(raw_mode)
        except ValueError:
            mode = EngineMode.SHADOW
        raw_limit = str(values.get("PLAIN_ENGINE_MAX_ACTIONS", DEFAULT_MAX_ACTIONS) or DEFAULT_MAX_ACTIONS)
        try:
            limit = max(0, int(raw_limit))
        except ValueError:
            limit = DEFAULT_MAX_ACTIONS
        kill_switch = str(values.get("PLAIN_ENGINE_KILL_SWITCH", "") or "").strip().casefold() in {
            "1", "true", "yes", "on",
        }
        return cls(requested_mode=mode, max_actions_per_run=limit, kill_switch=kill_switch)


@dataclass(frozen=True, slots=True)
class ActivationPreflight:
    requested_mode: EngineMode
    effective_mode: EngineMode
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivationRecommendation:
    action: ActivationAction
    enforceable: bool
    evidence: str
    confidence: float
    source_url: str
    headline: str
    source_title_key: str
    incoming_article_id: str
    target_article_id: str
    story_id: str
    canonical_is_custom: bool
    canonical_title: str
    canonical_source: str
    canonical_url: str
    reason: str
    category: str = ""
    placement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "enforceable": self.enforceable,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "source_url": self.source_url,
            "headline": self.headline,
            "source_title_key": self.source_title_key,
            "incoming_article_id": self.incoming_article_id,
            "target_article_id": self.target_article_id,
            "story_id": self.story_id,
            "canonical_is_custom": self.canonical_is_custom,
            "canonical_title": self.canonical_title,
            "canonical_source": self.canonical_source,
            "canonical_url": self.canonical_url,
            "reason": self.reason,
            "category": self.category,
            "placement": self.placement,
        }


@dataclass(slots=True)
class ActivationRun:
    config: ActivationConfig
    preflight: ActivationPreflight
    recommendations: list[ActivationRecommendation] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    before_placements: int = 0
    after_placements: int = 0
    circuit_breaker_tripped: bool = False
    current_regression_gate_passed: bool | None = None

    @property
    def publication_behavior_changed(self) -> bool:
        return bool(self.applied)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activation_version": ACTIVATION_VERSION,
            "requested_mode": self.config.requested_mode.value,
            "effective_mode": self.preflight.effective_mode.value,
            "preflight_passed": self.preflight.passed,
            "preflight_reasons": list(self.preflight.reasons),
            "kill_switch": self.config.kill_switch,
            "maximum_actions_per_run": self.config.max_actions_per_run,
            "minimum_confidence": self.config.minimum_confidence,
            "recommendation_count": len(self.recommendations),
            "enforceable_recommendation_count": sum(r.enforceable for r in self.recommendations),
            "applied_action_count": len(self.applied),
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "placements_before": self.before_placements,
            "placements_after": self.after_placements,
            "publication_behavior_changed": self.publication_behavior_changed,
            "current_regression_gate_passed": self.current_regression_gate_passed,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "applied_actions": list(self.applied),
            "enforcement_allowlist": [
                "custom_canonical_protection",
                "exact_normalized_title_identity",
                "publisher_attribution_title_identity",
                "exact_safe_source_article_identity",
                "guarded_same_story_stage_95",
            ],
            "observe_only_capabilities": [
                "semantic_resolver_merges",
                "incident_identity_merges",
                "follow_up_publication",
                "ranking",
                "hero_selection",
                "lifecycle_presentation",
                "canonical_replacement",
                "article_updates",
            ],
        }


def _registry_health_is_clean(health: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    if not health:
        return False, ["registry health unavailable"]
    reasons: list[str] = []
    if str(health.get("status", "")).casefold() != "clean":
        reasons.append("registry health is not clean")
    for key in (
        "remaining_exact_duplicate_title_groups",
        "remaining_publisher_title_duplicate_groups",
        "remaining_source_identity_groups",
        "remaining_incident_identity_groups",
        "remaining_timeline_coherence_violations",
        "remaining_unified_incident_groups",
    ):
        try:
            value = int(health.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 1
        if value:
            reasons.append(f"{key}={value}")
    try:
        new_quarantines = int(health.get("quarantined_story_count", 0) or 0)
    except (TypeError, ValueError):
        new_quarantines = 1
    if new_quarantines:
        reasons.append(f"new quarantines={new_quarantines}")
    return not reasons, reasons


def build_activation_preflight(
    config: ActivationConfig,
    *,
    previous_regression_report: Mapping[str, Any] | None,
    registry_health: Mapping[str, Any] | None,
) -> ActivationPreflight:
    reasons: list[str] = []
    if config.kill_switch:
        reasons.append("kill switch enabled")
    if config.requested_mode is EngineMode.ENFORCE:
        if not bool((previous_regression_report or {}).get("production_gate_passed", False)):
            reasons.append("previous production regression gate did not pass")
        clean, health_reasons = _registry_health_is_clean(registry_health)
        if not clean:
            reasons.extend(health_reasons)
        if config.max_actions_per_run <= 0:
            reasons.append("maximum actions per run is zero")

    if config.kill_switch or reasons:
        effective = EngineMode.SHADOW if config.requested_mode is EngineMode.ENFORCE else config.requested_mode
        return ActivationPreflight(config.requested_mode, effective, False, tuple(reasons))
    return ActivationPreflight(config.requested_mode, config.requested_mode, True, ())


def _trace_contains(row: Mapping[str, Any], phrase: str) -> bool:
    target = phrase.casefold()
    return any(target in str(item).casefold() for item in (row.get("decision_trace") or ()))


def recommend_activation_action(
    row: Mapping[str, Any],
    *,
    minimum_confidence: float = 1.0,
) -> ActivationRecommendation:
    route = str(row.get("route") or "")
    relationship = str(row.get("relationship") or "")
    confidence = float(row.get("relationship_confidence", 0.0) or 0.0)
    incoming = str(row.get("incoming_article_id") or "")
    target = str(row.get("target_article_id") or "")
    canonical_is_custom = bool(row.get("canonical_is_custom", False))
    incoming_is_custom = bool(row.get("incoming_is_custom", False))
    source_url = str(row.get("source_url") or "")
    headline = str(row.get("headline") or "")
    common = dict(
        confidence=confidence,
        source_url=source_url,
        headline=headline,
        source_title_key=normalize_identity_title(headline),
        incoming_article_id=incoming,
        target_article_id=target,
        story_id=str(row.get("story_id") or ""),
        canonical_is_custom=canonical_is_custom,
        canonical_title=str(row.get("canonical_title") or ""),
        canonical_source=str(row.get("canonical_source") or ""),
        canonical_url=str(row.get("canonical_url") or ""),
    )

    if route != "skip" or relationship != "same_event":
        return ActivationRecommendation(ActivationAction.NONE, False, "", confidence=confidence,
                                        source_url=source_url, headline=headline,
                                        source_title_key=normalize_identity_title(headline),
                                        incoming_article_id=incoming, target_article_id=target,
                                        story_id=str(row.get("story_id") or ""),
                                        canonical_is_custom=canonical_is_custom,
                                        canonical_title=str(row.get("canonical_title") or ""),
                                        canonical_source=str(row.get("canonical_source") or ""),
                                        canonical_url=str(row.get("canonical_url") or ""),
                                        reason="Only same-event skip decisions are eligible")
    if confidence < minimum_confidence:
        return ActivationRecommendation(ActivationAction.NONE, False, "", **common,
                                        reason="Decision confidence is below the activation threshold")
    if not incoming or not target or incoming == target:
        return ActivationRecommendation(ActivationAction.NONE, False, "", **common,
                                        reason="The incoming candidate is already the canonical article")

    if canonical_is_custom and not incoming_is_custom:
        return ActivationRecommendation(
            ActivationAction.PROTECT_CUSTOM_CANONICAL, True, "custom_canonical_protection", **common,
            reason="External duplicate is already covered by a custom Plain canonical article",
        )
    if _trace_contains(row, "Exact source article identity: true"):
        return ActivationRecommendation(
            ActivationAction.SUPPRESS_DUPLICATE, True, "exact_safe_source_article_identity", **common,
            reason="The exact safe source article URL already belongs to the canonical story",
        )
    if _trace_contains(row, "Exact normalized title match: true"):
        return ActivationRecommendation(
            ActivationAction.SUPPRESS_DUPLICATE, True, "exact_normalized_title_identity", **common,
            reason="The normalized source title already belongs to the canonical story",
        )
    return ActivationRecommendation(
        ActivationAction.NONE, False, "", **common,
        reason="Identity evidence is not in the first-release enforcement allowlist",
    )


def build_activation_run(
    rows: Iterable[Mapping[str, Any]],
    *,
    config: ActivationConfig,
    previous_regression_report: Mapping[str, Any] | None,
    registry_health: Mapping[str, Any] | None,
) -> ActivationRun:
    preflight = build_activation_preflight(
        config,
        previous_regression_report=previous_regression_report,
        registry_health=registry_health,
    )
    recommendations = [
        recommendation
        for recommendation in (
            recommend_activation_action(row, minimum_confidence=config.minimum_confidence)
            for row in rows
        )
        if recommendation.action is not ActivationAction.NONE
    ]
    run = ActivationRun(config=config, preflight=preflight, recommendations=recommendations)
    if (
        preflight.effective_mode is EngineMode.ENFORCE
        and len([r for r in recommendations if r.enforceable]) > config.max_actions_per_run
    ):
        run.circuit_breaker_tripped = True
        run.preflight = ActivationPreflight(
            preflight.requested_mode,
            EngineMode.SHADOW,
            False,
            (*preflight.reasons, "maximum actions per run exceeded"),
        )
    return run



def recommendation_from_guarded_suppression(
    record: Mapping[str, Any],
) -> ActivationRecommendation:
    """Convert an existing guarded suppression into an activation recommendation.

    The guarded story engine already made these same-stage, 95%+ decisions in
    production before v1.9.2. This adapter does not broaden the decision rule;
    it only moves the existing action under activation preflight, kill-switch,
    circuit-breaker, and reporting controls.
    """

    raw_confidence = float(record.get("match_confidence", 0.0) or 0.0)
    confidence = raw_confidence / 100.0 if raw_confidence > 1.0 else raw_confidence
    headline = str(record.get("headline") or "")
    return ActivationRecommendation(
        action=ActivationAction.SUPPRESS_DUPLICATE,
        enforceable=bool(record.get("eligible_for_activation", True)),
        evidence="guarded_same_story_stage_95",
        confidence=confidence,
        source_url=str(record.get("source_url") or ""),
        headline=headline,
        source_title_key=normalize_identity_title(
            str(record.get("source_title") or headline)
        ),
        incoming_article_id=str(record.get("slug") or ""),
        target_article_id=str(record.get("matched_prior_slug") or ""),
        story_id=str(record.get("story_id") or ""),
        canonical_is_custom=bool(record.get("matched_prior_is_custom", False)),
        canonical_title=str(record.get("matched_prior_headline") or ""),
        canonical_source=str(record.get("matched_prior_source") or ""),
        canonical_url=str(record.get("matched_prior_source_url") or ""),
        reason=str(record.get("reason") or "Existing guarded same-story suppression"),
        category=str(record.get("category_key") or ""),
        placement=str(record.get("placement") or ""),
    )


def extend_activation_run_with_guarded_suppressions(
    run: ActivationRun,
    records: Iterable[Mapping[str, Any]],
) -> list[ActivationRecommendation]:
    """Append unique guarded recommendations to an activation run."""

    existing = {
        (
            rec.evidence,
            rec.incoming_article_id,
            rec.target_article_id,
            rec.category,
            rec.placement,
        ): rec
        for rec in run.recommendations
    }
    aligned: list[ActivationRecommendation] = []
    for record in records:
        rec = recommendation_from_guarded_suppression(record)
        key = (
            rec.evidence,
            rec.incoming_article_id,
            rec.target_article_id,
            rec.category,
            rec.placement,
        )
        if key not in existing:
            run.recommendations.append(rec)
            existing[key] = rec
        aligned.append(existing[key])
    return aligned


def trip_activation_circuit_breaker(run: ActivationRun, reason: str) -> ActivationRun:
    """Roll an activation run back to shadow mode before publication changes."""

    run.circuit_breaker_tripped = True
    run.applied.clear()
    run.preflight = ActivationPreflight(
        run.preflight.requested_mode,
        EngineMode.SHADOW,
        False,
        (*run.preflight.reasons, reason),
    )
    run.after_placements = run.before_placements
    return run

def _count_placements(categories: Iterable[Mapping[str, Any]]) -> int:
    total = 0
    for category in categories:
        total += int(bool(category.get("hero")))
        total += len(category.get("cards") or ())
    return total


def _recommendation_matches_item(rec: ActivationRecommendation, item: Mapping[str, Any]) -> bool:
    if not item or bool(item.get("is_custom", False)):
        return False
    item_url = normalize_source_identity_url(item.get("link", ""))
    rec_url = normalize_source_identity_url(rec.source_url)
    if item_url and rec_url and item_url == rec_url:
        return True
    source_title = item.get("source_title") or item.get("headline") or item.get("title") or ""
    return bool(rec.source_title_key and normalize_identity_title(source_title) == rec.source_title_key)


def apply_activation_to_categories(
    categories: list[MutableMapping[str, Any]],
    run: ActivationRun,
) -> ActivationRun:
    """Apply allowlisted duplicate suppression to live placements in enforce mode."""

    run.before_placements = _count_placements(categories)
    original_categories = copy.deepcopy(categories)
    if run.preflight.effective_mode is not EngineMode.ENFORCE or run.circuit_breaker_tripped:
        run.after_placements = run.before_placements
        return run

    enforceable = [rec for rec in run.recommendations if rec.enforceable]
    for category in categories:
        category_key = str(category.get("category_key") or "")
        hero = category.get("hero")
        matched_hero = next((rec for rec in enforceable if hero and _recommendation_matches_item(rec, hero)), None)
        if matched_hero:
            replacement = (category.get("cards") or []).pop(0) if category.get("cards") else None
            category["hero"] = replacement
            run.applied.append({
                **matched_hero.to_dict(),
                "category": category_key,
                "placement": "hero",
                "removed_headline": str(hero.get("headline") or hero.get("source_title") or ""),
                "replacement_headline": str((replacement or {}).get("headline") or ""),
            })

        kept_cards = []
        for card in category.get("cards") or []:
            matched = next((rec for rec in enforceable if _recommendation_matches_item(rec, card)), None)
            if matched:
                run.applied.append({
                    **matched.to_dict(),
                    "category": category_key,
                    "placement": "card",
                    "removed_headline": str(card.get("headline") or card.get("source_title") or ""),
                    "replacement_headline": "",
                })
            else:
                kept_cards.append(card)
        category["cards"] = kept_cards

    # One source can appear in several category placements. The circuit breaker is
    # intentionally based on actual mutations as well as unique recommendations.
    if len(run.applied) > run.config.max_actions_per_run:
        trip_activation_circuit_breaker(
            run, "actual placement actions exceeded maximum"
        )
        categories[:] = original_categories
        return run

    run.after_placements = _count_placements(categories)
    return run
