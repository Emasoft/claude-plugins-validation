"""Regression guards for the full-audit batch-12 fixes.

This batch corrected stale documentation in four files so they match the
actual behaviour of `validate_marketplace.py` and the actual set of CPV
commands/agents:

  * skills/marketplace-authoring-contract/references/preflight-recipe.md
  * skills/marketplace-authoring-contract/references/source-shape.md
  * skills/marketplace-authoring-contract/SKILL.md
  * agents/cache-optimizer-agent.md

The fixes were:

  1. Removed the nonexistent ``--cross-validate-upstream`` argparse flag
     from the preflight recipe and the parent SKILL (the validator rejects
     it with exit 2; cross-validation already runs unconditionally).
  2. Replaced the deprecated ``CPV_SKIP_GITHUB_INTEGRITY`` env var with the
     canonical ``PLUGIN_SKIP_GITHUB_INTEGRITY``.
  3. Corrected every source-shape example from the FLAT form
     (``{"source": "github", "repo": "..."}``) to the NESTED form
     (``{"source": {"source": "github", "repo": "..."}}``) that the
     validator actually accepts, and fixed the bogus ``relative-path`` DICT
     type to the real ``directory`` dict / string-shorthand local forms.
  4. Repointed cache-optimizer-agent.md off the deleted ``cache-optimizer-menu``
     agent and the deleted ``/cpv-cache-optimize`` command onto the real
     dispatchers (cpv-main-menu + the two batch commands), and replaced the
     Phase-4 example's ``AskUserQuestion`` (which contradicted the hard
     NEVER rule) with the numbered Unicode-table approach.

The in-process tests below pin the VALIDATOR CONTRACT that the docs now
describe, so the docs cannot silently drift back. The doc-content tests pin
that the stale references are gone.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SKILL_DIR = REPO_ROOT / "skills" / "marketplace-authoring-contract"
PREFLIGHT = SKILL_DIR / "references" / "preflight-recipe.md"
SOURCE_SHAPE = SKILL_DIR / "references" / "source-shape.md"
SKILL_MD = SKILL_DIR / "SKILL.md"
CACHE_AGENT = REPO_ROOT / "agents" / "cache-optimizer-agent.md"

# CPV_SCAN_CACHE=0 so probes never hit the version-keyed scan cache.
_ENV = dict(os.environ, CPV_SCAN_CACHE="0", PLUGIN_SKIP_GITHUB_INTEGRITY="1")


# ---------------------------------------------------------------------------
# 1. The --cross-validate-upstream flag genuinely does not exist (HIGH fix).
# ---------------------------------------------------------------------------


class TestCrossValidateUpstreamFlag:
    """validate_marketplace.py has no --cross-validate-upstream flag."""

    def test_flag_is_rejected_by_argparse(self):
        """Passing --cross-validate-upstream exits 2 with 'unrecognized arguments'."""
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(
                [
                    "uv",
                    "run",
                    "python",
                    str(SCRIPTS / "validate_marketplace.py"),
                    d,
                    "--strict",
                    "--cross-validate-upstream",
                ],
                env=_ENV,
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
        assert r.returncode == 2, f"expected argparse exit 2, got {r.returncode}"
        assert "unrecognized arguments: --cross-validate-upstream" in (r.stdout + r.stderr)

    def test_cross_validation_runs_unconditionally(self):
        """The upstream cross-validator is wired in with no flag gate."""
        # The docs now say cross-validation runs unconditionally — assert the
        # call site exists and is NOT guarded behind an args.* condition.
        src = (SCRIPTS / "validate_marketplace.py").read_text()
        assert "_cross_validate_upstream_for_entries(plugins, marketplace_dir, json_path)" in src
        # The function is called once, directly inside validate logic — there
        # is no `if args.cross_validate_upstream` gate (which would have been
        # the only way a flag could toggle it).
        assert "args.cross_validate_upstream" not in src


# ---------------------------------------------------------------------------
# 2. Source shape: NESTED form is required; FLAT siblings are rejected.
# ---------------------------------------------------------------------------


class TestSourceShapeContract:
    """The validator requires source to be a nested object, not flat siblings."""

    def _validate_entry(self, entry: dict):
        from validate_marketplace import _validate_known_entry_fields, validate_plugin_source

        marketplace_dir = Path("/tmp")
        results = []
        results += _validate_known_entry_fields(entry, entry.get("name", "x"), "marketplace.json")
        results += validate_plugin_source(entry, entry.get("name", "x"), marketplace_dir, "marketplace.json")
        return results

    def _has_unknown_field(self, results, field: str) -> bool:
        return any("RC-MKPL-UNKNOWN-FIELD" in r.message and f"'{field}'" in r.message for r in results)

    def test_repo_url_package_are_not_top_level_entry_fields(self):
        """repo/url/package/ref must live inside source, never as siblings."""
        from validate_marketplace import _KNOWN_MARKETPLACE_ENTRY_FIELDS as known

        for field in ("repo", "url", "package", "ref"):
            assert field not in known, f"{field} should NOT be a top-level entry field"
        # source/name/version DO belong at the top level.
        for field in ("source", "name", "version"):
            assert field in known, f"{field} should be a top-level entry field"

    def test_flat_github_form_is_rejected(self):
        """FLAT {'source':'github','repo':...} flags 'repo' as unknown top-level field."""
        flat = {"name": "foo-plugin", "source": "github", "repo": "owner/foo-plugin"}
        results = self._validate_entry(flat)
        assert self._has_unknown_field(results, "repo"), (
            "flat-form sibling 'repo' must be flagged RC-MKPL-UNKNOWN-FIELD"
        )

    def test_nested_github_form_is_clean(self):
        """NESTED {'source':{'source':'github','repo':...}} raises no unknown-field finding."""
        nested = {"name": "foo-plugin", "source": {"source": "github", "repo": "owner/foo-plugin"}}
        results = self._validate_entry(nested)
        assert not self._has_unknown_field(results, "repo")
        # And the source type is recognised (no "invalid source type" finding).
        assert not any("invalid source type" in r.message for r in results)

    def test_relative_path_dict_type_is_invalid_but_directory_is_valid(self):
        """'relative-path' is NOT a dict source type; 'directory' is."""
        from validate_marketplace import validate_plugin_source

        bad = {"name": "foo", "version": "1.0.0", "source": {"source": "relative-path", "path": "./p"}}
        bad_results = validate_plugin_source(bad, "foo", Path("/tmp"), "marketplace.json")
        assert any("invalid source type" in r.message for r in bad_results), (
            "nested relative-path dict must be rejected as invalid source type"
        )

        good = {"name": "foo", "version": "1.0.0", "source": {"source": "directory", "path": "./p"}}
        good_results = validate_plugin_source(good, "foo", Path("/tmp"), "marketplace.json")
        assert not any("invalid source type" in r.message for r in good_results), (
            "nested directory dict is the valid local-source dict form"
        )


# ---------------------------------------------------------------------------
# 3. Doc-content guards: stale references are gone from the fixed files.
# ---------------------------------------------------------------------------


class TestDocReferencesAreCorrect:
    """The four fixed docs no longer carry the stale references the audit found."""

    @pytest.mark.parametrize("path", [PREFLIGHT, SOURCE_SHAPE, SKILL_MD])
    def test_no_nonexistent_cross_validate_flag(self, path: Path):
        """No fixed marketplace doc still invokes the nonexistent flag."""
        assert "--cross-validate-upstream" not in path.read_text()

    @pytest.mark.parametrize("path", [PREFLIGHT, SOURCE_SHAPE, SKILL_MD])
    def test_no_deprecated_integrity_env_var(self, path: Path):
        """No fixed marketplace doc still uses the deprecated CPV_SKIP_GITHUB_INTEGRITY."""
        assert "CPV_SKIP_GITHUB_INTEGRITY" not in path.read_text()

    def test_canonical_integrity_env_var_is_present(self):
        """The preflight recipe uses the canonical PLUGIN_SKIP_GITHUB_INTEGRITY."""
        assert "PLUGIN_SKIP_GITHUB_INTEGRITY" in PREFLIGHT.read_text()

    def test_source_shape_uses_nested_form(self):
        """source-shape.md teaches the nested object form, not the flat sibling form."""
        text = SOURCE_SHAPE.read_text()
        # Canonical nested github example is present.
        assert '{"source": "github", "repo": "owner/foo-plugin"}}' in text or (
            '"source": {' in text and '"source": "github"' in text
        )
        # The flat sibling form must not appear as an endorsed (Right) example.
        # It may appear only in the explicit "NOT the flat form" warning.
        assert "NOT the flat form" in text

    def test_skill_example_is_nested(self):
        """SKILL.md's worked example uses the nested source object."""
        text = SKILL_MD.read_text()
        assert '"source":{"source":"github","repo":"owner/foo-plugin"}' in text


class TestCacheOptimizerAgentReferences:
    """cache-optimizer-agent.md points at the real dispatchers, not deleted ones."""

    def test_no_ghost_cpv_cache_optimize_command(self):
        """The deleted /cpv-cache-optimize command is not referenced."""
        text = CACHE_AGENT.read_text()
        # The command file genuinely does not exist.
        assert not (REPO_ROOT / "commands" / "cpv-cache-optimize.md").exists()
        # And the agent must not point users at it.
        assert "/cpv-cache-optimize" not in text

    def test_real_dispatchers_are_referenced(self):
        """The agent names the real dispatchers (cpv-main-menu + batch commands)."""
        text = CACHE_AGENT.read_text()
        assert "cpv-main-menu" in text
        assert "/cpv-batch-caching-audit" in text
        assert "/cpv-batch-caching-optimize" in text
        # The real command files exist.
        assert (REPO_ROOT / "commands" / "cpv-batch-caching-audit.md").exists()
        assert (REPO_ROOT / "commands" / "cpv-batch-caching-optimize.md").exists()

    def test_deleted_menu_agent_not_claimed_as_live_dispatcher(self):
        """cache-optimizer-menu (deleted) is only mentioned as removed history."""
        text = CACHE_AGENT.read_text()
        # The deleted agent file does not exist.
        assert not (REPO_ROOT / "agents" / "cache-optimizer-menu.md").exists()
        # Any surviving mention must be the historical "was removed" note, never
        # a present-tense "dispatched by cache-optimizer-menu" claim.
        for line in text.splitlines():
            if "cache-optimizer-menu" in line:
                assert "former" in line or "removed" in line, f"stale live-dispatcher claim still present: {line!r}"

    def test_phase4_example_does_not_use_askuserquestion(self):
        """The Phase-4 example honors the NEVER-AskUserQuestion rule."""
        text = CACHE_AGENT.read_text()
        # Find the example block lines mentioning Phase 4 + a UI prompt.
        for line in text.splitlines():
            if line.strip().startswith("[Phase 4:") and "AskUserQuestion" in line:
                # Allowed ONLY if it is the negative form ("NEVER AskUserQuestion").
                assert "NEVER AskUserQuestion" in line, f"Phase-4 example contradicts the NEVER rule: {line!r}"
