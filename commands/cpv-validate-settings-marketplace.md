---
name: cpv-validate-settings-marketplace
description: Validate extraKnownMarketplaces in a settings.json file
allowed-tools: Read, Bash, Glob
argument-hint: "<settings.json-path>"
user-invocable: true
---

# /cpv-validate-settings-marketplace

Validates the `extraKnownMarketplaces` block inside a Claude Code `settings.json` file against the v2.1.80+ spec.

This is DIFFERENT from `/cpv-validate-github-marketplace` and `cpv-validate-plugin` — those validate `marketplace.json` files. This command validates the marketplace-source declarations that live in `settings.json` (user, project, or managed level).

## Schema

```json
{
  "extraKnownMarketplaces": {
    "<marketplace-name>": {
      "source": {
        "source": "github" | "url" | "git-subdir" | "npm" | "settings",
        // per-type required fields
      }
    }
  }
}
```

Supported source types:
- `github`: requires `repo: "owner/name"`
- `url`: requires `url`
- `git-subdir`: requires `url` and `path`
- `npm`: requires `package`
- `settings`: inline marketplace, requires `name` and `plugins` (array)

## Usage

```
/cpv-validate-settings-marketplace path/to/settings.json
```

## Execution

```bash
# Always invoke via the launcher for environment isolation:
CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
  python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
  settings-marketplace "$ARG" --strict
```

## Post-validate fix prompt (mandatory)

After printing the validation summary, print the following 6-row Unicode
table verbatim and wait for the user's number. Do NOT skip — even on
PASS / VALID, the user always gets the explicit "fix N or end" choice.
NEVER ask "what's next?" generically.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Action                                          ┃ What it does                                                          ┃ Severities the fixer will touch ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the marketplace-fixer agent on every finding                 │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ 0 │ End                                             │ Done — exit without running the fixer                                 │ —                                │
└───┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
Type a number to choose:
```

On `0` → reply `Done.` and stop.

On `1`-`5` → dispatch the **marketplace-fixer** agent with the report
path and the chosen `min_severity` (templates as in `/cpv-validate-plugin`).

After the agent returns, reply `Done.` and stop.

## Related

- `/cpv-validate-plugin` — validates plugin.json (different schema)
- `/cpv-validate-github-marketplace` — validates marketplace.json (different schema)
