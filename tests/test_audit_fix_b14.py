#!/usr/bin/env python3
"""Audit-fix regression tests (batch b14).

Covers the verified fixes for the two scaffold generators:

generate_marketplace_repo.py
  - HIGH: local-only marketplace must emit the LOCAL-mode pre-push hook so
    relative-path string plugin sources (which the local README recommends)
    pass validation instead of being rejected by the hub-and-spoke hook.
  - #76: the generated scripts/update_catalog.py must actually compile and
    run, and its docstring must describe the real positional MARKETPLACE_DIR
    interface (the old docstring promised a non-existent --marketplace-dir
    flag, and the emitted script did not even compile because "\\n" inside the
    template f-string collapsed to a real newline mid-string-literal).
  - #77: the update-catalog workflow push-retry loop must exit non-zero when
    all 3 push attempts fail (previously the loop completed and the step
    succeeded silently, never pushing the regenerated catalog).

generate_plugin_repo.py (gen_publish_py template)
  - #42: the generated publish.py stage_gh_release must fail-fast (abort) on a
    genuine release-creation failure, while still treating an already-existing
    release as idempotent success. The old version swallowed every non-zero
    gh exit, reporting success even when no release was created.
  - #127: stage_update_badges / stage_gh_release docstrings must carry the
    correct step numbers (8 and 11), matching their [N/11] terminal markers.

Refuted in this batch (NOT changed, documented here for traceability):
  - #10: _plugin_in_remote_marketplace returning False for string-format
    sources is INTENTIONAL and matches CPV's own publish.py reference
    ("a bare ./path string source is a local directory entry, not a remote
    registration — skipped"). The test below pins that deliberate behavior so
    a future "fix" that accepts string sources would fail loudly.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import generate_marketplace_repo as gmr  # noqa: E402
import generate_plugin_repo as gpr  # noqa: E402


def _extract_generated_publish_py() -> str:
    """Return the publish.py source that gen_publish_py emits (template string)."""

    class _P:  # minimal stand-in for PluginParams (signature-only arg)
        pass

    return gpr.gen_publish_py(_P())  # type: ignore[arg-type]


def _extract_func_source(module_src: str, func_name: str) -> str:
    """Return the source of a top-level function inside `module_src`."""
    tree = ast.parse(module_src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            seg = ast.get_source_segment(module_src, node)
            assert seg is not None
            return seg
    raise AssertionError(f"{func_name} not found")


# --------------------------------------------------------------------------
# HIGH — local-only marketplace emits the LOCAL pre-push hook
# --------------------------------------------------------------------------


def test_local_marketplace_emits_local_mode_pre_push_hook(tmp_path: Path) -> None:
    """github_owner='' -> .githooks/pre-push is the local-mode hook."""
    target = tmp_path / "local-mp"
    rc = gmr.generate_marketplace_repo(
        target, "local-mp", "Me", "desc", github_owner="", add_plugins=[], dry_run=False
    )
    assert rc == 0
    hook = (target / ".githooks" / "pre-push").read_text(encoding="utf-8")
    assert "local mode" in hook
    # Guard against the original bug: the hub-and-spoke rejection text must NOT
    # be present in a local marketplace's hook.
    assert "source must be an object (hub-and-spoke)" not in hook


def test_github_marketplace_emits_hub_and_spoke_pre_push_hook(tmp_path: Path) -> None:
    """github_owner set -> .githooks/pre-push is the strict hub-and-spoke hook."""
    target = tmp_path / "gh-mp"
    rc = gmr.generate_marketplace_repo(
        target, "gh-mp", "Me", "desc", github_owner="me", add_plugins=[], dry_run=False
    )
    assert rc == 0
    hook = (target / ".githooks" / "pre-push").read_text(encoding="utf-8")
    assert "hub-and-spoke" in hook
    assert "source must be an object (hub-and-spoke)" in hook


def test_local_hook_accepts_relative_string_source_end_to_end(tmp_path: Path) -> None:
    """The emitted local hook exits 0 on a relative-path string plugin source.

    This is the exact scenario _readme_local recommends; the hub-and-spoke
    hook would exit 1 on it.
    """
    local_hook = gmr._pre_push_hook(local=True)
    hub_hook = gmr._pre_push_hook(local=False)
    mkt = {
        "name": "mp",
        "owner": {"name": "Me"},
        "plugins": [{"name": "p", "source": "./plugins/p"}],
    }

    def _run(hook_src: str) -> int:
        root = tmp_path / f"r{abs(hash(hook_src)) % 10000}"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps(mkt))
        ghooks = root / ".githooks"
        ghooks.mkdir()
        hook_file = ghooks / "pre-push"
        hook_file.write_text(hook_src)
        return subprocess.run(
            [sys.executable, str(hook_file)], capture_output=True, text=True, input=""
        ).returncode

    assert _run(local_hook) == 0  # corrected behavior
    assert _run(hub_hook) == 1  # guard: the wrong hook would have blocked


# --------------------------------------------------------------------------
# #76 — generated update_catalog.py compiles, runs, honest docstring
# --------------------------------------------------------------------------


def test_generated_update_catalog_compiles() -> None:
    """The emitted scripts/update_catalog.py must be valid Python."""
    script = gmr._update_catalog_script("demo")
    # Guard: the original template embedded "\\n" inside an f-string, which
    # collapsed into a real newline mid-string-literal -> SyntaxError.
    compile(script, "<update_catalog>", "exec")


def test_generated_update_catalog_docstring_matches_positional_interface() -> None:
    """Docstring describes the real positional arg, not a phantom flag."""
    script = gmr._update_catalog_script("demo")
    assert "[MARKETPLACE_DIR]" in script
    assert "--marketplace-dir" not in script


def test_generated_update_catalog_regenerates_table(tmp_path: Path) -> None:
    """Running the emitted script rewrites the Plugins table, preserving rest."""
    script = gmr._update_catalog_script("demo-mp")
    root = tmp_path / "mp"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "demo-mp",
                "owner": {"name": "Me"},
                "plugins": [
                    {
                        "name": "alpha",
                        "description": "First",
                        "source": {"source": "github", "repo": "o/alpha"},
                    }
                ],
            }
        )
    )
    (root / "README.md").write_text("# T\n\n## Plugins\n\n| old |\n\n## Next\nkeep\n")
    (root / "scripts").mkdir()
    sp = root / "scripts" / "update_catalog.py"
    sp.write_text(script)
    # Positional path, as the corrected docstring documents.
    r = subprocess.run([sys.executable, str(sp), str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = (root / "README.md").read_text(encoding="utf-8")
    assert "alpha" in out and "First" in out
    assert "## Next\nkeep" in out  # content after the section preserved
    assert "| old |" not in out  # old table row replaced
    # The newline escapes must produce real line breaks (not literal "\\n").
    assert "\\n" not in out


# --------------------------------------------------------------------------
# #77 — update-catalog workflow push loop fails when all pushes fail
# --------------------------------------------------------------------------


def _simulate_push_block(push_succeeds: bool) -> int:
    """Run the workflow's Commit-and-push bash with git replaced by stubs."""
    import yaml

    wf = gmr._update_catalog_workflow("demo")
    doc = yaml.safe_load(wf)
    step = next(
        s
        for s in doc["jobs"]["update-readme"]["steps"]
        if s.get("name") == "Commit and push"
    )
    run = step["run"]
    run = run.replace("${{ github.event.repository.default_branch }}", "main")
    run = run.replace("git add README.md", "true")
    run = run.replace(
        'git commit -m "docs: regenerate plugin catalog from marketplace.json"', "true"
    )
    run = run.replace("git push", "true" if push_succeeds else "false")
    run = run.replace("git pull --rebase origin main", "true")
    return subprocess.run(["bash", "-c", run], capture_output=True, text=True).returncode


def test_update_catalog_workflow_yaml_is_valid() -> None:
    """The emitted workflow is parseable YAML after the push-loop fix."""
    yaml = pytest.importorskip("yaml")
    yaml.safe_load(gmr._update_catalog_workflow("demo"))


def test_update_catalog_push_loop_fails_when_all_pushes_fail() -> None:
    """All 3 push attempts fail -> the step exits non-zero (no silent success)."""
    pytest.importorskip("yaml")
    assert _simulate_push_block(push_succeeds=False) == 1


def test_update_catalog_push_loop_succeeds_when_push_works() -> None:
    """A successful push -> the step exits 0."""
    pytest.importorskip("yaml")
    assert _simulate_push_block(push_succeeds=True) == 0


# --------------------------------------------------------------------------
# #42 + #127 — generated publish.py stage_gh_release + step numbers
# --------------------------------------------------------------------------


def test_generated_publish_py_compiles() -> None:
    """The whole emitted publish.py must compile."""
    compile(_extract_generated_publish_py(), "<publish>", "exec")


def test_stage_docstrings_match_step_markers() -> None:
    """Every generated stage_*'s 'Step N' docstring matches its [N/11] marker."""
    pub = _extract_generated_publish_py()
    tree = ast.parse(pub)
    mismatches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("stage_"):
            doc = (ast.get_docstring(node) or "").splitlines()
            first = doc[0] if doc else ""
            seg = ast.get_source_segment(pub, node) or ""
            marker = re.search(r"\[(\d+)/11\]", seg)
            docnum = re.search(r"Step (\d+)", first)
            if marker and docnum and marker.group(1) != docnum.group(1):
                mismatches.append((node.name, docnum.group(1), marker.group(1)))
    assert not mismatches, f"docstring/marker mismatches: {mismatches}"


def test_stage_update_badges_is_step_8() -> None:
    """Guard the specific #127 fix for stage_update_badges."""
    src = _extract_func_source(_extract_generated_publish_py(), "stage_update_badges")
    assert "Step 8:" in src
    assert "[8/11]" in src
    assert "Step 7:" not in src


def test_stage_gh_release_is_step_11() -> None:
    """Guard the specific #127 fix for stage_gh_release."""
    src = _extract_func_source(_extract_generated_publish_py(), "stage_gh_release")
    assert "Step 11:" in src
    assert "[11/11]" in src
    assert "Step 10:" not in src


def _run_stage_gh_release(returncode: int, stderr: str, tmp_path: Path):
    """Exec the generated stage_gh_release with gh stubbed; return ('ok'|exit code)."""
    pub = _extract_generated_publish_py()
    src = _extract_func_source(pub, "stage_gh_release")
    import pathlib

    class _FakeResult:
        def __init__(self, rc: int, out: str = "", err: str = "") -> None:
            self.returncode = rc
            self.stdout = out
            self.stderr = err

    ns: dict = {}
    ns["sys"] = sys
    ns["re"] = re
    ns["Path"] = pathlib.Path
    for color in ("RED", "GREEN", "YELLOW", "BLUE", "NC", "BOLD"):
        ns[color] = ""
    ns["cprint"] = lambda *a, **k: None
    ns["gh_with_retry"] = lambda args, **kw: _FakeResult(returncode, "", stderr)
    fake_shutil = type("S", (), {"which": staticmethod(lambda x: "/usr/bin/gh")})
    ns["shutil"] = fake_shutil
    ns["_resolve_owner_repo"] = lambda root: ("o", "r")
    ns["_ensure_gh_auth"] = lambda o, r: None
    exec(compile(ast.parse(src), "<stage>", "exec"), ns)
    func = ns["stage_gh_release"]
    # tmp_path has no CHANGELOG.md -> --generate-notes branch
    try:
        func(tmp_path, "1.2.3", False)
        return "ok"
    except SystemExit as e:
        return e.code


def test_stage_gh_release_success_returns_normally(tmp_path: Path) -> None:
    """gh exit 0 -> stage returns normally (no abort)."""
    assert _run_stage_gh_release(0, "", tmp_path) == "ok"


def test_stage_gh_release_already_exists_is_idempotent_success(tmp_path: Path) -> None:
    """gh 'already_exists' -> treated as success (idempotent re-run)."""
    assert (
        _run_stage_gh_release(1, "HTTP 422: Validation Failed (already_exists)", tmp_path)
        == "ok"
    )


def test_stage_gh_release_genuine_failure_aborts(tmp_path: Path) -> None:
    """A real gh failure -> sys.exit(1) (fail-fast, the #42 fix).

    Guard against the original bug: before the fix, ANY non-zero gh exit was
    swallowed and the stage returned 'success'.
    """
    assert _run_stage_gh_release(1, "gh: authentication failed", tmp_path) == 1


# --------------------------------------------------------------------------
# #10 — REFUTED: pin the intentional string-source skip behavior
# --------------------------------------------------------------------------


def test_plugin_in_remote_marketplace_string_source_skip_is_intentional() -> None:
    """A bare relative-path string source is NOT a remote registration.

    This pins the deliberate behavior (matching CPV's own publish.py): the
    generated _plugin_in_remote_marketplace skips string sources and matches
    only github/url/git dict sources by repo slug.
    """
    pub = _extract_generated_publish_py()
    src = _extract_func_source(pub, "_plugin_in_remote_marketplace")
    ns: dict = {}
    exec(compile(ast.parse(src), "<reg>", "exec"), ns)
    f = ns["_plugin_in_remote_marketplace"]
    string_src = {"plugins": [{"name": "p", "source": "./plugins/p"}]}
    github_src = {
        "plugins": [{"name": "p", "source": {"source": "github", "repo": "o/p"}}]
    }
    # Intentional: string source never matches a remote slug.
    assert f(string_src, "p", "o/p") is False
    assert f(string_src, "p", None) is False
    # Dict github source matches (the supported remote registration form).
    assert f(github_src, "p", "o/p") is True
    assert f(github_src, "p", None) is True
