# Plain material-update recomposition hotfix

This hotfix ports TCT's protected material-update repair barrier into Plain.

## Production failure addressed

A source could pass the pre-generation semantic gate as a validated material update,
be selected by the assignment editor, and then fail the first writer's publication
quality contract. Plain previously treated every such failure as fatal, even when the
failure was safely repairable from the already-authorized canonical article plus the
exact incoming source.

## New behavior

Repairable writer defects are now deferred to the canonical write barrier. Examples:
short body, insufficient paragraph structure, thin lead, missing original-event context,
missing explicit new-development context, headline claims not repeated in the lead, and
first-reference/full-name defects.

The canonical composer then receives the existing Plain article, the exact incoming
source text, and the semantic gate's novel facts. Its output must pass both the semantic
material-update validator and Plain's final standalone publication-quality contract
before any public file is mutated.

Dangerous defects are still fail-closed. In particular, writer/source focus drift and
insufficient source evidence are not eligible for deferred repair.

A failed material-update card is removed from the visible snippet set but retained in a
hidden commit queue so its permanent canonical article is still updated. A failed
material-update hero remains protected until recomposition because `write_archives`
repairs the same object before `news.html` and `data.json` are rendered.

The final article-quality report is rewritten after canonical recomposition so it
reflects the actual publishable state rather than the rejected first writer draft.
