"""Deterministic audience-relevance classification for Plain's U.S.-wide newsroom."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class NationalRelevance:
    """How directly a story fits Plain's U.S.-wide general-news mission."""

    scope: str
    score: int
    regions: tuple[str, ...] = ()
    places: tuple[str, ...] = ()


_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "tennessee", "texas", "utah", "vermont", "virginia", "washington",
    "west virginia", "wisconsin", "wyoming", "district of columbia",
    "washington dc", "washington d.c.",
}

_FEDERAL_SIGNALS = (
    "white house", "congress", "senate", "house of representatives",
    "supreme court", "justice department", "department of justice",
    "pentagon", "department of defense", "state department", "treasury",
    "federal reserve", "cdc", "fda", "epa", "fbi", "nasa", "noaa",
    "federal government", "u.s. government", "us government",
    "united states", "u.s.", " us ",
)

_GLOBAL_MAJOR_SIGNALS = (
    "war", "invasion", "ceasefire", "missile", "nuclear", "hostage",
    "earthquake", "tsunami", "hurricane", "mass casualty", "pandemic",
    "global markets", "oil prices", "trade war", "sanctions",
)

_FOREIGN_SIGNALS = (
    "ukraine", "russia", "china", "taiwan", "israel", "gaza", "iran",
    "european union", "united kingdom", "france", "germany", "india",
    "japan", "south korea", "north korea", "mexico", "canada", "brazil",
    "australia", "africa", "middle east", "europe", "asia",
)


def _find_states(text: str) -> tuple[str, ...]:
    found = []
    lower = text.casefold()
    for state in sorted(_US_STATES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(state)}\b", lower):
            found.append(state.title().replace("D.C.", "D.C."))
    return tuple(dict.fromkeys(found))


def classify_national_relevance(*, locations=(), region: str | None = None, text: str = "") -> NationalRelevance:
    """Classify relevance without privileging one city/county.

    Plain serves a national U.S. audience while still covering major world news.
    Federal/nationwide stories rank highest, multi-state stories just below them,
    state/regional U.S. stories remain fully eligible, and major international
    developments retain substantial weight.
    """

    haystack = " ".join([str(text or ""), str(region or ""), *[str(x) for x in locations]])
    lower = " " + re.sub(r"\s+", " ", haystack.casefold()).strip() + " "
    states = _find_states(lower)
    federal = any(signal in lower for signal in _FEDERAL_SIGNALS)
    foreign = any(re.search(rf"\b{re.escape(signal)}\b", lower) for signal in _FOREIGN_SIGNALS)
    global_major = any(signal in lower for signal in _GLOBAL_MAJOR_SIGNALS)

    if federal:
        return NationalRelevance("us_national", 100, states, states)
    if len(states) >= 2:
        return NationalRelevance("us_multistate", 95, states, states)
    if len(states) == 1:
        return NationalRelevance("us_state_regional", 82, states, states)
    if foreign and global_major:
        return NationalRelevance("global_major", 88)
    if foreign:
        return NationalRelevance("international", 72)
    if global_major:
        return NationalRelevance("global_major", 85)
    return NationalRelevance("unknown", 60)
