# TCT → Plain architecture migration

## Preserved architecture

The following TCT editorial concepts are retained because they are audience-agnostic infrastructure:

- persistent story registry and timelines
- event/incident/source identity
- canonical story selection
- story evolution and material-update classification
- eligibility and source trust policy
- semantic publication gate and semantic material-update support
- story lifecycle
- deterministic ranking recommendations
- observability / audit output
- activation and production-routing infrastructure
- assignment-editor/model bake-off infrastructure for future controlled experiments

## Nationalized behavior

The TCT-specific editorial assumptions were replaced rather than renamed:

- `local_relevance` → `national_relevance`
- county/local proximity → national U.S., multi-state, state/regional, major-global, international scope
- local-government/public-safety importance weights → federal policy, elections, macroeconomics, national security, major disasters, business/technology consequence and true mass-casualty signals
- Treasure Coast publisher policy → national wires, national outlets, specialist national sources and federal primary sources
- local place/person stopword hacks and incident-specific Treasure Coast concepts removed
- Plain category prompts rebuilt around a nationwide U.S. audience

## Presentation intentionally preserved

This migration does not redesign the public Plain site. Existing categories, HTML/CSS, archive rendering, app `data.json`, image system and market ticker remain the presentation layer. The new engine sits between content enrichment and final ranking/publication.

## Publication behavior

A recognized unchanged story is still allowed to appear on a live category page. Recognition does **not** mean the story disappears from the current news product. Instead, persistent identity controls whether it is a new story/update and which permanent archive URL it belongs to.

Within a category, two generated slots that resolve to the same persistent story are collapsed in `enforce` mode. Cross-category placement remains allowed because one national story can legitimately belong in, for example, both U.S. and Politics; homepage presentation deduplication remains a separate concern.
