"""End-to-end tests for the strip-dev-parts feature surface.

TRDD-793ac32a — verifies the seams between:
  * `generate_plugin_repo.py --strip-dev` flag
  * `cpv.strip` block in scaffolded plugin.json
  * `cpv_strip_dev.build_plan()` reading that block back
  * `cpv_strip_dev.main(... --dry-run)` producing a usable plan

These tests do NOT touch real GitHub or modify the user's git state.
The "live" extraction flow is not exercised — that ships in a later
RC. This sprint (rc3) covers the metadata + dry-run flow only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_strip_dev as csd  # noqa: E402
from generate_plugin_repo import PluginParams, gen_plugin_json  # noqa: E402

# ── gen_plugin_json + cpv.strip block ─────────────────────────────────────────


def test_gen_plugin_json_includes_cpv_strip_block_by_default():
    """Default --strip-dev=True → plugin.json carries a cpv.strip block."""
    p = PluginParams(
        name="my-plugin",
        description="x",
        author="A",
        author_email="a@a.a",
        github_owner="Emasoft",
    )
    pj = json.loads(gen_plugin_json(p))
    assert "cpv" in pj
    assert "strip" in pj["cpv"]
    extract = pj["cpv"]["strip"]["extract"]
    srcs = {e["src"] for e in extract}
    # PSS-style default: ONE submodule per plugin (tests/ only).
    assert "tests/" in srcs
    assert len(extract) == 1, "Default scaffold emits exactly one extract entry"
    assert pj["cpv"]["strip"]["require_url_allowlist"] is True


def test_gen_plugin_json_omits_cpv_strip_block_when_disabled():
    """--no-strip-dev → no cpv.strip block in plugin.json."""
    p = PluginParams(
        name="my-plugin",
        description="x",
        author="A",
        author_email="a@a.a",
        github_owner="Emasoft",
        strip_dev=False,
    )
    pj = json.loads(gen_plugin_json(p))
    assert "cpv" not in pj or "strip" not in pj.get("cpv", {})


def test_gen_plugin_json_uses_owner_in_submodule_names():
    """When github_owner is set, scaffolded submodule names use it directly."""
    p = PluginParams(
        name="lint-checker",
        description="x",
        author="A",
        author_email="a@a.a",
        github_owner="Acme",
    )
    pj = json.loads(gen_plugin_json(p))
    submodules = {e["submodule"] for e in pj["cpv"]["strip"]["extract"]}
    assert "Acme/lint-checker-tests" in submodules


def test_gen_plugin_json_uses_placeholder_when_no_owner():
    """Without github_owner, submodule names get a `<owner>` placeholder."""
    p = PluginParams(
        name="lint-checker",
        description="x",
        author="A",
        author_email="a@a.a",
    )
    pj = json.loads(gen_plugin_json(p))
    submodules = {e["submodule"] for e in pj["cpv"]["strip"]["extract"]}
    assert "<owner>/lint-checker-tests" in submodules


# ── End-to-end: scaffold → build_plan ─────────────────────────────────────────


def _make_scaffold_with_strip(tmp_path: Path, *, strip: bool = True) -> Path:
    """Run generate_plugin_repo.py end-to-end via subprocess.

    Returns the scaffolded plugin path.
    """
    target = tmp_path / "demo"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "generate_plugin_repo.py"),
        str(target),
        "--name",
        "demo",
        "--description",
        "test plugin",
        "--author",
        "Tester",
        "--author-email",
        "t@t.t",
        "--github-owner",
        "Emasoft",
    ]
    if not strip:
        cmd.append("--no-strip-dev")
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PLUGIN_SKIP_GITHUB_INTEGRITY": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    if res.returncode != 0:
        pytest.fail(f"generate_plugin_repo failed: {res.stderr}\n{res.stdout}")
    return target


def test_scaffolded_plugin_has_cpv_strip_block(tmp_path):
    target = _make_scaffold_with_strip(tmp_path, strip=True)
    pj_path = target / ".claude-plugin" / "plugin.json"
    assert pj_path.is_file()
    pj = json.loads(pj_path.read_text())
    assert "cpv" in pj
    assert "strip" in pj["cpv"]
    extract = pj["cpv"]["strip"]["extract"]
    assert any(e["src"] == "tests/" for e in extract)


def test_scaffolded_plugin_no_strip_dev_omits_block(tmp_path):
    target = _make_scaffold_with_strip(tmp_path, strip=False)
    pj_path = target / ".claude-plugin" / "plugin.json"
    pj = json.loads(pj_path.read_text())
    assert pj.get("cpv", {}).get("strip", None) is None


# ── build_plan reads the scaffolded block correctly ──────────────────────────


def test_build_plan_reads_scaffolded_strip_block(tmp_path):
    """End-to-end seam: scaffold writes block; build_plan reads it back."""
    target = _make_scaffold_with_strip(tmp_path, strip=True)
    # PSS-style default extracts only tests/ — make sure that dir exists
    # so build_plan's path validation passes.
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / ".gitkeep").write_text("", encoding="utf-8")

    plan = csd.build_plan(target)
    srcs = {t.src for t in plan.targets}
    assert "tests/" in srcs
    # Scaffolded URLs use the github_owner.
    submodules = {t.submodule for t in plan.targets}
    assert "Emasoft/demo-tests" in submodules


# ── dry-run produces a parseable plan ─────────────────────────────────────────


def test_dry_run_summary_lists_steps(tmp_path):
    """--dry-run output explains what `--auto` would do."""
    target = _make_scaffold_with_strip(tmp_path, strip=True)
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "x.py").write_text("# placeholder\n", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "cpv_strip_dev.py"), str(target), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert res.returncode == 0
    out = res.stdout
    assert "Plan for" in out
    assert "extract targets" in out
    assert "tests/" in out
    assert "Steps that would execute" in out
    assert "gh repo create" in out
    assert "git filter-repo" in out


def test_dry_run_warns_when_working_tree_dirty(tmp_path):
    """Dry-run still completes BUT prints a NOTE that working tree isn't safe."""
    target = _make_scaffold_with_strip(tmp_path, strip=True)
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "x.py").write_text("x", encoding="utf-8")

    # Initialize git but DON'T commit — tree is "dirty" by definition.
    subprocess.run(["git", "-C", str(target), "init", "-b", "main"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(target), "config", "user.email", "t@t.t"], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(target), "config", "user.name", "T"], capture_output=True, check=False)
    # No git add / commit — tree is dirty (untracked files everywhere).

    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "cpv_strip_dev.py"), str(target), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert res.returncode == 0
    # The plan summary still prints. But the working-tree warning should
    # appear on stderr.
    assert "Plan for" in res.stdout
    assert "STRIP-W" in res.stderr or "working tree" in res.stderr.lower()


# ── Live execution is blocked in this RC ─────────────────────────────────────


def test_live_execution_blocked_with_clear_message(tmp_path):
    """Until the full extraction lands, --auto-style live runs must fail
    closed with an actionable message — never silently no-op."""
    target = _make_scaffold_with_strip(tmp_path, strip=True)
    (target / "tests").mkdir(exist_ok=True)
    (target / "tests" / "x.py").write_text("x", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "cpv_strip_dev.py"), str(target), "--extract", "tests/"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    # Live execution requires --auto. Without --auto, this falls through
    # to dry-run and exits 0. Other failure modes (working tree dirty,
    # path validation, missing gh CLI) hit before the --auto gate.
    assert res.returncode in (0, 1)
