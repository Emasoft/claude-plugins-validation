#!/usr/bin/env python3
"""Issue #192 — `--gate` advertised a no-push check mode, then refused to run.

THE DEFECT. The emitted publish.py's `--gate` help promised "lint + validate +
tests only (no bump/push)", but `run_gate` opened with an unconditional G0
ancestry check, so a standalone invocation died in 0.0s with "Direct push not
allowed" — an error about an action the user never took. The gates were
uninvokable except from inside the pre-push hook.

THE FIX'S INVARIANT, which these tests defend from BOTH sides: G0 and G1's
version-bump block protect a PUSH. They must be enforced exactly when a push is
in flight (git-push ancestry — including the fail-closed unknown case), and must
NOT fire when there is provably no push to protect. Weakening either direction
is a bug: enforce-always re-breaks the advertised mode; enforce-never lets a
direct `git push` bypass the release pipeline.

The behavioural tests import the RENDERED canon — the bytes plugins actually
receive — and flip only the process ancestry between cases, so what is measured
is the ancestry discrimination and nothing else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402


def _render() -> str:
    p = PluginParams(
        name="probe-plugin",
        description="probe",
        author="probe",
        author_email="probe@example.com",
        license="MIT",
        python_version="3.12",
        github_owner="probe-owner",
        marketplace="probe-marketplace",
    )
    return gen_publish_py(p)


@pytest.fixture(scope="module")
def emitted(tmp_path_factory):
    """The rendered canon, imported as a module from a scratch dir."""
    root = tmp_path_factory.mktemp("emitted-publish")
    path = root / "publish.py"
    path.write_text(_render(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("emitted_publish_192", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── ancestry discrimination (the core of the fix) ────────────────────────────


def test_standalone_ancestry_is_not_a_push(emitted, monkeypatch):
    monkeypatch.setattr(
        emitted,
        "_get_process_ancestry",
        lambda: [(10, "python scripts/publish.py --gate"), (5, "-zsh"), (1, "/sbin/launchd")],
    )
    assert emitted._push_in_flight() is False


def test_git_push_ancestor_is_a_push(emitted, monkeypatch):
    monkeypatch.setattr(
        emitted,
        "_get_process_ancestry",
        lambda: [
            (12, "python scripts/publish.py --gate"),
            (11, "sh .git/hooks/pre-push origin git@github.com:o/r.git"),
            (10, "git push --atomic origin HEAD v1.2.3"),
            (9, "python scripts/publish.py --patch"),
        ],
    )
    assert emitted._push_in_flight() is True


def test_hook_script_path_alone_counts_as_push_context(emitted, monkeypatch):
    """The hook's own path is push context even if the `git` line is missing.

    ps can truncate or miss a link in the chain; the hook script only ever runs
    during a push, so matching it keeps the guard closed under partial ancestry.
    """
    monkeypatch.setattr(
        emitted,
        "_get_process_ancestry",
        lambda: [(12, "python scripts/publish.py --gate"), (11, "sh .git/hooks/pre-push origin url")],
    )
    assert emitted._push_in_flight() is True


def test_unknown_ancestry_is_none_not_false(emitted, monkeypatch):
    """ps failure must be distinguishable from "provably no push".

    Collapsing None into False would turn a broken `ps` into a bypass: the
    pre-push hook's gate would skip G0 and wave the push through.
    """
    monkeypatch.setattr(emitted, "_get_process_ancestry", lambda: [])
    assert emitted._push_in_flight() is None


# ── run_gate behaviour: same bare repo, only the ancestry flips ──────────────


def test_standalone_gate_passes_g0_and_runs_the_checks(emitted, monkeypatch, tmp_path, capsys):
    """The advertised mode must actually run.

    On a bare directory the run proceeds past G0/G1 and fails at G2 (no
    scripts/ to lint) — proving the gates RAN. Before the fix this exact call
    returned 1 at G0 without running anything.
    """
    monkeypatch.setattr(
        emitted, "_get_process_ancestry", lambda: [(10, "python scripts/publish.py --gate")]
    )
    rc = emitted.run_gate(tmp_path)
    out = capsys.readouterr().out
    assert "No push in flight" in out
    assert "Direct push not allowed" not in out
    assert "cannot lint" in out  # reached G2 — the checks genuinely ran
    assert rc == 1  # bare dir fails G2; what matters is WHERE it failed


def test_hook_context_without_orchestrator_is_still_blocked(emitted, monkeypatch, tmp_path, capsys):
    """CONTROL — the other side. A direct `git push` must still be blocked.

    Without this, the standalone fix could have been "achieved" by deleting G0.
    """
    monkeypatch.setattr(
        emitted,
        "_get_process_ancestry",
        lambda: [
            (12, "python scripts/publish.py --gate"),
            (10, "git push origin master"),
            (5, "-zsh"),
        ],
    )
    rc = emitted.run_gate(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Direct push not allowed" in out
    assert "cannot lint" not in out  # blocked AT G0; nothing else ran


def test_unknown_ancestry_fails_closed(emitted, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(emitted, "_get_process_ancestry", lambda: [])
    rc = emitted.run_gate(tmp_path)
    out = capsys.readouterr().out
    assert rc == 1
    assert "Direct push not allowed" in out
    assert "failing closed" in out


def test_orchestrated_push_is_allowed_through_g0(emitted, monkeypatch, tmp_path, capsys):
    """POSITIVE CONTROL — the legitimate publish chain still passes G0."""
    monkeypatch.setattr(
        emitted,
        "_get_process_ancestry",
        lambda: [
            (12, "python scripts/publish.py --gate"),
            (11, "sh .git/hooks/pre-push origin url"),
            (10, "git push --atomic origin HEAD v1.2.3"),
            (9, "python scripts/publish.py --patch"),
        ],
    )
    rc = emitted.run_gate(tmp_path)
    out = capsys.readouterr().out
    assert "Orchestrated by publish.py." in out
    assert "Direct push not allowed" not in out
    assert rc == 1  # bare dir still fails G2 later; G0 is what this test pins


# ── structural pins on the emitted bytes ─────────────────────────────────────


def test_emitted_canon_carries_the_fix_and_the_block():
    src = _render()
    # Both halves must ship together: the standalone path AND the block.
    assert "_push_in_flight" in src
    assert "No push in flight" in src
    assert "Direct push not allowed" in src
    assert "fine for a standalone check" in src  # G1's standalone note
    # G0 still precedes G1 (pinned also by test_generate_plugin_repo).
    gate_fn = src.split("def run_gate")[1]
    assert gate_fn.index("[G0]") < gate_fn.index("[G1]")


def test_help_text_no_longer_promises_what_g0_refused():
    """The --help a user reads must describe the real contract."""
    src = _render()
    assert "Runs STANDALONE" in src
    # Wrapped across help-text lines, so assert a fragment that cannot wrap.
    assert "apply while one is in flight" in src
