#!/usr/bin/env python3
"""Tests for GitHub issues #144 + #145 — the STANDARDIZE half: profile-aware
``--force-templates``.

Bug (#145b / #144Bb): ``standardize --fix --force-templates`` force-overwrote
EVERY file in ``_FORCE_TEMPLATE_FILES`` (publish.py, ci/release/notify
workflows, cliff.toml, .mega-linter.yml, .markdownlint.json) WITHOUT checking
whether the plugin's copy is already at/AHEAD of canon — so a hardened /
ahead-of-canon plugin could not run the prescribed upgrade without being
DOWNGRADED. The VALIDATOR already detects this (it emits "at or AHEAD of canon …
Do NOT --force-templates"); the standardizer ignored it.

Fix 3: in the force-overwrite codepath, for each file, classify its drift
direction vs the freshly-generated profile-appropriate canon by REUSING
``validate_plugin._classify_drift_direction`` (read-only). ``ahead``/``mixed`` →
SKIP the overwrite (leave the plugin's file) with a clear line; ``behind``/
``plain`` → overwrite as before. ALSO skip any file the plugin lists in its
``cpv.pipeline.intentional_divergence`` manifest array (authored by C3),
regardless of drift direction.

Every test is two-sided:
  * an ahead-of-canon file is SKIPPED (untouched + skip message) AND
  * a behind/plain file is still OVERWRITTEN as before, AND
  * a marked-divergent file is SKIPPED regardless of direction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import PluginParams, gen_release_yml  # noqa: E402
from standardize_plugin import (  # noqa: E402
    _PIPELINE_DRIFT_RC,
    _force_template_skip_reason,
    _manifest_intentional_divergence,
    fix_missing_files,
)

# The canon release.yml is the integration target: it lives in both
# _FORCE_TEMPLATE_FILES and _FILE_TO_GENERATOR, and it carries genuine hardening
# markers (timeout-minutes, permissions:, SHA-pins) so a removed line yields a
# deterministic "behind" and an added marker line yields a deterministic
# "ahead".
RELEASE_REL = ".github/workflows/release.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_plugin_json(root: Path, *, divergence: list[str] | None = None) -> None:
    """Lay down a minimal valid plugin.json, optionally with an
    ``cpv.pipeline.intentional_divergence`` list."""
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "name": "plug",
        "version": "0.1.0",
        "description": "t",
        "author": {"name": "X", "email": "x@y"},
    }
    if divergence is not None:
        manifest["cpv"] = {"pipeline": {"intentional_divergence": divergence}}
    (cp / "plugin.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _canon_release(root: Path) -> str:
    """The exact canon release.yml the upgrade path would generate for this tree."""
    p = PluginParams(  # type: ignore[call-arg]
        name="plug",
        description="t",
        author="X",
        author_email="x@y",
    )
    return gen_release_yml(p)


def _write_release(root: Path, content: str) -> Path:
    path = root / RELEASE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _ahead_variant(canon: str) -> str:
    """Canon + a plugin-only line carrying a hardening marker → ``ahead``.

    The added line is a `+` line in unified_diff(canon, plugin) whose body
    contains a hardening marker (``timeout-minutes``), so the classifier sees
    plugin-has-extra-hardening and no canon-only hardening → ``ahead``.
    """
    return canon + "\n# extra plugin hardening: timeout-minutes belt-and-braces\n"


def _behind_variant(canon: str) -> str:
    """Canon with its hardening ``timeout-minutes`` line REMOVED → ``behind``.

    The removed line is a `-` line carrying a hardening marker → the classifier
    sees canon-has-extra-hardening and no plugin-only hardening → ``behind``.
    """
    lines = canon.splitlines()
    kept = [ln for ln in lines if "timeout-minutes" not in ln]
    assert len(kept) < len(lines), "fixture invalid: canon release.yml has no timeout-minutes line to remove"
    return "\n".join(kept) + "\n"


def _plain_variant(canon: str) -> str:
    """Canon with one benign (non-hardening) comment line changed → ``plain``.

    Neither side gains/loses a hardening marker, so the classifier returns
    ``plain`` (the ordinary stale-file case) → overwrite.
    """
    import re

    hard = (
        "git push --atomic",
        "SHA-pin",
        "actionlint",
        "commitlint-github-action",
        "wagoid/commitlint",
        "rhysd/actionlint",
        "timeout-minutes",
        "attest-build-provenance",
        "sbom-action",
        "SHA256SUMS",
        "persist-credentials: false",
        "permissions:",
        "MARKETPLACE_PAT",
    )
    sha = re.compile(r"uses:\s*\S+@[0-9a-f]{40}\b")
    out = []
    changed = False
    for ln in canon.splitlines():
        if (
            not changed
            and ln.strip().startswith("#")
            and not any(m in ln for m in hard)
            and not sha.search(ln)
        ):
            out.append(ln + " (locally edited prose)")
            changed = True
        else:
            out.append(ln)
    assert changed, "fixture invalid: canon release.yml has no benign comment line to edit"
    return "\n".join(out) + "\n"


# ===========================================================================
# Unit tests — _force_template_skip_reason on hand-crafted canon vs plugin
# (full control over the diff → deterministic direction)
# ===========================================================================


def test_skip_helper_ahead_returns_skip_line(tmp_path):
    """Plugin copy AHEAD of canon → returns the 'would downgrade' skip line."""
    canon = "permissions: read\nname: x\n"
    plugin = canon + "timeout-minutes: 30\n"  # plugin adds a hardening marker
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plugin, encoding="utf-8")
    line = _force_template_skip_reason(f, RELEASE_REL, canon, set())
    assert line is not None, "an ahead-of-canon file must be skipped"
    assert "at/AHEAD of canon (would downgrade)" in line
    assert _PIPELINE_DRIFT_RC in line
    assert RELEASE_REL in line


def test_skip_helper_mixed_returns_skip_line(tmp_path):
    """Hardening on BOTH sides (mixed) → skip (never downgrade when both harden)."""
    # canon has SHA256SUMS hardening the plugin lacks (a `-` marker) AND the
    # plugin has timeout-minutes hardening canon lacks (a `+` marker) → mixed.
    canon = "name: x\nstep: SHA256SUMS\n"
    plugin = "name: x\ntimeout-minutes: 30\n"
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plugin, encoding="utf-8")
    line = _force_template_skip_reason(f, RELEASE_REL, canon, set())
    assert line is not None, "a mixed-hardening file must be skipped (never downgrade)"
    assert "at/AHEAD of canon (would downgrade)" in line


def test_skip_helper_behind_returns_none(tmp_path):
    """Plugin copy BEHIND canon → None (overwrite as before)."""
    canon = "name: x\ntimeout-minutes: 30\n"  # canon carries the hardening marker
    plugin = "name: x\n"  # plugin lacks it
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plugin, encoding="utf-8")
    assert _force_template_skip_reason(f, RELEASE_REL, canon, set()) is None


def test_skip_helper_plain_returns_none(tmp_path):
    """Plain stale drift (no hardening markers either side) → None (overwrite)."""
    canon = "name: x\n# a benign comment\n"
    plugin = "name: x\n# a different benign comment\n"
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plugin, encoding="utf-8")
    assert _force_template_skip_reason(f, RELEASE_REL, canon, set()) is None


def test_skip_helper_identical_returns_none(tmp_path):
    """Byte-identical plugin/canon → None (a harmless no-op rewrite, not a skip)."""
    canon = "name: x\ntimeout-minutes: 30\n"
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(canon, encoding="utf-8")
    assert _force_template_skip_reason(f, RELEASE_REL, canon, set()) is None


def test_skip_helper_absent_file_returns_none(tmp_path):
    """An absent plugin file is never skipped (a new file must be written)."""
    f = tmp_path / RELEASE_REL  # not created
    assert _force_template_skip_reason(f, RELEASE_REL, "name: x\n", set()) is None


def test_skip_helper_divergence_skips_even_when_behind(tmp_path):
    """A file in the divergence set is skipped regardless of drift direction."""
    canon = "name: x\ntimeout-minutes: 30\n"  # plugin would be BEHIND…
    plugin = "name: x\n"
    f = tmp_path / RELEASE_REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(plugin, encoding="utf-8")
    line = _force_template_skip_reason(f, RELEASE_REL, canon, {RELEASE_REL})
    assert line is not None, "a marked-divergent file must be skipped even when behind canon"
    assert "marked intentional_divergence" in line
    assert RELEASE_REL in line
    # The divergence wording, NOT the drift wording, is used.
    assert "would downgrade" not in line


# ===========================================================================
# Manifest reader — _manifest_intentional_divergence
# ===========================================================================


def test_divergence_reader_parses_list():
    """A well-formed cpv.pipeline.intentional_divergence list is returned as a set."""
    m = {"cpv": {"pipeline": {"intentional_divergence": ["cliff.toml", RELEASE_REL]}}}
    assert _manifest_intentional_divergence(m) == {"cliff.toml", RELEASE_REL}


def test_divergence_reader_missing_key_is_empty():
    """No cpv key / no pipeline key / no list → empty set (conservative direction)."""
    assert _manifest_intentional_divergence({}) == set()
    assert _manifest_intentional_divergence({"cpv": {}}) == set()
    assert _manifest_intentional_divergence({"cpv": {"pipeline": {}}}) == set()


def test_divergence_reader_malformed_value_is_empty():
    """A non-list value or non-string elements never silently suppress an overwrite."""
    assert _manifest_intentional_divergence({"cpv": {"pipeline": {"intentional_divergence": "cliff.toml"}}}) == set()
    assert _manifest_intentional_divergence({"cpv": "notadict"}) == set()
    # Non-string elements are dropped; valid strings kept.
    assert _manifest_intentional_divergence(
        {"cpv": {"pipeline": {"intentional_divergence": ["cliff.toml", 7, None]}}}
    ) == {"cliff.toml"}


# ===========================================================================
# Integration — fix_missing_files(force_templates=True) with the REAL generator
# ===========================================================================


def test_force_templates_skips_ahead_release_yml(tmp_path, capsys):
    """--force-templates LEAVES an ahead-of-canon release.yml untouched + warns."""
    _write_plugin_json(tmp_path)
    canon = _canon_release(tmp_path)
    ahead = _ahead_variant(canon)
    _write_release(tmp_path, ahead)

    fix_missing_files(tmp_path, results=[], force_templates=True)

    after = (tmp_path / RELEASE_REL).read_text(encoding="utf-8")
    assert after == ahead, "an ahead-of-canon release.yml must NOT be force-overwritten (would downgrade)"
    # No backup written (no overwrite happened).
    assert not (tmp_path / (RELEASE_REL + ".bak")).exists()
    out = capsys.readouterr().out
    assert "skipped force-overwrite of .github/workflows/release.yml" in out
    assert "at/AHEAD of canon (would downgrade)" in out
    assert _PIPELINE_DRIFT_RC in out


def test_force_templates_overwrites_behind_release_yml(tmp_path, capsys):
    """--force-templates OVERWRITES a behind-canon release.yml (back to canon)."""
    _write_plugin_json(tmp_path)
    canon = _canon_release(tmp_path)
    behind = _behind_variant(canon)
    _write_release(tmp_path, behind)
    assert behind != canon  # sanity: the fixture actually drifted behind

    fix_missing_files(tmp_path, results=[], force_templates=True)

    after = (tmp_path / RELEASE_REL).read_text(encoding="utf-8")
    assert after == canon, "a behind-canon release.yml must be force-overwritten back to canon"
    # The pre-overwrite copy is backed up (this WAS an overwrite).
    assert (tmp_path / (RELEASE_REL + ".bak")).exists()
    out = capsys.readouterr().out
    assert "Overwrote" in out
    assert "skipped force-overwrite of .github/workflows/release.yml" not in out


def test_force_templates_overwrites_plain_drift_release_yml(tmp_path):
    """--force-templates OVERWRITES a plain (no-hardening) drifted release.yml."""
    _write_plugin_json(tmp_path)
    canon = _canon_release(tmp_path)
    plain = _plain_variant(canon)
    _write_release(tmp_path, plain)
    assert plain != canon

    fix_missing_files(tmp_path, results=[], force_templates=True)

    after = (tmp_path / RELEASE_REL).read_text(encoding="utf-8")
    assert after == canon, "a plain-drifted release.yml must still be overwritten (today's behavior preserved)"


def test_force_templates_skips_divergent_release_yml_even_when_behind(tmp_path, capsys):
    """A release.yml in intentional_divergence is skipped even when it's behind canon."""
    _write_plugin_json(tmp_path, divergence=[RELEASE_REL])
    canon = _canon_release(tmp_path)
    behind = _behind_variant(canon)  # would normally be OVERWRITTEN…
    _write_release(tmp_path, behind)

    fix_missing_files(tmp_path, results=[], force_templates=True)

    after = (tmp_path / RELEASE_REL).read_text(encoding="utf-8")
    assert after == behind, "a marked-divergent release.yml must be left untouched even when behind canon"
    assert not (tmp_path / (RELEASE_REL + ".bak")).exists()
    out = capsys.readouterr().out
    assert "skipped .github/workflows/release.yml — marked intentional_divergence" in out


def test_force_templates_dry_run_honors_skip(tmp_path, capsys):
    """Even in --dry-run, an ahead-of-canon file reports SKIP, not 'would overwrite'."""
    _write_plugin_json(tmp_path)
    canon = _canon_release(tmp_path)
    ahead = _ahead_variant(canon)
    _write_release(tmp_path, ahead)

    fix_missing_files(tmp_path, results=[], dry_run=True, force_templates=True)

    # File is untouched (dry-run never writes anyway, but the message must be SKIP).
    assert (tmp_path / RELEASE_REL).read_text(encoding="utf-8") == ahead
    out = capsys.readouterr().out
    assert "skipped force-overwrite of .github/workflows/release.yml" in out
    assert "Would overwrite .github/workflows/release.yml" not in out
