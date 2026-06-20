"""Regression tests for three canon-generator bug fixes (GitHub #138/#139/#140).

Each issue was traced by the reporter to a wrong byte the generator emits into a
scaffolded plugin's pipeline files:

  * #140 (CRITICAL — breaks CI for every canon plugin): the generated ``ci.yml``
    and ``release.yml`` CPV-validate steps set
    ``CLAUDE_PRIVATE_USERNAMES: "${{ github.repository_owner }}"``. That env var
    is the list of usernames to treat as PRIVATE, so seeding it with the PUBLIC
    repo owner makes CPV flag every ``github.com/<owner>/`` URL + the owner's
    git no-reply email as CRITICAL "private path leaked" → ``--strict`` CI fails.
    FIX: the line (and its stale explaining comment) is removed from BOTH
    workflows; ``PLUGIN_SKIP_GITHUB_INTEGRITY: "1"`` stays.

  * #139 (breaks ``standardize`` via uvx): ``_FALLBACK_CPV_REF`` was ``"main"``
    but CPV's default branch is ``master`` (no ``main`` ref), and
    ``_default_cpv_ref()`` only read ``.claude-plugin/plugin.json`` — absent in a
    pip/uvx-installed layout — so it degraded to the bogus ``main`` ref →
    ``uvx --from git+...@main`` 404'd. FIX: ``_default_cpv_ref()`` tries the
    installed-package version FIRST, then the in-repo plugin.json, then the
    fallback; the fallback is now ``master``. Generated git-source callsites
    must contain NO ``@main``.

  * #138 (gitleaks git-history FP): ``gen_mega_linter_yml`` hard-coded
    ``- REPOSITORY_GITLEAKS``. MegaLinter runs gitleaks in repository mode (full
    git HISTORY), so a security-teaching plugin with example secrets in docs —
    even in deleted/old commits — fails the Lint job on FPs unfixable in the
    working tree. TruffleHog (publish.py) already covers secrets. FIX: the
    linter is dropped from ENABLE_LINTERS; the other linters stay.

Every guard is TWO-SIDED where it matters: the wrong byte is asserted ABSENT and
the right neighbouring behavior is asserted PRESENT, so a regression in either
direction fails a test.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as g  # noqa: E402
from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    cpv_uvx_from_arg,
    gen_ci_yml,
    gen_mega_linter_yml,
    gen_publish_py,
    gen_release_yml,
)

_CPV_GIT_URL = "git+https://github.com/Emasoft/claude-plugins-validation"


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


# ── #140: CLAUDE_PRIVATE_USERNAMES must NOT be set on the validate steps ──────


def test_140_ci_yml_no_claude_private_usernames() -> None:
    """gen_ci_yml must NOT seed CLAUDE_PRIVATE_USERNAMES with the repo owner."""
    ci = gen_ci_yml(_params())
    assert "CLAUDE_PRIVATE_USERNAMES" not in ci, (
        "the inverted private-usernames env (issue #140) leaked back into ci.yml — "
        "it makes CPV flag every github.com/<owner>/ URL as a private-path leak"
    )


def test_140_ci_yml_keeps_skip_github_integrity() -> None:
    """gen_ci_yml still sets PLUGIN_SKIP_GITHUB_INTEGRITY (the keep-this env)."""
    ci = gen_ci_yml(_params())
    assert 'PLUGIN_SKIP_GITHUB_INTEGRITY: "1"' in ci


def test_140_release_yml_no_claude_private_usernames() -> None:
    """gen_release_yml must NOT seed CLAUDE_PRIVATE_USERNAMES with the repo owner."""
    rel = gen_release_yml(_params())
    assert "CLAUDE_PRIVATE_USERNAMES" not in rel, (
        "the inverted private-usernames env (issue #140) leaked back into release.yml"
    )


def test_140_release_yml_keeps_skip_github_integrity() -> None:
    """gen_release_yml still sets PLUGIN_SKIP_GITHUB_INTEGRITY (the keep-this env)."""
    rel = gen_release_yml(_params())
    assert 'PLUGIN_SKIP_GITHUB_INTEGRITY: "1"' in rel


def test_140_neither_workflow_passes_repo_owner_to_private_list() -> None:
    """No validate step pipes github.repository_owner into a private-usernames env."""
    for text in (gen_ci_yml(_params()), gen_release_yml(_params())):
        assert "CLAUDE_PRIVATE_USERNAMES" not in text
        # And the specific inverted spelling never appears, comment or not.
        assert 'CLAUDE_PRIVATE_USERNAMES: "${{ github.repository_owner }}"' not in text


# ── #139: _default_cpv_ref robust resolution + master fallback ───────────────


def test_139_fallback_ref_is_master_not_main() -> None:
    """_FALLBACK_CPV_REF is 'master' (CPV's real default branch), not 'main'."""
    assert g._FALLBACK_CPV_REF == "master"


def test_139_default_cpv_ref_returns_version_string() -> None:
    """With CPV importable (in-repo), _default_cpv_ref() returns a v-prefixed version."""
    ref = g._default_cpv_ref()
    assert re.match(r"^v[0-9]", ref), f"expected a v-prefixed version, got {ref!r}"
    assert ref != "main"


def test_139_default_cpv_ref_prefers_package_metadata(monkeypatch) -> None:
    """When the package version resolves, _default_cpv_ref() uses it (v-prefixed)."""

    def fake_version(_dist: str) -> str:
        return "2.222.0"

    monkeypatch.setattr(g.importlib.metadata, "version", fake_version)
    assert g._default_cpv_ref() == "v2.222.0"


def test_139_default_cpv_ref_handles_already_v_prefixed_metadata(monkeypatch) -> None:
    """A package version already carrying a leading 'v' is not double-prefixed."""

    def fake_version(_dist: str) -> str:
        return "v2.222.0"

    monkeypatch.setattr(g.importlib.metadata, "version", fake_version)
    assert g._default_cpv_ref() == "v2.222.0"


def test_139_default_cpv_ref_falls_back_to_master_when_uninspectable(monkeypatch) -> None:
    """No package metadata AND no readable plugin.json → the master fallback, never main."""

    def raise_not_found(_dist: str):
        raise g.importlib.metadata.PackageNotFoundError("claude-plugins-validation")

    def raise_oserror(*_a, **_k):
        raise OSError("plugin.json unavailable in this layout")

    monkeypatch.setattr(g.importlib.metadata, "version", raise_not_found)
    # Force the in-repo plugin.json read to fail too (simulating the pip layout).
    monkeypatch.setattr(g.Path, "read_text", raise_oserror)
    ref = g._default_cpv_ref()
    assert ref == "master"
    assert ref != "main"


def test_139_git_callsites_contain_no_at_main() -> None:
    """publish.py / ci.yml / release.yml at the DEFAULT git source pin a version, never @main."""
    p = _params()
    assert p.cpv_source == "git"  # default source unchanged
    for text in (gen_publish_py(p), gen_ci_yml(p), gen_release_yml(p)):
        assert "@main" not in text
        assert "claude-plugins-validation@main" not in text
        # The pin is the v-prefixed version resolved by _default_cpv_ref().
        assert f"{_CPV_GIT_URL}@{p.cpv_ref_resolved}" in text


def test_139_git_from_arg_no_main() -> None:
    """The shared cpv_uvx_from_arg helper (git source) never emits @main."""
    from_arg = cpv_uvx_from_arg(_params())
    assert "@main" not in from_arg
    assert from_arg.startswith(f"{_CPV_GIT_URL}@v")


def test_139_pypi_path_not_broken_by_master_fallback() -> None:
    """#137 pypi path still strips the leading v (wheel form); a branch ref → bare dist."""
    # A concrete version under pypi → bare wheel version (no leading v).
    pv = cpv_uvx_from_arg(_params(cpv_source="pypi", cpv_ref="v2.137.0"))
    assert pv == "claude-plugins-validation==2.137.0"
    # The 'master' branch fallback is NOT a published wheel version, so the pypi
    # form must degrade to the bare dist name — never an unsatisfiable ==master.
    pm = cpv_uvx_from_arg(_params(cpv_source="pypi", cpv_ref="master"))
    assert pm == "claude-plugins-validation"
    assert "==master" not in pm


# ── #138: REPOSITORY_GITLEAKS removed from the MegaLinter config ──────────────


def test_138_mega_linter_yml_no_repository_gitleaks() -> None:
    """gen_mega_linter_yml must NOT enable REPOSITORY_GITLEAKS (git-history FP)."""
    ml = gen_mega_linter_yml(_params())
    # The ENABLE_LINTERS entry is gone; allow the explanatory comment to remain.
    assert "\n  - REPOSITORY_GITLEAKS" not in ml, (
        "gitleaks is still enabled in ENABLE_LINTERS (issue #138) — it runs in "
        "repository mode over full git history and FP-fails docs with example secrets"
    )


def test_138_mega_linter_yml_keeps_other_linters() -> None:
    """Dropping gitleaks leaves the rest of the linter list intact."""
    ml = gen_mega_linter_yml(_params())
    for keep in (
        "\n  - PYTHON_RUFF",
        "\n  - BASH_SHELLCHECK",
        "\n  - REPOSITORY_CHECKOV",
        "\n  - REPOSITORY_TRIVY",
        "\n  - MARKDOWN_MARKDOWNLINT",
    ):
        assert keep in ml, f"expected linter entry {keep!r} to remain enabled"
