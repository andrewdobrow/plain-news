# Plain section-depth hotfix

This patch restores Plain's original section shape (one hero plus up to eight supporting cards) without weakening the TCT-derived safety gates.

## Root cause

Two backend changes could leave categories unnaturally thin even when the RSS prefetch had plenty of current candidates:

1. The national category classifier was being used as a hard pre-assignment filter. A 24-story category feed could be reduced to five candidates before the assignment editor ran.
2. The assignment editor was allowed to return "up to" eight cards, and any ordinary card that failed the publication-quality contract was simply dropped instead of being replaced.

## Fix

- `none` remains a hard classifier rejection. Cross-category labels are now advisory and remain available to the exact-source assignment editor, with positive section matches ordered first.
- The assignment editor is instructed to fill eight supporting slots whenever eight safe, current, distinct stories exist.
- A bounded deterministic backfill queue uses only full/summary sources, rejects stale/explicitly unsafe candidates, and avoids same-story duplicates.
- A failed ordinary card does not weaken the quality contract and does not create a hole; the next safe source is tried instead.
- Card writer target increased to 120-170 words / two paragraphs so cards clear the existing 90-word publication floor more reliably.
- Category generation budget increased from 180s to 240s to allow the original eight-card Plain product to coexist with one-source-per-writer architecture.
- Category cache version bumped so previously cached thin sections are not reused.
- Per-category logs now show retained candidates, editor-selected count, writer attempts, backfill attempts, writer failures, accepted card count, and any genuine shortfall.
- Hero-dedup/global-ranking JSON array parsing now tolerates valid JSON followed by accidental model commentary, fixing the `Extra data` failure shape seen in production.

The final quality gate, semantic duplicate/update authority, exact-source binding, material-update fail-closed behavior, and image/source protections remain intact.
