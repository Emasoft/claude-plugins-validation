---
trdd-id: a4260cc6-44f7-4150-9d1c-59daa4c700d3
title: v2.100.0 context-aware SkillAudit matcher — AST + schema + markdown contextualisation (closes #33)
status: in-progress
created: 2026-05-21T13:01:36+0200
updated: 2026-05-21T13:01:36+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-a4260cc6 — v2.100.0 context-aware SkillAudit matcher

## Source

GitHub issue [#33](https://github.com/Emasoft/claude-plugins-validation/issues/33) (verbatim):

> After v2.99.3 (`fe578d86…`) shipped the missing `rules/skillaudit_patterns.json` (closes #32), the audit now runs but mis-classifies hundreds of safe constructs as exploitable security findings. … This issue is NOT a request to skip SkillAudit. The audit needs to keep running and keep blocking real findings — but it needs to **understand the context of each match** so that documentation prose, JSON metadata strings, and hardcoded literal argv lists aren't reported as exploitable code paths.

User direction in chat (verbatim): "bigger" — i.e. ship the full v2.100.0 rewrite rather than a narrow patch.

## Iron-rule preservation

This TRDD MUST NOT weaken the SkillAudit gate:

- No env var, flag, or option bypasses the scanner.
- No rule is deleted from `scripts/rules/skillaudit_patterns.json`.
- "Demoted" matches stay visible as WARNING-level findings (per the user's "better safe than sorry" principle) — never silently dropped.
- A self-smoke gate in `publish.py` refuses any CPV release whose own self-audit produces CRITICAL/MAJOR/MINOR > 0. CPV legitimately uses `subprocess.run` heavily; if the matcher is too hot, CPV's own publish breaks first — preventing v2.99.3-style regressions from shipping.

## Concrete false-positive categories (from #33)

Reproduced on `ai-maestro-janitor` v0.5.0 with v2.99.3:

```
SUMMARY: CRITICAL=370 MAJOR=745 MINOR=253 NIT=243 WARNING=8
```

Every category below is a CPV-side mis-classification, not a defect in the audited plugin:

1. **CMD_INJECTION on hardcoded-literal argv** — `subprocess.run(["git-cliff", "--version"], capture_output=True, text=True)`. No f-string, no `shell=True`, no `.format(...)`, no variable. All elements are AST `Constant` nodes. Zero injection surface.
2. **CMD_INJECTION on Markdown prose** — inline `code` spans and backtick-quoted text in README.md prose: `re-run \`/janitor-arm\` if no drift surfaces`. Markdown does not execute its rendered text.
3. **CMD_INJECTION on JSON `description` fields** — `"description": "...re-shells \`git ls-files\` when HEAD has moved..."`. JSON description is UI metadata, never executed.
4. **TIME_BOMB on polling cadence description** — `"description": "Minimum seconds between checks against api.github.com... Default: 300 (5 min)"`. A configurable polling interval is the opposite of a logic bomb.
5. **SHELL_EXEC MINOR on every `subprocess.run`** — flagged at publish-blocking severity even on the safe shape from category 1.

Cross-cutting cause: regex over raw source text is structurally unable to distinguish "the word `curl` appears here" from "an exploitable execution path containing `curl` exists here."

## Design

### Layer 0: per-file-type context classifier (NEW)

Before the existing `_confidence()` heuristics run, every match passes through a context classifier specific to the host file's type. The classifier returns a `ContextVerdict`:

```python
class ContextVerdict(Enum):
    SAFE_LITERAL   = "safe_literal"    # Hardcoded constants, no injection surface → SUPPRESS
    SAFE_DOC       = "safe_doc"        # Documentation / UI metadata → SUPPRESS
    SAFE_SCHEMA    = "safe_schema"     # JSON description / title / etc. → SUPPRESS
    CODE_FENCE_NEUTRAL = "code_fence_neutral"  # Markdown ```bash with example shell → DEMOTE
    SUSPECT        = "suspect"         # The match-shape is consistent with a real exploit → KEEP
    UNKNOWN        = "unknown"         # No classifier applies / parse failed → KEEP (better safe than sorry)
```

`_confidence()` then collapses the verdict into the existing three-way classifier:

- `SAFE_LITERAL` / `SAFE_DOC` / `SAFE_SCHEMA` → `suppress`
- `CODE_FENCE_NEUTRAL` → `demote` (emits at NIT with ⚠ marker for agent triage)
- `SUSPECT` → `keep` (at rule's declared severity)
- `UNKNOWN` → `keep` (iron-rule preservation)

The existing heuristic-driven demote/suppress (markdown table, short-shell-token, docstring, GitHub Actions SSTI, …) remains as a SECOND PASS for matches the new layer classifies as UNKNOWN or SUSPECT.

### Per-file-type classifiers

#### Python (`_skillaudit_python_context.py`)

For `.py` files:

1. Parse the file with `ast.parse(source)`.
2. Locate the AST node covering the matched line.
3. If the node is a `Call`:
   - If the callee is one of `subprocess.{run,Popen,call,check_call,check_output}`, `os.{system,popen,execv,execve,execl,execlp,execvp}`, `commands.getoutput`, `pty.spawn` …
   - And the first positional argument is a `List`/`Tuple` of all-`Constant` elements
   - And `shell=` is absent OR `shell=False`
   - → `SAFE_LITERAL`
4. If the callee shape matches one of the above BUT contains any of: `JoinedStr` (f-string), `BinOp` (string concatenation with non-constant), `Call` to `.format` / `.join`, `Name` (variable reference)
   - → `SUSPECT`
5. If `shell=True` is set with any argument that contains non-`Constant` elements
   - → `SUSPECT`
6. If the matched line is inside a triple-quoted string literal (docstring or data string)
   - → `SAFE_DOC`
7. If the matched line is a comment-only line (starts with `#`)
   - → `SAFE_DOC`
8. Otherwise → `UNKNOWN`.

#### Shell (`_skillaudit_shell_context.py`)

For `.sh` / `.bash` / `.zsh` files:

1. Strip line comments (`# …`).
2. Tokenise with `shlex.shlex(posix=True)`.
3. If the matched token appears inside `'…'` (single-quoted, never expanded) → `SAFE_LITERAL`.
4. If the matched token appears immediately after a `case`/`if [`/`echo `/`printf ` construct as a literal pattern → `SAFE_DOC`.
5. Otherwise → `UNKNOWN`.

#### JSON (`_skillaudit_json_context.py`)

For `.json` / `.jsonc` files:

1. Parse with `json.loads` (or a lenient parser tolerating comments).
2. Locate the JSON path containing the matched line. Lines that don't fall inside a recognisable string-value (e.g. structural braces, whitespace) → `UNKNOWN`.
3. Walk the path back to its top-level segment(s):
   - **SAFE_KEY allowlist** (UI / metadata fields, never executed):
     `description`, `displayName`, `title`, `summary`, `keywords`, `tags`, `homepage`, `repository`, `bugs`, `documentation`, `license`, `licenses`, `author`, `authors`, `maintainers`, `contributors`, `funding`, `engines`, `readme`, `changelog`, `properties.*.description`, `properties.*.title`, `properties.*.default` (string default values are not code), `definitions.*.description`, `$comment`.
   - **DANGEROUS_KEY allowlist** (values that DO flow into runtime execution):
     `hooks.*.hooks.*.command`, `hooks.*.hooks.*.args`, `mcpServers.*.command`, `mcpServers.*.args`, `mcpServers.*.env.*`, `command`, `args`, `entrypoint`, `cmd`, `exec`, `run`, `script`, `shell`, `interpreter`, `binary`, `bin`.
4. SAFE_KEY → `SAFE_SCHEMA`. DANGEROUS_KEY → `UNKNOWN` (regex match remains; rule fires). Other paths → `UNKNOWN` (default to keep, matching iron-rule).

The SAFE_KEY allowlist is deliberately exhaustive for plugin / package / OpenAPI / JSON-Schema dialects, because over-permissive SAFE_KEY classification is the FP risk we accept (the iron-rule self-smoke gate catches the converse direction).

#### YAML / TOML (`_skillaudit_yaml_toml_context.py`)

Same shape as JSON. Use `yaml.safe_load` / `tomllib.loads` to parse; map line ranges to keys via the parser's line-tracking (PyYAML emits via `mark.line`; `tomllib` lacks line-tracking, so we fall back to a regex-based line-to-key map for TOML).

#### Markdown (`_skillaudit_markdown_context.py`)

For `.md` / `.markdown` files:

1. Stream the document line-by-line, maintaining state:
   - Open/close of fenced code blocks (` ``` ` or `~~~`), with their language tag.
   - Open/close of inline-code spans (` `…` ` within a line — track via a small backtick tokeniser).
   - Heading levels (for "Example" / "Snippet" / "Usage" / "Don't run this" heuristics).
2. For each matched line, return:
   - **Inside fenced code block with `bash` / `sh` / `shell` / `zsh` / `console` language** → fall through to shell-context classifier on that line.
   - **Inside fenced code block with `python` / `py` language** → fall through to python-context classifier.
   - **Inside fenced code block with `json` / `yaml` / `toml` / `jsonc` language** → `SAFE_SCHEMA`.
   - **Inside fenced code block with any other or no language** → `CODE_FENCE_NEUTRAL` (demote — could be intent-illustrative, agent triages).
   - **Outside any fence** → `SAFE_DOC` (prose / inline-code is documentation, not code).

The existing markdown-table demote heuristic remains as a redundant second pass.

#### Default (`.py` / `.json` / `.md` / `.yaml` / `.toml` / `.sh` only have the per-type classifier; everything else falls through to UNKNOWN and the existing heuristic chain)

### Severity rescale

A new optional `severity_floor` field is consulted per rule, per context. When the context classifier returns `SAFE_LITERAL` for a rule whose declared severity is high/critical, severity is floored to NIT (and demoted in the report) rather than fully suppressed. The user's "better safe than sorry" principle still routes the finding to the agent layer.

(Hardcoded-argv `subprocess.run` lands at `SAFE_LITERAL` → suppress in normal practice; severity_floor only matters if the classifier degrades to UNKNOWN under partial parse failure.)

### Self-smoke gate in `publish.py`

A NEW gate runs AFTER Gate 4 and BEFORE Gate 5:

```
═══ Gate 4b: CPV self-audit smoke ═══
$ uv run scripts/validate_plugin.py .
    → SkillAudit native must produce 0 CRITICAL / 0 MAJOR / 0 MINOR on CPV's own source tree.
    → If it fires, the matcher is over-permissive on CPV's own publish.py / scripts/dispatch.py
      which would over-fire on every other plugin too — publishing would re-introduce
      issue #33 downstream. FAIL.
```

This gate complements the regression suite — the suite pins specific FP categories (calibration), the smoke gate ensures the live audit on the host repo is sane.

### Test plan

NEW tests:

- `tests/test_skillaudit_python_context.py` — for each AST shape (literal argv, f-string argv, format-call argv, .join argv, shell=True with literal, shell=True with f-string, docstring, comment) → asserts the right `ContextVerdict`.
- `tests/test_skillaudit_json_context.py` — SAFE_KEY paths, DANGEROUS_KEY paths, nested paths, paths inside arrays.
- `tests/test_skillaudit_yaml_toml_context.py` — same shape, YAML + TOML inputs.
- `tests/test_skillaudit_markdown_context.py` — fenced bash, fenced python, fenced json, fenced no-tag, inline-code, prose paragraph, list item with backticks.
- `tests/test_issue_33_fp_calibration.py` — clones `Emasoft/ai-maestro-janitor@v0.5.0` into a session-scoped tmpdir, runs the full validate pipeline, asserts `CRITICAL == 0 and MAJOR == 0 and MINOR == 0 and NIT == 0`. Test is marked slow (network-dependent) and skipped when offline.
- `tests/test_skillaudit_still_catches_evil.py` — a curated set of malicious patterns (real `subprocess.run(f"curl http://{host}", shell=True)`, real exfil-then-curl, real prompt injection) MUST still produce CRITICAL findings.
- `tests/test_publish_self_smoke_gate.py` — pins Gate 4b's presence + the assertion semantics.

UPDATED tests:

- `tests/test_skillaudit_native.py` — TestNeverTouchesRules expectations (rule strings stay verbatim, no rule is deleted).
- `tests/test_skillaudit_v299_calibration.py` — assertions for demoted-vs-suppressed migrated to the new classifier surface.

### Files (new + modified)

NEW:
- `scripts/_skillaudit_python_context.py` (~250 LOC)
- `scripts/_skillaudit_shell_context.py` (~120 LOC)
- `scripts/_skillaudit_json_context.py` (~180 LOC)
- `scripts/_skillaudit_yaml_toml_context.py` (~140 LOC)
- `scripts/_skillaudit_markdown_context.py` (~200 LOC)
- 6 test files above (~1200 LOC total)
- `design/tasks/TRDD-20260521_130136+0200-a4260cc6-v2100-context-aware-skillaudit-matcher.md` (this file)

MODIFIED:
- `scripts/cpv_skillaudit_native.py` — `_confidence()` calls the new per-type classifiers FIRST; existing heuristics become second-pass.
- `scripts/publish.py` — Gate 4b self-smoke.
- `scripts/_plugin_compute_hashes.py` — register new modules as self-scan eligible (`is_validator_script`).
- `scripts/validate_security.py` — same registration on the mirror function.
- `CHANGELOG.md` (auto via git-cliff).
- `.plugin-self-hashes.json` / `.cpv-self-hashes.json` (auto via publish.py Gate 8 + 9b).

## Acceptance

- [x] TRDD authored, `status: in-progress`.
- [ ] `tests/test_issue_33_fp_calibration.py` scans `ai-maestro-janitor` v0.5.0 source tree → `CRITICAL=0 MAJOR=0 MINOR=0 NIT=0`.
- [ ] `tests/test_skillaudit_still_catches_evil.py` scans a curated malicious plugin → `CRITICAL > 0`.
- [ ] Self-smoke gate is present in `publish.py` and fails any release that produces CRITICAL/MAJOR/MINOR on CPV's own source.
- [ ] Full test suite passes (≥ 5500 tests).
- [ ] CPV's own self-scan: 0/0/0/0.
- [ ] `cpv-remote-validate plugin /path/to/ai-maestro-janitor --strict` produces 0 CRITICAL / 0 MAJOR / 0 MINOR / 0 NIT.
- [ ] v2.100.0 published, CI ✓ + Release ✓ + Notify Marketplace ✓ green.
- [ ] Issue #33 closed with link to v2.100.0.

## Lessons

A regex catalog that does not understand its host language will over-fire on benign content as soon as the host language has any literary use of the matched tokens — `subprocess.run` is fundamental to every plugin's job; backtick-quoted shell commands are how every README explains itself; JSON description fields literally exist to document behaviour. The fix is not "fewer rules" or "softer severity" — it is "the matcher knows what file it is reading." Each file type gets a small purpose-built parser whose job is to decide whether the regex match represents an exploit shape or a benign mention. The catalog stays intact; the gate becomes correct.

Iron-rule survival is enforced by the self-smoke gate: every release runs the audit on CPV's own source tree and refuses to publish if the matcher mis-fires on its host. v2.99.3 would have failed this gate; v2.100.0 must pass it.
