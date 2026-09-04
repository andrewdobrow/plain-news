"""Deterministic story-importance scoring for Plain's national newsroom."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Iterable, Mapping


class ImportanceLevel(str, Enum):
    BREAKING = "breaking"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class ImportanceReason:
    code: str
    label: str
    points: int

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "label": self.label, "points": self.points}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImportanceReason":
        return cls(
            code=str(value.get("code", "unknown")),
            label=str(value.get("label", "Unknown signal")),
            points=int(value.get("points", 0)),
        )


@dataclass(frozen=True, slots=True)
class StoryImportance:
    score: int
    level: ImportanceLevel
    reasons: tuple[ImportanceReason, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level.value,
            "reasons": [reason.to_dict() for reason in self.reasons],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "StoryImportance":
        if not isinstance(value, Mapping):
            return cls(score=0, level=ImportanceLevel.LOW)
        try:
            level = ImportanceLevel(str(value.get("level", "low")))
        except ValueError:
            level = ImportanceLevel.LOW
        raw_reasons = value.get("reasons", [])
        reasons = tuple(
            ImportanceReason.from_dict(item)
            for item in raw_reasons
            if isinstance(item, Mapping)
        )
        return cls(
            score=max(0, min(100, int(value.get("score", 0)))),
            level=level,
            reasons=reasons,
        )


class StoryImportanceEngine:
    """Score consequence and national significance with transparent rules.

    Plain should not let a dramatic single local incident outrank nationwide
    policy, major geopolitical developments, macroeconomic changes, or true mass
    casualty/disaster events. The model can still provide an urgency score; this
    deterministic layer supplies a stable newsroom baseline for identity/ranking.
    """

    _FEDERAL_ACTION = {
        "white house", "congress", "senate", "house of representatives",
        "supreme court", "executive order", "federal reserve", "justice department",
        "department of justice", "pentagon", "state department", "federal law",
        "national security", "impeachment", "government shutdown",
    }
    _ELECTION = {
        "election", "primary", "ballot", "presidential", "senate race",
        "house race", "governor", "electoral", "campaign",
    }
    _MACROECONOMY = {
        "inflation", "interest rate", "federal reserve", "jobs report",
        "unemployment", "recession", "tariff", "trade war", "oil prices",
        "stock market", "dow jones", "s&p 500", "nasdaq", "gdp",
    }
    _WAR_DIPLOMACY = {
        "war", "invasion", "airstrike", "missile", "ceasefire", "hostage",
        "sanctions", "nuclear", "peace talks", "treaty", "military action",
    }
    _MAJOR_DISASTER = {
        "hurricane", "tornado", "earthquake", "tsunami", "wildfire",
        "flash flood", "evacuation order", "state of emergency", "pandemic",
    }
    _PUBLIC_SAFETY = {
        "shooting", "stabbing", "fire", "crash", "collision", "explosion",
        "missing person", "amber alert", "hazmat", "armed suspect", "lockdown",
    }
    _COURT_ACTION = {
        "indicted", "convicted", "sentenced", "supreme court", "appeals court",
        "lawsuit", "verdict", "pleaded guilty", "pleads guilty",
    }
    _MAJOR_BUSINESS_TECH = {
        "merger", "acquisition", "bankruptcy", "recall", "data breach",
        "cyberattack", "antitrust", "layoffs", "artificial intelligence",
        "semiconductor", "fda approval",
    }
    _CULTURAL_SPORTS = {
        "super bowl", "world series", "nba finals", "stanley cup",
        "national championship", "academy awards", "oscars", "grammys",
    }

    def score(self, story: Mapping[str, Any]) -> StoryImportance:
        status = str(story.get("status", "developing")).strip().lower()
        if status == "archived":
            return StoryImportance(
                score=0,
                level=ImportanceLevel.ARCHIVED,
                reasons=(ImportanceReason("archived", "Archived story", -40),),
            )

        text = self._story_text(story)
        reasons: list[ImportanceReason] = []

        self._add_phrase_reason(reasons, text, self._FEDERAL_ACTION, "federal_action", "Federal/national government action", 32)
        self._add_phrase_reason(reasons, text, self._ELECTION, "election", "Major election/political consequence", 25)
        self._add_phrase_reason(reasons, text, self._MACROECONOMY, "macroeconomy", "Broad economic consequence", 28)
        self._add_phrase_reason(reasons, text, self._WAR_DIPLOMACY, "war_diplomacy", "War, diplomacy or national-security consequence", 30)
        self._add_phrase_reason(reasons, text, self._MAJOR_DISASTER, "major_disaster", "Major disaster or emergency", 30)
        self._add_phrase_reason(reasons, text, self._MAJOR_BUSINESS_TECH, "business_tech", "Major business/technology development", 18)
        self._add_phrase_reason(reasons, text, self._COURT_ACTION, "court_action", "Significant court/criminal-justice action", 15)
        self._add_phrase_reason(reasons, text, self._PUBLIC_SAFETY, "public_safety", "Public-safety event", 10)
        self._add_phrase_reason(reasons, text, self._CULTURAL_SPORTS, "cultural_sports", "Major sports/cultural event", 12)

        fatal_count = self._fatality_count(text)
        if fatal_count >= 50:
            reasons.append(ImportanceReason("mass_casualty", "Mass-casualty event", 45))
        elif fatal_count >= 10:
            reasons.append(ImportanceReason("multi_fatality", "Large multi-fatality event", 30))
        elif fatal_count >= 2:
            reasons.append(ImportanceReason("multiple_deaths", "Multiple deaths reported", 18))
        elif self._death_signal(text):
            # A single death is important context, but not an automatic national hero.
            reasons.append(ImportanceReason("single_death", "Death reported", 6))

        timeline = story.get("timeline", [])
        if isinstance(timeline, list) and len(timeline) > 1:
            reasons.append(ImportanceReason("follow_up", "Story has follow-up coverage", 5))

        agencies = self._normalized_values(story.get("agencies", []))
        if len(agencies) >= 2:
            reasons.append(ImportanceReason("multi_agency", "Multiple agencies involved", 5))

        relevance = story.get("audience_relevance") or {}
        scope = str(relevance.get("scope") or "")
        if scope == "us_national":
            reasons.append(ImportanceReason("national_scope", "Nationwide U.S. scope", 15))
        elif scope == "us_multistate":
            reasons.append(ImportanceReason("multistate_scope", "Multi-state U.S. scope", 10))
        elif scope == "global_major":
            reasons.append(ImportanceReason("global_scope", "Major international consequence", 10))

        raw_score = sum(reason.points for reason in reasons)
        score = max(0, min(100, raw_score))
        return StoryImportance(score=score, level=self._level_for_score(score), reasons=tuple(reasons))

    @staticmethod
    def _normalized_values(values: Any) -> set[str]:
        if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
            return set()
        return {str(value).strip().lower() for value in values if str(value).strip()}

    def _story_text(self, story: Mapping[str, Any]) -> str:
        values: list[str] = []
        for field in ("titles", "facts", "locations", "agencies", "event_types", "entities"):
            raw = story.get(field, [])
            if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
                values.extend(str(value) for value in raw)
        return " ".join(values).lower()

    @staticmethod
    def _fatality_count(text: str) -> int:
        counts = []
        for pattern in (
            r"\b(\d{1,5})\s+(?:people\s+)?(?:killed|dead|deaths|fatalities)\b",
            r"\bdeath toll(?:\s+(?:at|of|reaches|rose to))?\s+(\d{1,5})\b",
        ):
            counts.extend(int(m.group(1)) for m in re.finditer(pattern, text, re.I))
        return max(counts, default=0)

    @staticmethod
    def _death_signal(text: str) -> bool:
        return bool(re.search(r"\b(?:killed|dead|dies|died|death|fatality)\b", text, re.I))

    @staticmethod
    def _add_phrase_reason(reasons, text, phrases, code, label, points):
        if any(phrase in text for phrase in phrases):
            reasons.append(ImportanceReason(code, label, points))

    @staticmethod
    def _level_for_score(score: int) -> ImportanceLevel:
        if score >= 80:
            return ImportanceLevel.BREAKING
        if score >= 55:
            return ImportanceLevel.HIGH
        if score >= 25:
            return ImportanceLevel.NORMAL
        return ImportanceLevel.LOW
