"""Canon: the generated publish.py ships the Linux fork-parity gate (G4b).

Every plugin CPV scaffolds inherits its publish pipeline from
``gen_publish_py``. CPV shipped a fork-from-multithreaded-process deadlock in
v3.23.0 that no local gate could see (macOS defaults to ``spawn``, Linux to
``fork``), so the fix belongs in what CPV EMITS, not only in CPV's own tree —
the tool's own repo is the least important instance of a defect it hands out.

These tests pin the gate's CONTRACT rather than its wording, because the two
things that make it safe to add unconditionally are exactly the two a future
edit is most likely to erode: it must SELF-DETECT applicability, and it must
never false-block.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402


def _params(**overrides: object) -> PluginParams:
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _src() -> str:
    return gen_publish_py(_params())


# ---------------------------------------------------------------------------
# The gate exists, and the file it lives in is still valid Python
# ---------------------------------------------------------------------------


def test_emitted_publish_py_parses() -> None:
    """The template is a STRING, so only generating it proves it is valid. A
    syntax error here would break the pipeline of every plugin CPV scaffolds."""
    ast.parse(_src())


def test_gate_is_present() -> None:
    assert "[G4b] Linux fork-parity probe" in _src()


def test_gate_is_documented_in_the_gate_list() -> None:
    """A gate that runs but is undocumented is a surprise at publish time."""
    assert "G4b. Linux fork-parity probe" in _src()


def test_gate_runs_after_the_test_gate() -> None:
    """G4b re-runs the suite, so it must come after G4 — and both must be before
    the push, which the surrounding pipeline already guarantees."""
    src = _src()
    assert src.index("[G4] Running tests") < src.index("[G4b] Linux fork-parity probe")


# ---------------------------------------------------------------------------
# The two properties that make it safe to ship unconditionally
# ---------------------------------------------------------------------------


def _gate_block(src: str) -> str:
    """The probe's implementation.

    TRDD-EZHM759T audit row 14 moved the body out of ``run_gate`` into the
    module-level ``_fork_parity_probe`` so the PIPELINE can run it too (with
    hooks uninstalled the G4b gate copy never runs at all). The contract these
    tests pin is unchanged — only where the code lives — so the slice follows it
    to the helper instead of the old inline span.
    """
    start = src.index("def _fork_parity_probe(")
    end = src.index("\ndef run_gate(", start)
    return src[start:end]


def test_gate_self_detects_applicability() -> None:
    """Most plugins are not Python and never fork. The gate must skip them
    outright rather than cost them a second suite run — the same self-detecting
    idiom G2e (compiled builds) and G2f (shellcheck) already use."""
    block = _gate_block(_src())
    assert "ProcessPoolExecutor" in block and "multiprocessing" in block
    assert "No process pools in this plugin" in block


def test_gate_skips_on_linux_instead_of_doubling_ci() -> None:
    """On Linux the ordinary run already forks. Re-running the suite there would
    double every CI minute and prove nothing new."""
    block = _gate_block(_src())
    assert "get_start_method" in block
    assert "already defaults to fork" in block


def test_gate_degrades_to_warning_where_fork_is_unavailable() -> None:
    """Windows has no fork. A missing capability must WARN, never block — the
    degrade-gracefully contract every G2* gate follows."""
    block = _gate_block(_src())
    assert "fork unavailable here" in block
    assert "WARNING" in block


def test_a_hang_is_treated_as_a_failure_not_as_inconclusive() -> None:
    """LOAD-BEARING: a deadlock manifests as a HANG. If the timeout were treated
    as 'could not check', the gate would stay silent on the ONE signal it exists
    to catch."""
    block = _gate_block(_src())
    assert "TimeoutExpired" in block
    assert "HUNG under the Linux fork default" in block
    # ...and it must actually block on that path.
    hang_idx = block.index("HUNG under the Linux fork default")
    assert "return 1" in block[hang_idx : hang_idx + 500]


def test_gate_cleans_up_its_temp_sitecustomize() -> None:
    """The probe writes a sitecustomize into the repo. Leaving it behind would
    dirty the working tree and trip the clean-tree gate on the next publish."""
    block = _gate_block(_src())
    assert "finally:" in block
    assert "rmtree" in block


def test_gate_names_the_remedy_not_just_the_symptom() -> None:
    """A blocked publish must tell the author what to DO. 'Your suite hangs' is
    not actionable; 'pass an explicit mp_context' is."""
    block = _gate_block(_src())
    assert "mp_context" in block


def test_gate_reuses_the_suite_timeout_budget() -> None:
    """Reuse the existing, overridable budget rather than minting a second
    hardcoded cap — a fixed bound is the #179 defect all over again."""
    assert "suite_timeout" in _gate_block(_src())


# ---------------------------------------------------------------------------
# Two-sided: these assertions must be capable of failing
# ---------------------------------------------------------------------------


def test_probe_temp_dir_is_gitignored() -> None:
    """The probe writes ``.cpv-forkparity/`` into the repo and removes it in a
    ``finally``. A SIGKILLed run cannot run that ``finally``, so the dir must
    also be ignored — otherwise the NEXT publish fails its clean-tree gate for a
    reason that has nothing to do with the plugin."""
    from generate_plugin_repo import gen_gitignore  # noqa: PLC0415

    assert ".cpv-forkparity/" in gen_gitignore(_params())


def test_emitted_gate_dependencies_are_defined_before_it() -> None:
    """The G4b CALL SITE reuses ``suite_timeout`` from earlier in ``run_gate``.
    Emitting it above that would be a NameError at publish time — which
    py_compile cannot catch, because the file is syntactically fine.

    The probe itself now lives in ``_fork_parity_probe``, defined ABOVE
    ``run_gate`` and taking its budget as a parameter, so the only ordering
    constraint left is this one."""
    src = _src()
    i_gate = src.index("[G4b] Linux fork-parity probe")
    assert src.index("suite_timeout = _test_suite_timeout") < i_gate
    assert src.index("def _fork_parity_probe(") < i_gate


def test_contract_assertions_are_not_vacuous() -> None:
    """Guard the guard. If ``_gate_block`` ever returned an empty slice, every
    assertion above would pass while checking nothing."""
    block = _gate_block(_src())
    assert len(block) > 500, f"gate block suspiciously small ({len(block)} chars) — slicing is wrong"
    assert block.count("\n") > 15


def test_gate_block_slice_is_actually_the_gate() -> None:
    block = _gate_block(_src())
    assert "[G3]" not in block and "[G4] Running tests" not in block


def test_no_stray_unescaped_newline_broke_the_template() -> None:
    """The template is built with escaped ``\\n`` inside f-strings; a mistake
    there yields a file that still parses but prints garbage."""
    block = _gate_block(_src())
    assert not re.search(r'cprint\(f"[^"]*\n[^"]*"\)', block)
