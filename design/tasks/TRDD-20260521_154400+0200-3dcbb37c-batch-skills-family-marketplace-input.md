---
trdd-id: 3dcbb37c-8e98-41ac-8c1c-b87baf2781ae
title: Batch-skills family — marketplace-input + same-turn scan/verify/fix
status: completed
created: 2026-05-21T15:44:00+0200
updated: 2026-05-21T19:17:12+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-3dcbb37c — Batch-skills family (marketplace-input)

## Source

User directive (verbatim, condensed):

> Extend `cpv-batch-fix` to accept a marketplace URL or local path
> as input (in addition to single file / single skill / single plugin
> / list of paths / list of URLs). Add seven new sibling skills:
>
> * `cpv-batch-validate` — validation only.
> * `cpv-batch-security-audit` — security scanning only.
> * `cpv-batch-caching-audit` — caching-optimization audit only.
> * `cpv-batch-caching-optimize` — caching-optimization FIX (mirror of `cpv-batch-fix` for caching).
> * `cpv-batch-validate-and-fix` — validate + fix in ONE turn per file. Same-turn scan / verify / fix. False-positive verification by targeted agent in the same turn.
> * `cpv-batch-full-scan-and-fix` — same-turn validate + security + caching audit + caching optimize + fix.
> * (`cpv-batch-fix` extension above).
>
> All skills accept the same input shapes. All are user-invocable.
> All registered in `the-skills-menu`.
> Test on `Emasoft/emasoft-plugins` marketplace. Iterate on every
> false positive — improve the scanners until 0 FPs remain. Use
> targeted LLM-externalizer agents for FPs that cannot be resolved
> by static-analysis improvements alone, but keep agent input
> minimal (just the suspicious code range, not whole files).

User constraint: commit often; publish only when ALL tasks complete + verified.

## Scope

Eight skill targets in one TRDD because they share:

* **Identical input grammar** (the universal batch-input spec, §3).
* **Identical orchestration shape** (resolve → enumerate → fan out parallel agents from the main session → aggregate → return one line).
* **Shared common-code helpers** (`cpv_marketplace_input.py` — input resolution + plugin enumeration).
* **The same FP-iteration loop** during the verification phase against `Emasoft/emasoft-plugins`.

The scope-aware doctor skills (`cpv-batch-scope-diagnose`, `cpv-batch-scope-fix`, `cpv-batch-scope-diagnose-and-fix`) are in **TRDD-a175f78d** (sibling document) because their input shape is different (LOCAL project folder paths only, plus a scope parameter) and their target is the Claude installation rather than plugin source trees.

## §1 — Common input grammar

Every batch skill accepts the union of these input shapes via a single `<input>` argument plus optional flags:

| Shape | Form | Resolved to |
|---|---|---|
| Single file | `/path/to/foo.py` | `[("file", abs_path)]` |
| Single skill | `/path/to/skills/<name>` OR `/path/to/skills/<name>/SKILL.md` | `[("skill", skill_dir)]` |
| Single plugin (local) | `/path/to/plugin-root` (folder containing `.claude-plugin/plugin.json`) | `[("plugin", plugin_root)]` |
| Single plugin (URL) | `https://github.com/owner/repo` OR `owner/repo` | clone → `[("plugin", tmp_clone_root)]` |
| Single plugin (release URL) | `https://github.com/owner/repo/releases/tag/vX.Y.Z` | download tarball → `[("plugin", tmp_extract_root)]` |
| Marketplace (local) | `/path/to/marketplace-root` (folder containing `.claude-plugin/marketplace.json`) | parse + enumerate → `[("plugin", root1), ("plugin", root2), …]` |
| Marketplace (URL) | `https://github.com/owner/marketplace-repo` OR `owner/marketplace-repo` | fetch marketplace.json + clone every referenced plugin → list of plugin roots |
| List of paths (file) | `@/path/to/list.txt` (each line a path) | each line resolved with the same grammar |
| List of paths (CLI) | `--list a b c` OR comma-separated | each token resolved with the same grammar |

The detector is deterministic: if the path is a URL → fetch; if the path resolves to a `marketplace.json` (root or `.claude-plugin/`) → enumerate; if it has `.claude-plugin/plugin.json` → single plugin; if it's `SKILL.md` or a `skills/<name>/` folder → single skill; if it's any other file → single file. Ambiguity is resolved CRITICAL-error with a clear message ("path is both `<X>` and `<Y>` — pick one explicitly via `--input-kind`").

The resolver is implemented in `scripts/cpv_marketplace_input.py` (NEW). It exposes:

```python
def resolve(input_spec: str | list[str], *, allow_url: bool = True) -> list[ResolvedInput]:
    """Return a list of ResolvedInput entries (a tagged-union dataclass)."""

@dataclass
class ResolvedInput:
    kind: Literal["file", "skill", "plugin", "marketplace"]
    abs_path: Path           # local path (a clone tmp dir for URL inputs)
    source_url: str | None   # github URL when applicable; None for local-only
    cleanup_callback: Callable[[], None] | None  # tmp-dir cleanup; called after all consumers finish
```

Cleanup callbacks are invoked after the entire batch completes (the orchestrator wraps them in a `finally:` block). For marketplace-URL inputs that produce N plugin clones, the cleanup callback is reference-counted across the per-plugin shards so the first-to-finish doesn't yank the directory the others are still reading.

## §2 — Common orchestrator skeleton

Every batch skill's slash-command body follows this exact 4-step pattern:

```text
1. Read $1 (input spec). Run cpv_marketplace_input.resolve(input).
2. Build a per-plugin "plan" file under ${TMPDIR}/cpv-batch/<ts>/.
3. Dispatch N parallel <op>-agent calls from the SAME main-session message
   (one per resolved plugin, capped at --max-parallel, default 8). Each
   agent gets the plan path; returns ONE line: "[plugin-<i>] <kind> done: <details>".
4. Aggregate the per-plugin lines into a single summary table. Print + done.
```

This is the same shape `/cpv-batch-fix` already uses (TRDD-71e68ab5) — the new skills replicate it, parameterised on the OPERATION the dispatched agent performs:

| Skill | Dispatched agent | Mode | Per-plugin Output |
|---|---|---|---|
| `cpv-batch-fix` (extended) | `plugin-fixer` | `batch_shard` (existing) | findings fixed / remaining |
| `cpv-batch-validate` | `plugin-validator` (existing — haiku) | `batch_validate` (NEW frontmatter mode) | severity counts + report path |
| `cpv-batch-security-audit` | `plugin-validator` | `batch_security_audit` (NEW mode) | security severity counts + report path |
| `cpv-batch-caching-audit` | `cache-optimizer-agent` (existing) | `batch_audit` (NEW mode) | cache-issue counts + report path |
| `cpv-batch-caching-optimize` | `cache-optimizer-agent` | `batch_fix` (NEW mode) | applied/remaining + report path |
| `cpv-batch-validate-and-fix` | `plugin-fixer` | `batch_same_turn_validate_fix` (NEW mode) | "<C>/<M>/<n>/<t> found, <X>/<C+M+n> fixed, <Y> FPs verified, <Z> remaining" |
| `cpv-batch-full-scan-and-fix` | `plugin-fixer` | `batch_same_turn_full` (NEW mode) | same one-liner but covers validate + security + caching audit + caching optimize |

The `batch_same_turn_*` modes are the key novelty of this TRDD: they require the agent to do the **scan, verify (FP check), and fix in ONE turn** so the per-plugin source code is read at most once, the report is never serialised + re-read in a separate turn, and the FP-verification step happens immediately while the relevant code is already in the agent's context window.

## §3 — Same-turn scan / verify / fix contract

For `cpv-batch-validate-and-fix` and `cpv-batch-full-scan-and-fix`, the agent's turn must:

```text
1. Read the plugin (one Read pass per file in scope).
2. Run validate_plugin.py (and security/caching scanners) in process —
   producing an in-memory findings list (no JSON report written to disk).
3. For each finding the agent considers ambiguous:
   - Re-read ONLY the matched line ± 5 lines via Read with offset/limit.
   - Apply the rule's expected exploit shape against the read range.
   - If still ambiguous after the AST/schema/markdown context check,
     dispatch a SINGLE `mcp__plugin_llm-externalizer_llm-externalizer__chat`
     call with the suspect range ONLY (≤ 200 LOC). The externalizer
     returns "real" / "false-positive" / "uncertain".
4. For each "real" finding: apply the fix (Edit tool) in the same turn.
   No intermediate JSON written. The agent does NOT exit and restart.
5. After all findings processed, write ONE summary line to the status file
   (used by the aggregator).
```

The token-cost guarantee: the per-plugin agent reads each source file at most ONCE (not three times as in the validate → write report → re-read code → fix flow that the v2.99.x pipeline used). The orchestrator's main-session cost stays at ~2-3K tokens (paths + status lines only).

The "minimum-token-FP-check" requirement maps directly: the LLM-externalizer call sends only the suspect range, not the whole file. `mcp__plugin_llm-externalizer_llm-externalizer__chat` already supports `input_files_paths` with a sub-file range via the tool's own range-extract path — the agent passes `<file>:<start_line>-<end_line>` rather than the bare path.

## §4 — Skill file structure

Each new skill lives under `skills/<skill-name>/SKILL.md`:

```text
skills/cpv-batch-validate/SKILL.md
skills/cpv-batch-security-audit/SKILL.md
skills/cpv-batch-caching-audit/SKILL.md
skills/cpv-batch-caching-optimize/SKILL.md
skills/cpv-batch-validate-and-fix/SKILL.md
skills/cpv-batch-full-scan-and-fix/SKILL.md
```

(`cpv-batch-fix` already exists as a slash command at `commands/cpv-batch-fix.md` — the extension is a body edit, not a new skill file. We will ALSO add a thin `skills/cpv-batch-fix/SKILL.md` that loads when invoked via `Skill({skill: "..."})` so it's symmetric with the rest of the family.)

Frontmatter contract for every batch skill (all of them):

```yaml
---
name: cpv-batch-<op>
description: "<op-specific one-liner ending in 'across single file / single skill / single plugin / list / marketplace.'>"
user-invocable: true              # per user's directive
allowed-tools: Read, Write, Bash, Glob, Grep, AskUserQuestion
---
```

Each SKILL.md body has six sections (canonical):

1. **Overview** — what the skill batches over.
2. **Input grammar** — table copied verbatim from §1 above.
3. **Prerequisites** — `cpv_marketplace_input.py` available; network for URL inputs.
4. **Steps** — the §2 4-step skeleton specialised for this op.
5. **Output** — one summary line + a path to the per-batch report dir.
6. **Error handling** — partial-failure semantics, retry guidance.

## §5 — `the-skills-menu` integration

`skills/the-skills-menu/SKILL.md` and `references/skills-catalog.md` are extended:

* Add a new domain row `"Batch / fleet"` to the Plugin Skills table.
* Add the seven entries to that row.
* Add per-skill rows to the catalog reference (inputs + return contracts).

Per the user's directive: every CPV agent preloads `the-skills-menu` so adding entries here makes the batch skills discoverable across the agent fleet without touching individual agents.

Test invariant in `tests/test_the_skills_menu_batch_family.py`: the menu lists ALL seven entries; every entry resolves to an existing `skills/<name>/SKILL.md`; every SKILL.md has `user-invocable: true`.

## §6 — FP-iteration loop on `Emasoft/emasoft-plugins`

Acceptance: after the skills land, the verification step is to run **`cpv-batch-full-scan-and-fix` against `Emasoft/emasoft-plugins` URL** (the 17-plugin marketplace) and iterate:

```text
do:
    findings = run cpv-batch-full-scan-and-fix
    for each finding flagged "FP" by the per-plugin agent:
        # The agent already did the AST/schema/markdown context check
        # AND the LLM-externalizer verify on the suspect range.
        if both refuted the finding → confirmed FP.
        log to FP-corpus file
    if FP-corpus is non-empty:
        for each FP class:
            improve the scanner (regex / AST classifier / suppression rule)
            add a regression test pinning the FP shape
        # iron rule: NEVER delete a rule; only tighten / add contextual checks
until FP-corpus is empty
```

Done state: a single `cpv-batch-full-scan-and-fix Emasoft/emasoft-plugins` run produces 0 confirmed FPs.

Reports for each iteration land under `$MAIN_ROOT/reports/batch-fp-iteration/<iter-N>/`.

## §7 — Token-cost budgets

| Skill | Main-session cost | Per-plugin agent cost |
|---|---|---|
| `cpv-batch-validate` | ~2-3K (resolve + dispatch + aggregate) | scoped to the plugin only |
| `cpv-batch-fix` (extension) | ~2-3K | unchanged from TRDD-71e68ab5 |
| `cpv-batch-security-audit` | ~2-3K | scoped to the plugin only |
| `cpv-batch-caching-audit` | ~2-3K | scoped to the plugin only |
| `cpv-batch-caching-optimize` | ~2-3K | scoped to the plugin only |
| `cpv-batch-validate-and-fix` | ~2-3K | reads each source file ONCE (one-pass scan + verify + fix) |
| `cpv-batch-full-scan-and-fix` | ~2-3K | reads each source file ONCE; runs four scanners over the in-memory content |

The orchestrator NEVER reads finding bodies into its own context — only the per-plugin one-line returns.

## §8 — File list

NEW:

* `scripts/cpv_marketplace_input.py` (~300 LOC) — resolver + plugin enumeration.
* `scripts/cpv_batch_orchestrator.py` (~200 LOC) — shared dispatch helper (plan → parallel-Agent-block → aggregate).
* `commands/cpv-batch-validate.md`
* `commands/cpv-batch-security-audit.md`
* `commands/cpv-batch-caching-audit.md`
* `commands/cpv-batch-caching-optimize.md`
* `commands/cpv-batch-validate-and-fix.md`
* `commands/cpv-batch-full-scan-and-fix.md`
* `skills/cpv-batch-fix/SKILL.md` (the existing command's thin SKILL-wrapper)
* `skills/cpv-batch-validate/SKILL.md`
* `skills/cpv-batch-security-audit/SKILL.md`
* `skills/cpv-batch-caching-audit/SKILL.md`
* `skills/cpv-batch-caching-optimize/SKILL.md`
* `skills/cpv-batch-validate-and-fix/SKILL.md`
* `skills/cpv-batch-full-scan-and-fix/SKILL.md`
* `tests/test_cpv_marketplace_input.py` (~25 tests)
* `tests/test_cpv_batch_orchestrator.py` (~15 tests)
* `tests/test_the_skills_menu_batch_family.py` (~10 tests pinning menu integration)
* `tests/test_batch_skill_frontmatter.py` (~14 tests pinning user-invocable + allowed-tools)
* `tests/test_batch_fp_iteration_emasoft_plugins.py` — the empirical FP-iteration test (network-dependent, skipped offline)

MODIFIED:

* `commands/cpv-batch-fix.md` — input-grammar table + marketplace expansion path.
* `agents/plugin-fixer.md` — add `batch_same_turn_validate_fix`, `batch_same_turn_full` modes.
* `agents/plugin-validator.md` — add `batch_validate`, `batch_security_audit` modes.
* `agents/cache-optimizer-agent.md` — add `batch_audit`, `batch_fix` modes.
* `skills/the-skills-menu/SKILL.md` — Batch / fleet row.
* `skills/the-skills-menu/references/skills-catalog.md` — per-skill rows.

## §9 — Acceptance

* [ ] All 14 new files exist + every SKILL.md is `user-invocable: true`.
* [ ] `the-skills-menu` lists every new skill in its plugin-skills table.
* [ ] Unit tests for the resolver pass (25/25), orchestrator pass (15/15), menu-family pass (10/10), frontmatter pass (14/14).
* [ ] `cpv-batch-full-scan-and-fix Emasoft/emasoft-plugins` produces 0 confirmed FPs after the FP-iteration loop has run to fixed point.
* [ ] CPV self-scan stays at 0/0/0/0 + WARNING-only.
* [ ] Full test suite passes.
* [ ] CI ✓ + Release ✓ + Notify Marketplace ✓ green.
* [ ] No publish before all of the above are green.

## §10 — Lesson reservation

The design here keeps every same-turn scanner colocated in the per-plugin agent so the source code is read ONCE per run. Any future regression that re-introduces a "scan → write report → reload → fix" flow defeats the token-cost contract — the test `test_batch_validate_and_fix_single_read_per_file.py` pins that invariant via Read-call counting in a stubbed agent harness.
