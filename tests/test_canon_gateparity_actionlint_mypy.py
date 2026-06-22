"""Two-sided tests for the publish.py --gate CI-parity gates (G2c actionlint + G2d mypy).

Gap B / Increment C (TRDD-0085a444): the generated publish.py ``--gate`` (the
pre-push hook) must run actionlint + mypy LOCALLY so an adopting plugin cannot
pass its local gate yet still fail CI's Lint job — which runs both. The new
gates mirror the existing G2b jscpd probe-then-degrade-WARNING pattern:

* a MISSING tool DEGRADES to a non-blocking WARNING (a push is NEVER
  false-blocked on a tool-install failure — the issue #143 discipline);
* a tool that RAN and found a real error BLOCKS the gate (``return 1``).

Each gate is asserted two-sided: the present-block, the degrade-when-absent
branch, and the block-on-real-error branch all exist in the emitted template,
and the whole template still compiles.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
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


def test_emitted_template_is_valid_python() -> None:
    """The generated publish.py with the new G2c/G2d gates still compiles."""
    text = gen_publish_py(_params())
    compile(text, "publish.py", "exec")  # raises SyntaxError if the template is broken


def test_gate2c_actionlint_block_present() -> None:
    """The --gate emits a G2c actionlint stage probing actionlint on PATH."""
    text = gen_publish_py(_params())
    assert "[G2c] Workflow lint (actionlint" in text
    assert 'shutil.which("actionlint")' in text


def test_gate2c_actionlint_degrades_when_absent() -> None:
    """G2c WARNs + skips (never blocks) when actionlint is not on PATH."""
    text = gen_publish_py(_params())
    assert "actionlint not found — workflow lint SKIPPED locally" in text


def test_gate2c_actionlint_blocks_on_real_error() -> None:
    """G2c BLOCKS (return 1) when actionlint actually runs and finds errors."""
    text = gen_publish_py(_params())
    assert "BLOCKED: actionlint found workflow-syntax errors" in text


def test_gate2d_mypy_block_present() -> None:
    """The --gate emits a G2d mypy stage matching CI's mypy invocation."""
    text = gen_publish_py(_params())
    assert "[G2d] Type-check (mypy" in text
    assert '"scripts/", "--ignore-missing-imports"' in text


def test_gate2d_mypy_degrades_when_absent() -> None:
    """G2d WARNs + skips (never blocks) when mypy/uv is unavailable, and also on
    a --version probe failure (mirrors the G2b jscpd degrade pattern)."""
    text = gen_publish_py(_params())
    assert "mypy/uv not found — type-check SKIPPED locally" in text
    assert "mypy could not run — type-check SKIPPED locally" in text


def test_gate2d_mypy_blocks_on_real_error() -> None:
    """G2d BLOCKS (return 1) when mypy actually runs and finds type errors."""
    text = gen_publish_py(_params())
    assert "BLOCKED: mypy found type errors in scripts/" in text


def test_gate2d_mypy_uses_version_probe() -> None:
    """G2d uses a --version probe to distinguish 'mypy unavailable' (WARN) from
    'mypy ran, found errors' (BLOCK) — the issue #143 degrade-gracefully pattern."""
    text = gen_publish_py(_params())
    assert 'mypy_cmd + ["--version"]' in text


def test_new_gates_sit_between_jscpd_and_validate() -> None:
    """G2c/G2d are emitted AFTER G2b jscpd and BEFORE G3 validate (correct order)."""
    text = gen_publish_py(_params())
    i_jscpd = text.index("[G2b] Copy-paste check")
    i_actionlint = text.index("[G2c] Workflow lint")
    i_mypy = text.index("[G2d] Type-check")
    i_validate = text.index("[G3] Validating plugin")
    assert i_jscpd < i_actionlint < i_mypy < i_validate


def test_gates_present_in_submodule_build_profile() -> None:
    """The additive submodule-build profile still carries the G2c/G2d gates
    (its body includes the standard body verbatim)."""
    text = gen_publish_py(_params(), "submodule-build")
    assert "[G2c] Workflow lint (actionlint" in text
    assert "[G2d] Type-check (mypy" in text
