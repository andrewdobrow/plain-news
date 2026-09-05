# TCT backend parity audit for Plain

Plain keeps its existing national product and presentation, but TCT is the reference implementation for reusable newsroom backend protections.

## Integrated and live

The production generator now invokes the reusable TCT-class systems rather than merely carrying dormant modules:

- persistent story, event, incident and source identity
- story timelines, lifecycle, evolution and canonical selection
- shared RSS prefetch and publisher/source recovery
- Google News publisher resolution and full-article extraction fallbacks
- source-quality labels and source-focus repair
- Guardian exact alternate-source recovery when configured
- national category-fit classification before writing
- Sonnet 5 assignment editor separated from the exact-source Sonnet 4.5 writer
- source/writer drift checks and article-quality contracts
- persistent generation/source/materiality caching
- pre-generation semantic duplicate and material-update adjudication
- protected material-update authority carried through assignment, writing and placement
- no-silent-loss material-update commit receipts, including updates selected as snippet cards
- persistent editorial identity and same-story placement deduplication
- source-image authority, OG/social recovery, low-quality/logo rejection, archive restoration and rotating editorial fallbacks
- deterministic ranking recommendations and observability
- terminal semantic publication gate before a new permalink can be minted
- same-canonical material-update composition and URL continuity
- publication integrity reporting
- category-level failure containment using already-published canonicals
- persistent registry repair/compaction
- model request/token/list-cost reporting
- CI package validation, parity checks, compilation, regression tests, persistent cache save, diagnostics upload and repository-size guard

## Nationalized instead of copied literally

TCT's local rules are not appropriate for Plain. They are replaced by national equivalents:

- local/county relevance → national, federal, multi-state and major-world consequence
- Treasure Coast source preference → national wires, major national publishers, specialist national outlets and primary government/scientific sources
- local public-safety importance → national consequence, true mass-casualty scale, major disasters, national security and multi-state reach
- county routing → Plain's existing national category taxonomy

The compatibility fields named `county` that remain in generic identity APIs are retained only so the mature identity engine can be reused; Plain does not give a county special editorial weight.

## Intentionally excluded TCT product features

These are not reusable newsroom-backend improvements and are therefore not part of the Plain port:

- membership/paywall and protected-content systems
- Treasure Coast county pages and local relevance policy
- local weather-alert products
- TCT advertising/newsletter/membership UI
- local manual/custom-article queues and one-off Treasure Coast repair data

## Historical content safety

The migration does not rewrite Plain's historical `articles/` directory. Existing legacy duplicate groups can be reported by publication-integrity diagnostics, but are not destructively changed during installation. The TCT-style duplicate/update barriers apply to forward publication so a changed publisher or rewritten headline alone cannot create a second canonical.

## Maintained parity contract

`scripts/backend_parity_check.py` runs in CI and verifies that the reusable TCT engine modules, Plain national adapters and critical live-pipeline hooks remain present. A future change that accidentally removes one of those protections fails preflight before generation.
