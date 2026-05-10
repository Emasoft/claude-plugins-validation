"""Tests for [RC-PEP723-INVOCATION-001] — bare-python invocation of PEP 723
scripts (reported 2026-05-09 by another Claude after the creator agent
generated `python script.py` lines for ruamel.yaml-using scripts).

The validator scans every `scripts/*.py` for a PEP 723 inline-script
metadata block. When that block declares a NON-empty `dependencies = [...]`
(and at least one entry is not a stdlib module), the validator walks
every command / agent / skill / hook / README / .mcp.json / .lsp.json
for `python <script>` / `python3 <script>` invocations of that script
and emits MAJOR. `uv run <script>` and `uv run python <script>` are both
acceptable — uv's environment satisfies the inline metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_plugin  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _make_plugin(tmp_path: Path) -> Path:
    root = tmp_path / "demo-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {"name": "demo-plugin", "version": "1.0.0", "description": "x", "author": {"name": "t", "email": "t@e.com"}}
        )
    )
    (root / "scripts").mkdir()
    return root


def _write_pep723_script(scripts_dir: Path, name: str, dep: str = "ruamel.yaml>=0.18") -> None:
    """Create a script with a PEP 723 inline-metadata block declaring dep."""
    deps_line = f'#   "{dep}",' if dep else ""
    body = f"""#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
{deps_line}
# ]
# ///
\"\"\"Sample script needing inline deps.\"\"\"
import sys
print("hello")
"""
    (scripts_dir / name).write_text(body, encoding="utf-8")


def _findings(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if "RC-PEP723-INVOCATION-001" in r.message]


class TestValidatePep723Invocations:
    """Cover every documented branch of the PEP 723 invocation validator."""

    def test_no_scripts_dir_no_findings(self, tmp_path: Path) -> None:
        """Plugin without `scripts/` → no-op (no findings)."""
        root = tmp_path / "p"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(
                {"name": "p", "version": "1.0.0", "description": "x", "author": {"name": "t", "email": "t@e.com"}}
            )
        )
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_no_pep723_block_no_findings(self, tmp_path: Path) -> None:
        """Script lacks PEP 723 metadata → no scan (false-positive prevention)."""
        root = _make_plugin(tmp_path)
        (root / "scripts" / "plain.py").write_text("import sys\nprint('hi')\n")
        (root / "commands" / "x.md").parent.mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run: `python scripts/plain.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_pep723_with_empty_deps_no_findings(self, tmp_path: Path) -> None:
        """`dependencies = []` → no inline deps, bare python is fine."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "empty_deps.py", dep="")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run: `python scripts/empty_deps.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_pep723_stdlib_only_no_findings(self, tmp_path: Path) -> None:
        """A `dependencies` list that names ONLY stdlib modules → no findings.

        (Defensive — sometimes authors list `os` or `sys` by mistake; we
        don't want a false-positive there since the script will work.)"""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "stdlib_only.py", dep="os")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run: `python scripts/stdlib_only.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_bare_python_invocation_in_command_flagged(self, tmp_path: Path) -> None:
        """The exact bug from the report: a command file invokes
        `python scripts/<pep723>.py` → MAJOR."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "yaml.md").write_text("## Usage\n\n```bash\npython scripts/yaml_tool.py --check\n```\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        msgs = _findings(report)
        assert len(msgs) == 1
        assert "yaml_tool.py" in msgs[0]
        assert "uv run" in msgs[0]
        assert "commands/yaml.md" in msgs[0]

    def test_python3_invocation_also_flagged(self, tmp_path: Path) -> None:
        """`python3 <script>` is the same bug as `python <script>` — both flagged."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run `python3 scripts/yaml_tool.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        msgs = _findings(report)
        assert len(msgs) == 1

    def test_uv_run_invocation_clean(self, tmp_path: Path) -> None:
        """`uv run scripts/<script>.py` → no findings (correct pattern)."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("## Usage\n\nRun: `uv run scripts/yaml_tool.py --check`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_uv_run_python_invocation_clean(self, tmp_path: Path) -> None:
        """`uv run python scripts/<script>.py` is also acceptable — uv's
        managed environment satisfies the PEP 723 inline deps."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run: `uv run python scripts/yaml_tool.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_uv_run_with_explicit_deps_clean(self, tmp_path: Path) -> None:
        """`uv run --with ruamel.yaml python scripts/<script>.py` is the
        explicit-deps form that some plugin authors prefer."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "commands").mkdir(parents=True, exist_ok=True)
        (root / "commands" / "x.md").write_text("Run: `uv run --with ruamel.yaml python scripts/yaml_tool.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        assert _findings(report) == []

    def test_invocation_in_agent_md_flagged(self, tmp_path: Path) -> None:
        """Bug surface includes agents/, not just commands/."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "agents").mkdir(parents=True, exist_ok=True)
        (root / "agents" / "my-agent.md").write_text(
            "---\nname: my-agent\ndescription: x\n---\n\nRun python scripts/yaml_tool.py to fix.\n"
        )
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        msgs = _findings(report)
        assert len(msgs) == 1
        assert "agents/my-agent.md" in msgs[0]

    def test_invocation_in_readme_flagged(self, tmp_path: Path) -> None:
        """Bug surface includes README.md too."""
        root = _make_plugin(tmp_path)
        _write_pep723_script(root / "scripts", "yaml_tool.py")
        (root / "README.md").write_text("## Quickstart\n\n`python scripts/yaml_tool.py`\n")
        report = ValidationReport()
        validate_plugin.validate_pep723_invocations(root, report)
        msgs = _findings(report)
        assert len(msgs) == 1
        assert "README.md" in msgs[0]
