#!/usr/bin/env python3
"""Two-sided regression tests for issue #75 class 3.

Issue #62's `sys.path.insert` sibling resolver only recovered literal path
segments that live INSIDE the insert call. The very common janitor pattern
builds the directory into a VARIABLE first (an assignment / list-append /
``candidates`` loop) and inserts the NAME — so the literal segments are not in
the insert argument and #62 missed them, flagging local siblings
(``user_mem_lib`` / ``state``) as missing PyPI deps.

The fix adds a second resolution pass that fires ONLY when at least one insert
has a non-literal (variable) argument; it harvests the full ordered literal
``Path()/seg/...`` and ``*.join(...)`` chains module-wide and resolves each
against the anchors. The FN-safety invariant is UNCHANGED: a module name is
suppressed only if a matching ``<name>.py`` / ``<name>/__init__.py`` physically
EXISTS on disk under an anchor, so a genuinely-missing PyPI dep stays flagged.

Every test builds a real on-disk plugin tree (the resolver checks the
filesystem) and is two-sided — the local sibling CLEARS AND a genuine
third-party import with no local resolution STAYS flagged.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from validate_hook import (  # noqa: E402
    ScriptRef,
    ValidationReport,
    detect_python_third_party_imports,
    reconcile_python_runtime_deps,
)


def _plugin_with_sibling(sibling_name: str) -> Path:
    """A plugin tree with ``scripts/hooks/`` (empty) and ``scripts/lib/<sibling>.py``."""
    root = Path(tempfile.mkdtemp(prefix="cpv-75-"))
    (root / "scripts" / "hooks").mkdir(parents=True)
    (root / "scripts" / "lib").mkdir(parents=True)
    (root / "scripts" / "lib" / f"{sibling_name}.py").write_text("VALUE = 1\n")
    return root


# ---------------------------------------------------------------------------
# Test A — the #75 FP clears: variable (candidates-loop) insert.
# ---------------------------------------------------------------------------
def test_a_candidates_loop_variable_insert_sibling_suppressed() -> None:
    """A `candidates.append(Path(root)/"scripts"/"lib"); for d: insert(str(d))`
    pattern resolves the local sibling — it is NOT a missing PyPI dep."""
    root = _plugin_with_sibling("sib")
    hook = root / "scripts" / "hooks" / "h.py"
    hook.write_text(
        "import os\nimport sys\nfrom pathlib import Path\n"
        "candidates = []\n"
        'candidates.append(Path(os.environ["CLAUDE_PLUGIN_ROOT"]) / "scripts" / "lib")\n'
        'candidates.append(Path(__file__).resolve().parent.parent / "lib")\n'
        "for d in candidates:\n"
        '    if (d / "sib.py").is_file():\n'
        "        sys.path.insert(0, str(d))\n"
        "        break\n"
        "import sib\n"
    )
    res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
    assert "sib" not in res  # local sibling via the variable insert — suppressed


# ---------------------------------------------------------------------------
# Test B — FN-safety: a genuinely-missing PyPI dep still fires.
# ---------------------------------------------------------------------------
def test_b_missing_pypi_dep_still_flagged_with_variable_insert() -> None:
    """Even with a variable insert present, an import with NO local sibling on
    disk stays flagged — the on-disk existence gate is load-bearing."""
    root = _plugin_with_sibling("sib")
    hook = root / "scripts" / "hooks" / "h.py"
    # Same candidates-loop shape (so pass-2 fires) but import a name that has
    # no scripts/lib/<name>.py on disk → must remain "third-party".
    hook.write_text(
        "import os\nimport sys\nfrom pathlib import Path\n"
        "candidates = []\n"
        'candidates.append(Path(os.environ["CLAUDE_PLUGIN_ROOT"]) / "scripts" / "lib")\n'
        "for d in candidates:\n"
        '    if (d / "sib.py").is_file():\n'
        "        sys.path.insert(0, str(d))\n"
        "import nonexistent_pkg_xyz\n"
    )
    res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
    assert "nonexistent_pkg_xyz" in res  # genuinely missing — still flagged


# ---------------------------------------------------------------------------
# Test C — variant: os.path.join form of the variable-built path.
# ---------------------------------------------------------------------------
def test_c_os_path_join_variable_insert_sibling_suppressed() -> None:
    """`lib_dir = os.path.join(root, "scripts", "lib"); insert(lib_dir); import sib`
    is resolved via the join branch of pass-2."""
    root = _plugin_with_sibling("sib")
    hook = root / "scripts" / "hooks" / "h.py"
    hook.write_text(
        "import os\nimport sys\n"
        'root = os.environ["CLAUDE_PLUGIN_ROOT"]\n'
        'lib_dir = os.path.join(root, "scripts", "lib")\n'
        "sys.path.insert(0, lib_dir)\n"
        "import sib\n"
    )
    res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
    assert "sib" not in res  # resolved via os.path.join chain


def test_c2_os_path_join_variable_insert_missing_dep_still_flagged() -> None:
    """The join variant is also FN-safe: a non-existent sibling stays flagged."""
    root = _plugin_with_sibling("sib")
    hook = root / "scripts" / "hooks" / "h.py"
    hook.write_text(
        "import os\nimport sys\n"
        'root = os.environ["CLAUDE_PLUGIN_ROOT"]\n'
        'lib_dir = os.path.join(root, "scripts", "lib")\n'
        "sys.path.insert(0, lib_dir)\n"
        "import nonexistent_pkg_xyz\n"
    )
    res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
    assert "nonexistent_pkg_xyz" in res


# ---------------------------------------------------------------------------
# Test D — regression guard for #62: literal-in-insert path untouched.
# ---------------------------------------------------------------------------
def test_d_issue62_literal_in_insert_still_resolves() -> None:
    """The #62 case (literals directly in the insert call) still resolves; the
    new variable-insert pass does not interfere (no variable insert here → it
    stays skipped)."""
    root = Path(tempfile.mkdtemp(prefix="cpv-75-d-"))
    (root / "scripts" / "hooks").mkdir(parents=True)
    (root / "scripts" / "vendored").mkdir(parents=True)
    (root / "scripts" / "vendored" / "vmod.py").write_text("x = 1\n")
    hook = root / "scripts" / "hooks" / "h.py"
    hook.write_text(
        "import sys\nfrom pathlib import Path\nroot = '/x'\n"
        'sys.path.insert(0, str(Path(root) / "scripts" / "vendored"))\n'
        "import vmod\n"
        "import requests\n"
    )
    res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
    assert "vmod" not in res  # literal-in-insert sibling (#62) — still cleared
    assert "requests" in res  # genuine third-party — still flagged


# ---------------------------------------------------------------------------
# Test E — end-to-end through reconcile_python_runtime_deps.
# ---------------------------------------------------------------------------
_PEP723_NO_DEPS = "# /// script\n# requires-python = \">=3.10\"\n# dependencies = []\n# ///\n"


def _major_messages(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "MAJOR"]


def test_e_end_to_end_sibling_not_major_missing_dep_is_major() -> None:
    """The full uv-run-script reconciliation path: a no-deps PEP-723 hook whose
    only import is a candidates-loop local sibling produces NO `missing
    declarations` MAJOR, while a sibling-less `import requests` in another hook
    DOES."""
    root = _plugin_with_sibling("user_mem_lib")

    # Hook 1: PEP-723 declares no deps; imports a local sibling via variable insert.
    sib_hook = root / "scripts" / "hooks" / "hook_sibling.py"
    sib_hook.write_text(
        _PEP723_NO_DEPS
        + "import os\nimport sys\nfrom pathlib import Path\n"
        "candidates = []\n"
        'candidates.append(Path(os.environ.get("CLAUDE_PLUGIN_ROOT", "")) / "scripts" / "lib")\n'
        'candidates.append(Path(__file__).resolve().parent.parent / "lib")\n'
        "for d in candidates:\n"
        '    if (d / "user_mem_lib.py").is_file():\n'
        "        sys.path.insert(0, str(d))\n"
        "        break\n"
        "import user_mem_lib\n"
    )
    sib_report = ValidationReport()
    reconcile_python_runtime_deps(
        ScriptRef(path=sib_hook, invocation_mode="uv-run-script"),
        root,
        None,
        sib_report,
    )
    sib_majors = _major_messages(sib_report)
    assert not any("missing declarations" in m for m in sib_majors), (
        f"Local sibling wrongly reported as missing dep: {sib_majors}"
    )

    # Hook 2: PEP-723 declares no deps; imports a genuine PyPI package, no sibling.
    miss_hook = root / "scripts" / "hooks" / "hook_missing.py"
    miss_hook.write_text(_PEP723_NO_DEPS + "import requests\n")
    miss_report = ValidationReport()
    reconcile_python_runtime_deps(
        ScriptRef(path=miss_hook, invocation_mode="uv-run-script"),
        root,
        None,
        miss_report,
    )
    miss_majors = _major_messages(miss_report)
    assert any("requests" in m and "missing declarations" in m for m in miss_majors), (
        f"Genuinely-missing PyPI dep was NOT flagged: {miss_majors}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
