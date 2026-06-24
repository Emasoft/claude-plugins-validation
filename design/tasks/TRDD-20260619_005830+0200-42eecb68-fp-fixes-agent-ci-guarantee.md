---
trdd-id: 42eecb68-e6ca-4654-beb3-aed4e7f35e92
title: Fix #133/#134/#135 scanner FPs + harden ALL fix/upgrade/doctor/devitalizer agents to loop-until-0/0/0/0-and-CI-green + guarantee canonical-pipeline upgrade passes GitHub CI
column: published
created: 2026-06-19T00:58:30+0200
updated: 2026-06-24T03:27:35+0200
current-owner: claude-plugins-validation
task-type: bugfix
relevant-rules: []
release-via: publish
test-requirements: [unit, lint, typecheck]
---

# Fix scanner FPs + agent CI-green guarantee (user ultracode mandate, 2026-06-19)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative)

User mandate (ultracode, 2026-06-19): "complete all tasks; delegate to opus agents or
use ultracode workflows tailored from ~/.claude/commands/workflow-verified-scan-and-fix.md;
fix ALL issues; ensure ALL cpv agents + doctors have explicit instructions in their .md to
ITERATE UNTIL the plugin passes the scan with 0/0/0/0 AND GitHub CI/CD passes without
failures; ensure the upgrade agent + devitalizer agents GUARANTEE plugin refactoring / canonical
publish-pipeline upgrade/creation at 100% (no bugs/errors). User STILL receives tons of
notifications of plugins FAILING GitHub CI AFTER the agents upgraded their publish pipeline."

### NEXT ACTION
Phase 1 — fix the 3 scanner-FP issues, then ship. Then Phase 2 (agent hardening), Phase 3
(canonical-pipeline CI-green guarantee), Phase 4 (verify + ship + memory).

### Load-bearing facts
- amvcp/PSS/integrator/cos/ai-maestro-plugin are OTHER projects — READ-ONLY, validate only.
- Security-detector changes: Opus for analysis; FN-safe two-sided; CENTRAL-adversarial-verify
  through the REAL scanner with malicious siblings the agent did NOT enumerate (the recurring
  "delegated FP-fix clears the FP but quietly opens an FN hole" lesson). READ THE DIFF line-by-line.
- NO rule suppression / NO --strict relax / NO allowlist. Detector-precision only.
- Publishing ONLY via publish.py (pre-push hook blocks other pushes). Agents NEVER git/build —
  the orchestrator commits + publishes.
- Self-validate after editing ANY tracked file: regen hashes (`_plugin_compute_hashes.py`) then
  `remote_validation.py plugin . --strict` with PLUGIN_SKIP_GITHUB_INTEGRITY=1.

## Phase 1 — fix the 3 scanner-FP issues (detection precision, FN-safe two-sided)

| # | Title | File(s) | Fix |
|---|---|---|---|
| #133 | execution-class rules NIT-flag shell/Python EXAMPLES inside markdown `.md` reference docs (safe-shaped `subprocess.run([list])` in fenced blocks, security-checklist prose, hook snippets); #81 fix missed `.md` code blocks | `scripts/_skillaudit_markdown_context.py` | extend doc-context suppression to `.md`-fenced code blocks for safe-shaped exec (literal-arg `subprocess.run([list])`, no `shell=True`) + prose/checklist lines; a REAL executable threat in a ```bash``` fence / `shell=True` still fires |
| #134 | `PROTOTYPE_POLLUTION` (JS rule) FP on Python `list.extend([…"payload"…])` — pattern #6 matches `extend(` + substring `payload` | `scripts/rules/skillaudit_patterns.json` (+ classifier language-gate) | language-gate the JS proto-pollution patterns (skip `.py`/`.rb`/`.go`/…) OR require a JS proto indicator (`__proto__`/`constructor`/`Object.`/`Reflect.`/`req.`); a real JS `Object.assign(t, req.body)` / lodash `_.merge(req.body)` still fires |
| #135 | `CMD_INJECTION` FP on `curl <url>` inside markdown `<!-- -->` comments + inline prose code-spans | `scripts/_skillaudit_markdown_context.py` | skip command-shapes inside `<!-- -->` comment regions + inline code-spans/prose; a real `curl … \| sh` in a ```bash``` fence / script line still fires |

Issues #133 + #135 share `_skillaudit_markdown_context.py` (ONE agent, no parallel conflict); #134 is
disjoint (`skillaudit_patterns.json` + classifier). Two implementer agents (parallel) + two
verifier agents; then ORCHESTRATOR central-probe + ship.

## Phase 2 — agent hardening (loop-until-0/0/0/0 AND CI-green in every relevant agent .md)

Have it today: plugin-fixer, marketplace-fixer, cache-optimizer-agent. ADD/strengthen in:
plugin-devitalizer, plugin-leaks-preventer, plugin-diagnoser, cpv-doctor-agent, plugin-creator,
cpv. Plus the knowledge skills: standardize-plugin, canonical-pipeline. The instruction:
"iterate (loop) until the target plugin's validate scan is 0 CRITICAL / 0 MAJOR / 0 MINOR /
0 NIT AND the plugin's GitHub CI/CD passes with zero failures; on a red CI read the failing
job, fix the CAUSE, re-publish, re-watch; bounded only by oscillation (cpv_fix_loop_state.py),
never a hardcoded cap; never mute a check / relax --strict." Disjoint per-file units →
verified-implement workflow.

## Phase 3 — canonical-pipeline CI-GREEN GUARANTEE (the user's core pain)

"Tons of plugins fail GitHub CI AFTER the agents upgraded their publish pipeline." Two parts:
1. TEMPLATE CORRECTNESS — investigate `generate_plugin_repo.py`, `setup_plugin_pipeline.py`,
   the `canonical-pipeline` skill workflow templates: enumerate the failure modes that make a
   GENERATED `.github/workflows/*.yml` + `publish.py` fail on the target plugin's GitHub
   Actions, and fix the templates (per gh-actions best-practices: pinned actions, real
   timeout-minutes, correct job-id required-checks, cold-install ceilings, least-priv perms,
   matrix correctness, no duplicated action-internal flags).
2. AGENT BEHAVIOR — the upgrade/creator flow (plugin-fixer canonical mode + plugin-creator +
   standardize-plugin/canonical-pipeline skills) MUST, after generating the pipeline, RUN it on
   the target plugin's GitHub and LOOP UNTIL CI IS GREEN (read failing job → fix cause →
   re-push via publish.py → re-watch) before declaring success — never "generated, done".

## Phase 4 — verify + ship + memory

Full serial + xdist 9630+ green; self-validate --strict VALID 0/0/0/0; publish.py --minor;
CI+Release+Notify green; comment #133/#134/#135 self-id'd; update CLAUDE.md inventory + MEMORY.md.

## REMAINING STEPS
1. ✅ DONE — Phase 1 — FP fixes (#133/#134/#135): 2 opus agents (one rate-limit-died at reporting → recovered from disk) + CENTRAL-VERIFIED 7/7 two-sided through the real scanner. **Shipped v2.133.0** (CI+Release+Notify green); all 3 issues closed self-id'd.
2. ✅ DONE — Phase 2 — agent hardening: loop-until-0/0/0/0-AND-CI-green added to plugin-devitalizer/plugin-leaks-preventer/plugin-diagnoser/cpv-doctor-agent/cpv. Committed `8dec604`.
3. ✅ DONE — Phase 3 — canonical-pipeline CI-green guarantee: opus recon (11 failure modes, real run IDs) → 2 impl agents fixed the generator (pin CPV ref / skip-env / aggregate-`Test` gate / timeouts / notify-guard) + plugin-creator watch-CI-green + canonical/standardize skill docs. CENTRAL-VERIFIED by scaffolding a real sample plugin (actionlint exit 0, all 5 fixes present, branch contexts consistent). +20 tests; 627 gen/pipeline suites green.
4. SHIPPING — Phase 4 — publish v2.134.0 (bundles Phase 2 + Phase 3) → CI green → comment #128/#115 → update MEMORY.md.
