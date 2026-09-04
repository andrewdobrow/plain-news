"""Deterministic, structured incident identity signatures.

The identity layer sits between exact URL/title matching and broad semantic
similarity.  It only emits an anchor when the article contains enough
independent evidence that two differently framed reports describe the same
real-world incident.

Supported families:

* ``mass_animal_hoarding`` — a large local animal-hoarding/rescue case.
* ``named_person_death`` — death, mourning, memorial and cause-of-death
  coverage centered on one explicitly named person.
* ``infrastructure_condition`` — continuing coverage of one named public asset
  experiencing the same operational condition.

The second family is intentionally generic rather than case-specific.  It
prevents one death from fragmenting into parallel stories when different
publishers emphasize the agency response, condolences, personal background,
location, or cause of death.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping

INCIDENT_IDENTITY_VERSION = "3.4"

_WORD_RE = re.compile(r"[a-z0-9]+")
_ANIMAL_QUANTITY_RE = re.compile(r"\b(\d{1,3})\s+(?:cats?|dogs?|animals?|pets?)\b", re.IGNORECASE)
_ANIMAL_RE = re.compile(r"\b(?:cats?|dogs?|animals?|pets?)\b", re.IGNORECASE)
_PLURAL_ANIMAL_RE = re.compile(r"\b(?:cats|dogs|animals|pets)\b", re.IGNORECASE)
_HOARDING_RE = re.compile(r"hoard", re.IGNORECASE)
_ANIMAL_ACTION_RE = re.compile(
    r"rescu|removed|reunit|shelter|humane society|filth|feces|faeces|cages",
    re.IGNORECASE,
)
_POSTAL_ROLE_RE = re.compile(r"\b(?:mail carrier|postal worker)\b", re.IGNORECASE)

# A public-asset condition requires three independent anchors: one asset type,
# one named road/asset location and one operational-state phrase. This is broad
# enough for headline rewrites but narrow enough to avoid merging crashes or
# construction projects that merely mention the same road.
_INFRASTRUCTURE_ASSET_RE = re.compile(
    r"\b(?:traffic\s+(?:signal|light)|stoplight|rail(?:road)?\s+crossing|"
    r"drawbridge|bridge|fire\s+station|school|facility)\b",
    re.IGNORECASE,
)
_INFRASTRUCTURE_NONOP_RE = re.compile(
    r"\b(?:not\s+(?:fully\s+)?(?:operational|working|functioning)|"
    r"out\s+of\s+service|inoperable|malfunction(?:ing|s|ed)?|"
    r"still\s+flashing|flashing\s+(?:for|since|after))\b",
    re.IGNORECASE,
)
_NAMED_ROAD_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9'’.-]*(?:\s+[A-Z][A-Za-z0-9'’.-]*){0,6}\s+"
    r"(?:Road|Rd\.?|Street|St\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|"
    r"Drive|Dr\.?|Highway|Hwy\.?|Parkway|Pkwy\.?|Lane|Ln\.?|"
    r"Trail|Causeway|Bridge))\b"
)
_INFRASTRUCTURE_ASSET_CANONICAL = (
    (re.compile(r"\b(?:traffic\s+(?:signal|light)|stoplight)\b", re.I), "traffic-signal"),
    (re.compile(r"\b(?:rail(?:road)?\s+crossing)\b", re.I), "rail-crossing"),
    (re.compile(r"\b(?:drawbridge|bridge)\b", re.I), "bridge"),
    (re.compile(r"\bfire\s+station\b", re.I), "fire-station"),
    (re.compile(r"\bschool\b", re.I), "school"),
    (re.compile(r"\bfacility\b", re.I), "facility"),
)

# A death story can be framed as the death itself, a mourning statement, a
# memorial, a cause-of-death disclosure, or a tribute.  All remain one
# persistent incident when the same named person is the subject.

# A formally named law-enforcement operation is a durable incident identifier.
# Require both a title-cased multiword operation name and independent enforcement
# context so generic phrases such as "operation underway" never become anchors.
_NAMED_OPERATION_RE = re.compile(
    r"\bOperation\s+[\"'“‘]?([A-Z][A-Za-z0-9'’-]+(?:\s+(?:the|of|and|for|[A-Z][A-Za-z0-9'’-]+)){1,5})[\"'”’]?",
)
_LAW_ENFORCEMENT_OPERATION_CONTEXT_RE = re.compile(
    r"\b(?:sheriff|police|deput(?:y|ies)|narcotics?|drug|cocaine|fentanyl|methamphetamine|"
    r"traffick(?:ing)?|arrest(?:ed|s)?|indict(?:ed|ment)?|dea|fbi|homeland security)\b",
    re.IGNORECASE,
)


def _named_law_enforcement_operation_anchor(text: str) -> str:
    if not _LAW_ENFORCEMENT_OPERATION_CONTEXT_RE.search(text):
        return ""
    matches = []
    for match in _NAMED_OPERATION_RE.finditer(text):
        name = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;\"'“”‘’")
        # Avoid over-capturing a following sentence word while retaining names
        # such as "Beneath the Surface".
        words = name.split()
        while words and words[-1].casefold() in {
            "and", "of", "for", "the",
            "resulted", "resulting", "led", "targeted", "focused", "began", "started"
        }:
            words.pop()
        if len(words) < 2:
            continue
        slug = _slug(" ".join(words))
        if slug and slug not in {"underway", "in-progress", "ongoing-investigation"}:
            matches.append(slug)
    unique = tuple(dict.fromkeys(matches))
    if len(unique) != 1:
        return ""
    return f"law-enforcement-operation:{unique[0]}"

_DEATH_CONTEXT_RE = re.compile(
    r"\b(?:dies?|died|dead|death|deceased|killed|murdered|suicide|fatal(?:ity)?|"
    r"passes? away|passed away|mourns?|mourning|memorial|tribute|funeral|"
    r"celebration of life|loss of|remember(?:ed|ing)?|honor(?:s|ed|ing)? (?:the )?life)\b",
    re.IGNORECASE,
)

# Named missing-person incidents need a stable subject anchor that survives normal
# publisher drift: a first report may use the full legal name while a follow-up
# drops the middle name and simply says the person "visited" or "went to" the
# last-seen location.  The anchor deliberately uses first + surname only.
_MISSING_PERSON_CONTEXT_RE = re.compile(
    r"\b(?:missing|reported missing|went missing|last seen|disappeared|disappearance|"
    r"search(?:es|ed|ing)? for|seek(?:s|ing)?|locat(?:e|ing)|looking for|help find)\b",
    re.IGNORECASE,
)
_MISSING_PERSON_NAME_WORD = r"[A-Z][A-Za-z'’\-]*"
_MISSING_PERSON_NAME = (
    rf"{_MISSING_PERSON_NAME_WORD}(?:\s+{_MISSING_PERSON_NAME_WORD}){{1,3}}"
)
_MISSING_PERSON_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_MISSING_PERSON_EXCLUDED_NAME_TOKENS = {
    "county", "sheriff", "police", "office", "department", "beach", "island",
    "park", "city", "school", "hospital", "highway", "road", "street",
}
# Title-cased role phrases can look like conventional names to the movement-sentence
# fallback (for example, ``Good Samaritan went to ...``).  Keep these as exact
# phrases rather than token bans so a real person whose surname happens to be
# ``Good`` is not discarded.
_MISSING_PERSON_EXCLUDED_NAME_PHRASES = {
    "good samaritan",
}

def _missing_person_subject_key(text: str) -> str:
    """Return ``first-surname`` for one explicitly framed missing-person subject."""
    raw = str(text or "")
    if not _MISSING_PERSON_CONTEXT_RE.search(raw):
        return ""
    patterns = (
        rf"\b(?:find|finding|locate|locating|search(?:es|ed|ing)? for|looking for|"
        rf"seek(?:s|ing)?|help find)\s+(?:an?\s+|the\s+)?({_MISSING_PERSON_NAME})\b",
        rf"\b({_MISSING_PERSON_NAME})\b[^.!?]{{0,80}}\b(?:reported missing|went missing|"
        rf"is missing|was missing|last seen|disappeared)\b",
        # Follow-up copy often names the subject in a movement sentence immediately
        # after a generic "missing man/woman" lead, e.g. "Michael Debevec visited..."
        rf"\b({_MISSING_PERSON_NAME})(?:,?\s+who\s+)?(?:\s+was)?\s+"
        rf"(?:visiting|visited|went to|had gone to|left for|headed to|traveled to|"
        rf"travelled to|drove to|walked to)\b",
    )
    candidates = []
    for pattern in patterns:
        for match in re.finditer(pattern, raw):
            words = [
                re.sub(r"[^A-Za-z'’.-]", "", token).strip(".'’- ")
                for token in match.group(1).split()
            ]
            words = [word for word in words if word]
            while words and words[-1].casefold().rstrip(".") in _MISSING_PERSON_SUFFIXES:
                words.pop()
            if len(words) < 2:
                continue
            normalized_words = [
                re.sub(r"(?:'s|’s)$", "", word.casefold()).rstrip(".")
                for word in words
            ]
            first = normalized_words[0]
            last = normalized_words[-1]
            normalized_phrase = " ".join(normalized_words)
            if normalized_phrase in _MISSING_PERSON_EXCLUDED_NAME_PHRASES:
                continue
            if any(
                token in _MISSING_PERSON_EXCLUDED_NAME_TOKENS or token == "the"
                for token in normalized_words
            ):
                continue
            if len(first) < 2 or len(last) < 3:
                continue
            candidates.append(f"{_slug(first)}-{_slug(last)}")
    unique = tuple(dict.fromkeys(value for value in candidates if value and "-" in value))
    return unique[0] if len(unique) == 1 else ""

def _named_missing_person_anchor(text: str) -> str:
    subject = _missing_person_subject_key(text)
    return f"missing-person:{subject}" if subject else ""


def title_supports_named_person_death(value: object) -> bool:
    """Return whether title-level text explicitly frames a death story.

    Publisher article bodies frequently include unrelated recommendation rails,
    navigation copy, or embedded headlines.  Those snippets are useful for
    resolving the *name* of a death subject only after the article's own title
    has established that the article is actually about a death.  They must never
    create a death identity by themselves.
    """

    return bool(_DEATH_CONTEXT_RE.search(str(value or "")))

# Common role words are allowed around a person's name but are not part of the
# identity.  The patterns deliberately require a conventional first/last name.
_PERSON_TOKEN = r"[A-Z][A-Za-z'’-]{1,30}"
_FULL_NAME_RE = re.compile(rf"\b({_PERSON_TOKEN}(?:\s+{_PERSON_TOKEN}){{1,2}})\b")

_ORGANIZATION_NAME_WORDS = frozenset(
    {
        "county", "city", "department", "office", "commission", "board",
        "district", "rescue", "police", "sheriff", "school", "university",
        "hospital", "association", "foundation", "authority", "council",
        "administration", "government", "news", "daily", "times", "post",
        "network", "fire", "state", "federal", "court", "senate", "house",
        "department", "agency", "bureau", "committee", "ministry",
    }
)
_PERSON_NAME_STOPWORDS = frozenset(
    {
        "United States", "White House", "Supreme Court", "Federal Reserve",
        "New York", "Los Angeles", "Washington DC", "Washington D.C.",
        "Department Justice", "State Department", "Justice Department",
    }
)

_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "after", "from", "that", "this", "into",
        "over", "says", "said", "new", "county", "state", "man", "woman",
        "home", "story", "news", "local", "report", "reported", "officials",
        "following", "police", "what", "how", "why", "their", "they", "was",
        "were", "has", "have", "had", "near", "more", "than", "some",
    }
)
_PUBLISHER_NOISE = frozenset(
    {
        "ap", "associated", "press", "reuters", "npr", "bbc", "cnn", "cbs",
        "abc", "nbc", "fox", "axios", "politico", "hill", "guardian", "yahoo",
        "aol", "msn", "times", "post", "journal", "herald", "network", "com",
    }
)

_SYNONYMS = {
    "rescued": "rescue",
    "rescuing": "rescue",
    "removed": "rescue",
    "removing": "rescue",
    "remove": "rescue",
    "found": "rescue",
    "animals": "animal",
    "pets": "animal",
    "cats": "cat",
    "dogs": "dog",
    "hoarded": "hoard",
    "hoarding": "hoard",
    "owners": "owner",
    "reunited": "reunite",
    "reuniting": "reunite",
    "recovering": "recover",
    "arrested": "arrest",
    "charged": "arrest",
    "charges": "arrest",
    "houses": "house",
    "carrier": "postalworker",
    "postal": "postalworker",
    "worker": "postalworker",
}

_AREA_TERMS: Mapping[str, tuple[str, ...]] = {}


_DISTINCTIVE_MARKERS = frozenset(
    {"postal_worker", "owner_reunion", "animal_cruelty", "filthy_home", "sheriff"}
)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slug(value: str) -> str:
    return "-".join(_WORD_RE.findall(str(value or "").casefold()))


def _semantic_tokens(text: str) -> frozenset[str]:
    values: set[str] = set()
    for raw in _WORD_RE.findall(text.casefold()):
        token = _SYNONYMS.get(raw, raw)
        if len(token) < 3 or token in _STOPWORDS or token in _PUBLISHER_NOISE:
            continue
        values.add(token)
    if _POSTAL_ROLE_RE.search(text):
        values.add("postalworker")
    return frozenset(values)


def _animal_quantities(text: str) -> tuple[int, ...]:
    return tuple(sorted({int(match.group(1)) for match in _ANIMAL_QUANTITY_RE.finditer(text)}))


def _area_groups(text: str) -> frozenset[str]:
    folded = text.casefold()
    return frozenset(
        area
        for area, terms in _AREA_TERMS.items()
        if any(term in folded for term in terms)
    )


def _markers(text: str, quantities: Iterable[int]) -> frozenset[str]:
    folded = text.casefold()
    result: set[str] = set()
    if _HOARDING_RE.search(folded):
        result.add("hoarding")
    if _POSTAL_ROLE_RE.search(folded):
        result.add("postal_worker")
    if "animal cruelty" in folded:
        result.add("animal_cruelty")
    if re.search(r"reunit", folded) and re.search(r"owner|pet|cat|dog", folded):
        result.add("owner_reunion")
    if re.search(r"filth|feces|faeces|cages|no ac", folded):
        result.add("filthy_home")
    if "sheriff" in folded:
        result.add("sheriff")
    if re.search(r"arrest|charged", folded):
        result.add("arrest")
    if any(value >= 50 for value in quantities) or "nearly 100" in folded or "dozens" in folded:
        result.add("large_rescue")
    return frozenset(result)


def _valid_person_name(value: object) -> str:
    name = " ".join(str(value or "").strip().split())
    if not name or name in _PERSON_NAME_STOPWORDS:
        return ""
    words = name.replace(".", "").split()
    # Proper-name scans often capture a leading occupational title. Remove only
    # a narrow role vocabulary; never drop arbitrary first words.
    if len(words) == 3 and re.sub(r"[^a-z]", "", words[0].casefold()) in {
        "firefighter", "paramedic", "officer", "deputy", "chief", "captain",
        "detective", "teacher", "coach", "doctor", "dr", "senator", "mayor",
    }:
        words = words[1:]
        name = " ".join(words)
    # Use a deliberately conservative personal-name shape. Two-word names cover
    # the overwhelming majority of incident subjects. A three-word name is
    # accepted only when the middle element is an initial.
    if len(words) == 3 and len(re.sub(r"[^A-Za-z]", "", words[1])) != 1:
        return ""
    if not 2 <= len(words) <= 3:
        return ""
    folded_words = [re.sub(r"[^a-z]", "", word.casefold()) for word in words]
    if any(not word for word in folded_words):
        return ""
    rejected = {
        "county", "city", "department", "office", "commission", "board",
        "district", "rescue", "police", "sheriff", "school", "university",
        "hospital", "association", "foundation", "authority", "council",
        "administration", "government", "news", "daily", "times", "post",
        "network", "fire", "paramedic", "firefighter", "dies", "died", "death",
        "mourns", "mourn", "following", "personal", "tragedy", "officials",
        "service", "resident", "who", "began", "his", "her", "career",
        "here", "after", "daily", "times", "post", "press", "media",
    }
    if set(folded_words) & rejected:
        return ""
    # Publisher acronyms and sentence fragments are not people.
    if any(word.isupper() and len(word) >= 3 for word in words):
        return ""
    return name


def _named_people(text: str, entities: Iterable[object] = ()) -> tuple[str, ...]:
    """Extract the central named subject of death/mourning coverage.

    Articles often quote commissioners, spokespeople and family representatives near
    death language. Treating every nearby proper name as an incident subject caused
    the real person to become ambiguous. Candidates are therefore scored by repeated
    mentions, headline position and explicit subject-role/death syntax, while quoted
    officials and ``NAME said`` constructions are penalized.
    """
    scores: dict[str, float] = {}
    display: dict[str, str] = {}
    for match in _FULL_NAME_RE.finditer(text):
        valid = _valid_person_name(match.group(1))
        if not valid:
            continue
        start, end = match.span()
        context = text[max(0, start - 120): min(len(text), end + 120)]
        if not _DEATH_CONTEXT_RE.search(context):
            continue
        key = _slug(valid)
        display.setdefault(key, valid)
        score = 2.0
        # Headlines/source headlines are concatenated first and provide the strongest
        # framing evidence. Repeated appearances further identify the true subject.
        if start < 420:
            score += 4.0
        if re.search(
            rf"(?:firefighter|paramedic|officer|deputy|teacher|coach|doctor|chief|captain)"
            rf"[/\s]+{re.escape(valid)}\b",
            context,
            re.IGNORECASE,
        ):
            score += 5.0
        if re.search(
            rf"\b{re.escape(valid)}\b[^.!?]{{0,45}}\b(?:dies?|died|death|dead|killed|suicide|"
            rf"passed away|mourned|remembered)\b",
            context,
            re.IGNORECASE,
        ) or re.search(
            rf"\b(?:death|loss|mourning|mourns?|memorial|tribute)\b[^.!?]{{0,55}}\b{re.escape(valid)}\b",
            context,
            re.IGNORECASE,
        ):
            score += 5.0
        # Names introducing attribution are generally sources, not the deceased.
        if re.search(
            rf"\b{re.escape(valid)}\b\s+(?:said|says|told|wrote|added|called|announced|explained)\b",
            context,
            re.IGNORECASE,
        ):
            score -= 7.0
        if re.search(
            rf"(?:spokes(?:man|woman|person)|administrator|commissioner|chairman|mayor|chief|"
            rf"sheriff|detective|attorney)\s+{re.escape(valid)}\b",
            context,
            re.IGNORECASE,
        ):
            score -= 6.0
        scores[key] = scores.get(key, 0.0) + score

    if scores:
        ranked = sorted(scores, key=lambda key: (scores[key], key), reverse=True)
        top = ranked[0]
        second_score = scores[ranked[1]] if len(ranked) > 1 else float("-inf")
        # Require a clear central subject when several names occur. Ambiguous stories
        # fail open rather than merging different deaths.
        if scores[top] >= 5.0 and (len(ranked) == 1 or scores[top] - second_score >= 3.0):
            return (display[top],)
        return ()

    # Entity extraction is a fallback for headlines such as "firefighter dies
    # following personal tragedy" where the person's name appears only in structured
    # extraction. It remains conservative and requires one unique valid name.
    candidates = [
        valid
        for valid in (_valid_person_name(entity) for entity in entities)
        if valid
    ]
    unique = {_slug(value): value for value in candidates}
    return tuple(unique.values()) if len(unique) == 1 else ()


def _infrastructure_condition_anchor(text: str) -> str:
    if not _INFRASTRUCTURE_ASSET_RE.search(text):
        return ""
    if not _INFRASTRUCTURE_NONOP_RE.search(text):
        return ""
    asset_type = ""
    for pattern, canonical in _INFRASTRUCTURE_ASSET_CANONICAL:
        if pattern.search(text):
            asset_type = canonical
            break
    if not asset_type:
        return ""
    roads: list[str] = []
    for match in _NAMED_ROAD_RE.finditer(text):
        road = match.group(1).strip()
        if len(road.split()) < 2:
            continue
        roads.append(_slug(road))
    unique_roads = tuple(dict.fromkeys(roads))
    if len(unique_roads) != 1:
        return ""
    return f"infrastructure-condition:{asset_type}:{unique_roads[0]}:nonoperational"


def _named_person_death_anchor(
    text: str,
    *,
    entities: Iterable[object] = (),
) -> tuple[str, tuple[str, ...]]:
    if not _DEATH_CONTEXT_RE.search(text):
        return "", ()
    people = _named_people(text, entities)
    if len(people) != 1:
        return "", people
    return f"named-person-death:{_slug(people[0])}", people


def incident_anchor_write_authoritative(anchor_key: object) -> bool:
    """Return whether a structured incident anchor may *by itself* own a canonical URL.

    Incident anchors serve two different jobs: candidate retrieval and destructive
    publication identity.  Only anchors that encode an incident-specific subject or
    asset are strong enough for the latter.  Broad family+jurisdiction anchors remain
    useful for retrieving possible matches, but they must be independently
    corroborated before any merge, redirect, overwrite, or live-surface canonical
    rebind.  Unknown future anchor families fail closed to candidate-only.
    """

    value = str(anchor_key or "").strip().casefold()
    if not value:
        return False

    # Named subjects identify one continuing real-world incident rather than a
    # jurisdiction-wide class of incidents.
    for prefix in (
        "missing-person:",
        "named-person-death:",
        "law-enforcement-operation:",
    ):
        if value.startswith(prefix):
            subject = value[len(prefix):].strip(" :-")
            return bool(subject)

    # Infrastructure anchors encode an asset class, one specific road/corridor, and
    # the failure state (currently ``nonoperational``).  That is materially narrower
    # than a county/city class key and preserves the established traffic-signal
    # continuity contract.
    if value.startswith("infrastructure-condition:"):
        parts = [part.strip() for part in value.split(":")]
        return len(parts) >= 4 and bool(parts[1]) and bool(parts[2]) and bool(parts[3])

    # ``mass-animal-hoarding:<area>`` intentionally contains only an event family
    # plus a broad geography. Multiple unrelated hoarding cases can occur in the same
    # county, so this key is retrieval evidence only.
    if value.startswith("mass-animal-hoarding:"):
        return False

    return False


def incident_anchor_key(
    *,
    titles: Iterable[object],
    facts: Iterable[object] = (),
    locations: Iterable[object] = (),
    agencies: Iterable[object] = (),
    event_types: Iterable[object] = (),
    entities: Iterable[object] = (),
    body: object = "",
) -> str:
    """Return a durable structured anchor for one article, or ``""``.

    The function is safe to call from the feed pipeline, archive migration and
    final rendering pass.  It does not depend on model output or mutable story IDs.
    """

    title_values = [str(value or "").strip() for value in titles if str(value or "").strip()]
    full_text = " | ".join(
        [
            *title_values,
            str(body or ""),
            *[str(value or "") for value in facts],
            *[str(value or "") for value in locations],
            *[str(value or "") for value in agencies],
            *[str(value or "") for value in event_types],
            *[str(value or "") for value in entities],
        ]
    )
    # Death identity is title-gated. The body/entities may resolve the person's
    # name for a headline such as "Firefighter dies following personal tragedy",
    # but unrelated body/sidebar text can no longer turn an animal-cruelty,
    # business, sports, or other story into ``named-person-death:*``.
    if title_supports_named_person_death(" | ".join(title_values)):
        anchor, _people = _named_person_death_anchor(full_text, entities=entities)
        if anchor:
            return anchor

    # Missing-person identity is likewise title/context gated, but its subject may
    # appear only in the body after a generic "missing man" headline.
    missing_anchor = _named_missing_person_anchor(full_text)
    if missing_anchor:
        return missing_anchor

    operation_anchor = _named_law_enforcement_operation_anchor(full_text)
    if operation_anchor:
        return operation_anchor

    infrastructure_anchor = _infrastructure_condition_anchor(full_text)
    if infrastructure_anchor:
        return infrastructure_anchor

    quantities = _animal_quantities(full_text)
    family = _animal_incident_family(" | ".join(title_values), quantities)
    if family == "mass_animal_hoarding":
        areas = sorted(_area_groups(full_text))
        area = areas[0] if len(areas) == 1 else ""
        # A mass-rescue anchor without a local area remains comparison-driven;
        # avoid emitting an overly broad global key.
        if area:
            return f"mass-animal-hoarding:{area}"
    return ""


def _animal_incident_family(title_text: str, quantities: Iterable[int]) -> str:
    animal = bool(_ANIMAL_RE.search(title_text))
    plural_animal = bool(_PLURAL_ANIMAL_RE.search(title_text))
    explicit_hoarding = bool(_HOARDING_RE.search(title_text))
    action = bool(_ANIMAL_ACTION_RE.search(title_text))
    strong_mass_anchor = bool(
        _POSTAL_ROLE_RE.search(title_text) or any(value >= 50 for value in quantities)
    )
    if animal and (explicit_hoarding or (plural_animal and action and strong_mass_anchor)):
        return "mass_animal_hoarding"
    return ""


@dataclass(frozen=True, slots=True)
class IncidentSignature:
    family: str
    anchor_key: str
    subjects: tuple[str, ...]
    tokens: frozenset[str]
    quantities: tuple[int, ...]
    area_groups: frozenset[str]
    markers: frozenset[str]
    published_at: tuple[datetime, ...]
    evidence_title_count: int = 0
    total_title_count: int = 0

    @property
    def supported(self) -> bool:
        return bool(self.family)

    @property
    def evidence_ratio(self) -> float:
        return (
            self.evidence_title_count / self.total_title_count
            if self.total_title_count
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class IncidentIdentityMatch:
    matched: bool
    confidence: float
    reason: str
    decision_trace: tuple[str, ...]
    story_id: str | None = None


def build_incident_signature(
    *,
    titles: Iterable[object],
    facts: Iterable[object] = (),
    locations: Iterable[object] = (),
    agencies: Iterable[object] = (),
    event_types: Iterable[object] = (),
    entities: Iterable[object] = (),
    published_at: Iterable[object] = (),
    body: object = "",
) -> IncidentSignature:
    title_values = [str(value or "").strip() for value in titles if str(value or "").strip()]
    title_text = " | ".join(title_values)
    full_text = " | ".join(
        [
            title_text,
            str(body or ""),
            *[str(value or "") for value in facts],
            *[str(value or "") for value in locations],
            *[str(value or "") for value in agencies],
            *[str(value or "") for value in event_types],
            *[str(value or "") for value in entities],
        ]
    )
    quantities = _animal_quantities(full_text)
    timestamps = tuple(
        parsed
        for parsed in (_parse_datetime(value) for value in published_at)
        if parsed is not None
    )

    anchor, people = ("", ())
    if title_supports_named_person_death(title_text):
        anchor, people = _named_person_death_anchor(full_text, entities=entities)
    if anchor:
        evidence_titles = sum(bool(_DEATH_CONTEXT_RE.search(value)) for value in title_values)
        return IncidentSignature(
            family="named_person_death",
            anchor_key=anchor,
            subjects=people,
            tokens=_semantic_tokens(full_text),
            quantities=quantities,
            area_groups=_area_groups(full_text),
            markers=_markers(full_text, quantities),
            published_at=timestamps,
            evidence_title_count=evidence_titles,
            total_title_count=len(title_values),
        )

    infrastructure_anchor = _infrastructure_condition_anchor(full_text)
    if infrastructure_anchor:
        evidence_titles = sum(
            bool(_INFRASTRUCTURE_ASSET_RE.search(value) and _INFRASTRUCTURE_NONOP_RE.search(value))
            for value in title_values
        )
        return IncidentSignature(
            family="infrastructure_condition",
            anchor_key=infrastructure_anchor,
            subjects=(),
            tokens=_semantic_tokens(full_text),
            quantities=quantities,
            area_groups=_area_groups(full_text),
            markers=_markers(full_text, quantities),
            published_at=timestamps,
            evidence_title_count=evidence_titles,
            total_title_count=len(title_values),
        )

    family = _animal_incident_family(title_text, quantities)
    return IncidentSignature(
        family=family,
        anchor_key=incident_anchor_key(
            titles=title_values,
            facts=facts,
            locations=locations,
            agencies=agencies,
            event_types=event_types,
            entities=entities,
            body=body,
        ),
        subjects=(),
        tokens=_semantic_tokens(full_text),
        quantities=quantities,
        area_groups=_area_groups(full_text),
        markers=_markers(full_text, quantities),
        published_at=timestamps,
        evidence_title_count=len(title_values) if family else 0,
        total_title_count=len(title_values),
    )


def build_story_incident_signature(story: Mapping[str, Any]) -> IncidentSignature:
    timeline = list(story.get("timeline", ()) or ())
    candidate_titles = [
        candidate.get("title", "")
        for candidate in story.get("title_candidates", ()) or ()
        if isinstance(candidate, Mapping)
    ]
    timeline_titles = [
        entry.get("title", "") for entry in timeline if isinstance(entry, Mapping)
    ]
    return build_incident_signature(
        titles=[
            story.get("canonical_title", ""),
            *story.get("titles", ()),
            *candidate_titles,
            *timeline_titles,
        ],
        facts=story.get("facts", ()),
        locations=story.get("locations", ()),
        agencies=story.get("agencies", ()),
        event_types=story.get("event_types", ()),
        entities=story.get("entities", ()),
        published_at=[entry.get("published_at") for entry in timeline],
    )


def named_person_death_subjects(story: Mapping[str, Any]) -> tuple[str, ...]:
    """Return unique named death subjects visible anywhere in a story record."""

    signature = build_story_incident_signature(story)
    if signature.family != "named_person_death":
        return ()
    return signature.subjects


def timeline_incident_anchor(
    entry: Mapping[str, Any],
    *,
    inherited_subjects: Iterable[object] = (),
) -> str:
    """Resolve a timeline entry without inheriting unrelated story text.

    A no-name headline such as "firefighter/paramedic dies following personal
    tragedy" may inherit one unambiguous named subject from the containing
    story.  A title without death/mourning language never inherits the anchor,
    which lets repair detach an unrelated fire or animal-rescue entry from a
    contaminated story.
    """

    title = str(entry.get("title") or "").strip()
    if not title or not _DEATH_CONTEXT_RE.search(title):
        return ""
    direct = incident_anchor_key(titles=(title,), entities=())
    if direct:
        return direct
    subjects = tuple(
        value for value in (_valid_person_name(item) for item in inherited_subjects) if value
    )
    unique = { _slug(value): value for value in subjects }
    if len(unique) == 1:
        return f"named-person-death:{next(iter(unique))}"
    return ""


def _quantity_compatible(left: Iterable[int], right: Iterable[int]) -> bool:
    return any(
        abs(a - b) <= max(30, int(0.30 * max(a, b)))
        for a in left
        for b in right
    )


def compare_incident_signatures(
    left: IncidentSignature,
    right: IncidentSignature,
    *,
    max_age_days: float = 7.0,
) -> IncidentIdentityMatch:
    if not left.supported or left.family != right.family:
        return IncidentIdentityMatch(False, 0.0, "No supported shared incident family", ())

    if left.family == "named_person_death":
        if not left.anchor_key or left.anchor_key != right.anchor_key:
            return IncidentIdentityMatch(
                False,
                0.0,
                "Named-person death subjects differ",
                (
                    f"Left anchor: {left.anchor_key or 'none'}",
                    f"Right anchor: {right.anchor_key or 'none'}",
                ),
            )
        age_gap_days: float | None = None
        if left.published_at and right.published_at:
            age_gap_days = min(
                abs((a - b).total_seconds()) / 86400.0
                for a in left.published_at
                for b in right.published_at
            )
        return IncidentIdentityMatch(
            True,
            0.995,
            "Exact named-person death anchor matched",
            (
                "Incident family: named_person_death",
                f"Incident anchor: {left.anchor_key}",
                "Named subject match: true",
                f"Left evidence ratio: {left.evidence_ratio:.2f}",
                f"Right evidence ratio: {right.evidence_ratio:.2f}",
                (
                    f"Publication gap days: {age_gap_days:.2f}"
                    if age_gap_days is not None
                    else "Publication gap days: unknown"
                ),
                "Confidence: 0.99",
            ),
        )

    if left.family == "infrastructure_condition":
        if not left.anchor_key or left.anchor_key != right.anchor_key:
            return IncidentIdentityMatch(
                False,
                0.0,
                "Named infrastructure conditions differ",
                (
                    f"Left anchor: {left.anchor_key or 'none'}",
                    f"Right anchor: {right.anchor_key or 'none'}",
                ),
            )
        return IncidentIdentityMatch(
            True,
            0.995,
            "Exact named-infrastructure condition anchor matched",
            (
                "Incident family: infrastructure_condition",
                f"Incident anchor: {left.anchor_key}",
                "Named asset and operational state match: true",
                "Confidence: 0.99",
            ),
        )

    if left.area_groups and right.area_groups and not (left.area_groups & right.area_groups):
        return IncidentIdentityMatch(
            False,
            0.0,
            "Conflicting local areas prevent incident consolidation",
            (
                f"Incident family: {left.family}",
                f"Left areas: {', '.join(sorted(left.area_groups))}",
                f"Right areas: {', '.join(sorted(right.area_groups))}",
                "Location conflict: true",
            ),
        )

    age_gap_days: float | None = None
    if left.published_at and right.published_at:
        age_gap_days = min(
            abs((a - b).total_seconds()) / 86400.0
            for a in left.published_at
            for b in right.published_at
        )
        if age_gap_days > max_age_days:
            return IncidentIdentityMatch(
                False,
                0.0,
                "Incident coverage falls outside the safe consolidation window",
                (
                    f"Incident family: {left.family}",
                    f"Minimum publication gap days: {age_gap_days:.2f}",
                    f"Maximum allowed gap days: {max_age_days:.2f}",
                ),
            )

    shared_tokens = left.tokens & right.tokens
    overlap = (
        len(shared_tokens) / min(len(left.tokens), len(right.tokens))
        if left.tokens and right.tokens
        else 0.0
    )
    quantity_match = _quantity_compatible(left.quantities, right.quantities)
    shared_markers = left.markers & right.markers
    distinctive_match = bool(shared_markers & _DISTINCTIVE_MARKERS)
    concept_match = overlap >= 0.25 and len(shared_tokens) >= 2
    area_match = bool(left.area_groups & right.area_groups)
    strong_evidence_count = sum((quantity_match, distinctive_match, concept_match))

    if area_match:
        matched = bool(
            strong_evidence_count >= 1
            or ("hoarding" in shared_markers and len(shared_tokens) >= 2)
        )
    elif left.area_groups or right.area_groups:
        matched = bool(
            strong_evidence_count >= 2
            or (quantity_match and "hoarding" in shared_markers)
        )
    else:
        matched = bool(strong_evidence_count >= 2 and (quantity_match or distinctive_match))

    confidence = 0.40
    confidence += 0.18 if area_match else 0.0
    confidence += 0.18 if quantity_match else 0.0
    confidence += 0.15 if distinctive_match else 0.0
    confidence += 0.14 if concept_match else 0.0
    confidence += 0.05 if age_gap_days is not None and age_gap_days <= max_age_days else 0.0
    confidence = min(0.99, confidence) if matched else 0.0

    trace = (
        f"Incident family: {left.family}",
        f"Area match: {area_match}",
        f"Quantity compatible: {quantity_match}",
        f"Distinctive marker match: {distinctive_match}",
        f"Concept overlap: {overlap:.2f}",
        f"Shared concept tokens: {', '.join(sorted(shared_tokens)[:12]) or 'none'}",
        f"Shared markers: {', '.join(sorted(shared_markers)) or 'none'}",
        f"Publication gap days: {age_gap_days:.2f}" if age_gap_days is not None else "Publication gap days: unknown",
        f"Confidence: {confidence:.2f}",
    )
    return IncidentIdentityMatch(
        matched,
        confidence,
        "High-confidence deterministic incident signature matched"
        if matched
        else "Incident evidence did not meet the conservative consolidation threshold",
        trace,
    )


def compare_story_incidents(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> IncidentIdentityMatch:
    return compare_incident_signatures(
        build_story_incident_signature(left),
        build_story_incident_signature(right),
    )


def _story_anchor_evidence(story: Mapping[str, Any], anchor_key: str) -> tuple[int, int]:
    subjects = named_person_death_subjects(story)
    timeline = [entry for entry in story.get("timeline", ()) if isinstance(entry, Mapping)]
    matching = sum(
        timeline_incident_anchor(entry, inherited_subjects=subjects) == anchor_key
        for entry in timeline
    )
    return matching, len(timeline)


def find_matching_incident_story(
    *,
    title: str,
    facts: Iterable[str] = (),
    locations: Iterable[str] = (),
    agencies: Iterable[str] = (),
    event_types: Iterable[str] = (),
    entities: Iterable[str] = (),
    published_at: object = None,
    stories: Iterable[Mapping[str, Any]],
) -> IncidentIdentityMatch:
    incoming = build_incident_signature(
        titles=(title,),
        facts=facts,
        locations=locations,
        agencies=agencies,
        event_types=event_types,
        entities=entities,
        published_at=(published_at,),
    )
    if not incoming.supported:
        return IncidentIdentityMatch(False, 0.0, "Incoming article has no supported incident signature", ())

    matches: list[tuple[IncidentIdentityMatch, float, int, int]] = []
    for story in stories:
        match = compare_incident_signatures(incoming, build_story_incident_signature(story))
        if not match.matched:
            continue
        matching_entries, total_entries = _story_anchor_evidence(story, incoming.anchor_key)
        purity = matching_entries / total_entries if total_entries else 0.0
        matches.append(
            (
                IncidentIdentityMatch(
                    True,
                    match.confidence,
                    match.reason,
                    match.decision_trace,
                    str(story.get("story_id") or "") or None,
                ),
                purity,
                matching_entries,
                -int(re.sub(r"\D", "", str(story.get("story_id") or "999999")) or 999999),
            )
        )
    if not matches:
        return IncidentIdentityMatch(False, 0.0, "No existing story met the incident identity threshold", ())
    # Prefer a clean story record over a larger but contaminated aggregate.
    return max(matches, key=lambda item: (item[1], item[2], item[0].confidence, item[3]))[0]
