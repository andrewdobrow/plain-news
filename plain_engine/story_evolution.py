"""Story evolution analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class UpdateClassification(Enum):
    NO_CHANGE = auto()
    UPDATE = auto()
    MAJOR_UPDATE = auto()
    CORRECTION = auto()


@dataclass(frozen=True, slots=True)
class StorySnapshot:
    event_key: str
    facts: tuple[str, ...]
    status: str = "developing"


@dataclass(frozen=True, slots=True)
class IncomingStoryUpdate:
    event_key: str
    facts: tuple[str, ...]
    status: str = "developing"
    is_major: bool = False
    is_correction: bool = False


@dataclass(frozen=True, slots=True)
class StoryUpdateResult:
    classification: UpdateClassification
    new_facts: tuple[str, ...]
    status_changed: bool
    previous_status: str
    new_status: str


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def evaluate_story_update(
    current: StorySnapshot,
    incoming: IncomingStoryUpdate,
) -> StoryUpdateResult:
    if current.event_key != incoming.event_key:
        raise ValueError("Stories must belong to the same event")

    current_facts = {_normalize(f) for f in current.facts}

    new_facts = tuple(
        fact
        for fact in incoming.facts
        if _normalize(fact) not in current_facts
    )

    status_changed = current.status != incoming.status

    if incoming.is_correction:
        classification = UpdateClassification.CORRECTION
    elif incoming.is_major:
        classification = UpdateClassification.MAJOR_UPDATE
    elif new_facts or status_changed:
        classification = UpdateClassification.UPDATE
    else:
        classification = UpdateClassification.NO_CHANGE

    return StoryUpdateResult(
        classification=classification,
        new_facts=new_facts,
        status_changed=status_changed,
        previous_status=current.status,
        new_status=incoming.status,
    )