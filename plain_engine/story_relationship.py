"""Relationship classification for distinct events that belong to one story."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .timeline_coherence import incoming_entry_conflicts_with_story

_WORD_RE = re.compile(r"[a-z0-9]+")
_NUMBER_RE = re.compile(r"\b\d+\b")
_STOP = {
    "the", "and", "for", "with", "from", "after", "before", "into",
    "county", "news", "update", "says", "said", "earlier", "reported",
}
_FOLLOW_UP_MILESTONES = {
    "arrest": {"arrest", "arrested", "charge", "charged", "charges"},
    "identified": {"identified", "named", "identity"},
    "death": {"dies", "died", "dead", "killed", "death"},
    "sentencing": {"sentenced", "sentence", "sentencing"},
    "court_action": {"trial", "hearing", "lawsuit", "sues", "sued", "indicted"},
    "investigation": {"investigation", "reopen", "reopens", "reopened"},
    "recovery": {"recovered", "found", "located", "rescued", "reunited"},
    "release": {"released", "discharged", "returns", "returned"},
    "resolution": {"contained", "resolved", "cleared", "reopened"},
}
# Observe-only milestone detection uses phrase-aware regular expressions.  These
# patterns are intentionally stricter than the live follow-up vocabulary above:
# generic words such as ``breaks``, ``ending`` and ``wins`` caused false-positive
# production diagnostics (for example, "expert breaks down" and "happy ending").
_ADVISORY_FOLLOW_UP_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "arrest": (
        r"\barrest(?:ed|s|ing)?\b",
        r"\bcharg(?:e|ed|es|ing)\b",
        r"\btaken into custody\b",
    ),
    "identified": (
        r"\bidentified\b",
        r"\bidentity (?:was )?released\b",
        r"\bnamed as\b",
    ),
    "death": (
        r"\b(?:dies|died|dead|deadly|killed|death|fatal|fatality|fatalities)\b",
        r"\b(?:suspected|apparent) suicide\b",
        r"\bdied by suicide\b",
    ),
    "sentencing": (r"\b(?:sentenced|sentencing)\b",),
    "court_action": (
        r"\b(?:trial|hearing|lawsuit|sues|sued|indicted|indictment|arraigned|convicted)\b",
        r"\bpleads? guilty\b",
        r"\bfound guilty\b",
    ),
    "investigation": (
        r"\binvestigation\b",
        r"\breopen(?:s|ed|ing)? (?:the )?investigation\b",
    ),
    "recovery": (
        r"\b(?:safely located|located safe|found safe|found alive|recovered|rescued|reunited)\b",
        r"\bmissing .{0,40}\b(?:located|found)\b",
    ),
    "release": (
        r"\breleased (?:from|on)\b",
        r"\bdischarged\b",
        r"\breturns? home\b",
        r"\breturned home\b",
    ),
    "resolution": (
        r"\b(?:contained|resolved|cleared)\b",
        r"\b(?:road|bridge|causeway|lane|lanes|highway|intersection) (?:reopens?|reopened)\b",
        r"\b(?:all )?lanes? (?:reopen|reopened)\b",
        r"\bevacuation (?:order )?lifted\b",
    ),
    "approval": (
        r"\b(?:approves?|approved|adopts?|adopted)\b",
        r"\bpass(?:es|ed)? (?:the )?(?:measure|ordinance|budget|plan|proposal|resolution|bill)\b",
    ),
    "rejection": (r"\b(?:rejects?|rejected|denies|denied|vetoes|vetoed)\b",),
    "opening": (
        r"\b(?:opens?|opened|launches?|launched|groundbreaking)\b",
        r"\bbreaks? ground\b",
        r"\bground (?:is )?broken\b",
    ),
    "closure": (
        r"\b(?:closes?|closed) (?:the )?(?:store|school|road|bridge|facility|business|office|route|service|program|park|beach|airport|plant|location)\b",
        r"\b(?:cancels?|canceled|cancelled) (?:the )?(?:route|service|event|program|project|flight|flights)\b",
        r"\bends? (?:the )?(?:route|service|program|operations|operation|season|project)\b",
        r"\bshuts? down\b",
    ),
    "election_result": (
        r"\belected\b",
        r"\bwins? (?:the )?(?:election|race|seat|primary)\b",
        r"\bwon (?:the )?(?:election|race|seat|primary)\b",
        r"\bdeclared (?:the )?winner\b",
    ),
    "funding": (
        r"\b(?:awarded|receives?|received|secures?|secured) (?:a |an |the )?(?:\$[\d.,]+ )?(?:grant|funding)\b",
        r"\bgrant (?:awarded|approved|funded)\b",
        r"\bfunding (?:approved|awarded|secured)\b",
    ),
}
_CASUALTY_WORDS = {"injured", "killed", "dead", "fatalities", "victims", "hurt"}


def _norm(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _set(values: Iterable[object]) -> set[str]:
    return {_norm(value) for value in values if _norm(value)}


def _tokens(value: object) -> set[str]:
    return {
        token for token in _WORD_RE.findall(_norm(value))
        if len(token) >= 3 and token not in _STOP
    }


def _overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


_GENERIC_IDENTITY_FACTS = frozenset({
    "arrest made", "fire reported", "road closed", "no injuries reported",
    "missing person", "investigation ongoing",
})


def _identity_facts(values: set[str]) -> set[str]:
    """Return only facts distinctive enough to support incident identity."""
    return {value for value in values if value not in _GENERIC_IDENTITY_FACTS}


def _milestones_for(mapping: Mapping[str, set[str]], *values: object) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                tokens |= _tokens(item)
        else:
            tokens |= _tokens(value)
    return {
        milestone
        for milestone, markers in mapping.items()
        if tokens & markers
    }


def _milestones(*values: object) -> set[str]:
    return _milestones_for(_FOLLOW_UP_MILESTONES, *values)


def _advisory_text(*values: object) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set, frozenset)):
            parts.extend(_norm(item) for item in value if _norm(item))
        elif _norm(value):
            parts.append(_norm(value))
    return " ".join(parts)


def detect_advisory_follow_up_evidence(*values: object) -> dict[str, tuple[str, ...]]:
    """Return phrase-level evidence for observe-only follow-up milestones.

    This helper is also used by retrospective timeline observability so live and
    historical diagnostics share one deterministic vocabulary.  It does not
    authorize story grouping or publication changes.
    """

    text = _advisory_text(*values)
    evidence: dict[str, tuple[str, ...]] = {}
    if not text:
        return evidence
    for milestone, patterns in _ADVISORY_FOLLOW_UP_PATTERNS.items():
        matches: set[str] = set()
        for pattern in patterns:
            matches.update(
                _norm(match.group(0))
                for match in re.finditer(pattern, text, flags=re.IGNORECASE)
                if _norm(match.group(0))
            )
        if matches:
            evidence[milestone] = tuple(sorted(matches))
    return evidence


def detect_advisory_follow_up_milestones(*values: object) -> set[str]:
    """Return observe-only follow-up milestone names found in supplied text."""

    return set(detect_advisory_follow_up_evidence(*values))


def _advisory_milestones(*values: object) -> set[str]:
    return detect_advisory_follow_up_milestones(*values)


def _numeric_casualty_signatures(facts: Iterable[str]) -> set[tuple[str, str]]:
    signatures: set[tuple[str, str]] = set()
    for fact in facts:
        text = _norm(fact)
        numbers = _NUMBER_RE.findall(text)
        casualty_terms = _tokens(text) & _CASUALTY_WORDS
        for number in numbers:
            for term in casualty_terms:
                signatures.add((term, number))
    return signatures


class StoryRelationshipType(str, Enum):
    SAME_EVENT = "same_event"
    FOLLOW_UP = "follow_up"
    RELATED = "related"
    NEW_STORY = "new_story"


@dataclass(frozen=True, slots=True)
class StoryRelationship:
    relationship: StoryRelationshipType
    story_id: str | None
    confidence: float
    reason: str
    decision_trace: tuple[str, ...] = ()
    candidate_story_id: str | None = None
    candidate_confidence: float = 0.0
    candidate_milestones: tuple[str, ...] = ()
    candidate_reason_codes: tuple[str, ...] = ()
    candidate_trace: tuple[str, ...] = ()

    @property
    def attaches_to_story(self) -> bool:
        return self.relationship in {
            StoryRelationshipType.SAME_EVENT,
            StoryRelationshipType.FOLLOW_UP,
        } and bool(self.story_id)


def _candidate_identity_anchor_codes(
    *,
    exact_event_anchor: bool,
    location_match: bool,
    agency_match: bool,
    type_match: bool,
    entity_match: bool,
    title_score: float,
    fact_score: float,
) -> tuple[str, ...]:
    """Return explainable identity-anchor codes for observe-only candidates.

    Milestone words and generic fact overlap are not identity. A current-run
    follow-up candidate must have either an exact event key or a corroborated
    combination of place, agency, named entity, event type, and strong title/fact
    continuity. This affects diagnostics only; live grouping remains governed by
    the existing conservative relationship contract.
    """
    codes: list[str] = []
    if exact_event_anchor:
        codes.append("exact_event_key")
    if location_match:
        codes.append("location_match")
    if agency_match:
        codes.append("agency_match")
    if type_match:
        codes.append("event_type_match")
    if entity_match:
        codes.append("entity_match")
    if title_score >= 0.35:
        codes.append("title_continuity")
    if fact_score >= 0.35:
        codes.append("fact_continuity")

    corroborated_pair = any(
        (
            location_match and agency_match,
            location_match and entity_match,
            agency_match and entity_match and title_score >= 0.45,
        )
    )
    singular_named_anchor = (
        (agency_match or entity_match)
        and type_match
        and title_score >= 0.70
        and fact_score >= 0.50
    )
    # A location-specific incident can also remain continuous across lifecycle
    # wording changes (for example, a crash closure followed by a reopening)
    # when the event family, distinctive facts, and headline all corroborate one
    # another. Requiring all four signals keeps generic same-city incidents from
    # attaching while preserving legitimate operational follow-ups.
    structured_incident_anchor = (
        location_match
        and type_match
        and fact_score >= 0.66
        and title_score >= 0.45
    )
    if structured_incident_anchor:
        codes.append("structured_incident_continuity")
    qualified = (
        exact_event_anchor
        or corroborated_pair
        or singular_named_anchor
        or structured_incident_anchor
    )
    if qualified:
        codes.append("identity_anchor_qualified")
    return tuple(codes)


class StoryRelationshipEngine:
    """Conservatively groups distinct events into a persistent story."""

    FOLLOW_UP_THRESHOLD = 0.68

    def classify(
        self,
        *,
        event_key: str,
        title: str,
        facts: Iterable[str],
        locations: Iterable[str] = (),
        agencies: Iterable[str] = (),
        event_types: Iterable[str] = (),
        entities: Iterable[str] = (),
        stories: Iterable[Mapping[str, Any]],
    ) -> StoryRelationship:
        incoming = {
            "facts": _set(facts),
            "locations": _set(locations),
            "agencies": _set(agencies),
            "types": _set(event_types),
            "entities": _set(entities),
        }
        incoming_title_tokens = _tokens(title)
        incoming_event_tokens = _tokens(event_key.replace("-", " "))
        incoming_casualties = _numeric_casualty_signatures(incoming["facts"])
        incoming_milestones = _milestones(title, incoming["facts"], event_key.replace("-", " "))
        incoming_advisory_milestones = _advisory_milestones(
            title, incoming["facts"], event_key.replace("-", " ")
        )

        best: StoryRelationship | None = None
        best_advisory: StoryRelationship | None = None
        timeline_conflicts: list[tuple[str, dict[str, Any]]] = []

        for story in stories:
            if story.get("status") == "archived":
                continue
            story_id = str(story.get("story_id", "")).strip()
            if not story_id:
                continue

            timeline_conflict = incoming_entry_conflicts_with_story(
                story,
                event_key=event_key,
                title=title,
            )
            if timeline_conflict is not None:
                timeline_conflicts.append((story_id, timeline_conflict))
                continue

            known = {
                "facts": _set(story.get("facts", ())),
                "locations": _set(story.get("locations", ())),
                "agencies": _set(story.get("agencies", ())),
                "types": _set(story.get("event_types", ())),
                "entities": _set(story.get("entities", ())),
            }

            # Location and casualty contradictions remain hard stops. Agency and
            # event-type conflicts still block live grouping, but are retained as
            # observe-only diagnostics because legitimate follow-ups often move from
            # one agency or lifecycle label to another (for example, rescue -> arrest).
            location_conflict = bool(
                incoming["locations"]
                and known["locations"]
                and not incoming["locations"] & known["locations"]
            )
            agency_conflict = bool(
                incoming["agencies"]
                and known["agencies"]
                and not incoming["agencies"] & known["agencies"]
            )
            type_conflict = bool(
                incoming["types"]
                and known["types"]
                and not incoming["types"] & known["types"]
            )
            if location_conflict:
                continue

            known_casualties = _numeric_casualty_signatures(known["facts"])
            if incoming_casualties and known_casualties:
                incoming_terms = {term for term, _ in incoming_casualties}
                known_terms = {term for term, _ in known_casualties}
                shared_terms = incoming_terms & known_terms
                if any(
                    {number for term, number in incoming_casualties if term == casualty_term}
                    != {number for term, number in known_casualties if term == casualty_term}
                    for casualty_term in shared_terms
                ):
                    continue

            scores = {key: _overlap(incoming[key], known[key]) for key in incoming}
            identity_fact_score = _overlap(
                _identity_facts(incoming["facts"]),
                _identity_facts(known["facts"]),
            )
            known_title_tokens = _tokens(story.get("canonical_title", "")) | set(story.get("title_tokens", ()))
            title_score = _overlap(incoming_title_tokens, known_title_tokens)
            known_event_tokens: set[str] = set()
            for known_event in story.get("events", ()):
                known_event_tokens |= _tokens(str(known_event).replace("-", " "))
            event_score = _overlap(incoming_event_tokens, known_event_tokens)

            location_match = bool(incoming["locations"] & known["locations"])
            agency_match = bool(incoming["agencies"] & known["agencies"])
            type_match = bool(incoming["types"] & known["types"])
            entity_match = bool(incoming["entities"] & known["entities"])
            fact_score = identity_fact_score
            known_milestones = _milestones(
                story.get("canonical_title", ""),
                story.get("titles", ()),
                known["facts"],
                story.get("events", ()),
            )
            novel_milestones = incoming_milestones - known_milestones
            known_advisory_milestones = _advisory_milestones(
                story.get("canonical_title", ""),
                story.get("titles", ()),
                known["facts"],
                story.get("events", ()),
            )
            novel_advisory_milestones = (
                incoming_advisory_milestones - known_advisory_milestones
            )
            novel_facts = incoming["facts"] - known["facts"]
            novel_fact_ratio = (
                len(novel_facts) / len(incoming["facts"])
                if incoming["facts"]
                else 0.0
            )
            lifecycle_signal = bool(novel_milestones)
            distinct_event_signal = lifecycle_signal or novel_fact_ratio >= 0.34

            # Produce an observe-only candidate even when the current conservative
            # relationship contract refuses to attach it. This gives production
            # observability real examples to review before broader follow-up rules
            # are allowed to change story grouping.
            exact_event_anchor = event_key in {
                str(value or "").strip() for value in story.get("events", ())
            }
            identity_anchor_codes = _candidate_identity_anchor_codes(
                exact_event_anchor=exact_event_anchor,
                location_match=location_match,
                agency_match=agency_match,
                type_match=type_match,
                entity_match=entity_match,
                title_score=title_score,
                fact_score=fact_score,
            )
            advisory_eligible = bool(novel_advisory_milestones) and (
                "identity_anchor_qualified" in identity_anchor_codes
            )
            if advisory_eligible:
                advisory_confidence = min(
                    1.0,
                    0.38
                    + 0.28 * float(exact_event_anchor)
                    + 0.10 * float(location_match)
                    + 0.10 * float(agency_match)
                    + 0.08 * float(type_match)
                    + 0.12 * float(entity_match)
                    + 0.10 * min(1.0, title_score)
                    + 0.10 * min(1.0, fact_score)
                    - 0.08 * float(agency_conflict)
                    - 0.06 * float(type_conflict),
                )
                reason_codes = ["novel_milestone", *identity_anchor_codes]
                if agency_conflict:
                    reason_codes.append("agency_conflict")
                if type_conflict:
                    reason_codes.append("event_type_conflict")
                advisory_trace = (
                    "Follow-up candidate mode: observe_only",
                    f"Candidate story: {story_id}",
                    f"Novel milestones: {', '.join(sorted(novel_advisory_milestones))}",
                    f"Exact event-key anchor: {exact_event_anchor}",
                    f"Facts overlap: {fact_score:.2f}",
                    f"Title overlap: {title_score:.2f}",
                    f"Location match: {location_match}",
                    f"Agency match: {agency_match}",
                    f"Event type match: {type_match}",
                    f"Entity match: {entity_match}",
                    f"Agency conflict: {agency_conflict}",
                    f"Event type conflict: {type_conflict}",
                    f"Candidate confidence: {advisory_confidence:.2f}",
                )
                advisory = StoryRelationship(
                    StoryRelationshipType.NEW_STORY,
                    None,
                    0.0,
                    "Created new story under the current conservative contract",
                    ("Relationship: new_story",),
                    candidate_story_id=story_id,
                    candidate_confidence=advisory_confidence,
                    candidate_milestones=tuple(sorted(novel_advisory_milestones)),
                    candidate_reason_codes=tuple(reason_codes),
                    candidate_trace=advisory_trace,
                )
                if (
                    best_advisory is None
                    or advisory.candidate_confidence > best_advisory.candidate_confidence
                ):
                    best_advisory = advisory

            # Preserve current live grouping behavior for this release. Conflicting
            # agencies or event types remain ineligible for an enforced follow-up.
            if agency_conflict or type_conflict:
                continue

            # Strong fact continuity plus concrete identity anchors is enough to
            # establish that a later event belongs to the same developing story.
            strong_public_safety_follow_up = (
                type_match
                and location_match
                and fact_score >= 0.66
                and distinct_event_signal
                and (lifecycle_signal or agency_match or entity_match)
            )
            strong_agency_follow_up = (
                type_match
                and location_match
                and agency_match
                and fact_score >= 0.50
                and distinct_event_signal
            )
            strong_entity_follow_up = (
                type_match
                and (location_match or agency_match)
                and entity_match
                and fact_score >= 0.40
                and distinct_event_signal
            )
            lifecycle_follow_up = (
                lifecycle_signal
                and type_match
                and (location_match or agency_match or entity_match)
                and fact_score >= 0.50
            )
            unstructured_lifecycle_follow_up = (
                lifecycle_signal
                and fact_score >= 0.66
                and (title_score >= 0.35 or event_score >= 0.35)
            )

            live_anchor_codes = _candidate_identity_anchor_codes(
                exact_event_anchor=exact_event_anchor,
                location_match=location_match,
                agency_match=agency_match,
                type_match=type_match,
                entity_match=entity_match,
                title_score=title_score,
                fact_score=fact_score,
            )
            identity_anchor_qualified = "identity_anchor_qualified" in live_anchor_codes

            eligible = identity_anchor_qualified and any((
                strong_public_safety_follow_up,
                strong_agency_follow_up,
                strong_entity_follow_up,
                lifecycle_follow_up,
                unstructured_lifecycle_follow_up,
            ))
            if not eligible:
                continue

            anchor_score = (
                0.18 * float(location_match)
                + 0.18 * float(agency_match)
                + 0.14 * float(type_match)
                + 0.18 * float(entity_match)
            )
            if unstructured_lifecycle_follow_up:
                confidence = min(
                    1.0,
                    0.52 + 0.30 * fact_score + 0.10 * title_score + 0.08 * event_score,
                )
            else:
                confidence = min(
                    1.0,
                    0.42 * fact_score
                    + anchor_score
                    + 0.05 * title_score
                    + 0.03 * event_score
                    + 0.08 * float(lifecycle_signal),
                )
            if confidence < self.FOLLOW_UP_THRESHOLD:
                continue

            trace = (
                f"Relationship: {StoryRelationshipType.FOLLOW_UP.value}",
                f"Distinctive facts overlap: {fact_score:.2f}",
                f"Identity anchor qualified: {identity_anchor_qualified}",
                f"Location match: {location_match}",
                f"Agency match: {agency_match}",
                f"Event type match: {type_match}",
                f"Entity match: {entity_match}",
                f"Lifecycle signal: {lifecycle_signal}",
                f"Novel milestones: {', '.join(sorted(novel_milestones)) or 'none'}",
                f"Novel fact ratio: {novel_fact_ratio:.2f}",
                f"Confidence: {confidence:.2f}",
                f"Threshold: {self.FOLLOW_UP_THRESHOLD:.2f}",
            )
            candidate = StoryRelationship(
                StoryRelationshipType.FOLLOW_UP,
                story_id,
                confidence,
                "Attached distinct event as a follow-up to an existing developing story",
                trace,
                candidate_story_id=story_id,
                candidate_confidence=confidence,
                candidate_milestones=tuple(sorted(novel_milestones)),
                candidate_reason_codes=(
                    "enforced_follow_up",
                    *live_anchor_codes,
                ),
                candidate_trace=trace,
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate

        if best is not None:
            return best
        if best_advisory is not None:
            return best_advisory
        if timeline_conflicts:
            story_id, conflict = timeline_conflicts[0]
            return StoryRelationship(
                StoryRelationshipType.NEW_STORY,
                None,
                0.0,
                "Created new story: proposed attachment failed timeline coherence",
                (
                    "Relationship: new_story",
                    "Timeline coherence hard conflict: true",
                    f"Rejected candidate story: {story_id}",
                    f"Incoming family: {conflict.get('incoming_family', 'unknown')}",
                    f"Existing family: {conflict.get('existing_family', 'unknown')}",
                    f"Title overlap: {float(conflict.get('title_overlap', 0.0)):.2f}",
                ),
            )
        return StoryRelationship(
            StoryRelationshipType.NEW_STORY,
            None,
            0.0,
            "Created new story: no supported cross-event relationship was found",
            ("Relationship: new_story",),
        )
