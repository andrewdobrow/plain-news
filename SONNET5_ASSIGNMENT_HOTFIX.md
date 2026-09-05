# Plain News Sonnet 5 assignment-editor hotfix v2

## Production failure fixed

Claude Sonnet 5 enables adaptive thinking by default. Plain's assignment-editor
request retained the TCT-sized 1,800-token output ceiling but did not explicitly
disable thinking. On some categories Sonnet 5 consumed the available output budget
in thinking and returned no final text/JSON, causing archive recovery.

This overlay:

- explicitly sends `thinking={"type":"disabled"}` on the Sonnet 5 assignment-editor call;
- preserves the 1,800-token ceiling because the assignment is only compact JSON selection;
- improves no-text diagnostics with stop reason, content-block types, and output-token count;
- adds a regression asserting production cannot accidentally re-enable default Sonnet 5 thinking;
- extends the backend parity preflight to guard this request setting.

It does not modify articles, public HTML, feeds, branding, or archive content.
