"""Two-sided canon-generator tests for GitHub issues #149 and #151.

#149 — the generated ``publish.py`` bumps ``pyproject.toml`` but not ``uv.lock``,
so the next publish's outer ``uv run`` re-syncs ``uv.lock`` in place, dirties the
tree, and Gate 1 (clean-tree) aborts. The bump stage must re-resolve ``uv.lock``
after writing ``pyproject.toml`` — gated on ``uv.lock`` existing AND ``uv`` on
PATH (skip cleanly otherwise).

#151 — five canon defects in the generated ``ci.yml`` / ``publish.py``:
  1. mypy [arg-type] in the jscpd gate (double ``shutil.which("npx")`` keeps the
     in-list element ``str | None``) → resolve ``npx`` once into a variable.
  2. Pyright ``reportAssignmentType`` on the network-resilience import shim → add
     ``# pyright: ignore[reportAssignmentType]`` on the import line.
  3. All ``actions/checkout`` steps lack ``persist-credentials: false`` → zizmor
     ``artipacked``.
  4. commitlint pinned to the annotated-tag-OBJECT sha (``6cf16ef…``), not its
     commit sha (``b948419…``) → zizmor ``ref-version-mismatch``.
  5. canon ci.yml dropped the dedicated zizmor job → re-add it (least-privilege,
     SHA-pinned action).

Each fix is asserted two-sided: the new shape is present AND the defective shape
is absent; and the emitted templates still compile / parse.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_ci_yml,
    gen_publish_py,
)

# The annotated-tag-OBJECT sha for commitlint v6.2.1 (`git rev-parse v6.2.1`),
# which is NOT a commit — pinning to it is the #151.4 defect.
COMMITLINT_TAG_OBJECT_SHA = "6cf16efdf4da5277c791d335142c03a0bdf1766e"
# The COMMIT sha that the v6.2.1 tag object points at — the correct pin.
COMMITLINT_COMMIT_SHA = "b948419dd99f3fd78a6548d48f94e3df7f6bf3ed"


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


# ── emitted templates still compile / parse ──────────────────────────────────


def test_emitted_publish_py_compiles() -> None:
    """The generated publish.py with the #149 + #151 fixes still compiles."""
    text = gen_publish_py(_params())
    compile(text, "publish.py", "exec")  # raises SyntaxError if broken


def test_emitted_ci_yml_is_valid_yaml() -> None:
    """The generated ci.yml with the #151 fixes parses as a single YAML doc."""
    import yaml

    doc = yaml.safe_load(gen_ci_yml(_params()))
    assert isinstance(doc, dict)
    assert "jobs" in doc


# ── #149 — uv.lock sync, gated ───────────────────────────────────────────────


def test_149_uv_lock_sync_helper_present() -> None:
    """The bump stage re-resolves uv.lock (a _sync_uv_lock helper + a call)."""
    text = gen_publish_py(_params())
    assert "_sync_uv_lock" in text
    assert '["uv", "lock"]' in text
    # Defined AND invoked (from do_bump) → at least two occurrences.
    assert text.count("_sync_uv_lock(") >= 2


def test_149_uv_lock_sync_is_double_gated() -> None:
    """uv.lock sync runs only when uv.lock exists AND uv is on PATH."""
    text = gen_publish_py(_params())
    assert 'uv.lock").is_file()' in text  # skip cleanly for non-uv plugins
    assert 'shutil.which("uv")' in text  # skip cleanly when uv isn't installed


def test_149_uv_lock_sync_does_not_run_on_dry_run() -> None:
    """do_bump's dry-run path returns before any uv.lock mutation.

    A dry run must not call `uv lock` — the helper is gated behind the
    post-success branch, after the early `if dry_run: ... return True`.
    """
    text = gen_publish_py(_params())
    do_bump_start = text.index("def do_bump(")
    do_bump_body = text[do_bump_start : text.index("\ndef ", do_bump_start)]
    dry_return = do_bump_body.index("if dry_run:")
    sync_call = do_bump_body.index("_sync_uv_lock(root)")
    # The sync call is AFTER the dry-run early return, so a dry run never reaches it.
    assert sync_call > dry_return


# ── #151.1 — jscpd gate mypy [arg-type] ──────────────────────────────────────


def test_151_1_npx_resolved_once_into_variable() -> None:
    """The jscpd gate resolves npx once (npx_bin) so mypy narrows the type."""
    text = gen_publish_py(_params())
    assert 'npx_bin = shutil.which("npx")' in text
    assert "[npx_bin, " in text


def test_151_1_no_double_which_npx_inside_list() -> None:
    """The defective double shutil.which('npx') inside the list is gone."""
    text = gen_publish_py(_params())
    assert '[shutil.which("npx"), "--yes", "jscpd"]' not in text


# ── #151.2 — Pyright reportAssignmentType on the import shim ──────────────────


def test_151_2_pyright_ignore_on_import_line() -> None:
    """The network-resilience import line carries the Pyright suppression."""
    text = gen_publish_py(_params())
    import_lines = [
        line
        for line in text.splitlines()
        if "from cpv_network_resilience import gh_with_retry, git_with_retry"
        in line
    ]
    assert import_lines, "network-resilience import not found in template"
    assert any(
        "pyright: ignore[reportAssignmentType]" in line for line in import_lines
    ), "pyright: ignore not on the import line itself"


def test_151_2_mypy_no_redef_misc_still_on_shims() -> None:
    """The mypy [no-redef, misc] suppression on the fallback shims is kept."""
    text = gen_publish_py(_params())
    assert text.count("# type: ignore[no-redef, misc]") >= 2


# ── #151.3 — every checkout has persist-credentials: false ───────────────────


def test_151_3_every_checkout_has_persist_credentials_false() -> None:
    """Every actions/checkout in ci.yml disables credential persistence."""
    import yaml

    doc = yaml.safe_load(gen_ci_yml(_params()))
    checkouts = 0
    for job_name, job in doc["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses", "")
            if uses.startswith("actions/checkout@"):
                checkouts += 1
                with_block = step.get("with") or {}
                assert with_block.get("persist-credentials") is False, (
                    f"checkout in job {job_name!r} missing "
                    "persist-credentials: false"
                )
    assert checkouts >= 4, f"expected >=4 ci.yml checkouts, found {checkouts}"


# ── #151.4 — commitlint commit sha, not tag-object sha ───────────────────────


def test_151_4_commitlint_uses_commit_sha_not_tag_object() -> None:
    """commitlint is pinned to the commit sha, not the annotated-tag-object sha."""
    text = gen_ci_yml(_params())
    assert (
        f"wagoid/commitlint-github-action@{COMMITLINT_COMMIT_SHA}" in text
    ), "commitlint not pinned to the v6.2.1 commit sha"
    assert (
        COMMITLINT_TAG_OBJECT_SHA not in text
    ), "the v6.2.1 annotated-tag-object sha is still present (ref-version-mismatch)"


# ── #151.5 — a zizmor job exists in canon ci.yml ─────────────────────────────


def test_151_5_zizmor_job_present() -> None:
    """canon ci.yml ships a dedicated zizmor (workflow-security) job."""
    import yaml

    doc = yaml.safe_load(gen_ci_yml(_params()))
    assert "zizmor" in doc["jobs"], "no zizmor job in ci.yml"
    steps = doc["jobs"]["zizmor"].get("steps", [])
    assert any(
        str(step.get("uses", "")).startswith("zizmorcore/zizmor-action@")
        for step in steps
    ), "zizmor job does not run the zizmor-action"


def test_151_5_zizmor_action_is_sha_pinned() -> None:
    """The zizmor-action is SHA-pinned (40-hex commit), not a floating tag."""
    import re

    text = gen_ci_yml(_params())
    m = re.search(r"zizmorcore/zizmor-action@([0-9a-f]+)", text)
    assert m is not None, "zizmor-action pin not found"
    assert len(m.group(1)) == 40, "zizmor-action must be pinned to a full commit sha"


def test_151_5_zizmor_job_has_least_privilege_permissions() -> None:
    """zizmor needs security-events: write (SARIF upload) + contents: read."""
    import yaml

    doc = yaml.safe_load(gen_ci_yml(_params()))
    perms = doc["jobs"]["zizmor"].get("permissions", {})
    assert perms.get("security-events") == "write"
    assert perms.get("contents") == "read"


# ── Mega-Linter / Checkov coverage is NOT removed by re-adding zizmor ─────────


def test_151_5_megalinter_still_present_alongside_zizmor() -> None:
    """Re-adding zizmor keeps Mega-Linter (the issue says keep both)."""
    text = gen_ci_yml(_params())
    assert "oxsecurity/megalinter@" in text
    assert "zizmorcore/zizmor-action@" in text
