# Plain News workflow YAML hotfix v6

Fixes invalid YAML in `.github/workflows/update.yml` caused by a colon-space sequence inside a plain `run:` scalar:

`run: /usr/bin/time -f "Generate elapsed: %E" python -u scripts/generate.py`

The command is now expressed as a YAML block scalar:

```yaml
- name: Generate news
  run: |
    /usr/bin/time -f "Generate elapsed: %E" python -u scripts/generate.py
```

The manual trigger remains explicit as `workflow_dispatch: {}`.
