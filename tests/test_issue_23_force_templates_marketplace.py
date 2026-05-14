#!/usr/bin/env python3
"""Issue #23 regression tests.

Bug: ``standardize --force-templates`` clobbered the marketplace name in
``.github/workflows/notify-marketplace.yml`` with the literal placeholder
``my-plugins-marketplace`` and rewrote the secret name with the hardcoded
``MARKETPLACE_PAT``, silently breaking the plugin's marketplace dispatch
chain when the plugin used a real marketplace or a custom secret name.

Fix (v2.85.0):
* ``_detect_existing_notify_marketplace`` extracts ``MARKETPLACE_OWNER``,
  ``MARKETPLACE_REPO``, and ``secrets.<NAME>`` from the pre-existing file
  before backup.
* ``_apply_notify_marketplace_overrides`` plumbs detected values into the
  ``PluginParams`` instance with CLI > detection > defaults precedence.
* ``gen_notify_marketplace_yml`` uses ``p.marketplace_owner`` and
  ``p.marketplace_secret_name`` so the generator emits the right values.
* When ``--force-templates`` would emit the placeholder over a real file,
  the migration refuses and asks the user to pass ``--marketplace``.

Coverage:
* Detector — owner/repo/secret extraction, placeholder filtering, missing
  file, malformed file.
* Generator — secret name and owner overrides flow through.
* Override application — CLI > detection > defaults.
* End-to-end — full migration preserves real marketplace + secret.
* Refusal — --force-templates blocks placeholder emission over a real
  pre-existing file when nothing else resolves the marketplace name.
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import PluginParams, gen_notify_marketplace_yml  # noqa: E402
from standardize_plugin import (  # noqa: E402
    _apply_notify_marketplace_overrides,
    _detect_existing_notify_marketplace,
    fix_missing_files,
)


def _write_notify_yml(plugin_path: Path, content: str) -> Path:
    yml = plugin_path / ".github" / "workflows" / "notify-marketplace.yml"
    yml.parent.mkdir(parents=True, exist_ok=True)
    yml.write_text(content, encoding="utf-8")
    return yml


def _write_plugin_json(plugin_path: Path, name: str = "test-plugin") -> Path:
    cp = plugin_path / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    pj = cp / "plugin.json"
    pj.write_text(
        '{"name": "%s", "version": "0.1.0", "description": "t",\n "author": {"name": "X", "email": "x@y"}}\n' % name,
        encoding="utf-8",
    )
    return pj


# ---------------------------------------------------------------------------
# detector
# ---------------------------------------------------------------------------


def test_detector_extracts_all_three_values(tmp_path):
    """Detector pulls owner, repo, and secret name from a canonical YAML."""
    _write_notify_yml(
        tmp_path,
        "name: Notify Marketplace\n"
        "env:\n"
        "  MARKETPLACE_OWNER: 'Emasoft'\n"
        "  MARKETPLACE_REPO: 'ai-maestro-plugins'\n"
        "jobs:\n"
        "  notify:\n"
        "    steps:\n"
        "      - uses: peter-evans/repository-dispatch@v4\n"
        "        with:\n"
        "          token: ${{ secrets.MARKETPLACE_DISPATCH_TOKEN }}\n",
    )
    got = _detect_existing_notify_marketplace(tmp_path)
    assert got == {
        "owner": "Emasoft",
        "repo": "ai-maestro-plugins",
        "secret_name": "MARKETPLACE_DISPATCH_TOKEN",
    }


def test_detector_filters_canonical_placeholder_repo(tmp_path):
    """Placeholder repo `my-plugins-marketplace` is treated as not-detected."""
    _write_notify_yml(
        tmp_path,
        "env:\n"
        "  MARKETPLACE_OWNER: 'X'\n"
        "  MARKETPLACE_REPO: 'my-plugins-marketplace'\n"
        "  token: ${{ secrets.MARKETPLACE_PAT }}\n",
    )
    got = _detect_existing_notify_marketplace(tmp_path)
    # owner found, repo treated as None (placeholder), secret found
    assert got["owner"] == "X"
    assert got["repo"] is None
    assert got["secret_name"] == "MARKETPLACE_PAT"


def test_detector_returns_all_none_when_file_missing(tmp_path):
    """No notify-marketplace.yml → all detector slots are None."""
    got = _detect_existing_notify_marketplace(tmp_path)
    assert got == {"owner": None, "repo": None, "secret_name": None}


def test_detector_handles_unquoted_values(tmp_path):
    """YAML allows unquoted scalar values — the regex must accept them."""
    _write_notify_yml(
        tmp_path,
        "env:\n  MARKETPLACE_OWNER: bare-owner\n  MARKETPLACE_REPO: bare-repo\n",
    )
    got = _detect_existing_notify_marketplace(tmp_path)
    assert got["owner"] == "bare-owner"
    assert got["repo"] == "bare-repo"


def test_detector_handles_double_quoted_values(tmp_path):
    """Double-quoted form must also parse."""
    _write_notify_yml(
        tmp_path,
        'env:\n  MARKETPLACE_OWNER: "ownerX"\n  MARKETPLACE_REPO: "repoY"\n',
    )
    got = _detect_existing_notify_marketplace(tmp_path)
    assert got["owner"] == "ownerX"
    assert got["repo"] == "repoY"


def test_detector_ignores_non_uppercase_secret_refs(tmp_path):
    """`secrets.lowercase` would not be a real PAT — regex requires UPPER_SNAKE."""
    _write_notify_yml(
        tmp_path,
        "env:\n  MARKETPLACE_OWNER: 'X'\n  MARKETPLACE_REPO: 'Y'\n      token: ${{ secrets.OK_NAME }}\n",
    )
    got = _detect_existing_notify_marketplace(tmp_path)
    assert got["secret_name"] == "OK_NAME"


# ---------------------------------------------------------------------------
# generator output reflects new PluginParams fields
# ---------------------------------------------------------------------------


def test_generator_default_secret_name_is_MARKETPLACE_PAT():
    """Fresh PluginParams keeps the historical default for backward compat."""
    p = PluginParams(
        name="x",
        description="x",
        author="X",
        author_email="x@x",
        github_owner="Emasoft",
        marketplace="my-marketplace",
    )
    out = gen_notify_marketplace_yml(p)
    assert "secrets.MARKETPLACE_PAT" in out
    assert "MARKETPLACE_OWNER: 'Emasoft'" in out
    assert "MARKETPLACE_REPO: 'my-marketplace'" in out


def test_generator_always_emits_canonical_MARKETPLACE_PAT():
    """v2.86.0: the generator ALWAYS emits `secrets.MARKETPLACE_PAT` regardless of plugin.

    Per-plugin secret-name overrides were reverted — single canonical name
    everywhere. Plugins migrating from a deviant name get a loud
    [ACTION REQUIRED] block telling them to rename their gh secret.
    """
    p = PluginParams(
        name="x",
        description="x",
        author="X",
        author_email="x@x",
        github_owner="Emasoft",
        marketplace="ai-maestro-plugins",
    )
    out = gen_notify_marketplace_yml(p)
    assert "secrets.MARKETPLACE_PAT" in out
    assert "secrets.MARKETPLACE_DISPATCH_TOKEN" not in out
    assert "secrets.MARKETPLACE_TOKEN" not in out
    # The header comment mentions only MARKETPLACE_PAT — no other secret names.
    assert "Requires MARKETPLACE_PAT secret" in out


def test_PluginParams_does_not_have_marketplace_secret_name():
    """v2.86.0: the per-plugin secret-name field was reverted."""
    assert "marketplace_secret_name" not in PluginParams.__dataclass_fields__


def test_generator_marketplace_owner_overrides_github_owner():
    """marketplace_owner overrides github_owner in the MARKETPLACE_OWNER env."""
    p = PluginParams(
        name="x",
        description="x",
        author="X",
        author_email="x@x",
        github_owner="PluginAuthor",
        marketplace="some-marketplace",
        marketplace_owner="MarketplaceOrg",
    )
    out = gen_notify_marketplace_yml(p)
    assert "MARKETPLACE_OWNER: 'MarketplaceOrg'" in out
    assert "MARKETPLACE_OWNER: 'PluginAuthor'" not in out


def test_generator_no_placeholder_when_marketplace_set():
    """The literal placeholder must NEVER appear when params.marketplace is set."""
    p = PluginParams(
        name="x",
        description="x",
        author="X",
        author_email="x@x",
        github_owner="Emasoft",
        marketplace="ai-maestro-plugins",
    )
    out = gen_notify_marketplace_yml(p)
    assert "my-plugins-marketplace" not in out


# ---------------------------------------------------------------------------
# override application precedence
# ---------------------------------------------------------------------------


def test_overrides_detection_wins_when_no_cli(tmp_path):
    """Detection populates params (owner/repo) and records secret-name deviation."""
    _write_notify_yml(
        tmp_path,
        "env:\n"
        "  MARKETPLACE_OWNER: 'Emasoft'\n"
        "  MARKETPLACE_REPO: 'ai-maestro-plugins'\n"
        "          token: ${{ secrets.MARKETPLACE_DISPATCH_TOKEN }}\n",
    )
    p = PluginParams(name="x", description="x", author="X", author_email="x@x")
    changes = _apply_notify_marketplace_overrides(p, tmp_path, cli_marketplace=None)
    # Owner + repo flow into params (per-plugin values).
    assert p.marketplace == "ai-maestro-plugins"
    assert p.marketplace_owner == "Emasoft"
    # Each change is recorded with old → new tuple.
    assert changes["marketplace"][1] == "ai-maestro-plugins"
    assert changes["marketplace_owner"][1] == "Emasoft"
    # v2.86.0: secret-name deviation is RECORDED but NOT plumbed back —
    # the canon wins. Caller uses this to emit [ACTION REQUIRED].
    assert "marketplace_secret_name__DEVIATION" in changes
    old_secret, canon = changes["marketplace_secret_name__DEVIATION"]
    assert old_secret == "MARKETPLACE_DISPATCH_TOKEN"
    assert canon == "MARKETPLACE_PAT"


def test_overrides_cli_wins_over_detection(tmp_path):
    """--marketplace=owner/repo CLI flag overrides the detected values."""
    _write_notify_yml(
        tmp_path,
        "env:\n  MARKETPLACE_OWNER: 'Detected'\n  MARKETPLACE_REPO: 'detected-repo'\n",
    )
    p = PluginParams(name="x", description="x", author="X", author_email="x@x")
    _apply_notify_marketplace_overrides(p, tmp_path, cli_marketplace="CLIOwner/cli-repo")
    assert p.marketplace_owner == "CLIOwner"
    assert p.marketplace == "cli-repo"


def test_overrides_returns_empty_when_nothing_detected(tmp_path):
    """No existing file + no CLI flag → no params change, no change record."""
    p = PluginParams(name="x", description="x", author="X", author_email="x@x")
    p_before = (p.marketplace, p.marketplace_owner)
    changes = _apply_notify_marketplace_overrides(p, tmp_path, cli_marketplace=None)
    p_after = (p.marketplace, p.marketplace_owner)
    assert p_before == p_after
    assert changes == {}


# ---------------------------------------------------------------------------
# end-to-end: fix_missing_files preserves real values
# ---------------------------------------------------------------------------


def _audit_item_force_notify():
    """Build the audit-item shape fix_missing_files expects for the notify file."""
    from standardize_plugin import AuditItem

    # The migration path doesn't list notify-marketplace.yml as MISSING when
    # the file exists — force_templates triggers the overwrite branch
    # independently. We seed force-set membership via _FORCE_TEMPLATE_FILES,
    # which contains the path, so an empty audit list still triggers the
    # overwrite under force_templates=True.
    return [
        AuditItem(category="other", name="placeholder", status="PASS", message="seed"),
    ]


def test_force_templates_preserves_real_marketplace_repo_normalizes_secret(tmp_path, capsys):
    """End-to-end: --force-templates preserves owner/repo but normalizes secret to canon.

    v2.86.0 (issue #22): the marketplace OWNER and REPO values are
    plugin-specific and detected from the pre-existing YAML. The secret
    NAME, on the other hand, is canonical and ALWAYS written as
    `MARKETPLACE_PAT`. A deviation in the pre-existing YAML triggers a
    loud [ACTION REQUIRED] block telling the maintainer to rename their
    gh secret to match.
    """
    _write_plugin_json(tmp_path)
    _write_notify_yml(
        tmp_path,
        "env:\n"
        "  MARKETPLACE_OWNER: 'Emasoft'\n"
        "  MARKETPLACE_REPO: 'ai-maestro-plugins'\n"
        "          token: ${{ secrets.MARKETPLACE_DISPATCH_TOKEN }}\n",
    )
    fix_missing_files(
        tmp_path,
        _audit_item_force_notify(),
        dry_run=False,
        marketplace=None,
        force_templates=True,
    )
    written = (tmp_path / ".github/workflows/notify-marketplace.yml").read_text()
    # Per-plugin VALUES preserved.
    assert "MARKETPLACE_REPO: 'ai-maestro-plugins'" in written
    assert "my-plugins-marketplace" not in written
    # Canonical secret NAME enforced — deviation NOT preserved.
    assert "secrets.MARKETPLACE_PAT" in written
    assert "secrets.MARKETPLACE_DISPATCH_TOKEN" not in written
    # [ACTION REQUIRED] block printed to stdout for the maintainer.
    captured = capsys.readouterr().out
    assert "[ACTION REQUIRED]" in captured
    assert "secrets.MARKETPLACE_DISPATCH_TOKEN" in captured
    assert "gh secret set MARKETPLACE_PAT" in captured
    assert '--body "$MARKETPLACE_PAT"' in captured


def test_force_templates_refuses_when_no_marketplace_resolvable(tmp_path, capsys):
    """--force-templates over an unparseable file refuses to ship the placeholder."""
    _write_plugin_json(tmp_path)
    _write_notify_yml(
        tmp_path,
        "# pre-existing file with no detectable marketplace fields\n# this is just a comment\n",
    )
    fix_missing_files(
        tmp_path,
        _audit_item_force_notify(),
        dry_run=False,
        marketplace=None,
        force_templates=True,
    )
    captured = capsys.readouterr().out
    assert "REFUSED" in captured
    # The file should remain untouched.
    after = (tmp_path / ".github/workflows/notify-marketplace.yml").read_text()
    assert "pre-existing file with no detectable" in after
    assert "my-plugins-marketplace" not in after


def test_force_templates_cli_marketplace_overrides_detection(tmp_path):
    """CLI --marketplace=owner/repo wins over a different detected value."""
    _write_plugin_json(tmp_path)
    _write_notify_yml(
        tmp_path,
        "env:\n  MARKETPLACE_OWNER: 'Old'\n  MARKETPLACE_REPO: 'old-repo'\n",
    )
    fix_missing_files(
        tmp_path,
        _audit_item_force_notify(),
        dry_run=False,
        marketplace="New/new-repo",
        force_templates=True,
    )
    written = (tmp_path / ".github/workflows/notify-marketplace.yml").read_text()
    assert "MARKETPLACE_OWNER: 'New'" in written
    assert "MARKETPLACE_REPO: 'new-repo'" in written


def test_force_templates_no_existing_file_uses_placeholder(tmp_path):
    """Fresh scaffold (file does NOT exist) keeps the historical placeholder behavior."""
    _write_plugin_json(tmp_path)
    # No pre-existing notify-marketplace.yml.
    fix_missing_files(
        tmp_path,
        _audit_item_force_notify(),
        dry_run=False,
        marketplace=None,
        force_templates=True,
    )
    yml = tmp_path / ".github/workflows/notify-marketplace.yml"
    assert yml.is_file()
    # Fresh scaffold: placeholder is the documented fallback when nothing else is known.
    # This is the historical no-bug-yet behavior we must not break.
    assert "my-plugins-marketplace" in yml.read_text()
