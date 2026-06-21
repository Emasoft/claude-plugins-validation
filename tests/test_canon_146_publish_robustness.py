#!/usr/bin/env python3
"""Two-sided tests for GitHub issue #146 — two publish.py robustness bugs,
fixed in BOTH places: CPV's own ``scripts/publish.py`` AND the canonical
TEMPLATE emitted by ``generate_plugin_repo.gen_publish_py``.

F9 (LOW) — ``run()`` never caught ``subprocess.TimeoutExpired``. A command
that ran past its ``timeout`` died with a raw traceback instead of the styled
fail-fast exit the rest of the gate uses. Both copies now catch it and
``sys.exit(1)`` with a one-line message.

F3 (MEDIUM) — the pyproject version read/write used a line-anchored whole-file
first-match for ``version = "..."``. If a ``[tool.X]`` table carrying its OWN
top-level ``version`` (e.g. ``[tool.commitizen]``) appears BEFORE ``[project]``,
the WRONG version is read/written. Both copies now operate on the ``[project]``
table body when present, and FALL BACK to the legacy whole-file first-match
when there is no ``[project]`` table (poetry-style layouts keep working).

Every guard is TWO-SIDED:
 * F9: the fixed catch is asserted PRESENT (template text + own-copy source),
   and the own-copy ``run()`` is exercised so a timeout becomes SystemExit(1).
 * F3: a ``[tool.X].version`` ABOVE ``[project]`` is proven NOT chosen; a
   standard ``[project]``-only file still works; a poetry-style file with NO
   ``[project]`` table still falls back to today's behavior.

The own-copy ``run()`` is tested behaviorally (not just source-contains): we
monkeypatch ``publish.subprocess.run`` to raise ``TimeoutExpired`` and assert
``run()`` exits 1 — hermetic and instant (no real 600 s wait).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO_ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tests"))

import publish  # noqa: E402
from generate_plugin_repo import gen_publish_py  # noqa: E402
from test_canon_142_genrepo import _params  # noqa: E402

# ── template-function extraction (mirrors tests/test_issue_25_publish_defects) ─


def _extract_template_function(template_text: str, fn_name: str, extra_names: set[str] | None = None):
    """Compile a top-level function out of the generated template string and
    return the live callable. Sibling helper functions it references (e.g.
    ``_project_block``) are pulled in by naming them in ``extra_names``; the
    stdlib names it uses (``re``, ``json``, ``Path``) are seeded into the exec
    namespace below.
    """
    mod = ast.parse(template_text)
    wanted = {fn_name}
    if extra_names:
        wanted |= extra_names
    nodes: list[ast.FunctionDef] = [
        n for n in mod.body
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    if not any(n.name == fn_name for n in nodes):
        raise AssertionError(f"{fn_name!r} not found in template")
    compiled = compile(
        ast.Module(body=cast("list[ast.stmt]", nodes), type_ignores=[]),
        "<gen_publish_py-template>",
        "exec",
    )
    # The template functions reference stdlib modules at call time; the
    # generated file imports them, but our isolated exec namespace must seed
    # them so the compiled functions resolve `re` / `json` / `Path`.
    import json as _json
    import re as _re

    ns: dict = {"re": _re, "json": _json, "Path": Path}
    exec(compiled, ns)
    return ns[fn_name]


def _template_update_pyproject():
    """Live template ``update_pyproject_toml`` (carries its sibling _project_block)."""
    return _extract_template_function(
        gen_publish_py(_params()), "update_pyproject_toml", {"_project_block"}
    )


def _template_check_consistency():
    """Live template ``check_version_consistency`` (carries its sibling _project_block)."""
    return _extract_template_function(
        gen_publish_py(_params()), "check_version_consistency", {"_project_block"}
    )


# ── F9 — TimeoutExpired is caught in run() (both copies) ─────────────────────


def test_f9_template_run_catches_timeout_expired() -> None:
    """The GENERATED template's run() catches subprocess.TimeoutExpired."""
    text = gen_publish_py(_params())
    # The catch must live in run(), not just somewhere in the file.
    run_src = _function_source(text, "run")
    assert "except subprocess.TimeoutExpired" in run_src, run_src
    assert "timed out" in run_src.lower(), run_src


def test_f9_own_copy_source_catches_timeout_expired() -> None:
    """CPV's own publish.py run() catches subprocess.TimeoutExpired in source."""
    src = (SCRIPTS / "publish.py").read_text(encoding="utf-8")
    run_src = _function_source(src, "run")
    assert "except subprocess.TimeoutExpired" in run_src, run_src
    assert "timed out" in run_src.lower(), run_src


def test_f9_own_copy_run_exits_on_timeout(monkeypatch, tmp_path, capsys) -> None:
    """BEHAVIORAL: a timeout in the own-copy run() becomes a clean SystemExit(1),
    not a propagated TimeoutExpired traceback."""

    def _fake_run(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["sleep", "999"], timeout=600)

    monkeypatch.setattr(publish.subprocess, "run", _fake_run)
    with pytest.raises(SystemExit) as exc:
        publish.run(["sleep", "999"], tmp_path)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "timed out" in err.lower(), err


def _function_source(module_text: str, fn_name: str) -> str:
    """Return the exact source segment of the named top-level function."""
    mod = ast.parse(module_text)
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            seg = ast.get_source_segment(module_text, node)
            if seg is not None:
                return seg
    raise AssertionError(f"{fn_name!r} not found")


# ── F3 — pyproject version is [project]-scoped, with poetry fallback ──────────

# A pyproject where a [tool.X] table with its OWN top-level version precedes
# [project]. The version-aware code MUST pick the [project] one (1.0.0), never
# the [tool.commitizen] decoy (9.9.9).
_TOOL_BEFORE_PROJECT = """\
[tool.commitizen]
name = "cz_conventional_commits"
version = "9.9.9"
tag_format = "v$version"

[project]
name = "demo"
version = "1.0.0"
description = "x"
"""

# Standard layout — only [project] carries a version.
_PROJECT_ONLY = """\
[project]
name = "demo"
version = "1.0.0"
description = "x"
"""

# Poetry-style — NO [project] table; version lives under [tool.poetry]. Both
# copies must fall back to the legacy whole-file first-match (today's behavior).
_POETRY_NO_PROJECT = """\
[tool.poetry]
name = "demo"
version = "1.0.0"
description = "x"
"""


def _write(tmp_path: Path, content: str) -> Path:
    root = tmp_path
    (root / "pyproject.toml").write_text(content, encoding="utf-8")
    return root


# --- read side (check_version_consistency) -----------------------------------


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.check_version_consistency), ("template", _template_check_consistency())],
)
def test_f3_read_picks_project_not_tool_table(label, fn, tmp_path) -> None:
    """The version READ comes from [project] (1.0.0), NOT a [tool.X] table above it."""
    root = _write(tmp_path, _TOOL_BEFORE_PROJECT)
    # Make plugin.json agree with [project] so 'all match' proves which version
    # was read for pyproject (9.9.9 would surface as a mismatch).
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0"}\n', encoding="utf-8"
    )
    ok, msg = fn(root)
    assert ok is True, f"{label}: {msg}"
    assert "1.0.0" in msg, f"{label}: {msg}"
    assert "9.9.9" not in msg, f"{label}: read the [tool] decoy version — {msg}"


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.check_version_consistency), ("template", _template_check_consistency())],
)
def test_f3_read_project_only_unchanged(label, fn, tmp_path) -> None:
    """A standard [project]-only file reads 1.0.0 (behavior unchanged)."""
    root = _write(tmp_path, _PROJECT_ONLY)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0"}\n', encoding="utf-8"
    )
    ok, msg = fn(root)
    assert ok is True, f"{label}: {msg}"
    assert "1.0.0" in msg, f"{label}: {msg}"


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.check_version_consistency), ("template", _template_check_consistency())],
)
def test_f3_read_poetry_fallback(label, fn, tmp_path) -> None:
    """No [project] table → fall back to whole-file first-match (poetry version)."""
    root = _write(tmp_path, _POETRY_NO_PROJECT)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo", "version": "1.0.0"}\n', encoding="utf-8"
    )
    ok, msg = fn(root)
    assert ok is True, f"{label}: {msg}"
    assert "1.0.0" in msg, f"{label}: poetry fallback failed — {msg}"


# --- write side (update_pyproject_toml) ---------------------------------------


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.update_pyproject_toml), ("template", _template_update_pyproject())],
)
def test_f3_write_targets_project_not_tool_table(label, fn, tmp_path) -> None:
    """The version WRITE updates [project] (1.0.0→2.0.0), leaving [tool.X] at 9.9.9."""
    root = _write(tmp_path, _TOOL_BEFORE_PROJECT)
    ok, msg = fn(root, "2.0.0")
    assert ok is True, f"{label}: {msg}"
    out = (root / "pyproject.toml").read_text(encoding="utf-8")
    # [project] version bumped; the [tool.commitizen] decoy is untouched.
    assert 'version = "2.0.0"' in out, f"{label}: {out}"
    assert 'version = "9.9.9"' in out, f"{label}: clobbered the [tool] table — {out}"
    assert 'version = "1.0.0"' not in out, f"{label}: [project] not bumped — {out}"


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.update_pyproject_toml), ("template", _template_update_pyproject())],
)
def test_f3_write_project_only_unchanged(label, fn, tmp_path) -> None:
    """A standard [project]-only file is bumped exactly once (behavior unchanged)."""
    root = _write(tmp_path, _PROJECT_ONLY)
    ok, msg = fn(root, "2.0.0")
    assert ok is True, f"{label}: {msg}"
    out = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "2.0.0"' in out, f"{label}: {out}"
    assert 'version = "1.0.0"' not in out, f"{label}: {out}"


@pytest.mark.parametrize(
    "label, fn",
    [("own", publish.update_pyproject_toml), ("template", _template_update_pyproject())],
)
def test_f3_write_poetry_fallback(label, fn, tmp_path) -> None:
    """No [project] table → fall back to whole-file first-match (poetry version bumped)."""
    root = _write(tmp_path, _POETRY_NO_PROJECT)
    ok, msg = fn(root, "2.0.0")
    assert ok is True, f"{label}: {msg}"
    out = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "2.0.0"' in out, f"{label}: poetry fallback write failed — {out}"
    assert 'version = "1.0.0"' not in out, f"{label}: {out}"


# --- the helper itself --------------------------------------------------------


def test_f3_project_block_helper_own_and_template() -> None:
    """_project_block returns the [project] body span when present, None when absent."""
    own_block = publish._project_block(_TOOL_BEFORE_PROJECT)
    assert own_block is not None
    lo, hi = own_block
    body = _TOOL_BEFORE_PROJECT[lo:hi]
    assert 'version = "1.0.0"' in body and 'version = "9.9.9"' not in body, body
    assert publish._project_block(_POETRY_NO_PROJECT) is None

    # Same contract for the template's copy of the helper.
    tmpl_block = _extract_template_function(
        gen_publish_py(_params()), "_project_block"
    )
    blk = tmpl_block(_TOOL_BEFORE_PROJECT)
    assert blk is not None
    body2 = _TOOL_BEFORE_PROJECT[blk[0]:blk[1]]
    assert 'version = "1.0.0"' in body2 and 'version = "9.9.9"' not in body2, body2
    assert tmpl_block(_POETRY_NO_PROJECT) is None
