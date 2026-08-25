---
trdd-id: 858e449c-8c56-445b-aa05-dde27db1876b
title: Issue #75 — security-scanner-plugin false positives after self-exemption removal (5 classes)
column: published
created: 2026-06-11T10:58:49+0200
updated: 2026-06-24T03:27:35+0200
current-owner: cpv-maintainer-claude
task-type: bugfix
release-via: publish
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
relevant-rules: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/75"]
---

# TRDD-858e449c — Issue #75 scanner-plugin FPs (5 classes)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative) — 2026-06-11

**What this is:** Issue #75 (filed by the ai-maestro-janitor Claude) reports 5 FP
classes from CPV `--strict` after self-exemption removal. ai-maestro-janitor is a
SECURITY SCANNER; its real detector needles are already devitalized. These 5 are
the residual where the reporter says there's no safe in-plugin fix.

**Cross-project guard:** the janitor dev tree at `~/Code/ai-maestro-janitor`
is a SEPARATE project — READ/SCAN only to reproduce, NEVER edit. All fixes land in CPV.

**Governing rules:** never suppress a rule, never relax `--strict`; only auto-clear
is provably-inert data OR FN-safe precision (language/AST/context). Every FP fix is
two-sided (the real-threat sibling MUST still fire). ALWAYS Opus for the security calls.

**The 5 classes + preliminary verdicts (to be confirmed by code-read + /tmp repro):**

| # | Class (rule) | CPV code | Preliminary verdict | Sibling |
|---|---|---|---|---|
| 1 | `tests/` fixtures flagged live threats (RC-70) | validate_plugin.py ~2469-2576; cpv_validation_common.py ~2763+ | LIKELY BY-DESIGN (tests/-skip = RT-hole). Confirm whether provably-inert carve-out should apply to string-literal fixtures | #70-B4, #63 |
| 2 | safe `yaml.load(x, Loader=_DupLoader)` flagged unsafe (RC-73) | cpv_validation_common.py ~3776/4426/4459; validate_plugin.py ~2470 | LIKELY VALID FP — parallels #60 but in RC-73 path | #60 |
| 3 | local sys.path sibling imports → "missing PEP-723 dep" | validate_plugin.py (PEP-723 check) | LIKELY VALID FP — parallels #62; confirm hooks/ coverage | #62 |
| 4 | `env["CARGO_TARGET_DIR"]=tmp` flagged ENV_INJECTION | _skillaudit_python_context.py ~2679-2694; cpv_validation_common.py:6443 | LIKELY VALID FP — keep firing on LD_PRELOAD/PATH/PYTHONPATH/NODE_OPTIONS | new |
| 5 | `tools/<crate>` Rust build dir → RC-NONSTD-DIR-001 + "no build script" | validate_plugin.py:2039 | PARTLY BY-DESIGN (canonical = `rust/` per #71/#72). build.sh-in-tools/<crate> recognition MAY be addressable | #71/#72 |

**RESULTS (all 5 implemented + central-verified):**
- C1 RC-70 inert-string AST carve-out → validate_security.py (+12 tests). FP clears; real exec(b64decode) in tests/ still fires. tests/-skip + annotation asks REJECTED (RT-holes).
- C2 RC-73 yaml Loader-safety taint carve-out → cpv_taint_engine.py (+13 tests). SafeLoader-subclass clears; bare/yaml.Loader/FullLoader/python-object-readd fire.
- C3 PEP-723 variable-insert sibling resolution → validate_hook.py (+6 tests). local sibling clears; missing PyPI dep fires.
- C4 ENV_INJECTION build-output/cache allowlist → _skillaudit_python_context.py (+22 tests). CARGO_TARGET_DIR clears; LD_PRELOAD/PATH/etc fire (allowlist ∩ hijack = ∅).
- C5 PARTIAL: "no build script" ancestor-dir search → validate_plugin.py (+3 tests). RC-NONSTD-DIR-001 on tools/ kept BY-DESIGN.
- Central: ruff+mypy clean; manifest regen; self-validate VALID 0/0/0/0+4WARN; **serial suite 8972 passed / 2 skipped / exit 0**.

**FOLLOW-UP (tracked, NOT in this release — already mitigated by C2 RC-73):** #60 skillaudit
DESERIALIZATION `_classdef_subclasses_safe_loader` ignores add_constructor re-enablement —
a python/object-readding SafeLoader subclass clears there, but C2's RC-73 now FIRES on it.

**NEXT ACTION:** publish (publish.py --patch → v2.126.7), then comment on #75 (self-id line)
explaining each class's resolution (C1-C4 fixed; C5 build-script fixed + tools/ by-design),
then close. Reporter's plugin still gets the tools/ MAJOR unless it moves the crate to rust/.

**Load-bearing gotchas:**
- `CPV_SCAN_CACHE=0` for local classifier testing (cache keyed on version+catalog, not classifier code).
- Regen self-hash manifest (`uv run python scripts/_plugin_compute_hashes.py`) AFTER editing CPV files, BEFORE trusting self-validate.
- Serial suite before publish (`uv run pytest -p no:cacheprovider -o addopts=""`); CI is serial+no-re2.
- GitHub comment MUST begin: "This is the Claude responsible for the claude-plugins-validation project."

## Plan
1. Reproduce each class on a minimal /tmp fixture through the real validator.
2. Per-class FN-safe fix OR documented by-design call (never punch attacker-forgeable holes).
3. Two-sided tests (FP clears AND real-threat sibling fires).
4. Manifest regen, serial-suite, self-validate --strict.
5. Comment on #75 + publish (only the fixed classes; by-design classes explained).
