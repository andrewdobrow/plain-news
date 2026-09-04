"""Canonical event-identity authority boundary.

Candidate retrieval and canonical publication writes are deliberately separate.
Fuzzy/textual overlap may nominate a possible relationship, but only exact identity
or a hard composite of source-derived event facts may authorize mutation of an
existing permalink.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .incident_identity import incident_anchor_write_authoritative

OUTCOME_VERIFIED = "same_event_verified"
OUTCOME_POSSIBLE = "possible_relationship"
OUTCOME_NEW = "new_story"

TIER_EXACT = "exact_identity"
TIER_HARD_COMPOSITE = "hard_composite_identity"
TIER_CANDIDATE = "candidate_only"
TIER_CONFLICT = "conflict"
TIER_INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True)
class IdentityAuthorityDecision:
    outcome: str
    evidence_tier: str
    write_authorized: bool
    proof_type: str
    reason: str
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _decision(
    outcome: str,
    tier: str,
    authorized: bool,
    proof_type: str,
    reason: str,
    *reason_codes: str,
) -> IdentityAuthorityDecision:
    return IdentityAuthorityDecision(
        outcome=outcome,
        evidence_tier=tier,
        write_authorized=authorized,
        proof_type=proof_type,
        reason=reason,
        reason_codes=tuple(code for code in reason_codes if code),
    )


def authorize_exact_identity_keys(
    matched_keys: Iterable[str],
    *,
    trusted_story_ids: Iterable[str] = (),
) -> IdentityAuthorityDecision:
    """Authorize only deterministic ledger identities.

    Exact source URLs, structured incident keys, custom/weather event keys, and
    registry-certified persistent story IDs are authoritative. A merely present
    story ID is not authoritative unless the registry marked it safe.
    """
    keys = tuple(dict.fromkeys(str(key or "").strip() for key in matched_keys if key))
    trusted = {str(value or "").strip() for value in trusted_story_ids if value}

    for prefix, proof in (
        ("source:", "exact_source_url"),
        ("custom-event:", "exact_custom_event_key"),
        ("weather:", "exact_weather_event_key"),
    ):
        if any(key.startswith(prefix) for key in keys):
            return _decision(
                OUTCOME_VERIFIED,
                TIER_EXACT,
                True,
                proof,
                proof,
                "deterministic_identity_key",
            )

    incident_keys = [key for key in keys if key.startswith("incident:")]
    if incident_keys:
        authoritative = [
            key for key in incident_keys
            if incident_anchor_write_authoritative(key.split(":", 1)[1])
        ]
        if authoritative:
            return _decision(
                OUTCOME_VERIFIED,
                TIER_EXACT,
                True,
                "exact_structured_incident_key",
                "exact_structured_incident_key",
                "deterministic_identity_key",
                "incident_specific_anchor",
            )
        return _decision(
            OUTCOME_POSSIBLE,
            TIER_CANDIDATE,
            False,
            "broad_structured_incident_key",
            "structured_incident_key_requires_independent_event_proof",
            "candidate_only",
            "write_forbidden",
            "broad_incident_anchor",
        )

    # A persistent story ID is retrieval evidence only. The registry may already
    # be contaminated by a broad event key, so circularly trusting that ID would
    # allow the original mistake to authorize an overwrite. Independent source-
    # derived event proof is required later by ``decide_cross_source_identity``.
    if any(key.startswith("story:") for key in keys):
        return _decision(
            OUTCOME_POSSIBLE,
            TIER_CANDIDATE,
            False,
            "uncorroborated_persistent_story_id",
            "persistent_story_id_requires_independent_event_proof",
            "candidate_only",
            "write_forbidden",
        )

    if keys:
        return _decision(
            OUTCOME_POSSIBLE,
            TIER_CANDIDATE,
            False,
            "untrusted_identity_key",
            "identity_key_not_write_authoritative",
            "candidate_only",
        )
    return _decision(
        OUTCOME_NEW,
        TIER_INSUFFICIENT,
        False,
        "none",
        "no_identity_key_match",
        "insufficient_evidence",
    )


def decide_cross_source_identity(
    *,
    conflict_reason: str = "",
    exact_incident_anchor: bool = False,
    exact_known_event_key: bool = False,
    shared_named_people: int = 0,
    shared_precise_locations: int = 0,
    shared_agencies: int = 0,
    shared_subject_phrases: int = 0,
    shared_headline_topic_core: int = 0,
    shared_distinctive_facts: int = 0,
    shared_locality: bool = False,
    shared_event_family: bool = False,
    policy_family: bool = False,
    time_safe: bool = True,
    locality_safe: bool = True,
    family_safe: bool = True,
    near_duplicate_headline: bool = False,
) -> IdentityAuthorityDecision:
    """Classify a cross-source pair without using fuzzy agreement as authority."""
    if conflict_reason:
        return _decision(
            OUTCOME_NEW,
            TIER_CONFLICT,
            False,
            "conflict",
            conflict_reason,
            conflict_reason,
            "write_forbidden",
        )

    safe = time_safe and locality_safe and family_safe
    hard_corroboration = bool(
        shared_named_people
        or shared_precise_locations
        or (
            policy_family
            and shared_agencies
            and shared_subject_phrases
            and shared_locality
        )
    )

    if exact_incident_anchor and safe and hard_corroboration:
        return _decision(
            OUTCOME_VERIFIED,
            TIER_EXACT,
            True,
            "exact_structured_incident_anchor",
            "exact_structured_incident_anchor_with_independent_corroboration",
            "structured_incident_anchor",
            "independent_corroboration",
        )

    if exact_known_event_key and safe and hard_corroboration:
        return _decision(
            OUTCOME_VERIFIED,
            TIER_EXACT,
            True,
            "exact_known_event_key",
            "exact_known_event_key_with_independent_corroboration",
            "known_event_key",
            "independent_corroboration",
        )

    if (
        safe
        and shared_event_family
        and shared_locality
        and shared_named_people
        and shared_precise_locations
    ):
        return _decision(
            OUTCOME_VERIFIED,
            TIER_HARD_COMPOSITE,
            True,
            "participant_plus_precise_location",
            "same_participant_and_precise_location",
            "shared_named_people",
            "shared_precise_location",
            "compatible_event_family",
        )

    if (
        safe
        and shared_event_family
        and shared_locality
        and shared_named_people
        and shared_distinctive_facts >= 5
    ):
        return _decision(
            OUTCOME_VERIFIED,
            TIER_HARD_COMPOSITE,
            True,
            "participant_plus_distinctive_incident_facts",
            "same_participant_and_multiple_distinctive_incident_facts",
            "shared_named_people",
            "distinctive_fact_overlap",
            "compatible_event_family",
        )

    if (
        safe
        and shared_event_family
        and shared_locality
        and shared_precise_locations
        and shared_distinctive_facts >= 5
        # A precise-location composite is intentionally weaker than a named-person
        # composite, so it also needs headline-level subject continuity. Publisher
        # article extraction can contain related-story modules from the same site;
        # those modules may contribute a street plus many distinctive tokens from an
        # unrelated incident. Two shared headline topic concepts prevent that
        # contaminated body text from granting canonical write authority while
        # preserving genuine rewritten same-incident reports.
        and shared_headline_topic_core >= 2
    ):
        return _decision(
            OUTCOME_VERIFIED,
            TIER_HARD_COMPOSITE,
            True,
            "precise_location_plus_distinctive_facts",
            "same_precise_location_and_multiple_distinctive_facts",
            "shared_precise_location",
            "distinctive_fact_overlap",
            "compatible_event_family",
        )

    if (
        safe
        and policy_family
        and shared_locality
        and shared_agencies
        and shared_subject_phrases
        and shared_distinctive_facts >= 4
    ):
        return _decision(
            OUTCOME_VERIFIED,
            TIER_HARD_COMPOSITE,
            True,
            "governing_body_plus_policy_subject",
            "same_governing_body_and_specific_policy_subject",
            "shared_agency",
            "shared_subject_phrase",
            "distinctive_fact_overlap",
        )

    candidate_signal = bool(
        exact_incident_anchor
        or exact_known_event_key
        or shared_named_people
        or shared_precise_locations
        or (
            shared_locality
            and shared_event_family
            and (shared_agencies or shared_subject_phrases)
        )
        or near_duplicate_headline
        or shared_headline_topic_core >= 2
    )
    if candidate_signal:
        return _decision(
            OUTCOME_POSSIBLE,
            TIER_CANDIDATE,
            False,
            "candidate_similarity",
            "possible_relationship_without_hard_event_proof",
            "candidate_only",
            "write_forbidden",
        )

    return _decision(
        OUTCOME_NEW,
        TIER_INSUFFICIENT,
        False,
        "none",
        "insufficient_event_identity_evidence",
        "insufficient_evidence",
    )


def authorization_matches_target(
    authorization: object,
    canonical_slug: str,
) -> bool:
    if not isinstance(authorization, dict):
        return False
    return bool(
        authorization.get("write_authorized") is True
        and authorization.get("outcome") == OUTCOME_VERIFIED
        and str(authorization.get("canonical_slug") or "").strip()
        == str(canonical_slug or "").strip()
        and str(authorization.get("proof_type") or "").strip()
    )
