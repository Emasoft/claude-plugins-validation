# Version Strategy

## Table of Contents

- [The Rule](#the-rule)
- [Why It Matters](#why-it-matters)
- [Local vs Remote Sources](#local-vs-remote-sources)
- [How Each Agent Applies It](#how-each-agent-applies-it)
- [Worked Examples](#worked-examples)
- [Migration Behaviour](#migration-behaviour)
- [Edge Cases](#edge-cases)
- [Cross References](#cross-references)

## Checklist

- [ ] `source: github` / `url` / `git` / `git-subdir` / `npm` → version field OMITTED
- [ ] `source: relative-path` / `./` / `directory` → version field REQUIRED, must equal `plugin.json.version`
- [ ] When fixing a version-drift finding on a remote source, agent DROPS the field (does not bump)
- [ ] When fixing a version-drift finding on a local source, agent SYNCS the field to the upstream value

## The Rule

**For remote sources** (`github`, `url`, `git`, `git-subdir`, `npm`): **OMIT** the `version` field.

**For local sources** (`relative-path`, `./` Layout C, `directory`): **REQUIRE** the `version` field, equal to `plugin.json.version`.

A "remote" source means the install resolver fetches an upstream artefact (git ref or npm tag) at install time. A "local" source means the install resolver reads files relative to the marketplace directory — there is no upstream to consult, so the marketplace IS the source of truth.

## Why It Matters

The install resolver consults the upstream's version, not the marketplace's. For `source: github`:

```
claude plugin install foo@mkpl
  ↓
read mkpl/.../marketplace.json — find entry
  ↓
clone entry.source (GitHub repo)
  ↓
checkout latest tag (NOT entry.version)
  ↓
read plugin.json.version — display to user
```

The marketplace's `.version` is decorative — used at most for display in `claude plugin list`. It goes stale within hours of every upstream release: ship `v1.2.0` of the plugin, the marketplace still says `v1.1.9` until someone manually bumps it, and the user sees confusingly inconsistent state.

PIT-002 in [common-pitfalls](common-pitfalls.md) is the canonical case. In the 2026-05-11 incident, every multi-plugin marketplace had at least one entry with version drift, despite agents claiming to "carefully update" the field.

**Dropping the field eliminates the entire class of bug.**

For local sources, there IS no upstream — the install resolver reads `<marketplace-dir>/<path>/plugin.json` directly, so the marketplace.json `version` and the local `plugin.json.version` MUST agree. Layout B and Layout C bump scripts (publish.py) update both atomically; out-of-band edits drift.

## Local vs Remote Sources

| Source type | Class | Version field |
|---|---|---|
| `github` | remote | OMIT |
| `url` | remote | OMIT |
| `git` | remote | OMIT |
| `git-subdir` | remote | OMIT |
| `npm` | remote | OMIT |
| `relative-path` | local | REQUIRED, must equal `plugin.json.version` |
| `./` (Layout C self-entry) | local | REQUIRED, must equal `plugin.json.version` |
| `directory` | local | REQUIRED, must equal `plugin.json.version` |

The split is: does the install resolver fetch from outside the marketplace's filesystem? If yes → remote → OMIT. If no → local → REQUIRED.

## How Each Agent Applies It

### plugin-creator

When scaffolding a brand-new marketplace.json entry:
- Default to `source: github` for new entries → emit NO `version` field.
- If the user requests `relative-path` (Layout B), add `version` field equal to the upstream plugin.json's version (fetched at scaffold time).
- Layout C self-entry → emit `version` equal to the plugin's own plugin.json.

### plugin-fixer

When fixing a "version drift" finding from the validator:
- If the source is remote → DROP the version field. (Auto-bumping is wrong because the field is decorative; dropping eliminates the bug class permanently.)
- If the source is local → SYNC the version to the upstream/local value. Do NOT bump independently.

### migrate-marketplace

When migrating Layout A → B (hub-and-spoke → nested monorepo):
- Source type changes from `github`/`url` to `relative-path`.
- ADD `version` field equal to the plugin.json.version at the migration point.

When migrating Layout B → A:
- Source type changes from `relative-path` to `github`/`url`.
- DROP the `version` field.

When migrating to/from Layout C:
- Layout C uses `source: "./"` (a literal — see [source-shape](source-shape.md)).
- Treated as a local source → `version` REQUIRED and synced to the plugin's own plugin.json.
- The Layout-C-aware publish.py bumps both atomically (plugin.json.version + marketplace.json plugins[0].version + marketplace.json metadata.version).

## Worked Examples

### Example 1 — fresh Layout A entry (correct)

```json
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "owner/foo-plugin",
  "description": "Foo plugin for Claude Code"
}
```

No `version` field. Resolver pulls upstream tag at install.

### Example 2 — fresh Layout A entry (WRONG — PIT-002)

```json
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // ← drift bomb
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

The agent set `version: "1.0.0"` at scaffold time. Upstream releases `v1.0.1`, `v1.0.2`, `v1.1.0` — marketplace.json still says `1.0.0`. Users see misleading "latest version" in `claude plugin list`.

### Example 3 — Layout B entry (correct)

```json
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED for local sources
  "source": "relative-path",
  "path": "./plugins/foo-plugin"
}
```

publish.py atomically bumps `plugins/foo-plugin/.claude-plugin/plugin.json` and this entry together. Drift is impossible.

### Example 4 — Layout C self-entry (correct)

```json
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED — must equal plugin.json.version
  "source": "./",                     // literal "./" — see source-shape.md
  "description": "Foo plugin for Claude Code"
}
```

The Layout-C-aware publish.py from `generate_plugin_repo.py --self-marketplace` bumps both plugin.json.version and marketplace.json.plugins[0].version + metadata.version atomically.

## Migration Behaviour

When the validator reports a "version drift" finding (PIT-002):

```
[MAJOR] marketplace entry "foo" has version "1.0.0" but upstream
plugin.json declares "1.4.2". Either drop the version field
(recommended for remote sources) or sync it (required for local
sources).
```

The plugin-fixer recipe is mechanical:

```python
if entry["source"] in {"github", "url", "git", "git-subdir", "npm"}:
    del entry["version"]    # DROP
else:
    entry["version"] = upstream_plugin_json["version"]    # SYNC
```

Never bump independently. Never "round up to the next minor". Never guess.

## Edge Cases

**Empty marketplace metadata.version vs plugin entry version.** The top-level `metadata.version` of marketplace.json (Layout C and Layout B) tracks the *marketplace's* version, not any plugin's. It IS allowed to differ from any plugin entry. Layout C's publish.py syncs both because the marketplace is the plugin (single-self-entry) — that synchronisation is Layout-C-specific, not a general rule.

**npm-source `version` pinning.** Some users want to pin `source: npm` to a specific version. The official model is to use npm's own semver in the `package` field (`"package": "@scope/foo@1.0.0"`), not to add a top-level `version` field. The contract does NOT relax for "pinning" — drop the field, use npm semver.

**Layout C with `directory` source instead of `./`.** Both literals work, but `./` is the canonical Layout C form per TRDD-262c0a8e (the Layout C migration TRDD). `directory` source is reserved for Layout B's nested-but-not-self case. Stick to `./` for self-entry; the field rules are the same (REQUIRED, must equal plugin.json.version).

## Cross References

- [name-canonicalisation](name-canonicalisation.md) — the first rule
- [source-shape](source-shape.md) — per-source-type field allowlists
- [layout-decision-tree](layout-decision-tree.md) — when each source type applies
- [common-pitfalls](common-pitfalls.md) — PIT-002 is the canonical drift case
