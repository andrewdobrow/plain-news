"""Conservative cross-source incident identity for sparse and drifting headlines.

This layer sits between exact/source identity and semantic resolution. It requires
concrete, explainable anchors and never treats absent evidence as agreement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping

UNIFIED_INCIDENT_EVIDENCE_VERSION = 4
_STORY_EVIDENCE_CACHE: dict[tuple[Any, ...], tuple["UnifiedIncidentEvidence", ...]] = {}
_STORY_EVIDENCE_CACHE_LIMIT = 10000

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "it", "local", "man", "new", "news", "of", "on", "or",
    "said", "says", "the", "this", "to", "was", "were", "with", "woman",
    "state", "county", "after", "near", "used", "police", "accused",
})
_GENERIC = frozenset({
    "arrest", "arrested", "charge", "charged", "crash", "family", "incident",
    "road", "vehicle", "vehicles", "fire", "death", "killed", "dead",
    "shooting", "shot", "officials", "investigation", "report", "reported",
    "murder", "suicide", "domestic", "related", "victim", "suspect",
    "identified", "identify", "identifies", "dies", "died", "fatal",
    "collision", "wreck", "driver", "rider", "person", "people",
})


def _norm(value: object) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").casefold()))


def _tokens(value: object) -> set[str]:
    return {
        token for token in _WORD_RE.findall(str(value or "").casefold())
        if len(token) >= 3 and token not in _STOP
    }


def _overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / min(len(left), len(right)) if left and right else 0.0


def _person_alias_key(value: object) -> str:
    """Normalize a person to first + surname, ignoring middle names/suffix drift."""
    parts = [token for token in _WORD_RE.findall(str(value or "").casefold()) if token]
    while parts and parts[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts.pop()
    if len(parts) < 2:
        return ""
    return f"{parts[0]} {parts[-1]}"


def _missing_person_shared_aliases(
    people_a: set[str], people_b: set[str],
    distinctive_a: set[str], distinctive_b: set[str],
) -> set[str]:
    """Return conservative missing-person subject aliases shared across framings.

    A follow-up may say ``Michael Debevec`` while the original alert says
    ``Michael Anthony Debevec II``. Some publisher copy does not tie the shortened
    name to a syntactic "missing" role, so it lands in distinctive tokens rather
    than ``people``. First + surname agreement is accepted only when the other side
    has no competing extracted person.
    """
    aliases_a = {key for value in people_a if (key := _person_alias_key(value))}
    aliases_b = {key for value in people_b if (key := _person_alias_key(value))}
    shared = aliases_a & aliases_b
    if shared:
        return shared

    def present(alias: str, tokens: set[str]) -> bool:
        parts = alias.split()
        return len(parts) == 2 and all(part in tokens for part in parts)

    # If both sides extracted different people, fail closed; token fallbacks could
    # otherwise mistake a family member or official for the missing subject.
    if people_a and people_b:
        return set()
    if people_a and not people_b:
        return {alias for alias in aliases_a if present(alias, distinctive_b)}
    if people_b and not people_a:
        return {alias for alias in aliases_b if present(alias, distinctive_a)}
    return set()


def _family(text: str) -> str:
    patterns = (
        ("road_rage", r"\broad rage\b|\b(?:pit|police) maneuver\b|\brun(?:ning)? .{0,45} off (?:the )?road\b|\bchased? off (?:the )?road\b"),
        ("wildfire_arson", r"\bwildfire\b|\bbrush fire\b.{0,45}\b(?:arson|set|setting|charged)\b|\b(?:arson|set|setting)\b.{0,45}\b(?:wildfire|brush fire)\b"),
        ("animal_cruelty", r"\banimal cruelty\b|\b(?:dog|cat|animal|pet)\b.{0,55}\b(?:kicked|kicking|abused|abusing|beaten|beating|cruelty)\b|\b(?:kicked|kicking|abused|abusing|beaten|beating|cruelty)\b.{0,55}\b(?:dog|cat|animal|pet)\b"),
        ("animal_rescue", r"\b(?:cat|cats|dog|dogs|animal|animals|hamster|pets?)\b.{0,45}\b(?:rescue|rescued|saved)\b|\b(?:rescue|rescued|saved)\b.{0,45}\b(?:cat|cats|dog|dogs|animal|animals|hamster|pets?)\b"),
        (
            "missing_person",
            r"\b(?:missing|reported missing|went missing|last seen)\b.{0,90}\b(?:person|child|boy|girl|teen|teenager|man|woman|student)\b"
            r"|\b(?:help|search|seek|seeking|find|finding|locate|locating)\b.{0,75}\b(?:missing|last seen|autistic|teen|boy|girl|child)\b"
            r"|\b(?:person|child|boy|girl|teen|teenager|man|woman|student)\b.{0,75}\b(?:missing|last seen|reported missing|went missing)\b"
            r"|\b(?:search(?:ing)? for|locat(?:e|ing))\b.{0,120}\b(?:disappeared|disappearance|has not been heard from|family has not heard)\b",
        ),
        ("traffic_crash", r"\b(?:crash|collision|wreck|vehicle overturned|hit and run)\b"),
        ("murder_suicide", r"\bmurder[- ]suicide\b|\bdomestic[- ]related\b.{0,45}\b(?:two|2) dead\b"),
        ("shooting", r"\b(?:shooting|shot|gunfire)\b"),
        ("government_finance", r"\b(?:property tax|tax reform|millage|budget|job cuts?)\b"),
        ("dui", r"\b(?:dui|driving under the influence)\b"),
        ("fire", r"\b(?:fire|blaze)\b"),
        ("death", r"\b(?:killed|dead|dies|died|death|murder)\b"),
    )
    for family, pattern in patterns:
        if re.search(pattern, text):
            return family
    return "unknown"


def _concepts(text: str) -> set[str]:
    raw = str(text or "")
    normalized = _norm(raw)
    concepts: set[str] = set()
    tests = (
        ("pit_maneuver", r"\b(?:pit|police) maneuver\b"),
        ("forced_off_road", r"\b(?:run|running|ran|chased|forced|sent|sends|crash(?:ed|es)?)\b.{0,60}\b(?:off (?:the )?road|into (?:a )?(?:barbed wire )?fence)\b|\bvehicle.{0,50}\bfence\b|\bsuv.{0,50}\bfence\b"),
        ("family_victim", r"\bfamily(?:'s)?\b"),
        ("fence_collision", r"\bbarbed wire fence\b|\binto (?:a )?fence\b|\bsuv.{0,35}\bfence\b"),
        ("i95", r"\bi\s*95\b|\binterstate 95\b"),
        ("fourth_arrest", r"\b(?:4th|fourth) arrest\b|\b4 (?:people )?arrested\b"),
        ("missing_person_signal", r"\b(?:missing|reported missing|went missing|last seen|disappeared|disappearance)\b|\b(?:help|search|seek|seeking|find|finding|locate|locating|searching for)\b.{0,75}\b(?:person|child|boy|girl|teen|teenager|man|woman|student|autistic|disappeared)\b"),
        ("last_seen", r"\blast seen\b"),
        ("public_search", r"\b(?:help|search|seek|seeking|find|finding|locate|locating|looking)\b"),
        ("autistic_subject", r"\bautistic\b|\bautism\b"),
        ("minor_subject", r"\b(?:child|boy|girl|teen|teenager|juvenile|minor)\b|\b\d{1,2}\s+year\s+old\b"),
        # Animal-cruelty reports frequently drift from a broad charge headline to
        # a video/action headline.  These are concrete incident anchors, not
        # generic crime vocabulary.
        ("animal_cruelty", r"\banimal cruelty\b"),
        ("small_dog", r"\bsmall dog\b|\blittle dog\b"),
        ("social_media_video", r"\bsocial media (?:video|clip|post)\b|\bviral video\b"),
        ("kicking_dog", r"\b(?:kick|kicked|kicking)\b.{0,28}\b(?:dog|pet)\b|\b(?:dog|pet)\b.{0,28}\b(?:kick|kicked|kicking)\b"),
        ("pool_scene", r"\b(?:pool|swimming pool)\b"),
    )
    for name, pattern in tests:
        if re.search(pattern, normalized):
            concepts.add(name)

    for match in re.finditer(r"\b(\d{1,2})\s+year\s+old\b", normalized):
        age = int(match.group(1))
        if 0 <= age <= 99:
            concepts.add(f"age_{age}")

    # Publisher copy also commonly renders age as ``Full Name, 68, ...``.
    for match in re.finditer(
        r"\b[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,2},\s*(\d{1,2})(?:,|\s)",
        raw,
    ):
        age = int(match.group(1))
        if 0 <= age <= 99:
            concepts.add(f"age_{age}")

    # Bond is a highly discriminating arrest fact. Normalize punctuation so
    # ``$7,500`` and ``$7500`` become the same deterministic concept.
    for pattern in (
        r"\$\s*([0-9][0-9,]{2,})\s+(?:bond|bail)",
        r"\b(?:bond|bail)(?:\s+(?:was|is|set|amount|at))*\s*\$\s*([0-9][0-9,]{2,})",
    ):
        for match in re.finditer(pattern, raw, re.I):
            amount = re.sub(r"[^0-9]", "", match.group(1))
            if amount:
                concepts.add(f"bond_{amount}")
    return concepts


def _person_names(text: str) -> set[str]:
    """Extract participant names tied to an incident role.

    Missing-person alerts commonly put the age before the name (``14-year-old
    Ethan Boyd``) or describe the name as ``last seen`` rather than arrested.
    Those are strong identity signals and must survive publisher headline drift.
    """
    names: set[str] = set()
    raw = str(text or "")
    name_rx = r"[A-Z][a-z]+(?:\s+(?:[A-Z]\.?|[A-Z][a-z]+)){1,2}"
    patterns = (
        rf"\b({name_rx})(?:,?\s+(?:age\s+)?\d{{1,3}}\b|\s+(?:was\s+)?(?:arrested|charged|identified|killed)\b)",
        rf"\b\d{{1,3}}[- ]year[- ]old\s+({name_rx})\b",
        rf"\b({name_rx}),\s+(?:an?\s+|the\s+)?\d{{1,3}}[- ]year[- ]old\b",
        rf"\b(?:find|finding|locate|locating|search(?:ing)? for|looking for)\s+({name_rx})\b",
        rf"\b({name_rx})\b[^.!?]{{0,65}}\b(?:reported missing|went missing|is missing|was missing|last seen|found safe|located safe)\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            value = " ".join(match.group(1).casefold().split())
            names.add(value)

    excluded = {
        "united states", "white house", "supreme court", "federal reserve",
        "new york", "los angeles", "washington dc", "washington d c",
        "justice department", "state department", "department of justice",
    }
    return {
        value for value in names
        if value not in excluded
        and not any(
            token in {"county", "sheriff", "police", "office", "facility", "living"}
            for token in value.split()
        )
    }


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _days_between(left: str, right: str) -> float | None:
    if not left or not right:
        return None
    try:
        a = datetime.fromisoformat(left.replace("Z", "+00:00"))
        b = datetime.fromisoformat(right.replace("Z", "+00:00"))
    except ValueError:
        return None
    return abs((a - b).total_seconds()) / 86400


@dataclass(frozen=True, slots=True)
class UnifiedIncidentEvidence:
    family: str
    concepts: tuple[str, ...]
    people: tuple[str, ...]
    locations: tuple[str, ...]
    agencies: tuple[str, ...]
    distinctive_tokens: tuple[str, ...]
    title_tokens: tuple[str, ...]
    published_at: str = ""
    evidence_version: int = UNIFIED_INCIDENT_EVIDENCE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_version": self.evidence_version,
            "family": self.family,
            "concepts": list(self.concepts),
            "people": list(self.people),
            "locations": list(self.locations),
            "agencies": list(self.agencies),
            "distinctive_tokens": list(self.distinctive_tokens),
            "title_tokens": list(self.title_tokens),
            "published_at": self.published_at,
        }


def build_unified_incident_evidence(
    *,
    title: object,
    body: object = "",
    facts: Iterable[object] = (),
    locations: Iterable[object] = (),
    agencies: Iterable[object] = (),
    entities: Iterable[object] = (),
    published_at: object = None,
    source_url: object = "",
) -> UnifiedIncidentEvidence:
    title_text = str(title or "")
    text = " ".join(
        [
            title_text,
            str(body or ""),
            str(source_url or ""),
            *(str(v or "") for v in facts),
            *(str(v or "") for v in entities),
        ]
    )
    normalized = _norm(text)
    title_tokens = _tokens(title_text)
    normalized_locations = {_norm(v) for v in locations if _norm(v)}
    normalized_agencies = {_norm(v) for v in agencies if _norm(v)}
    location_tokens: set[str] = set()
    agency_tokens: set[str] = set()
    for value in normalized_locations:
        location_tokens |= _tokens(value)
    for value in normalized_agencies:
        agency_tokens |= _tokens(value)
    # Location and agency are scored as structured fields. Excluding their words
    # from the distinctive token set prevents a broad city/county name from
    # masquerading as independent evidence.
    distinctive = _tokens(text) - _GENERIC - location_tokens - agency_tokens
    return UnifiedIncidentEvidence(
        family=_family(normalized),
        concepts=tuple(sorted(_concepts(text))),
        people=tuple(sorted(_person_names(text))),
        locations=tuple(sorted(normalized_locations)),
        agencies=tuple(sorted(normalized_agencies)),
        distinctive_tokens=tuple(sorted(distinctive))[:48],
        title_tokens=tuple(sorted(title_tokens))[:36],
        published_at=_iso(published_at),
    )


def evidence_from_mapping(value: Mapping[str, Any]) -> UnifiedIncidentEvidence:
    return UnifiedIncidentEvidence(
        family=str(value.get("family") or "unknown"),
        concepts=tuple(str(v) for v in value.get("concepts", ()) if str(v)),
        people=tuple(str(v) for v in value.get("people", ()) if str(v)),
        locations=tuple(str(v) for v in value.get("locations", ()) if str(v)),
        agencies=tuple(str(v) for v in value.get("agencies", ()) if str(v)),
        distinctive_tokens=tuple(str(v) for v in value.get("distinctive_tokens", ()) if str(v)),
        title_tokens=tuple(str(v) for v in value.get("title_tokens", ()) if str(v)),
        published_at=str(value.get("published_at") or ""),
        evidence_version=int(value.get("evidence_version") or 1),
    )


@dataclass(frozen=True, slots=True)
class UnifiedIncidentMatch:
    story_id: str | None
    confidence: float
    reason: str
    decision_trace: tuple[str, ...]

    @property
    def matched(self) -> bool:
        return bool(self.story_id) and self.confidence >= 0.86


def compare_unified_incident_evidence(
    incoming: UnifiedIncidentEvidence,
    known: UnifiedIncidentEvidence,
) -> tuple[float, tuple[str, ...]]:
    if incoming.family == "unknown" or known.family == "unknown" or incoming.family != known.family:
        return 0.0, ("Compatible event family: false",)

    days = _days_between(incoming.published_at, known.published_at)
    if days is not None and days > 21:
        return 0.0, (f"Publication window days: {days:.1f}", "Within incident window: false")

    concepts_a, concepts_b = set(incoming.concepts), set(known.concepts)
    people_a, people_b = set(incoming.people), set(known.people)
    locations_a, locations_b = set(incoming.locations), set(known.locations)
    agencies_a, agencies_b = set(incoming.agencies), set(known.agencies)
    distinctive_a, distinctive_b = set(incoming.distinctive_tokens), set(known.distinctive_tokens)
    title_a, title_b = set(incoming.title_tokens), set(known.title_tokens)

    shared_concepts = concepts_a & concepts_b
    shared_people = people_a & people_b
    shared_person_aliases = set()
    if incoming.family == "missing_person":
        shared_person_aliases = _missing_person_shared_aliases(
            people_a, people_b, distinctive_a, distinctive_b
        )
    effective_shared_people = shared_people or shared_person_aliases
    shared_locations = locations_a & locations_b
    shared_agencies = agencies_a & agencies_b
    shared_distinctive = distinctive_a & distinctive_b
    distinctive_overlap = _overlap(distinctive_a, distinctive_b)
    title_overlap = _overlap(title_a, title_b)

    location_conflict = bool(locations_a and locations_b and not shared_locations)
    if location_conflict and not effective_shared_people and incoming.family != "road_rage":
        return 0.0, ("Location conflict: true",)

    confidence = 0.0
    qualified = False
    if effective_shared_people:
        confidence = 0.94 + min(0.04, 0.01 * len(shared_concepts))
        # Missing-person first+surname aliases are intentionally treated as the same
        # named subject even when a middle name/suffix disappeared in follow-up copy.
        if incoming.family == "missing_person" and shared_person_aliases:
            confidence = max(confidence, 0.97)
        qualified = True
    elif incoming.family == "animal_cruelty":
        ages_a = {value for value in concepts_a if value.startswith("age_")}
        ages_b = {value for value in concepts_b if value.startswith("age_")}
        bonds_a = {value for value in concepts_a if value.startswith("bond_")}
        bonds_b = {value for value in concepts_b if value.startswith("bond_")}
        shared_age = ages_a & ages_b
        shared_bond = bonds_a & bonds_b
        age_conflict = bool(ages_a and ages_b and not shared_age)
        bond_conflict = bool(bonds_a and bonds_b and not shared_bond)
        if age_conflict or bond_conflict:
            return 0.0, (
                f"Animal-cruelty age conflict: {age_conflict}",
                f"Animal-cruelty bond conflict: {bond_conflict}",
            )
        core = {"animal_cruelty", "small_dog", "social_media_video", "kicking_dog", "pool_scene"}
        shared_core = shared_concepts & core
        # Same place + same age + same bond + the same concrete animal/video
        # facts is an incident fingerprint strong enough to survive a one-letter
        # publisher spelling discrepancy in the subject's surname.
        if shared_locations and shared_age and shared_bond and len(shared_core) >= 2:
            confidence = 0.99
            qualified = True
        elif (
            shared_locations
            and bool(shared_age or shared_bond)
            and bool(shared_agencies)
            and len(shared_core) >= 3
        ):
            confidence = 0.97
            qualified = True
        elif (
            shared_locations
            and bool(shared_agencies)
            and len(shared_core) >= 4
            and len(shared_distinctive) >= 3
        ):
            confidence = 0.93
            qualified = True
    elif incoming.family == "missing_person":
        ages_a = {value for value in concepts_a if value.startswith("age_")}
        ages_b = {value for value in concepts_b if value.startswith("age_")}
        shared_age = ages_a & ages_b
        age_conflict = bool(ages_a and ages_b and not shared_age)
        person_conflict = bool(people_a and people_b and not effective_shared_people)
        if age_conflict or person_conflict:
            return 0.0, (
                f"Missing-person age conflict: {age_conflict}",
                f"Missing-person name conflict: {person_conflict}",
            )
        shared_landmark: set[str] = set()
        shared_profile = shared_concepts & {"autistic_subject", "minor_subject"}
        search_continuity = bool(
            "missing_person_signal" in shared_concepts
            and ("last_seen" in shared_concepts or "public_search" in shared_concepts)
        )
        # Same location plus an exact age is a strong cross-publisher alert
        # signature. Add condition/subject or search continuity so unrelated
        # missing-person alerts in the same city remain separate.
        if shared_locations and shared_age and (shared_profile or search_continuity):
            confidence = 0.96 + min(0.02, 0.01 * len(shared_landmark))
            qualified = True
        elif shared_locations and shared_landmark and search_continuity:
            confidence = 0.95
            qualified = True
        elif (
            shared_locations
            and {"autistic_subject", "minor_subject", "last_seen"}.issubset(shared_concepts)
            and title_overlap >= 0.30
            and len(shared_distinctive) >= 4
        ):
            confidence = 0.91
            qualified = True
    elif incoming.family == "road_rage":
        core = {"pit_maneuver", "forced_off_road"}
        region = {"fence_collision", "family_victim"}
        core_match = bool(shared_concepts & core) or (
            bool(concepts_a & core) and bool(concepts_b & core)
        )
        region_match = bool((concepts_a & region) & (concepts_b & region))
        # Source headlines can describe the same maneuver as PIT, police maneuver,
        # chased off road, or SUV into a fence. Require the core action plus one
        # distinctive geographic/victim anchor, never generic "road rage" alone.
        shared_victim = "family_victim" in shared_concepts
        if core_match and region_match:
            confidence = 0.94 + min(0.04, 0.01 * len(shared_concepts))
            qualified = True
        elif core_match and shared_victim and title_overlap >= 0.25:
            confidence = 0.91
            qualified = True
        elif len(shared_concepts) >= 3 and title_overlap >= 0.28:
            confidence = 0.90
            qualified = True
        elif len(shared_concepts & region) >= 2 and title_overlap >= 0.25:
            confidence = 0.88
            qualified = True
        elif shared_locations and title_overlap >= 0.30:
            confidence = 0.87
            qualified = True
    else:
        # General source-framing drift contract. A shared city or county by itself
        # is never enough: the same event family must also retain substantial
        # distinctive wording from the publisher source. Agency corroboration can
        # lower the wording threshold slightly, while location-only matches require
        # both high token overlap and at least three shared distinctive terms.
        general_family_allowed = incoming.family in {
            "traffic_crash", "shooting", "murder_suicide",
            "animal_rescue", "animal_cruelty", "dui", "wildfire_arson", "missing_person",
        }
        location_agency_signature = (
            general_family_allowed
            and bool(shared_locations)
            and bool(shared_agencies)
            and len(shared_distinctive) >= 2
            and distinctive_overlap >= 0.50
            and title_overlap >= 0.45
        )
        location_fact_signature = (
            general_family_allowed
            and bool(shared_locations)
            and (
                (
                    len(shared_distinctive) >= 3
                    and distinctive_overlap >= 0.60
                    and title_overlap >= 0.55
                )
                or (
                    len(shared_distinctive) >= 2
                    and distinctive_overlap >= 0.66
                    and title_overlap >= 0.65
                )
            )
        )
        if location_agency_signature:
            confidence = 0.91 + min(0.04, 0.01 * len(shared_distinctive))
            qualified = True
        elif location_fact_signature:
            confidence = 0.89 + min(0.04, 0.01 * len(shared_distinctive))
            qualified = True

    trace = (
        f"Compatible event family: {incoming.family == known.family}",
        f"Event family: {incoming.family}",
        f"Shared people: {', '.join(sorted(shared_people)) or 'none'}",
        f"Shared person aliases: {', '.join(sorted(shared_person_aliases)) or 'none'}",
        f"Shared locations: {', '.join(sorted(shared_locations)) or 'none'}",
        f"Shared agencies: {', '.join(sorted(shared_agencies)) or 'none'}",
        f"Shared concepts: {', '.join(sorted(shared_concepts)) or 'none'}",
        f"Shared distinctive tokens: {len(shared_distinctive)}",
        f"Distinctive-token overlap: {distinctive_overlap:.2f}",
        f"Title overlap: {title_overlap:.2f}",
        f"Identity anchors qualified: {qualified}",
        f"Confidence: {confidence:.2f}",
    )
    return confidence if qualified else 0.0, trace


def _story_evidence_cache_key(story: Mapping[str, Any]) -> tuple[Any, ...]:
    timeline = tuple(
        (
            str(row.get("title") or ""),
            str(row.get("source") or ""),
            str(row.get("url") or ""),
            str(row.get("published_at") or ""),
        )
        for row in (story.get("timeline", ()) or ())
        if isinstance(row, Mapping)
    )
    stored = tuple(
        (
            int(row.get("evidence_version") or 1),
            str(row.get("family") or "unknown"),
            tuple(str(v) for v in row.get("concepts", ()) or ()),
            tuple(str(v) for v in row.get("people", ()) or ()),
            tuple(str(v) for v in row.get("locations", ()) or ()),
        )
        for row in (story.get("unified_incident_evidence", ()) or ())
        if isinstance(row, Mapping)
    )
    return (
        str(story.get("story_id") or ""),
        str(story.get("canonical_title") or ""),
        tuple(str(v) for v in story.get("titles", ()) or ()),
        tuple(str(v) for v in story.get("facts", ()) or ()),
        tuple(str(v) for v in story.get("locations", ()) or ()),
        tuple(str(v) for v in story.get("agencies", ()) or ()),
        tuple(str(v) for v in story.get("entities", ()) or ()),
        timeline,
        stored,
    )


def story_unified_evidence(story: Mapping[str, Any]) -> tuple[UnifiedIncidentEvidence, ...]:
    cache_key = _story_evidence_cache_key(story)
    cached = _STORY_EVIDENCE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    stored_rows = [
        row for row in (story.get("unified_incident_evidence", ()) or ())
        if isinstance(row, Mapping)
    ]
    stored = [evidence_from_mapping(row) for row in stored_rows]
    # Versioned rows were built with the current extraction contract and can be
    # trusted directly. Legacy rows are rebuilt because older releases classified
    # missing-person alerts as ``unknown`` and discarded age/name continuity.
    if stored and all(
        evidence.evidence_version >= UNIFIED_INCIDENT_EVIDENCE_VERSION
        for evidence in stored
    ):
        result = tuple(stored)
    else:
        timeline = list(story.get("timeline", ()) or ())
        published = str(timeline[0].get("published_at") or "") if timeline else ""
        source_urls = " ".join(
            str(row.get("source") or row.get("url") or "")
            for row in timeline if isinstance(row, Mapping)
        )
        titles = [
            story.get("canonical_title", ""),
            *story.get("titles", ()),
            *(row.get("title", "") for row in timeline if isinstance(row, Mapping)),
        ]
        built: list[UnifiedIncidentEvidence] = []
        seen: set[tuple[Any, ...]] = set()
        for title in titles:
            if not str(title or "").strip():
                continue
            evidence = build_unified_incident_evidence(
                title=title,
                body=source_urls,
                facts=story.get("facts", ()),
                locations=story.get("locations", ()),
                agencies=story.get("agencies", ()),
                entities=story.get("entities", ()),
                published_at=published,
                source_url=source_urls,
            )
            key = (
                evidence.family, evidence.concepts, evidence.people, evidence.locations,
                evidence.agencies, evidence.distinctive_tokens, evidence.title_tokens,
            )
            if key not in seen:
                seen.add(key)
                built.append(evidence)
        result = tuple(built) if any(
            evidence.family != "unknown" for evidence in built
        ) else tuple(stored or built)

    if len(_STORY_EVIDENCE_CACHE) >= _STORY_EVIDENCE_CACHE_LIMIT:
        _STORY_EVIDENCE_CACHE.clear()
    _STORY_EVIDENCE_CACHE[cache_key] = result
    return result


def find_matching_unified_incident_story(
    incoming: UnifiedIncidentEvidence,
    stories: Iterable[Mapping[str, Any]],
) -> UnifiedIncidentMatch:
    candidates: list[tuple[float, str, tuple[str, ...]]] = []
    for story in stories:
        if story.get("status") == "archived":
            continue
        story_id = str(story.get("story_id") or "").strip()
        if not story_id:
            continue
        best_score = 0.0
        best_trace: tuple[str, ...] = ()
        for known in story_unified_evidence(story):
            score, trace = compare_unified_incident_evidence(incoming, known)
            if score > best_score:
                best_score, best_trace = score, trace
        if best_score >= 0.86:
            candidates.append((best_score, story_id, best_trace))
    candidates.sort(reverse=True)
    if not candidates:
        return UnifiedIncidentMatch(None, 0.0, "No verified unified incident match", ("Unified incident match: false",))
    best_score, best_story, best_trace = candidates[0]
    if len(candidates) > 1 and candidates[1][0] >= best_score - 0.03:
        return UnifiedIncidentMatch(None, 0.0, "Ambiguous unified incident candidates", (*best_trace, "Ambiguous candidate margin: true"))
    return UnifiedIncidentMatch(
        best_story,
        best_score,
        "Verified cross-source incident evidence belongs to an existing story",
        ("Unified incident match: true", *best_trace),
    )


def story_has_verified_unified_identity(story: Mapping[str, Any]) -> bool:
    """Return true when every stored title/evidence row forms one verified incident.

    Registry repair uses this to distinguish a legitimate sparse-key incident with
    many publisher phrasings from an unsupported sparse merge. The evidence graph
    must be connected; one matching pair is not enough to excuse a disconnected
    title cluster.
    """
    evidence = list(story_unified_evidence(story))
    if len(evidence) < 2:
        return False
    parent = list(range(len(evidence)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for left in range(len(evidence)):
        for right in range(left + 1, len(evidence)):
            score, _ = compare_unified_incident_evidence(evidence[left], evidence[right])
            if score >= 0.86:
                union(left, right)
    return len({find(index) for index in range(len(evidence))}) == 1


def _timeline_split_lineage_roots(story: Mapping[str, Any]) -> frozenset[str]:
    """Read durable negative-identity roots written by registry repair."""

    roots = {
        str(value or "").strip()
        for value in (story.get("timeline_coherence_split_roots", ()) or ())
        if str(value or "").strip()
    }
    repair = story.get("timeline_coherence_repair")
    if isinstance(repair, Mapping):
        original = str(repair.get("original_story_id") or "").strip()
        if original:
            roots.add(original)
    return frozenset(roots)


def unified_incident_components(stories: Mapping[str, Mapping[str, Any]]) -> list[set[str]]:
    ids = list(stories)
    parent = {story_id: story_id for story_id in ids}
    lineages = {
        story_id: set(_timeline_split_lineage_roots(story))
        for story_id, story in stories.items()
    }

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a == b:
            return
        # A high-confidence timeline split is negative identity authority. Do not
        # let weaker cross-source similarity reconnect sibling components, even
        # transitively through a third record.
        if lineages.get(a, set()) & lineages.get(b, set()):
            return
        parent[b] = a
        lineages.setdefault(a, set()).update(lineages.get(b, set()))
        lineages.pop(b, None)

    # Evidence extraction can inspect timeline URLs and legacy rows. Cache it once
    # per story; recomputing it inside the pairwise family loop previously turned a
    # repair pass into repeated full-registry parsing and caused workflow creep.
    evidence_by_story = {
        story_id: story_unified_evidence(story)
        for story_id, story in stories.items()
    }
    buckets: dict[str, list[str]] = {}
    for story_id, evidence_rows in evidence_by_story.items():
        families = {ev.family for ev in evidence_rows if ev.family != "unknown"}
        for family in families:
            buckets.setdefault(family, []).append(story_id)
    for members in buckets.values():
        for i, left in enumerate(members):
            left_evidence = evidence_by_story[left]
            for right in members[i + 1:]:
                matched = False
                for a in left_evidence:
                    for b in evidence_by_story[right]:
                        score, _ = compare_unified_incident_evidence(a, b)
                        if score >= 0.86:
                            union(left, right)
                            matched = True
                            break
                    if matched:
                        break
    grouped: dict[str, set[str]] = {}
    for story_id in ids:
        grouped.setdefault(find(story_id), set()).add(story_id)
    return [group for group in grouped.values() if len(group) > 1]
