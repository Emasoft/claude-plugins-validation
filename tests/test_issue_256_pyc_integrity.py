"""#256 item 7 / TRDD-O3K5H8GG item 6: a pyc that matches a verified .py must never run.

Python loads `__pycache__/x.cpython-XYZ.pyc` INSTEAD of `x.py` when the pyc header
(mtime+size, or a hash-unchecked flag) matches the source, so planting a pyc beside
a source that passes the integrity check runs unverified code. The manifest walk
skips every `.pyc`, so the check could not see it. The fix is that the integrity-checked
entry points never read or write bytecode, and that a sourceless `.pyc` is reported.

Real subprocesses, real manifests (built with `_plugin_compute_hashes.compute_manifest`
on a COPY of scripts/, about 16 MB, far under the 4 GB test-data cap), no network
(the tag manifest is pre-seeded in the cache of a throwaway HOME), no mocks.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import marshal
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPTS_SRC = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_SRC))

import _plugin_compute_hashes  # noqa: E402
import _plugin_verify_hashes  # noqa: E402

PLUGIN_JSON_SRC = SCRIPTS_SRC.parent / ".claude-plugin" / "plugin.json"
MARK = "PYC-256-MARKER"
# conftest sets the skip variables for the suite; a child that inherited them would skip the very check under test.
_DROPPED_ENV = (
    "PLUGIN_SKIP_GITHUB_INTEGRITY",
    "CPV_SKIP_GITHUB_INTEGRITY",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPYCACHEPREFIX",
)


class Install(NamedTuple):
    root: Path
    scripts: Path
    target: Path
    env: dict[str, str]


@pytest.fixture
def install(tmp_path: Path) -> Install:
    """A standalone CPV install (copy of scripts/) whose tag manifest is cached offline."""
    root = tmp_path / "plugin"
    shutil.copytree(SCRIPTS_SRC, root / "scripts", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"))
    (root / ".claude-plugin").mkdir()
    shutil.copy2(PLUGIN_JSON_SRC, root / ".claude-plugin" / "plugin.json")
    version = json.loads(PLUGIN_JSON_SRC.read_text(encoding="utf-8"))["version"]
    home = tmp_path / "home"
    cache = home / ".cache" / "cpv"
    cache.mkdir(parents=True)
    manifest = _plugin_compute_hashes.compute_manifest(root)
    (cache / f"github-manifest-tag-v{version}.json").write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "target"
    (target / ".claude-plugin").mkdir(parents=True)
    (target / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "tiny", "version": "0.0.1", "description": "x"}), encoding="utf-8"
    )
    env = {k: v for k, v in os.environ.items() if k not in _DROPPED_ENV}
    # Without the ceiling a git work tree above tmp would make the added-file walk read an unrelated index.
    env.update(HOME=str(home), USERPROFILE=str(home), GIT_CEILING_DIRECTORIES=str(tmp_path))
    return Install(root=root, scripts=root / "scripts", target=target, env=env)


def _run(inst: Install, *argv: str) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, *argv],
        capture_output=True,
        text=True,
        env=inst.env,
        cwd=str(inst.root.parent),
        timeout=900,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _plant(inst: Install, module: str, flavor: str, tag: str, extra: str = "") -> Path:
    """Write a pyc for scripts/<module>.py whose header matches the REAL source."""
    src = inst.scripts / f"{module}.py"
    data = src.read_bytes()
    st = src.stat()
    payload = "\nimport sys as _poc_s\nprint(" + repr(f"{MARK} {tag}") + ", file=_poc_s.stderr)\n" + extra
    code = compile(data.decode("utf-8") + payload, str(src), "exec", dont_inherit=True)
    magic = importlib.util.MAGIC_NUMBER
    if flavor == "timestamp":
        header = magic + struct.pack("<III", 0, int(st.st_mtime) & 0xFFFFFFFF, st.st_size & 0xFFFFFFFF)
    elif flavor == "hash-unchecked":
        header = magic + struct.pack("<I", 0b01) + importlib.util.source_hash(data)
    else:
        raise ValueError(flavor)
    pyc = Path(importlib.util.cache_from_source(str(src)))
    pyc.parent.mkdir(exist_ok=True)
    pyc.write_bytes(header + marshal.dumps(code))
    return pyc


def test_planted_timestamp_pyc_does_not_run(install: Install) -> None:
    """A timestamp-matching pyc for a module the launcher imports is not executed."""
    _plant(install, "cpv_watchdog", "timestamp", "ts")
    rc, out = _run(install, str(install.scripts / "remote_validation.py"), "plugin", str(install.target))
    assert "SUMMARY:" in out, f"the run did not complete (rc={rc}): {out[-2000:]}"
    assert MARK not in out, "planted bytecode ran instead of the verified source"


def test_planted_hash_unchecked_pyc_does_not_run(install: Install) -> None:
    """A hash-unchecked pyc (source never consulted by the loader) is not executed."""
    _plant(install, "cpv_watchdog", "hash-unchecked", "hu")
    rc, out = _run(install, str(install.scripts / "remote_validation.py"), "plugin", str(install.target))
    assert "SUMMARY:" in out, f"the run did not complete (rc={rc}): {out[-2000:]}"
    assert MARK not in out, "planted bytecode ran instead of the verified source"


def test_direct_validate_plugin_ignores_planted_pyc(install: Install) -> None:
    """validate_plugin.py run directly (not via the launcher) does not execute a planted pyc."""
    _plant(install, "cpv_dependency_schema", "timestamp", "direct")
    rc, out = _run(install, str(install.scripts / "validate_plugin.py"), str(install.target))
    assert "SUMMARY:" in out, f"the run did not complete (rc={rc}): {out[-2000:]}"
    assert MARK not in out, "planted bytecode ran instead of the verified source"


def test_poisoned_verifier_pyc_cannot_hide_tampered_source(install: Install) -> None:
    """A pyc for the verifier itself must not turn a hash mismatch into a pass."""
    watchdog = install.scripts / "cpv_watchdog.py"
    watchdog.write_bytes(watchdog.read_bytes() + b"\n# tampered after the manifest was built\n")
    _plant(
        install,
        "_plugin_verify_hashes",
        "timestamp",
        "verifier-poisoned",
        extra="\nverify_self_integrity = lambda *a, **k: True\n",
    )
    rc, out = _run(install, str(install.scripts / "validate_plugin.py"), str(install.target))
    assert "integrity manifest mismatch" in out, f"the tampered source was not refused (rc={rc}): {out[-2000:]}"
    assert rc == 2
    assert MARK not in out, "the poisoned verifier bytecode was loaded"


def _verify_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra_files: tuple[str, ...]) -> bool:
    """Plugin-mode verify of a tiny install whose cached tag manifest was built BEFORE extra_files appeared."""
    for var in ("PLUGIN_SKIP_GITHUB_INTEGRITY", "CPV_SKIP_GITHUB_INTEGRITY", "PYTEST_CURRENT_TEST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(_plugin_verify_hashes, "_VERIFIED_THIS_PROCESS", False)
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "t", "version": "1.2.3", "description": "x"}), encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "validate_x.py").write_text("# covered\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(_plugin_verify_hashes, "CACHE_DIR", cache_dir)
    manifest = _plugin_compute_hashes.compute_manifest(root)
    (cache_dir / "github-manifest-tag-v1.2.3.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel in extra_files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"\x00")
    return _plugin_verify_hashes.verify_self_integrity(plugin_root=root, fail_on_mismatch=False, quiet=True)


def test_clean_tiny_install_verifies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Control: the tiny install used by the sourceless tests verifies when nothing was added."""
    assert _verify_root(monkeypatch, tmp_path, ()) is True


@pytest.mark.parametrize("name", ["x.pyc", "x.pyo", "x.pyd"])
def test_sourceless_pyc_pyo_pyd_reported_as_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str) -> None:
    """A sourceless bytecode or extension file beside the sources is importable, so it is reported as added."""
    assert _verify_root(monkeypatch, tmp_path, (f"scripts/{name}",)) is False


def test_pycache_pyc_still_not_reported_as_added(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Paired: a cached pyc under __pycache__ stays invisible to the walk (the entry points never read it)."""
    assert _verify_root(monkeypatch, tmp_path, ("scripts/__pycache__/x.cpython-312.pyc",)) is True


_REQUIRED_TARGETS = (
    "sys.dont_write_bytecode",
    "sys.pycache_prefix",
    'os.environ["PYTHONDONTWRITEBYTECODE"]',
    'os.environ["PYTHONPYCACHEPREFIX"]',
)


def _norm(expr: str) -> str:
    return ast.unparse(ast.parse(expr, mode="eval"))


def _cpv_import_lines(scope: ast.AST) -> list[int]:
    stems = {p.stem for p in SCRIPTS_SRC.glob("*.py")} - set(sys.stdlib_module_names)
    lines: list[int] = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            mods = [node.module or ""]
        else:
            continue
        if any(m.split(".")[0] in stems for m in mods):
            lines.append(node.lineno)
    return lines


def _preamble_assigns(scope: ast.AST) -> dict[str, ast.Assign]:
    wanted = {_norm(t) for t in _REQUIRED_TARGETS}
    found: dict[str, ast.Assign] = {}
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            key = ast.unparse(node.targets[0])
            if key in wanted:
                found[key] = node
    return found


def _assert_preamble(scope: ast.AST, label: str) -> int:
    """All four statements present with the abspath(__file__) prefix; returns the last statement line."""
    found = _preamble_assigns(scope)
    assert set(found) == {_norm(t) for t in _REQUIRED_TARGETS}, f"{label}: missing no-bytecode statements"
    for key in ("sys.pycache_prefix", _norm(_REQUIRED_TARGETS[3])):
        assert ast.unparse(found[key].value) == "os.path.abspath(__file__)", f"{label}: {key} must name the entry file"
    return max(node.lineno for node in found.values())


def _tree(name: str) -> ast.Module:
    return ast.parse((SCRIPTS_SRC / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["validate_plugin.py", "validate_marketplace.py", "_plugin_verify_hashes.py"])
def test_gated_entry_points_carry_the_preamble_before_first_cpv_import(name: str) -> None:
    """The no-bytecode block sits inline, under the __main__/__mp_main__ gate, ahead of every CPV import."""
    tree = _tree(name)
    gates = [
        n
        for n in tree.body
        if isinstance(n, ast.If) and "__mp_main__" in ast.unparse(n.test) and "__main__" in ast.unparse(n.test)
    ]
    assert gates, f"{name}: no module-level __main__/__mp_main__ gate carrying the preamble"
    last = max(_assert_preamble(g, name) for g in gates)
    first_import = min(_cpv_import_lines(tree), default=10**9)
    assert last < first_import, f"{name}: the preamble must precede the first CPV import (line {first_import})"


def test_launcher_main_carries_the_preamble_before_first_cpv_import() -> None:
    """remote_validation.main() sets the no-bytecode block before it imports cpv_watchdog."""
    main = next(n for n in _tree("remote_validation.py").body if isinstance(n, ast.FunctionDef) and n.name == "main")
    last = _assert_preamble(main, "remote_validation.main")
    assert last < min(_cpv_import_lines(main)), "remote_validation.main: preamble after the first CPV import"


def test_cli_wrappers_call_the_helper_before_their_lazy_import() -> None:
    """Every cli.py console-script wrapper starts with _no_bytecode_cache(), which holds the preamble."""
    tree = _tree("cli.py")
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    helper = next((f for f in funcs if f.name == "_no_bytecode_cache"), None)
    assert helper is not None, "cli.py has no _no_bytecode_cache helper"
    _assert_preamble(helper, "cli._no_bytecode_cache")
    wrappers = [f for f in funcs if not f.name.startswith("_")]
    assert len(wrappers) >= 20
    for fn in wrappers:
        body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0]
        assert isinstance(first, ast.Expr) and ast.unparse(first.value) == "_no_bytecode_cache()", fn.name


def test_child_interpreter_inherits_prefix(install: Install) -> None:
    """The preamble also sets the environment, so a spawned interpreter reads no bytecode either."""
    probe = "import sys; print(sys.pycache_prefix); print(sys.dont_write_bytecode)"
    code = (
        "import subprocess, sys\n"
        f"sys.path.insert(0, {str(SCRIPTS_SRC)!r})\n"
        "import cli\n"
        "cli._no_bytecode_cache()\n"
        f'print(subprocess.run([sys.executable, "-c", {probe!r}], capture_output=True, text=True, check=True).stdout)\n'
    )
    rc, out = _run(install, "-c", code)
    assert rc == 0, out
    assert out.split() == [str(SCRIPTS_SRC / "cli.py"), "True"]
