"""Two-sided tests for GitHub issue #142 — cpv-canonical-pipeline TEMPLATE defects
that broke the GENERATED plugin under ``mypy --strict`` / CI.

These templates ship to OTHER plugins, so the generated output must be
mypy-clean and the generated ``pyproject.toml`` must declare the dev
dependencies the generated CI workflows actually install.

DEFECT #1 — the generated ``publish.py`` network-resilience import-fallback
shims (``gh_with_retry`` / ``git_with_retry``) carried a bare
``# type: ignore[no-redef]``. Downstream CI runs the generated ``publish.py``
under ``mypy --strict``, which ALSO reports [misc] ("All conditional function
variants must have identical signatures") because the typed real import and the
minimal fallback shim are conditional variants with non-identical signatures —
so the bare ``[no-redef]`` ignore left an uncovered [misc] error. The fix
broadens both to ``# type: ignore[no-redef, misc]`` (the standard
import-fallback idiom; cf. the tomli fallback in ``cpv_lint_engine.py`` using
``[no-redef,import-not-found]``).

DEFECT #2 — the generated CI/release workflows run ``uv sync --extra dev``, so
the generated ``pyproject.toml`` MUST declare a ``[project.optional-dependencies]
dev`` extra that is a superset of ``{pytest, ruff, mypy}``.

Every guard is TWO-SIDED: the fixed form is asserted PRESENT and the old broken
form is asserted ABSENT, so a regression in either direction fails a test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_publish_py,
    gen_pyproject_toml,
)


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


# ── DEFECT #1: the publish.py fallback-shim ignore comments ──────────────────

# Matches a bare ``[no-redef]`` ignore that is NOT followed by an additional
# error code (i.e. the OLD broken form). ``misc`` is the required extra code, so
# ``[no-redef, misc]`` / ``[no-redef,misc]`` are correctly EXCLUDED by the
# negative lookahead.
_BARE_NO_REDEF_RE = re.compile(r"#\s*type:\s*ignore\[no-redef\](?!\s*,)")
# Matches the FIXED form for either fallback def (whitespace after the comma is
# tolerant: ``[no-redef, misc]`` or ``[no-redef,misc]``).
_FIXED_DEF_RE = re.compile(
    r"def (?:gh|git)_with_retry\(cmd, \*\*kwargs\):\s*#\s*type:\s*ignore\[no-redef,\s*misc\]"
)


def test_publish_fallback_defs_carry_no_redef_misc() -> None:
    """Both fallback shims carry ``[no-redef, misc]`` (the FIXED form)."""
    text = gen_publish_py(_params())
    fixed = _FIXED_DEF_RE.findall(text)
    # Exactly the two fallback defs (gh_with_retry + git_with_retry).
    assert len(fixed) == 2, f"expected 2 fixed fallback defs, found {len(fixed)}: {fixed}"


def test_publish_fallback_defs_no_bare_no_redef() -> None:
    """The OLD bare ``[no-redef]``-only ignore form is ABSENT from publish.py."""
    text = gen_publish_py(_params())
    bare = _BARE_NO_REDEF_RE.findall(text)
    assert bare == [], f"old bare [no-redef] ignore form must be gone, found: {bare}"


def test_publish_fallback_defs_present_for_both_functions() -> None:
    """Each named fallback function individually carries the ``[no-redef, misc]`` form."""
    text = gen_publish_py(_params())
    for fn in ("gh_with_retry", "git_with_retry"):
        pat = re.compile(
            rf"def {fn}\(cmd, \*\*kwargs\):\s*#\s*type:\s*ignore\[no-redef,\s*misc\]"
        )
        assert pat.search(text), f"{fn} fallback def missing the [no-redef, misc] ignore"


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
def test_generated_publish_py_is_mypy_strict_clean_on_fallback_defs(
    tmp_path: Path,
) -> None:
    """``mypy --strict`` on the generated publish.py reports 0 errors on the
    fallback defs — neither [misc] nor [no-redef] resurfaces.

    The generated publish.py imports ``cpv_network_resilience`` (its real,
    typed retry wrappers) and falls back to the shims on ImportError. To
    exercise the conditional-variant [misc] path that ``--strict`` flags, the
    real module is copied alongside the generated file so the typed import
    resolves and mypy sees BOTH the real signatures and the fallback shims.
    """
    real_mod = SCRIPTS / "cpv_network_resilience.py"
    assert real_mod.is_file(), "cpv_network_resilience.py must exist to type-check the import"
    shutil.copy2(real_mod, tmp_path / "cpv_network_resilience.py")

    pub = tmp_path / "publish.py"
    pub.write_text(gen_publish_py(_params()), encoding="utf-8")

    proc = subprocess.run(
        [
            "mypy",
            "--strict",
            "--no-error-summary",
            "--no-color-output",
            str(pub),
        ],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    # The fallback defs must NOT be the source of any misc / no-redef error.
    offending = [
        ln
        for ln in out.splitlines()
        if ("publish.py" in ln)
        and (("[misc]" in ln) or ("[no-redef]" in ln))
        and (("gh_with_retry" in ln) or ("git_with_retry" in ln) or ("conditional function" in ln))
    ]
    assert offending == [], (
        "mypy --strict reported misc/no-redef errors on the fallback defs:\n"
        + "\n".join(offending)
        + "\n--- full mypy output ---\n"
        + out
    )


# ── DEFECT #2: pyproject dev extra ⊇ {pytest, ruff, mypy} ─────────────────────

# A dependency specifier like ``pytest>=8.0.0`` — the leading run of name chars
# (PEP 508: letters, digits, ``.``/``-``/``_``) is the distribution name.
_DEP_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_REQUIRED_DEV = {"pytest", "ruff", "mypy"}


def _dev_extra_names(pyproject_text: str) -> set[str]:
    """Parse ``[project.optional-dependencies].dev`` and return the normalized
    set of distribution names it declares."""
    data = tomllib.loads(pyproject_text)
    dev = data["project"]["optional-dependencies"]["dev"]
    names: set[str] = set()
    for spec in dev:
        m = _DEP_NAME_RE.match(spec)
        assert m is not None, f"unparseable dependency specifier: {spec!r}"
        # PEP 503 normalization (case-insensitive; ``_`` ≡ ``-``) so e.g.
        # ``pytest-cov`` and a hypothetical ``PyTest`` compare predictably.
        names.add(m.group(1).lower().replace("_", "-"))
    return names


def test_pyproject_declares_dev_extra() -> None:
    """The generated pyproject declares ``[project.optional-dependencies].dev``."""
    data = tomllib.loads(gen_pyproject_toml(_params()))
    assert "optional-dependencies" in data["project"], "missing [project.optional-dependencies]"
    assert "dev" in data["project"]["optional-dependencies"], "missing the 'dev' extra"
    assert isinstance(
        data["project"]["optional-dependencies"]["dev"], list
    ), "the 'dev' extra must be an array"


def test_pyproject_dev_extra_superset_of_required() -> None:
    """The dev extra is a SUPERSET of {pytest, ruff, mypy} (so ``uv sync --extra
    dev`` installs the tools the generated CI/release workflows invoke)."""
    names = _dev_extra_names(gen_pyproject_toml(_params()))
    missing = _REQUIRED_DEV - names
    assert not missing, f"dev extra is missing required tools: {sorted(missing)} (have {sorted(names)})"


def test_pyproject_dev_extra_each_tool_present() -> None:
    """Each required tool is individually present in the dev extra (two-sided per tool)."""
    names = _dev_extra_names(gen_pyproject_toml(_params()))
    for tool in sorted(_REQUIRED_DEV):
        assert tool in names, f"required dev tool {tool!r} absent from the dev extra: {sorted(names)}"
