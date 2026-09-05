# Plain News ThinkingBlock compatibility hotfix

## Root cause
The live Sonnet 5 assignment editor can return a non-text `ThinkingBlock` before the JSON `TextBlock`. The production assignment pipeline assumed `response.content[0].text`, so all seven categories failed after the model returned successfully. Category failure containment then recovered older archive stories, making the site appear stale even though RSS prefetch was current.

## Fix
- Add one shared `extract_model_text()` helper that joins only text-bearing Anthropic response blocks and ignores thinking/non-text blocks.
- Use it in the Sonnet 5 assignment editor, Sonnet 4.5 exact-source writer, category classifier, and all remaining direct Anthropic response parsing in `scripts/generate.py`.
- Add regressions for `ThinkingBlock -> TextBlock` responses and for the no-text error path.
- Add the response parser to the backend parity preflight contract.

## Expected live behavior
Fresh categories should once again be assigned and written. `archive recovery activated` should occur only for genuine source/model/quality failures, not merely because Claude included a thinking block.
