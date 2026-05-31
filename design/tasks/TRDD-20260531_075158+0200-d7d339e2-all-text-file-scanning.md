---
trdd-id: d7d339e2-654d-47ec-b37f-68358b3100f9
title: Point 1 — scan EVERY text file, not an extension allowlist (+ shebang dispatch + literal-exec-sink hardening)
status: completed
created: 2026-05-31T07:51:58+0200
updated: 2026-05-31T07:51:58+0200
---

# TRDD-d7d339e2 — Scan EVERY text file (Point 1) + shebang dispatch + literal-exec-sink hardening

**Filename:** `design/tasks/TRDD-20260531_075158+0200-d7d339e2-all-text-file-scanning.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-05-31

**Shipped in v2.114.0. status: completed.** Three layered changes, each
verified. Do NOT re-derive — read this block + the test file.

1. **Point 1 (core):** CPV now content-scans EVERY text file, gated on
   text-vs-binary, NOT on a file-extension allowlist. The two old
   allowlists — `_SCAN_EXTENSIONS` (skillaudit) and `SCANNABLE_EXTENSIONS`
   (private-info / abs-path / marketplace) — are **deleted**. A malicious
   payload parked in `payload.info` / `recipe.cfg` / a `.rst` / an
   extension-less `runme` and referenced from `SKILL.md` is now scanned.
2. **Derived fix — shebang dispatch:** extension-less executables (git
   hooks, `configure`, `runme`) are now scanned, but the context
   classifiers dispatch on extension. `_shebang_language()` recovers the
   language from `#!…` and rewrites `file_path` to a synthetic extension so
   the right classifier (and its internal extension guard) fires. Without
   this, CPV's OWN canonical-pipeline python git-hooks (`subprocess.run`)
   would blocking-FP on every adopting plugin.
3. **Derived² fix — literal exec-sink hardening (the important one):** the
   Python classifier returned `safe_literal` for the enclosing-call shape
   *regardless of rule*, so a hardcoded
   `os.system('bash -i >& /dev/tcp/…')` (REVERSE_SHELL) or
   `os.system('cat ~/.ssh/id_rsa | nc …')` (CRED_ENV_READ) was suppressed
   to `info` (non-blocking). A literal argv removes the INJECTION surface,
   NOT the CONTENT threat — the literal is still EXECUTED. Fixed at
   `_skillaudit_python_context.py:~2371`: `safe_literal` from
   `_classify_call` now suppresses ONLY `_CALL_SHAPE_SUPPRESSIBLE_RULES`
   = `{CMD_INJECTION, SHELL_EXEC}` (pure injection-surface rules); every
   content-threat rule falls through → keep. This hardens ALL `.py`
   scanning, not just Point 1's new surface.

**NEXT ACTION (if reopened):** none — completed. Two follow-ups are tracked
below under "Deferred / follow-up", NOT blockers.

**Load-bearing facts / gotchas:**
- The placeholder/reserved-indicator suppression is REAL and correct:
  `example.com`, `203.0.113.x` (TEST-NET-3), etc. are suppressed to `info`.
  When writing malicious-detection tests, use a NON-reserved domain
  (`malware-cdn.cc`) and a NON-reserved IP (`45.137.21.89`), else the test
  asserts "blocking" on content CPV correctly treats as a doc placeholder.
- A skillaudit verdict of `"suppress"` does NOT drop the finding — it
  emits it at `info` (`cpv_skillaudit_native.py:2341`). `info` is
  non-blocking and below the CRITICAL/MAJOR/MINOR/NIT/WARNING summary.
  "Visible but non-blocking" satisfies the "never silently suppress"
  invariant.
- `cpv_self_scan_skip` is SHA-anchored: editing any CPV `.py` file makes
  its self-scan-skip stop firing until the manifest is regenerated
  (`uv run python scripts/_plugin_compute_hashes.py`). A local self-scan
  showing findings ONLY in an edited scanner file = stale hash, NOT a real
  regression. Regenerate, re-scan.

**SUPERSEDED — do NOT carry forward:**
- ✗ "`safe_literal` suppresses regardless of rule (the shape precludes
  exploitation)" — the old `_skillaudit_python_context.py:2371` comment.
  REPLACED: only injection-surface rules; content-threat rules keep.
- ✗ "`SCANNABLE_EXTENSIONS` / `_SCAN_EXTENSIONS` gate the scanners." Both
  constants are DELETED; the gate is `is_scannable_text_file` (= not
  binary).

## The vector being closed

The session uncovered the `references/` bypass (move executable recipes into
a doc-only `.md` referenced from `SKILL.md`). The user generalised it:

> "any text file that is not code can be used as a vector. even a `.ini`
> file or a `.info` file can be used maliciously if referenced from the
> `SKILL.md`. You should extend the scan to all text files, no exceptions."

The old gates enumerated ~14–21 extensions and SILENTLY skipped everything
else. Park the payload in `payload.info` → never scanned. Closed.

## Files changed (v2.114.0)

- `scripts/cpv_validation_common.py` — NEW `is_scannable_text_file()`
  (`= not is_binary_file`); private-info (`scan_directory_for_private_info`)
  and abs-path (`validate_no_absolute_paths`) gates switched to it;
  `SCANNABLE_EXTENSIONS` constant DELETED.
- `scripts/validate_marketplace.py` — import + 2 gates switched; the
  extension-less `LICENSE` root file (suffix `""`, previously skipped) now
  scanned.
- `scripts/cpv_skillaudit_native.py` — NEW `_file_is_binary_for_gate` +
  `_file_is_scannable` (text always; binary only when `CPV_BINARY_SCAN` on);
  walker gates (`_iter_scannable_files`, single-file + dir-walk) switched;
  `_SCAN_EXTENSIONS` DELETED. NEW `_shebang_language` + `_CLASSIFIER_EXTENSIONS`;
  `_context_classifier_verdict` rewrites `file_path` to a synthetic
  shebang-derived extension when no classifier extension matches.
- `scripts/_skillaudit_python_context.py` — NEW `_CALL_SHAPE_SUPPRESSIBLE_RULES`
  = `{CMD_INJECTION, SHELL_EXEC}`; the `_find_enclosing_call` →
  `_classify_call` block (~2371) no longer returns `safe_literal` for
  content-threat rules.
- `tests/test_point1_all_text_file_scan.py` — NEW, ~51 two-sided tests
  across 7 classes (helper, walker, scan_path, gate helpers, private-info/
  abs-path, shebang dispatch, literal exec-sink content threats).
- `tests/test_skillaudit_python_context.py` — `test_eval_with_literal_arg`
  updated (literal eval arg now → `unknown`, not `safe_literal`; a literal
  eval CAN be a malicious payload).
- `tests/test_skillaudit_integration_v2_104.py` — stale `_SCAN_EXTENSIONS`
  comment refreshed.

## Why each derived fix is mandatory (DERIVED tasks)

- **Shebang dispatch:** without it, scanning extension-less python hooks
  (which CPV's own canonical pipeline ships) routes them through the raw
  heuristic chain → benign `subprocess.run(["git",…])` blocking-FPs every
  adopting plugin. With it, they route to the Python classifier (benign
  call shape → quiet).
- **Literal exec-sink hardening:** the shebang dispatch routes extension-less
  python through the SAME classifier as `.py`, which had a pre-existing hole
  (literal exec-sink content threats suppressed). Shipping shebang dispatch
  WITHOUT this fix would let an extension-less python hook hide a reverse
  shell at `info`. The fix closes it for `.py` AND extension-less alike.

## Security invariants honored

- FPs recognised INTRINSICALLY (text-vs-binary content sniff, AST call
  shape, shebang) — never self-declared.
- "Never silently suppress malicious code": a literal reverse shell / exfil
  now KEEPS at declared severity (blocking). Verified two-sided.
- `_CALL_SHAPE_SUPPRESSIBLE_RULES` kept deliberately SMALL — a rule omitted
  merely stays VISIBLE (over-flag), never hidden.
- Exhaustive SHA self-integrity unaffected (manifest regenerated; self-scan
  0/0/0/0).

## Deferred / follow-up (NOT blockers)

1. **TypeScript/JS exec-sink hardening:** the TS/JS classifier has
   the analogous `safe_literal` path; a literal `child_process.exec('<evil>')`
   may have the same shape. Audit `_skillaudit_typescript_context.py` for the
   same content-threat carve-out. (Lower priority — JS template literals are
   already handled differently.)
2. **`references/` inline-code-in-warning-prose residual** (from the prior
   bypass work) — separate `_MD_DOC_EXAMPLE_RULES` heuristic; tracked earlier.

## Acceptance (all verified before publish)

- `tests/test_point1_all_text_file_scan.py` — all green (51).
- Full skillaudit/classifier suite green (442) after the one expected
  `eval` test update.
- `mypy scripts/` clean (117 files); `ruff check` clean.
- CPV self-scan 0/0/0/0/0 after manifest regen.
- Real CPV non-code text files (uv.lock / LICENSE / .python-version /
  git-hooks) scanned with NO blocking findings (git-hooks' benign
  `subprocess.run` quieted via shebang+classifier).
