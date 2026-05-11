# Source Shape

## Table of Contents

- [The Rule](#the-rule)
- [Source GitHub](#source-github)
- [Source URL](#source-url)
- [Source Git](#source-git)
- [Source Git-Subdir](#source-git-subdir)
- [Source NPM](#source-npm)
- [Source Relative-Path](#source-relative-path)
- [Source Layout C Self-Entry](#source-layout-c-self-entry)
- [Field Allowlist Summary](#field-allowlist-summary)
- [Common Wrong Shapes](#common-wrong-shapes)
- [Cross References](#cross-references)

## Checklist

- [ ] Every `source` value is one of: `github`, `url`, `git`, `git-subdir`, `npm`, `relative-path`, `./`
- [ ] Per-source-type fields match the canonical examples below
- [ ] No `version` field on remote sources (`github`, `url`, `git`, `git-subdir`, `npm`)
- [ ] `version` field present and matched on local sources (`relative-path`, `./`)
- [ ] No invented per-source fields (e.g. `branch`, `tag`, `commit` — use `ref` only)

## The Rule

Each source type has a fixed field allowlist. The agent emits exactly the fields in the allowlist for the chosen source type — no more, no less.

The 7 supported sources (per Claude Code v2.1.121, `plugin-marketplaces.md` lines 180–229):

1. `github` — owner/repo on GitHub.com (most common for public plugins).
2. `url` — any HTTPS git endpoint cloneable by `git clone`.
3. `git` — like url but explicit, supports `ref`.
4. `git-subdir` — monorepo subdirectory.
5. `npm` — published npm package.
6. `relative-path` — Layout B nested monorepo.
7. `./` — Layout C self-marketplace-in-plugin (a literal, not a relative path expression).

Each has its own canonical example below.

## Source GitHub

The most common form. Used for any plugin hosted as a regular repo on GitHub.com.

```jsonc
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "owner/foo-plugin",
  "ref": "main"                       // optional — default branch otherwise
}
```

Required: `repo` in `owner/repo` shorthand (NOT a full URL — see PIT-005). Optional: `ref` (branch, tag, or commit SHA).

Forbidden in github entries: `url` (use `git` instead if you need a full URL), `package` (npm-only).

## Source URL

For plugins hosted anywhere git can clone via HTTPS (GitHub, GitLab, self-hosted Gitea, etc.).

```jsonc
{
  "name": "foo-plugin",
  "source": "url",
  "url": "https://github.com/owner/foo-plugin.git"
}
```

Required: `url` (full HTTPS clone URL, must include `.git` for git endpoints).

Forbidden in url entries: `ref` (use `git` source type if you need ref-pinning), `repo` (github-only shorthand).

## Source Git

Like `url` but supports explicit `ref` pinning. Use for non-GitHub git hosts where the user wants to pin a version.

```jsonc
{
  "name": "foo-plugin",
  "source": "git",
  "url": "https://example.com/repo.git",
  "ref": "v1.2.3"                     // branch, tag, or commit
}
```

Required: `url`. Optional: `ref`.

## Source Git-Subdir

For plugins that live in a subdirectory of a larger monorepo. The install resolver clones the whole repo, then enters the subdir.

```jsonc
{
  "name": "foo-plugin",
  "source": "git-subdir",
  "url": "https://github.com/owner/monorepo.git",
  "subdir": "plugins/foo",
  "ref": "main"                       // optional
}
```

Required: `url`, `subdir`. Optional: `ref`.

Note: some Claude Code versions accept `path` instead of `subdir`. CPV's validator accepts both. New code SHOULD prefer `subdir` because it is the documented name in `plugin-marketplaces.md`.

## Source NPM

For plugins published as npm packages. The install resolver does `npm pack` + extract.

```jsonc
{
  "name": "foo-plugin",
  "source": "npm",
  "package": "@scope/foo-plugin"
}
```

Required: `package`.

Pinning to a specific npm version uses npm semver suffix in the `package` field, NOT a separate field:

```jsonc
{"source": "npm", "package": "@scope/foo-plugin@1.2.3"}
```

## Source Relative-Path

For Layout B (nested monorepo). The marketplace lives at the root of a repo that contains the plugins as subdirectories; the resolver reads them from the local filesystem at install time.

```jsonc
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED for local sources
  "source": "relative-path",
  "path": "./plugins/foo-plugin"
}
```

Required: `path` (relative to the marketplace directory). The `version` field is REQUIRED per [version-strategy](version-strategy.md) — local sources have no upstream to consult.

## Source Layout C Self-Entry

For Layout C (self-marketplace-in-plugin). A single repo holds both `plugin.json` and `marketplace.json` at root. The marketplace's `plugins[]` array has exactly one entry, pointing at the plugin's own directory.

```jsonc
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED, must equal plugin.json.version
  "source": "./",                     // literal "./", not omitted, not "."
  "description": "Foo plugin for Claude Code"
}
```

The `"./"` is a literal (a two-character string). Not `.`, not the empty string, not omitted — see PIT-004 in [common-pitfalls](common-pitfalls.md).

Required: literal `"./"`. The `version` field is REQUIRED and must equal `plugin.json.version` (publish.py syncs both atomically).

## Field Allowlist Summary

| Source | Required fields | Optional fields | Version field |
|---|---|---|---|
| `github` | `repo` | `ref` | OMIT |
| `url` | `url` | — | OMIT |
| `git` | `url` | `ref` | OMIT |
| `git-subdir` | `url`, `subdir` | `ref` | OMIT |
| `npm` | `package` | — | OMIT |
| `relative-path` | `path` | — | REQUIRED |
| `./` (Layout C) | (just the literal) | — | REQUIRED |

(Plus the top-level fields from [known-fields](known-fields.md): `name`, `description`, `author`, etc.)

## Common Wrong Shapes

Patterns observed in 2026-05-11 incident output:

### Full URL instead of owner/repo shorthand (PIT-005)

Wrong:
```json
{"source": "github", "repo": "https://github.com/owner/foo-plugin"}
```

Right:
```json
{"source": "github", "repo": "owner/foo-plugin"}
```

The `github` source expects the bare `owner/repo` form. Full URLs use `source: url` instead.

### Layout C `source` omitted

Wrong:
```json
{"name": "foo-plugin", "version": "1.0.0", "description": "..."}
```

Right:
```json
{"name": "foo-plugin", "version": "1.0.0", "source": "./", "description": "..."}
```

The `source: "./"` literal is required — the install resolver does NOT infer Layout C from "marketplace.json sits next to plugin.json". See PIT-004.

### Invented branch / tag / commit fields

Wrong:
```json
{"source": "github", "repo": "owner/foo", "branch": "develop"}
{"source": "github", "repo": "owner/foo", "tag": "v1.0.0"}
{"source": "github", "repo": "owner/foo", "commit": "abc123"}
```

Right:
```json
{"source": "github", "repo": "owner/foo", "ref": "develop"}
{"source": "github", "repo": "owner/foo", "ref": "v1.0.0"}
{"source": "github", "repo": "owner/foo", "ref": "abc123"}
```

The single `ref` field accepts branches, tags, or commits. No type-specific aliases.

### Layout B `relative-path` with no `version`

Wrong:
```json
{"source": "relative-path", "path": "./plugins/foo"}
```

Right:
```json
{"name": "foo-plugin", "version": "1.0.0", "source": "relative-path", "path": "./plugins/foo"}
```

Local sources require `version` (the marketplace IS the source of truth for them).

## Cross References

- [known-fields](known-fields.md) — top-level field allowlist (this file covers per-source fields)
- [version-strategy](version-strategy.md) — when `version` is required vs forbidden
- [layout-decision-tree](layout-decision-tree.md) — when each source type applies
- [common-pitfalls](common-pitfalls.md) — PIT-004 (Layout C `./`) and PIT-005 (full URL in github source)
