# Plain card-summary product hotfix v8

Plain's reader-facing product contract is intentionally different from TCT's article surfaces:

- Heroes are full-length Plain articles.
- Supporting cards are concise two-paragraph summaries of the most important confirmed facts.
- Archive/backfill canonicals may supply story identity and facts, but their full article bodies must never be exposed as card bodies.

## Changes

- Card writer target restored to roughly 90-120 words in exactly two concise paragraphs.
- Card quality uses a summary-specific 60-word floor and a 140-word ceiling instead of hero/article length assumptions.
- Summary cards are no longer promoted into failed hero slots by the quality gate.
- Archive category recovery makes card summaries from canonical leads rather than copying full archived article bodies.
- Archive depth backfill compacts every recovered candidate before it becomes a card, including a recovered category hero used as filler.
- A final pre-render card product guard compacts any accidental full-length card while leaving heroes untouched.
- Category generation cache version bumped so previously cached long-card packages are not reused.
- Backend parity CI now checks that the card-vs-hero product boundary remains wired into production.
