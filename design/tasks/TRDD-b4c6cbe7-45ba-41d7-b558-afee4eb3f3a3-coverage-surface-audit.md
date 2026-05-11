# TRDD-b4c6cbe7 — Comprehensive Coverage-Surface Audit: CPV ≥ Claude CLI

**TRDD ID:** `b4c6cbe7-45ba-41d7-b558-afee4eb3f3a3`
**Filename:** `design/tasks/TRDD-b4c6cbe7-45ba-41d7-b558-afee4eb3f3a3-coverage-surface-audit.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Phase 1+2 done (2026-05-11) — audit infrastructure shipped + 3 of 4 reports produced; real-world-failures deferred to Phase 3 pending network access
**Author:** Emanuele (orchestrator captured 2026-05-11)
**Created:** 2026-05-11
**Priority:** **CRITICAL — supersedes all incremental validator work until coverage parity is established**

---

## 1. User's request (verbatim)

> the doctor should be the last resort. the agents should be always producing a valid plugin and a valid marketplace. this is the minimum you expect from the mighty validator plugin. Instead not only the plugins and the marketplaces are so full of flaws that even the stupid validate command of claude cli flags them as invalid! Clearly your surface of control of the plugin structure is way smaller than it should be. The agents are missing huge surfaces areas both in creation and validation phases. All those holes must be found, and skills to handle them (both in creation/fix and validation) must be written.

---

## 2. Problem statement (single sentence)

CPV ships as "the comprehensive validator for Claude Code plugins" but its validation surface is **demonstrably smaller** than Claude CLI's own `claude plugin validate` command — and its creation agents (`plugin-creator`, `plugin-fixer`, `marketplace-fixer`, `cpv-upgrade-plugin`, `cpv-migrate-marketplace`) emit output that fails Claude CLI's basic check, requiring the user to run the opus-expensive `/cpv-doctor` to discover the failures.

This violates CPV's core promise: *the doctor should be a last resort, not the only line of defense against an authoring flow that systematically produces broken output.*

---

## 3. Invariants this audit will establish + maintain

| # | Invariant | How enforced |
|---|---|---|
| INV-1 | For every Claude CLI validation finding, CPV emits an equivalent or stronger finding | Architectural-discipline test that diffs CPV findings against CLI findings on N golden plugins |
| INV-2 | CPV does not emit false positives where CLI accepts | Same architectural test, inverted (CPV finding count ≤ CLI plus CPV-extension count) |
| INV-3 | Every CPV creation agent emits output that passes BOTH `claude plugin validate` AND `validate_plugin.py --strict --cross-validate-upstream` on the first try | End-to-end scenario test per agent |
| INV-4 | Every Claude Code spec section has at least one CPV check that asserts it | Spec-coverage matrix (§5) ratchet test |
| INV-5 | Doctor is opt-in, not the discovery layer — its findings exist purely to surface NEW spec changes that haven't propagated into validator + agent yet | Doctor's CHANGELOG entries must be RARE; if they fire on a non-novel error class, it's a regression on INV-1 |

---

## 4. Audit methodology

### 4.1 Phase 1 — discover Claude CLI's validation surface

**Inputs:**
- Claude CLI source / behavior (via `claude plugin validate --help` and empirical probing)
- Claude Code plugin spec docs (`https://code.claude.com/docs/llms.txt` index → every linked spec file)
- The 6 known incident reports (PIT-001..PIT-007 in TRDD-962fdc55)

**Procedure:**
1. Build a fixture-grid of ~30 plugins covering every known spec field, source type, layout, hook event, MCP shape, agent frontmatter variation, skill variation.
2. For each fixture, run BOTH `claude plugin validate <fixture>` AND `uv run python scripts/validate_plugin.py <fixture> --strict`.
3. Diff the findings → produces matrix `cli_only`, `cpv_only`, `both`.
4. `cli_only` rows are CPV gaps (INV-1 violations).
5. `cpv_only` rows are either CPV-extensions (intentional) OR false-positives.

**Output:** `design/audits/coverage-surface-2026-05-11.md` with the diff matrix.

### 4.2 Phase 2 — discover spec sections without coverage

**Procedure:**
1. Extract every concrete rule from the Claude Code plugin spec docs (every "MUST", "SHOULD", "MUST NOT", every example, every error message).
2. Map each rule to either:
   - An existing CPV check (point at the regex/function/severity)
   - A "missing check" gap
3. Output a spec-coverage matrix.

**Output:** `design/audits/spec-coverage-2026-05-11.md`.

### 4.3 Phase 3 — discover real-world failure modes

**Procedure:**
1. Enumerate every plugin currently listed in `Emasoft/ai-maestro-plugins` (and any other accessible marketplace).
2. Run BOTH `claude plugin validate` and `cpv-validate` against each.
3. Catalogue every finding, especially the ones where CPV emits zero but CLI emits a finding.

**Output:** `design/audits/real-world-failures-2026-05-11.md`.

### 4.4 Phase 4 — agent emission audit

**Procedure:**
1. Run a controlled experiment: ask each in-scope agent (`plugin-creator`, `plugin-fixer`, `marketplace-fixer`, `cpv-upgrade-plugin`, `cpv-migrate-marketplace`) to create or fix a plugin under specified conditions.
2. Capture the emitted output before any post-emit validator gate.
3. Run BOTH `claude plugin validate` and `cpv-validate` against the raw output.
4. Catalogue every failure mode produced by each agent.

**Output:** `design/audits/agent-emission-2026-05-11.md`.

---

## 5. Spec-section coverage matrix (initial — to be expanded by audit)

This is the "ratcheting" matrix Phase 2 will fill in. Initial rows from a quick pass:

| Spec section | Rule | CPV current check? | Gap? |
|---|---|---|---|
| plugin.json `name` | MUST be unique kebab-case, no underscores, ≤ 50 chars | partial (regex check exists, length check unclear) | **POSSIBLE GAP** |
| plugin.json `version` | MUST be valid semver (X.Y.Z) | yes | clean |
| plugin.json `description` | RECOMMENDED ≤ 250 chars for marketplace display | unclear | **POSSIBLE GAP** |
| plugin.json `dependencies` | object, keys ARE plugin names with semver-range values | unclear | **GAP — recently shipped TRDD-20108ab7 covers some** |
| plugin.json `commands[].name` | MUST not conflict with built-in slash commands | yes (Phase 15 v2.31.0) | clean |
| plugin.json `agents[].frontmatter` | MUST validate against agent-frontmatter spec (15 fields per v2.17.0) | yes | clean |
| plugin.json `skills[].frontmatter` | MUST validate (15 fields per v2.32.0) | yes | clean |
| plugin.json `hooks[]` | 28 events × 5 hook types | yes (Phase 12 v2.28.0) | clean |
| plugin.json `mcpServers` | per-server fields | partial | **POSSIBLE GAP** |
| plugin.json `lspServers` | LSP-server shape | partial | **GAP** |
| plugin.json `monitors` | v2.1.105 background monitors | partial (frontmatter exists; runtime semantics unclear) | **POSSIBLE GAP** |
| plugin.json `outputStyle` | output style declaration | unclear | **GAP** |
| marketplace.json `name` | unique kebab-case | yes | clean |
| marketplace.json `plugins[].name` | MUST EQUAL upstream plugin.json.name | NO — being added by TRDD-c0ee9543 | gap (in flight) |
| marketplace.json `plugins[].source` | per-source-type allowlist | partial — TRDD-c0ee9543 hardens it | gap (in flight) |
| marketplace.json `category` | enum or freeform? | unclear | **POSSIBLE GAP** |
| marketplace.json `tags` | array of strings | unclear | **POSSIBLE GAP** |
| marketplace.json `claude_versions` | semver range | unclear | **POSSIBLE GAP** |
| marketplace.json `platforms` | enum: darwin, linux, windows | unclear | **POSSIBLE GAP** |
| hook script paths | MUST resolve to a file in the plugin tree | yes | clean |
| hook script lint | MUST pass per-language lint | yes | clean |
| skill `disable-model-invocation` | boolean | yes | clean |
| skill `allowed-tools` | array of valid tool names (39 valid as of v2.1.109) | yes (v2.17.0) | clean |
| skill `arguments` | declared list for $<name> substitution (v2.1.121) | yes (Phase 12 v2.28.0) | clean |
| `.claude-plugin/settings.json` | strictKnownMarketplaces shape | yes (Phase 16 v2.32.0 + TRDD-e2b17a61 v2.80.0) | clean |
| `.claude-plugin/env.example` | dotenv shape, no secrets | partial | **POSSIBLE GAP** |
| `.claude-plugin/.gitignore` | should exclude `.env`, `.venv`, `node_modules`, etc. | partial | **POSSIBLE GAP** |
| README.md | MUST contain install command using the canonical name | unclear | **POSSIBLE GAP** |
| CHANGELOG.md | format checks | unclear | **POSSIBLE GAP** |
| LICENSE | presence | unclear | **POSSIBLE GAP** |

Rows marked **POSSIBLE GAP** / **GAP** become child TRDDs after Phase 1-4 audits confirm them.

---

## 6. Phasing

### Phase 1 — Audit infrastructure (no production code change)

**Files to create:**
- `scripts/audit/cpv_vs_cli_diff.py` — runs both validators against a fixture set, diffs findings
- `scripts/audit/spec_rule_extractor.py` — fetches Claude Code spec pages, extracts rules
- `scripts/audit/fixture_grid_generator.py` — generates ~30 fixture plugins
- `tests/audit/fixtures/grid/` — the 30 fixtures (each in its own subdir)
- `design/audits/` — new directory for audit outputs

**Tests:**
- `tests/test_audit_infrastructure.py` — sanity that the audit harness runs

### Phase 2 — Run the audit, write the four audit reports

Output the 4 matrices from §4.1–§4.4. Each is a checked-in markdown file under `design/audits/`.

### Phase 3 — Triage findings → spawn child TRDDs

For every confirmed gap, spawn a child TRDD that captures:
- The gap (severity, scope, repro)
- The validator-side fix (new check or rule)
- The creator-side fix (skill content or agent instructions)
- The fix-validation recipe

Expected child TRDD count: 10-25 based on initial gap-list above.

### Phase 4 — Implement child TRDDs in waves

Each child TRDD becomes its own implementation wave (similar to Wave 1-7 pattern from TRDD-82e836dc onwards). Parallel where files don't overlap.

### Phase 5 — Ratchet via architectural-discipline tests

`tests/test_coverage_surface_invariants.py` — for INV-1..INV-5:

```python
def test_inv1_cpv_finds_everything_cli_finds():
    """For every plugin in tests/audit/fixtures/grid/, every CLI finding
    must have a corresponding CPV finding."""
    for fixture in FIXTURE_GRID:
        cli_findings = run_claude_cli_validate(fixture)
        cpv_findings = run_cpv_validate(fixture)
        for cli_finding in cli_findings:
            assert any(matches(cli_finding, cpv_finding)
                       for cpv_finding in cpv_findings), \
                f"INV-1 violation: {fixture} — CLI flagged but CPV did not: {cli_finding}"

def test_inv3_each_agent_emits_cli_valid_output():
    """plugin-creator, plugin-fixer, marketplace-fixer, cpv-upgrade-plugin,
    cpv-migrate-marketplace — each must emit output that passes
    `claude plugin validate` on the first try."""
    AGENTS = ["plugin-creator", "plugin-fixer", "marketplace-fixer",
              "cpv-upgrade-plugin", "cpv-migrate-marketplace"]
    for agent in AGENTS:
        output_path = invoke_agent_with_canned_input(agent)
        rc = subprocess.run(["claude", "plugin", "validate", output_path]).returncode
        assert rc == 0, f"INV-3 violation: {agent} emitted CLI-invalid output"
```

### Phase 6 — Continuous ratchet

After all child TRDDs land, add a CI gate that runs the invariant tests on every PR. Any regression fails the build.

---

## 7. Concrete first-pass child TRDD candidates

Based on the initial gap list in §5, expect the audit to surface (at minimum):

1. **plugin.json `name` length + character check** — CLI may enforce kebab-case + length; CPV's regex may be looser
2. **plugin.json `description` length recommendation** — marketplace display truncates beyond N chars
3. **plugin.json `outputStyle` declaration shape** — likely uncovered
4. **plugin.json `lspServers` validation** — partial coverage; likely incomplete vs CLI
5. **plugin.json `monitors` runtime semantics** — frontmatter accepted but runtime semantics unchecked
6. **mcpServers schema strictness** — per-server fields likely loose
7. **marketplace.json `category` enum** — if CLI enforces an enum, CPV must too
8. **marketplace.json `tags` shape** — array-of-strings vs nested array
9. **marketplace.json `claude_versions` semver-range** — strict vs loose semver parsing
10. **marketplace.json `platforms` enum** — `darwin`/`linux`/`windows` vs free string
11. **.claude-plugin/env.example secret scan** — must not embed real values
12. **.claude-plugin/.gitignore minimum patterns** — `.env`, `node_modules`, etc.
13. **README install-command shape** — must use canonical name from plugin.json
14. **CHANGELOG.md format** — keepachangelog or git-cliff-compatible
15. **LICENSE presence + recognised SPDX identifier**
16. **plugin.json `dependencies` semver-range parsing strictness**
17. **plugin.json `commands[].name` collision check vs SYSTEM slash commands** (existing) AND vs CROSS-PLUGIN names (likely uncovered)
18. **Agent frontmatter `permissionMode` enum strictness** — limited set of valid modes
19. **Skill `paths` field shape** — array of relative paths
20. **Hook script PEP 723 metadata + uv compatibility** — partial coverage post-TRDD-0028dd34
21. **MCP server `args` array — paths inside `args` must resolve** — uncovered

Each becomes a child TRDD after the audit confirms it.

---

## 8. Critical files (Phase 1-2 only — child TRDDs add more)

| Path | Phase | Action |
|---|---|---|
| `scripts/audit/cpv_vs_cli_diff.py` | 1 | NEW |
| `scripts/audit/spec_rule_extractor.py` | 1 | NEW |
| `scripts/audit/fixture_grid_generator.py` | 1 | NEW |
| `tests/audit/fixtures/grid/` (30 subdirs) | 1 | NEW |
| `tests/test_audit_infrastructure.py` | 1 | NEW |
| `design/audits/coverage-surface-2026-05-11.md` | 2 | NEW |
| `design/audits/spec-coverage-2026-05-11.md` | 2 | NEW |
| `design/audits/real-world-failures-2026-05-11.md` | 2 | NEW |
| `design/audits/agent-emission-2026-05-11.md` | 2 | NEW |
| `tests/test_coverage_surface_invariants.py` | 5 | NEW |

---

## 9. Acceptance criteria for the META TRDD

The META TRDD is **Done** when:

- [ ] Phase 1 audit infrastructure ships + tests pass
- [ ] Phase 2 four audit reports checked into `design/audits/`
- [ ] Every confirmed gap has a corresponding child TRDD in `design/tasks/`
- [ ] Phase 5 invariant tests ratchet the four invariants
- [ ] After all child TRDDs land: `test_inv1_cpv_finds_everything_cli_finds` and `test_inv3_each_agent_emits_cli_valid_output` are GREEN
- [ ] `/cpv-doctor` runs against representative fixtures and finds **zero new issues** beyond what the validator already surfaced (doctor becomes safety-net, not discovery layer)
- [ ] This TRDD's `**Status:**` line set to `Done (YYYY-MM-DD) — audit complete; N child TRDDs spawned; INVs 1-5 ratcheted`

---

## 10. Risks + mitigations

| Risk | Mitigation |
|---|---|
| `claude plugin validate` requires network to function or is unavailable in CI | Phase 1 invokes it with `--offline` if supported; if not, fall back to a captured-output golden file refreshed by a separate online job |
| 30-fixture grid is too large to maintain | Generate fixtures programmatically; each one is ≤ 30 lines |
| Child TRDDs proliferate beyond practical implementation rate | Triage: each child TRDD has a severity (CRITICAL/MAJOR/MINOR/NIT); critical-only first wave, others tracked in backlog |
| Audit reveals CPV has many false positives (CPV flags what CLI accepts) | Tag those as "CPV-extensions" with explicit justification; if no justification, fix or remove |
| Coverage parity becomes a moving target as Claude Code spec evolves | Phase 6 CI ratchet runs on every PR — drift surfaces immediately |
| Agent emission audit (Phase 4) requires running each agent against canned inputs and inspecting raw output BEFORE the post-emit gate fires | Mock the gate in the harness; or capture-and-discard via subprocess with `CPV_DRY_RUN=1` env var |

---

## 11. Dependencies + ordering

```
TRDD-c0ee9543 (validator cross-validation) ── Wave 6 ┐
TRDD-962fdc55 (proactive auth contract)   ── Wave 7 ─┤
TRDD-b4c6cbe7 (this meta TRDD)            ── Wave 8 ─┴── child TRDDs (Waves 9+)
```

This TRDD's Phase 1+2 (audit infrastructure + reports) can start IN PARALLEL with Wave 6/7 since it touches only new files under `scripts/audit/` and `design/audits/`. Phases 3+ (child TRDDs implementing the gaps) WAIT for Wave 6/7 to land so they have the validator + contract as foundation.

---

## 12. Out of scope

- Rewriting Claude CLI's validator (Anthropic's responsibility)
- Adding NEW Claude Code spec features beyond what already exists
- Auto-publishing fixes upstream
- Building a UI for the audit dashboard (CLI + markdown reports suffice)

---

## 13. References

- 2026-05-11 user escalation (§1 verbatim)
- TRDD-c0ee9543 — validator-side reactive gap closure (prerequisite)
- TRDD-962fdc55 — proactive authoring contract (prerequisite)
- Claude Code spec — `https://code.claude.com/docs/llms.txt` index
- v2.32.0 Phase 12-16 shipped the bulk of the existing CPV check surface — audit will inventory it

---

## 14. Note on user's "agents missing huge surface areas"

The user's claim is empirically testable: Phase 4 (agent emission audit) WILL measure it. If the audit finds that each agent invocation produces N errors on average where Claude CLI catches them, that's the quantitative answer.

If N is small (≤ 1 on average), the user's "huge surface areas" claim is overstated and TRDD-962fdc55's contract suffices.

If N is large (e.g. 5+ errors per agent invocation on average), then the agents need deep restructuring beyond the contract — likely a per-agent rewrite informed by the audit findings, captured in child TRDDs.

Either way, the audit gives us the data to decide instead of guessing.
