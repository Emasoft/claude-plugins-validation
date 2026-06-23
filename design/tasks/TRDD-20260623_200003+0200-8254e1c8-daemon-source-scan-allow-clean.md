---
trdd-id: 8254e1c8-7864-4be9-8b1b-82ddf81c83ed
title: Daemon-installer source-scan — allow provably-clean+non-exploitable boot daemons, keep blocking malicious/exploitable ones; + clear all open issues #147-#151
column: complete
created: 2026-06-23T20:00:03+0200
updated: 2026-06-23T21:00:47+0200
current-owner: cpv-main
assignee: cpv-main
priority: 1
severity: HIGH
effort: XL
labels: [security, scanner, persistence, daemon, false-positive, canon, publish]
task-type: security
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
must-pass-tests-before-merge: true
publish-target: claude-plugins-validation
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan, adversarial-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: [public-api, ci-pipeline]
attempts: 0
last-test-result: not-run
implementation-commits: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/147", "github.com/Emasoft/claude-plugins-validation/issues/148", "github.com/Emasoft/claude-plugins-validation/issues/149", "github.com/Emasoft/claude-plugins-validation/issues/150", "github.com/Emasoft/claude-plugins-validation/issues/151", "github.com/Emasoft/claude-plugins-validation/issues/61", "github.com/Emasoft/claude-plugins-validation/issues/63"]
---

# TRDD-8254e1c8 — Daemon-installer source-scan (allow clean+non-exploitable boot daemons) + clear all open issues

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-06-23

**User directive (verbatim intent):** "all issues must be solved before shipping!
read the open issues on github. there are many problems with the installers of
boot loaded daemons, but the solution is simple: do not forbid the installation
if the daemon source is available and you can scan it to ensure there is no
malicious code or data exfiltration code. If the daemon script is clean, the cpv
plugin must recognize it as such and allows the install (but beware of scripts
that can be clean but exploitable, like scripts loading other scripts dynamically
or accepting/listening to inputs that can make them a door to malicious code
execution)."

**Two work-streams, both required before ship:**
1. **PRIMARY — daemon-installer source-scan discriminator** (this is the new
   feature). Overrides the #63 won't-fix premise.
2. **All open issues #147-#151** must be cleared.

### Current state — IMPLEMENTATION COMPLETE + CENTRAL-VERIFIED (pre-ship)
- PRIMARY discriminator: SHIPPED in `scripts/cpv_persistence_target.py` (the four
  ALLOW conditions C1-C4 + bounded launcher chain), wired into BOTH detector
  paths (`_skillaudit_shell_context.py` lazy-import branch + `validate_security.py`
  RC-39 guard) via the one shared helper. Design at
  `reports/daemon-scan-design/20260623_200620+0200-*.md`, impl report at
  `reports/daemon-scan-impl/20260623_203011+0200-*.md`.
- CENTRAL-ADVERSARIAL VERIFY (this session) found + FIXED one real FN hole the
  impl agent's 39 tests missed: `_is_thin_launcher_target` followed only the
  FIRST launch token, so a thin launcher that exec'd TWO in-tree scripts
  (`python ./clean.py` then `python ./evil.py`) had only `clean.py` scanned →
  a clean-first/evil-second launcher CLEARED. Replaced it with `_launch_targets`
  (collect+follow EVERY target via finditer, NO cap — a cap would re-open the
  hole) and rewrote `_target_chain_passes` with a diamond-safe `proven` memo +
  `on_path` cycle guard (so a file reached by two launch paths verifies once and
  does not falsely trip the cycle guard). Removed dead `_depth` param. +4
  two-sided tests in `tests/test_persistence_daemon_scan.py` (multi-exec
  evil-second NEGATIVE, both-clean POSITIVE, single-line two-launch NEGATIVE,
  diamond POSITIVE). All 43 daemon tests + 75 cross-path + 72 RC-39 regressions
  green; ruff + mypy clean; re2-safe (added no new regex).
- SELF-SCAN-CLEAN: the cache-cold `--strict` self-validate first flagged
  `cpv_persistence_target.py:564` (the analyzer's own `@reboot`/`crontab`
  dispatch needles tripping the PERSISTENCE rule — scanning-the-scanner). Fixed
  by moving ALL mechanism needles into a recognized `_MECHANISM_TOKENS`
  pattern-source collection (the dispatch `if`s now reference the table, no
  inline literals) so CPV's self-scan reads them as DATA. This is NOT a rule
  suppression — the P-1 `is_pattern_source_line` skip is gated to CPV's OWN
  hash-pinned source via the non-spoofable `_CPV_IS_RUNNING_CPV`, so a
  third-party plugin's real persistence install still BLOCKS. Re-validated
  cache-cold → 0/0/0/0.
- #148-#151: all four code fixes landed by file-disjoint opus agents and
  re-verified green this session (targeted suites: #148 13, #149/#151 15, #150 12,
  standardize 12 = 52 pass). #147: coordination Q — answer AFTER ship =
  "publish against v2.145.0".

### NEXT ACTION (ship sequence)
1. Await the full serial-suite run (arbiter, in flight) → confirm green.
2. Write the v2.145.0 version-history entry in CLAUDE.md (counts already bumped
   to scripts 121 / test files 370 / ~10155 tests).
3. Regen self-hashes LAST (`_plugin_compute_hashes.py`) — after ALL md/TRDD edits.
4. Cache-cold self-validate `--strict` → 0/0/0/0.
5. Commit per concern, then `publish.py --minor` → v2.145.0, watch CI green.
6. Close #148-#151 + #63 (intrinsic-discriminator overrides the won't-fix) and
   answer #147 — each comment led by the self-id line.

### Load-bearing facts / gotchas
- **TWO persistence code paths**, BOTH must get the discriminator:
  - skillaudit `PERSISTENCE` rule (rule[24] in `scripts/rules/skillaudit_patterns.json`,
    severity CRITICAL). Detection + suppression in
    `scripts/_skillaudit_shell_context.py`: `_is_launchagent_removal` (L387), applied
    at L1299 (`if rule_id == "PERSISTENCE" and _is_launchagent_removal(line_text)`).
    Also `scripts/cpv_skillaudit_native.py` (PERSISTENCE at L775/1013/1058/1112).
  - `validate_security.py` `RC-39` persistence (`PERSISTENCE_PATTERNS`, applied
    ~L9230-9245).
- **#61 (FIXED v2.112.0)** already added the *removal* intrinsic discriminator
  (`rm`/`unlink`/`launchctl bootout` of a plist → suppressed). The new work adds
  the *install* discriminator (resolve launched program → scan clean →
  non-exploitable → allow).
- **#63 (WON'T-FIX) rationale being overridden:** #63 refused a SELF-DECLARED
  suppression (inline `cpv:allow`, `.cpv-allow.yaml`, `--baseline`) because a
  malicious plugin could annotate real payload as intentional. The new discriminator
  is **INTRINSIC** (computed from scanning the launched script's AST/source), NOT
  self-declaration — so it does NOT reopen the #63 bypass hole. This is the exact
  "CPV recognizes FPs intrinsically, never by self-declaration" invariant, applied
  to the launched-program contents.
- **NEVER relax the gate.** The discriminator must be FN-safe: a real
  persistence-malware (exfil / dynamic-loader / input-RCE) MUST still block. Every
  test is two-sided (clean+inert daemon → ALLOWED; malicious/exfil/dynamic-load/
  input-listen daemon → BLOCKED).

### SUPERSEDED — do NOT carry forward
- (none yet)

### Durable artifacts to read before acting
- `reports/daemon-scan-design/<ts>-*.md` — the opus technical design (pending).
- Closed issues #61 (removal discriminator, the pattern to mirror) and #63
  (the won't-fix being overridden) — full bodies fetched 2026-06-23.

## Design — the daemon-source-scan discriminator (ALLOW conditions)

A persistence-install finding (skillaudit PERSISTENCE / RC-39) is downgraded from
CRITICAL-block to non-blocking (informational/visible) **iff ALL** hold — else it
stays CRITICAL:

1. **RESOLVABLE** — the program the persistence mechanism launches is identifiable
   and present **inside the plugin tree** (launchd plist `ProgramArguments`/`Program`;
   systemd `ExecStart`; cron command; the script being `cp`/`install`-ed). An opaque
   external binary, a path outside the plugin, or an unresolvable target → NOT
   allowed (stays CRITICAL).
2. **CLEAN** — scanning that launched script with CPV's own scanner yields no
   CRITICAL/MAJOR execution/exfiltration findings (no reverse shell, no exfil, no
   obfuscated decode-then-exec, no credential theft, etc.).
3. **NON-EXPLOITABLE** — the launched script does NOT:
   - dynamically load/execute external or mutable code: `curl|bash`, `wget|sh`,
     `eval` of downloaded/variable content, `source`/`.` of a remote or
     out-of-plugin/mutable file, `importlib`/`__import__` of a computed name,
     `exec()`/`Function()` of dynamic content, loading a plugin/module by a runtime
     path, etc.; AND
   - accept/listen to external inputs that enable RCE: bind/listen on a socket,
     read+eval from stdin/a named pipe/an env var/argv, expose an HTTP/RPC endpoint,
     watch a file and exec its content, a deserialization sink (pickle/yaml.load/
     Marshal) on external data, etc.
4. The install line is otherwise well-formed (no separate exfil/exec on the line).

If ANY of 1-4 fails → the finding stays CRITICAL (block). The discriminator only
ever *clears* a finding when the launched code is provably clean AND provably
inert; it never clears on the author's say-so.

## Scope / phased plan

- **Phase 1 (design):** opus design report (resolution of launched program per
  mechanism; reuse of CPV scanner for clean+non-exploitable; exact integration
  points in both code paths; two-sided test matrix). IN PROGRESS.
- **Phase 2 (impl):** implement the discriminator in both paths + a shared helper;
  TDD two-sided tests. Each ALLOW condition has a positive (clean→allowed) and a
  negative (each evasion → still blocked) test.
- **Phase 3 (open issues):**
  - #148 — REPO LINT hangs ~30 min on CI: add hard per-linter timeout + graceful
    skip of absent linters + `PLUGIN_SKIP_REPO_LINT=1` opt-out.
  - #149 — publish.py bumps pyproject.toml but not uv.lock → dirty tree: re-lock /
    sync `uv.lock` root version in the bump stage (gated on uv.lock + uv on PATH).
    Touches BOTH CPV's own publish.py AND the generated template in
    `generate_plugin_repo.py`.
  - #150 — standardize --force-templates: empty the-skills-menu + strips agent
    skills: POPULATE the catalog from the real `skills/` inventory before/while
    migrating; do NOT strip the agent's skills until the catalog is populated; drop
    `allowed-tools` from the generated menu skill; never report success on an empty
    catalog.
  - #151 — v2.143.0 canon ci.yml + publish.py defects (5 sub-items): mypy npx
    single-resolve narrowing; Pyright `reportAssignmentType` on the resilience
    import shim; zizmor `artipacked` (persist-credentials:false on all checkouts);
    commitlint annotated-tag-object→commit-sha deref; keep a zizmor job in canon
    ci.yml (or warn on removal). Touches `generate_plugin_repo.py` (ci.yml +
    publish.py templates) + CPV's own publish.py for the mypy/Pyright parts.
  - #147 — coordination: comment the final shipped version + cross-marketplace-dep
    guidance (AFTER ship).
- **Phase 4 (ship):** self-validate `--strict` cache-cold (0/0/0/0), update
  docs/README/CLAUDE.md + version-history, regen self-hashes LAST, `publish.py`
  with bump, watch CI green, answer #147.

## Verification (two-sided, FN-safe — the load-bearing requirement)

Every daemon-discriminator test is paired:
- a CLEAN+INERT daemon installer (resolvable launched script, no exec/exfil, no
  dynamic load, no input-listen) → the PERSISTENCE/RC-39 finding is CLEARED.
- for EACH evasion class, an otherwise-clean installer whose launched script
  (a) exfiltrates, (b) reverse-shells, (c) `curl|bash`-loads, (d) `eval`s an env
  var, (e) binds a listening socket, (f) `source`s an out-of-plugin file,
  (g) is unresolvable/external → the finding STAYS CRITICAL.
- regression: the #61 removal case stays cleared; a bare `LaunchAgents` mention
  with no resolvable+clean target stays CRITICAL.

## Approval log
- 2026-06-23T20:00:03+0200 — TRDD authored under explicit USER directive ("all
  issues must be solved before shipping" + the daemon-source-scan design). CPV is
  its own project → worked directly. Tier-0 (in-project security improvement under
  explicit user instruction).
