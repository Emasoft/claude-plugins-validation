"""
Native parity for the hooks.json `modules` entry checks (gap report items
A2, A3, A4, A6). Oracle: `claude plugin validate` on Claude Code 2.1.332;
fixture ids (p01 ... p59) are those of reports_dev/mods-gap/fixtures/. Each
test rebuilds the fixture shape in tmp_path and asserts CPV reaches the same
verdict as native: native pass -> no MAJOR/CRITICAL; native ERROR -> MAJOR;
p04 (native WARN, entry ignored) -> a targeted CRITICAL. Module code is only
read as text, never executed.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import validate_hooks  # noqa: E402
from validate_plugin import validate_inline_hooks  # noqa: E402

VALID_MODULE = """export function register(on) {
  on("tool.call", async ($, e, next) => next(e))
}
"""
NO_REGISTER_MODULE = "export const marker = 1\n"


def _plugin(tmp_path: Path, modules: list[str], files: dict[str, str]) -> Path:
    """Build tmp_path/plugin with hooks/hooks.json naming `modules` plus `files`
    (paths relative to the plugin root); return the hooks.json path."""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "probe", "version": "0.1.0"}))
    (root / "hooks").mkdir()
    hooks_json = root / "hooks" / "hooks.json"
    hooks_json.write_text(json.dumps({"modules": modules}))
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return hooks_json


def _blocking(hooks_json: Path) -> list[tuple[str, str]]:
    report = validate_hooks(hooks_json, hooks_json.parent.parent)
    return [(r.level, r.message) for r in report.results if r.level in ("MAJOR", "CRITICAL")]


def _warnings(hooks_json: Path) -> list[str]:
    report = validate_hooks(hooks_json, hooks_json.parent.parent)
    return [r.message for r in report.results if r.level == "WARNING"]


def _assert_native_pass(hooks_json: Path) -> None:
    # Native pass: no MAJOR/CRITICAL, and no leftover "bare name unprobed" WARNING.
    assert _blocking(hooks_json) == []
    assert not [w for w in _warnings(hooks_json) if "unprobed" in w or "does not start with" in w]


def test_p01_bare_name_passes(tmp_path):
    # p01: native success=true for "register.js" (resolved under hooks/).
    _assert_native_pass(_plugin(tmp_path, ["register.js"], {"hooks/register.js": VALID_MODULE}))


def test_p02_dotdot_inside_passes(tmp_path):
    # p02: native success=true for "./sub/../register.js" (stays inside the root).
    _assert_native_pass(_plugin(tmp_path, ["./sub/../register.js"], {"hooks/register.js": VALID_MODULE}))


def test_p03_subdir_noprefix_passes(tmp_path):
    # p03: native success=true for "lib/register.js" (no "./" prefix).
    _assert_native_pass(_plugin(tmp_path, ["lib/register.js"], {"hooks/lib/register.js": VALID_MODULE}))


def test_p58_parent_inside_passes(tmp_path):
    # p58: native success=true for "../lib/register.js" (plugin-root lib/).
    _assert_native_pass(_plugin(tmp_path, ["../lib/register.js"], {"lib/register.js": VALID_MODULE}))


def test_p53_bare_missing_major(tmp_path):
    # p53: native ERROR "no such file" for a bare name that does not exist.
    hooks_json = _plugin(tmp_path, ["nothere.js"], {})
    assert any(level == "MAJOR" and "does not exist" in msg for level, msg in _blocking(hooks_json))


def test_p54_bare_no_register_major(tmp_path):
    # p54: native ERROR "no export named register" for a bare-name module.
    hooks_json = _plugin(tmp_path, ["register.js"], {"hooks/register.js": NO_REGISTER_MODULE})
    assert any(level == "MAJOR" and "register" in msg for level, msg in _blocking(hooks_json))


def test_p59_parent_escape_major(tmp_path):
    # p59: native ERROR "The module path leaves the plugin directory".
    hooks_json = _plugin(tmp_path, ["../../outside.js"], {})
    (tmp_path / "outside.js").write_text(VALID_MODULE)
    assert any(level == "MAJOR" and "leaves the plugin directory" in msg for level, msg in _blocking(hooks_json))


def test_p32_absolute_major(tmp_path):
    # p32: native resolves "/tmp/register.js" under hooks/ ("hooks/tmp/register.js:
    # no such file"), so an absolute path never loads.
    hooks_json = _plugin(tmp_path, ["/tmp/register.js"], {"hooks/register.js": VALID_MODULE})
    assert any(
        level == "MAJOR" and "absolute" in msg and "relative to hooks.json" in msg
        for level, msg in _blocking(hooks_json)
    )


def test_p04_inline_manifest_modules_targeted_critical(tmp_path):
    # p04: `modules` inside the inline manifest `hooks` object; native: "unknown
    # hook event; entry ignored at runtime". CPV keeps CRITICAL but names the cause.
    root = tmp_path / "plugin"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "register.js").write_text(VALID_MODULE)
    manifest = {"name": "probe", "version": "0.1.0", "hooks": {"modules": ["./hooks/register.js"]}}
    report = ValidationReport()
    validate_inline_hooks(manifest, report, root)
    critical = [r.message for r in report.results if r.level == "CRITICAL"]
    assert any("modules" in m and "hooks FILE" in m and "will not load" in m for m in critical), critical
    assert not any("Valid events" in m for m in critical), critical
