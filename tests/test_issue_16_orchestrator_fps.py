"""Tests for issue #16 false-positive fixes.

Coverage by category:
- A: orchestrator parent-traversal detection (`is_orchestrator_skill`,
     downgrade to MINOR when target is an orchestrator with ≥3 sibling
     consumers, allow_orchestrator_traversal opt-out)
- C: npm-package shape skip (`is_npm_package_shape`)
- D: TOC threshold ≥500 lines (short reference files emit INFO not MINOR)
- E: REMOVED in TRDD-021250b5 — the trigger-phrase description exemption
     (`description_has_trigger_phrases`) is gone; description limits are
     token-based (200 tokens) and non-negotiable
- F: vendored-path skip (`is_vendored_path` covers external/, vendor/,
     third_party/, node_modules/, .gitmodules submodules, cpv.exclude_paths)
- I: numbered-prose Instructions list accepted as valid checklist
     (`has_numbered_prose_steps`)
- B: generic cpv config loader (`load_cpv_config`) round-trips supported keys.
     NOTE: the size-override keys cpv.max_chars / cpv.max_lines /
     cpv.skill_size_severity were removed in TRDD-021250b5 (size limits are
     token-based and non-negotiable)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_validation_common as cvc  # noqa: E402

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_plugin_with_cpv_config(tmp_path: Path, cpv_block: dict) -> Path:
    """Create a plugin root with a custom .claude-plugin/plugin.json cpv block."""
    root = tmp_path / "test-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "test-plugin", "version": "1.0.0", "cpv": cpv_block}),
        encoding="utf-8",
    )
    return root


# ── Category C: npm-package shape skip ──────────────────────────────────────


class TestNpmPackageShape:
    def test_at_scope_name_md_is_npm(self):
        assert cvc.is_npm_package_shape("@google/design.md")

    def test_at_scope_name_no_ext_is_npm(self):
        assert cvc.is_npm_package_shape("@babel/standalone")

    def test_name_at_version_is_npm(self):
        assert cvc.is_npm_package_shape("react@18.3.1")

    def test_at_scope_with_complex_version_is_npm(self):
        assert cvc.is_npm_package_shape("@babel/standalone@7.29.0")

    def test_id_slash_version_is_npm(self):
        assert cvc.is_npm_package_shape("diagram-ir/1.0")

    def test_real_path_is_not_npm(self):
        assert not cvc.is_npm_package_shape("references/foo.md")

    def test_path_with_slash_is_not_npm(self):
        assert not cvc.is_npm_package_shape("scripts/validate.py")

    def test_simple_filename_is_not_npm(self):
        assert not cvc.is_npm_package_shape("README.md")


# ── Category E removed: trigger-phrase description exemption ────────────────
# The `description_has_trigger_phrases` helper and the trigger-phrase length
# exemption were removed in TRDD-021250b5 — description limits are now token-
# based (200 tokens) and non-negotiable, with no per-phrase exemption.


# ── Category F: vendored-path skip ──────────────────────────────────────────


class TestVendoredPathSkip:
    def test_external_dir_skipped(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(tmp_path, {})
        assert cvc.is_vendored_path(Path("external/lib/foo.md"), plugin_root)

    def test_node_modules_skipped(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(tmp_path, {})
        assert cvc.is_vendored_path(Path("node_modules/x/y.md"), plugin_root)

    def test_third_party_skipped(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(tmp_path, {})
        assert cvc.is_vendored_path(Path("third_party/lib.md"), plugin_root)

    def test_normal_path_not_skipped(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(tmp_path, {})
        assert not cvc.is_vendored_path(Path("skills/my-skill/SKILL.md"), plugin_root)

    def test_gitmodule_path_skipped(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(tmp_path, {})
        (plugin_root / ".gitmodules").write_text(
            '[submodule "vendored/lib"]\n  path = vendored/lib\n  url = x\n',
            encoding="utf-8",
        )
        # Clear caches so the new .gitmodules is read
        cvc._read_gitmodules_paths.cache_clear()
        assert cvc.is_vendored_path(Path("vendored/lib/foo.md"), plugin_root)

    def test_cpv_exclude_paths_honored(self, tmp_path):
        plugin_root = _make_plugin_with_cpv_config(
            tmp_path, {"exclude_paths": ["custom-vendor/", "SKILLS-TO-INTEGRATE"]}
        )
        cvc._load_cpv_config_cached.cache_clear()
        assert cvc.is_vendored_path(Path("custom-vendor/x.md"), plugin_root)
        assert cvc.is_vendored_path(Path("SKILLS-TO-INTEGRATE/foo.md"), plugin_root)


# ── Category A: orchestrator skill detection ────────────────────────────────


class TestOrchestratorSkill:
    def _make_skills(self, tmp_path: Path, n_consumers: int, target: str = "orch") -> Path:
        skills_root = tmp_path / "skills"
        skills_root.mkdir(parents=True)
        # Create the orchestrator with shared rules
        orch = skills_root / target
        orch.mkdir()
        (orch / "SKILL.md").write_text("# Orchestrator", encoding="utf-8")
        (orch / "shared-rule-a.md").write_text("rules", encoding="utf-8")
        (orch / "shared-rule-b.md").write_text("rules", encoding="utf-8")
        # Create N consumer sibling skills, each referencing the orchestrator
        for i in range(n_consumers):
            consumer = skills_root / f"consumer-{i}"
            consumer.mkdir()
            (consumer / "SKILL.md").write_text(
                f"# Consumer {i}\nReferences ../{target}/shared-rule-a.md\n",
                encoding="utf-8",
            )
        return skills_root

    def test_orchestrator_with_3_consumers_detected(self, tmp_path):
        skills_root = self._make_skills(tmp_path, n_consumers=3)
        assert cvc.is_orchestrator_skill("orch", skills_root, threshold=3)

    def test_orchestrator_with_2_consumers_below_threshold(self, tmp_path):
        skills_root = self._make_skills(tmp_path, n_consumers=2)
        assert not cvc.is_orchestrator_skill("orch", skills_root, threshold=3)

    def test_orchestrator_with_5_consumers_detected(self, tmp_path):
        skills_root = self._make_skills(tmp_path, n_consumers=5)
        assert cvc.is_orchestrator_skill("orch", skills_root, threshold=3)

    def test_nonexistent_skill_not_orchestrator(self, tmp_path):
        skills_root = self._make_skills(tmp_path, n_consumers=3)
        assert not cvc.is_orchestrator_skill("nope", skills_root, threshold=3)

    def test_missing_skills_root(self, tmp_path):
        assert not cvc.is_orchestrator_skill("orch", tmp_path / "missing", threshold=3)


# ── Category I: numbered-prose checklist accept ─────────────────────────────


class TestNumberedProseSteps:
    def test_three_numbered_steps_detected(self):
        text = "## Instructions\n1. Do X.\n2. Do Y.\n3. Do Z.\n"
        assert cvc.has_numbered_prose_steps(text)

    def test_two_numbered_steps_below_threshold(self):
        text = "1. Do X.\n2. Do Y.\nplus prose.\n"
        assert not cvc.has_numbered_prose_steps(text)

    def test_no_numbered_steps_returns_false(self):
        text = "Just a paragraph with no numbered list.\n"
        assert not cvc.has_numbered_prose_steps(text)

    def test_inline_numbers_ignored(self):
        text = "I have 1. some text and 2. more text without newlines.\n"
        assert not cvc.has_numbered_prose_steps(text)

    def test_indented_numbered_steps_detected(self):
        text = "## Instructions\n  1. Do X.\n  2. Do Y.\n  3. Do Z.\n"
        assert cvc.has_numbered_prose_steps(text)


# ── Category B: cpv config loader ───────────────────────────────────────────


class TestCpvConfigLoader:
    def test_no_manifest_returns_empty(self, tmp_path):
        cvc._load_cpv_config_cached.cache_clear()
        assert cvc.load_cpv_config(tmp_path) == {}

    def test_manifest_without_cpv_returns_empty(self, tmp_path):
        cvc._load_cpv_config_cached.cache_clear()
        plugin_root = tmp_path / "p"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "p", "version": "1.0.0"}),
            encoding="utf-8",
        )
        assert cvc.load_cpv_config(plugin_root) == {}

    def test_cpv_block_returned_verbatim(self, tmp_path):
        # load_cpv_config is a generic parser of the plugin.json `cpv` block —
        # it returns whatever valid keys are present. (The old size-override keys
        # max_chars / max_lines / skill_size_severity were removed in
        # TRDD-021250b5; this test now exercises current, supported cpv keys.)
        cvc._load_cpv_config_cached.cache_clear()
        plugin_root = _make_plugin_with_cpv_config(
            tmp_path,
            {
                "allow_root_dirs": ["design", "templates"],
                "allow_orchestrator_traversal": ["skills/cpv-canonical-pipeline"],
                "allow_pipeline_drift": ["scripts/foo.py"],
            },
        )
        cfg = cvc.load_cpv_config(plugin_root)
        assert cfg["allow_root_dirs"] == ["design", "templates"]
        assert cfg["allow_orchestrator_traversal"] == ["skills/cpv-canonical-pipeline"]
        assert cfg["allow_pipeline_drift"] == ["scripts/foo.py"]

    def test_invalid_json_returns_empty(self, tmp_path):
        cvc._load_cpv_config_cached.cache_clear()
        plugin_root = tmp_path / "p"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            "{not valid json",
            encoding="utf-8",
        )
        assert cvc.load_cpv_config(plugin_root) == {}
