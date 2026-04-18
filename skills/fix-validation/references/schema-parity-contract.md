# CPV Schema-Parity Contract

**What CPV guarantees — and what it does NOT.**

## Table of Contents

- [What CPV does](#what-cpv-does)
- [The contract](#the-contract)
- [What this contract does NOT say](#what-this-contract-does-not-say)
- [What IS covered](#what-is-covered)
- [Validator-gap protocol](#validator-gap-protocol)
- [Historical incidents](#historical-incidents)
- [Related](#related)

## Checklist (use when investigating a suspected validator gap)

- [ ] Confirm CPV on the source reports zero findings above WARNING
- [ ] Capture the exact Claude Code runtime error (verbatim `Validation errors: ...`)
- [ ] Identify the manifest snippet the runtime rejected
- [ ] Note the CPV version that passed the source
- [ ] File a validator-gap issue (NOT a plugin patch) with the three items above
- [ ] Add a regression test mirroring the failing manifest
- [ ] Release a CPV validator fix before considering the issue closed

## What CPV does

CPV validates plugin **sources**: a local folder, a GitHub repo, a remote archive. It does not install anything. Plugins do not need to be installed to be scanned. The commands `/cpv-validate-plugin`, `/cpv-validate-github-plugin`, `/cpv-validate-project-scope` all operate on sources, not installation state.

## The contract

CPV's manifest / agent / skill / hook / MCP / LSP validators mirror the rules Claude Code applies when it reads the same files at install time. Concretely: if CPV reports zero schema findings on a source, `claude plugin install` on that SAME source should not be rejected for a schema reason.

## What this contract does NOT say

- It is **not** a promise that `install` will succeed. Install can fail for reasons CPV does not (and cannot) check:
  - Network errors during git clone / archive fetch
  - Missing git refs, tags, or branches
  - Missing host binaries — LSP servers that aren't on `$PATH`, MCP `command` executables that aren't installed
  - Runtime execution errors from hooks, SessionStart scripts, or plugin-shipped binaries
  - Permission issues (file mode bits, GitHub repo visibility, PAT scope)
  - OS-specific gaps (Windows shell scripts, macOS Gatekeeper, Linux-only paths)
- It is **not** a claim that a plugin must be installed to be validated.
- It is **not** a claim about runtime behavior — CPV does not execute plugin code.

## What IS covered

Every rule the runtime enforces via the manifest Zod schema must be mirrored in CPV:

- Required fields (`name`, `userConfig.<key>.title`, `userConfig.<key>.type`, ...)
- Enum constraints (`userConfig.<key>.type ∈ {string, number, boolean, directory, file}`)
- Type coercions (`default` must match declared `type`; no bool for `number`)
- Identifier regex (userConfig keys, channel names, skill names)
- Structural invariants (channels[].server must match an mcpServers key)
- Path sanity (no `..` traversal in manifest path fields)
- Frontmatter schemas for agents, skills, commands, hooks

## Validator-gap protocol

If a CPV-clean source IS rejected by Claude Code at install time for a schema reason, that is a **validator gap** in CPV — not a bug in the plugin. Protocol:

1. Capture the exact runtime error message (`Validation errors: userConfig.<key>.field: ...`).
2. File an issue against `Emasoft/claude-plugins-validation` with:
   - The error message, verbatim
   - The manifest snippet the runtime rejected
   - The CPV version that passed it (e.g. `v2.22.4`)
3. Fix is a validator update + regression test + release, not a plugin patch.

## Historical incidents

| Date | Incident | CPV fix | Release |
|---|---|---|---|
| 2026-04-18 | `ai-maestro-janitor` v0.1.2 — 11 `userConfig` entries missing `type` — passed CPV ≤ v2.22.3, rejected at install | Added `type` to the required-field set; narrowed enum from `{string,number,integer,boolean,array,object}` to runtime-accurate `{string,number,boolean,directory,file}` | v2.22.4 |
| 2026-04-18 | Follow-up hardening — plugin-creator step-3 now delegates to plugin-fixer; heuristic table added to `plugin-structure-fixes.md`; regression guards added to test suite | Workflow + docs + tests | v2.22.5 |

## Related

- [Plugin Error Index](plugin-error-index.md) — lookup for every MAJOR the validator emits
- [Plugin Structure Fixes](plugin-structure-fixes.md) — "userConfig schema invalid" section has the field-name → type heuristic table the fixer uses
