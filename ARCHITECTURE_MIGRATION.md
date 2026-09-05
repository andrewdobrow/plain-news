# TCT → Plain backend migration

## Goal

Plain keeps its existing U.S.-wide product and presentation, while Treasure Coast Today is the reference implementation for reusable newsroom-backend protections. Local/Treasure-Coast policies are nationalized rather than copied literally.

## Reusable TCT systems now integrated

- persistent story registry, canonical identity, timelines and lifecycle
- event, incident and source identity
- story evolution and material-update classification
- deterministic editorial eligibility and source policy
- semantic publication and material-update gates
- ranking recommendations and observability
- registry repair/compaction and production routing
- model usage/cost instrumentation
- shared RSS prefetch, source recovery and source-focus repair
- national category-fit classification
- assignment-editor / exact-source-writer role separation
- publication-quality and source-drift contracts
- persistent generation/source/materiality caches
- source-image authority, image-quality rejection and fallback rotation
- category-level failure containment
- final publication-integrity checks

The detailed maintained audit lives in `BACKEND_PARITY_AUDIT.md`. `scripts/backend_parity_check.py` enforces the structural/live-wiring contract in CI.

## Nationalized behavior

TCT-specific editorial assumptions are replaced rather than renamed:

- local/county relevance → U.S.-national, federal, multi-state, state/regional, major-global and international scope
- local-government/public-safety importance → federal policy, elections, macroeconomics, national security, major courts, major disasters, business/technology consequence and true mass-casualty scale
- Treasure Coast source preference → national wires, major national outlets, specialist national sources and primary government/scientific sources
- county routing → Plain's existing national categories

Generic identity APIs still contain compatibility fields such as `county`; Plain does not give those fields local editorial weight.

## Live production flow

```text
RSS/discovery feeds
  ↓
shared prefetch + publisher/source recovery + full-text extraction
  ↓
source quality / source-focus repair / Guardian alternate recovery
  ↓
national category-fit classification
  ↓
pre-generation duplicate/material-update semantic gate
  ↓
Sonnet 5 assignment editor (select exact source + angle)
  ↓
Sonnet 4.5 exact-source writer
  ↓
article/source-grounding quality contract
  ↓
persistent editorial identity + same-story placement handling
  ↓
source-image authority + image validation/recovery/fallback
  ↓
ranking + observability
  ↓
terminal semantic permalink barrier
  ├─ duplicate/no change → no new canonical
  ├─ material update → refresh established canonical URL
  └─ genuinely new story → mint new permalink
  ↓
Plain renderer: hero + supporting snippets + archive/data output
```

## Material-update no-silent-loss contract

A likely same-event material development is evaluated before writing. When validated, its target canonical identity travels through assignment, writing and placement. If the update survives into a hero or supporting snippet, a commit receipt requires the established canonical article to be updated—even when that live placement is only a snippet card. A validated update cannot silently disappear because an older same-story placement was processed first or because a writer failed; those cases fail closed or prefer the validated update.

The final permalink barrier independently rechecks publication identity before any new URL is created.

## Presentation intentionally preserved

This migration does not redesign Plain. The existing hero, supporting snippets/cards, national categories, article/archive pages, branding, market ticker and public site shell remain the product layer.

## Historical-content safety

The migration itself does not rewrite historical files under `articles/`. Legacy duplicate groups are reportable but are not destructively repaired as part of installation. Forward publication receives the TCT-style canonical/update barriers.

## Intentionally excluded TCT product features

- membership/paywall/protected-content systems
- Treasure Coast county pages and local relevance policy
- local weather products
- TCT ad/newsletter/membership UI
- local manual/custom-article queues and one-off local repair data
