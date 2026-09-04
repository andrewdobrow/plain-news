"""Editorial decision engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .story_evolution import UpdateClassification


class EditorialAction(Enum):
    PUBLISH_NEW = auto()
    REPLACE_CANONICAL = auto()
    UPDATE_EXISTING = auto()
    IGNORE = auto()
    HOLD_FOR_REVIEW = auto()


@dataclass(frozen=True, slots=True)
class EditorialDecisionInput:
    has_existing_story: bool
    existing_is_custom: bool
    incoming_is_custom: bool
    update_classification: UpdateClassification
    event_match_confirmed: bool = True


@dataclass(frozen=True, slots=True)
class EditorialDecision:
    action: EditorialAction
    reason: str
    canonical_should_change: bool = False


def decide_editorial_action(
    decision: EditorialDecisionInput,
) -> EditorialDecision:
    """
    Determine what the editorial system should do with an incoming story.
    """

    if not decision.event_match_confirmed:
        return EditorialDecision(
            action=EditorialAction.HOLD_FOR_REVIEW,
            reason="Unable to confidently determine event identity.",
        )

    if not decision.has_existing_story:
        return EditorialDecision(
            action=EditorialAction.PUBLISH_NEW,
            reason="No existing story covers this event.",
        )

    if (
        decision.incoming_is_custom
        and not decision.existing_is_custom
    ):
        return EditorialDecision(
            action=EditorialAction.REPLACE_CANONICAL,
            reason="Incoming Plain story replaces external canonical story.",
            canonical_should_change=True,
        )

    if decision.update_classification is UpdateClassification.NO_CHANGE:
        return EditorialDecision(
            action=EditorialAction.IGNORE,
            reason="Incoming story adds no new information.",
        )

    return EditorialDecision(
        action=EditorialAction.UPDATE_EXISTING,
        reason="Incoming story adds meaningful new information.",
        canonical_should_change=False,
    )