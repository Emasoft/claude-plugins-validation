---
trdd-id: K7P2XR4Q
title: Shard CI test job into duration-balanced serial matrix + optimize the 60s parity test + fleet template
column: published
created: 2026-07-01T17:06:26+0200
updated: 2026-07-01T21:14:21+0200
current-owner: cpv-main-session
task-type: infra
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
audit-requirements: []
review-requirements: []
impacts: [ci-pipeline]
relevant-rules: []
external-refs: []
---

# TRDD-K7P2XR4Q — Shard CI test job into a duration-balanced serial matrix (+ parity-test optimization + fleet template)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-01

**✅ DONE — SHIPPED v2.151.0 (2026-07-01), CI GREEN.** Commits 52ca8d8 (impl) + 4ac96fd (docs); release green. **MEASURED: CI 7m37s→3m41s (~2.07×, −52%); Test job ~7min→~3m19s; Validate (3m38s) is now the CI bottleneck (next lever).** Shipped as COUNT-BASED split — committing pytest-split's 1.3MB `.test_durations` tripped CPV's own abs-path check (its test-ids contain `/etc/passwd`), so it was gitignored; count-based round-robin still distributes the heavy tests and its imbalance is harmless since Validate dominates the total. Everything below is the original design/plan, retained for history.

**Origin.** User: "7 minutes to run the tests? that is too much. is there some
way to run them in parallel?" Then chose scope = **CPV + fleet template** via
AskUserQuestion.

**Profile (measured this session, `pytest -n auto --durations=30`):**
- Parallel (xdist, local) = **82.8s** for 10417 tests; serial (CI) = **346s**.
- Cost is **highly concentrated**, NOT spread:
  - `#1 tests/test_skillaudit_integration_v2_104.py::TestParity::test_parity_old_vs_new_engine_on_cpv_scripts` = **60.2s** (~17% of serial). Compares OLD vs NEW skillaudit engine over the whole cpv scripts tree.
  - `#2..#~25` ≈ 15-21s each: `test_validate_scoring*`, `test_validate_security*`, `test_security_gate_banners`, `test_tirith_integration`, `test_*_parallelism` — heavy integration tests that run the full validator / security scan on fixtures.
  - The other ~10,000 tests are individually tiny (the whole tail is noise).

**Design (APPROVED approach — the fork was only scope):**
1. **Duration-balanced serial matrix shards** via `pytest-split`. CI `test` job
   becomes a matrix `group: [1..N]` (N=4 for CPV), each shard runs
   `uv run pytest tests/ --splits N --group K -v` **SERIALLY (no -n) and without
   google-re2** → the serial-pollution catch + the no-re2 ReDoS catch are
   PRESERVED WITHIN each shard. Balanced by a committed `.test_durations` so the
   60s parity test doesn't make one shard the bottleneck.
2. **Parity-test optimization** — parametrize
   `test_parity_old_vs_new_engine_on_cpv_scripts` **per-script** (one case per
   scanned file) so it distributes across shards and no single 60s case remains.
   Coverage IDENTICAL (old==new asserted on every script — just as N cases
   instead of 1 loop). REMOVES the single-test floor.
3. **Fleet template** — `scripts/generate_plugin_repo.py`'s ci.yml emitter gets
   the same matrix shard so every downstream plugin CPV scaffolds inherits it
   (user chose this scope). Downstream suites are small → count-based split (no
   committed `.test_durations` needed downstream; pytest-split degrades
   gracefully). Consider a smaller N (2) downstream to bound matrix overhead.

**THE INVARIANT TO PRESERVE (do NOT relax — this is the whole point):** each
shard runs **serial + no-re2**. The catch that CI's serial run exists for
(order-dependent serial-pollution + no-re2 catastrophic-backtracking) stays
alive WITHIN a shard. The ONLY thing a shard cannot catch is cross-shard
ordering pollution (a polluter in shard 1, victim in shard 3) — and that is
already covered by the **mandatory LOCAL full-serial pre-publish run** (the
authoritative ordering gate, run every publish per the CLAUDE.md invariant).
Document this tradeoff in a ci.yml comment AND the CLAUDE.md CI note so the
rationale is not lost.

**NEXT ACTION:** implement Phase 1 (CPV ci.yml matrix + pytest-split dep +
`.test_durations`) — but FIRST resolve the required-status-check derived task
(below), because a matrix changes the check name and could dangle the branch
ruleset's required check.

**Load-bearing facts / gotchas:**
- CPV CI test step today: `uv run pytest tests/ -v` (`.github/workflows/ci.yml`
  ~line 93, job "Test", `timeout-minutes: 15`).
- Template emitter: `scripts/generate_plugin_repo.py` ~line 1229 emits
  `uv run pytest tests/ -v` for downstream plugins.
- `publish.py` Gate 2 runs `pytest -n auto` LOCALLY (already parallel/fast) — do
  NOT need to change it. The CLAUDE.md "full serial suite before publish"
  invariant is a MANUAL local run; it stays the authoritative cross-shard gate.
- **Required-status-check derived task (BLOCKER — resolve first):** the branch
  ruleset `baseline-pr-and-checks` has `required_status_checks` (auto-detected job
  ids). A matrix produces N check legs ("Test (1)"…), so the old required check
  "Test" would dangle. Add a stable **aggregate gate job** (e.g. `ci-tests` that
  `needs: [test]`) as the single required check, OR update the ruleset's required
  checks to the matrix legs. VERIFY the current required-check config
  (`gh api repos/Emasoft/claude-plugins-validation/rulesets`) before changing the
  job names. `publish.py` uses admin direct-push (bypass) so it is not itself
  blocked, but a dangling required check is bad hygiene and blocks PR merges.
- `pytest-split` is deterministic given the same test set + durations; new tests
  without a duration entry get an estimated slot (graceful, never broken).

**SUPERSEDED — do NOT carry forward:** nothing yet (new TRDD).

**Durable artifacts to read before acting:**
- This TRDD.
- The profile: re-run `uv run pytest -n auto -o addopts="" -q --durations=30 tests/` if `.test_durations` needs regenerating.

## Plan (phases; ≤5 files each; verify between)

### Phase 1 — CPV's own CI matrix (prove it here first)
- Add `pytest-split` to the test dependency group (pyproject / the CI install step).
- Generate + commit `.test_durations` (from a serial durations run so the balance
  reflects CI's serial timings, not xdist's contended ones).
- Rewrite `.github/workflows/ci.yml` `test` job → matrix `group: [1,2,3,4]`, each
  `uv run pytest tests/ --splits 4 --group ${{ matrix.group }} -v` (serial). Add
  the **aggregate gate job** with a stable name for the required status check.
- Update any pinning test that asserts ci.yml content (grep first).
- Resolve the required-status-check config so the aggregate job is the required
  check (verify current ruleset first).

### Phase 2 — parity-test optimization
- Read `tests/test_skillaudit_integration_v2_104.py::TestParity`; parametrize the
  old-vs-new comparison per-script (identical coverage), removing the 60s floor.
- Two-sided sanity: the parametrized cases still assert old==new on every script;
  a deliberately-divergent stub would still fail (coverage preserved).

### Phase 3 — fleet template
- Update `scripts/generate_plugin_repo.py`'s ci.yml emitter → same matrix (N=2 for
  small downstream suites; count-based split, no committed durations required).
- Add/refresh the template pinning tests to match the new emitted ci.yml.

### Phase 4 — docs + verify + publish
- Update the CLAUDE.md CI invariant note (CI is now sharded-serial; the local
  full-serial pre-publish run is the authoritative cross-shard ordering gate) +
  version-history + README if it describes CI.
- Regen self-hashes LAST.
- ruff + mypy + cache-cold self-validate 0/0/0/0 + full LOCAL serial suite green.
- `publish.py --minor` → watch the SHARDED CI go green (proves the matrix works)
  → measure wall-clock before/after (gate on the measured ratio, per the
  profile-before-parallelizing lesson: if the matrix doesn't move wall-clock, cut
  it).

## Derived tasks / risks
- **R (blocker):** required-status-check dangle on the matrix rename → aggregate
  gate job + ruleset verify (Phase 1). Preserve the SAME protection level (no gate
  relaxed).
- **R:** `.test_durations` staleness as tests grow → imbalance (graceful). Optional
  CI step to refresh it, or accept periodic manual refresh.
- **R:** downstream small-suite matrix overhead → N=2 + count-based split.
- **R:** matrix adds per-shard setup cost (checkout+uv+deps × N). Net win only if
  test time dominates setup — it does (setup ~1-2 min, tests 5-6 min), but keep N
  modest (4) so N×setup doesn't erode the gain. This is the measured-ratio gate.

## Out of scope
- Changing `publish.py`'s local `pytest -n auto` gate (already fast).
- Relaxing the serial/no-re2 invariant (the shards preserve it).
