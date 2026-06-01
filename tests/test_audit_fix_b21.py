#!/usr/bin/env python3
"""Regression tests for the full-audit batch B21 fixes.

Covers five findings across three scripts:

  * #17  setup_branch_rules.list_installed_apps — `gh api --paginate --jq`
         emits NDJSON (one array per page); the old bare `json.loads` + bare
         `except: pass` silently dropped EVERY installed app on multi-page
         accounts. The fix parses line-by-line.
  * #23  (HIGH) setup_plugin_pipeline._fix_hooks fabricated a fake `.git/hooks`
         tree on a non-git project, which then masked the "Not a git
         repository" CRITICAL and flipped is_valid to True.
  * #81  setup_plugin_pipeline default setup mode returned 0 even when fix()
         left the project invalid.
  * #18  standardize_plugin.save_report_to_file dropped all "drift" findings
         (category absent from its category_titles).
  * #82  standardize_plugin.audit_gitignore gave a false PASS via a substring
         containment check (`.env.example` satisfied `.env`). This is a
         security-relevant false negative, so it is tested TWO-SIDED: the
         attacker-shaped benign-substring line MUST NOT mark the entry present,
         while a genuine entry (or a broader glob that truly covers it) MUST.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import setup_branch_rules as sbr  # noqa: E402
import setup_plugin_pipeline as spp  # noqa: E402
import standardize_plugin as sp  # noqa: E402

# ── helpers ────────────────────────────────────────────────────────────────


def _make_plugin(tmp_path: Path, *, with_git: bool) -> Path:
    """A minimal valid single-plugin project, optionally with a real .git."""
    d = tmp_path / ("git" if with_git else "nogit")
    (d / ".claude-plugin").mkdir(parents=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "demo", "version": "0.1.0"}), encoding="utf-8"
    )
    if with_git:
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    return d


# ── #17 setup_branch_rules: paginated --jq NDJSON parsing ───────────────────


def test_paginated_jq_arrays_flattens_multi_page_ndjson() -> None:
    """Multi-page `--paginate --jq .installations` NDJSON must NOT be dropped."""
    # Two pages: each page is one JSON array; gh joins them with a newline.
    two_page = '[{"app_id": 1}, {"app_id": 2}]\n[{"app_id": 3}]\n'
    apps = sbr._parse_paginated_jq_arrays(two_page, source="user")
    assert [a["app_id"] for a in apps] == [1, 2, 3]


def test_paginated_jq_arrays_single_page_unchanged() -> None:
    """A single-page response still parses correctly (no regression)."""
    one_page = '[{"app_id": 7}, {"app_id": 8}]\n'
    apps = sbr._parse_paginated_jq_arrays(one_page, source="org")
    assert [a["app_id"] for a in apps] == [7, 8]


def test_paginated_jq_arrays_guard_bug_would_have_dropped_multipage() -> None:
    """Guard: the original bare json.loads truly fails on multi-page NDJSON."""
    two_page = '[{"app_id": 1}]\n[{"app_id": 2}]\n'
    with pytest.raises(json.JSONDecodeError):
        json.loads(two_page)  # the old code path — proves the bug was real
    # The fix recovers every app the old code would have silently lost.
    assert len(sbr._parse_paginated_jq_arrays(two_page, source="user")) == 2


def test_paginated_jq_arrays_skips_garbage_line_keeps_valid() -> None:
    """A malformed page line is skipped, valid pages still collected."""
    mixed = '[{"app_id": 1}]\nnot json at all\n[{"app_id": 2}]\n'
    apps = sbr._parse_paginated_jq_arrays(mixed, source="user")
    assert sorted(a["app_id"] for a in apps) == [1, 2]


# ── #23 (HIGH) setup_plugin_pipeline: no fake .git on a non-git project ─────


def test_non_git_project_does_not_fabricate_dot_git(tmp_path: Path) -> None:
    """fix() on a non-git plugin must NOT create a .git directory."""
    d = _make_plugin(tmp_path, with_git=False)
    setup = spp.PipelineSetup(d, dry_run=False, verbose=False)
    setup.validate()
    setup.fix()
    assert not (d / ".git").exists(), "fix() fabricated a fake .git tree"


def test_non_git_critical_not_masked_after_fix(tmp_path: Path) -> None:
    """The 'Not a git repository' CRITICAL must survive a fix()+revalidate."""
    d = _make_plugin(tmp_path, with_git=False)
    setup = spp.PipelineSetup(d, dry_run=False, verbose=False)
    setup.validate()
    setup.fix()
    setup.status = spp.PipelineStatus(project_type=spp.ProjectType.UNKNOWN, project_path=d)
    status = setup.validate()
    assert not status.is_valid
    assert any(i.component == "git" for i in status.issues), "git CRITICAL was masked"


def test_non_git_validate_does_not_emit_spurious_hook_majors(tmp_path: Path) -> None:
    """Without a real .git, hook checks are meaningless and must not fire."""
    d = _make_plugin(tmp_path, with_git=False)
    setup = spp.PipelineSetup(d, dry_run=False, verbose=False)
    status = setup.validate()
    assert not any(i.component == "hooks" for i in status.issues)
    # And the git issue must be flagged NOT auto-fixable (the tool never inits git).
    git_issues = [i for i in status.issues if i.component == "git"]
    assert git_issues and all(i.fix_available is False for i in git_issues)


def test_real_git_project_installs_hooks_and_becomes_valid(tmp_path: Path) -> None:
    """Guard: a genuine git repo still gets hooks installed (no over-correction)."""
    d = _make_plugin(tmp_path, with_git=True)
    setup = spp.PipelineSetup(d, dry_run=False, verbose=False)
    setup.validate()
    fixed = setup.fix()
    assert fixed > 0
    assert (d / ".git" / "hooks" / "pre-commit").exists()
    assert (d / ".git" / "hooks" / "pre-push").exists()
    setup.status = spp.PipelineStatus(project_type=spp.ProjectType.UNKNOWN, project_path=d)
    assert setup.validate().is_valid


# ── #81 setup_plugin_pipeline: default mode exit code reflects reality ──────


def test_default_mode_exits_nonzero_when_unfixable(tmp_path: Path) -> None:
    """Default setup on a non-git project must exit 1, not a false 0."""
    d = _make_plugin(tmp_path, with_git=False)
    r = subprocess.run(
        [sys.executable, "setup_plugin_pipeline.py", str(d), "--quiet"],
        cwd=_SCRIPTS,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}: {r.stdout}{r.stderr}"


def test_default_mode_exits_zero_when_fully_fixed(tmp_path: Path) -> None:
    """Guard: default setup on a real git repo still exits 0 once fixed."""
    d = _make_plugin(tmp_path, with_git=True)
    r = subprocess.run(
        [sys.executable, "setup_plugin_pipeline.py", str(d), "--quiet"],
        cwd=_SCRIPTS,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stdout}{r.stderr}"


# ── #18 standardize_plugin: save_report_to_file keeps drift findings ────────


def test_save_report_includes_drift_findings(tmp_path: Path) -> None:
    """The saved file report must render 'drift' category findings."""
    results = [
        sp.AuditItem("files", "README.md", "PASS", "README.md present"),
        sp.AuditItem("drift", "requests", "WARN", "requests declared but never imported"),
        sp.AuditItem("drift", "numpy", "CRITICAL", "numpy imported but not declared"),
    ]
    report = tmp_path / "report.txt"
    sp.save_report_to_file(results, tmp_path, report)
    text = report.read_text(encoding="utf-8")
    assert "Project Drift (deps vs imports)" in text
    assert "requests declared but never imported" in text
    assert "numpy imported but not declared" in text


def test_save_report_rendered_count_matches_summary(tmp_path: Path) -> None:
    """Guard: rendered item count must equal the summary's check total."""
    results = [
        sp.AuditItem("files", "README.md", "PASS", "README.md present"),
        sp.AuditItem("drift", "requests", "WARN", "requests declared but never imported"),
        sp.AuditItem("drift", "numpy", "CRITICAL", "numpy imported but not declared"),
    ]
    report = tmp_path / "report.txt"
    sp.save_report_to_file(results, tmp_path, report)
    text = report.read_text(encoding="utf-8")
    rendered = sum(1 for ln in text.splitlines() if ln.strip().startswith("["))
    assert rendered == len(results) == 3


def test_save_report_category_titles_match_stdout_report() -> None:
    """The file report and stdout report must cover the same category set."""
    # Every category an AuditItem can carry must be renderable in BOTH paths,
    # otherwise findings vanish silently. This pins them in sync going forward.
    emitted_categories = {"files", "dirs", "gitignore", "badges", "pyproject", "python", "drift"}
    # Re-derive the file-report titles by exercising one item per category.
    for cat in emitted_categories:
        item = sp.AuditItem(cat, "x", "WARN", f"{cat} probe message")
        with tempfile.TemporaryDirectory() as td:
            report = Path(td) / "r.txt"
            sp.save_report_to_file([item], Path(td), report)
            text = report.read_text(encoding="utf-8")
        assert f"{cat} probe message" in text, f"category {cat!r} dropped from file report"


# ── #82 standardize_plugin: audit_gitignore — no substring false PASS ───────


def _gitignore_status(tmp_path: Path, gitignore_body: str) -> dict[str, str]:
    d = tmp_path / "p"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".gitignore").write_text(gitignore_body, encoding="utf-8")
    return {it.name: it.status for it in sp.audit_gitignore(d)}


@pytest.mark.parametrize(
    ("entry", "decoy_line"),
    [
        (".env", ".env.example"),  # secrets file NOT ignored despite decoy
        ("dist/", "redist/"),
        ("build/", "prebuild/"),
        (".coverage", ".coverage_html/"),
    ],
)
def test_audit_gitignore_substring_decoy_is_not_a_pass(tmp_path: Path, entry: str, decoy_line: str) -> None:
    """MALICIOUS side: a substring-collision line must NOT mark the entry present."""
    status = _gitignore_status(tmp_path, decoy_line + "\n")
    assert status[entry] == "WARN", f"{decoy_line!r} falsely satisfied required entry {entry!r} (false PASS)"


@pytest.mark.parametrize(
    "entry",
    [".env", "dist/", "build/", ".coverage", "*_dev/", "node_modules/", "__pycache__/"],
)
def test_audit_gitignore_exact_entry_passes(tmp_path: Path, entry: str) -> None:
    """BENIGN side: a genuine exact entry must still PASS (no over-correction)."""
    status = _gitignore_status(tmp_path, entry + "\n")
    assert status[entry] == "PASS"


def test_audit_gitignore_broader_glob_covers_entry(tmp_path: Path) -> None:
    """A legitimately broader glob ('*_cache/') still covers the cache entries."""
    status = _gitignore_status(tmp_path, "*_cache/\n")
    assert status[".pytest_cache/"] == "PASS"
    assert status[".ruff_cache/"] == "PASS"


def test_audit_gitignore_anchored_pattern_covers_entry(tmp_path: Path) -> None:
    """A root-anchored '/dist/' still covers the unanchored 'dist/' entry."""
    status = _gitignore_status(tmp_path, "/dist/\n")
    assert status["dist/"] == "PASS"


def test_gitignore_audit_and_fix_agree(tmp_path: Path) -> None:
    """audit and the auto-add fix path must use the same coverage decision.

    With only `.env.example` present, audit reports `.env` missing; the fix
    path must therefore also consider `.env` missing (and add it), not skip it
    via a stale substring test — otherwise the two would disagree forever.
    """
    d = tmp_path / "p"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".gitignore").write_text(".env.example\n", encoding="utf-8")

    audit_missing = {it.name for it in sp.audit_gitignore(d) if it.status != "PASS"}
    assert ".env" in audit_missing

    content = (d / ".gitignore").read_text(encoding="utf-8")
    active = [s.strip() for s in content.splitlines() if s.strip() and not s.strip().startswith("#")]
    fix_missing = {
        e for e in sp.REQUIRED_GITIGNORE_ENTRIES if not any(sp._gitignore_line_covers_entry(e, ln) for ln in active)
    }
    assert ".env" in fix_missing, "fix path disagrees with audit on .env"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
