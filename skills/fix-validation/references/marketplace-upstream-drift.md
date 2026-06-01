# Marketplace ↔ Upstream Plugin.json Drift Fixes

## Table of Contents

- [1. Name mismatch — RC-MKPL-NAME-MISMATCH](#1-name-mismatch--rc-mkpl-name-mismatch)
- [2. Version drift — RC-MKPL-VERSION-DRIFT](#2-version-drift--rc-mkpl-version-drift)
- [3. Unknown entry field — RC-MKPL-UNKNOWN-FIELD](#3-unknown-entry-field--rc-mkpl-unknown-field)
- [4. Unknown source sub-field — RC-MKPL-UNKNOWN-SOURCE-FIELD](#4-unknown-source-sub-field--rc-mkpl-unknown-source-field)
- [5. Source unreachable — RC-MKPL-UPSTREAM-UNREACHABLE](#5-source-unreachable--rc-mkpl-upstream-unreachable)
- [6. Description / author / keywords drift — RC-MKPL-METADATA-DRIFT](#6-description--author--keywords-drift--rc-mkpl-metadata-drift)
- [7. Per-batch bulk align — consolidated marketplace patch](#7-per-batch-bulk-align--consolidated-marketplace-patch)
- [8. Opt-out flags — when drift IS intentional](#8-opt-out-flags--when-drift-is-intentional)

---

## Overview

Phase B of TRDD-c0ee9543 added an upstream-cross-validation pass to
`validate_marketplace.py`: for every plugin entry whose `source` resolves
to a reachable `plugin.json`, CPV diffs the marketplace entry against the
upstream plugin.json and emits findings against the marketplace side.

These recipes are the mechanical fixes for those findings. The validator
emits stable RC-MKPL-* codes; the fixer reads the code and routes here.

**When the agent should NOT auto-fix**: rows tagged "user-blessed" below
indicate cases where the drift may be intentional (brand-vs-canonical
name, etc.). In those cases the recipe is "add a `_cpv_skip_upstream_check`
opt-out flag", NOT "rewrite the entry".

---

## 1. Name mismatch — RC-MKPL-NAME-MISMATCH

### Symptom

`claude plugin install <name>@<marketplace>` returns "not found" because
the marketplace entry's `name` field does not equal the upstream
`plugin.json`'s `name` field. The install resolver hashes the user-typed
name against the marketplace entry name; if that name does not also exist
in the upstream `plugin.json`, the cache layer rejects the lookup.

This is the **exact** bug class that broke
`ai-maestro-visual-communicator-plugin` on 2026-05-11: the marketplace
entry said `ai-maestro-visual-communicator` while the plugin's own
`plugin.json` said `ai-maestro-visual-communicator-plugin`.

### Fix recipe

Edit the marketplace.json entry to match the upstream plugin.json name:

```json
{
  "name": "ai-maestro-visual-communicator-plugin",  // ← was "ai-maestro-visual-communicator"
  "source": { "source": "github", "repo": "Emasoft/ai-maestro-visual-communicator-plugin" }
}
```

### When this is intentional

If the marketplace lists multiple aliases for one plugin (e.g. a
brand name vs the canonical name), keep the marketplace-side name
and add a per-entry opt-out:

```json
{
  "name": "ai-maestro-visual-communicator",          // brand alias
  "source": { "source": "github", "repo": "Emasoft/ai-maestro-visual-communicator-plugin" },
  "_cpv_skip_upstream_check": true
}
```

The leading underscore signals "non-spec, intentional". Document the
alias in the README so install-time confusion is mitigated.

---

## 2. Version drift — RC-MKPL-VERSION-DRIFT

### Symptom

The marketplace entry declares a `version` field that differs from the
upstream plugin.json's `version` field. Per `plugin-marketplaces.md:696-698`
the plugin manifest always wins silently, so the marketplace-side version
is just visual drift that confuses users browsing the marketplace.

The triggering incident shipped marketplace entry `version: "1.0.0"` while
the plugin was already at `v1.2.2`.

### Fix recipe (preferred — drop the field)

Delete the marketplace.json `version` field entirely. The upstream
plugin.json is the single source of truth.

```diff
 {
   "name": "ai-maestro-visual-communicator-plugin",
-  "version": "1.0.0",
   "source": { "source": "github", "repo": "Emasoft/ai-maestro-visual-communicator-plugin" }
 }
```

### Fix recipe (alternate — align the field)

If the marketplace MUST advertise a version (e.g. for a UI that reads it
without fetching upstream), bump it to match upstream:

```diff
 {
   "name": "ai-maestro-visual-communicator-plugin",
-  "version": "1.0.0",
+  "version": "1.2.2",
   "source": { "source": "github", "repo": "Emasoft/ai-maestro-visual-communicator-plugin" }
 }
```

### Choice tree

| Source type      | Recommendation                                                |
|------------------|---------------------------------------------------------------|
| `github`         | Drop the field (plugin.json is always fetched at install)    |
| `url`            | Drop the field                                                |
| `git-subdir`     | Drop the field                                                |
| `relative-path`  | Drop the field (plugin.json is sibling-readable)             |
| `npm`            | Keep + align (npm's own version metadata may shadow)         |

---

## 3. Unknown entry field — RC-MKPL-UNKNOWN-FIELD

### Symptom

The marketplace entry carries a top-level field NOT defined by the Claude
Code marketplace spec. The most common offender is `scope` (which several
authors mistakenly use to mean "this plugin is local-only"). The spec has
no such field; `claude plugin validate` rejects entries that carry it.

### Fix recipe — DROP the field

```diff
 {
   "name": "my-plugin",
-  "scope": "local",
   "source": { "source": "github", "repo": "owner/my-plugin" }
 }
```

### UX-intent migration

If the intent was to express "install scope" (project-only vs user-wide),
that is a CLI-time concept, not a marketplace-time one. Document it in
the README:

```markdown
## Installation

For project-only scope:
    cd <project>; claude plugin install my-plugin@my-marketplace --scope project

For user-wide scope:
    claude plugin install my-plugin@my-marketplace --scope user
```

### CPV-private flags

Fields starting with `_` are CPV-private opt-out flags and are accepted
without warning. Document them at the top of marketplace.json:

```json
{
  "_cpv_comment": "Opt-out flags use leading underscore.",
  "plugins": [
    {
      "name": "drifting-plugin",
      "source": "...",
      "_cpv_skip_upstream_check": true
    }
  ]
}
```

---

## 4. Unknown source sub-field — RC-MKPL-UNKNOWN-SOURCE-FIELD

### Symptom

The `source` block has a sub-field NOT in the allowlist for its `source`
type. Example: a `github` source MUST be one of `{source, repo, ref, skipLfs}` —
adding `branch` or `tag` causes the install resolver to ignore it.

### Fix recipe — per source type

The allowed sub-fields below mirror `validate_marketplace.py::_KNOWN_SOURCE_FIELDS_BY_TYPE`
(the source of truth). `skipLfs` (v2.1.153) is a boolean that skips Git LFS
downloads during clone/update and is valid ONLY on `github` and `git` sources —
do NOT strip it during a fix.

| `source.source` | Allowed sub-fields                                | Common mistake                                |
|-----------------|---------------------------------------------------|-----------------------------------------------|
| `github`        | `source`, `repo`, `ref`, `skipLfs`               | Using `branch` or `tag` → switch to `ref`     |
| `url`           | `source`, `url`                                  | Adding `repo` → use `github` source instead   |
| `git`           | `source`, `url`, `ref`, `subdir`, `skipLfs`      | Pin via `ref`, not `commit` or `sha`          |
| `git-subdir`    | `source`, `url`, `subdir`, `ref`, `path`        | `subdir` is canonical; `path` accepted as compat |
| `npm`           | `source`, `package`, `version`                  | `tag` not supported — use `version`           |
| `directory`     | `source`, `path`                                 | Layout-B nested plugin; never use `repo`      |

### Example fix

```diff
 {
   "name": "my-plugin",
   "source": {
     "source": "github",
     "repo": "owner/my-plugin",
-    "branch": "main"
+    "ref": "main"
   }
 }
```

---

## 5. Source unreachable — RC-MKPL-UPSTREAM-UNREACHABLE

### Symptom

CPV could not fetch the upstream `plugin.json` for cross-validation. This
is a **WARNING**, not an error — it is fully expected on air-gapped CI,
flaky links, GitHub rate-limit windows, or private repos that need auth.

### Diagnostic-only — NOT auto-fixable

The validator skips name/version/metadata cross-check for this entry and
moves on. The marketplace entry's other findings still apply.

### When to silence the warning permanently

Three escape hatches, in order of granularity:

1. **Per-entry** (preferred for one offending entry):
   ```json
   {
     "name": "private-plugin",
     "source": { "source": "git", "url": "git@private:plugins/private-plugin" },
     "_cpv_skip_upstream_check": true
   }
   ```

2. **Per-marketplace** (whole marketplace is offline / air-gapped):
   Create a zero-byte sentinel file at
   `<marketplace-root>/.claude-plugin/.cpv-no-upstream-check`.

3. **Per-CI-run** (air-gapped pipeline):
   ```bash
   export CPV_SKIP_UPSTREAM_CROSS_CHECK=1
   uv run python scripts/validate_marketplace.py <path> --strict
   ```

Do NOT use the env-var bypass in `publish.py` — Gate 0 explicitly rejects
it (a release must not ship without the cross-check).

### Cache behaviour

CPV caches successful fetches at `~/.cache/cpv/plugin-json/<sha256>.json`
with a sidecar `.meta` (timestamp + fetcher version). TTL is 1 hour
(override via `CPV_PLUGIN_JSON_TTL_SECONDS=<N>`). A failed fetch is not
cached — next run will retry.

---

## 6. Description / author / keywords drift — RC-MKPL-METADATA-DRIFT

### Symptom

The marketplace entry's `description`, `author`, `keywords`, or
`homepage` field differs from upstream's `plugin.json`. These are NIT
severity — install still works — but they confuse users when the
marketplace UI shows a different description than the plugin's own
manifest.

### Fix recipe — preferred: drop the marketplace-side field

If the marketplace catalog UI fetches `plugin.json` at display time, drop
the marketplace-side field and let upstream win:

```diff
 {
   "name": "my-plugin",
-  "description": "Outdated marketplace description.",
-  "keywords": ["old", "tags"],
   "source": { "source": "github", "repo": "owner/my-plugin" }
 }
```

### Fix recipe — alternate: re-align the marketplace side

If the marketplace MUST mirror upstream (e.g. for offline browsing):

```diff
 {
   "name": "my-plugin",
-  "description": "Outdated marketplace description.",
+  "description": "Current upstream description (kept in sync via /cpv-doctor).",
   "source": { "source": "github", "repo": "owner/my-plugin" }
 }
```

Add a CI step that runs `/cpv-doctor <marketplace>` on a schedule so the
mirrored fields don't silently drift again.

### Severity ladder

| Field          | Severity | Rationale                                            |
|----------------|----------|------------------------------------------------------|
| `name`         | MAJOR    | Install breaks                                       |
| `version`      | MINOR    | Display drift; user sees wrong version pre-install   |
| `description`  | NIT      | UX papercut                                          |
| `author`       | NIT      | Branding mismatch                                    |
| `keywords`     | NIT      | Search-result quality                                |
| `homepage`     | NIT      | "Visit project" UX confusion                         |

---

## 7. Per-batch bulk align — consolidated marketplace patch

### Symptom

A single marketplace.json has N entries (often 5-20) ALL drifted in the
same way (e.g. all 9 entries carry the legacy `scope` field, all entries
have stale versions). One finding per (entry × drift) makes a noisy report
and is annoying to fix one-by-one.

### Fix recipe — single bulk patch

Generate ONE consolidated patch that fixes every drifted entry at once.
The shape is:

```python
# Pseudo-code for the consolidated fixer (use Edit tool sequentially)
mkpl_data = json.loads(mkpl_path.read_text())
for entry in mkpl_data["plugins"]:
    # Drop bogus `scope` field on every entry.
    entry.pop("scope", None)
    # Drop stale `version` field (let upstream win).
    entry.pop("version", None)
    # Align name to upstream — only if `_cpv_skip_upstream_check` is absent.
    if not entry.get("_cpv_skip_upstream_check"):
        upstream = fetch_upstream_plugin_json(entry)
        if upstream and upstream.get("name") and upstream["name"] != entry["name"]:
            entry["name"] = upstream["name"]
mkpl_path.write_text(json.dumps(mkpl_data, indent=2) + "\n")
```

The plugin-fixer agent does this via `Read` + `Edit` tools (no Python
exec). The Edit tool gives the user diff-by-diff visibility.

### Ordering invariant

Always apply MAJOR fixes (name mismatch, unknown field) BEFORE MINOR
fixes (version drift) BEFORE NIT fixes (metadata drift). The MAJOR
fixes are mandatory; the others are stylistic and can be deferred to a
follow-up commit if the user prefers.

### Conventional Commits

Use one commit per severity tier so reviewers can land MAJORs first:

```text
fix(marketplace): RC-MKPL-UNKNOWN-FIELD — drop bogus `scope` field on 9 entries
fix(marketplace): RC-MKPL-NAME-MISMATCH — align entry names with upstream plugin.json
chore(marketplace): RC-MKPL-VERSION-DRIFT — drop stale version pins (upstream wins)
chore(marketplace): RC-MKPL-METADATA-DRIFT — align description/keywords with upstream
```

---

## 8. Opt-out flags — when drift IS intentional

Three escape hatches in increasing granularity:

| Scope           | How                                                                 | When                              |
|-----------------|---------------------------------------------------------------------|-----------------------------------|
| Per-entry       | `"_cpv_skip_upstream_check": true` on the marketplace entry         | Brand-vs-canonical name alias     |
| Per-marketplace | Zero-byte file `.claude-plugin/.cpv-no-upstream-check`              | Whole marketplace is offline       |
| Per-CI-run     | `export CPV_SKIP_UPSTREAM_CROSS_CHECK=1`                            | Air-gapped CI run                  |

### Risk decision matrix

| Scenario                                                | Severity | Auto-fix? | Recipe |
|---------------------------------------------------------|----------|-----------|--------|
| User added drift intentionally (per-entry opt-out)      | NIT      | No        | Leave; document in README |
| Agent-introduced drift (no opt-out, no documentation)   | MAJOR    | Yes       | Realign per §1 / §7 |
| User wants the alias but forgot the opt-out             | MAJOR    | Ask first | "Add `_cpv_skip_upstream_check` OR realign?" |
| CI failure on air-gapped runner                         | WARNING  | No        | Add `.cpv-no-upstream-check` sentinel |

### Plugin-creator / plugin-fixer / cpv-upgrade-plugin pre-flight

These agents MUST refuse to ship a marketplace.json that emits any of
the MAJOR codes here UNLESS the entry carries `_cpv_skip_upstream_check`
or the marketplace carries `.cpv-no-upstream-check`. See agent prompts
for the exact gate text.

---

## Related

- `marketplace-error-index.md` §1.1 — RC-MKPL-* error code index
- `plugin-error-index.md` §20 — validate_marketplace cross-validation rules
- `empirical-loading-bugs.md` — install-resolver edge cases the cross-check now catches
- `iterative-fix-loop.md` — wrap these recipes in the standard fix → re-validate loop
- TRDD-c0ee9543 §5 Phase D — design notes for these recipes
