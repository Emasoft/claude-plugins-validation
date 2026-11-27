"""Canon fixes: pre-push hook is shfmt-clean, release.yml disables the uv cache, publish.py bounds every subprocess.run."""

from __future__ import annotations

import ast
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_pipeline_profile import (  # noqa: E402
    PROFILE_BINARY_RELEASE,
    PROFILE_REMOTE_VALIDATION,
    PROFILE_STANDARD,
    PROFILE_SUBMODULE_BUILD,
)
from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_pre_push_hook,
    gen_publish_py,
    gen_release_yml,
)

ALL_PROFILES = [
    PROFILE_STANDARD,
    PROFILE_SUBMODULE_BUILD,
    PROFILE_REMOTE_VALIDATION,
    PROFILE_BINARY_RELEASE,
]


def _params() -> PluginParams:
    """A minimal PluginParams for rendering the canon templates."""
    return PluginParams(
        name="my-test-plugin",
        description="A test plugin",
        author="Test Author",
        author_email="test@example.com",
        github_owner="test-owner",
        marketplace="test-marketplace",
    )


def test_pre_push_hook_is_shfmt_clean(tmp_path: Path) -> None:
    """`shfmt -d` (default tab indent) reports no diff on the rendered pre-push hook."""
    shfmt = shutil.which("shfmt")
    if shfmt is None:
        pytest.skip("shfmt is not installed")
    hook = tmp_path / "pre-push"
    hook.write_text(gen_pre_push_hook(_params()))
    result = subprocess.run([shfmt, "-d", str(hook)], capture_output=True, text=True, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_pre_push_hook_has_no_space_indented_lines() -> None:
    """No emitted pre-push line starts with 4 spaces (shfmt default indent is a tab)."""
    offenders = [ln for ln in gen_pre_push_hook(_params()).splitlines() if ln.startswith("    ")]
    assert offenders == []


def test_release_yml_does_not_enable_uv_cache() -> None:
    """The rendered release.yml never sets `enable-cache: true` (zizmor cache-poisoning)."""
    text = gen_release_yml(_params())
    assert "enable-cache: true" not in text
    assert "enable-cache: false" in text


def test_release_yml_setup_uv_steps_disable_cache() -> None:
    """Every setup-uv step of release.yml carries `enable-cache: false`."""
    parsed = yaml.safe_load(gen_release_yml(_params()))
    steps = [
        s
        for job in parsed["jobs"].values()
        for s in job.get("steps", [])
        if "astral-sh/setup-uv" in str(s.get("uses", ""))
    ]
    assert steps
    assert all(s["with"]["enable-cache"] is False for s in steps)


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_publish_py_every_subprocess_run_has_timeout(profile: str) -> None:
    """Every subprocess.run call in the rendered publish.py passes a timeout keyword."""
    src = gen_publish_py(_params(), profile)
    compile(src, "publish.py", "exec")
    tree = ast.parse(src)
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "subprocess.run"]
    assert runs, "expected subprocess.run calls in publish.py"
    missing = [n.lineno for n in runs if not any(k.arg == "timeout" for k in n.keywords)]
    assert missing == []


@pytest.mark.parametrize("profile", ALL_PROFILES)
def test_publish_py_timeouts_use_named_constants(profile: str) -> None:
    """The rendered publish.py defines _GIT_TIMEOUT_S = 60 once, at module level."""
    tree = ast.parse(gen_publish_py(_params(), profile))
    assigns = [
        n
        for n in tree.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_GIT_TIMEOUT_S" for t in n.targets)
    ]
    assert len(assigns) == 1
    assert ast.literal_eval(assigns[0].value) == 60


def test_publish_py_install_hook_handles_timeout_like_failure() -> None:
    """The install-hook git-config call turns TimeoutExpired into the same return-1 failure path."""
    src = gen_publish_py(_params())
    assert "except subprocess.TimeoutExpired:\n        cfg_rc = None" in src
    assert "if cfg_rc != 0:" in src
