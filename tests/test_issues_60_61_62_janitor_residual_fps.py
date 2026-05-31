#!/usr/bin/env python3
"""Two-sided regression tests for the residual ai-maestro-janitor FPs.

Each discriminator is INTRINSIC (computed from the AST / source, never
self-declared) and tested TWO-SIDED — the benign shape is suppressed AND a
hand-crafted malicious counterpart with the same surface stays VISIBLE.

  * #60 — ``yaml.load(Loader=<SafeLoader subclass>)`` is as safe as
    ``yaml.safe_load``; plain ``yaml.load`` / ``Loader=yaml.Loader`` /
    ``UnsafeLoader`` / an unrelated subclass stay flagged.
  * #61 — ``rm`` / ``unlink`` / ``launchctl bootout|unload`` of a LaunchAgent
    is REMOVAL (the opposite of persistence); install/load (cp / cat > / tee /
    launchctl load) stays flagged.
  * #62 — ``import X`` after a literal ``sys.path.insert(...)`` of a local
    subdir is a local sibling, not a missing PyPI dep; a genuine third-party
    import (no local resolution) stays flagged.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from cpv_skillaudit_native import scan_content  # noqa: E402
from validate_hook import detect_python_third_party_imports  # noqa: E402


def _visible(content: str, file_path: str, rule_id: str) -> int:
    """Number of VISIBLE (not suppressed) findings for ``rule_id``."""
    return sum(
        1
        for f in scan_content(content, file_path)
        if f.get("ruleId") == rule_id and not f.get("suppressed")
    )


class TestIssue60YamlSafeLoaderSubclass:
    def test_safeloader_subclass_suppressed(self) -> None:
        src = "import yaml\nclass _DupLoader(yaml.SafeLoader):\n    pass\ntree = yaml.load(raw, Loader=_DupLoader)\n"
        assert _visible(src, "patterns.py", "DESERIALIZATION") == 0

    def test_direct_safeloader_suppressed(self) -> None:
        assert _visible("import yaml\nd = yaml.load(raw, Loader=yaml.SafeLoader)\n", "x.py", "DESERIALIZATION") == 0

    def test_transitive_subclass_suppressed(self) -> None:
        src = "import yaml\nclass _A(yaml.SafeLoader):\n    pass\nclass _B(_A):\n    pass\nx = yaml.load(raw, Loader=_B)\n"
        assert _visible(src, "x.py", "DESERIALIZATION") == 0

    def test_plain_yaml_load_kept(self) -> None:
        assert _visible("import yaml\nx = yaml.load(raw)\n", "x.py", "DESERIALIZATION") >= 1

    def test_unsafe_loader_kept(self) -> None:
        assert _visible("import yaml\nx = yaml.load(raw, Loader=yaml.Loader)\n", "x.py", "DESERIALIZATION") >= 1
        assert _visible("import yaml\nx = yaml.load(raw, Loader=yaml.UnsafeLoader)\n", "x.py", "DESERIALIZATION") >= 1

    def test_unrelated_subclass_kept(self) -> None:
        src = "import yaml\nclass Evil(object):\n    pass\nx = yaml.load(raw, Loader=Evil)\n"
        assert _visible(src, "x.py", "DESERIALIZATION") >= 1


def _persist_md(body: str) -> int:
    md = "# Skill\n\n```bash\n" + body + "\n```\n"
    return _visible(md, "skills/x/SKILL.md", "PERSISTENCE")


class TestIssue61LaunchAgentRemoval:
    def test_rm_plist_suppressed(self) -> None:
        assert _persist_md('rm -f "$HOME/Library/LaunchAgents/com.emasoft.rotator.plist"') == 0

    def test_unlink_plist_suppressed(self) -> None:
        assert _persist_md("unlink ~/Library/LaunchAgents/com.x.plist") == 0

    def test_launchctl_bootout_suppressed(self) -> None:
        assert _persist_md("launchctl bootout gui/$(id -u)/com.emasoft.rotator") == 0

    def test_launchctl_unload_suppressed(self) -> None:
        assert _persist_md("launchctl unload ~/Library/LaunchAgents/com.x.plist") == 0

    def test_install_cp_kept(self) -> None:
        assert _persist_md("cp com.evil.plist ~/Library/LaunchAgents/") >= 1

    def test_install_heredoc_kept(self) -> None:
        assert _persist_md('cat > "$HOME/Library/LaunchAgents/com.evil.plist" <<EOF') >= 1

    def test_launchctl_load_kept(self) -> None:
        assert _persist_md("launchctl load ~/Library/LaunchAgents/com.evil.plist") >= 1

    def test_remove_then_install_kept(self) -> None:
        # A line that removes an old agent AND installs a new one stays visible.
        assert _persist_md("rm -f old.plist && cp new.plist ~/Library/LaunchAgents/") >= 1


class TestIssue62SysPathInsertSibling:
    def _plugin(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="cpv-62-"))
        (root / "scripts" / "hooks").mkdir(parents=True)
        (root / "scripts" / "oauth_rotator").mkdir(parents=True)
        (root / "scripts" / "oauth_rotator" / "supervisor.py").write_text("x = 1\n")
        return root

    def test_variable_base_sibling_suppressed_and_real_dep_kept(self) -> None:
        root = self._plugin()
        hook = root / "scripts" / "hooks" / "on-session-start.py"
        hook.write_text(
            "import sys\nfrom pathlib import Path\nplugin_root = '/x'\n"
            'sys.path.insert(0, str(Path(plugin_root) / "scripts" / "oauth_rotator"))\n'
            "import supervisor\nimport requests\n"
        )
        res = detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")
        assert "supervisor" not in res  # local sibling via sys.path.insert
        assert "requests" in res  # genuine third-party — still flagged

    def test_relative_string_base_suppressed(self) -> None:
        root = self._plugin()
        hook = root / "scripts" / "hooks" / "h.py"
        hook.write_text('import sys\nsys.path.insert(0, "scripts/oauth_rotator")\nimport supervisor\n')
        assert "supervisor" not in detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")

    def test_no_insert_kept(self) -> None:
        root = self._plugin()
        hook = root / "scripts" / "hooks" / "h.py"
        hook.write_text("import supervisor\n")
        assert "supervisor" in detect_python_third_party_imports(hook, plugin_script_dir=root / "scripts")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
