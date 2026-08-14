"""#207 — RC-TEST-COVERAGE was wrong in BOTH directions, measured on two repos.

The check decided "is this component tested" by looking for its NAME as a
literal string anywhere under `tests/`. That is not the same question, and it
gave the wrong answer twice:

* OVER-CREDIT (the half that costs safety). `commands/maintainer-config-lint.md`
  was counted as tested because `tests/test_skill_contracts.py` listed the
  string `"maintainer-config-lint"` — in a file that never reads `commands/` at
  all (`grep -n commands` on it returns nothing). So a COMMAND was credited by a
  test of the same-named SKILL, and no assertion anywhere failed if that command
  file broke. A repo can reach "0 untested" that way with its entire command
  surface unguarded, which is exactly the failure the check exists to prevent.
  FIX: scope the match to the component's own KIND and PATH. A pure narrowing.

* UNDER-CREDIT (the half that misdirects effort). `tests/test_command_contracts.py`
  names no command literally — it does `COMMANDS_ROOT.glob("*.md")` and
  parametrizes six assertions over all 23 — and scored ZERO. A second repo
  reproduced it independently: 27 skills, all asserted over by one
  `SKILLS_DIR.glob("*/SKILL.md")` loop, 1 named literally, 26 reported untested.
  The glob is the STRONGER shape (a new component is covered the moment it is
  added), so the metric was penalising the better construction — and a real
  commit that replaced a stale 24-name list with a filesystem glob RAISED
  coverage 24 -> 32 skills while moving this advisory 16 -> 29 "untested".
  FIX: credit a test whose source enumerates a component ROOT.

* WORDING. After both halves the check still cannot support "have no
  discoverable test" — it knows whether the suite REFERS to a component, not
  whether anything asserts on it. The message now says the measurable thing
  (the issue's own third suggestion).

Every test is two-sided: each "is now credited" has a sibling proving the credit
did not become a blanket mute, because a change that credited everything would
satisfy the fix half while making the advisory vacuous.

The finding is WARNING-only and MUST stay WARNING-only — pinned at the bottom.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_plugin as vp  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _warning(root: Path) -> str:
    """The single RC-TEST-COVERAGE warning, or '' when the check stayed silent."""
    report = ValidationReport()
    vp.check_test_coverage(root, report)
    hits = [
        r.message
        for r in report.results
        if r.level == "WARNING" and "RC-TEST-COVERAGE" in r.message
    ]
    assert len(hits) <= 1, f"the advisory must emit at most ONE finding, got {hits}"
    return hits[0] if hits else ""


# ---------------------------------------------------------------------------
# HALF 1 — OVER-CREDIT: a bare name owned by another KIND no longer counts
# ---------------------------------------------------------------------------


def test_a_command_is_not_credited_by_a_test_of_the_same_named_skill(tmp_path: Path) -> None:
    """THE REPORTED CASE. The skill test names the skill by path and lists the
    bare name; the command must NOT ride along on that bare name."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/config-lint.md": "# command\n",
            "skills/config-lint/SKILL.md": "# skill\n",
            "tests/test_skill_contracts.py": (
                '"""Contracts for skills/config-lint/SKILL.md."""\n'
                'SKILL_NAMES = ["config-lint"]\n'
            ),
        },
    )
    warning = _warning(root)
    assert "commands/config-lint.md" in warning, warning
    # Positive control in the SAME warning: the skill, which the test really
    # does reference by path, is absent — so this is not passing because the
    # check simply listed everything.
    assert "skills/config-lint/SKILL.md" not in warning, warning


def test_the_command_is_credited_by_a_reference_to_its_own_path(tmp_path: Path) -> None:
    """MUST-STILL-CREDIT control for the narrowing: a genuinely covered repo
    loses nothing. `commands/x` and `commands/x.md` both count."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/config-lint.md": "# command\n",
            "skills/config-lint/SKILL.md": "# skill\n",
            "tests/test_skill_contracts.py": '"""skills/config-lint/SKILL.md"""\n',
            "tests/test_command_contracts.py": '"""commands/config-lint.md"""\n',
        },
    )
    assert _warning(root) == ""


def test_a_skill_is_not_credited_by_a_bare_name_mention(tmp_path: Path) -> None:
    """The mirror direction — a skill named only as a bare string is not
    credited either. The rule is kind-scoped, not commands-specific."""
    root = _tree(
        tmp_path / "plug",
        {
            "skills/alpha/SKILL.md": "# skill\n",
            "tests/test_names.py": 'NAMES = ["alpha"]\n',
        },
    )
    assert "skills/alpha/SKILL.md" in _warning(root)


def test_a_skill_is_credited_by_any_path_under_its_directory(tmp_path: Path) -> None:
    """A test that asserts on `skills/alpha/references/x.md` is a test that
    refers to the skill — the needle is the DIRECTORY, so both spellings work."""
    root = _tree(
        tmp_path / "plug",
        {
            "skills/alpha/SKILL.md": "# skill\n",
            "tests/test_refs.py": 'P = "skills/alpha/references/guide.md"\n',
        },
    )
    assert _warning(root) == ""


def test_a_longer_name_does_not_satisfy_a_shorter_path_needle(tmp_path: Path) -> None:
    """Boundary check. Plain substring matching would let a mention of
    `commands/deploy-all.md` credit `commands/deploy.md`."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/deploy.md": "# command\n",
            "tests/test_other.py": 'P = "commands/deploy-all.md"\n',
        },
    )
    assert "commands/deploy.md" in _warning(root)


def test_the_exact_path_still_matches_after_the_boundary_check(tmp_path: Path) -> None:
    """CONTROL for the boundary: the tightening must not break the real form."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/deploy.md": "# command\n",
            "tests/test_other.py": 'P = "commands/deploy.md"\n',
        },
    )
    assert _warning(root) == ""


def test_a_python_module_is_still_credited_by_its_import_name(tmp_path: Path) -> None:
    """THE DELIBERATE EXCEPTION, pinned. `scripts/*.py` keeps bare-stem matching
    because a module's canonical reference IS its import name — `import
    cpv_fix_ledger`, not the string "scripts/cpv_fix_ledger". Scoping scripts to
    a path form would report every unit-tested module as untested and would
    silently kill the one-hop dispatcher credit built on the same identity."""
    root = _tree(
        tmp_path / "plug",
        {
            "scripts/widget_tool.py": "X = 1\n",
            "tests/test_suite.py": "import widget_tool\n",
        },
    )
    assert _warning(root) == ""


# ---------------------------------------------------------------------------
# HALF 2 — UNDER-CREDIT: a test that ENUMERATES a root covers its members
# ---------------------------------------------------------------------------


_TABLE_DRIVEN_COMMAND_TEST = (
    "from pathlib import Path\n"
    "import pytest\n"
    'COMMANDS_ROOT = Path(__file__).resolve().parents[1] / "commands"\n'
    'COMMANDS = sorted(p.name for p in COMMANDS_ROOT.glob("*.md"))\n'
    '@pytest.mark.parametrize("name", COMMANDS)\n'
    "def test_frontmatter_parses(name):\n"
    "    assert name.endswith('.md')\n"
)


def test_a_glob_over_the_command_root_credits_every_command(tmp_path: Path) -> None:
    """THE REPORTED UNDER-CREDIT. Three commands, none named literally, all
    covered by one table-driven contract test."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/one.md": "# a\n",
            "commands/two.md": "# b\n",
            "commands/three.md": "# c\n",
            "tests/test_command_contracts.py": _TABLE_DRIVEN_COMMAND_TEST,
        },
    )
    assert _warning(root) == ""


def test_a_glob_over_an_unrelated_directory_credits_nothing(tmp_path: Path) -> None:
    """MUST-STILL-FIRE control: the credit is keyed on WHICH root is enumerated.
    Without this, "any glob anywhere" would mute the advisory entirely."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/one.md": "# a\n",
            "commands/two.md": "# b\n",
            "tests/test_docs.py": (
                "from pathlib import Path\n"
                'DOCS_ROOT = Path(__file__).resolve().parents[1] / "references"\n'
                'DOCS = sorted(p.name for p in DOCS_ROOT.glob("*.md"))\n'
            ),
        },
    )
    warning = _warning(root)
    assert "commands/one.md" in warning and "commands/two.md" in warning, warning


def test_a_skills_walk_by_pattern_credits_every_skill(tmp_path: Path) -> None:
    """The second repo's shape: `.glob("*/SKILL.md")` is unambiguously an
    enumeration of skills whatever the receiver happens to be called."""
    root = _tree(
        tmp_path / "plug",
        {
            "skills/one/SKILL.md": "# a\n",
            "skills/two/SKILL.md": "# b\n",
            "tests/test_skills.py": (
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                'SHIPPED = sorted(p.parent.name for p in ROOT.glob("*/SKILL.md"))\n'
            ),
        },
    )
    assert _warning(root) == ""


def test_an_inline_root_expression_glob_is_credited(tmp_path: Path) -> None:
    """`(root / "agents").glob("*.md")` — the root named inline, no variable."""
    root = _tree(
        tmp_path / "plug",
        {
            "agents/one.md": "# a\n",
            "agents/two.md": "# b\n",
            "tests/test_agents.py": (
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                'AGENTS = sorted((ROOT / "agents").glob("*.md"))\n'
            ),
        },
    )
    assert _warning(root) == ""


def test_a_root_named_only_in_the_glob_pattern_is_credited(tmp_path: Path) -> None:
    """`.glob("commands/*.md")` — the root inside the PATTERN rather than the
    receiver."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/one.md": "# a\n",
            "tests/test_cmds.py": (
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                'CMDS = sorted(ROOT.glob("commands/*.md"))\n'
            ),
        },
    )
    assert _warning(root) == ""


def test_a_variable_pointing_below_a_root_is_not_a_root_enumeration(tmp_path: Path) -> None:
    """PRECISION control, and it is not hypothetical: on CPV's own suite a
    looser rule credited three files with enumerating a root they only reach
    THROUGH — a per-skill `references/` dir, a per-skill menu dir, and a path
    inside a string literal. Only the LAST path segment decides."""
    root = _tree(
        tmp_path / "plug",
        {
            "skills/one/SKILL.md": "# a\n",
            "skills/two/SKILL.md": "# b\n",
            "tests/test_menus.py": (
                "from pathlib import Path\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                'MENUS = ROOT / "skills" / "one" / "menus"\n'
                'SPECS = sorted(MENUS.glob("*.json"))\n'
            ),
        },
    )
    warning = _warning(root)
    assert "skills/two/SKILL.md" in warning, warning


def test_globbed_roots_helper_is_two_sided(tmp_path: Path) -> None:
    """Unit level, both directions, so a regression is localised to the helper."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)
    globbing = tests_dir / "test_a.py"
    globbing.write_text(_TABLE_DRIVEN_COMMAND_TEST, encoding="utf-8")
    plain = tests_dir / "test_b.py"
    plain.write_text("def test_x():\n    assert True\n", encoding="utf-8")

    roots, complete = vp._coverage_globbed_roots([globbing])
    assert roots == {"commands"}
    assert complete is True

    roots_plain, complete_plain = vp._coverage_globbed_roots([plain])
    assert roots_plain == set()
    assert complete_plain is True


# ---------------------------------------------------------------------------
# WORDING — say only what the method supports
# ---------------------------------------------------------------------------


def test_the_message_states_what_it_actually_measures(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "plug",
        {
            "commands/lonely.md": "# a\n",
            "tests/test_nothing.py": "def test_x():\n    assert True\n",
        },
    )
    warning = _warning(root)
    assert "no discoverable test" not in warning, (
        "the check cannot support a claim about TESTING — only about REFERENCE"
    )
    assert "are not referenced by any test" in warning, warning
    assert "enumerates the directory" in warning, warning
    assert "does not block the publish" in warning, warning


# ---------------------------------------------------------------------------
# WARNING-ONLY — must never block, in either direction
# ---------------------------------------------------------------------------


def test_the_finding_never_blocks_strict(tmp_path: Path) -> None:
    """LOAD-BEARING. #207 is a report of an advisory being WRONG; an advisory
    nobody can be blocked by is one that can be corrected without breaking
    anyone's publish. Both halves of this fix keep that true."""
    root = _tree(
        tmp_path / "plug",
        {
            "commands/lonely.md": "# a\n",
            "tests/test_nothing.py": "def test_x():\n    assert True\n",
        },
    )
    report = ValidationReport()
    vp.check_test_coverage(root, report)
    assert report.has_warning is True, "precondition: the advisory must have fired"
    assert report.exit_code == 0
    assert report.exit_code_strict() == 0
    assert report.has_critical is False
    assert report.has_major is False
    assert report.has_minor is False
    assert report.has_nit is False
