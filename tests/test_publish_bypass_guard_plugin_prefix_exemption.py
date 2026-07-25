"""Regression tests: the RENAMED integrity env var must not read as a bypass attempt.

`_plugin_verify_hashes` renamed `CPV_SKIP_GITHUB_INTEGRITY` to
`PLUGIN_SKIP_GITHUB_INTEGRITY` (TRDD-bbff5bc5) and instructs users to export the
new name — but `PLUGIN_SKIP_` is a FORBIDDEN PREFIX in publish's bypass guard, so
following that instruction aborted the publish with "Bypass attempt detected".
These tests pin the exemption, and — the load-bearing half — pin that the guard
still rejects every genuine bypass var, so the exemption cannot silently widen.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402
from publish import stage_bypass_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_bypass_env(monkeypatch):
    """Remove every bypass-shaped var so ambient shell env cannot skew a case."""
    for var in list(os.environ):
        if var.startswith(("PLUGIN_SKIP_", "PLUGIN_FORCE_", "PLUGIN_BYPASS_", "CPV_SKIP_", "SKIP_")) or var == "NO_VERIFY":
            monkeypatch.delenv(var, raising=False)


def test_renamed_integrity_var_is_exempt(monkeypatch):
    """PLUGIN_SKIP_GITHUB_INTEGRITY (the current spelling) must not abort publish."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    assert stage_bypass_guard() == 0


def test_legacy_integrity_var_still_exempt(monkeypatch):
    """The deprecated CPV_SKIP_GITHUB_INTEGRITY spelling must keep working."""
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    assert stage_bypass_guard() == 0


def test_gh_auth_check_var_still_exempt(monkeypatch):
    """CPV_SKIP_GH_AUTH_CHECK remains a documented infrastructure exemption."""
    monkeypatch.setenv("CPV_SKIP_GH_AUTH_CHECK", "1")
    assert stage_bypass_guard() == 0


@pytest.mark.parametrize(
    "var",
    [
        "PLUGIN_SKIP_TESTS",
        "PLUGIN_SKIP_GH_AUTH_CHECK",  # no such var exists — must NOT be exempt
        "PLUGIN_FORCE_PUBLISH",
        "PLUGIN_BYPASS_GATES",
        "CPV_SKIP_GATE7",
        "SKIP_LINT",
        "NO_VERIFY",
    ],
)
def test_genuine_bypass_vars_still_abort(monkeypatch, var):
    """Every real bypass var must still abort — the exemption must not widen."""
    monkeypatch.setenv(var, "1")
    assert stage_bypass_guard() == 1


def test_empty_value_is_not_a_bypass(monkeypatch):
    """An empty-valued bypass var is inert and must not abort (pre-existing behavior)."""
    monkeypatch.setenv("PLUGIN_SKIP_TESTS", "")
    assert stage_bypass_guard() == 0


def test_generated_plugin_publish_inherits_the_exemption():
    """A newly generated plugin's publish.py must carry the renamed exemption too."""
    params = PluginParams(
        name="demo-plugin",
        description="d",
        author="a",
        author_email="a@example.com",
        github_owner="owner",
    )
    generated = gen_publish_py(params)
    assert '"PLUGIN_SKIP_GITHUB_INTEGRITY"' in generated
    assert '"CPV_SKIP_GITHUB_INTEGRITY"' in generated
    assert '"CPV_SKIP_GH_AUTH_CHECK"' in generated
    # The forbidden prefixes must survive — the exemption is additive, not a relaxation.
    assert '"PLUGIN_SKIP_"' in generated
    assert '"CPV_SKIP_"' in generated
