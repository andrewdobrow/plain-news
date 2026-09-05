# Plain News

Plain is a U.S.-wide general-news publication. Its reader-facing product remains Plain—one main hero, smaller supporting snippets/cards, national categories and permanent archive/article pages—but the production newsroom backend now adopts the reusable protections developed for Treasure Coast Today.

## Production pipeline

```text
feeds
 → shared source recovery/full-text extraction
 → source quality + national category fit
 → pre-write duplicate/material-update gate
 → Sonnet 5 assignment editor
 → exact-source Sonnet 4.5 writer
 → article/source-grounding quality gate
 → persistent story/event/source identity
 → image authority and recovery
 → ranking/observability
 → terminal permalink duplicate/update gate
 → existing Plain renderer
```

This separates **which story to cover** from **writing the story**, binds generated copy to one recovered source, and moves duplicate/update authority to deterministic/persistent systems around the model rather than trusting rewritten headlines.

## Important backend components

- `plain_engine/` — reusable TCT-derived editorial engine plus Plain national adapters.
- `plain_engine/source_recovery.py` — RSS/publisher recovery, full-text extraction and source-focus repair.
- `plain_engine/assignment_pipeline.py` — Sonnet 5 assignment editor → exact-source Sonnet 4.5 writer.
- `plain_engine/article_quality.py` — article/source-grounding contracts.
- `plain_engine/image_authority.py` — source image recovery, quality rejection, archive restoration and rotating fallbacks.
- `plain_engine/generation_cache.py` — private persistent generation/source/materiality cache.
- `plain_engine/national_relevance.py` — U.S.-wide relevance model replacing TCT local relevance.
- `scripts/editorial_runtime.py` — persistent identity/placement bridge.
- `scripts/backend_parity_check.py` — CI contract ensuring major TCT-derived protections remain live.
- `BACKEND_PARITY_AUDIT.md` — detailed included/excluded parity audit.

## Canonical story/update behavior

Plain uses persistent story identity rather than treating every rewritten headline or new publisher URL as a new story. Before writing, likely same-event candidates are checked for no-change reprints versus material developments. A validated update carries its canonical target through assignment and writing. If selected as either a hero or snippet, it creates a required canonical-update receipt.

Immediately before permalink creation, a terminal semantic gate checks again:

- same event, no material change → do not mint a duplicate URL
- same event, meaningful new facts → update the existing canonical article
- genuinely different story → create a new canonical
- unresolved suspicious match → fail closed rather than publish a questionable duplicate

## Images and source details

Plain now uses a source-image authority chain instead of relying mainly on a single RSS thumbnail: publisher/RSS evidence, article OG/Twitter images, archive restoration, related-source matching and rotating editorial fallback. Logos, placeholders and obviously unsuitable assets are rejected.

Source text similarly uses direct publisher recovery and full-article extraction fallbacks, with Guardian alternate-source enrichment when `GUARDIAN_API_KEY` is configured.

## Reliability and observability

Production runs include package validation, backend parity validation, compile checks and regression tests before generation. Generation caches are restored/saved privately through GitHub Actions. Diagnostics include source quality, image quality, assignment-editor output, article quality, editorial identity/observability, material-update integrity, ranking recommendations and model token/cost reporting.

If a category cannot safely generate current copy, it can recover recent already-published canonicals rather than invent filler or fail the entire edition. A protected validated material update is never silently replaced by that fallback.

## What stays Plain

TCT-specific product features are intentionally excluded: Treasure Coast county/local relevance, membership/paywall/protected-content systems, local weather products, local advertising/newsletter UI, and one-off local repair queues. Those are not reusable newsroom-backend improvements.

## Local validation

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_package.py
python scripts/backend_parity_check.py
python -m compileall -q plain_engine scripts
python -m pytest tests -q
```

Generation requires `ANTHROPIC_API_KEY`. Guardian enrichment uses `GUARDIAN_API_KEY` when configured.
