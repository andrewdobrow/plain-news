"""Load deterministic newsroom policy without third-party dependencies."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceProfile:
    domain: str
    source_class: str
    trust: int
    eligible: bool
    canonical_priority: int


class EditorialPolicy:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else Path(__file__).with_name("editorial_policy.yaml")
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not load editorial policy: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Editorial policy must be an object")
        return payload

    @property
    def never_publish_classes(self) -> set[str]:
        return {str(x) for x in self.data.get("never_publish_classes", [])}

    @property
    def blocked_path_patterns(self) -> tuple[str, ...]:
        return tuple(str(x).casefold() for x in self.data.get("blocked_path_patterns", []))

    @property
    def listing_title_patterns(self) -> tuple[str, ...]:
        return tuple(str(x).casefold() for x in self.data.get("listing_title_patterns", []))

    def source_profile(self, domain: str) -> SourceProfile:
        domain = (domain or "").casefold().removeprefix("www.")
        sources = self.data.get("sources", {})
        selected_domain = ""
        selected: dict[str, Any] | None = None
        for candidate, profile in sources.items():
            candidate = str(candidate).casefold().removeprefix("www.")
            if domain == candidate or domain.endswith("." + candidate):
                if len(candidate) > len(selected_domain):
                    selected_domain = candidate
                    selected = profile
        if selected is None:
            selected = dict(self.data.get("default_source", {}))
            selected_domain = domain
        source_class = str(selected.get("class", "unknown"))
        priority = int(self.data.get("canonical_priority", {}).get(source_class, 50))
        return SourceProfile(
            domain=selected_domain,
            source_class=source_class,
            trust=max(0, min(100, int(selected.get("trust", 50)))),
            eligible=bool(selected.get("eligible", True)),
            canonical_priority=priority,
        )

    def source_profile_for(self, domain: str = "", source: str = "") -> SourceProfile:
        """Resolve a profile from the publisher domain, then a source/feed alias."""
        profile = self.source_profile(domain)
        if profile.source_class != "unknown":
            return profile
        source_fold = (source or "").casefold().strip()
        aliases = self.data.get("source_aliases", {})
        best_key = ""
        best_profile = None
        for key, value in aliases.items():
            key_fold = str(key).casefold()
            if key_fold and key_fold in source_fold and len(key_fold) > len(best_key):
                best_key = key_fold
                best_profile = value
        if best_profile is None:
            return profile
        source_class = str(best_profile.get("class", "unknown"))
        priority = int(self.data.get("canonical_priority", {}).get(source_class, 50))
        return SourceProfile(
            domain=domain or best_key,
            source_class=source_class,
            trust=max(0, min(100, int(best_profile.get("trust", 50)))),
            eligible=bool(best_profile.get("eligible", True)),
            canonical_priority=priority,
        )
