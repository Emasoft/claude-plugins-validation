#!/usr/bin/env python3
"""Tests for issue #25 — canonical-pipeline publish.py / release.yml defects.

* Defect A — ``_plugin_in_remote_marketplace`` rejected ``url`` / ``git``
  marketplace source forms (falsely blocked Stage 5 of every downstream
  publish whose marketplace.json used the ``url`` shape).
* Defect B (regression guard) — neither CPV's ``publish.py`` nor the template
  emitted by ``gen_publish_py`` may push ``--tags`` (a ``--tags`` push exits
  non-zero on any diverged local tag and aborts the publish after main + the
  release tag were already pushed, leaving a partial-publish state).
* Defect C (regression guard) — neither copy may contain doubled-backslash
  literals (``"\\t"``, ``r"\\."``, ``\\\\\"``) where a single-backslash escape
  is intended. They are valid Python but evaluate to runtime garbage.
* Defect D — when the migration emits ``release.yml`` / ``ci.yml`` (which run
  ``uv run mypy / pytest / ruff`` under ``uv sync --extra dev``), CPV must
  alert when the pre-existing ``pyproject.toml``'s
  ``[project.optional-dependencies].dev`` lacks those tools. pyproject is
  user-owned (never force-overwritten), so the migration ALERTS rather than
  auto-edits.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402
from publish import _plugin_in_remote_marketplace as cpv_remote_check  # noqa: E402
from standardize_plugin import (  # noqa: E402
    _CANONICAL_DEV_EXTRA_TOOLS,
    AuditItem,
    _canonical_dev_extras_missing,
    fix_missing_files,
)


def _params(**overrides) -> PluginParams:
    """Build a minimal PluginParams suitable for any gen_* template."""
    kwargs = {
        "name": "test-plugin",
        "description": "test",
        "author": "X",
        "author_email": "x@x",
        "python_version": "3.12",
        "github_owner": "Emasoft",
        "marketplace": "test-marketplace",
    }
    kwargs.update(overrides)
    return PluginParams(**kwargs)


def _extract_template_function(template_text: str, fn_name: str):
    """Extract a top-level function definition from a template-string of
    Python source, compile it in isolation, and return the live function."""
    mod = ast.parse(template_text)
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            compiled = compile(
                ast.Module(body=[node], type_ignores=[]),
                "<gen_publish_py-template>",
                "exec",
            )
            ns: dict = {}
            exec(compiled, ns)
            return ns[fn_name]
    raise AssertionError(f"{fn_name!r} not found in template")


def _template_remote_check():
    """Live ``_plugin_in_remote_marketplace`` compiled from gen_publish_py output."""
    template = gen_publish_py(_params())
    return _extract_template_function(template, "_plugin_in_remote_marketplace")


def _both_copies():
    """Yield (label, function) pairs for the CPV copy and the template copy."""
    return (("cpv", cpv_remote_check), ("template", _template_remote_check()))


# ---------------------------------------------------------------------------
# Defect A — accept every documented remote source-object shape
# ---------------------------------------------------------------------------


def test_a_github_source_form_still_matches():
    """The {source:'github', repo:'owner/repo'} form (previously the only accepted one) still works."""
    mkt = {"plugins": [{"name": "p", "source": {"source": "github", "repo": "Emasoft/p"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is True, label
        assert fn(mkt, "p", "Other/p") is False, label


def test_a_url_form_with_git_suffix_matches():
    """{source:'url', url:'https://github.com/owner/repo.git'} matches owner/repo (issue #25 Defect A)."""
    mkt = {"plugins": [{"name": "p", "source": {"source": "url", "url": "https://github.com/Emasoft/p.git"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is True, label


def test_a_url_form_without_git_suffix_matches():
    """{source:'url', url:'https://github.com/owner/repo'} (no .git suffix) also matches."""
    mkt = {"plugins": [{"name": "p", "source": {"source": "url", "url": "https://github.com/Emasoft/p"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is True, label


def test_a_git_ssh_form_matches():
    """{source:'git', url:'git@github.com:owner/repo.git'} (SSH) matches owner/repo."""
    mkt = {"plugins": [{"name": "p", "source": {"source": "git", "url": "git@github.com:Emasoft/p.git"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is True, label


def test_a_url_repo_mismatch_returns_false():
    """A url form pointing at a different slug returns False."""
    mkt = {"plugins": [{"name": "p", "source": {"source": "url", "url": "https://github.com/Other/p.git"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is False, label


def test_a_expected_repo_none_accepts_any_remote_form():
    """expected_repo=None accepts any matching-name remote-source entry, regardless of shape."""
    for source in (
        {"source": "github", "repo": "Emasoft/p"},
        {"source": "url", "url": "https://github.com/Emasoft/p.git"},
        {"source": "git", "url": "git@github.com:Emasoft/p.git"},
    ):
        mkt = {"plugins": [{"name": "p", "source": source}]}
        for label, fn in _both_copies():
            assert fn(mkt, "p", None) is True, f"{label}: {source}"


def test_a_bare_string_source_is_local_not_remote():
    """A bare string source ('./plugins/foo') is a local directory entry, not a remote registration."""
    mkt = {"plugins": [{"name": "p", "source": "./plugins/p"}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is False, label


def test_a_legacy_type_field_for_github_still_matches():
    """{type:'github', repo:...} (alt shape with 'type' instead of 'source') is also accepted for github."""
    mkt = {"plugins": [{"name": "p", "source": {"type": "github", "repo": "Emasoft/p"}}]}
    for label, fn in _both_copies():
        assert fn(mkt, "p", "Emasoft/p") is True, label


# ---------------------------------------------------------------------------
# Defect B — no `git push … --tags` in publish.py or template
# ---------------------------------------------------------------------------


def _scan_for_push_all_tags(text: str) -> list[str]:
    """Return source lines that push --tags (excluding docstring / comment lines)."""
    hits: list[str] = []
    in_triple = False
    for line in text.splitlines():
        s = line.strip()
        if s.count('"""') == 1:
            in_triple = not in_triple
            continue
        if in_triple or s.startswith("#"):
            continue
        if "push" in line and "--tags" in line:
            hits.append(line)
    return hits


def test_b_no_push_all_tags_in_cpv_publish_py():
    """CPV's publish.py must NOT push --tags — one diverged local tag aborts the whole publish."""
    src = (scripts_dir / "publish.py").read_text()
    assert _scan_for_push_all_tags(src) == [], _scan_for_push_all_tags(src)


def test_b_no_push_all_tags_in_gen_publish_py_template():
    """gen_publish_py template must NOT emit `git push … --tags`."""
    template = gen_publish_py(_params())
    assert _scan_for_push_all_tags(template) == [], _scan_for_push_all_tags(template)


# ---------------------------------------------------------------------------
# Defect C — no doubled-backslash literals in publish.py or template
# ---------------------------------------------------------------------------


# Source-level bug patterns (each is a raw string representing the EXACT
# character sequence that should not appear in code-bearing lines).
_BUG_PATTERNS_C: tuple[str, ...] = (
    r'"\\t"',
    r"'\\t'",
    r'"\\n"',
    r"'\\n'",
    r'r"\\.',
    r"r'\\.",
    r"\\\"",
)


def _scan_for_doubled_backslash(text: str) -> list[str]:
    """Return (pattern, line) tuples for bug patterns found in code lines."""
    hits: list[str] = []
    in_triple = False
    for line in text.splitlines():
        s = line.strip()
        if s.count('"""') == 1:
            in_triple = not in_triple
            continue
        if in_triple or s.startswith("#"):
            continue
        for pat in _BUG_PATTERNS_C:
            if pat in line:
                hits.append(f"{pat!r} in {line!r}")
    return hits


def test_c_no_doubled_backslash_literals_in_cpv_publish_py():
    """CPV's publish.py must not contain doubled-backslash literal bugs (Defect C class)."""
    src = (scripts_dir / "publish.py").read_text()
    hits = _scan_for_doubled_backslash(src)
    assert hits == [], hits


def test_c_no_doubled_backslash_literals_in_gen_publish_py_template():
    """gen_publish_py template must not contain doubled-backslash literal bugs."""
    template = gen_publish_py(_params())
    hits = _scan_for_doubled_backslash(template)
    assert hits == [], hits


# ---------------------------------------------------------------------------
# Defect D — pyproject dev-extras reconciliation alert
# ---------------------------------------------------------------------------


def test_d_canonical_dev_extras_missing_when_no_pyproject(tmp_path):
    """No pyproject.toml → no missing-tools claim (the user has no Python toolchain to reconcile)."""
    assert _canonical_dev_extras_missing(tmp_path) == []


def test_d_canonical_dev_extras_missing_when_dev_extra_empty(tmp_path):
    """An empty dev extra reports every canonical tool as missing."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0.1.0"\n[project.optional-dependencies]\ndev = []\n'
    )
    missing = _canonical_dev_extras_missing(tmp_path)
    for tool in _CANONICAL_DEV_EXTRA_TOOLS:
        assert tool in missing, missing


def test_d_canonical_dev_extras_missing_partial_dev(tmp_path):
    """A dev extra with only `ruff` reports `mypy` + `pytest` as missing."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0.1.0"\n[project.optional-dependencies]\ndev = ["ruff>=0.14.0"]\n'
    )
    missing = _canonical_dev_extras_missing(tmp_path)
    assert "ruff" not in missing
    assert "mypy" in missing
    assert "pytest" in missing


def test_d_canonical_dev_extras_complete_dev_reports_nothing(tmp_path):
    """A dev extra containing every canonical tool reports nothing missing."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'dev = ["mypy>=1.0", "pytest>=8.0.0", "ruff>=0.14.0"]\n'
    )
    assert _canonical_dev_extras_missing(tmp_path) == []


def test_d_canonical_dev_extras_pep503_case_insensitive(tmp_path):
    """Tool names are matched case-insensitively per PEP-503 (`MyPy` → `mypy`)."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "0.1.0"\n'
        "[project.optional-dependencies]\n"
        'dev = ["MyPy>=1.0", "PyTest>=8.0.0", "Ruff>=0.14.0"]\n'
    )
    assert _canonical_dev_extras_missing(tmp_path) == []


def _make_plugin_dir(tmp_path: Path, pyproject_dev: list[str]) -> Path:
    """Lay down a minimal plugin tree with a pyproject.toml whose dev extra
    is exactly `pyproject_dev` (list of PEP-508 specs)."""
    root = tmp_path / "test-plugin"
    root.mkdir()
    cp = root / ".claude-plugin"
    cp.mkdir()
    (cp / "plugin.json").write_text(
        json.dumps(
            {
                "name": "test-plugin",
                "version": "0.1.0",
                "description": "test",
                "author": "X",
            },
            indent=2,
        )
    )
    dev_list = "[" + ", ".join(f'"{s}"' for s in pyproject_dev) + "]"
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "test-plugin"\nversion = "0.1.0"\n[project.optional-dependencies]\ndev = {dev_list}\n'
    )
    return root


def test_d_dev_extra_provisioned_when_release_yml_emitted_and_dev_extras_lacking(tmp_path, capsys):
    """Issue #142 #2: emitting release.yml when pyproject's dev lacks tools now
    AUTO-PROVISIONS them under --fix (superseding the old issue-#25 [ACTION
    REQUIRED] warn-only behaviour, which moved to the AUDIT path)."""
    import tomllib

    root = _make_plugin_dir(tmp_path, pyproject_dev=["ruff>=0.14.0"])  # no mypy, no pytest
    # Mark release.yml as MISSING so fix_missing_files emits it.
    results = [
        AuditItem(
            "files",
            ".github/workflows/release.yml",
            "MISSING",
            "release workflow missing",
        )
    ]
    fix_missing_files(root, results, dry_run=False)
    out = capsys.readouterr().out
    # The fix path now provisions (it no longer prints the old warn block).
    assert "[dev-extra]" in out, out
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    names = {s.split(">")[0].split("=")[0].split("<")[0].split("[")[0].strip().lower() for s in dev}
    assert {"pytest", "ruff", "mypy"} <= names, dev
    # Existing pinned entry is preserved (augment, not replace).
    assert "ruff>=0.14.0" in (root / "pyproject.toml").read_text(encoding="utf-8")


def test_d_no_alert_when_pyproject_dev_extras_complete(tmp_path, capsys):
    """A complete dev extra produces NO [ACTION REQUIRED] block even when release.yml is emitted."""
    root = _make_plugin_dir(
        tmp_path,
        pyproject_dev=["mypy>=1.0", "pytest>=8.0.0", "ruff>=0.14.0"],
    )
    results = [
        AuditItem(
            "files",
            ".github/workflows/release.yml",
            "MISSING",
            "release workflow missing",
        )
    ]
    fix_missing_files(root, results, dry_run=False)
    out = capsys.readouterr().out
    assert "pyproject.toml dev extras incomplete" not in out, out


def test_d_no_alert_when_no_workflow_files_emitted(tmp_path, capsys):
    """If no workflow file is emitted (only e.g. cliff.toml), no dev-extras alert fires."""
    root = _make_plugin_dir(tmp_path, pyproject_dev=[])  # empty dev, but no workflow emission
    results = [
        AuditItem("files", "cliff.toml", "MISSING", "changelog config missing"),
    ]
    fix_missing_files(root, results, dry_run=False)
    out = capsys.readouterr().out
    assert "pyproject.toml dev extras incomplete" not in out, out
