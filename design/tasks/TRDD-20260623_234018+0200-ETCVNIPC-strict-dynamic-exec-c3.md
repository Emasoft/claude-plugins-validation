---
trdd-id: ETCVNIPC
title: Maximum-strictness C3 — block ALL dynamic exec/script-run (os.exec*/spawn/runpy/file-import), no sandbox exemption
column: published
created: 2026-06-23T23:40:18+0200
updated: 2026-06-24T03:08:24+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: HIGH
effort: M
labels: [security, persistence, false-negative, issue-152]
task-type: security
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
delivery: direct-push
target-branch: master
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
runtime-targets: [macos, linux]
impacts: []
attempts: 1
last-test-result: pass
implementation-commits: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/152"]
---

# Maximum-strictness C3 — block ALL dynamic exec/script-run

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-23

**Decision (user, ratified):** MAXIMUM STRICTNESS. CPV's daemon-source-scan
discriminator C3 (`_non_exploitable`) must treat **any** dynamic process-exec /
script-run / file-based dynamic-import as exploitable → C3 FAIL → STAY CRITICAL.
**NO `~/.claude/plugins/` sandbox exemption** — even an exec of the plugin's OWN
cache/data path disqualifies, because the resolved target is mutable /
version-stamped, so what RUNS is not what was SCANNED.

**The proven FN hole (empirical, v2.145.1):** `cpv_persistence_target._matches_3a`
catches `exec(`/`eval(`/`compile(`/computed-`import` but MISSES every
`os.exec*` / `os.spawn*` / `os.posix_spawn` / `runpy.run_(path|module)` /
`imp.load_*` / `spec_from_file_location`. A minimal `os.execv(resolved_path, argv)`
daemon → 3a False, 3b False → `_non_exploitable` False → C3 PASSES → wrongly
cleared. (The janitor's recovered `daemon-launcher.py` only fails C3 today by
coincidence — 3b fires on its `subprocess`+`sys.argv` proximity.)

**BOTH phases landed in THIS change** (the earlier "Phase 2 = separate TRDD"
plan was collapsed — the fold is what makes the strict-exec policy usable, so
they ship together):

**Phase 1 — strict dynamic-exec (LANDED):** appended the missing dynamic-exec/load
primitives to `_3A_PATTERNS` so the hole closes (`os.exec*`/`os.spawn*`/
`os.posix_spawn`/`runpy.run_(path|module)`/`imp.load_*`/`spec_from_file_location`).
Fixed-argv `subprocess.run([...])` (the persistence INSTALL action, judged by C4)
stays UNMATCHED — verified two-sided.

**Phase 2 — data-dir fold + interpreter/script resolve (LANDED):** so a
genuinely-clean STATIC sandbox daemon can PASS:
- C1 data-dir fold (`_PLUGIN_DATA_LITERAL_RE` + `_fold_to_plugin_root`):
  recognize `~/.claude/plugins/data/<slug>/<rest>` in `~` / `$HOME` / `${HOME}`
  forms (and `$CLAUDE_PLUGIN_DATA/<rest>` via the existing env fold) → resolve to
  `plugin_root/<rest>` and scan THAT in-tree source. **No slug gate** (CPV must
  scan uninstalled, marketplace-less plugins — see memory
  `feedback-cpv-scans-uninstalled-plugins`); the wildcard `[^/]+` slug + the
  exact `~/.claude/plugins/data/` prefix is the whole gate. A non-sandbox `$HOME`
  / cache / residual-`$VAR` / bare-`~` target still REFUSES (returns None → C1
  fails → STAY CRITICAL) — "evaluate the full path", per the user.
- Gap A — `_interp_script_target`: a plist `ProgramArguments:[python3, <script>]`
  / systemd `ExecStart={python} {launcher}` now extracts argv[1] (the scannable
  script), not argv[0] (the interpreter, never in-tree). Interpreter + only-flags
  ⇒ inline code ⇒ no scannable file ⇒ STAY CRITICAL.

**THE SOUNDNESS INVARIANT (memory `feedback-daemon-staging-verbatim-copy`):** the
fold scans `plugin_root/<rest>`, so it is FN-safe ONLY IF the file staged at
`data/<slug>/<rest>` is a **verbatim copy** of that in-tree source. A plugin
installer may COPY a shipped (already-scanned) script into the data folder but may
NOT generate/template/edit it at install time. CPV today ASSUMES copy-only and is
fail-safe (no `plugin_root/<rest>` source ⇒ C1 fails ⇒ CRITICAL); a future CPV
enhancement could data-flow the installer to actively enforce copy-vs-generate.

**Janitor side (USER-owned, not CPV):** the user is repointing the OS service at a
static, already-shipped plugin script (no self-roll `os.execv`), staged as a
verbatim copy preserving its in-tree relpath. That static daemon now
resolves+scans+passes. The janitor's CURRENT self-roll `daemon-launcher.py`
(`os.execv(newest_cache/daemon.py)`) correctly STAYS CRITICAL — the intended,
ratified maximum-strictness outcome.

**Still a separate, SEQUENCED follow-up (NOT this change):** the
`_skillaudit_shell_context` FPs the janitor's #152 findings hit (persistence vocab
inside a return-message STRING LITERAL; a removal/uninstall line). Those are a
DIFFERENT detector path AND they fire on janitor source lines that the refactor
will rewrite — fixing FPs against soon-deleted code is wasteful. Re-verify the
GENERAL FP classes against the janitor's REFACTORED installer once it lands, then
fix the universal class (string-literal vocab; removal-line) if it still fires.

## Phase 1 implementation

In `scripts/cpv_persistence_target.py`, append to `_3A_PATTERNS` (so they are part
of 3a AND recognized as `*_PATTERNS` pattern-source for CPV's own self-scan-skip):

```
\bos\.exec[a-z]*\s*\(            os.execv/execve/execvp/execl…
\bos\.spawn[a-z]*\s*\(           os.spawnv/spawnl/spawnvp…
\bos\.posix_spawnp?\s*\(         os.posix_spawn/posix_spawnp
\brunpy\.run_(?:path|module)\s*\(  runpy.run_path/run_module
\bimp\.load_(?:source|module|compiled)\s*\(   legacy dynamic import
\bspec_from_file_location\s*\(   importlib file-based dynamic import
```

All re2-safe (no lookaround). `ctypes.CDLL`/`cdll.LoadLibrary` deliberately
EXCLUDED in Phase 1 — dual-use (benign system-API daemons load fixed system
libs); revisit separately if needed.

## Tests (two-sided) — `tests/test_persistence_daemon_scan.py`

- POSITIVE-block: `_matches_3a` True for each of os.execv/execve/execvp/spawnv/
  posix_spawn/runpy.run_path/imp.load_source/spec_from_file_location.
- Regression: `_matches_3a` False for a clean static-leaf daemon body, and for
  `subprocess.run(["launchctl","load",str(p)])` (install action stays allowed).
- End-to-end: a daemon whose launched program contains `os.execv(...)` →
  `persistence_launches_clean_inert_target(...)` returns False (STAY CRITICAL).
- Recovered janitor `daemon-launcher.py` content → `_matches_3a` True (hole closed
  directly, not via the 3b coincidence).

## Verification gates
- `CPV_SCAN_CACHE=0` self-validate of `cpv_persistence_target.py` stays 0/0/0/0
  (the new `re.compile(r"…")` lines must read as `*_PATTERNS` rule-data, not
  self-flag).
- serial pytest (CI is serial + no-re2) on the persistence suite.
- `mypy scripts/ --ignore-missing-imports`.
- central independent verify (separate opus agent): try to find an
  os.exec*/spawn/runpy shape that evades the new patterns, or a benign clean
  daemon the patterns now over-block.

## Durable artifacts
- Recovered janitor L0 source (read-only evidence):
  `scratchpad/janitor-l0/{launchd_keepalive.py,daemon-launcher.py}` (from
  ai-maestro-janitor commit `eb109fb^`, branch `extract-launchd-l0`).
- Issue #152 (the C1-fold request — Phase 2).
