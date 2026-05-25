#!/usr/bin/env python3
"""Two-sided regression tests for the PUBLISH/LINT/SETUP audit findings.

10-agent whole-plugin audit (TRDD-021250b5 follow-up),
report 20260525_102243+0200-publish-pipeline-lint-setup.md:

#6  MINOR — notify-marketplace.yml: top-level permissions: {} (PAT-only),
            SHA-pinned peter-evans/repository-dispatch, per-job timeout.
#7  MINOR — cpv_strip_dev validates submodule_path (reserved-dir rejected).
#8  MINOR — _replace_with_submodule uses `git rm --ignore-unmatch` so a
            resume after a crash mid-step is idempotent.
#9  MINOR — _read_deps_from_git_url turns a clone TimeoutExpired into a clean
            RuntimeError instead of a raw traceback.
#10 MINOR — add_skill/add_agent/add_command reject path-traversal --name.
#11 MINOR — the deps git clone uses a `--` end-of-options separator.
#12 MINOR — fclones binary is installed atomically (tmp + os.replace).
#14 MINOR — setup_branch_rules logs gh/JSON failures instead of silently
            returning an empty/None ruleset.
#16 NIT   — covered in test_phase6_sarif_writer.py (SARIF host-path leak).
"""

from __future__ import annotations

import inspect
import io
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

_TEMPLATES = Path(__file__).parent.parent / "templates" / "github-workflows"


class TestNotifyMarketplaceHardening:
    """#6 - the shipped notify-marketplace.yml template is hardened."""

    def _doc(self):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load((_TEMPLATES / "notify-marketplace.yml").read_text(encoding="utf-8"))

    def test_permissions_empty_set(self):
        # PAT-only workflow → least privilege is the empty permission set.
        assert self._doc().get("permissions") == {}

    def test_job_has_timeout(self):
        jobs = self._doc().get("jobs", {})
        assert jobs
        for name, cfg in jobs.items():
            assert "timeout-minutes" in cfg, f"{name} missing timeout-minutes"

    def test_repository_dispatch_sha_pinned(self):
        text = (_TEMPLATES / "notify-marketplace.yml").read_text(encoding="utf-8")
        import re

        assert re.search(r"peter-evans/repository-dispatch@[0-9a-f]{40}", text), (
            "repository-dispatch must be pinned to a 40-char commit SHA"
        )
        assert "peter-evans/repository-dispatch@v4\n" not in text  # bare tag gone


class TestStripDevSubmodulePathValidation:
    """#7 - submodule_path is validated like src (reserved dir rejected)."""

    def _plugin(self, tmp_path: Path, submodule_path: str) -> Path:
        import json

        plugin = tmp_path / "demo"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / "tests").mkdir()
        (plugin / "tests" / "x.py").write_text("x\n", encoding="utf-8")
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "demo",
                    "version": "0.1.0",
                    "description": "x",
                    "repository": "https://github.com/Emasoft/demo",
                    "cpv": {
                        "strip": {
                            "extract": [
                                {
                                    "src": "tests/",
                                    "submodule": "Emasoft/demo-tests",
                                    "submodule_path": submodule_path,
                                }
                            ]
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return plugin

    def test_reserved_submodule_path_rejected(self, tmp_path):
        import cpv_strip_dev as csd

        plugin = self._plugin(tmp_path, "scripts/")  # reserved runtime dir
        with pytest.raises(csd.StripError):
            csd.build_plan(plugin)

    def test_traversal_submodule_path_rejected(self, tmp_path):
        import cpv_strip_dev as csd

        plugin = self._plugin(tmp_path, "../escape/")
        with pytest.raises(csd.StripError):
            csd.build_plan(plugin)

    def test_valid_submodule_path_accepted(self, tmp_path):
        """Two-sided: a normal dev/ mount point (not yet existing) is accepted."""
        import cpv_strip_dev as csd

        plugin = self._plugin(tmp_path, "dev/tests/")
        plan = csd.build_plan(plugin)
        assert plan.targets[0].submodule_path == "dev/tests/"


class TestReplaceWithSubmoduleIdempotent:
    """#8 - the git rm in _replace_with_submodule is resume-idempotent."""

    def test_git_rm_uses_ignore_unmatch(self):
        import cpv_strip_dev as csd

        src = inspect.getsource(csd._replace_with_submodule)
        assert "--ignore-unmatch" in src


class TestReadDepsFromGitUrl:
    """#9 + #11 - clone has `--` separator and clean timeout handling."""

    def test_clone_has_end_of_options_separator(self, monkeypatch):
        import add_dependencies

        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(add_dependencies, "_read_dependencies_from_source", lambda _tmp: [])
        monkeypatch.setattr(subprocess, "run", _fake_run)
        add_dependencies._read_deps_from_git_url("https://example.com/o/r")
        cmd = captured["cmd"]
        assert "--" in cmd
        # `--` must come immediately before the url positional.
        assert cmd.index("--") == cmd.index("https://example.com/o/r") - 1

    def test_timeout_becomes_runtimeerror(self, monkeypatch):
        import add_dependencies

        def _boom(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 60)

        monkeypatch.setattr(subprocess, "run", _boom)
        with pytest.raises(RuntimeError, match="timed out"):
            add_dependencies._read_deps_from_git_url("https://example.com/o/r")


class TestAddComponentNameValidation:
    """#10 - component writers reject path-traversal --name."""

    def _plugin(self, tmp_path: Path) -> Path:
        plugin = tmp_path / "plug"
        plugin.mkdir()
        return plugin

    def test_traversal_name_rejected(self, tmp_path):
        import add_component

        plugin = self._plugin(tmp_path)
        with pytest.raises(SystemExit):
            add_component.add_skill(plugin, "../evil", "desc", force=True)
        with pytest.raises(SystemExit):
            add_component.add_agent(plugin, "../evil", "desc", "", force=True)
        with pytest.raises(SystemExit):
            add_component.add_command(plugin, "../evil", "desc", "", force=True)

    def test_valid_name_accepted(self, tmp_path):
        """Two-sided: a kebab-case name writes the component file."""
        import add_component

        plugin = self._plugin(tmp_path)
        rc = add_component.add_skill(plugin, "my-skill", "desc", force=True)
        assert rc == 0
        assert (plugin / "skills" / "my-skill" / "SKILL.md").is_file()


class TestSkillsMenuRegistration:
    """#15 - backtick-precise idempotency + pipe escaping in the catalog."""

    def _catalog_plugin(self, tmp_path: Path, listed: str) -> Path:
        plugin = tmp_path / "plug"
        menu_dir = plugin / "skills" / "the-skills-menu"
        menu_dir.mkdir(parents=True)
        (menu_dir / "SKILL.md").write_text(
            "# Menu\n\n## Plugin Skills\n\n| # | Domain | Skills |\n"
            "|---|--------|--------|\n"
            f"| 1 | core | `{listed}` — an existing skill |\n",
            encoding="utf-8",
        )
        return plugin

    def test_substring_name_not_falsely_skipped(self, tmp_path):
        import add_component

        plugin = self._catalog_plugin(tmp_path, "fix-validation")
        # "fix" is a SUBSTRING of "fix-validation" but a DIFFERENT skill — must
        # be added, not skipped.
        changed = add_component._register_in_the_skills_menu(plugin, "fix", "the fix skill")
        assert changed is True
        content = (plugin / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
        assert "`fix`" in content

    def test_exact_name_is_idempotent(self, tmp_path):
        """Two-sided: re-registering an already-listed skill is a no-op."""
        import add_component

        plugin = self._catalog_plugin(tmp_path, "fix-validation")
        assert add_component._register_in_the_skills_menu(plugin, "fix-validation", "desc") is False

    def test_pipe_in_description_escaped(self, tmp_path):
        import add_component

        plugin = self._catalog_plugin(tmp_path, "other-skill")
        add_component._register_in_the_skills_menu(plugin, "new-skill", "does a | b | c")
        content = (plugin / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8")
        # The pipe must be escaped so it can't break the table cell.
        assert "does a \\| b \\| c" in content


class TestAtomicInstallBinary:
    """#12 - fclones (and any binary) is installed atomically."""

    def test_install_leaves_no_part_file(self, tmp_path):
        from cpv_install_scanners import _atomic_install_binary

        target = tmp_path / "fclones"
        ok = _atomic_install_binary(io.BytesIO(b"#!/bin/sh\necho hi\n"), target)
        assert ok is True
        assert target.read_bytes() == b"#!/bin/sh\necho hi\n"
        # No leftover .part staging file.
        assert not any(p.name.endswith(".part") for p in tmp_path.iterdir())

    def test_failed_install_leaves_no_target(self, tmp_path):
        """Two-sided: a write into a missing dir fails cleanly (no partial target)."""
        from cpv_install_scanners import _atomic_install_binary

        target = tmp_path / "nonexistent-subdir" / "fclones"
        ok = _atomic_install_binary(io.BytesIO(b"data"), target)
        assert ok is False
        assert not target.exists()


class TestSetupBranchRulesDiagnostics:
    """#14 - gh/JSON failures are logged, not silently swallowed."""

    def test_api_failure_warns(self, monkeypatch, capsys):
        import setup_branch_rules

        def _fail(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "gh: 503 server error")

        monkeypatch.setattr(setup_branch_rules, "run", _fail)
        result = setup_branch_rules._fetch_all_rulesets("o", "r")
        assert result == []
        err = capsys.readouterr().err
        assert "could not list rulesets" in err

    def test_success_no_warn(self, monkeypatch, capsys):
        """Two-sided: a valid ruleset list produces no warning."""
        import setup_branch_rules

        def _ok(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "[]", "")

        monkeypatch.setattr(setup_branch_rules, "run", _ok)
        assert setup_branch_rules._fetch_all_rulesets("o", "r") == []
        assert "could not list rulesets" not in capsys.readouterr().err
