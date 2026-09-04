"""Translate editorial decisions into production workflow instructions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .editorial_decision import EditorialAction
from .editorial_engine import EditorialEngineResult


class ProductionRoute(str, Enum):
    """Actions the production article workflow can execute."""

    SKIP = "skip"
    GENERATE_NEW = "generate_new"
    UPDATE_EXISTING = "update_existing"
    REPLACE_CANONICAL = "replace_canonical"


@dataclass(frozen=True)
class ProductionInstruction:
    """Concrete instruction for the production publishing workflow."""

    route: ProductionRoute
    event_key: str
    incoming_article_id: str
    target_article_id: str
    new_facts: tuple[str, ...]
    should_generate: bool
    should_update: bool
    should_skip: bool


def route_editorial_result(
    result: EditorialEngineResult,
) -> ProductionInstruction:
    """Convert an editorial result into a production instruction."""

    if result.action is EditorialAction.PUBLISH_NEW:
        return ProductionInstruction(
            route=ProductionRoute.GENERATE_NEW,
            event_key=result.event_key,
            incoming_article_id=result.article_id,
            target_article_id=result.canonical_article_id,
            new_facts=result.new_facts,
            should_generate=True,
            should_update=False,
            should_skip=False,
        )

    if result.action is EditorialAction.UPDATE_EXISTING:
        return ProductionInstruction(
            route=ProductionRoute.UPDATE_EXISTING,
            event_key=result.event_key,
            incoming_article_id=result.article_id,
            target_article_id=result.canonical_article_id,
            new_facts=result.new_facts,
            should_generate=False,
            should_update=True,
            should_skip=False,
        )

    if result.action is EditorialAction.REPLACE_CANONICAL:
        return ProductionInstruction(
            route=ProductionRoute.REPLACE_CANONICAL,
            event_key=result.event_key,
            incoming_article_id=result.article_id,
            target_article_id=result.canonical_article_id,
            new_facts=result.new_facts,
            should_generate=True,
            should_update=False,
            should_skip=False,
        )

    if result.action is EditorialAction.IGNORE:
        return ProductionInstruction(
            route=ProductionRoute.SKIP,
            event_key=result.event_key,
            incoming_article_id=result.article_id,
            target_article_id=result.canonical_article_id,
            new_facts=result.new_facts,
            should_generate=False,
            should_update=False,
            should_skip=True,
        )

    raise ValueError(
        f"Unsupported editorial action: {result.action!r}"
    )