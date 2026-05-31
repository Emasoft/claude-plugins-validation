"""Regression tests for the audit batch-11 fixes in ``scripts/validate_plugin.py``.

Each fixed finding is pinned with assertions that would FAIL against the
pre-fix code and PASS against the fix, plus a control that guards the
behavior the fix must NOT regress:

* HIGH — monitors inline array false-MAJOR 'must be a string path, got dict':
  the spec-valid inline array of monitor dicts no longer produces a bogus
  path-string MAJOR, while the string (path-reference) form keeps its
  ``./``-prefix and traversal checks, and every OTHER ``path_fields`` list
  (commands/agents/…) keeps the generic string-path checks.

* HIGH — ``lstrip('./')`` mangles dotfile paths: ``_mcp_server_keys`` and
  ``validate_monitors_entries`` resolve a ``.mcp.json`` / ``.monitors.json``
  dotfile reference correctly, so the channels cross-reference and the
  monitors-file contents are validated instead of silently skipped. A bogus
  channel ``server`` is now caught; non-dotfile paths still resolve.

* MED #46 — ``_locate_run_body`` wrong line for duplicate first lines: two
  ``run:`` blocks sharing the same first non-empty line resolve to DISTINCT
  source lines instead of both collapsing onto the first occurrence.

* LOW #133 — ``userConfig`` ``description`` is REQUIRED per the spec
  (plugins-reference.md:473). The previously-misleading inline comment is
  corrected; this test pins the corrected behavior so a future edit can't
  silently demote ``description`` to optional.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Make the scan cache inert for any code path that consults it (probes here
# call validators directly, but set it for parity with the audit protocol).
os.environ.setdefault("CPV_SCAN_CACHE", "0")

import validate_plugin as vp  # noqa: E402


def _majors(report: "vp.ValidationReport") -> list[str]:
    return [r.message for r in report.results if r.level == "MAJOR"]


def _plugin_with(manifest: dict, extra_files: dict[str, str] | None = None) -> Path:
    """Create a throwaway plugin dir with ``manifest`` and optional sibling files."""
    root = Path(tempfile.mkdtemp())
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


# ─────────────────────────────────────────────────────────────────────────────
# HIGH — monitors inline array must NOT be checked as path strings
# ─────────────────────────────────────────────────────────────────────────────
def test_monitors_inline_dict_array_no_path_string_major() -> None:
    """Inline array of monitor dicts is spec-valid → no 'must be a string path' MAJOR."""
    root = _plugin_with(
        {
            "name": "p",
            "version": "1.0.0",
            "description": "x",
            "monitors": [
                {"name": "deploy", "command": "echo hi", "description": "watch deploy"},
                {"name": "errors", "command": "echo bye", "description": "watch errors"},
            ],
        }
    )
    report = vp.ValidationReport()
    vp.validate_manifest(root, report)
    bogus = [m for m in _majors(report) if "must be a string path" in m and "monitors" in m]
    assert bogus == [], f"inline monitor dicts wrongly flagged as path strings: {bogus}"


def test_monitors_string_form_keeps_dotslash_prefix_check() -> None:
    """The path-string form of monitors still requires a ``./`` prefix (guard)."""
    root = _plugin_with({"name": "p", "version": "1.0.0", "description": "x", "monitors": "monitors.json"})
    report = vp.ValidationReport()
    vp.validate_manifest(root, report)
    assert any(
        "must start with './'" in m and "monitors" in m for m in _majors(report)
    ), "lost the ./-prefix check for the monitors path-string form"


def test_monitors_string_form_keeps_traversal_check() -> None:
    """The path-string form of monitors still rejects ``..`` traversal (guard)."""
    root = _plugin_with({"name": "p", "version": "1.0.0", "description": "x", "monitors": "./../evil.json"})
    report = vp.ValidationReport()
    vp.validate_manifest(root, report)
    assert any(
        "path-traversal" in m and "monitors" in m for m in _majors(report)
    ), "lost the traversal check for the monitors path-string form"


def test_other_path_fields_list_checks_unaffected() -> None:
    """Non-monitors path_fields (commands) keep the generic string-path checks (guard)."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x", "commands": ["nodotslash.md", 123]}
    )
    report = vp.ValidationReport()
    vp.validate_manifest(root, report)
    majors = _majors(report)
    assert any(
        "must be a string path" in m and "commands" in m for m in majors
    ), "commands list non-string element should still MAJOR"
    assert any(
        "must start with './'" in m and "commands" in m for m in majors
    ), "commands list ./-prefix check should still fire"


# ─────────────────────────────────────────────────────────────────────────────
# HIGH — lstrip('./') dotfile mangling in _mcp_server_keys + monitors loader
# ─────────────────────────────────────────────────────────────────────────────
def test_mcp_server_keys_resolves_dotfile_with_prefix() -> None:
    """``mcpServers: './.mcp.json'`` resolves the standard dotfile, not 'mcp.json'."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {".mcp.json": json.dumps({"mcpServers": {"realserver": {"command": "x"}}})},
    )
    keys = vp._mcp_server_keys({"mcpServers": "./.mcp.json"}, root)
    assert keys == {"realserver"}, f"dotfile mcp config not resolved (got {keys!r})"


def test_mcp_server_keys_resolves_bare_dotfile() -> None:
    """A bare ``.mcp.json`` (no ./ prefix) also resolves correctly."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {".mcp.json": json.dumps({"mcpServers": {"realserver": {"command": "x"}}})},
    )
    keys = vp._mcp_server_keys({"mcpServers": ".mcp.json"}, root)
    assert keys == {"realserver"}, f"bare dotfile mcp config not resolved (got {keys!r})"


def test_mcp_server_keys_resolves_nondotfile_control() -> None:
    """Non-dotfile ``./mcp.json`` still resolves (guard against over-correction)."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {"mcp.json": json.dumps({"mcpServers": {"realserver": {"command": "x"}}})},
    )
    keys = vp._mcp_server_keys({"mcpServers": "./mcp.json"}, root)
    assert keys == {"realserver"}, f"non-dotfile mcp config regressed (got {keys!r})"


def test_channels_bogus_server_caught_with_dotfile_mcp() -> None:
    """A channel referencing a non-existent server is CAUGHT when mcp is a dotfile."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {".mcp.json": json.dumps({"mcpServers": {"realserver": {"command": "x"}}})},
    )
    manifest = {"mcpServers": "./.mcp.json", "channels": [{"server": "NONEXISTENT"}]}
    report = vp.ValidationReport()
    vp.validate_channels_structure(manifest, root, report)
    assert any(
        "NONEXISTENT" in m and "does not match" in m for m in _majors(report)
    ), "bogus channel server slipped through (dotfile mcp config was silently skipped)"


def test_channels_valid_server_clean_with_dotfile_mcp() -> None:
    """A channel referencing a REAL server stays clean (benign side)."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {".mcp.json": json.dumps({"mcpServers": {"realserver": {"command": "x"}}})},
    )
    manifest = {"mcpServers": "./.mcp.json", "channels": [{"server": "realserver"}]}
    report = vp.ValidationReport()
    vp.validate_channels_structure(manifest, root, report)
    server_majors = [m for m in _majors(report) if "does not match" in m]
    assert server_majors == [], f"valid channel server wrongly rejected: {server_majors}"


def test_monitors_dotfile_contents_validated() -> None:
    """``monitors: './.monitors.json'`` loads the dotfile, so a bad monitor is caught."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {".monitors.json": json.dumps([{"name": "m1", "description": "d"}])},  # missing command
    )
    report = vp.ValidationReport()
    vp.validate_monitors_entries({"monitors": "./.monitors.json"}, root, report)
    assert any(
        "missing required 'command'" in m for m in _majors(report)
    ), "monitors dotfile contents silently skipped (lstrip mangled the dotfile name)"


def test_monitors_nondotfile_contents_validated_control() -> None:
    """Non-dotfile ``./monitors.json`` still validates correctly (guard)."""
    root = _plugin_with(
        {"name": "p", "version": "1.0.0", "description": "x"},
        {"monitors.json": json.dumps([{"name": "m1", "command": "c", "description": "d"}])},
    )
    report = vp.ValidationReport()
    vp.validate_monitors_entries({"monitors": "./monitors.json"}, root, report)
    assert _majors(report) == [], f"valid non-dotfile monitors regressed: {_majors(report)}"


# ─────────────────────────────────────────────────────────────────────────────
# MED #46 — _locate_run_body distinct lines for duplicate first lines
# ─────────────────────────────────────────────────────────────────────────────
def test_locate_run_body_duplicate_first_line_distinct_lines() -> None:
    """Two run: blocks with the same first non-empty line get DISTINCT line numbers."""
    content = (
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo start\n"
        "          ls foo/aaa.sh\n"
        "  b:\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo start\n"
        "          ls bar/bbb.sh\n"
    )
    blocks = vp._collect_run_blocks(content)
    lines = [ln for _, ln in blocks]
    # 'echo start' appears at source lines 5 and 10.
    assert lines == [5, 10], f"duplicate first-line run blocks collapsed to wrong lines: {lines}"


def test_locate_run_body_single_block_unchanged() -> None:
    """A single run: block still reports its correct line (no off-by-one regression)."""
    content = (
        "jobs:\n"
        "  a:\n"
        "    steps:\n"
        "      - run: |\n"
        "          echo only\n"
        "          ls x.sh\n"
    )
    blocks = vp._collect_run_blocks(content)
    assert [ln for _, ln in blocks] == [5], f"single block line wrong: {blocks}"


def test_locate_run_body_tuple_cursor_semantics() -> None:
    """``_locate_run_body`` advances its cursor and is cursor-stable when not found."""
    content = "line1\nNEEDLE here\nmore\nNEEDLE here\n"
    line_a, pos_a = vp._locate_run_body(content, "NEEDLE here")
    line_b, pos_b = vp._locate_run_body(content, "NEEDLE here", pos_a)
    assert (line_a, line_b) == (2, 4), f"cursor did not advance to second match: {line_a},{line_b}"
    line_c, pos_c = vp._locate_run_body(content, "ABSENT", pos_b)
    assert line_c == 1 and pos_c == pos_b, "not-found should fall back to line 1 and keep the cursor"


# ─────────────────────────────────────────────────────────────────────────────
# LOW #133 — userConfig.description is REQUIRED (pin corrected behavior)
# ─────────────────────────────────────────────────────────────────────────────
def test_userconfig_description_is_required() -> None:
    """A userConfig entry without ``description`` MUST MAJOR (spec: Required Yes)."""
    manifest = {"name": "p", "userConfig": {"api_token": {"type": "string", "title": "API token"}}}
    report = vp.ValidationReport()
    vp.validate_user_config_structure(manifest, report)
    assert any(
        "missing required sub-field 'description'" in m for m in _majors(report)
    ), "userConfig.description must be enforced as required"


def test_userconfig_with_description_clean() -> None:
    """A complete userConfig entry (type+title+description) is clean (benign side)."""
    manifest = {
        "name": "p",
        "userConfig": {"api_token": {"type": "string", "title": "API token", "description": "tok"}},
    }
    report = vp.ValidationReport()
    vp.validate_user_config_structure(manifest, report)
    assert _majors(report) == [], f"complete userConfig entry wrongly flagged: {_majors(report)}"
