# Name Canonicalisation

## Table of Contents

- [The Rule](#the-rule)
- [Why It Matters](#why-it-matters)
- [How Agents Must Apply It](#how-agents-must-apply-it)
- [Common Wrong Patterns](#common-wrong-patterns)
- [Worked Examples](#worked-examples)
- [Refusal Templates](#refusal-templates)
- [Unreachable Upstream Fallback](#unreachable-upstream-fallback)
- [Cross References](#cross-references)

## Checklist

- [ ] Upstream `plugin.json` fetched (via TRDD-c0ee9543 Phase B fetcher) before drafting any marketplace entry
- [ ] Marketplace entry `name` matches `plugin.json.name` byte-for-byte
- [ ] No shortening, no brand-aliasing, no human-readable variants
- [ ] Unreachable upstream → draft with `"<UPSTREAM_PLUGIN_JSON_NAME_HERE>"` placeholder + comment, never invent

## The Rule

**The marketplace entry's `name` MUST equal the upstream `plugin.json`'s `name`. No exceptions, no brand-aliasing, no shortening.**

Plain language: when you scaffold or fix a `marketplace.json` plugin entry, the `name` field is copied character-for-character from the upstream `plugin.json` of the plugin being indexed. Capitalisation matches. Suffix (`-plugin`, `-helper`, `-cli`) matches. Even hyphenation matches.

## Why It Matters

The install resolver in Claude Code looks up plugins by name:

```
claude plugin install foo@mkpl
  ↓
read mkpl/.claude-plugin/marketplace.json
  ↓
find entry where entry.name == "foo"
  ↓
clone entry.source
  ↓
read .claude-plugin/plugin.json at HEAD
  ↓
assert plugin.json.name == "foo"   ← if mismatch, install FAILS
```

If the marketplace `.name` differs from `plugin.json.name`, the canonical install command emits a cryptic "not found" or "name mismatch" error that does not tell the user which side to fix. Hundreds of bug reports against `claude plugin install` have this root cause — they all reduce to "the marketplace lied about the plugin's identity."

PIT-001 in [common-pitfalls](common-pitfalls.md) is the canonical example: a marketplace entry named `ai-maestro-visual-communicator` indexed a plugin whose own `plugin.json` declared `ai-maestro-visual-communicator-plugin`. The plugin worked when cloned manually; it never worked through the marketplace.

## How Agents Must Apply It

The flow for any agent emitting or modifying a marketplace entry:

1. **Fetch the upstream plugin.json** — use the Phase B fetcher introduced by TRDD-c0ee9543. For `source: github` use the GitHub raw URL; for `source: url` use the URL with `/.claude-plugin/plugin.json` appended; for `source: relative-path` read the file at `<marketplace-dir>/<path>/.claude-plugin/plugin.json`.
2. **Read `plugin.json.name`** — the value of the top-level `name` key.
3. **Use it verbatim** as the marketplace entry's `.name`. Do not transform it. Do not strip suffixes. Do not change capitalisation.
4. **Reject any user request to use a different name** with the refusal template below.

The fetcher exists so the agent never has to "guess" the upstream name. If the fetcher returns content, the agent must use that content; if the fetcher fails (network down, repo private), the agent must NOT fall back to inventing a name — it must emit a placeholder and stop.

## Common Wrong Patterns

The 2026-05-11 incident catalog shows these recurring failure modes:

| Wrong pattern | Why agents do it | What it breaks |
|---|---|---|
| Strip `-plugin` suffix | Agent thinks the suffix is "redundant boilerplate" | Install resolver fails — plugin.json keeps the suffix |
| Strip `-cli` / `-helper` suffix | Same reasoning | Same failure |
| Convert kebab-case to snake_case | Agent applies "Python conventions" | Install resolver fails — plugin.json is the source of truth |
| Add a vendor prefix (`anthropic-foo` → `foo`) | Agent thinks the prefix is "implied by the marketplace name" | Resolver does no prefix-stripping; lookup fails |
| Use a "display name" from README | Agent confuses display name with identifier | Resolver only reads the `name` field, not display strings |
| Pluralise / singularise the name | Agent thinks "tools" sounds more natural than "tool" | Resolver does no inflection; lookup fails |

Every one of these turns the marketplace entry into a dead link.

## Worked Examples

### Example 1 — agent must NOT shorten

Upstream `plugin.json`:
```json
{"name": "ai-maestro-visual-communicator-plugin", "version": "0.6.3", ...}
```

Correct marketplace entry:
```json
{"name": "ai-maestro-visual-communicator-plugin", "source": "github", "repo": "owner/repo"}
```

Wrong marketplace entry (PIT-001):
```json
{"name": "ai-maestro-visual-communicator", "source": "github", "repo": "owner/repo"}
```

### Example 2 — agent must NOT change case

Upstream `plugin.json`:
```json
{"name": "GoLang-Helper", "version": "1.0.0"}
```

Correct marketplace entry:
```json
{"name": "GoLang-Helper", "source": "github", "repo": "owner/golang-helper"}
```

Wrong:
```json
{"name": "golang-helper", "source": "github", "repo": "owner/golang-helper"}
```

(The repo URL uses GitHub's case-insensitive name; the `name` field does NOT.)

### Example 3 — user requests rename

User: "Add my plugin to the marketplace but call it `viz` instead of `visualizer-plugin`."

Correct response: Refuse and explain. Use the [Refusal Templates](#refusal-templates) below. The user's options are (a) rename the plugin in its own `plugin.json` first, (b) accept the canonical name.

## Refusal Templates

When the user asks for a non-canonical name:

```
I cannot use "<requested-name>" because the upstream plugin.json
declares name="<canonical-name>". The Claude Code install resolver
looks up plugins by name and requires the marketplace entry to match
the upstream byte-for-byte; using a different name produces a
"not found" error at install time.

Two ways forward:
1. Rename the upstream plugin first: change plugin.json's name field,
   commit, then I add the entry with the new canonical name.
2. Use the canonical name "<canonical-name>" in the marketplace.

Which do you prefer?
```

## Unreachable Upstream Fallback

If the upstream fetcher returns a non-200 (network down, repo private without auth, deleted), do NOT invent a name. Emit a DRAFT entry with an explicit placeholder:

```jsonc
{
  // FIXME — upstream plugin.json could not be fetched at scaffold time.
  // Replace this placeholder with the exact value from
  // <source-url>/.claude-plugin/plugin.json's "name" field, then
  // re-run validate_marketplace.py --strict (upstream cross-validation
  // runs unconditionally — there is no flag to toggle it).
  "name": "<UPSTREAM_PLUGIN_JSON_NAME_HERE>",
  "source": {
    "source": "github",
    "repo": "owner/repo"
  }
}
```

The placeholder is intentionally invalid JSON-as-data; `validate_marketplace.py --strict` will flag it and force the user to resolve before publishing. This is the SAFE failure mode — better a loud TODO than a quietly-wrong identifier.

## Cross References

- [version-strategy](version-strategy.md) — the second rule that prevents marketplace drift
- [common-pitfalls](common-pitfalls.md) — PIT-001 is the canonical name-mismatch case
- [preflight-recipe](preflight-recipe.md) — the mechanical preflight that catches name mismatches before emit
