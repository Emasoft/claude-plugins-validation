#!/usr/bin/env python3
"""CPVPPC framework tests (canon 0.1.0, phase P1).

Covers: verdict labels + exit codes (UNKNOWN never 0), every fact type
positive + negative on tmp_path fixtures, spec-equality (committed
design/specs/cpvppc.md == re-rendered output), and the never-execute
sentinel: a repo renderer carrying an os.system payload must FAIL MKT-002
AND the payload must not run.

Network-free; real files on tmp_path; no vacuous pins.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
for p in (SCRIPTS,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cpvppc.verify import (  # type: ignore[import-not-found]  # noqa: E402
    EXIT_COMPLIANT,
    EXIT_NON_COMPLIANT,
    EXIT_NOT_DECLARED,
    EXIT_UNKNOWN,
    load_canon,
    main,
    render_text,
    run_assertions,
    verdict,
)

START = "<!-- PLUGIN-VERSIONS-START -->"
END = "<!-- PLUGIN-VERSIONS-END -->"
TEMPLATE_RENDERER = REPO_ROOT / "templates" / "scripts" / "render_readme_table.py"


# --- fixture builder -----------------------------------------------------------


def _manifest(plugins: list[dict], version: str = "1.0.0") -> str:
    return json.dumps({"name": "test-marketplace", "owner": {"name": "t"}, "version": version, "plugins": plugins})


def _plugin(name: str, version: str = "0.1.0") -> dict:
    return {
        "name": name,
        "version": version,
        "category": "developer-tools",
        "description": f"The {name} plugin",
        "source": {"source": "github", "repo": f"acme/{name}"},
    }


def _rendered_readme(plugins: list[dict]) -> str:
    """Produce the README the template renderer would emit, WITHOUT running it
    on a repo we care about — run the template on a scratch copy."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="cpvppc-test-render-") as tmp:
        root = Path(tmp)
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "marketplace.json").write_text(_manifest(plugins), encoding="utf-8")
        (root / "README.md").write_text(f"# M\n\n{START}\nold\n{END}\n", encoding="utf-8")
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(TEMPLATE_RENDERER), "--root", str(root)],
            cwd=str(root), capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, proc.stderr
        return (root / "README.md").read_text(encoding="utf-8")


def _mk_repo(
    tmp_path: Path,
    *,
    manifest: str | None = None,
    readme: str | None = None,
    renderer: str | None = None,
    copy_template: bool = True,
    workflows: dict[str, str] | None = None,
    publish_canon: str | None = "0.1.0",
) -> Path:
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        manifest if manifest is not None else _manifest([_plugin("alpha"), _plugin("beta")]), encoding="utf-8"
    )
    if manifest is None:
        plugins = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"]
        rendered = _rendered_readme(plugins)
    else:
        # A caller-supplied manifest may be deliberately invalid (a negative
        # fixture); render a two-plugin table without consulting it.
        rendered = _rendered_readme([_plugin("alpha"), _plugin("beta")])
    # An explicit readme override always wins (negative fixtures hand-edit it).
    (root / "README.md").write_text(readme if readme is not None else rendered, encoding="utf-8")
    if copy_template:
        (root / "scripts" / "render_readme_table.py").write_text(
            TEMPLATE_RENDERER.read_text(encoding="utf-8"), encoding="utf-8"
        )
    elif renderer is not None:
        (root / "scripts" / "render_readme_table.py").write_text(renderer, encoding="utf-8")
    if workflows is not None:
        for name, text in workflows.items():
            (root / ".github" / "workflows" / name).write_text(text, encoding="utf-8")
    if publish_canon:
        (root / "scripts" / "publish.py").write_text(f"CANON_VERSION = {publish_canon!r}\n", encoding="utf-8")
    return root


CHECK_WF = """\
name: Validate
on:
  push:
    branches: [main]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check README table
        run: python3 scripts/render_readme_table.py --check
"""

UPDATE_WF = """\
name: Update Submodules
on:
  repository_dispatch:
    types: [plugin-updated]
jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Regenerate README plugin table
        run: python3 scripts/render_readme_table.py
"""

NOTIFY_WF = """\
name: Update Submodules
on:
  repository_dispatch:
    types: [plugin-updated]
jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - name: Resolve notified plugin
        env:
          PAYLOAD_PLUGIN: ${{ github.event.client_payload.plugin }}
        run: |
          jq -e --arg p "$PAYLOAD_PLUGIN" '.plugins[] | select(.name == $p)' .claude-plugin/marketplace.json
      - name: Regenerate README plugin table
        run: python3 scripts/render_readme_table.py
"""


def _result(results: list, aid: str):
    return next(r for r in results if r.id == aid)


# --- canon manifest sanity -----------------------------------------------------


class TestCanonManifest:
    def test_canon_version_is_0_1_0(self):
        canon = load_canon()
        assert canon["canon_version"] == "0.1.0"

    def test_seven_assertions_present(self):
        canon = load_canon()
        ids = [a["id"] for a in canon["assertions"]]
        assert ids == [f"CPVPPC-MKT-{i:03d}" for i in range(1, 8)]

    def test_unknown_fact_type_is_refused(self, tmp_path):
        canon = load_canon()
        canon = dict(canon)
        canon["assertions"] = [dict(canon["assertions"][0])]
        canon["assertions"][0] = dict(canon["assertions"][0])
        canon["assertions"][0]["fact"] = {"type": "teleport_moon", "args": {}}
        import cpvppc.verify as v  # type: ignore[import-not-found]

        with pytest.raises(ValueError, match="not in the canon registry"):
            v._parse_assertions(canon)


# --- verdicts and exit codes ---------------------------------------------------


class TestVerdictsAndExitCodes:
    def test_compliant_repo_is_exit_0(self, tmp_path):
        # Fully canon-complete INCLUDING the P5-style notify resolution step.
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF, "update.yml": UPDATE_WF, "notify.yml": NOTIFY_WF})
        rc = main([str(repo)])
        assert rc == EXIT_COMPLIANT

    def test_non_compliant_repo_is_exit_1(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF, "update.yml": UPDATE_WF})
        (repo / ".claude-plugin" / "marketplace.json").write_text(
            _manifest([_plugin("alpha"), _plugin("beta")], version=None) if False else _manifest([_plugin("alpha")]),
            encoding="utf-8",
        )
        (repo / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [_plugin("alpha")]}), encoding="utf-8"  # drops top-level version
        )
        rc = main([str(repo)])
        assert rc == EXIT_NON_COMPLIANT

    def test_not_declared_exit_6_and_reports_best_passing(self, tmp_path):
        # No CANON_VERSION anywhere; everything else compliant.
        repo = _mk_repo(tmp_path, publish_canon=None, workflows={"check.yml": CHECK_WF, "update.yml": UPDATE_WF})
        rc = main([str(repo)])
        assert rc == EXIT_NOT_DECLARED

    def test_unknown_cannot_run_is_exit_5_never_0(self, tmp_path):
        """UNKNOWN: a check cannot RUN. Force it precisely — the only workflow
        file is unparseable YAML, so MKT-004/005/007 cannot run at all."""
        repo = _mk_repo(tmp_path, workflows={"broken.yml": "jobs: [unclosed\n"})
        rc = main([str(repo)])
        assert rc == EXIT_UNKNOWN
        assert rc != 0

    def test_verdict_object_shape(self, tmp_path):
        from cpvppc.verify import AssertionResult  # type: ignore[import-not-found]

        results = [AssertionResult(id="X", passed=False, detail="cannot run: no yaml module")]
        v = verdict(results, "0.1.0")
        assert v.label == "UNKNOWN"
        assert v.exit_code == EXIT_UNKNOWN
        v2 = verdict([], None)
        assert v2.label == "NOT-DECLARED" and v2.exit_code == EXIT_NOT_DECLARED

    def test_render_text_mentions_label(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF, "update.yml": UPDATE_WF})
        results = run_assertions(repo, "0.1.0")
        v = verdict(results, "0.1.0")
        text = render_text(v)
        assert "COMPLIANT" in text


# --- fact types: positive + negative -------------------------------------------


class TestFilePresent:
    def test_markers_present_passes(self, tmp_path):
        repo = _mk_repo(tmp_path)
        results = run_assertions(repo, "0.1.0")
        assert _result(results, "CPVPPC-MKT-001").passed

    def test_marker_missing_fails(self, tmp_path):
        repo = _mk_repo(tmp_path, readme="# no markers here\n")
        results = run_assertions(repo, "0.1.0")
        r = _result(results, "CPVPPC-MKT-001")
        assert not r.passed
        assert "PLUGIN-VERSIONS-START" in r.detail

    def test_markers_out_of_order_fail(self, tmp_path):
        repo = _mk_repo(tmp_path, readme=f"# m\n{END}\nmid\n{START}\n")
        results = run_assertions(repo, "0.1.0")
        r = _result(results, "CPVPPC-MKT-001")
        assert not r.passed
        assert "out of order" in r.detail


class TestFileMatchesRender:
    def test_byte_identical_passes(self, tmp_path):
        repo = _mk_repo(tmp_path)
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-002").passed

    def test_any_mutation_fails(self, tmp_path):
        repo = _mk_repo(tmp_path)
        text = (repo / "scripts" / "render_readme_table.py").read_text(encoding="utf-8")
        (repo / "scripts" / "render_readme_table.py").write_text(text + "\n# tampered\n", encoding="utf-8")
        assert not _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-002").passed

    def test_crlf_copy_still_matches(self, tmp_path):
        repo = _mk_repo(tmp_path)
        text = (repo / "scripts" / "render_readme_table.py").read_text(encoding="utf-8")
        (repo / "scripts" / "render_readme_table.py").write_text(text.replace("\n", "\r\n"), encoding="utf-8")
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-002").passed


class TestReadmeSectionCurrent:
    def test_current_table_passes(self, tmp_path):
        repo = _mk_repo(tmp_path)
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-003").passed

    def test_hand_edited_table_fails(self, tmp_path):
        plugins = [_plugin("alpha"), _plugin("beta")]
        readme = _rendered_readme(plugins).replace("0.1.0", "9.9.9", 1)
        repo = _mk_repo(tmp_path, readme=readme)
        r = _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-003")
        assert not r.passed
        assert "stale" in r.detail

    def test_fails_when_renderer_differs(self, tmp_path):
        repo = _mk_repo(tmp_path, copy_template=False, renderer="print('evil')\n")
        r = _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-003")
        assert not r.passed
        assert "MKT-002" in r.detail or "renderer" in r.detail


class TestWorkflowStep:
    def test_check_gate_passes(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF})
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-004").passed

    def test_check_gate_missing_fails(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"update.yml": UPDATE_WF})
        assert not _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-004").passed

    def test_pyyaml_on_key_quirk_handled(self, tmp_path):
        # `on:` parsed by yaml.safe_load becomes key True; the matcher must still find steps.
        wf = CHECK_WF.replace("on:", "on:")
        assert "on:" in wf
        repo = _mk_repo(tmp_path, workflows={"check.yml": wf})
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-004").passed

    def test_unparseable_yaml_reports_cannot_run(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"broken.yml": "jobs: [unclosed\n"})
        r = _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-004")
        assert not r.passed
        assert r.detail.startswith("cannot run") or "cannot" in r.detail or "parse" in r.detail

    def test_write_mode_step_passes(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"update.yml": UPDATE_WF})
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-005").passed

    def test_write_mode_missing_fails(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF})
        assert not _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-005").passed

    def test_check_only_workflow_is_not_write_mode(self, tmp_path):
        # A --check run must not satisfy MKT-005 (negative control on the not_matches clause).
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF})
        r = _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-005")
        assert not r.passed

    def test_compound_echo_and_invocation_is_credited(self, tmp_path):
        # P1 review finding (a): a compound `echo "…" && <real invocation>` line
        # must satisfy MKT-005 — dropping the line whole read a compliant repo
        # as NON-COMPLIANT. Pin the fixed behavior so it cannot regress.
        wf = UPDATE_WF.replace(
            "run: python3 scripts/render_readme_table.py",
            'run: echo "checking" && python3 scripts/render_readme_table.py',
        )
        assert wf != UPDATE_WF, "anchor line missing from UPDATE_WF"
        repo = _mk_repo(tmp_path, workflows={"update.yml": wf})
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-005").passed

    def test_heredoc_body_mention_does_not_count(self, tmp_path):
        # P1 review finding (a), other direction: a documentation heredoc that
        # NAMES the renderer is not an invocation — MKT-005 must stay failed.
        wf = UPDATE_WF.replace(
            "run: python3 scripts/render_readme_table.py",
            "run: |\n          cat <<EOF\n          python3 scripts/render_readme_table.py\n          EOF",
        )
        assert wf != UPDATE_WF, "anchor line missing from UPDATE_WF"
        repo = _mk_repo(tmp_path, workflows={"update.yml": wf})
        assert not _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-005").passed


class TestJsonPathEquals:
    def test_top_level_version_passes(self, tmp_path):
        repo = _mk_repo(tmp_path)
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-006").passed

    def test_version_in_metadata_only_fails(self, tmp_path):
        repo = _mk_repo(tmp_path, manifest=json.dumps({"metadata": {"version": "1.0.0"}, "plugins": []}))
        r = _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-006")
        assert not r.passed

    def test_invalid_json_fails(self, tmp_path):
        repo = _mk_repo(tmp_path, manifest="{oops")
        assert not _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-006").passed


class TestNotifyContractMkt007:
    def test_scaffold_without_resolution_fails_mkt007(self, tmp_path):
        # Today's scaffold: update workflow has no manifest resolution step.
        repo = _mk_repo(tmp_path, workflows={"update.yml": UPDATE_WF})
        results = run_assertions(repo, "0.1.0")
        assert not _result(results, "CPVPPC-MKT-007").passed

    def test_resolution_step_satisfies_mkt007(self, tmp_path):
        repo = _mk_repo(tmp_path, workflows={"notify.yml": NOTIFY_WF})
        assert _result(run_assertions(repo, "0.1.0"), "CPVPPC-MKT-007").passed

    def test_scaffold_fails_exactly_mkt007(self, tmp_path):
        """The canonical P1 gate: a canon-complete scaffold (renderer copy, current
        table, check gate, write step, top-level version) fails ONLY MKT-007,
        which is tied to P5."""
        repo = _mk_repo(tmp_path, workflows={"check.yml": CHECK_WF, "update.yml": UPDATE_WF})
        results = run_assertions(repo, "0.1.0")
        failing = [r.id for r in results if not r.passed]
        assert failing == ["CPVPPC-MKT-007"]


# --- never-execute sentinel -----------------------------------------------------


class TestNeverExecuteSentinel:
    def test_evil_renderer_fails_mkt002_and_payload_does_not_run(self, tmp_path):
        """The repo renderer carries an os.system payload that would create a file.
        MKT-002 must fail (not byte-identical) and the payload must NOT run —
        verify.py executes only the CPV-template renderer, never the repo's copy."""
        side_effect = tmp_path / "payload-ran.txt"
        marker = str(side_effect)
        evil = (
            "import os\n"
            f"os.system({marker!r} > /dev/null 2>&1 || touch {marker})\n"  # noqa: S604
            "print('hello')\n"
        )
        repo = _mk_repo(tmp_path, copy_template=False, renderer=evil, workflows={"check.yml": CHECK_WF})
        assert not side_effect.exists(), "precondition: payload has not run yet"
        results = run_assertions(repo, "0.1.0")
        r002 = _result(results, "CPVPPC-MKT-002")
        assert not r002.passed, "MKT-002 must fail on a non-identical renderer"
        assert not side_effect.exists(), "the repo renderer's payload must never execute"

    def test_evil_readme_table_via_mkt003_uses_template_only(self, tmp_path):
        """Even when the repo renderer is identical, MKT-003 runs the TEMPLATE from
        the CPV tree, not the repo's copy (the identical copy would be safe, but
        the executed path must be the template)."""
        evil = TEMPLATE_RENDERER.read_text(encoding="utf-8") + "\n# comment\n"
        repo = _mk_repo(tmp_path, copy_template=False, renderer=evil)
        results = run_assertions(repo, "0.1.0")
        assert not _result(results, "CPVPPC-MKT-003").passed


# --- spec equality (deterministic render) ---------------------------------------


class TestSpecEquality:
    def test_committed_spec_equals_rendered(self):
        from cpvppc.render_spec import _spec_path, render_spec  # type: ignore[import-not-found]

        canon = load_canon()
        rendered = render_spec(canon)
        committed = _spec_path().read_text(encoding="utf-8")
        assert committed == rendered, "design/specs/cpvppc.md drifted from canon.json — re-run render_spec.py"

    def test_render_is_stable_across_calls(self):
        from cpvppc.render_spec import render_spec  # type: ignore[import-not-found]

        canon = load_canon()
        assert render_spec(canon) == render_spec(canon)

    def test_render_contains_no_dates(self):
        from cpvppc.render_spec import render_spec  # type: ignore[import-not-found]

        out = render_spec(load_canon())
        for banned in ("2026-", "2027-", "T00:", "timestamp"):
            assert banned not in out
