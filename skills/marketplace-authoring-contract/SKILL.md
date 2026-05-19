---
name: marketplace-authoring-contract
description: >
  Proactive marketplace.json authoring contract for plugin-touching agents. Use
  when drafting, modifying, or migrating any marketplace.json. Used dynamically via skills-index (TRDD-478d9687).
when_to_use: >
  Use when drafting, modifying, or migrating any marketplace.json. Apply every
  sub-rule when emitting entries. Run the preflight before declaring done.
tags:
  - marketplace
  - authoring
  - contract
allowed-tools: Read, Write, Edit, Bash(uv:*,git:*,gh:*,jq:*,curl:*), Glob, Grep, AskUserQuestion
user-invocable: false
---

# Marketplace Authoring Contract

## Overview

Seven sub-rules make marketplace.json authoring deterministic. Agents internalise them and emit correct entries on the FIRST try; `validate_marketplace.py --strict --cross-validate-upstream` becomes a safety net.

## Prerequisites

- FS write access to the marketplace.json
- `validate_marketplace.py --strict --cross-validate-upstream` runnable
- Remote sources: upstream `plugin.json` reachable via TRDD-c0ee9543 Phase B fetcher

## Instructions

Read the seven sub-rules in order:

1. [name-canonicalisation](references/name-canonicalisation.md) — entry `name` equals upstream byte-for-byte.
2. [version-strategy](references/version-strategy.md) — omit on remote, require on local.
3. [known-fields](references/known-fields.md) — closed 15-field allowlist.
4. [source-shape](references/source-shape.md) — per-source field rules.
5. [layout-decision-tree](references/layout-decision-tree.md) — count, then same/different repos.
6. [common-pitfalls](references/common-pitfalls.md) — PIT-001..PIT-007.
7. [preflight-recipe](references/preflight-recipe.md) — 4-step preflight.

Copy this checklist and track your progress:

- [ ] Step 1 baseline (modify/migrate flows only)
- [ ] Step 2 fetch upstream + cross-check
- [ ] Layout pinned before drafting
- [ ] Source shape matches canonical example
- [ ] No PIT-NNN pattern in draft
- [ ] Step 3 emit
- [ ] Step 4 post-emit validator (exit 0 or known-baseline only)

## Output

`marketplace.json` passing the validator on the FIRST try, zero new findings vs baseline.

## Error Handling

| Error | Resolution |
|-------|------------|
| Upstream fetch fails | Emit placeholder draft; step 4 blocks ship |
| Non-canonical name requested | Refuse via `name-canonicalisation.md#refusal-templates` |
| Unknown field requested | Refuse via `known-fields.md#refusal-patterns` |
| Step 4 new finding | Re-enter step 2, fix, re-emit |
| Finding unclear | Stop, ask user |

The contract is non-negotiable.

## Examples

Input: plugin-creator scaffolds for `github.com/owner/foo-plugin`.

```json
{"name":"foo-plugin","source":"github","repo":"owner/foo-plugin"}
```

Name matches upstream. No version on github source. Validator exits 0.

Input: plugin-fixer addresses PIT-002 stale-version. Output: version DROPPED. Finding clears.

## Resources

- [Name Canonicalisation](references/name-canonicalisation.md)
  > The Rule · Why It Matters · How Agents Must Apply It · Common Wrong Patterns · Worked Examples · Refusal Templates · Unreachable Upstream Fallback · Cross References
- [Version Strategy](references/version-strategy.md)
  > The Rule · Why It Matters · Local vs Remote Sources · How Each Agent Applies It · Worked Examples · Migration Behaviour · Edge Cases · Cross References
- [Known Fields](references/known-fields.md)
  > The Allowlist · Why a Closed Allowlist · Forbidden Fields · Refusal Patterns · Future Field Additions · Self-Consistency With the Validator · Cross References
- [Source Shape](references/source-shape.md)
  > The Rule · Source GitHub · Source URL · Source Git · Source Git-Subdir · Source NPM · Source Relative-Path · Source Layout C Self-Entry · Field Allowlist Summary · Common Wrong Shapes · Cross References
- [Layout Decision Tree](references/layout-decision-tree.md)
  > The Decision Tree · Layout A — Hub and Spoke · Layout B — Nested Monorepo · Layout C — Self-Marketplace-in-Plugin · When to Migrate Between Layouts · Disqualifying Conditions · Wizard Question Flow · Cross References
- [Common Pitfalls](references/common-pitfalls.md)
  > PIT-001 — Name Mismatch via Suffix Stripping · PIT-002 — Stale Version on Remote Source · PIT-003 — Top-Level Scope Field · PIT-004 — Layout C Self-Entry Missing Source · PIT-005 — Source GitHub With Full URL · PIT-006 — Homepage Pointing at Wrong Repo · PIT-007 — Category With Arbitrary Value · Cross References
- [Preflight Recipe](references/preflight-recipe.md)
  > The Mechanical Preflight · Step 1 — Validate Existing · Step 2 — Fetch Upstream and Cross-Check · Step 3 — Emit · Step 4 — Post-Emit Sanity Check · When to Skip Steps · Failure Modes · Cross References
- Sibling: `skills/migrate-marketplace-architecture/`
- Sibling: `skills/fix-marketplace-validation/`
