# Plain News

Plain is a U.S.-wide general-news publication. This repository now uses the same **editorial architecture pattern** developed for Treasure Coast Today, but its audience rules, importance model, source policy and prompts are national rather than local.

The existing Plain site design, categories, archive, app data output, feeds and branding remain in place. The major change is behind the presentation layer: story identity and publication decisions now run through a persistent modular editorial engine instead of living almost entirely inside `scripts/generate.py`.

## Production flow

```text
RSS feeds
   ↓
Candidate generation + Plain national category prompts
   ↓
Article/card enrichment
   ↓
plain_engine
   ├─ deterministic eligibility
   ├─ source trust / canonical priority
   ├─ fact extraction
   ├─ event + incident identity
   ├─ persistent story registry
   ├─ story evolution / material updates
   ├─ national audience relevance
   ├─ story importance / lifecycle
   ├─ semantic publication gates
   └─ ranking + observability
   ↓
Homepage/category ranking
   ↓
Permanent article archive + data.json
```

## Important files

- `plain_engine/` — modular editorial engine ported from the TCT architecture and adapted for a national newsroom.
- `plain_engine/editorial_rules.py` — model-facing national editorial mission and category rules.
- `plain_engine/editorial_policy.yaml` — deterministic source classes, trust and canonical priority.
- `plain_engine/national_relevance.py` — replaces TCT local/proximity assumptions with U.S.-wide and major-world relevance.
- `scripts/editorial_runtime.py` — bridge between generated Plain content and persistent story identity.
- `story-registry.json` — created/updated by production runs; persistent story identity across workflow runs.
- `editorial-state.json` — replayable engine state created/updated by production runs.
- `editorial-audit.json` — per-run publication/identity audit created by production runs.
- `tests/` — national editorial regression tests.

## What intentionally did **not** carry over from TCT

Plain is not a local Treasure Coast publication, so the migration intentionally does not bring over county quotas, local-source preference, Treasure Coast geography, membership/paywall logic, local-business rules, or local proximity scoring. Those were replaced with national relevance and consequence rules.

A routine single-city incident should not become nationally important because it happens to contain dramatic keywords. Federal action, nationwide policy, macroeconomics, major courts/elections, national security, multi-state events and genuinely major world developments receive the stronger deterministic signals.

## Engine modes

`PLAIN_ENGINE_MODE` supports:

- `off` — skip the engine.
- `shadow` — run identity/auditing but do not remove rejected or duplicate slots. This is the default for local/manual runs.
- `enforce` — production mode. Reject non-news/listings and remove duplicate same-story cards within a category while retaining intentional cross-category coverage.

The production GitHub Action explicitly uses `enforce` and runs the regression suite before generation.

## Persistent article identity

Newly processed items receive a `story_id`. `archive.json` stores that ID, and future material updates resolve to the same permanent article slug even if the headline or publisher URL changes. Older archive records are migrated opportunistically through the legacy source-URL/headline fallback when a current story matches them.

This is the main structural change that prevents AI rewording or a new source from automatically becoming a brand-new permanent story.

## Local development

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_package.py
python -m pytest tests -v
```

Generation additionally requires `ANTHROPIC_API_KEY`; Guardian enrichment uses `GUARDIAN_API_KEY` when configured.
