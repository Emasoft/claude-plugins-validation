"""CPVPPC P1 — marketplace README-table fixture tests (TRDD-DFRPRZYD).

The fixture is a REAL generator scaffold: generate_marketplace_repo.py
(github mode, so the CI/update workflows exist) run into tmp_path. The plan's
acceptance is that the scaffold passes CPVPPC-MKT-001..006 and fails exactly
MKT-007 (the P5-tied notify-contract assertion), and that ONE mutation per
assertion flips ONLY that assertion — no vacuous "file exists" pins.

The never-execute sentinel: a fixture repo whose renderer carries an
os.system payload must fail MKT-002 AND the payload must never run
(side-effect file absent) — verify.py judges the repo renderer by BYTE
comparison, never by executing it.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cpvppc.verify import (  # noqa: E402
    EXIT_NON_COMPLIANT,
    EXIT_NOT_DECLARED,
    run_assertions,
    verdict,
)
from generate_marketplace_repo import generate_marketplace_repo  # noqa: E402

CANON_VERSION = "0.1.0"
NAME = "drift-marketplace"


def _scaffold(tmp_path: Path) -> Path:
    repo = tmp_path / "scaffold"
    rc = generate_marketplace_repo(
        repo, NAME, "Test Owner", "desc", "testowner", [], False
    )
    assert rc == 0
    return repo


def _run(repo: Path) -> tuple[list, object]:
    results = run_assertions(repo, CANON_VERSION)
    return results, verdict(results, CANON_VERSION)


def _ids(results: list) -> dict[str, bool]:
    return {r.id: r.passed for r in results}


def _mutate(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.write_text(text, encoding="utf-8")


# ── the acceptance: real scaffold, per-assertion expectations ────────────


def test_scaffold_passes_001_to_006_and_fails_exactly_007(tmp_path: Path) -> None:
    results, v = _run(_scaffold(tmp_path))
    ids = _ids(results)
    for n in range(1, 7):
        assert ids[f"CPVPPC-MKT-00{n}"], f"MKT-00{n} failed on the real scaffold: {results}"
    assert not ids["CPVPPC-MKT-007"]
    assert v.failures and [f.id for f in v.failures] == ["CPVPPC-MKT-007"]
    assert v.label == "NON-COMPLIANT"
    assert v.exit_code == EXIT_NON_COMPLIANT


def test_no_declaration_gives_not_declared_exit(tmp_path: Path) -> None:
    results, _ = _run(_scaffold(tmp_path))
    v = verdict(results, None)
    assert v.label == "NOT-DECLARED"
    assert v.exit_code == EXIT_NOT_DECLARED


# ── one mutation per assertion; each flips only its own assertion ────────


def test_marker_removal_fails_only_001(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    mutated = readme.replace("<!-- PLUGIN-VERSIONS-START -->\n", "", 1)
    assert mutated != readme
    _mutate(repo, "README.md", mutated)
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-001"]
    # MKT-003 legitimately cascades: the template renderer REFUSES a README
    # without markers, so the table section no longer exists to be current —
    # the documented "cannot run" path, which the verdict layer reports as a
    # failure distinct from a stale table.
    assert not ids["CPVPPC-MKT-003"]
    for n in (2, 4, 5, 6):
        assert ids[f"CPVPPC-MKT-00{n}"], f"MKT-00{n} must be unaffected"


def test_renderer_byte_edit_fails_only_002(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    renderer = (repo / "scripts" / "render_readme_table.py").read_text(encoding="utf-8")
    _mutate(repo, "scripts/render_readme_table.py", renderer + "\n# edited\n")
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-002"]
    # MKT-003 rides the byte-identity gate: an unverified renderer makes table
    # currency unverifiable too — this is the documented cascade, assert it.
    assert not ids["CPVPPC-MKT-003"]
    for n in (1, 4, 5, 6):
        assert ids[f"CPVPPC-MKT-00{n}"]


def test_hand_edited_table_row_fails_only_003(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    marker = "| *(no plugins yet)* | | | |"
    assert marker in readme, "empty-scaffold fixture expected"
    _mutate(repo, "README.md", readme.replace(marker, "| *(no plugins yet)* | x | | |", 1))
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-003"]
    for n in (1, 2, 4, 5, 6):
        assert ids[f"CPVPPC-MKT-00{n}"]


def test_check_workflow_deleted_fails_only_004(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    (repo / ".github" / "workflows" / "validate-readme-table.yml").unlink()
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-004"]
    for n in (1, 2, 3, 5, 6):
        assert ids[f"CPVPPC-MKT-00{n}"]


def test_write_mode_step_removed_fails_only_005(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    wf = repo / ".github" / "workflows" / "update-catalog.yml"
    text = wf.read_text(encoding="utf-8")
    # Remove ONLY the actual invocation line — the guard's echo-warning also
    # mentions the renderer, and a mention is not an invocation (the matcher
    # must not credit it; asserting both here keeps this a real test of that
    # discrimination).
    invocation = "            python scripts/render_readme_table.py\n"
    assert invocation in text
    assert "run 'python scripts/render_readme_table.py' by hand" in text
    _mutate(repo, ".github/workflows/update-catalog.yml", text.replace(invocation, "", 1))
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-005"], "a mention in the echo-warning must not satisfy the write-mode assertion"
    for n in (1, 2, 3, 4, 6):
        assert ids[f"CPVPPC-MKT-00{n}"]


def test_top_level_version_removed_fails_only_006(tmp_path: Path) -> None:
    import json

    repo = _scaffold(tmp_path)
    mj = repo / ".claude-plugin" / "marketplace.json"
    data = json.loads(mj.read_text(encoding="utf-8"))
    assert "version" in data
    del data["version"]
    mj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-006"]
    for n in (1, 2, 3, 4, 5):
        assert ids[f"CPVPPC-MKT-00{n}"]


# ── environment robustness the verify layer promises ─────────────────────


def test_dated_renderer_fails_003(tmp_path: Path) -> None:
    """A renderer that stamps date.today() makes --check compare a value that
    changes at midnight (the v5.18.0 lesson) — byte-identity vs the template
    breaks, and the cascade marks 002+003."""
    repo = _scaffold(tmp_path)
    renderer = (repo / "scripts" / "render_readme_table.py").read_text(encoding="utf-8")
    dated = renderer.replace(
        "import sys",
        "import sys\nfrom datetime import date",
        1,
    )
    assert dated != renderer
    dated += '\n\n# dated\nprint(date.today().isoformat())\n'
    _mutate(repo, "scripts/render_readme_table.py", dated)
    ids = _ids(_run(repo)[0])
    assert not ids["CPVPPC-MKT-002"]
    assert not ids["CPVPPC-MKT-003"]


def test_bare_on_key_workflow_still_checked(tmp_path: Path) -> None:
    """PyYAML 1.1 parses a bare `on:` as True — a workflow written that way
    must still have its steps examined, not silently skipped."""
    repo = _scaffold(tmp_path)
    wf = repo / ".github" / "workflows" / "validate-readme-table.yml"
    text = wf.read_text(encoding="utf-8")
    assert "\non:" in text
    (repo / ".github" / "workflows" / "bare-on.yml").write_text(
        text.replace("\non:", "\nTrue:"), encoding="utf-8"
    )
    wf.unlink()
    ids = _ids(_run(repo)[0])
    assert ids["CPVPPC-MKT-004"], "bare-`on:` (True-key) workflow must still satisfy the --check gate"


def test_crlf_readme_still_checked(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    _mutate(repo, "README.md", readme.replace("\n", "\r\n"))
    ids = _ids(_run(repo)[0])
    for n in range(1, 7):
        assert ids[f"CPVPPC-MKT-00{n}"], f"MKT-00{n} must survive CRLF"


# ── the never-execute sentinel ────────────────────────────────────────────


def test_malicious_renderer_fails_002_and_never_runs(tmp_path: Path) -> None:
    repo = _scaffold(tmp_path)
    marker_file = tmp_path / "payload-ran"
    payload = f"import os; os.system('touch {marker_file}')"
    renderer = (repo / "scripts" / "render_readme_table.py").read_text(encoding="utf-8")
    _mutate(repo, "scripts/render_readme_table.py", renderer + f"\n{payload}\n")
    results, _ = _run(repo)
    ids = _ids(results)
    assert not ids["CPVPPC-MKT-002"]
    assert not marker_file.exists(), "verify must NEVER execute repo code"
    assert not any("payload-ran" in r.detail for r in results)


# ── empty vs populated scaffolds both render (v5.18.0 guard) ─────────────


def test_populated_scaffold_also_meets_001_to_006(tmp_path: Path) -> None:
    repo = tmp_path / "populated"
    rc = generate_marketplace_repo(
        repo, NAME, "Test Owner", "desc", "testowner",
        ["testowner/real-plugin"], False,
    )
    assert rc == 0
    ids = _ids(_run(repo)[0])
    for n in range(1, 7):
        assert ids[f"CPVPPC-MKT-00{n}"]
    assert not ids["CPVPPC-MKT-007"]


# ── the launcher mode itself ─────────────────────────────────────────────


def test_remote_validation_cpvppc_mode_passes_exit_through(tmp_path: Path) -> None:
    """`remote_validation.py cpvppc <repo>` returns verify.py's verdict exit
    code verbatim (0/1/5/6) — the mode must NOT remap into 0-4."""
    import remote_validation as rv

    repo = _scaffold(tmp_path)
    rc = rv.main.__wrapped__() if hasattr(rv.main, "__wrapped__") else None
    # Drive main() the way the CLI does: patch parse targets via sys.argv.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    old_argv = sys.argv
    # --canon is required here: a bare scaffold declares no canon version, so
    # WITHOUT it the correct verdict is NOT-DECLARED (6) — this test pins the
    # exit-through of a real verdict, and pins 6 on the bare invocation too.
    sys.argv = ["cpv-remote-validate", "cpvppc", str(repo), "--canon", CANON_VERSION]
    try:
        with redirect_stdout(buf):
            rc = rv.main()
        assert rc == EXIT_NON_COMPLIANT, f"expected 1 (NON-COMPLIANT), got {rc}"
        sys.argv = ["cpv-remote-validate", "cpvppc", str(repo)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rv.main()
    finally:
        sys.argv = old_argv
    assert rc == 6, f"bare scaffold must be NOT-DECLARED (6), got {rc}"
