"""Real subprocess/filesystem tests for templates/scripts/render_readme_table.py
and its copy-step wiring in scripts/setup_marketplace_automation.py.

No mocks: every test builds a real temp marketplace directory (a real
.claude-plugin/marketplace.json + README.md with the marker comments) and
runs the actual script as a subprocess, asserting on exit codes and file
content.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "templates" / "scripts" / "render_readme_table.py"

START = "<!-- PLUGIN-VERSIONS-START -->"
END = "<!-- PLUGIN-VERSIONS-END -->"

BASE_README = f"""# My Marketplace

Some intro text.

{START}
old stale content
{END}

Footer text.
"""


def _write_marketplace(tmp_path: Path, plugins: list[dict]) -> Path:
    (tmp_path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": plugins}), encoding="utf-8"
    )
    (tmp_path / "README.md").write_text(BASE_README, encoding="utf-8")
    return tmp_path


def _run(tmp_path: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_rewrite_updates_readme_between_markers(tmp_path: Path) -> None:
    """A plain run replaces the block between the markers with a live-rendered table."""
    _write_marketplace(
        tmp_path,
        [
            {"name": "foo-plugin", "version": "1.2.3", "category": "dev-tools", "description": "Does foo."},
        ],
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "old stale content" not in text
    assert "foo-plugin" in text
    assert "1.2.3" in text
    assert "Dev Tools" in text  # category title-cased
    assert text.startswith("# My Marketplace")
    assert text.rstrip().endswith("Footer text.")
    assert START in text and END in text


def test_check_flag_exits_1_on_drift(tmp_path: Path) -> None:
    """--check must fail (exit 1) when the README block is stale relative to the manifest."""
    _write_marketplace(
        tmp_path,
        [{"name": "foo-plugin", "version": "1.0.0", "category": "dev-tools", "description": "x"}],
    )
    result = _run(tmp_path, "--check")
    assert result.returncode == 1
    assert "STALE" in result.stderr
    # --check must never write
    assert "old stale content" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_check_flag_exits_0_when_current(tmp_path: Path) -> None:
    """--check must pass (exit 0) once the README has already been regenerated."""
    _write_marketplace(
        tmp_path,
        [{"name": "foo-plugin", "version": "1.0.0", "category": "dev-tools", "description": "x"}],
    )
    first = _run(tmp_path)
    assert first.returncode == 0
    second = _run(tmp_path, "--check")
    assert second.returncode == 0
    assert "already current" in second.stdout


def test_refuses_to_blank_table_on_empty_plugins(tmp_path: Path) -> None:
    """An empty/missing plugins list must error, never silently render an empty table."""
    _write_marketplace(tmp_path, [])
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "no plugins" in result.stderr
    # README must be untouched
    assert "old stale content" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_pipe_and_newline_are_escaped_in_cells(tmp_path: Path) -> None:
    """A description containing a literal pipe or newline must not break the table structure."""
    _write_marketplace(
        tmp_path,
        [
            {
                "name": "pipe-plugin",
                "version": "1.0.0",
                "category": "dev-tools",
                "description": "Does A | B\nand more",
            }
        ],
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    table_lines = [line for line in text.splitlines() if line.startswith("| pipe-plugin")]
    assert len(table_lines) == 1
    row = table_lines[0]
    # Escaped pipe stays literal text, not a new column boundary.
    assert "Does A \\| B and more" in row
    # Exactly 5 unescaped '|' delimiters => 4 columns (name, version, category, description).
    unescaped_pipes = row.replace("\\|", "").count("|")
    assert unescaped_pipes == 5


def test_missing_markers_errors(tmp_path: Path) -> None:
    """A README lacking the PLUGIN-VERSIONS markers must error rather than guess where to write."""
    _write_marketplace(
        tmp_path,
        [{"name": "foo-plugin", "version": "1.0.0", "category": "dev-tools", "description": "x"}],
    )
    (tmp_path / "README.md").write_text("# No markers here\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "markers" in result.stderr


def test_setup_marketplace_automation_copies_and_makes_executable(tmp_path: Path) -> None:
    """setup_marketplace_automation.py must install render_readme_table.py, executable, into scripts/."""
    marketplace = _write_marketplace(
        tmp_path,
        [{"name": "foo-plugin", "version": "1.0.0", "category": "dev-tools", "description": "x"}],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "setup_marketplace_automation.py"),
            "--marketplace-dir",
            str(marketplace),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    dst = marketplace / "scripts" / "render_readme_table.py"
    assert dst.is_file()
    assert dst.read_text(encoding="utf-8") == SCRIPT.read_text(encoding="utf-8")
    assert dst.stat().st_mode & 0o111  # executable bit set


def test_rendered_block_carries_no_date_and_no_doc_promises_one() -> None:
    """The block must be DATE-FREE, and no doc may promise a timestamp it will not find.

    `--check` compares the fully rendered text, so a generation date would differ every
    midnight and fail the CI gate on an unchanged manifest — a daily false red for every
    adopter. The reference implementation this was ported from stamps `date.today()`
    there; the port deliberately does not.

    The doc half is the load-bearing half: two lines in readme-template.md described a
    "generated-timestamp line" after the date was removed, which is the exact shape of
    drift that survives review — a reader compares the doc to the doc, finds agreement,
    and never opens the script. Both sides are pinned here so neither can move alone.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    # Comments are stripped before the check: the renderer's own WHY comment NAMES
    # date.today() in order to record that it was deliberately dropped, and a naive
    # substring test fails on that comment — punishing the code for explaining itself.
    code_only = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    assert "date.today" not in code_only, "the rendered block must not carry a generation date"
    assert "datetime" not in code_only, "the rendered block must not carry a generation timestamp"
    assert "date.today" in source, "the WHY comment recording the dropped date must survive"

    skill_docs = REPO_ROOT / "skills" / "cpv-setup-github-marketplace"
    for doc in sorted(skill_docs.rglob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "render_readme_table" not in line and "PLUGIN-VERSIONS" not in line:
                continue
            lowered = line.lower()
            # "no generation date" (readme-template.md's own explanation) is the one
            # legitimate way these words appear together — it states the absence.
            if "no generation date" in lowered:
                continue
            for claim in ("generated-timestamp", "generation timestamp", "generated timestamp"):
                assert claim not in lowered, f"{doc.name}:{lineno} promises a {claim!r} the renderer does not emit"


def test_doc_embedded_copy_is_byte_identical_to_the_shipped_script() -> None:
    """The copy in script-templates.md must equal templates/scripts/render_readme_table.py.

    The guide tells authors to COPY that fenced block into their marketplace, so the doc
    copy is executable code with users, not illustration. It had already drifted: it kept
    `from datetime import date` after the date footer was removed from both, leaving a dead
    import that fails ruff F401 for anyone who copies it, plus a divergent WHY comment.

    Byte parity is the only check that holds here — "documented somewhere" is what let the
    drift through, and a looser assertion (both mention render_readme_table) would pass on
    two files that disagree about what the script does.
    """
    import re

    doc_path = REPO_ROOT / "skills" / "cpv-setup-github-marketplace" / "references" / "script-templates.md"
    doc = doc_path.read_text(encoding="utf-8")
    section = doc[doc.index("## render_readme_table.py") :]
    match = re.search(r"```python\n(.*?)\n```", section, re.S)
    assert match, "script-templates.md no longer embeds a python block for render_readme_table.py"
    embedded = match.group(1)
    shipped = SCRIPT.read_text(encoding="utf-8").rstrip("\n")
    assert embedded == shipped, (
        "the documented copy has drifted from templates/scripts/render_readme_table.py; "
        "update the fenced block in script-templates.md to match the shipped script byte-for-byte"
    )


def test_emitted_update_workflow_stages_tracked_only_not_a_bare_add_dot() -> None:
    """The emitted update workflow must stage with `git add -u`, never `git add .`.

    Issue #186 forbids sweeping untracked files into a commit, and this workflow's
    commit is pushed to a marketplace repo. The tempting exemption — "it updates
    submodules, so it needs broad staging" — is a FALSE BINARY, measured on a real
    submodule fixture: after `git submodule update --remote --merge`, naming files
    leaves the gitlink unstaged, while `git add -u` stages the gitlink AND leaves an
    untracked file untracked. So `-u` satisfies the submodule need with none of the
    untracked sweep. `generate_plugin_repo.py` already carries this rule in its own
    release path; this template was the lone holdout.
    """
    wf = (REPO_ROOT / "templates" / "github-workflows" / "update-submodules.yml").read_text(encoding="utf-8")
    staging = [ln.strip() for ln in wf.splitlines() if ln.strip().startswith("git add")]
    assert staging, "no staging line found — the workflow shape changed"
    for line in staging:
        assert line not in ("git add .", "git add -A"), f"untracked-sweep staging: {line!r}"
    assert "git add -u" in staging
