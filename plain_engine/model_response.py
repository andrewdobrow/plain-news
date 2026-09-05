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
