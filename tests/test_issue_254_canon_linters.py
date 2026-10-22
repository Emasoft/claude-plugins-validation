"""Issue #254 (and #257 item 3): the canon Mega-Linter must lint with CPV's own ruff and
bandit configuration, and the local preflight must run the SAME rule set as CI.

Real tools, no mocks: every bandit/ruff assertion runs the actual binary on a canon
file rendered by the generator, and the adversarial cases plant a real defect.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight  # noqa: E402
import generate_plugin_repo as gen  # noqa: E402
import standardize_plugin  # noqa: E402
from cpv_ci_preflight import PreflightResult  # noqa: E402

BANDIT = shutil.which("bandit")
RUFF = shutil.which("ruff")
needs_bandit = pytest.mark.skipif(BANDIT is None, reason="bandit is not installed")
needs_ruff = pytest.mark.skipif(RUFF is None, reason="ruff is not installed")


def _params() -> gen.PluginParams:
    return gen.PluginParams(
        name="demo-plugin", description="d", author="A", author_email="a@b.c", github_owner="Emasoft"
    )


@pytest.fixture
def canon(tmp_path: Path) -> Path:
    """A directory holding the canon files the generator emits, plus one pytest file."""
    p = _params()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts" / "publish.py").write_text(gen.gen_publish_py(p), encoding="utf-8")
    (tmp_path / "scripts" / "cpv_network_resilience.py").write_text(
        gen.gen_cpv_network_resilience_py(), encoding="utf-8"
    )
    (tmp_path / ".bandit.yml").write_text(gen.gen_bandit_yml(p), encoding="utf-8")
    (tmp_path / ".ruff.toml").write_text(gen.gen_ruff_toml(p), encoding="utf-8")
    (tmp_path / ".mega-linter.yml").write_text(gen.gen_mega_linter_yml(p), encoding="utf-8")
    (tmp_path / "tests" / "test_x.py").write_text("def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    return tmp_path


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=300, check=False)


# ---- what CI actually does (the evidence the old `-ll` comment was wrong) --------


@needs_bandit
def test_bare_bandit_fails_on_low_findings_where_ll_passes(canon: Path) -> None:
    """Mega-Linter runs bandit with no severity filter: LOW findings exit 1, `-ll` hides them."""
    assert BANDIT is not None
    files = ["scripts/publish.py", "scripts/cpv_network_resilience.py"]
    assert _run([BANDIT, "-q", *files], canon).returncode == 1
    assert _run([BANDIT, "-q", "-ll", *files], canon).returncode == 0


@needs_ruff
def test_mega_linter_default_ruff_config_flags_canon_publish_py(canon: Path) -> None:
    """Root cause of the I001: Mega-Linter's own default (line-length 88) disagrees with canon."""
    assert RUFF is not None
    default = canon / "mega-default.toml"
    default.write_text('line-length = 88\n[lint]\nselect = ["E4", "E7", "E9", "F"]\n', encoding="utf-8")
    argv = [RUFF, "check", "--no-cache", "--select=E,F,W,I", "--ignore=E501", "scripts"]
    assert _run([*argv, "--config", str(default)], canon).returncode == 1
    assert _run([*argv, "--config", ".ruff.toml"], canon).returncode == 0


# ---- the canon is clean under the CPV-authored config -----------------------------


@needs_bandit
@pytest.mark.parametrize("test_path", ["tests/test_x.py", "./tests/test_x.py"])
def test_canon_is_bandit_clean_under_canon_config(canon: Path, test_path: str) -> None:
    """Both path spellings (Mega-Linter passes relative paths, `-r .` yields `./`)."""
    assert BANDIT is not None
    proc = _run(
        [BANDIT, "-q", "-c", ".bandit.yml", "scripts/publish.py", "scripts/cpv_network_resilience.py", test_path], canon
    )
    assert proc.returncode == 0, proc.stdout[-2000:]


@needs_ruff
def test_canon_is_ruff_clean_under_canon_config(canon: Path) -> None:
    assert RUFF is not None
    proc = _run([RUFF, "check", "--no-cache", "scripts", "tests"], canon)
    assert proc.returncode == 0, proc.stdout[-2000:]


# ---- adversarial: the CPV config must NOT mute real defects -----------------------


@needs_bandit
def test_canon_bandit_config_still_flags_real_defects(canon: Path) -> None:
    """shell=True, a partial-path call, a hardcoded password and a non-test `assert` all still fire."""
    assert BANDIT is not None
    (canon / "scripts" / "evil.py").write_text(
        "import subprocess\n"
        "password = 'hunter2hunter2'\n"
        "def f(cmd):\n"
        "    subprocess.run(cmd, shell=True)\n"
        "    subprocess.run(['ls', '-l'])\n"
        "    assert cmd\n",
        encoding="utf-8",
    )
    proc = _run([BANDIT, "-q", "-c", ".bandit.yml", "scripts/evil.py"], canon)
    assert proc.returncode == 1
    for rule in ("B602", "B607", "B105", "B101"):
        assert rule in proc.stdout, (rule, proc.stdout[-1500:])
    # B404/B603 are the only rules the canon config skips.
    assert "B603" not in proc.stdout and "B404" not in proc.stdout


@needs_bandit
@pytest.mark.parametrize("path", ["tests/test_y.py", "./tests/test_y.py", "tests/conftest.py"])
def test_assert_skip_covers_only_b101_in_test_files(canon: Path, path: str) -> None:
    """A test file may `assert`, but every OTHER bandit rule still scans it."""
    assert BANDIT is not None
    (canon / path).write_text(
        "import subprocess\n\ndef test_b():\n    assert True\n    subprocess.run('x', shell=True)\n", encoding="utf-8"
    )
    out = _run([BANDIT, "-q", "-c", ".bandit.yml", path], canon).stdout
    assert "B602" in out
    assert "B101" not in out


def test_bandit_config_skips_exactly_b404_and_b603(canon: Path) -> None:
    cfg = yaml.safe_load((canon / ".bandit.yml").read_text(encoding="utf-8"))
    assert cfg["skips"] == ["B404", "B603"]


# ---- Mega-Linter is pointed at the CPV-owned files --------------------------------


def test_mega_linter_reads_the_canon_config_files(canon: Path) -> None:
    cfg = yaml.safe_load((canon / ".mega-linter.yml").read_text(encoding="utf-8"))
    assert cfg["PYTHON_RUFF_CONFIG_FILE"] == ".ruff.toml"
    assert cfg["PYTHON_BANDIT_CONFIG_FILE"] == ".bandit.yml"
    assert cfg["PYTHON_RUFF_RULES_PATH"] == "." and cfg["PYTHON_BANDIT_RULES_PATH"] == "."
    # The rule selection has ONE source: CLI arguments would override the file in CI only.
    assert "PYTHON_RUFF_ARGUMENTS" not in cfg


def test_scaffold_emits_the_two_files_and_standardize_force_templates_them() -> None:
    for name, func in ((".ruff.toml", "gen_ruff_toml"), (".bandit.yml", "gen_bandit_yml")):
        assert standardize_plugin._FILE_TO_GENERATOR[name] == func
        assert name in standardize_plugin._FORCE_TEMPLATE_FILES
        assert name in {rel for rel, _req, _desc in standardize_plugin.STANDARD_FILES}


def test_pyproject_no_longer_carries_a_competing_ruff_table() -> None:
    """ruff ignores [tool.ruff] when .ruff.toml sits beside it: two sources would drift."""
    assert "[tool.ruff" not in gen.gen_pyproject_toml(_params())


# ---- the local preflight runs the same thing as CI --------------------------------


def test_argv_bandit_is_ci_command_without_severity_filter(canon: Path) -> None:
    argv = cpv_ci_preflight._argv_bandit("/usr/bin/bandit", canon)
    assert argv[:4] == ["/usr/bin/bandit", "-q", "-c", ".bandit.yml"]
    assert not {"-l", "-ll", "-lll", "-r"} & set(argv)
    assert "scripts/publish.py" in argv and "tests/test_x.py" in argv


def test_python_file_list_skips_only_never_tracked_trees(tmp_path: Path) -> None:
    for d in ("src", ".venv/lib", "node_modules/x", ".git/hooks", "venv/lib", "tests/fixtures"):
        (tmp_path / d).mkdir(parents=True)
        (tmp_path / d / "m.py").write_text("x = 1\n", encoding="utf-8")
    found = cpv_ci_preflight._python_file_paths(tmp_path)
    assert sorted(found) == ["src/m.py", "tests/fixtures/m.py"]


@needs_bandit
def test_preflight_bandit_gate_passes_canon_and_fails_planted_defect(canon: Path) -> None:
    """Two-sided, real binary: the canon passes; a planted `shell=True` fails."""
    ok = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_bandit(ok, {"PYTHON_BANDIT"})
    assert [f.severity for f in ok.findings if f.gate == "bandit"] == ["PASS"]

    (canon / "scripts" / "evil.py").write_text("import os\nos.system('rm -rf /tmp/x')\n", encoding="utf-8")
    bad = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_bandit(bad, {"PYTHON_BANDIT"})
    assert [f.severity for f in bad.findings if f.gate == "bandit"] == ["FAIL"]


@needs_ruff
def test_preflight_ruff_gate_fails_planted_import_disorder(canon: Path) -> None:
    ok = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_ruff(ok, {"PYTHON_RUFF"})
    assert [f.severity for f in ok.findings if f.gate == "ruff"] == ["PASS"]

    (canon / "scripts" / "unsorted.py").write_text("import sys\nimport os\n\nprint(os, sys)\n", encoding="utf-8")
    bad = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_ruff(bad, {"PYTHON_RUFF"})
    assert [f.severity for f in bad.findings if f.gate == "ruff"] == ["FAIL"]


def test_preflight_bandit_gate_passes_when_no_python_files(tmp_path: Path) -> None:
    res = PreflightResult(plugin_path=tmp_path)
    cpv_ci_preflight._gate_bandit(res, {"PYTHON_BANDIT"})
    assert [f.severity for f in res.findings] == ["PASS"]


# ---- the B607 fix in the canon publish.py -----------------------------------------


def test_canon_publish_py_resolves_executables_through_exe_helper() -> None:
    src = gen.gen_publish_py(_params())
    assert "def _exe(name: str) -> str:" in src
    assert 'subprocess.run(["git"' not in src and 'subprocess.run(["uv"' not in src
    namespace: dict[str, object] = {"shutil": shutil}
    helper = src[src.index("def _exe(") : src.index("def run(")]
    exec(helper, namespace)  # noqa: S102 - the generator's own helper, no external input
    exe = namespace["_exe"]
    assert callable(exe)
    assert Path(exe("python3")).is_absolute()
    assert exe("definitely-not-an-installed-tool-xyz") == "definitely-not-an-installed-tool-xyz"


# ---- review follow-ups: explicit config, git-accurate file list, chunking ---------


def test_bandit_config_never_skips_the_rules_that_carry_a_signal(canon: Path) -> None:
    cfg = yaml.safe_load((canon / ".bandit.yml").read_text(encoding="utf-8"))
    for rule in ("B602", "B604", "B605", "B606", "B607"):
        assert rule not in cfg["skips"], rule
    assert cfg["skips"] == ["B404", "B603"]


@needs_ruff
def test_nested_ruff_toml_cannot_weaken_the_local_ruff_gate(canon: Path) -> None:
    """`--config .ruff.toml` is explicit: a tests/ruff.toml ignoring F401 changes nothing."""
    (canon / "tests" / "ruff.toml").write_text('[lint]\nignore = ["F401"]\n', encoding="utf-8")
    (canon / "tests" / "test_unused.py").write_text("import os\n", encoding="utf-8")
    assert cpv_ci_preflight._argv_ruff("ruff", canon)[1:5] == ["check", "--no-cache", "--config", ".ruff.toml"]
    res = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_ruff(res, {"PYTHON_RUFF"})
    assert [f.severity for f in res.findings if f.gate == "ruff"] == ["FAIL"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def test_tracked_file_under_a_venv_named_dir_is_linted_in_a_git_repo(tmp_path: Path) -> None:
    """In a git repo `git ls-files` is the authority: a TRACKED docs/venv/x.py must not escape."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "docs" / "venv").mkdir(parents=True)
    (tmp_path / "docs" / "venv" / "x.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    _git(tmp_path, "add", "docs/venv/x.py", ".gitignore")
    found = cpv_ci_preflight._python_file_paths(tmp_path)
    assert "docs/venv/x.py" in found
    assert ".venv/ignored.py" not in found  # untracked AND gitignored: not shipped


def test_missing_canon_bandit_config_is_reported_as_drift(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    res = PreflightResult(plugin_path=tmp_path)
    cpv_ci_preflight._warn_missing_canon_config(res, "bandit", ".bandit.yml")
    (finding,) = res.findings
    assert finding.severity == "WARNING"
    assert "canonical .bandit.yml is missing" in finding.message and "drift" in finding.message


def test_chunking_splits_the_file_list_and_keeps_every_path() -> None:
    files = [f"f{i}.py" for i in range(1201)]
    chunks = cpv_ci_preflight._chunked_argvs(["bandit", "-q"], files, 500)
    assert [len(c) - 2 for c in chunks] == [500, 500, 201]
    assert [p for c in chunks for p in c[2:]] == files
    assert cpv_ci_preflight._chunked_argvs(["bandit"], []) == [["bandit"]]


@needs_bandit
def test_a_defect_in_the_last_chunk_still_fails_the_gate(canon: Path) -> None:
    """1001 files = 3 chunks of 500; the planted defect sorts into the final chunk."""
    (canon / "tests" / "test_x.py").unlink()
    bulk = canon / "bulk"
    bulk.mkdir()
    for i in range(1000):
        (bulk / f"m{i:04d}.py").write_text("x = 1\n", encoding="utf-8")
    (bulk / "zz_evil.py").write_text("import os\nos.system('ls')\n", encoding="utf-8")
    argvs = cpv_ci_preflight._argvs_bandit("bandit", canon)
    assert len(argvs) >= 3
    res = PreflightResult(plugin_path=canon)
    cpv_ci_preflight._gate_bandit(res, {"PYTHON_BANDIT"})
    assert [f.severity for f in res.findings if f.gate == "bandit"] == ["FAIL"]
