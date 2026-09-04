import hashlib
import re

from .fact_extraction import ExtractedArticleFacts


def _slug(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _identity_suffix(facts: ExtractedArticleFacts) -> str:
    """Return a stable suffix for event keys that lack identity anchors."""
    identity = str(facts.article_id or "").strip()
    if not identity:
        identity = "|".join(
            (
                str(facts.source or ""),
                *map(str, facts.event_types),
                *map(str, facts.locations),
                *map(str, facts.agencies),
                *map(str, facts.entities),
                *map(str, facts.facts),
            )
        )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]


def generate_event_key(
    facts: ExtractedArticleFacts,
) -> str:
    """Generate a deterministic event key without reusing generic identities.

    Keys with an event type and a location retain their shared semantic form so
    duplicate coverage can still resolve at the event level. Sparse articles
    that would otherwise become one global ``unknown-event``, ``fire``,
    ``traffic-crash`` or city-level ``missing-person`` key receive a stable
    article-derived suffix instead.
    """

    if not facts.event_types:
        return f"unknown-event-{_identity_suffix(facts)}"

    parts = [
        _slug(facts.event_types[0]),
    ]

    if facts.locations:
        parts.append(_slug(facts.locations[0]))
    else:
        parts.append(_identity_suffix(facts))

    # Crash, fire and missing-person labels identify a class of incidents, not
    # one incident. Keep the useful semantic prefix for observability, but add a
    # stable article-derived suffix so a city-level key can never merge unrelated
    # events. Named missing-person continuity is resolved later by stronger
    # participant/source identity rather than by a generic city key.
    if parts[0] in {"traffic-crash", "fire", "missing-person"}:
        parts.append(_identity_suffix(facts))

    if "cats rescued" in facts.facts:
        parts.append("cats")

    return "-".join(parts)
