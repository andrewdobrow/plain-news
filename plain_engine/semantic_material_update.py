"""Grounded composition for semantic same-event material updates.

The semantic publication gate decides *identity* and *materiality*.  This module
performs the narrower editorial job that follows a validated material-update
decision: combine the existing canonical Plain article with the incoming report into
one self-contained update while preserving the canonical permalink.

The model receives only the two supplied article texts and the gate's explicit novel
facts.  It never searches, chooses a permalink, or writes files.  The caller validates
the result again and performs all publication mutations deterministically.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping

SEMANTIC_MATERIAL_UPDATE_VERSION = "1.1"

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
    "for", "from", "has", "have", "in", "into", "is", "it", "its", "of",
    "on", "or", "that", "the", "this", "to", "was", "were", "will", "with",
    "after", "before", "about", "over", "under", "near", "said", "says",
    "news", "update", "officials",
}

_TOKEN_CANONICAL = {
    "directed": "directive", "directs": "directive", "direction": "directive",
    "directions": "directive",
    "aligned": "align", "aligning": "align", "alignment": "align",
    "recovered": "recover", "recovery": "recover", "recovering": "recover",
    "found": "find", "finding": "find",
    "receives": "receive", "received": "receive", "receiving": "receive",
}


def _plain(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokens(value: object) -> set[str]:
    tokens = set()
    for raw in re.findall(r"[a-z0-9]+", _plain(value).casefold()):
        if len(raw) < 3 or raw in _STOP_WORDS:
            continue
        tokens.add(_TOKEN_CANONICAL.get(raw, raw))
    return tokens


def _paragraphs(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"\n\s*\n+", text)
        if re.sub(r"\s+", " ", part).strip()
    ]


def _headline_progression(
    headline: object,
    *,
    canonical: Mapping[str, Any],
    incoming: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify that a material update's display headline advances the story state.

    A canonical permalink is intentionally immutable, but its visible headline is
    not.  If the semantic gate has already determined that new facts are material
    enough to replace the canonical article, preserving the old headline makes the
    update effectively invisible to readers.  Require the replacement headline to
    differ from the old canonical wording and surface at least one fact-token that
    was genuinely novel to the canonical article.
    """
    updated = _plain(headline)
    canonical_headline = _plain(canonical.get("headline"))
    canonical_headline_tokens = _tokens(canonical_headline)
    explicit_novelty = " ".join(str(value) for value in decision.get("novel_facts") or [])
    novelty_tokens = _tokens(explicit_novelty) - canonical_headline_tokens
    if not novelty_tokens:
        incoming_headline = str(
            incoming.get("headline") or incoming.get("source_headline") or ""
        )
        novelty_tokens = _tokens(incoming_headline) - canonical_headline_tokens
    updated_tokens = _tokens(updated)
    novelty_hits = sorted(updated_tokens & novelty_tokens)
    same_as_canonical = bool(
        updated
        and canonical_headline
        and updated.casefold() == canonical_headline.casefold()
    )
    return {
        "same_as_canonical": same_as_canonical,
        "novelty_tokens": sorted(novelty_tokens)[:80],
        "novelty_hits": novelty_hits[:40],
        "passed": bool(updated and not same_as_canonical and (not novelty_tokens or novelty_hits)),
    }


def _json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        from json_repair import repair_json

        parsed = json.loads(repair_json(text))
    except Exception:
        try:
            parsed = json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("material-update composer returned no JSON object")
            parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("material-update composer response was not a JSON object")
    return parsed


def _response_text(response: Any) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        value = getattr(block, "text", None)
        if value:
            chunks.append(str(value))
    return "\n".join(chunks).strip()


def validate_material_update(
    payload: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any],
    incoming: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a proposed merged update before the caller may publish it."""
    headline = _plain(payload.get("headline"))
    teaser = _plain(payload.get("teaser"))
    body = str(payload.get("body") or "").strip()
    paragraphs = _paragraphs(body)
    lead = paragraphs[0] if paragraphs else ""
    errors: list[str] = []

    words = re.findall(r"\b\w+\b", _plain(body))
    if not headline or len(headline.split()) < 5:
        errors.append("headline_missing_or_too_short")
    if len(headline) > 180:
        errors.append("headline_too_long")
    if not teaser or len(teaser.split()) < 12:
        errors.append("teaser_missing_or_too_short")
    if len(paragraphs) < 3:
        errors.append("insufficient_paragraphs")
    if len(words) < 170:
        errors.append("body_too_short")
    if len(words) > 700:
        errors.append("body_too_long")
    if len(lead.split()) < 24:
        errors.append("lead_too_short")

    canonical_context = " ".join(
        [
            str(canonical.get("headline") or ""),
            str(canonical.get("teaser") or ""),
            str(canonical.get("body") or ""),
        ]
    )
    incoming_context = " ".join(
        [
            str(incoming.get("headline") or incoming.get("source_headline") or ""),
            str(incoming.get("teaser") or ""),
            str(incoming.get("body") or ""),
            " ".join(str(value) for value in decision.get("novel_facts") or []),
        ]
    )
    canonical_headline_tokens = _tokens(canonical.get("headline"))
    canonical_context_tokens = _tokens(canonical_context)
    incoming_tokens = _tokens(incoming_context)
    novelty_tokens = incoming_tokens - canonical_context_tokens
    lead_tokens = _tokens(lead)
    body_tokens = _tokens(body)

    baseline_hits = sorted(canonical_headline_tokens & lead_tokens)
    novelty_hits = sorted(novelty_tokens & lead_tokens)
    body_baseline_hits = sorted(canonical_context_tokens & body_tokens)
    body_incoming_hits = sorted(incoming_tokens & body_tokens)

    headline_progression = _headline_progression(
        headline, canonical=canonical, incoming=incoming, decision=decision
    )
    if headline_progression["same_as_canonical"]:
        errors.append("headline_not_refreshed_for_material_update")
    if headline_progression["novelty_tokens"] and not headline_progression["novelty_hits"]:
        errors.append("headline_missing_material_development")

    baseline_required = min(2, len(canonical_headline_tokens))
    if baseline_required and len(baseline_hits) < baseline_required:
        errors.append("lead_missing_original_event_context")
    # A material update must state at least one source-distinctive fact in the lead.
    # When token subtraction produces no unique terms, fall back to requiring broad
    # incoming overlap rather than allowing an unsupported generic lead.
    if novelty_tokens:
        if not novelty_hits:
            errors.append("lead_missing_new_development")
    elif len(incoming_tokens & lead_tokens) < min(2, len(incoming_tokens)):
        errors.append("lead_missing_incoming_context")
    if len(body_baseline_hits) < min(5, len(canonical_context_tokens)):
        errors.append("body_missing_canonical_context")
    if len(body_incoming_hits) < min(5, len(incoming_tokens)):
        errors.append("body_missing_incoming_update")

    return {
        "status": "validated" if not errors else "invalid_composition",
        "headline": headline,
        "teaser": teaser,
        "body": "\n\n".join(paragraphs),
        "lead": lead,
        "word_count": len(words),
        "paragraph_count": len(paragraphs),
        "baseline_lead_hits": baseline_hits,
        "novelty_lead_hits": novelty_hits,
        "headline_changed": not headline_progression["same_as_canonical"],
        "headline_novelty_hits": headline_progression["novelty_hits"],
        "headline_novelty_tokens": headline_progression["novelty_tokens"],
        "validation_errors": errors,
    }


def _prompt(
    canonical: Mapping[str, Any],
    incoming: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    canonical_payload = {
        "headline": canonical.get("headline", ""),
        "teaser": canonical.get("teaser", ""),
        "body": canonical.get("body", ""),
        "source_url": canonical.get("source_url", ""),
        "first_published": canonical.get("first_published", ""),
    }
    incoming_payload = {
        "headline": incoming.get("headline", ""),
        "source_headline": incoming.get("source_headline", ""),
        "teaser": incoming.get("teaser", ""),
        "body": incoming.get("body", ""),
        "source_url": incoming.get("source_url", ""),
        "published_at": incoming.get("published_at", ""),
    }
    gate_payload = {
        "shared_anchors": list(decision.get("shared_anchors") or []),
        "novel_facts": list(decision.get("novel_facts") or []),
        "reason": decision.get("reason", ""),
    }
    return f"""You are updating an existing Plain article after a final semantic gate confirmed that a newer report covers the same continuing real-world story and contains a material development.

Write one complete replacement article for the EXISTING CANONICAL PAGE. Use ONLY facts present in the supplied canonical article and incoming update. Do not infer, speculate, add generic context, or use outside knowledge.

Editorial requirements:
- Preserve the canonical story's original context while foregrounding the material development.
- The FIRST paragraph must explicitly explain BOTH what originally happened and what is new now. It must make sense to a reader who never saw the earlier article.
- Write 3 to 6 full paragraphs and roughly 220 to 500 words.
- Use direct, neutral local-news language. No markdown, section headings, datelines, bullet lists, or commentary.
- Do not use direct quotes unless the exact quote appears in the supplied text.
- The headline MUST be refreshed to foreground the material development. Do not reuse or lightly paraphrase the old canonical headline when the story state has changed. It must remain accurate and locally specific.
- Every specific city, county, or monetary claim stated in the headline must also be explicitly stated in the FIRST paragraph.
- The teaser must be one or two complete sentences and explain the new development in context.

Return ONLY this JSON object:
{{
  "headline": "updated headline",
  "teaser": "one- or two-sentence contextual summary",
  "body": "paragraph 1\\n\\nparagraph 2\\n\\nparagraph 3"
}}

EXISTING CANONICAL ARTICLE:
{json.dumps(canonical_payload, ensure_ascii=False, indent=2)}

INCOMING MATERIAL UPDATE:
{json.dumps(incoming_payload, ensure_ascii=False, indent=2)}

SEMANTIC GATE FINDINGS:
{json.dumps(gate_payload, ensure_ascii=False, indent=2)}
"""


def compose_material_update(
    client: Any,
    *,
    model: str,
    canonical: Mapping[str, Any],
    incoming: Mapping[str, Any],
    decision: Mapping[str, Any],
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Request and validate one grounded canonical-page update.

    Any unavailable client, malformed output, or validation failure returns a
    fail-closed result.  The caller must preserve both current pages in that case.
    """
    if client is None or not getattr(client, "messages", None):
        return {
            "status": "model_unavailable",
            "validation_errors": ["model_unavailable"],
            "reason": "Material-update composer unavailable; publication held.",
        }

    request_client = client
    if hasattr(client, "with_options"):
        request_client = client.with_options(
            timeout=max(1.0, float(timeout_seconds)), max_retries=0
        )

    def _request(prompt_text: str) -> dict[str, Any]:
        kwargs = {
            "model": model,
            "max_tokens": 1800,
            "messages": [{"role": "user", "content": prompt_text}],
        }
        if not hasattr(client, "with_options"):
            kwargs["timeout"] = max(1.0, float(timeout_seconds))
        response = request_client.messages.create(**kwargs)
        parsed = _json_object(_response_text(response))
        return validate_material_update(
            parsed,
            canonical=canonical,
            incoming=incoming,
            decision=decision,
        )

    try:
        result = _request(_prompt(canonical, incoming, decision))
        headline_errors = {
            "headline_not_refreshed_for_material_update",
            "headline_missing_material_development",
        }
        errors = set(result.get("validation_errors") or [])
        # A headline-only miss is repairable without weakening any content or
        # identity contract. Give the composer one explicit correction attempt; if
        # it still cannot advance the headline, the caller fails closed.
        if errors and errors.issubset(headline_errors):
            retry_prompt = _prompt(canonical, incoming, decision) + (
                "\n\nREVISION REQUIRED: Your previous article body passed validation, but "
                "the headline did not visibly advance the material development. "
                f"Rejected headline: {result.get('headline','')!r}. "
                "Return the complete JSON object again with a NEW headline that "
                "foregrounds at least one of the semantic gate's novel facts. "
                "Do not reuse the existing canonical headline."
            )
            retry = _request(retry_prompt)
            retry["headline_retry"] = True
            result = retry
        result["composer_version"] = SEMANTIC_MATERIAL_UPDATE_VERSION
        return result
    except Exception as exc:
        return {
            "status": "model_error",
            "validation_errors": ["model_error"],
            "reason": f"Material-update composer failed; publication held: {exc}",
            "composer_version": SEMANTIC_MATERIAL_UPDATE_VERSION,
        }
