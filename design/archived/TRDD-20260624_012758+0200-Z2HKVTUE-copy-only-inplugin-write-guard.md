---
trdd-id: Z2HKVTUE
title: Copy-only enforcement — flag a plugin script that generates or edits a script INSIDE the plugin tree (ROOT or DATA)
column: published
created: 2026-06-24T01:27:58+0200
updated: 2026-06-24T03:08:24+0200
current-owner: cpv-main-session
assignee: cpv-main-session
priority: 1
severity: HIGH
effort: M
labels: [security, persistence, copy-only, issue-152]
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

# Copy-only enforcement — block in-plugin script generation/editing

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-24

**Why this exists:** the #152 daemon-source-scan fold (TRDD-ETCVNIPC) scans the
IN-TREE source `plugin_root/<rest>` and clears it. That is sound ONLY IF the file
staged at `~/.claude/plugins/data/<slug>/<rest>` is a VERBATIM COPY of it. Without
an active check, a plugin could GENERATE a different staged file at install time
(what RUNS ≠ what was SCANNED). This guard is that check — it ships WITH
TRDD-ETCVNIPC as one release, closing the fold's soundness loop. Ratified with the
user this session.

**THE RULE (user, ratified):** a plugin may COPY a shipped, already-scanned script
into the plugin DATA folder, but may NOT generate / template / string-substitute /
edit a script that lands INSIDE the plugin tree — and "inside" means BOTH the
plugin ROOT tree AND the DATA tree (`~/.claude/plugins/data/<slug>/…`). Only
CPV-scanned scripts are trusted; a post-install mutation produces an unscanned
script that then runs, with no CPV pass to re-scan it.

**THE QUIRK (do NOT over-block):** a plugin's legitimate FUNCTION is often to
generate/edit scripts. The discriminator is the DESTINATION, not the act: output
written OUTSIDE the plugin (into the user's PROJECT folder, evaluated by that
project's own Claude) is ALLOWED; only writes that create/modify a script INSIDE
the plugin tree are blocked.

**THE FAIL-SAFE (ratified ONLY FOR THIS VERSION):** block only PROVABLE in-plugin
writes — a destination that statically resolves, or env-folds via
`CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA`/the `~/.claude/plugins/data/<slug>/`
literal, into the plugin tree. A dynamic/unresolvable destination PASSES (so legit
code-gen plugins computing project paths are not over-blocked). The residual gap
(a computed in-plugin path slips) is accepted for now and tracked by
TRDD-ETDWX70R (the next-version unambiguous resolver).

**STATUS (verified-green):** IMPLEMENTED. `scripts/cpv_inplugin_write_guard.py`
wired as RC-164 in `validate_security.py::check_phase2e_extras` (CRITICAL,
self-scan-skip via `*_PATTERNS` gated on `_CPV_IS_RUNNING_CPV`, re2-safe) + 115
two-sided tests. Independently re-verified: serial pytest pass, mypy clean,
whole-plugin cold self-validate (`CPV_SCAN_CACHE=0`) 0/0/0/0 with RC-164 active over
all CPV scripts. Verification ALSO added the read-then-write copy carve-out
(`write_bytes(read_bytes())` / `write_text(read_text())`) so a verbatim-copy idiom
is not over-flagged, with a BLOCK sibling proving a generated write still flags (+3
tests). Ships WITH TRDD-ETCVNIPC as one release; both close #152.

**v1 RESIDUALS (accepted this version; the unambiguous resolver is TRDD-ETDWX70R):**
- Computed / variable-held / `os.path.join` destinations PASS (lenient fail-safe, by
  design — they do not provably resolve in-tree).
- A bare-relative destination is treated as in-plugin (FN-safe, consistent with the
  #152 fold's `_fold_to_plugin_root`); a bare-relative PROJECT-output write is
  therefore flagged — a documented choice, not a defect.
- The copy carve-out does NOT verify the copy SOURCE is itself in-tree (`cp` /
  `shutil.copy` / `write_bytes(read_bytes())` from an EXTERNAL source is allowed) — a
  copy-from-external residual; ETDWX70R should verify a copy's source resolves
  in-tree.
- A `cat <file> > dst.py` shell read-redirect copy is flagged as a generate (minor
  FP; `cp` is the common shell copy and IS carved out).

## Detection design

A plugin source file is FLAGGED (CRITICAL) when it performs a WRITE that:

1. creates/modifies a SCRIPT/SOURCE file (the script-file gate — see below), AND
2. whose destination PROVABLY resolves INSIDE the plugin tree (ROOT or DATA) via
   `cpv_persistence_target._resolve_in_tree` / `_fold_to_plugin_root` (returns a
   `Path` under `plugin_root`, incl. the data-dir literal fold; `None` ⇒ not
   provable ⇒ PASS), AND
3. is NOT a verbatim COPY of an in-tree source (the copy carve-out — see below).

### Write primitives detected
- Python: `open(dst, "w"|"a"|"x"|"w+"|…)`, `Path(dst).write_text/​write_bytes(…)`,
  `os.open(dst, …O_WRONLY|O_CREAT…)`, heredoc/`f.write` into an opened in-plugin
  file.
- Shell: `> dst`, `>> dst`, `tee dst`, `cat … > dst`, `sed -i … dst` (edit in
  place), heredoc `cat <<EOF > dst`.

### Copy carve-out (ALLOW)
`shutil.copy/copy2/copyfile/copytree`, shell `cp`, `install` (no transform) — when
the SOURCE is an in-tree path and the destination is in-plugin → ALLOW (a verbatim
copy of an already-scanned file is exactly what the rule permits).

### Script-file gate (avoid over-blocking the blessed DATA dir)
DATA is the plugin's writable home — plugins legitimately write caches, state, and
downloaded deps there (`CLAUDE_PLUGIN_DATA` convention). So flag ONLY when the
destination is a SCRIPT/SOURCE file — by extension (`.py .pyw .sh .bash .zsh .ksh
.js .mjs .cjs .ts .tsx .rb .pl .pm .lua .ps1 .psm1 .bat .cmd .php .r .jl
.applescript .scpt`), OR a shebang is written into it, OR it is `chmod +x`'d on an
in-plugin path. A write of a non-script file (`.json .log .txt .cache .db .lock
.yaml …`) into DATA is ALLOWED.

### Severity
CRITICAL — an unscanned in-plugin script that runs is the risk; matches the user's
"the plugin's scripts must not change, and no new in-plugin script may be added."

## Integration
- New module `scripts/cpv_inplugin_write_guard.py` exporting one function that
  scans a plugin file's content + returns findings; reuses the resolution helpers
  from `cpv_persistence_target.py`.
- Wire into `validate_security.py`'s per-file scan loop as a new RC rule (next free
  RC id, determined at implementation by grepping the max RC-NN).
- Self-scan-skip: the module's own write-primitive pattern literals MUST live in a
  recognized `*_PATTERNS` collection so CPV's self-validate stays 0/0/0/0 (same
  `_CPV_IS_RUNNING_CPV`-gated pattern-source skip the persistence module uses).

## Two-sided tests (`tests/test_inplugin_write_guard.py`)
- BLOCK: generate a `.py`/`.sh` into DATA via `write_text` / `open("w")` / `>` /
  heredoc / `sed -i` → flagged.
- BLOCK: edit an existing in-plugin script in place → flagged.
- ALLOW: `shutil.copyfile(in_tree_src, data_dst)` → not flagged (verbatim copy).
- ALLOW: write a `.json`/`.cache` into DATA → not flagged (not a script).
- ALLOW: generate a `.py` into the PROJECT folder / cwd / a non-plugin path → not
  flagged (outside).
- ALLOW: write to a dynamic/unresolvable destination → not flagged (lenient).
- Coupling: a daemon staged by GENERATE (not copy) into `data/<slug>/<rest>` →
  flagged (this is the #152 fold gap closure).

## Verification gates
- Serial pytest on the new suite + the persistence suite (CI is serial + no-re2).
- `mypy scripts/cpv_inplugin_write_guard.py --ignore-missing-imports`.
- Whole-plugin self-validate cold (`CPV_SCAN_CACHE=0`) stays 0/0/0/0 — the new
  module's pattern literals must read as `*_PATTERNS` data, not self-flag.

## Relationship
- Ships WITH TRDD-ETCVNIPC (the #152 fold + strict-exec) as one release; together
  they make the daemon-from-data-dir path sound. Both close issue #152.
- Superseded-resolver follow-up: TRDD-ETDWX70R (next-version unambiguous in/out
  path determination) replaces this guard's fail-safe-LENIENT resolver.
