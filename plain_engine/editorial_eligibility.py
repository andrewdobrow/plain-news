"""Deterministic editorial eligibility gate."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlparse

from .editorial_policy import EditorialPolicy, SourceProfile


class EligibilityStatus(str, Enum):
    PUBLISHABLE = "publishable"
    PRESS_RELEASE = "press_release"
    LOW_VALUE = "low_value"
    NON_NEWS = "non_news"
    LISTING = "listing"
    ARCHIVE_PAGE = "archive_page"
    SEARCH_PAGE = "search_page"
    CATEGORY_PAGE = "category_page"


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    eligible: bool
    status: EligibilityStatus
    reasons: tuple[str, ...]
    source_profile: SourceProfile


class EditorialEligibilityEngine:
    def __init__(self, policy: EditorialPolicy | None = None) -> None:
        self.policy = policy or EditorialPolicy()

    @staticmethod
    def _text(entry: Mapping[str, Any], key: str) -> str:
        value = entry.get(key, "")
        return str(value or "").strip()

    def evaluate(self, entry: Mapping[str, Any], *, source: str = "") -> EligibilityDecision:
        title = self._text(entry, "title")
        url = self._text(entry, "link")
        parsed = urlparse(url)
        domain = parsed.netloc.casefold().split(":", 1)[0].removeprefix("www.")
        path = parsed.path.casefold()
        profile = self.policy.source_profile_for(domain, source)
        title_fold = title.casefold()
        reasons: list[str] = []

        if profile.source_class == "listing" or not profile.eligible:
            reasons.append(f"Source class '{profile.source_class}' is excluded by policy")
            return EligibilityDecision(False, EligibilityStatus.LISTING, tuple(reasons), profile)

        for pattern in self.policy.blocked_path_patterns:
            if pattern and pattern in path:
                if "search" in pattern:
                    status = EligibilityStatus.SEARCH_PAGE
                elif "archive" in pattern:
                    status = EligibilityStatus.ARCHIVE_PAGE
                elif "tag" in pattern or "category" in pattern:
                    status = EligibilityStatus.CATEGORY_PAGE
                else:
                    status = EligibilityStatus.LISTING
                reasons.append(f"URL path matches blocked pattern '{pattern}'")
                return EligibilityDecision(False, status, tuple(reasons), profile)

        for pattern in self.policy.listing_title_patterns:
            if pattern and pattern in title_fold:
                reasons.append(f"Headline matches listing pattern '{pattern}'")
                return EligibilityDecision(False, EligibilityStatus.LISTING, tuple(reasons), profile)

        # Reject pages that are essentially an address plus listing vocabulary.
        address_like = bool(re.search(r"\b\d{1,6}\s+[a-z0-9 .'-]+\b(?:st|street|ave|avenue|rd|road|dr|drive|cir|circle|blvd|boulevard|ln|lane|ct|court|way)\b", title_fold))
        listing_vocab = any(token in title_fold for token in ("unit ", "bedroom", "bathroom", "sq ft", "rent", "listing"))
        if address_like and listing_vocab:
            reasons.append("Headline resembles a property listing rather than a news event")
            return EligibilityDecision(False, EligibilityStatus.LISTING, tuple(reasons), profile)

        if profile.source_class == "aggregator":
            reasons.append("Aggregator source accepted at reduced editorial value")
            return EligibilityDecision(True, EligibilityStatus.LOW_VALUE, tuple(reasons), profile)

        if profile.source_class == "government":
            reasons.append("Government/public-agency source")
            return EligibilityDecision(True, EligibilityStatus.PRESS_RELEASE, tuple(reasons), profile)

        reasons.append("Candidate passed deterministic editorial eligibility rules")
        return EligibilityDecision(True, EligibilityStatus.PUBLISHABLE, tuple(reasons), profile)
