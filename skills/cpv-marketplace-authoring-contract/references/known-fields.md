# Known Fields

## Table of Contents

- [The Allowlist](#the-allowlist)
- [Why a Closed Allowlist](#why-a-closed-allowlist)
- [Forbidden Fields](#forbidden-fields)
- [Refusal Patterns](#refusal-patterns)
- [Future Field Additions](#future-field-additions)
- [Self-Consistency With the Validator](#self-consistency-with-the-validator)
- [Cross References](#cross-references)

## Checklist

- [ ] Every field in the emitted entry appears in the allowlist below
- [ ] No `scope`, `private`, `published`, `requires`, `archived` fields anywhere
- [ ] If the user requests an unknown field, the agent REFUSES and points at this file
- [ ] This file never recommends a field the validator rejects — the one-way subset-ratchet test fails if it does (see [Self-Consistency With the Validator](#self-consistency-with-the-validator))

## The Allowlist

The marketplace entry top-level field allowlist is **exactly**:

```
name             — REQUIRED, canonical plugin identifier (see name-canonicalisation)
description      — short user-facing string
version          — conditional (see version-strategy)
author           — string or object {name, email, url}
homepage         — full URL to plugin docs / landing page
repository       — full URL to source repo (when different from source.repo)
license          — SPDX identifier (MIT, Apache-2.0, …)
keywords         — array of strings, for marketplace search
source           — REQUIRED, the install source (see source-shape)
category         — broad taxonomy slot (see common-pitfalls PIT-007)
tags             — array of strings, finer-grained than category
claude_versions  — object {min, max} compatibility window
platforms        — array of strings (macos, linux, windows)
alwaysLoad       — boolean, v2.1.121 — opt into eager skill+command load
headersHelper    — string, v2.1.85+ — HTTP headers helper for http hooks
```

No other fields. This list is closed.

## Why a Closed Allowlist

Open-allowlist marketplaces (anything-goes JSON) accumulate cruft:

1. **Field-name collision risk.** When `claude plugin install` reads a marketplace entry and sees `"private": true`, what does the resolver do? If the resolver does nothing, the field is silently misleading. If the resolver one day adopts the field with different semantics than the author intended, every old marketplace breaks.
2. **Validator drift.** Open allowlists let agents invent fields that the validator accepts today but rejects after a minor version bump — flaky validation results across CPV versions.
3. **User confusion.** "I see `scope` in the example marketplace.json — what does that do?" Nothing. But someone wrote it, so someone else assumed it worked.

The closed allowlist eliminates all three. Fields ARE the contract.

## Forbidden Fields

Five fields turn up repeatedly in wrong agent output. The contract explicitly forbids each, with rationale + the correct alternative:

| Forbidden | Why agents try to add it | Correct alternative |
|---|---|---|
| `scope` | Conflated with `claude plugin install --scope <local\|user\|project>` install flag | Document the recommended scope in plugin README, OR set as default in plugin.json's `settings` block |
| `private` | Conflated with GitHub repo visibility | Rely on `source: github` returning 404 on private repos for unauthed installs; do not embed visibility in marketplace |
| `published` | Conflated with `claude_versions` compatibility window | Use `claude_versions: { min: "2.1.x" }` |
| `requires` | Conflated with `plugin.json.dependencies` block | Use plugin.json's `dependencies` block at the upstream — the marketplace does not duplicate dependency declarations |
| `archived` | Conflated with GitHub archived-repo status | Mark the GitHub repo as archived; the marketplace will reflect it; do not duplicate state |

Each one was observed in 2026-05-11 incident output. PIT-003 in [common-pitfalls](common-pitfalls.md) is the canonical `scope` case.

Other invented fields seen in the wild — all forbidden:

- `maintained` (boolean) — duplicates `archived` semantics
- `featured` (boolean) — marketplaces don't curate; if you want a featured plugin, list it first
- `downloadCount` / `installs` — install counts come from the resolver, not author-declared
- `rating` (number) — there is no rating system; authors can't self-rate
- `verified` (boolean) — only Anthropic can verify; authors can't self-verify
- `deprecated` (boolean) — use `archived` repo status; the marketplace will reflect it
- `language` (string) — use `tags` or `category`
- `priority` (number) — entries are listed in `plugins` array order

## Refusal Patterns

When the user asks for an unknown field:

```
The marketplace entry field "<requested>" is not in the contract's
allowlist. The closed-allowlist design exists to prevent install-
resolver edge cases.

For your use case ("<what user wanted>"), the right tool is:
  <suggestion based on the forbidden-fields table>

Reference: skills/cpv-marketplace-authoring-contract/references/known-fields.md
```

When the user asks "why can't I just add it":

```
Two reasons:
1. The install resolver does not consume the field — it would be
   silently ignored, misleading future readers.
2. If Anthropic later adopts the same field name with different
   semantics, your marketplace breaks at install time.

The closed allowlist is part of the contract.
```

## Future Field Additions

This allowlist matches CPV v2.32.0's understanding of `plugin-marketplaces.md` as of 2026-05-11. Anthropic may add fields in future Claude Code releases. The flow for adopting a new field:

1. Anthropic ships a new official field (e.g. `licenseUrl` in some hypothetical v2.2.x).
2. CPV adds it to the validator's `OPTIONAL_PLUGIN_FIELDS` constant. The
   subset-ratchet test still passes — the validator accepting MORE than this
   contract recommends is the allowed direction, so this step does not, on its
   own, force a change here.
3. Decide whether agents should be allowed to emit the new field. If yes,
   add it to the [Allowlist](#the-allowlist) above in the same release.
4. Ship the CPV release. The contract now recommends the field for all agents.

The ratchet only fires in ONE direction: if this file ever recommends a field the validator REJECTS (and it is not grandfathered into `_KNOWN_CONTRACT_DRIFT_FIELDS`), the test fails. The reverse — the validator gaining a field this file lacks — is allowed and does not fail the test, so keep this file in step with the validator deliberately rather than relying on the ratchet to force it.

## Self-Consistency With the Validator

The architectural test `test_contract_known_fields_match_validator_allowlist`
(in `tests/test_marketplace_authoring_contract.py`) is the load-bearing
self-consistency check. It is a ONE-WAY subset ratchet, not a bidirectional
equality check:

```python
def test_contract_known_fields_match_validator_allowlist():
    from validate_marketplace import _KNOWN_MARKETPLACE_ENTRY_FIELDS
    contract_fields = _parse_contract_known_fields()    # parses this file
    validator_fields = set(_KNOWN_MARKETPLACE_ENTRY_FIELDS)
    # The contract must never recommend a field the validator rejects.
    # Four pre-existing drift fields are grandfathered in (see below).
    new_drift = (contract_fields - validator_fields) - _KNOWN_CONTRACT_DRIFT_FIELDS
    assert not new_drift
```

The check is intentionally directional: the validator may accept MORE fields
than this contract recommends (it auto-accepts any plugin-manifest field per
`plugin-marketplaces.md`), but this contract must NEVER recommend a field the
validator would reject. So adding a field to `validate_marketplace.py` does
NOT fail the test; only adding a validator-rejected field to THIS file does.
Four fields (`alwaysLoad`, `headersHelper`, `claude_versions`, `platforms`)
are pre-existing drift, grandfathered into the test's
`_KNOWN_CONTRACT_DRIFT_FIELDS` allowlist so they do not fail it; new drift
outside that set is a real bug and breaks the build.

The parser `_parse_contract_known_fields()` reads the [Allowlist](#the-allowlist) section and extracts identifiers from the code block. Keep the format machine-parseable: one field per line, identifier first, dash-space separator.

## Cross References

- [name-canonicalisation](name-canonicalisation.md) — `name` is the most-important known field
- [version-strategy](version-strategy.md) — when `version` is required vs forbidden
- [source-shape](source-shape.md) — `source` field's per-type allowlist
- [common-pitfalls](common-pitfalls.md) — PIT-003 (scope) and PIT-007 (category misuse)
