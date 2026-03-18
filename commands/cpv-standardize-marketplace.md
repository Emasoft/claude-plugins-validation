---
description: Audit an existing marketplace and standardize it to match CPV standards
---

Audit and optionally fix an existing marketplace repository.

**Audit only:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path>
```

**Audit and fix:**
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/standardize_marketplace.py" <marketplace-path> --fix
```

Checks: marketplace.json structure, all plugin sources point to external GitHub repos (flags local paths as errors), CI/CD workflows exist, README has plugin catalog.

With `--fix`, generates missing workflows, hooks, and scripts WITHOUT modifying marketplace.json plugin entries or existing code.
