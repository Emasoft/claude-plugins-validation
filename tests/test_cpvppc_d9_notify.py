#!/usr/bin/env python3
"""TRDD-DFRPRZYD phase P1 — D9: the notify payload's plugin name is the
marketplace ENTRY name (the ``name`` field of ``.claude-plugin/plugin.json``),
never the repo name.

Field evidence (row D9, reports/rollout-investigation/20260924_182730+0200-…):
token-reporter and llm-externalizer had to hand-patch their notify workflows
because the canon sent ``github.event.repository.name`` as the payload
``plugin`` value while marketplaces match the dispatch on their ENTRY name —
and the two differ whenever a plugin's repo name is not its plugin name
(web-scenario-tester went silently stale for exactly this reason).

This suite pins the sender-side fix, positively and negatively:

* BEHAVIOURAL — the rendered ``Get plugin info`` run block is EXECUTED with
  bash, not grepped: with a well-formed manifest it writes the plugin.json
  name to ``$GITHUB_OUTPUT``; with a missing, malformed, or nameless manifest
  it exits non-zero (fail loud — no repo-name fallback).
* STATIC — ``github.event.repository.name`` never feeds the payload name
  (it only survives as error context inside ``::error::`` lines); the
  env-binding sanitization pattern (no raw ``${{ github.* }}`` inside
  ``run:`` blocks) is preserved; the template file and the skill reference
  doc carry the same fix.

Anti-vacuity: every extraction helper asserts it actually found the step and
that the extracted script is the plugin.json-reading step, so a future
re-spelling cannot turn these assertions into silent no-ops.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_plugin_repo import PluginParams, gen_notify_marketplace_yml  # noqa: E402

TEMPLATE_PATH = REPO_ROOT / "templates" / "github-workflows" / "notify-marketplace.yml"
GUIDE_PATH = (
    REPO_ROOT / "skills" / "cpv-publish-to-marketplace" / "references" / "publish-pipeline-guide.md"
)
STEP_NAME = "Get plugin info"


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults (mirrors sibling canon tests)."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _step_run_script(yml_text: str, step_name: str = STEP_NAME) -> str:
    """Extract a step's ``run: |`` block-scalar from rendered workflow YAML."""
    lines = yml_text.splitlines()
    step_idx = next((i for i, ln in enumerate(lines) if f"name: {step_name}" in ln), None)
    assert step_idx is not None, f"step '{step_name}' not found in workflow"
    run_idx = next(
        (i for i in range(step_idx, len(lines)) if lines[i].strip() == "run: |"), None
    )
    assert run_idx is not None, f"step '{step_name}' has no block-scalar run:"
    out: list[str] = []
    indent: int | None = None
    for line in lines[run_idx + 1:]:
        if not line.strip():
            out.append("")
            continue
        cur_indent = len(line) - len(line.lstrip())
        if indent is None:
            indent = cur_indent
        if cur_indent < indent:
            break
        out.append(line[indent:])
    script = "\n".join(out).strip("\n")
    # Anti-vacuity: the extracted block must be the plugin.json-reading step,
    # not some other step (or a failed extraction).
    assert "plugin.json" in script, "extracted run block lacks plugin.json — extraction is wrong"
    return script


def _run_get_plugin_info(
    script: str, tmp_path: Path, plugin_json: str | None
) -> tuple[int, dict[str, str]]:
    """Execute the step's run script inside tmp_path; return (exit_code, outputs)."""
    output_file = tmp_path / "github_output.txt"
    output_file.write_text("", encoding="utf-8")
    if plugin_json is not None:
        manifest_dir = tmp_path / ".claude-plugin"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "plugin.json").write_text(plugin_json, encoding="utf-8")
    env = {
        **os.environ,
        "REPO_NAME": "repo-called-differently",
        "REF_SHA": "abc123def456",
        "GITHUB_OUTPUT": str(output_file),
    }
    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    outputs: dict[str, str] = {}
    for line in output_file.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key.strip()] = value.strip()
    return proc.returncode, outputs


def _assert_no_raw_github_in_run_blocks(yml_text: str) -> None:
    """Env-binding sanitization: github.* expressions never appear raw in run:."""
    parsed = yaml.safe_load(yml_text)
    assert "jobs" in parsed, "rendered workflow has no jobs"
    for job_name, job in parsed["jobs"].items():
        for step in job.get("steps", []):
            run_block = step.get("run", "")
            if not run_block:
                continue
            assert "${{ github." not in run_block, (
                f"raw github.* expression inside run: of job '{job_name}' "
                f"step '{step.get('name')}' — must be bound via env: first"
            )


class TestEmittedWorkflowBehavior:
    """gen_notify_marketplace_yml output — the run block is EXECUTED, not grepped."""

    @pytest.fixture(scope="class")
    def run_script(self) -> str:
        return _step_run_script(gen_notify_marketplace_yml(_params()))

    def test_reads_name_from_plugin_json(self, run_script: str, tmp_path: Path) -> None:
        """Positive: the name output is the plugin.json name."""
        rc, outputs = _run_get_plugin_info(
            run_script, tmp_path, json.dumps({"name": "marketplace-entry-name"})
        )
        assert rc == 0, "well-formed manifest must pass"
        assert outputs.get("name") == "marketplace-entry-name"
        assert outputs.get("ref") == "abc123def456"

    def test_name_differs_from_repo_name(self, run_script: str, tmp_path: Path) -> None:
        """The repo name is 'repo-called-differently'; the output must NOT be it."""
        rc, outputs = _run_get_plugin_info(
            run_script, tmp_path, json.dumps({"name": "marketplace-entry-name"})
        )
        assert rc == 0
        assert outputs.get("name") == "marketplace-entry-name"
        assert outputs.get("name") != "repo-called-differently"

    def test_missing_manifest_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: no plugin.json → exit non-zero, no name output, no fallback."""
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, None)
        assert rc != 0, "missing plugin.json must fail the step, never fall back to the repo name"
        assert outputs.get("name") is None

    def test_malformed_manifest_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: malformed JSON → exit non-zero, no name output."""
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, "{not json")
        assert rc != 0
        assert outputs.get("name") is None

    def test_nameless_manifest_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: JSON without a 'name' field → exit non-zero."""
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, json.dumps({"version": "1.0.0"}))
        assert rc != 0
        assert outputs.get("name") is None

    def test_empty_name_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: empty 'name' → exit non-zero (empty payload name = wrong name)."""
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, json.dumps({"name": ""}))
        assert rc != 0
        assert outputs.get("name") is None

    def test_non_string_name_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: non-string 'name' (e.g. 123) → exit non-zero.

        `print(123)` exits 0 and the shell sees "123" — without a type check the
        step passes its own -z guard and notifies the marketplace with a
        stringified number as the entry name (found by the P1 adversarial review).
        """
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, json.dumps({"name": 123}))
        assert rc != 0
        assert outputs.get("name") is None

    def test_whitespace_only_name_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: whitespace-only 'name' → exit non-zero.

        A truthy string of spaces passes the emptiness assert and would notify
        the marketplace with an all-whitespace entry name (review round 2).
        """
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, json.dumps({"name": "   "}))
        assert rc != 0
        assert outputs.get("name") is None

    def test_internal_newline_name_fails_loud(self, run_script: str, tmp_path: Path) -> None:
        """Negative: 'name' containing a newline → exit non-zero.

        printf would write `name=a` then a bare `b` line, and GITHUB_OUTPUT
        multi-line semantics hand the dispatch a TRUNCATED payload name —
        silent, the exact defect class the guard exists for (review round 3).
        """
        rc, outputs = _run_get_plugin_info(run_script, tmp_path, json.dumps({"name": "a\nb"}))
        assert rc != 0
        assert outputs.get("name") is None


class TestEmittedWorkflowStatic:
    """Static pins on the rendered generator output."""

    @pytest.fixture(scope="class")
    def yml(self) -> str:
        return gen_notify_marketplace_yml(_params())

    def test_repo_name_only_used_in_error_context(self, yml: str) -> None:
        """github.event.repository.name survives only as error context ($REPO_NAME in ::error::)."""
        script = _step_run_script(yml)
        repo_lines = [line for line in script.splitlines() if "REPO_NAME" in line]
        assert repo_lines, "REPO_NAME should survive as error context (fail-loud messages)"
        for line in repo_lines:
            assert "::error::" in line, f"REPO_NAME used outside an error line: {line!r}"

    def test_repo_name_never_writes_the_name_output(self, yml: str) -> None:
        """Negative: the old defect line is gone from the run block."""
        script = _step_run_script(yml)
        assert "name=$REPO_NAME" not in script
        assert 'printf \'name=%s\' "$REPO_NAME"' not in script

    def test_payload_still_uses_step_output(self, yml: str) -> None:
        """The client-payload keeps consuming steps.plugin.outputs.name."""
        assert '"plugin": "${{ steps.plugin.outputs.name }}"' in yml

    def test_env_sanitization_preserved(self, yml: str) -> None:
        _assert_no_raw_github_in_run_blocks(yml)


class TestTemplateFile:
    """templates/github-workflows/notify-marketplace.yml carries the same fix."""

    @pytest.fixture(scope="class")
    def template_text(self) -> str:
        text = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "Get plugin info" in text, "template missing the Get plugin info step"
        return text

    def test_reads_name_from_plugin_json(self, template_text: str, tmp_path: Path) -> None:
        """Positive (behavioral): template step writes the plugin.json name."""
        rc, outputs = _run_get_plugin_info(
            _step_run_script(template_text), tmp_path, json.dumps({"name": "template-entry-name"})
        )
        assert rc == 0
        assert outputs.get("name") == "template-entry-name"

    def test_missing_manifest_fails_loud(self, template_text: str, tmp_path: Path) -> None:
        """Negative (behavioral): no plugin.json → exit non-zero, no fallback."""
        rc, outputs = _run_get_plugin_info(_step_run_script(template_text), tmp_path, None)
        assert rc != 0
        assert outputs.get("name") is None

    def test_repo_name_never_writes_the_name_output(self, template_text: str) -> None:
        script = _step_run_script(template_text)
        assert "name=$REPO_NAME" not in script
        assert "${{ github.event.repository.name }}" not in script
        assert "python3 -c" in script, "template step must read the manifest via python3"

    def test_template_github_bindings_stay_env_bound(self, template_text: str) -> None:
        """github.* expressions in the step stay bound via env: (sanitization pattern)."""
        script = _step_run_script(template_text)
        assert "${{ github." not in script, "raw github.* inside run: — bind via env: first"
        step_lines = template_text.splitlines()
        step_idx = next(i for i, ln in enumerate(step_lines) if f"name: {STEP_NAME}" in ln)
        env_idx = next(
            (i for i in range(step_idx, len(step_lines)) if step_lines[i].strip() == "env:"),
            None,
        )
        assert env_idx is not None, "Get plugin info step lost its env: binding block"
        joined_env = "\n".join(step_lines[env_idx:env_idx + 5])
        assert "REPO_NAME: ${{ github.event.repository.name }}" in joined_env
        assert "REF_SHA: ${{ github.sha }}" in joined_env

    def test_template_step_byte_identical_to_emitted(self, template_text: str) -> None:
        """The template's Get-plugin-info run block EQUALS the generator's.

        The template is a reference copy for hand-adoption; nothing at runtime
        copies it into scaffolds (notify-marketplace.yml is not in
        REQUIRED_TEMPLATES, and standardize routes through the generator), so
        only this pin keeps the two aligned. Without it the template can
        silently regress to a stale D9 shape while every generator test stays
        green — the hole the round-4 review found: the negative tests bind the
        generator fixture only, so the P1-era template shape passed every test
        on the books before the round-4 propagation.
        """
        emitted_script = _step_run_script(gen_notify_marketplace_yml(_params()))
        template_script = _step_run_script(template_text)
        assert template_script == emitted_script, (
            "template's Get-plugin-info step diverged from gen_notify_marketplace_yml — "
            "re-render and copy the emitted block, one source of truth"
        )


class TestReferenceDoc:
    """skills/cpv-publish-to-marketplace/references/publish-pipeline-guide.md."""

    @pytest.fixture(scope="class")
    def guide_text(self) -> str:
        text = GUIDE_PATH.read_text(encoding="utf-8")
        assert "Get plugin info" in text, "guide missing the Get plugin info step"
        return text

    def test_doc_documents_plugin_json_name(self, guide_text: str) -> None:
        assert "python3 -c" in guide_text, "doc must show the manifest-reading step"
        assert "no repo-name fallback" in guide_text, "doc must state the no-fallback contract"

    def test_doc_no_longer_shows_repo_name_output(self, guide_text: str) -> None:
        assert 'echo "name=${{ github.event.repository.name }}"' not in guide_text
        assert "name=$REPO_NAME" not in guide_text

    def test_doc_documents_the_entry_name_contract(self, guide_text: str) -> None:
        assert "client_payload.plugin" in guide_text
        assert "ENTRY name" in guide_text or "entry name" in guide_text
