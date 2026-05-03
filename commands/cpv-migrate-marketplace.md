---
name: cpv-migrate-marketplace
description: Normalize an existing marketplace.json — convert source.url → source.repo + detect dead repos
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-migrate-marketplace

Normalize a marketplace.json against the canonical CPV schema. Per the
Phase 0 marketplace survey, real-world marketplaces have drift:

- Some entries use `source.url` (older form), others use `source.repo`
  (canonical: `{type: "github", repo: "owner/name"}`).
- Some entries point at GitHub repos that 404 (deleted / renamed).
- Some entries use bare string URL form.

This command detects + applies all migrations atomically, and probes
each github plugin entry for live-ness via `gh api` (retry-wrapped).

## Usage

```bash
# Apply migrations (writes to marketplace.json atomically + probes live-ness)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" /path/to/marketplace

# Check mode — exit 1 if migrations would change the file (CI gate)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" /path/to/marketplace --check

# Skip live-ness probe (offline / no gh CLI)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/migrate_marketplace.py" /path/to/marketplace --no-probe
```

## What it migrates

| Before | After |
|---|---|
| `{"url": "https://github.com/owner/repo"}` | `{"type": "github", "repo": "owner/repo"}` |
| `{"url": "https://github.com/owner/repo.git"}` | `{"type": "github", "repo": "owner/repo"}` |
| `{"url": "git@github.com:owner/repo.git"}` | `{"type": "github", "repo": "owner/repo"}` |
| `"https://github.com/owner/repo"` (string) | `{"type": "github", "repo": "owner/repo"}` |
| `{"type": "github", "repo": "..."}` (canonical) | unchanged |
| `{"source": "relative-path", "path": "..."}` | unchanged |

## Dead-repo detection

After migrations, each github entry's repo is probed via `gh api`. Dead
entries (404) are surfaced as a warning at the end — but NOT removed
automatically. The user decides whether to delete or restore each.

## Atomic write

Marketplace.json is written via a tmp+rename pattern so a crash mid-write
cannot corrupt the file. The intermediate `.json.tmp` is cleaned up on
success.
