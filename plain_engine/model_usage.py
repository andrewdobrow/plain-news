"""Behavior-neutral Anthropic API usage and list-cost observability for Plain.

This module never participates in editorial decisions.  It records only response
usage metadata (token counts, model, call site, timing) and deliberately stores
no prompts, source text, generated text, API keys, or user data.
"""

from __future__ import annotations

import inspect
import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

MODEL_USAGE_REPORT_SCHEMA_VERSION = 1
MODEL_USAGE_OBSERVABILITY_VERSION = "1.13.7.1y"

# Anthropic Claude API standard/global list pricing, USD per 1M tokens.
# Source basis: Anthropic list prices published 2026-06-30.
# The current Plain production model uses 5-minute ephemeral prompt caching.
# Keep the raw token counts in every report so historical workloads can be
# repriced later without changing or rerunning the generator.
_ANTHROPIC_PRICING = {
    "claude-sonnet-4-5": {
        "base_input": 3.00,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.00,
        "cache_read": 0.30,
        "output": 15.00,
        "source_date": "2026-06-30",
        "scope": "Claude API standard/global <=200K context",
    },
    "claude-sonnet-5": {
        "base_input": 2.00,
        "cache_write_5m": 2.50,
        "cache_write_1h": 4.00,
        "cache_read": 0.20,
        "output": 10.00,
        "source_date": "2026-08-21",
        "scope": "Claude API standard/global; $2/$10 pricing is now permanent",
    },
    "claude-opus-5": {
        "base_input": 5.00,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.00,
        "cache_read": 0.50,
        "output": 25.00,
        "source_date": "2026-07-24",
        "scope": "Claude API standard/global",
    },
}

_WORKLOAD_CLASS_BY_FUNCTION = {
    "generate_category_content": "mixed_generation_and_selection",
    "enhance_card": "writing_enrichment",
    "enhance_hero_article": "writing_enrichment",
    "_rewrite_alert_to_article": "writing_rewrite",
    "select_front_page_hero": "editorial_selection",
    "_request_json_index_array": "editorial_selection",
    "classify_stories": "classification",
    "find_canonical_event_entry": "identity_decision",
    "confirm_same_story": "identity_decision",
    "adjudicate_candidates": "identity_decision",
    "compose_material_update": "update_decision",
    "_run_known_canonical_materiality_gate": "material_update_decision",
    "_run_model_bakeoff_variant": "model_bakeoff_challenger",
    "_run_assignment_editor": "assignment_editor",
    "_run_assignment_writer": "assignment_writer",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _usage_value(usage: Any, name: str, default: int = 0) -> int:
    if usage is None:
        return default
    try:
        if isinstance(usage, dict):
            value = usage.get(name, default)
        else:
            value = getattr(usage, name, default)
        return max(0, int(value or 0))
    except Exception:
        return default


def _nested_usage_value(usage: Any, parent: str, child: str) -> int:
    if usage is None:
        return 0
    try:
        node = usage.get(parent) if isinstance(usage, dict) else getattr(usage, parent, None)
        if node is None:
            return 0
        value = node.get(child, 0) if isinstance(node, dict) else getattr(node, child, 0)
        return max(0, int(value or 0))
    except Exception:
        return 0


def _pricing_for_model(model: str) -> Optional[Dict[str, Any]]:
    normalized = str(model or "").strip().lower()
    for prefix, pricing in _ANTHROPIC_PRICING.items():
        if normalized == prefix or normalized.startswith(prefix + "-"):
            return pricing
    return None


def _callsite() -> Dict[str, Any]:
    frame = inspect.currentframe()
    if frame is not None:
        frame = frame.f_back
    while frame is not None:
        filename = frame.f_code.co_filename
        if Path(filename).name != "model_usage.py":
            function = frame.f_code.co_name
            return {
                "file": Path(filename).name,
                "function": function,
                "line": int(frame.f_lineno),
                "workload_class": _WORKLOAD_CLASS_BY_FUNCTION.get(function, "other"),
            }
        frame = frame.f_back
    return {"file": "unknown", "function": "unknown", "line": 0, "workload_class": "other"}


def _round_money(value: float) -> float:
    return round(float(value), 6)


class ModelUsageTracker:
    """Thread-safe accumulator for one generator process."""

    def __init__(self, report_path: Path):
        self.report_path = Path(report_path)
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.started_at = _utc_now_iso()
            self.calls = []
            self.failures = []

    def record_response(self, *, response: Any, requested_model: str, callsite: Dict[str, Any], duration_seconds: float) -> None:
        try:
            usage = getattr(response, "usage", None)
            if usage is None and isinstance(response, dict):
                usage = response.get("usage")
            # Some offline/fake clients intentionally omit usage.  Those calls are
            # still valid; there is simply nothing billable to record.
            if usage is None:
                return

            actual_model = getattr(response, "model", None)
            if actual_model is None and isinstance(response, dict):
                actual_model = response.get("model")
            model = str(actual_model or requested_model or "unknown")

            input_tokens = _usage_value(usage, "input_tokens")
            output_tokens = _usage_value(usage, "output_tokens")
            cache_creation_total = _usage_value(usage, "cache_creation_input_tokens")
            cache_read_tokens = _usage_value(usage, "cache_read_input_tokens")
            cache_write_5m = _nested_usage_value(usage, "cache_creation", "ephemeral_5m_input_tokens")
            cache_write_1h = _nested_usage_value(usage, "cache_creation", "ephemeral_1h_input_tokens")

            # Older SDK responses expose only cache_creation_input_tokens. Plain's
            # prompt cache uses ephemeral cache_control without a 1h TTL, so the
            # unbroken remainder is correctly treated as a 5-minute write.
            detailed_cache_total = cache_write_5m + cache_write_1h
            if cache_creation_total > detailed_cache_total:
                cache_write_5m += cache_creation_total - detailed_cache_total
            elif cache_creation_total == 0 and detailed_cache_total:
                cache_creation_total = detailed_cache_total

            pricing = _pricing_for_model(model)
            estimated_cost = None
            cost_components = None
            if pricing is not None:
                cost_components = {
                    "base_input_usd": _round_money(input_tokens * pricing["base_input"] / 1_000_000),
                    "cache_write_5m_usd": _round_money(cache_write_5m * pricing["cache_write_5m"] / 1_000_000),
                    "cache_write_1h_usd": _round_money(cache_write_1h * pricing["cache_write_1h"] / 1_000_000),
                    "cache_read_usd": _round_money(cache_read_tokens * pricing["cache_read"] / 1_000_000),
                    "output_usd": _round_money(output_tokens * pricing["output"] / 1_000_000),
                }
                estimated_cost = _round_money(sum(cost_components.values()))

            row = {
                "sequence": 0,
                "model": model,
                "requested_model": str(requested_model or model),
                "workload_class": callsite.get("workload_class", "other"),
                "callsite": {
                    "file": callsite.get("file", "unknown"),
                    "function": callsite.get("function", "unknown"),
                    "line": int(callsite.get("line", 0) or 0),
                },
                "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
                "tokens": {
                    "base_input": input_tokens,
                    "cache_write": cache_creation_total,
                    "cache_write_5m": cache_write_5m,
                    "cache_write_1h": cache_write_1h,
                    "cache_read": cache_read_tokens,
                    "total_input_context": input_tokens + cache_creation_total + cache_read_tokens,
                    "output": output_tokens,
                },
                "estimated_list_cost_usd": estimated_cost,
                "cost_components_usd": cost_components,
            }
            with self._lock:
                row["sequence"] = len(self.calls) + 1
                self.calls.append(row)
        except Exception:
            # Telemetry must never affect publishing.
            return

    def record_failure(self, *, requested_model: str, callsite: Dict[str, Any], duration_seconds: float, error: BaseException) -> None:
        try:
            row = {
                "sequence": 0,
                "requested_model": str(requested_model or "unknown"),
                "workload_class": callsite.get("workload_class", "other"),
                "callsite": {
                    "file": callsite.get("file", "unknown"),
                    "function": callsite.get("function", "unknown"),
                    "line": int(callsite.get("line", 0) or 0),
                },
                "duration_seconds": round(max(0.0, float(duration_seconds)), 3),
                "error_type": type(error).__name__,
            }
            with self._lock:
                row["sequence"] = len(self.failures) + 1
                self.failures.append(row)
        except Exception:
            return

    @staticmethod
    def _empty_totals() -> Dict[str, Any]:
        return {
            "requests": 0,
            "base_input_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "total_input_context_tokens": 0,
            "output_tokens": 0,
            "estimated_list_cost_usd": 0.0,
            "unpriced_requests": 0,
        }

    @classmethod
    def _add_row(cls, totals: Dict[str, Any], row: Dict[str, Any]) -> None:
        tokens = row.get("tokens") or {}
        totals["requests"] += 1
        totals["base_input_tokens"] += int(tokens.get("base_input", 0) or 0)
        totals["cache_write_tokens"] += int(tokens.get("cache_write", 0) or 0)
        totals["cache_read_tokens"] += int(tokens.get("cache_read", 0) or 0)
        totals["total_input_context_tokens"] += int(tokens.get("total_input_context", 0) or 0)
        totals["output_tokens"] += int(tokens.get("output", 0) or 0)
        cost = row.get("estimated_list_cost_usd")
        if cost is None:
            totals["unpriced_requests"] += 1
        else:
            totals["estimated_list_cost_usd"] = _round_money(totals["estimated_list_cost_usd"] + float(cost))

    def build_report(self) -> Dict[str, Any]:
        with self._lock:
            calls = [dict(row) for row in self.calls]
            failures = [dict(row) for row in self.failures]
            started_at = self.started_at

        totals = self._empty_totals()
        by_workload = defaultdict(self._empty_totals)
        by_model = defaultdict(self._empty_totals)
        by_callsite = defaultdict(self._empty_totals)
        for row in calls:
            self._add_row(totals, row)
            self._add_row(by_workload[row.get("workload_class", "other")], row)
            self._add_row(by_model[row.get("model", "unknown")], row)
            call = row.get("callsite") or {}
            call_key = f"{call.get('file','unknown')}:{call.get('function','unknown')}"
            self._add_row(by_callsite[call_key], row)

        pricing_catalog = {}
        for model, pricing in _ANTHROPIC_PRICING.items():
            pricing_catalog[model] = {
                "usd_per_million_tokens": {
                    "base_input": pricing["base_input"],
                    "cache_write_5m": pricing["cache_write_5m"],
                    "cache_write_1h": pricing["cache_write_1h"],
                    "cache_read": pricing["cache_read"],
                    "output": pricing["output"],
                },
                "source_date": pricing["source_date"],
                "scope": pricing["scope"],
            }

        return {
            "schema_version": MODEL_USAGE_REPORT_SCHEMA_VERSION,
            "observability_version": MODEL_USAGE_OBSERVABILITY_VERSION,
            "started_at": started_at,
            "finished_at": _utc_now_iso(),
            "provider": "anthropic",
            "billing_note": (
                "Estimated standard/global Claude API list cost from response usage metadata. "
                "Actual invoice cost can differ because of credits, negotiated pricing, regional inference, "
                "or provider billing adjustments. Prompts and generated text are never stored in this report."
            ),
            "pricing_catalog": pricing_catalog,
            "totals": totals,
            "by_workload_class": dict(sorted(by_workload.items())),
            "by_model": dict(sorted(by_model.items())),
            "by_callsite": dict(sorted(by_callsite.items())),
            "failed_requests_without_usage": len(failures),
            "calls": calls,
            "failures": failures,
        }

    def write_report(self) -> Dict[str, Any]:
        report = self.build_report()
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.report_path.with_suffix(self.report_path.suffix + ".tmp")
        temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.report_path)
        return report


class _TrackedMessagesProxy:
    def __init__(self, messages: Any, tracker: ModelUsageTracker):
        self._messages = messages
        self._tracker = tracker

    def create(self, **kwargs: Any) -> Any:
        requested_model = str(kwargs.get("model") or "unknown")
        callsite = _callsite()
        started = time.perf_counter()
        try:
            response = self._messages.create(**kwargs)
        except Exception as exc:
            self._tracker.record_failure(
                requested_model=requested_model,
                callsite=callsite,
                duration_seconds=time.perf_counter() - started,
                error=exc,
            )
            raise
        self._tracker.record_response(
            response=response,
            requested_model=requested_model,
            callsite=callsite,
            duration_seconds=time.perf_counter() - started,
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


class TrackedAnthropicClient:
    """Transparent proxy that preserves the Anthropic client's public behavior."""

    def __init__(self, wrapped: Any, tracker: ModelUsageTracker):
        self._wrapped = wrapped
        self._tracker = tracker
        self.messages = _TrackedMessagesProxy(wrapped.messages, tracker)

    def with_options(self, *args: Any, **kwargs: Any) -> "TrackedAnthropicClient":
        return TrackedAnthropicClient(self._wrapped.with_options(*args, **kwargs), self._tracker)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def instrument_anthropic_client(client: Any, tracker: ModelUsageTracker) -> Any:
    if client is None or isinstance(client, TrackedAnthropicClient):
        return client
    return TrackedAnthropicClient(client, tracker)
