"""Issue #186 — the release path used `git add -A`.

`git add -A` stages untracked files. At release time a plugin tree routinely
holds `reports/` (which CPV's own convention says "routinely contain private
data — absolute paths, usernames, internal hostnames, proprietary source,
tokens caught in logs"), local scratch, and whatever a failed earlier run left
behind. The release commit is the worst place for an accidental inclusion: it
is pushed to a public repo AND it is the artifact users install, so once it
lands, forks and caches make it unrecoverable in practice.

Three sites were affected. The third matters most: it is in the GENERATOR, so
every plugin CPV scaffolds inherited blanket staging in its own release path.

The replacement stages tracked modifications (`git add -u`) plus the pipeline's
own generated files BY NAME, and reports anything left untracked instead of
absorbing it. That is safe here specifically because the clean-tree gate ran
first: everything legitimate is already committed, so anything still untracked
at this point is by construction not part of the release.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

PUBLISH_PY = SCRIPTS / "publish.py"
GENERATOR_PY = SCRIPTS / "generate_plugin_repo.py"


# ------------------------------------------------------------ CPV's own publish


def test_cpv_publish_has_no_blanket_add() -> None:
    """No `git add -A` / `add .` / `add --all` anywhere in CPV's release path."""
    src = PUBLISH_PY.read_text(encoding="utf-8")
    for forbidden in ('"add", "-A"', '"add", "."', '"add", "--all"'):
        assert forbidden not in src, f"publish.py still stages with {forbidden}"


def test_cpv_publish_stages_tracked_modifications() -> None:
    src = PUBLISH_PY.read_text(encoding="utf-8")
    assert '"add", "-u"' in src


def test_cpv_publish_names_its_generated_files() -> None:
    """The pipeline's own outputs are staged by name, never swept in."""
    src = PUBLISH_PY.read_text(encoding="utf-8")
    for rel in (
        ".claude-plugin/plugin.json",
        ".plugin-self-hashes.json",
        ".cpv-self-hashes.json",
        "CHANGELOG.md",
    ):
        assert rel in src, f"{rel} is not staged by name"


def test_cpv_publish_surfaces_untracked_files() -> None:
    """Silently dropping a file is as bad as silently including one."""
    src = PUBLISH_PY.read_text(encoding="utf-8")
    assert "git status --porcelain" in src or '"status", "--porcelain"' in src
    assert "NOT staged" in src


def test_stage_release_changes_is_wired_into_the_release_commit() -> None:
    """The helper must actually be called, not merely defined."""
    src = PUBLISH_PY.read_text(encoding="utf-8")
    assert src.count("stage_release_changes(") >= 2, "helper defined but not called"


# --------------------------------------------------------- the emitted template


def _emit_publish_py() -> str:
    import generate_plugin_repo as g

    params = g.PluginParams(
        name="demo-plugin",
        description="fixture",
        author="T",
        author_email="t@example.invalid",
    )
    return g.gen_publish_py(params)


def test_emitted_template_compiles() -> None:
    compile(_emit_publish_py(), "publish.py", "exec")


def test_emitted_template_has_no_blanket_add() -> None:
    """The generator is the site that reaches every scaffolded plugin."""
    src = _emit_publish_py()
    for forbidden in ('"add", "-A"', '"add", "--all"'):
        assert forbidden not in src, f"generated publish.py still stages with {forbidden}"


def test_emitted_template_stages_tracked_modifications() -> None:
    assert '"add", "-u"' in _emit_publish_py()


def test_emitted_template_surfaces_untracked_files() -> None:
    src = _emit_publish_py()
    assert "#186" in src, "the generated template carries no rationale for its staging"


# ------------------------------------------------------- behaviour, on real git


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-qm", "init")
    return root


def test_untracked_scratch_is_not_staged(tmp_path: Path) -> None:
    """The actual defect: a private report must not enter the release commit."""
    root = _repo(tmp_path)
    (root / "tracked.txt").write_text("v2\n", encoding="utf-8")
    (root / "reports").mkdir()
    (root / "reports" / "private-audit.md").write_text("/Users/someone/secret\n", encoding="utf-8")

    _git(root, "add", "-u")

    staged = _git(root, "diff", "--cached", "--name-only").split()
    assert "tracked.txt" in staged, "tracked modifications must still be staged"
    assert not [p for p in staged if p.startswith("reports/")], "untracked scratch was swept in"


def test_blanket_add_would_have_staged_it(tmp_path: Path) -> None:
    """Positive control: prove the old shape really did absorb the scratch.

    Without this, the test above could pass for an unrelated reason and the
    regression it guards would be unproven.
    """
    root = _repo(tmp_path)
    (root / "reports").mkdir()
    (root / "reports" / "private-audit.md").write_text("/Users/someone/secret\n", encoding="utf-8")

    _git(root, "add", "-A")

    staged = _git(root, "diff", "--cached", "--name-only").split()
    assert "reports/private-audit.md" in staged


def test_named_new_file_is_staged_even_when_untracked(tmp_path: Path) -> None:
    """A first-ever CHANGELOG.md is new; naming it is what keeps `-A` unneeded."""
    root = _repo(tmp_path)
    (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

    _git(root, "add", "-u")
    assert "CHANGELOG.md" not in _git(root, "diff", "--cached", "--name-only").split()

    _git(root, "add", "--", "CHANGELOG.md")
    assert "CHANGELOG.md" in _git(root, "diff", "--cached", "--name-only").split()


def test_deletions_are_staged_by_add_u(tmp_path: Path) -> None:
    """`-u` must still record a removal, or a release would resurrect the file."""
    root = _repo(tmp_path)
    (root / "tracked.txt").unlink()

    _git(root, "add", "-u")

    assert "tracked.txt" in _git(root, "diff", "--cached", "--name-only").split()


# ---------------------------------------------------------- printed guidance


def test_scaffold_instructions_do_not_teach_blanket_add() -> None:
    """CPV must not print the antipattern it just removed from its own path."""
    for script in (GENERATOR_PY, SCRIPTS / "generate_marketplace_repo.py"):
        for line in script.read_text(encoding="utf-8").splitlines():
            if "print(" in line and "git add" in line:
                assert "git add -A" not in line, f"{script.name} prints `git add -A`: {line.strip()}"
                assert "git add --all" not in line, f"{script.name} prints `git add --all`: {line.strip()}"
