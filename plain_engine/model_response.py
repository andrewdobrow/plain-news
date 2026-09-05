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
        raise ValueError("Anthropic response contained no text blocks")
    return text
