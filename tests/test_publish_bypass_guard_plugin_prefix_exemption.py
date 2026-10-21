"""Gate 0 and the integrity skip variables.

History: `_plugin_verify_hashes` renamed `CPV_SKIP_GITHUB_INTEGRITY` to
`PLUGIN_SKIP_GITHUB_INTEGRITY` (TRDD-bbff5bc5), and both names used to be
exempt from publish's bypass guard because publish.py itself set them for the
validator gates. Owner decision 2026-10-21 ("Anchor to candidate") removed that
bypass: the validator gates now verify the candidate against its own committed
manifest, so BOTH spellings must abort a publish. These tests pin that, and pin
that the guard still rejects every other bypass var.

The generated-plugin template (generate_plugin_repo.gen_publish_py) still
exempts both names: its CI workflows set the variable, so changing it is a
separate, open item. The last test below pins the template as it is.
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


def test_renamed_integrity_var_is_refused(monkeypatch):
    """PLUGIN_SKIP_GITHUB_INTEGRITY must abort publish: nothing may switch the integrity check off."""
    monkeypatch.setenv("PLUGIN_SKIP_GITHUB_INTEGRITY", "1")
    assert stage_bypass_guard() == 1


def test_legacy_integrity_var_is_refused(monkeypatch):
    """The legacy CPV_SKIP_GITHUB_INTEGRITY spelling must abort publish too."""
    monkeypatch.setenv("CPV_SKIP_GITHUB_INTEGRITY", "1")
    assert stage_bypass_guard() == 1


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
