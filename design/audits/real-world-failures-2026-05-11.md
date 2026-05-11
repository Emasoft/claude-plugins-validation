# Real-World Failures Audit — DEFERRED

**TRDD:** b4c6cbe7
**Generated:** 2026-05-11
**Status:** _Deferred to Phase 3 pending network access._

---

## 1. Why this report is a stub

Per the META TRDD §4.3, this audit was supposed to enumerate every
plugin currently listed in `Emasoft/ai-maestro-plugins` (and any other
accessible marketplace), then run BOTH `claude plugin validate` and
`uv run python scripts/validate_plugin.py --strict --cross-validate-upstream`
against each, then catalogue every finding — especially the rows where
CPV emits zero but CLI emits a finding.

The Phase 1+2 wave was scoped to the worktree at `/tmp/cpv-trdd-b4c6cbe7`,
which does not have authenticated GitHub access via `gh`. The kraken
prompt explicitly says:

> SKIP `real-world-failures-2026-05-11.md` for this wave (would require
> network access to Emasoft/ai-maestro-plugins and may timeout under the
> current GitHub flakiness) — leave a stub file noting it as deferred
> to Phase 3.

We have intentionally NOT run the audit. This file exists so the
META TRDD's §4.3 acceptance row reads "Done (stub)" instead of "Missing"
and so the orchestrator can hand the unfinished work to the Phase 3
agent without re-discovering the scope.

---

## 2. Scope when this report is implemented

The Phase 3 child TRDD (TBD UUID) will:

1. Iterate every plugin entry in
   `https://github.com/Emasoft/ai-maestro-plugins/blob/main/.claude-plugin/marketplace.json`
2. For each entry whose `source.type == "github"`:
   - `git clone` (or `gh repo clone`) into a scratch dir under
     `${TMPDIR:-/tmp}/audit-real-world-<plugin>-<timestamp>/`
   - Resolve the plugin root inside the clone (Layout A → repo root;
     Layout B → `plugins/<name>/`; Layout C → repo root with both
     manifests)
   - Run BOTH validators with output captured to disk
   - Append one row to a markdown table covering: plugin name, layout,
     CLI exit code, CPV exit code, CLI-only findings count, CPV-only
     findings count, both-flagged count
3. Produce a separate "ranked gaps" section listing each unique CLI-only
   finding category and how many real-world plugins it affects — that
   number is the best severity heuristic for which child TRDDs to ship
   first.

---

## 3. Phase 3 prerequisite checklist

When the Phase 3 agent picks this up, verify:

- [ ] `gh auth status` is logged in with at least repo:read scope
- [ ] `claude` CLI is on `$PATH` and `claude --version` reports v2.1.x
  or later (older versions have different output format the parser
  does not handle)
- [ ] `scripts/audit/cpv_vs_cli_diff.py` is unchanged from the Phase 1+2
  commit (or the parser has been extended to match)
- [ ] `~/.claude/rules/github-timeouts.md` retry pattern is wired into
  the bulk-clone loop (Fastweb's GitHub transit is currently flaky)
- [ ] Free disk space > 5 GB for the audit clone dir

---

## 4. Hand-off

Phase 3 child TRDD title (suggested):
**TRDD-{uuid} — Real-world coverage audit on Emasoft/ai-maestro-plugins**

Phase 3 child TRDD description (suggested):
> Complete the §4.3 audit of TRDD-b4c6cbe7 by running cpv_vs_cli_diff.py
> against every plugin in the Emasoft/ai-maestro-plugins marketplace.
> Produce `design/audits/real-world-failures-2026-05-11.md` with the
> finding matrix + ranked-gaps section. No source-code changes — this
> is the discovery wave that supplies the data for the Phase 4 wave
> (child-TRDD-per-gap implementation).

When this stub is replaced by the real report, leave a `git mv` trail
to make the deferral history searchable.
