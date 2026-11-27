"""
TRDD-TLCJ9LPE item 8: the `modules` array of a Claude Mods hooks.json
(v2.1.287). Two-sided: every accept case has a reject sibling. Also covers
the c75e56c3 review fixes: cwd-safe base-dir resolution (inline relative
hook_path label) and the "./"-prefix WARNING demotion.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402


def _run(tmp: Path, modules, hooks=None, hook_path=None, plugin_root=None):
    hooks_dir = tmp / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    if hook_path is None:
        hook_path = str(hooks_dir / "hooks.json")
        (hooks_dir / "hooks.json").write_text("{}")  # real file: the cwd-safe branch requires hook_path.exists()
    report = HookValidationReport(hook_path=hook_path)
    data = {"modules": modules}
    if hooks is not None:
        data["hooks"] = hooks
    return validate_hooks_data(data, plugin_root if plugin_root is not None else tmp, report)


def _levels(report):
    return [r.level for r in report.results]


def test_empty_modules_major(tmp_path):
    r = _run(tmp_path, [])
    assert "MAJOR" in _levels(r)
    assert any("empty" in x.message for x in r.results if x.level == "MAJOR")


def test_valid_module_clean(tmp_path):
    _run(tmp_path, ["./mod.js"])  # creates hooks/ first (fixture-order fix)
    # A valid named `register` export: TRDD-775XK7HE scans every in-root module.
    (tmp_path / "hooks" / "mod.js").write_text("export function register(on) {}\n")
    r = validate_hooks_data(
        {"modules": ["./mod.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json")),
    )
    assert "MAJOR" not in _levels(r)
    assert "CRITICAL" not in _levels(r)


def test_missing_file_major(tmp_path):
    r = _run(tmp_path, ["./gone.js"])
    assert any("does not exist" in x.message for x in r.results if x.level == "MAJOR")


def test_traversal_major(tmp_path):
    # Native parity (A4, Claude Code 2.1.332, p59): a ".." path that leaves the
    # plugin root is refused ("The module path leaves the plugin directory");
    # one that stays inside (p02 "./sub/../register.js") is valid. hooks/ sits
    # one level under tmp_path, so "./../../evil.js" escapes the plugin root.
    r = _run(tmp_path, ["./../../evil.js"])
    assert any("leaves the plugin directory" in x.message for x in r.results if x.level == "MAJOR")


def test_no_prefix_warning_not_major(tmp_path):
    # Native parity (A2, Claude Code 2.1.332, p01/p53): a bare name resolves under
    # hooks/ like the "./" form. Existing valid module: clean, no WARNING;
    # missing bare module: MAJOR.
    _run(tmp_path, ["mod.js"])  # creates hooks/ first (fixture-order fix)
    (tmp_path / "hooks" / "mod.js").write_text("export function register(on) {}\n")
    r = validate_hooks_data(
        {"modules": ["mod.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json")),
    )
    assert "WARNING" not in _levels(r), [x.message for x in r.results]
    assert "MAJOR" not in _levels(r), [x.message for x in r.results]
    assert "CRITICAL" not in _levels(r), [x.message for x in r.results]
    r2 = validate_hooks_data(
        {"modules": ["gone.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json")),
    )
    assert any("does not exist" in x.message for x in r2.results if x.level == "MAJOR")


def test_bad_extension_major(tmp_path):
    # MAJOR not WARNING: oracle-probed 2026-10-05 (TRDD-TLCJ9LPE) — CC errors
    # on a non-listed extension, so the unprobed-WARNING rationale is gone.
    _run(tmp_path, ["./mod.txt"])  # creates hooks/ first (fixture-order fix)
    (tmp_path / "hooks" / "mod.txt").write_text("x\n")
    r = validate_hooks_data(
        {"modules": ["./mod.txt"]},
        tmp_path,
        HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json")),
    )
    assert any("not one of the documented" in x.message for x in r.results if x.level == "MAJOR")


def test_outside_root_major(tmp_path):
    # Mandated code only reaches the outside-root check via "./"-prefixed
    # entries joined against hooks_json_dir; a symlinked module is the way a
    # "."-free relative path can resolve outside the plugin root.
    outside = tempfile.mkdtemp()
    Path(outside, "evil.js").write_text("x\n")
    _run(tmp_path, ["./link.js"])
    (tmp_path / "hooks" / "link.js").symlink_to(Path(outside, "evil.js"))
    r = validate_hooks_data(
        {"modules": ["./link.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json")),
    )
    assert any("resolves outside" in x.message for x in r.results if x.level == "MAJOR")


def test_non_list_critical(tmp_path):
    r = _run(tmp_path, "not-a-list")
    assert "CRITICAL" in _levels(r)


def test_non_string_entry_critical(tmp_path):
    r = _run(tmp_path, [42])
    assert any("must be a string path" in x.message for x in r.results if x.level == "CRITICAL")


def test_inline_relative_hook_path_is_cwd_safe(tmp_path):
    # The inline-manifest path passes hook_path=".claude-plugin/plugin.json"
    # (relative label). Resolution must fall back to plugin_root, NOT the CWD:
    # run from a foreign cwd, an existing in-root module must stay clean.
    # With the fallback the module resolves against plugin_root itself, so
    # the module lives at the plugin root (not under hooks/).
    (tmp_path / "hooks").mkdir()
    (tmp_path / "mod.js").write_text("export function register(on) {}\n")  # valid mod (TRDD-775XK7HE)
    import os
    old = os.getcwd()
    os.chdir("/")  # a cwd where ".claude-plugin/mod.js" cannot exist
    try:
        r = _run(
            tmp_path,
            ["./mod.js"],
            hook_path=".claude-plugin/plugin.json",
            plugin_root=tmp_path,
        )
    finally:
        os.chdir(old)
    assert "MAJOR" not in _levels(r), [x.message for x in r.results]


def test_stale_absolute_hook_path_warns(tmp_path):
    # Review of 9c2a220a: an absolute-but-deleted hook_path silently degraded
    # to plugin-root resolution; now it emits a visible WARNING.
    (tmp_path / "mod.js").write_text("export function register(on) {}\n")  # valid mod (TRDD-775XK7HE)
    stale = tmp_path / "moved-away" / "hooks.json"  # absolute, does not exist
    r = validate_hooks_data(
        {"modules": ["./mod.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(stale)),
    )
    assert any("stale" in x.message for x in r.results if x.level == "WARNING")
    assert "MAJOR" not in [x.level for x in r.results]
    # Sibling control: with the module MISSING, the does-not-exist MAJOR must
    # still fire through the fallback — proving resolution used plugin_root.
    r2 = validate_hooks_data(
        {"modules": ["./gone.js"]},
        tmp_path,
        HookValidationReport(hook_path=str(stale)),
    )
    assert any("does not exist" in x.message for x in r2.results if x.level == "MAJOR")


def test_mod_without_hooks_key_accepted(tmp_path):
    # A bare mods hooks.json: modules and NO hooks key (top-level relax).
    (tmp_path / "hooks").mkdir(exist_ok=True)
    (tmp_path / "hooks" / "mod.js").write_text("x\n")
    hooks_dir = tmp_path / "hooks"
    report = HookValidationReport(hook_path=str(hooks_dir / "hooks.json"))
    r = validate_hooks_data({"modules": ["./mod.js"]}, tmp_path, report)
    assert "CRITICAL" not in _levels(r)


def test_bare_mod_file_via_cli(tmp_path):
    # End-to-end: a bare mod hooks.json through the CLI entry validates clean.
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "mod.js").write_text("x\n")
    (hooks_dir / "hooks.json").write_text(json.dumps({"modules": ["./mod.js"]}))
    import subprocess
    res = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent.parent / "scripts" / "validate_hook.py"), str(hooks_dir / "hooks.json")],
        capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    import re
    # Anti-vacuity control: the regex must fire on a nonzero count — pinning
    # the summary format itself (the /11-docstring class of vacuous regex).
    assert re.search(r"(CRITICAL|MAJOR):\s*[1-9]", "CRITICAL: 1")
    # Close the format-drift hole: the REAL passing summary must carry a
    # colon-digit line — a count-first summary would satisfy both the
    # fabricated control and the negative assert.
    assert re.search(r"CRITICAL:\s*0", res.stdout), res.stdout + res.stderr
    # The summary block always prints the labels with counts ("CRITICAL: 0");
    # the verdict contract is a nonzero count, so assert no nonzero line.
    assert not re.search(r"(CRITICAL|MAJOR):\s*[1-9]", res.stdout), res.stdout + res.stderr
