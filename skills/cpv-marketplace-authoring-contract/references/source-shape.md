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

- [ ] `source` is a NESTED object `{"source": "<type>", ...}` for remote types, or a bare relative-path STRING (`"./path"`, `"./"`) for local sources
- [ ] The inner `source` type is one of: `github`, `url`, `git`, `git-subdir`, `npm`, `directory` (local dict form) — NOT the literal `relative-path` as a dict type
- [ ] Per-source-type fields (inside the `source` object) match the canonical examples below
- [ ] No top-level `version` field on remote sources (`github`, `url`, `git`, `git-subdir`, `npm`)
- [ ] Top-level `version` field present and matched on local sources (string shorthand `"./..."` / `"./"` / `directory` dict)
- [ ] No invented per-source fields (e.g. `branch`, `tag`, `commit` — use `ref` only)

## The Rule

Each source type has a fixed field allowlist. The agent emits exactly the fields in the allowlist for the chosen source type — no more, no less.

**Structure: `source` is a NESTED object, not a flat string with sibling fields.** The per-source fields (`repo`, `url`, `subdir`, `package`, `path`, `ref`) live INSIDE the `source` object, alongside the type-name under the inner `source` key:

```json
{"name": "foo-plugin", "source": {"source": "github", "repo": "owner/foo-plugin"}}
```

NOT the flat form `{"name": "foo-plugin", "source": "github", "repo": "owner/foo-plugin"}` — a top-level sibling `repo`/`url`/`package`/`path` triggers MAJOR `RC-MKPL-UNKNOWN-FIELD` and the validator cannot even detect the source type (it reports `<inline>`). The only exception is the Layout B/C string shorthand `"source": "./plugins/foo"`, where `source` is a plain relative-path string and there are no nested fields. The canonical examples below show the nested object form for every named source type.

The 7 supported sources (per Claude Code v2.1.121, `plugin-marketplaces.md` lines 180–229):

1. `github` — owner/repo on GitHub.com (most common for public plugins).
2. `url` — any HTTPS git endpoint cloneable by `git clone`.
3. `git` — like url but explicit, supports `ref`.
4. `git-subdir` — monorepo subdirectory.
5. `npm` — published npm package.
6. relative-path (Layout B nested monorepo) — expressed as a bare string `"./plugins/foo"` OR the `directory` dict `{"source": "directory", "path": "./plugins/foo"}`. There is no `relative-path` dict type.
7. `./` — Layout C self-marketplace-in-plugin (a literal string, not a relative path expression).

Each has its own canonical example below.

## Source GitHub

The most common form. Used for any plugin hosted as a regular repo on GitHub.com.

```jsonc
{
  "name": "foo-plugin",
  "source": {
    "source": "github",
    "repo": "owner/foo-plugin",
    "ref": "main"                     // optional — default branch otherwise
  }
}
```

Required (inside the `source` object): `repo` in `owner/repo` shorthand (NOT a full URL — see PIT-005). Optional: `ref` (branch, tag, or commit SHA).

Forbidden in the `source` object: `url` (use `git` instead if you need a full URL), `package` (npm-only).

## Source URL

For plugins hosted anywhere git can clone via HTTPS (GitHub, GitLab, self-hosted Gitea, etc.).

```jsonc
{
  "name": "foo-plugin",
  "source": {
    "source": "url",
    "url": "https://github.com/owner/foo-plugin.git"
  }
}
```

Required (inside the `source` object): `url` (full HTTPS clone URL, must include `.git` for git endpoints).

Forbidden in the `source` object: `ref` (use `git` source type if you need ref-pinning), `repo` (github-only shorthand).

## Source Git

Like `url` but supports explicit `ref` pinning. Use for non-GitHub git hosts where the user wants to pin a version.

```jsonc
{
  "name": "foo-plugin",
  "source": {
    "source": "git",
    "url": "https://example.com/repo.git",
    "ref": "v1.2.3"                   // branch, tag, or commit
  }
}
```

Required (inside the `source` object): `url`. Optional: `ref`.

## Source Git-Subdir

For plugins that live in a subdirectory of a larger monorepo. The install resolver clones the whole repo, then enters the subdir.

```jsonc
{
  "name": "foo-plugin",
  "source": {
    "source": "git-subdir",
    "url": "https://github.com/owner/monorepo.git",
    "subdir": "plugins/foo",
    "ref": "main"                     // optional
  }
}
```

Required (inside the `source` object): `url`, `subdir`. Optional: `ref`.

Note: some Claude Code versions accept `path` instead of `subdir`. CPV's validator accepts both. New code SHOULD prefer `subdir` because it is the documented name in `plugin-marketplaces.md`.

## Source NPM

For plugins published as npm packages. The install resolver does `npm pack` + extract.

```jsonc
{
  "name": "foo-plugin",
  "source": {
    "source": "npm",
    "package": "@scope/foo-plugin"
  }
}
```

Required (inside the `source` object): `package`.

Pinning to a specific npm version uses npm semver suffix in the `package` field, NOT a separate field:

```jsonc
{"source": {"source": "npm", "package": "@scope/foo-plugin@1.2.3"}}
```

## Source Relative-Path

For Layout B (nested monorepo). The marketplace lives at the root of a repo that contains the plugins as subdirectories; the resolver reads them from the local filesystem at install time.

Local sources have two valid forms. The **string shorthand** is the simplest — `source` is the bare relative path:

```jsonc
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED for local sources (top-level, sibling of source)
  "source": "./plugins/foo-plugin"    // bare relative-path STRING, not a dict
}
```

The equivalent **nested `directory` dict** form pins `path` inside the `source` object:

```jsonc
{
  "name": "foo-plugin",
  "version": "1.0.0",                 // REQUIRED for local sources
  "source": {
    "source": "directory",            // dict local-source type is "directory", NOT "relative-path"
    "path": "./plugins/foo-plugin"
  }
}
```

There is no `relative-path` DICT type — `{"source": "relative-path", ...}` is rejected MAJOR "invalid source type". Use the bare string `"./plugins/foo-plugin"` OR the nested `directory` dict. `version` is a top-level plugin-entry field (sibling of `name`/`source`), REQUIRED per [version-strategy](version-strategy.md) — local sources have no upstream to consult.

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

The "Required/Optional fields" below live INSIDE the nested `source` object (except the two string-shorthand rows). The `version` column is a TOP-LEVEL plugin-entry field (sibling of `name`/`source`), never inside `source`.

| `source` value | Fields inside `source` (required) | Fields inside `source` (optional) | Top-level `version` |
|---|---|---|---|
| `{"source": "github", …}` | `repo` | `ref` | OMIT |
| `{"source": "url", …}` | `url` | — | OMIT |
| `{"source": "git", …}` | `url` | `ref` | OMIT |
| `{"source": "git-subdir", …}` | `url`, `subdir` | `ref` | OMIT |
| `{"source": "npm", …}` | `package` | — | OMIT |
| `{"source": "directory", …}` | `path` | — | REQUIRED |
| `"./plugins/foo"` (string shorthand) | (the path IS the string) | — | REQUIRED |
| `"./"` (Layout C, string shorthand) | (just the literal) | — | REQUIRED |

(Plus the top-level fields from [known-fields](known-fields.md): `name`, `description`, `author`, etc.)

## Common Wrong Shapes

Patterns observed in 2026-05-11 incident output:

### Full URL instead of owner/repo shorthand (PIT-005)

Wrong:
```json
{"source": {"source": "github", "repo": "https://github.com/owner/foo-plugin"}}
```

Right:
```json
{"source": {"source": "github", "repo": "owner/foo-plugin"}}
```

The `github` source expects the bare `owner/repo` form. Full URLs use `{"source": "url", "url": "…"}` instead.

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
{"source": {"source": "github", "repo": "owner/foo", "branch": "develop"}}
{"source": {"source": "github", "repo": "owner/foo", "tag": "v1.0.0"}}
{"source": {"source": "github", "repo": "owner/foo", "commit": "abc123"}}
```

Right:
```json
{"source": {"source": "github", "repo": "owner/foo", "ref": "develop"}}
{"source": {"source": "github", "repo": "owner/foo", "ref": "v1.0.0"}}
{"source": {"source": "github", "repo": "owner/foo", "ref": "abc123"}}
```

The single `ref` field accepts branches, tags, or commits. No type-specific aliases.

### Layout B local source with no `version`

Wrong:
```json
{"name": "foo-plugin", "source": "./plugins/foo"}
```

Right:
```json
{"name": "foo-plugin", "version": "1.0.0", "source": "./plugins/foo"}
```

Local sources require `version` (the marketplace IS the source of truth for them).

## Cross References

- [known-fields](known-fields.md) — top-level field allowlist (this file covers per-source fields)
- [version-strategy](version-strategy.md) — when `version` is required vs forbidden
- [layout-decision-tree](layout-decision-tree.md) — when each source type applies
- [common-pitfalls](common-pitfalls.md) — PIT-004 (Layout C `./`) and PIT-005 (full URL in github source)
