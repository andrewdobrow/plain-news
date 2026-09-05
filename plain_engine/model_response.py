"""Anthropic response compatibility helpers.

Modern Claude responses may include non-text content blocks (for example
ThinkingBlock) before the TextBlock that contains the requested JSON/text.
Never assume response.content[0] exposes .text.
"""
from __future__ import annotations


def extract_model_text(response, *, require_text: bool = True) -> str:
    """Join text-bearing Anthropic content blocks and ignore non-text blocks.

    Supports SDK block objects, mapping-style blocks used by tests/adapters, and
    plain strings. Raises a clear ValueError when a caller requires textual
    output but the response contains none.
    """
    chunks: list[str] = []
    for block in (getattr(response, "content", None) or []):
        if isinstance(block, str):
            value = block
        elif isinstance(block, dict):
            value = block.get("text")
        else:
            value = getattr(block, "text", None)
        if value is not None and str(value).strip():
            chunks.append(str(value))
    text = "\n".join(chunks).strip()
    if require_text and not text:
        block_types = []
        for block in (getattr(response, "content", None) or []):
            if isinstance(block, dict):
                block_type = block.get("type") or "mapping"
            else:
                block_type = getattr(block, "type", None) or type(block).__name__
            block_types.append(str(block_type))
        stop_reason = getattr(response, "stop_reason", None)
        usage = getattr(response, "usage", None)
        if isinstance(usage, dict):
            output_tokens = usage.get("output_tokens")
        else:
            output_tokens = getattr(usage, "output_tokens", None) if usage is not None else None
        raise ValueError(
            "Anthropic response contained no text blocks "
            f"(stop_reason={stop_reason!r}, block_types={block_types!r}, "
            f"output_tokens={output_tokens!r})"
        )
    return text


def parse_first_json_value(text, *, expected_type=None):
    """Parse the first JSON value from a model response, tolerating prose/fences after it."""
    import json
    raw = str(text or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.strip()
    decoder = json.JSONDecoder()
    starts = [i for i, ch in enumerate(raw) if ch in "[{"]
    last_error = None
    for start in starts:
        try:
            value, _ = decoder.raw_decode(raw[start:])
        except Exception as exc:
            last_error = exc
            continue
        if expected_type is not None and not isinstance(value, expected_type):
            continue
        return value
    if last_error is not None:
        raise ValueError(f"Model response contained no parseable JSON value: {last_error}")
    raise ValueError("Model response contained no JSON value")
