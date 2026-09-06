# Plain source-depth / section recovery hotfix

This patch corrects the ordering of Plain's TCT-derived live generation pipeline.

## Production bug exposed by the September 5 run

Plain still allowed `brief`/`thin` sources (including sources under the 80-word evidence floor) to reach pre-generation materiality and the assignment editor. That could create a protected material-update receipt for evidence the publication-quality contract would later refuse to publish, producing a fatal `source_under_80_words` contradiction.

The section-depth patch also tried to repair every missing supporting slot with more live writer calls. TCT's production pipeline instead rejects weak sources before generation and fills remaining section depth from recent already-published canonicals after live quality/deduplication.

## Changes

- Adds a pre-writer source-depth gate: only `full`/`summary` sources with at least 80 words of recovered evidence can reach materiality, assignment, or writing.
- Adds defense-in-depth so pre-generation materiality cannot mint update authority from an under-evidenced source even if call ordering changes later.
- Adds `data/source-depth-report.json` and per-category logs showing exactly how many sources were publication-ready and why weak ones were removed.
- Preserves all surviving current generated stories, then fills only missing supporting-card slots from recent (7-day), nonduplicate Plain canonicals.
- Archive depth filler never replaces a live hero and is capped below fresh-card urgency.
- Prints a histogram of exact writer quality-failure reasons per category.
- Bumps the live category cache contract so sparse packages from the prior behavior are not reused.
- Extends backend parity CI to require both the source-depth gate and archive-depth backfill hook.

No historical article, public HTML, CSS, or image file is part of this hotfix.
