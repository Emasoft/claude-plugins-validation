"""Wave-2 CI-failure root fixes: CIP-1/5/6 MIGRATE (not just warn), RC-9's real
source, RC-1's migration gap, and the derived CIP range.

Grounded in ``reports/ci-failure-forensics/20260713_123038+0200-agent-pipeline-failures.md``
(RC-1, RC-2, RC-4, RC-7/8, RC-9 + "Top 5 fixes" row 3). Six of the 18 observed CI
failures come from defects CPV already DETECTED but never REPAIRED — a legacy repo
kept its broken workflow until a human intervened.

EVERY migration here is tested TWO-SIDED, because a migration that rewrites a
VALID input is a NEW bug, strictly worse than the one it fixes:

* the broken input is REPAIRED, **and**
* a correct / already-good input is left BYTE-IDENTICAL (the positive control).

The positive controls that matter most, each pinned below:

* the correct LOCAL scan idiom ``CLAUDE_PRIVATE_USERNAMES="$(whoami)"`` is a
  SHELL assignment and must survive the CIP-1 migration untouched (stripping it
  would break every local validation run);
* a valid ``@master`` / ``@v<semver>`` / SHA CPV pin must never be rewritten;
* an author's ``.jscpd.json`` / ``.commitlintrc.json`` must never be clobbered;
* a plugin whose CI is NOT sharded must never have ``pytest-split`` invented for it.

Nothing here suppresses a security rule or relaxes ``--strict``: the private-path
LEAK rule keeps firing on a genuine leak — CIP-1 removes a *misconfigured input*
that was feeding that rule the PUBLIC owner as if it were a private username.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_parity_checks  # noqa: E402
import cpv_ci_preflight  # noqa: E402
from cpv_ci_parity_checks import check_ci_parity  # noqa: E402
from generate_plugin_repo import PYTEST_SPLIT_REQUIREMENT  # noqa: E402
from standardize_plugin import (  # noqa: E402
    _PROVISION_DEV_EXTRA,
    _canonical_dev_extras_missing,
    _project_declares_pytest_split,
    _pytest_split_requirement,
    _strip_inverted_private_usernames,
    _workflow_runs_sharded_pytest,
    audit_commitlint_config,
    audit_inverted_private_usernames,
    provision_commitlintrc_config,
    provision_dev_extra,
    provision_jscpd_config,
    repin_stale_cpv_ref,
)

# ─────────────────────────────────────────────────────────────────────────
# Fixtures — minimal plugin trees
# ─────────────────────────────────────────────────────────────────────────

SHARDED_CI = """\
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        group: [1, 2]
    steps:
      - uses: actions/checkout@v5
      - run: uv sync --extra dev
      - run: uv run pytest tests/ --splits 2 --group ${{ matrix.group }} -v
"""

PLAIN_CI = """\
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: uv sync --extra dev
      - run: uv run pytest tests/ -v
"""

# The RC-2 defect shape: the inverted env sits NEXT TO the (correct) integrity
# skip, exactly as the pre-v2.137.1 template emitted it.
INVERTED_CI = """\
name: CI
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Run plugin validation (remote CPV, --strict)
        env:
          PLUGIN_SKIP_GITHUB_INTEGRITY: '1'
          CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}
        run: uvx --from git+https://github.com/Emasoft/claude-plugins-validation@v2.137.0 cpv-remote-validate plugin . --strict
"""

# The CORRECT local idiom — a SHELL assignment inside a `run:` block. It must
# survive the CIP-1 migration byte-identically.
LOCAL_IDIOM_CI = """\
name: CI
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - name: Validate the way a developer does locally
        run: |
          CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run python scripts/remote_validation.py plugin . --strict
"""


def _plugin(tmp_path: Path, *, ci: str | None = None, dev: list[str] | None = None, **extra: str) -> Path:
    """A minimal plugin tree: manifest + pyproject (+ optional ci.yml, + extra files)."""
    root = tmp_path / "sample-plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "sample-plugin", "version": "0.1.0", "description": "d"}),
        encoding="utf-8",
    )
    pyproject = '[project]\nname = "sample-plugin"\nversion = "0.1.0"\n'
    if dev is not None:
        entries = "".join(f'    "{d}",\n' for d in dev)
        pyproject += f"\n[project.optional-dependencies]\ndev = [\n{entries}]\n"
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    if ci is not None:
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ci.yml").write_text(ci, encoding="utf-8")
    for rel, content in extra.items():
        target = root / rel.replace("__", ".")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _dev(root: Path) -> list[str]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data.get("project", {}).get("optional-dependencies", {}).get("dev", []))


# ═════════════════════════════════════════════════════════════════════════
# TASK 1 — RC-9: the sharded matrix ↔ pytest-split coupling
# ═════════════════════════════════════════════════════════════════════════


def test_rc9_sharded_ci_without_pytest_split_is_REPAIRED(tmp_path: Path) -> None:
    """THE RC-9 DEFECT: a sharded matrix + a dev extra with no pytest-split.

    Deployed symptom (run 28959141245):
        pytest: error: unrecognized arguments: --splits --group
    """
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy"])
    assert "pytest-split" in _canonical_dev_extras_missing(root)

    notes = provision_dev_extra(root, dry_run=False)

    assert notes, "the defect must produce a change note"
    assert PYTEST_SPLIT_REQUIREMENT in _dev(root), _dev(root)
    # The pre-existing entries survive verbatim.
    assert {"pytest", "ruff", "mypy"} <= set(_dev(root))


def test_rc9_UNSHARDED_ci_never_gets_pytest_split_invented(tmp_path: Path) -> None:
    """POSITIVE CONTROL: no `--splits` ⇒ no pytest-split. Never invent a dependency."""
    root = _plugin(tmp_path, ci=PLAIN_CI, dev=["pytest", "ruff", "mypy"])
    before = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert _workflow_runs_sharded_pytest(root) is False
    assert _canonical_dev_extras_missing(root) == []
    assert provision_dev_extra(root, dry_run=False) == []
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


def test_rc9_already_declared_is_left_byte_identical(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a sharded plugin that ALREADY declares it is untouched."""
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy", "pytest-split>=0.9"])
    before = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert _canonical_dev_extras_missing(root) == []
    assert provision_dev_extra(root, dry_run=False) == []
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "pyproject_tail",
    [
        # Declared in ANOTHER extra …
        '\n[project.optional-dependencies]\ndev = ["pytest", "ruff", "mypy"]\ntest = ["pytest-split>=0.9"]\n',
        # … or in a PEP-735 dependency-group …
        '\n[project.optional-dependencies]\ndev = ["pytest", "ruff", "mypy"]\n'
        '\n[dependency-groups]\nsharding = ["pytest-split>=0.9"]\n',
        # … or in the main dependencies.
        '\n[project.optional-dependencies]\ndev = ["pytest", "ruff", "mypy"]\n',
    ],
)
def test_rc9_declared_on_any_dependency_surface_is_not_duplicated(tmp_path: Path, pyproject_tail: str) -> None:
    """POSITIVE CONTROL: pytest-split declared ANYWHERE a `uv sync` installs it ⇒ no duplicate.

    The third case declares it in ``[project].dependencies``, patched in below.
    """
    root = _plugin(tmp_path, ci=SHARDED_CI)
    text = '[project]\nname = "sample-plugin"\nversion = "0.1.0"\n'
    if "dependency-groups" not in pyproject_tail and "test = " not in pyproject_tail:
        text += 'dependencies = ["pytest-split>=0.9"]\n'
    (root / "pyproject.toml").write_text(text + pyproject_tail, encoding="utf-8")

    assert _project_declares_pytest_split(root) is True
    assert "pytest-split" not in _canonical_dev_extras_missing(root)
    assert provision_dev_extra(root, dry_run=False) == []


def test_rc9_pytest_splitter_is_a_DIFFERENT_distribution(tmp_path: Path) -> None:
    """FN-safety: `pytest-splitter` must NOT satisfy the `pytest-split` requirement."""
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy", "pytest-splitter>=1.0"])

    assert _project_declares_pytest_split(root) is False
    assert "pytest-split" in _canonical_dev_extras_missing(root)
    provision_dev_extra(root, dry_run=False)
    assert PYTEST_SPLIT_REQUIREMENT in _dev(root)


def test_rc9_underscore_spelling_satisfies_it_pep503(tmp_path: Path) -> None:
    """POSITIVE CONTROL: `pytest_split` IS `pytest-split` (PEP-503) ⇒ no duplicate."""
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy", "pytest_split>=0.9"])

    assert _project_declares_pytest_split(root) is True
    assert provision_dev_extra(root, dry_run=False) == []


def test_rc9_requirement_literal_cannot_desync_from_the_generator() -> None:
    """The provisioned literal IS the generator's — imported, never re-typed."""
    assert _pytest_split_requirement() == PYTEST_SPLIT_REQUIREMENT
    assert "pytest-split" in _PROVISION_DEV_EXTRA


def test_rc9_dry_run_reports_but_never_mutates(tmp_path: Path) -> None:
    """The AUDIT path surfaces the defect and writes nothing."""
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy"])
    before = (root / "pyproject.toml").read_text(encoding="utf-8")

    notes = provision_dev_extra(root, dry_run=True)

    assert notes and any("pytest-split" in n for n in notes)
    assert (root / "pyproject.toml").read_text(encoding="utf-8") == before


def test_rc9_cip8_detector_and_the_migrator_agree(tmp_path: Path) -> None:
    """END TO END: CIP-8 FIRES on the defect and goes SILENT after the migration.

    Detector and migrator share the rule by construction; this proves they agree
    on the same tree (a migrator that does not clear its own detector is broken).
    """
    root = _plugin(tmp_path, ci=SHARDED_CI, dev=["pytest", "ruff", "mypy"])
    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-8"]

    provision_dev_extra(root, dry_run=False)

    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-8"] == []


# ═════════════════════════════════════════════════════════════════════════
# TASK 2 — CIP-1: the inverted CLAUDE_PRIVATE_USERNAMES env (RC-2)
# ═════════════════════════════════════════════════════════════════════════


def test_cip1_inverted_env_is_REMOVED(tmp_path: Path) -> None:
    """THE RC-2 DEFECT: the env is set to the PUBLIC owner ⇒ 22 false CRITICALs."""
    root = _plugin(tmp_path, ci=INVERTED_CI)

    notes = remove_inverted_env(root)

    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert notes, "the defect must produce a change note"
    assert "CLAUDE_PRIVATE_USERNAMES" not in text
    # The correct sibling key SURVIVES — we remove one line, not the env block.
    assert "PLUGIN_SKIP_GITHUB_INTEGRITY: '1'" in text
    # …and the workflow is still valid YAML with the env mapping intact.
    doc = yaml.safe_load(text)
    env = doc["jobs"]["validate"]["steps"][1]["env"]
    assert env == {"PLUGIN_SKIP_GITHUB_INTEGRITY": "1"}


def test_cip1_LOCAL_whoami_idiom_is_left_BYTE_IDENTICAL(tmp_path: Path) -> None:
    """POSITIVE CONTROL (the load-bearing one).

    ``CLAUDE_PRIVATE_USERNAMES="$(whoami)"`` is the CORRECT local scan idiom — a
    SHELL assignment, not a YAML mapping to the repo owner. Stripping it would
    break every local validation run. It must survive untouched.
    """
    root = _plugin(tmp_path, ci=LOCAL_IDIOM_CI)
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert remove_inverted_env(root) == []
    assert (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == before
    assert 'CLAUDE_PRIVATE_USERNAMES="$(whoami)"' in before


def test_cip1_a_clean_workflow_is_never_rewritten(tmp_path: Path) -> None:
    """POSITIVE CONTROL: no inverted env ⇒ byte-identical, no note."""
    root = _plugin(tmp_path, ci=PLAIN_CI)
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert remove_inverted_env(root) == []
    assert (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == before


def test_cip1_a_DIFFERENT_value_is_not_our_business(tmp_path: Path) -> None:
    """POSITIVE CONTROL: only the INVERTED (`github.repository_owner`) form is removed.

    A workflow deliberately listing private usernames from a secret is a valid
    configuration — CPV does not get to delete it.
    """
    ci = INVERTED_CI.replace("${{ github.repository_owner }}", "${{ secrets.PRIVATE_USERNAMES }}")
    root = _plugin(tmp_path, ci=ci)
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert remove_inverted_env(root) == []
    assert (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == before


def test_cip1_a_childless_env_block_is_removed_too() -> None:
    """A fix must not trade a validation failure for a SYNTAX failure.

    When the inverted line is the env block's ONLY key, dropping it alone would
    leave `env:` as a null mapping, which the Actions schema rejects. The opener
    goes with it, and the result still parses.
    """
    text = (
        "jobs:\n"
        "  validate:\n"
        "    steps:\n"
        "      - name: Validate\n"
        "        env:\n"
        "          CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}\n"
        "        run: echo hi\n"
    )
    new_text, removed = _strip_inverted_private_usernames(text)

    assert removed == 1
    assert "env:" not in new_text
    step = yaml.safe_load(new_text)["jobs"]["validate"]["steps"][0]
    assert "env" not in step
    assert step["run"] == "echo hi"


def test_cip1_dry_run_reports_but_never_mutates(tmp_path: Path) -> None:
    root = _plugin(tmp_path, ci=INVERTED_CI)
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    notes = remove_inverted_env(root, dry_run=True)

    assert notes and "[dry-run]" in notes[0]
    assert (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == before


def test_cip1_audit_is_two_sided(tmp_path: Path) -> None:
    """WARN on the defect, PASS on a clean tree — and never mutates either."""
    bad = _plugin(tmp_path / "bad", ci=INVERTED_CI)
    good = _plugin(tmp_path / "good", ci=PLAIN_CI)

    assert [i.status for i in audit_inverted_private_usernames(bad)] == ["WARN"]
    assert [i.status for i in audit_inverted_private_usernames(good)] == ["PASS"]
    assert "CLAUDE_PRIVATE_USERNAMES" in (bad / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"), (
        "the audit path must not mutate"
    )


def test_cip1_detector_and_the_migrator_agree(tmp_path: Path) -> None:
    """END TO END: CIP-1 FIRES on the defect and goes SILENT after the migration."""
    root = _plugin(tmp_path, ci=INVERTED_CI)
    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-1"]

    remove_inverted_env(root)

    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-1"] == []


# ═════════════════════════════════════════════════════════════════════════
# TASK 2 — CIP-5: emit `.jscpd.json` (RC-4) — regression-lock the migration
# ═════════════════════════════════════════════════════════════════════════


def test_cip5_missing_jscpd_config_is_EMITTED(tmp_path: Path) -> None:
    """RC-4: threshold 5 + ignore globs, so local jscpd and CI's Mega-Linter agree."""
    root = _plugin(tmp_path, ci=PLAIN_CI)

    notes = provision_jscpd_config(root, dry_run=False)

    assert notes
    config = json.loads((root / ".jscpd.json").read_text(encoding="utf-8"))
    assert config["threshold"] == 5
    assert config.get("ignore"), "the ignore globs are what make the threshold meetable"


def test_cip5_an_existing_config_is_NEVER_clobbered(tmp_path: Path) -> None:
    """POSITIVE CONTROL: the author's tuned config survives byte-identically."""
    root = _plugin(tmp_path, ci=PLAIN_CI)
    custom = '{\n  "threshold": 12,\n  "ignore": ["**/vendor/**"]\n}\n'
    (root / ".jscpd.json").write_text(custom, encoding="utf-8")

    assert provision_jscpd_config(root, dry_run=False) == []
    assert (root / ".jscpd.json").read_text(encoding="utf-8") == custom


def test_cip5_detector_and_the_migrator_agree(tmp_path: Path) -> None:
    """END TO END: CIP-5 FIRES on the defect and goes SILENT after the migration.

    NOTE the surface: ``_check_jscpd_config`` greps `.github/workflows/*` for the
    `COPYPASTE_JSCPD` token, so the fixture puts it THERE. See the companion test
    below — the CANONICAL plugin carries that token in `.mega-linter.yml`, which
    the detector does not read (an FN handed off to the detector's owner). The
    MIGRATION does not depend on the detector, which is why it repairs both.
    """
    ci = PLAIN_CI + "      - run: echo ENABLE_LINTERS COPYPASTE_JSCPD\n"
    root = _plugin(tmp_path, ci=ci)
    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-5"]

    provision_jscpd_config(root, dry_run=False)

    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-5"] == []


def test_cip5_canonical_megalinter_shape_is_repaired_regardless(tmp_path: Path) -> None:
    """The REAL canonical shape: the token lives ONLY in `.mega-linter.yml`.

    Verified against the live generator — `gen_ci_yml` does NOT contain
    `COPYPASTE_JSCPD`; `gen_mega_linter_yml` does. The migrator is not gated on
    the token (it provisions on any `--fix`), so RC-4 is repaired on a canonical
    plugin even though the CIP-5 detector is blind to that surface.
    """
    root = _plugin(tmp_path, ci=PLAIN_CI)
    (root / ".mega-linter.yml").write_text(
        'ENABLE_LINTERS:\n  - COPYPASTE_JSCPD\nCOPYPASTE_JSCPD_ARGUMENTS: "--threshold 5"\n',
        encoding="utf-8",
    )

    assert provision_jscpd_config(root, dry_run=False)

    config = json.loads((root / ".jscpd.json").read_text(encoding="utf-8"))
    assert config["threshold"] == 5


# ═════════════════════════════════════════════════════════════════════════
# TASK 2 — CIP-6: re-pin a stale CPV ref (RC-7 / RC-8) — regression-lock
# ═════════════════════════════════════════════════════════════════════════


def test_cip6_stale_main_ref_is_REPINNED(tmp_path: Path) -> None:
    """RC-7/RC-8: CPV's default branch is `master`, so `@main` 404s forever."""
    ci = PLAIN_CI + (
        "      - run: uvx --from git+https://github.com/Emasoft/claude-plugins-validation@main "
        "cpv-remote-validate plugin . --strict\n"
    )
    root = _plugin(tmp_path, ci=ci)

    notes = repin_stale_cpv_ref(root, dry_run=False)

    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert notes
    assert "claude-plugins-validation@main" not in text
    assert "claude-plugins-validation@" in text


@pytest.mark.parametrize("ref", ["master", "v2.137.0", "a1b2c3d4e5f6"])
def test_cip6_a_VALID_pin_is_left_byte_identical(tmp_path: Path, ref: str) -> None:
    """POSITIVE CONTROL: master / v<semver> / SHA are resolvable — never rewrite them."""
    ci = PLAIN_CI + (
        f"      - run: uvx --from git+https://github.com/Emasoft/claude-plugins-validation@{ref} "
        "cpv-remote-validate plugin . --strict\n"
    )
    root = _plugin(tmp_path, ci=ci)
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert repin_stale_cpv_ref(root, dry_run=False) == []
    assert (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == before


def test_cip6_only_the_CPV_ref_is_rewritten(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a THIRD-PARTY action pinned `@main` is not ours to touch."""
    ci = PLAIN_CI + (
        "      - uses: some/other-action@main\n"
        "      - run: uvx --from git+https://github.com/Emasoft/claude-plugins-validation@main x\n"
    )
    root = _plugin(tmp_path, ci=ci)

    repin_stale_cpv_ref(root, dry_run=False)

    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "some/other-action@main" in text, "a third-party ref must be left alone"
    assert "claude-plugins-validation@main" not in text


# ═════════════════════════════════════════════════════════════════════════
# RC-1 — the `.commitlintrc.json` migration gap (wave-1 handoff)
# ═════════════════════════════════════════════════════════════════════════

COMMITLINT_CI = PLAIN_CI + (
    "  commitlint:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: wagoid/commitlint-github-action@v6.2.1\n"
)


def test_rc1_commitlint_gate_without_a_config_is_PROVISIONED(tmp_path: Path) -> None:
    """RC-1: with no config the gate falls back to body-max-line-length = 100,
    and EVERY Dependabot PR fails on its machine-generated body."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)

    notes = provision_commitlintrc_config(root, dry_run=False)

    assert notes
    config = json.loads((root / ".commitlintrc.json").read_text(encoding="utf-8"))
    assert config["rules"]["body-max-line-length"] == [0]
    # The GATE IS NOT WEAKENED — config-conventional (type-enum, subject-empty,
    # header-max-length, …) is still extended, so RC-5 still fails CI.
    assert config["extends"] == ["@commitlint/config-conventional"]
    assert list(config["rules"]) == ["body-max-line-length"], "only that one rule is touched"


def test_rc1_NO_commitlint_gate_means_NO_config_invented(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a repo that does not run commitlint gets no config."""
    root = _plugin(tmp_path, ci=PLAIN_CI)

    assert provision_commitlintrc_config(root, dry_run=False) == []
    assert not (root / ".commitlintrc.json").exists()


def test_rc1_an_existing_config_is_AUGMENTED_preserving_the_authors_rules(tmp_path: Path) -> None:
    """The author's own rules survive verbatim; only the missing rule is added."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)
    (root / ".commitlintrc.json").write_text(
        '{\n  "extends": ["@commitlint/config-conventional"],\n'
        '  "rules": {\n    "scope-enum": [2, "always", ["api", "ui"]]\n  }\n}\n',
        encoding="utf-8",
    )

    notes = provision_commitlintrc_config(root, dry_run=False)

    assert notes
    config = json.loads((root / ".commitlintrc.json").read_text(encoding="utf-8"))
    assert config["rules"]["body-max-line-length"] == [0]
    assert config["rules"]["scope-enum"] == [2, "always", ["api", "ui"]], "author rule preserved"


def test_rc1_an_already_correct_config_is_BYTE_IDENTICAL(tmp_path: Path) -> None:
    """POSITIVE CONTROL: already disabled ⇒ nothing to do, nothing written."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)
    good = '{\n  "extends": ["@commitlint/config-conventional"],\n  "rules": {"body-max-line-length": [0]}\n}\n'
    (root / ".commitlintrc.json").write_text(good, encoding="utf-8")

    assert provision_commitlintrc_config(root, dry_run=False) == []
    assert (root / ".commitlintrc.json").read_text(encoding="utf-8") == good


def test_rc1_an_EXPLICIT_author_value_is_never_overwritten(tmp_path: Path) -> None:
    """POSITIVE CONTROL: the author deliberately set a limit — report, never clobber."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)
    authored = '{\n  "rules": {"body-max-line-length": [2, "always", 120]}\n}\n'
    (root / ".commitlintrc.json").write_text(authored, encoding="utf-8")

    notes = provision_commitlintrc_config(root, dry_run=False)

    assert notes and "NOT modified" in notes[0]
    assert (root / ".commitlintrc.json").read_text(encoding="utf-8") == authored


def test_rc1_an_author_owned_config_in_another_form_is_left_alone(tmp_path: Path) -> None:
    """POSITIVE CONTROL: a commitlint.config.js owner keeps it; no second config appears."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)
    (root / "commitlint.config.js").write_text("module.exports = {};\n", encoding="utf-8")

    notes = provision_commitlintrc_config(root, dry_run=False)

    assert notes and "author-owned" in notes[0]
    assert not (root / ".commitlintrc.json").exists()


def test_rc1_dry_run_reports_but_never_mutates(tmp_path: Path) -> None:
    root = _plugin(tmp_path, ci=COMMITLINT_CI)

    notes = provision_commitlintrc_config(root, dry_run=True)

    assert notes
    assert not (root / ".commitlintrc.json").exists()


def test_rc1_audit_is_two_sided(tmp_path: Path) -> None:
    bad = _plugin(tmp_path / "bad", ci=COMMITLINT_CI)
    good = _plugin(tmp_path / "good", ci=COMMITLINT_CI)
    (good / ".commitlintrc.json").write_text('{"rules": {"body-max-line-length": [0]}}\n', encoding="utf-8")

    assert [i.status for i in audit_commitlint_config(bad)] == ["WARN"]
    assert [i.status for i in audit_commitlint_config(good)] == ["PASS"]
    assert not (bad / ".commitlintrc.json").exists(), "the audit path must not mutate"


def test_rc1_detector_and_the_migrator_agree(tmp_path: Path) -> None:
    """END TO END: CIP-7 FIRES on the defect and goes SILENT after the migration."""
    root = _plugin(tmp_path, ci=COMMITLINT_CI)
    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-7"]

    provision_commitlintrc_config(root, dry_run=False)

    assert [f for f in check_ci_parity(root) if f.check_id == "CIP-7"] == []


# ═════════════════════════════════════════════════════════════════════════
# TASK 3 — the CIP range is DERIVED, never hardcoded
# ═════════════════════════════════════════════════════════════════════════


def test_task3_cip_ids_are_derived_from_the_parity_module() -> None:
    """The ids come from cpv_ci_parity_checks itself — CIP-7/8 included."""
    ids = cpv_ci_preflight._cip_check_ids()

    assert ids == list(range(1, len(ids) + 1)), f"expected a contiguous CIP-1..N range, got {ids}"
    assert 7 in ids and 8 in ids, "the range that went stale must now include CIP-7 and CIP-8"


def test_task3_derived_count_matches_the_checks_actually_run() -> None:
    """DRIFT GUARD: the announced count == the number of checks check_ci_parity runs.

    An INDEPENDENT cross-check (it counts the dispatch calls, not the id literals),
    so adding a CIP-9 check without a finding-code — or vice versa — trips here.
    """
    import inspect

    dispatched = inspect.getsource(cpv_ci_parity_checks.check_ci_parity).count("findings.extend(")

    assert len(cpv_ci_preflight._cip_check_ids()) == dispatched


def test_task3_pass_message_states_the_real_range() -> None:
    """The stale "six (CIP-1..6)" string is gone and cannot come back."""
    message = cpv_ci_preflight._cip_all_passed_message()

    assert "CIP-1..8" in message
    assert "8 static CI-parity checks" in message
    assert "CIP-1..6" not in message
    assert "six" not in message.lower()


def test_task3_no_hardcoded_stale_range_survives_in_the_source() -> None:
    """The module must not carry a hardcoded CIP range anywhere."""
    source = (SCRIPTS / "cpv_ci_preflight.py").read_text(encoding="utf-8")

    assert "CIP-1..6" not in source
    assert "six static CI-parity" not in source


def test_task3_message_degrades_gracefully_without_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """No source (a zipped install) ⇒ a count-free message, never a wrong number."""
    monkeypatch.setattr(cpv_ci_preflight, "_cip_check_ids", lambda: [])

    assert cpv_ci_preflight._cip_all_passed_message() == "All static CI-parity checks passed."


# ─────────────────────────────────────────────────────────────────────────
# Helper — keeps the CIP-1 tests readable
# ─────────────────────────────────────────────────────────────────────────


def remove_inverted_env(root: Path, dry_run: bool = False) -> list[str]:
    from standardize_plugin import remove_inverted_private_usernames

    return remove_inverted_private_usernames(root, dry_run=dry_run)
