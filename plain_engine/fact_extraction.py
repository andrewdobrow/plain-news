"""Deterministic article fact extraction for U.S.-wide and world coverage."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawArticle:
    article_id: str
    title: str
    body: str
    source: str
    url: str
    published_at: datetime
    county: str | None = None  # retained for API compatibility; Plain does not rank by county
    is_custom: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedArticleFacts:
    article_id: str
    source: str
    is_custom: bool
    facts: tuple[str, ...]
    locations: tuple[str, ...]
    agencies: tuple[str, ...]
    event_types: tuple[str, ...]
    entities: tuple[str, ...] = ()


_US_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
    "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma",
    "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee",
    "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia",
)

_AGENCIES = (
    (re.compile(r"\b(?:u\.?s\.?\s+)?department of justice\b|\bjustice department\b", re.I), "U.S. Department of Justice"),
    (re.compile(r"\bfederal bureau of investigation\b|\bFBI\b"), "FBI"),
    (re.compile(r"\bdepartment of defense\b|\bPentagon\b", re.I), "U.S. Department of Defense"),
    (re.compile(r"\bdepartment of state\b|\bState Department\b", re.I), "U.S. Department of State"),
    (re.compile(r"\bdepartment of the treasury\b|\bTreasury Department\b", re.I), "U.S. Department of the Treasury"),
    (re.compile(r"\bfederal reserve\b|\bFed\b"), "Federal Reserve"),
    (re.compile(r"\bcenters for disease control(?: and prevention)?\b|\bCDC\b"), "CDC"),
    (re.compile(r"\bfood and drug administration\b|\bFDA\b"), "FDA"),
    (re.compile(r"\benvironmental protection agency\b|\bEPA\b"), "EPA"),
    (re.compile(r"\bnational aeronautics and space administration\b|\bNASA\b"), "NASA"),
    (re.compile(r"\bnational oceanic and atmospheric administration\b|\bNOAA\b"), "NOAA"),
    (re.compile(r"\bsupreme court\b", re.I), "U.S. Supreme Court"),
    (re.compile(r"\bwhite house\b", re.I), "White House"),
    (re.compile(r"\bu\.?s\.? senate\b|\bSenate\b"), "U.S. Senate"),
    (re.compile(r"\bu\.?s\.? house(?: of representatives)?\b|\bHouse of Representatives\b", re.I), "U.S. House of Representatives"),
)

_NUMBER_WORDS = {"one":"1","two":"2","three":"3","four":"4","five":"5","six":"6","seven":"7","eight":"8","nine":"9","ten":"10"}


def _number_to_digit(value: str) -> str:
    return _NUMBER_WORDS.get(value.lower(), value)


def _unique(values):
    seen=set(); out=[]
    for value in values:
        value=str(value).strip()
        if value and value not in seen:
            seen.add(value); out.append(value)
    return tuple(out)


def _is_active_missing_person_incident(text: str) -> bool:
    lower = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if "missing" not in lower and "last seen" not in lower:
        return False
    patterns = (
        r"\bmissing[- ](?:person|man|woman|boy|girl|teen(?:ager)?|child|adult|student)\b",
        r"\b(?:man|woman|boy|girl|teen(?:ager)?|child|adult|student|person)\b[^.!?]{0,50}\b(?:is|was|remains|reported)\s+missing\b",
        r"\blast seen\b", r"\bamber alert\b", r"\bsilver alert\b",
        r"\bsearch(?:ing)? for\b[^.!?]{0,100}\b(?:man|woman|child|teen|person)\b",
    )
    return any(re.search(p, lower, re.I) for p in patterns)


def _extract_locations(text: str) -> list[str]:
    locations=[]
    lower=text.casefold()
    for state in _US_STATES:
        if re.search(rf"\b{re.escape(state.casefold())}\b", lower):
            locations.append(state)
    if re.search(r"\bWashington,?\s+D\.?C\.?\b", text, re.I):
        locations.append("Washington, D.C.")
    # City/state constructions are useful incident anchors without needing a full gazetteer.
    state_alt = "|".join(re.escape(s) for s in _US_STATES)
    for match in re.finditer(rf"\b([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){{0,2}}),\s*({state_alt})\b", text):
        locations.append(f"{match.group(1)}, {match.group(2)}")
    return locations


def extract_article_facts(article: RawArticle) -> ExtractedArticleFacts:
    text=f"{article.title} {article.body}"
    lower=text.lower()
    facts=[]; locations=_extract_locations(text); agencies=[]; event_types=[]; entities=[]

    for pattern, agency in _AGENCIES:
        if pattern.search(text): agencies.append(agency)

    # Quantified casualties/arrests are durable facts.
    for m in re.finditer(r"\b(\d{1,5}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:people\s+)?(?:were\s+)?(killed|dead|injured|arrested)\b", text, re.I):
        facts.append(f"{_number_to_digit(m.group(1))} people {m.group(2).lower()}")
    for m in re.finditer(r"\b(\d{1,3})[- ]year[- ]old\b", text, re.I):
        facts.append(f"{m.group(1)}-year-old")

    event_patterns = (
        ("election", r"\b(?:election|primary|runoff|ballot|polls?)\b"),
        ("legislation", r"\b(?:bill|legislation|law|act)\b.{0,80}\b(?:passes|passed|signed|approved|vetoed|blocked)\b|\b(?:passes|passed|signed|approved|vetoed|blocked)\b.{0,80}\b(?:bill|legislation|law|act)\b"),
        ("court ruling", r"\b(?:supreme court|appeals court|judge|court)\b.{0,80}\b(?:rules|ruled|blocks|blocked|upholds|strikes down|orders)\b"),
        ("war or military action", r"\b(?:war|invasion|airstrike|air strike|missile strike|military strike|ceasefire)\b"),
        ("cybersecurity incident", r"\b(?:cyberattack|cyber attack|data breach|ransomware|hack(?:ed|ing)?)\b"),
        ("business transaction", r"\b(?:merger|acquisition|acquires|acquired|buyout|bankruptcy)\b"),
        ("labor action", r"\b(?:strike|walkout|union vote|collective bargaining)\b"),
        ("shooting", r"\b(?:shooting|shot|gunfire)\b"),
        ("traffic crash", r"\b(?:crash|collision|wreck)\b"),
        ("fire", r"\b(?:fire|blaze|wildfire|arson)\b"),
        ("natural disaster", r"\b(?:hurricane|tornado|earthquake|tsunami|flood|wildfire)\b"),
        ("arrest", r"\b(?:arrested|arrest|charged|indicted)\b"),
        ("death", r"\b(?:killed|dead|dies|died|death)\b"),
    )
    for name, pattern in event_patterns:
        if re.search(pattern, lower, re.I):
            event_types.append(name)

    if _is_active_missing_person_incident(text):
        event_types.append("missing person")
        facts.append("missing person")

    if "no injuries" in lower:
        facts.append("no injuries reported")

    # Proper-name extraction supplies flexible identity anchors for people,
    # companies, teams, agencies, courts, projects, products and places.
    proper_name = r"\b[A-Z][A-Za-z0-9&.'’/-]+(?:\s+(?:of|the|and|for|[A-Z][A-Za-z0-9&.'’/-]+)){1,5}\b"
    for match in re.finditer(proper_name, text):
        value=re.sub(r"\s+", " ", match.group(0)).strip(" ,.;:")
        if 5 <= len(value) <= 100:
            entities.append(value)

    return ExtractedArticleFacts(
        article_id=article.article_id,
        source=article.source,
        is_custom=article.is_custom,
        facts=_unique(facts),
        locations=_unique(locations),
        agencies=_unique(agencies),
        event_types=_unique(event_types),
        entities=_unique(entities),
    )
