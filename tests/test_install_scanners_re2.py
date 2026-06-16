"""Tests for J6 — google-re2 install path + scaffolded scan-cache restore step.

Four slices of behavior pinned here:

1. ``cpv_install_scanners.ensure_google_re2()`` follows the same contract as
   every other ``ensure_*`` helper: idempotent on second call, respects the
   per-tool opt-out env var, returns ``bool``, never raises, never installs
   when the module already imports.

2. ``install_all_scanners()`` exposes ``google-re2`` as a key in its return
   dict so the doctor's status table renders it next to the other scanners.

3. ``pyproject.toml`` declares ``performance`` as an optional dep group
   carrying ``google-re2>=1.1.0`` — installable via
   ``pip install claude-plugins-validation[performance]``.

4. ``generate_plugin_repo.gen_ci_yml`` emits an ``actions/cache@v4`` block
   pinned to a full commit SHA, restoring ``~/.cache/cpv`` keyed on
   ``hashFiles('**/.cpv-self-hashes.json')``, parses as valid YAML, and
   leaves the existing Lint / Test jobs untouched.

NO test in this module actually installs google-re2 — we mock ``subprocess.run``
and ``importlib.util.find_spec`` so the test suite is hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_install_scanners as cis  # noqa: E402
from generate_plugin_repo import PluginParams, gen_ci_yml  # noqa: E402

# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _default_params(**overrides: object) -> PluginParams:
    """Minimal PluginParams instance for gen_ci_yml tests."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin for J6 tests",
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


# ─────────────────────────────────────────────────────────────────────
# Part A — ensure_google_re2() contract
# ─────────────────────────────────────────────────────────────────────


class TestEnsureGoogleRe2Idempotency:
    """ensure_google_re2 must be a no-op when re2 already imports."""

    def test_returns_true_when_already_importable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First call: re2 IS importable → True, NO pip invocation."""
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: name == "re2")
        called: list[Any] = []
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
        assert cis.ensure_google_re2() is True
        assert called == [], "no pip install should fire when re2 is already importable"

    def test_idempotent_on_second_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call returns the same True without re-invoking pip."""
        # First state: re2 already there.
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: True)
        called: list[Any] = []
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
        first = cis.ensure_google_re2()
        second = cis.ensure_google_re2()
        assert first is True
        assert second is True
        assert called == [], "idempotent: zero pip invocations across two calls"


class TestEnsureGoogleRe2OptOut:
    """CPV_NO_GOOGLE_RE2_INSTALL=1 must bypass the installer entirely."""

    def test_opt_out_skips_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)
        monkeypatch.setenv("CPV_NO_GOOGLE_RE2_INSTALL", "1")
        called: list[Any] = []
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
        assert cis.ensure_google_re2() is False
        assert called == [], "opt-out must short-circuit BEFORE any pip invocation"

    def test_opt_out_truthy_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Every form _opt_out accepts ('1', 'true', 'yes', 'on') must skip install."""
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)
        for value in ("1", "true", "TRUE", "yes", "Yes", "on", "ON"):
            monkeypatch.setenv("CPV_NO_GOOGLE_RE2_INSTALL", value)
            called: list[Any] = []
            monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: called.append(a))
            assert cis.ensure_google_re2() is False, f"opt-out value {value!r} must return False"
            assert called == [], f"opt-out value {value!r} must skip install"


class TestEnsureGoogleRe2InstallPath:
    """When not installed and not opted-out, pip is invoked exactly once."""

    def test_invokes_pip_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_NO_GOOGLE_RE2_INSTALL", raising=False)
        # importability flips from False (pre-install) to True (post-install)
        seen = {"calls": 0}

        def fake_find_spec(name: str) -> bool:
            seen["calls"] += 1
            # First probe: not installed; subsequent: installed.
            return seen["calls"] >= 2

        monkeypatch.setattr(cis, "_is_module_importable", fake_find_spec)
        run_calls: list[list[str]] = []

        def fake_run(argv: list[str], *a: Any, **kw: Any) -> bool:
            run_calls.append(argv)
            return True

        monkeypatch.setattr(cis, "_silent_run", fake_run)
        result = cis.ensure_google_re2()
        assert result is True
        assert len(run_calls) == 1, "pip must be invoked exactly once"
        # pip command shape: [<python>, '-m', 'pip', 'install', '--user', 'google-re2']
        assert run_calls[0][1:] == ["-m", "pip", "install", "--user", "google-re2"]

    def test_returns_false_when_install_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pip ran but re2 still not importable → False (graceful degrade)."""
        monkeypatch.delenv("CPV_NO_GOOGLE_RE2_INSTALL", raising=False)
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: False)
        assert cis.ensure_google_re2() is False


class TestEnsureGoogleRe2Signature:
    """Function signature must match the rest of the ensure_* family."""

    def test_function_exists(self) -> None:
        assert hasattr(cis, "ensure_google_re2"), "ensure_google_re2 must be exported"

    def test_returns_bool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Even on the failure path, the function MUST return bool, never None."""
        monkeypatch.delenv("CPV_NO_GOOGLE_RE2_INSTALL", raising=False)
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)
        monkeypatch.setattr(cis, "_silent_run", lambda *a, **kw: False)
        result = cis.ensure_google_re2()
        assert isinstance(result, bool), f"must return bool, got {type(result)}"

    def test_in_all_export(self) -> None:
        """__all__ must list ensure_google_re2 so * imports pick it up."""
        assert "ensure_google_re2" in cis.__all__

    def test_never_raises_on_subprocess_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same hard guarantee as every other ensure_*: NO exceptions escape.

        We mock at the real subprocess.run boundary so the function exercises
        its production path through _silent_run — that helper documents
        "never raises" and the contract must hold transitively for
        ensure_google_re2.
        """
        monkeypatch.delenv("CPV_NO_GOOGLE_RE2_INSTALL", raising=False)
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)

        def raising_subprocess_run(*a: Any, **kw: Any) -> Any:
            raise OSError("simulated pip blow-up")

        # _silent_run itself catches OSError/TimeoutExpired/FileNotFoundError.
        monkeypatch.setattr(cis.subprocess, "run", raising_subprocess_run)
        try:
            result = cis.ensure_google_re2()
        except Exception as exc:
            pytest.fail(f"ensure_google_re2 must never raise; got {type(exc).__name__}: {exc}")
        assert isinstance(result, bool)
        # Install attempt failed silently → False
        assert result is False


# ─────────────────────────────────────────────────────────────────────
# Part A — _is_module_importable helper
# ─────────────────────────────────────────────────────────────────────


class TestIsModuleImportable:
    """The thin importlib.util.find_spec wrapper must be exception-safe."""

    def test_known_stdlib_module_returns_true(self) -> None:
        """A module that definitely exists must return True."""
        assert cis._is_module_importable("json") is True

    def test_missing_module_returns_false(self) -> None:
        """A module name that cannot exist returns False, not an exception."""
        assert cis._is_module_importable("definitely_not_a_real_module_xyz_j6") is False

    def test_invalid_name_returns_false(self) -> None:
        """An invalid module spec (e.g. empty string) returns False, not ValueError."""
        # Empty string raises ValueError inside find_spec; helper must swallow.
        assert cis._is_module_importable("") is False


# ─────────────────────────────────────────────────────────────────────
# Part A — install_all_scanners batch helper
# ─────────────────────────────────────────────────────────────────────


class TestInstallAllScannersIncludesGoogleRe2:
    """google-re2 must show up as a key in the batch installer's result."""

    def test_google_re2_in_returned_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """install_all_scanners() exposes google-re2 alongside the other 6 tools."""
        # All scanners already installed; no cascades fire.
        monkeypatch.setattr(cis.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: True)
        statuses = cis.install_all_scanners()
        assert "google-re2" in statuses
        assert statuses["google-re2"] is True

    def test_all_seven_keys_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Adding google-re2 must NOT drop any of the 6 pre-existing scanners."""
        monkeypatch.setattr(cis.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: True)
        statuses = cis.install_all_scanners()
        assert set(statuses.keys()) == {
            "fclones",
            "cc-audit",
            "trufflehog",
            "semgrep",
            "tirith",
            "skill-scanner",
            "google-re2",
        }

    def test_google_re2_false_when_opted_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the opt-out is set, install_all_scanners reports False."""
        monkeypatch.setattr(cis.shutil, "which", lambda name: None)
        monkeypatch.setattr(cis, "_is_module_importable", lambda name: False)
        for var in (
            "CPV_NO_FCLONES_INSTALL",
            "CPV_NO_CC_AUDIT_INSTALL",
            "CPV_NO_TRUFFLEHOG_INSTALL",
            "CPV_NO_SEMGREP_INSTALL",
            "CPV_NO_TIRITH_INSTALL",
            "CPV_NO_CISCO_INSTALL",
            "CPV_NO_GOOGLE_RE2_INSTALL",
        ):
            monkeypatch.setenv(var, "1")
        statuses = cis.install_all_scanners()
        assert statuses["google-re2"] is False
        # And every other scanner correctly reports False too.
        assert all(v is False for v in statuses.values()), statuses


# ─────────────────────────────────────────────────────────────────────
# Part B — pyproject.toml optional dependency declaration
# ─────────────────────────────────────────────────────────────────────


class TestPyprojectOptionalPerformanceDep:
    """The `performance` extra must carry google-re2 with a sane lower bound."""

    @pytest.fixture(scope="class")
    def pyproject_content(self) -> str:
        return (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    def test_performance_section_exists(self, pyproject_content: str) -> None:
        """The [project.optional-dependencies] table must declare a `performance` key."""
        assert "[project.optional-dependencies]" in pyproject_content, (
            "must declare a [project.optional-dependencies] table"
        )
        # The key 'performance' must appear after the table header.
        idx = pyproject_content.index("[project.optional-dependencies]")
        rest = pyproject_content[idx:]
        # Stop at the next top-level [section] so we only check inside the table.
        next_section = rest.find("\n[", 1)
        block = rest if next_section == -1 else rest[:next_section]
        assert "performance" in block, "the `performance` key must live inside the optional-dependencies table"

    def test_performance_pulls_google_re2(self, pyproject_content: str) -> None:
        """`performance` must explicitly list google-re2 with a version pin."""
        idx = pyproject_content.index("[project.optional-dependencies]")
        rest = pyproject_content[idx:]
        next_section = rest.find("\n[", 1)
        block = rest if next_section == -1 else rest[:next_section]
        assert "google-re2" in block, "google-re2 must be declared inside [project.optional-dependencies]"
        # Lower bound: anything semver-shaped >=1.0 is fine, but we want a pin.
        assert ">=1." in block or ">= 1." in block, (
            "google-re2 must have an explicit >=1.x version lower bound, not a bare unversioned reference"
        )

    def test_performance_is_parseable_toml(self, pyproject_content: str) -> None:
        """The whole pyproject.toml must still parse as valid TOML."""
        try:
            import tomllib  # noqa: PLC0415  # Python 3.11+
        except ImportError:  # pragma: no cover — Python < 3.11
            pytest.skip("tomllib unavailable on this Python")
        parsed = tomllib.loads(pyproject_content)
        opt_deps = parsed.get("project", {}).get("optional-dependencies", {})
        assert "performance" in opt_deps, "TOML parse must surface the performance extra"
        perf_list = opt_deps["performance"]
        assert isinstance(perf_list, list), "the performance extra must be a list of requirement specifiers"
        joined = " ".join(perf_list)
        assert "google-re2" in joined, "google-re2 must appear in the performance extra's requirement list"


# ─────────────────────────────────────────────────────────────────────
# Part C — gen_ci_yml emits a SHA-pinned actions/cache for ~/.cache/cpv
# ─────────────────────────────────────────────────────────────────────


class TestGenCiYmlScanCacheBlock:
    """gen_ci_yml must scaffold a SHA-pinned actions/cache step inside the Validate job."""

    @pytest.fixture(scope="class")
    def ci_yml(self) -> str:
        return gen_ci_yml(_default_params())

    def test_includes_actions_cache_sha_pin_with_version_comment(self, ci_yml: str) -> None:
        """The Validate job must use a SHA-pinned actions/cache with a version comment.

        Version-agnostic on purpose: canon bumps the cache action over time
        (v4.x → v5.x …) — what must hold is a 40-hex SHA pin carrying a
        pinact-compatible ``# vMAJOR.MINOR[.PATCH]`` comment, not a specific
        major version.
        """
        import re  # noqa: PLC0415

        # SHA-pinned form per gh-actions.md §"Pin third-party actions to a full commit SHA"
        assert "actions/cache@" in ci_yml, "gen_ci_yml must emit an actions/cache step"
        # Pin comment carries the version for pinact-compatible re-syncing —
        # any vX.Y[.Z], not a hardcoded major.
        assert re.search(r"actions/cache@[0-9a-f]{40}\s*#\s*v\d+\.\d+", ci_yml), (
            "actions/cache must be SHA-pinned with a `# vX.Y[.Z]` pinact comment"
        )

    def test_cache_step_path_is_cpv_cache(self, ci_yml: str) -> None:
        """The cached path must be ~/.cache/cpv (scan-cache root)."""
        assert "path: ~/.cache/cpv" in ci_yml, "actions/cache step must persist ~/.cache/cpv across runs"

    def test_cache_key_uses_self_hashes_file(self, ci_yml: str) -> None:
        """Cache key must include hashFiles('**/.cpv-self-hashes.json')."""
        # The braces are doubled because gen_ci_yml is an f-string template.
        assert "hashFiles('**/.cpv-self-hashes.json')" in ci_yml, (
            "cache key must be busted whenever CPV's self-hashes file changes"
        )
        assert "cpv-scan-cache-" in ci_yml, "cache key must follow the cpv-scan-cache-<os>-<hash> namespace"

    def test_cache_step_has_restore_keys_fallback(self, ci_yml: str) -> None:
        """restore-keys must offer a same-OS fallback for first-warm-cache hits."""
        assert "restore-keys:" in ci_yml, "actions/cache must declare restore-keys"
        # Same-OS prefix without the hash → at least a partial cache hit on bumps.
        assert "cpv-scan-cache-${{ runner.os }}-" in ci_yml, (
            "restore-keys must include the same-OS prefix as the cold-bump fallback"
        )

    def test_cache_step_precedes_validation_run(self, ci_yml: str) -> None:
        """The cache restore must be ordered BEFORE the validation run step."""
        cache_idx = ci_yml.index("Restore CPV scan-cache")
        run_idx = ci_yml.index("Run plugin validation")
        assert cache_idx < run_idx, "the Restore CPV scan-cache step must come BEFORE Run plugin validation"

    def test_ci_yml_parses_as_valid_yaml(self, ci_yml: str) -> None:
        """The whole generated workflow must round-trip through yaml.safe_load."""
        parsed = yaml.safe_load(ci_yml)
        assert isinstance(parsed, dict), "ci.yml must parse as a top-level mapping"
        assert "jobs" in parsed, "ci.yml must declare jobs"
        validate_job = parsed["jobs"].get("validate")
        assert validate_job is not None, "validate job must be present"
        # Find the cache step among the validate job's steps.
        steps = validate_job.get("steps", [])
        cache_steps = [s for s in steps if isinstance(s, dict) and "cache" in s.get("uses", "")]
        assert len(cache_steps) == 1, (
            f"validate job must declare exactly one actions/cache step; got {len(cache_steps)}"
        )
        cache_step = cache_steps[0]
        with_block = cache_step.get("with", {})
        assert with_block.get("path") == "~/.cache/cpv", (
            f"cached path must be ~/.cache/cpv; got {with_block.get('path')!r}"
        )

    def test_ci_yml_lint_and_test_jobs_unchanged(self, ci_yml: str) -> None:
        """Lint and Test jobs must still be present (not accidentally removed)."""
        parsed = yaml.safe_load(ci_yml)
        jobs = parsed.get("jobs", {})
        assert "lint" in jobs, "lint job must remain in scaffold"
        assert "test" in jobs, "test job must remain in scaffold"
        # Lint job name pinned to "Lint" — cpv-setup-branch-rules reads it verbatim.
        assert jobs["lint"].get("name") == "Lint", "lint job display name must stay 'Lint'"
        assert jobs["test"].get("name") == "Test", "test job display name must stay 'Test'"
        # Test job must NOT have inherited a cache step (only validate gets it).
        test_steps = jobs["test"].get("steps", [])
        test_cache_steps = [s for s in test_steps if isinstance(s, dict) and "actions/cache" in s.get("uses", "")]
        assert test_cache_steps == [], "actions/cache step must be confined to validate job, not test job"

    def test_ci_yml_cache_step_uses_sha_pin(self, ci_yml: str) -> None:
        """actions/cache must be pinned to a full 40-char commit SHA (security)."""
        import re  # noqa: PLC0415

        # Match `uses: actions/cache@<40-hex>` — SHA pin form.
        sha_pattern = re.compile(r"uses:\s*actions/cache@[a-f0-9]{40}")
        assert sha_pattern.search(ci_yml) is not None, "actions/cache must be SHA-pinned (40-char hex), not tag-pinned"
