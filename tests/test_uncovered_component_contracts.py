#!/usr/bin/env python3
"""Contract tests for eight shipped components that had no test of their own.

Flagged by CPV's own RC-TEST-COVERAGE advisory. Each component gets exactly two
tests: (1) the shipped file passes CPV's OWN validator with zero CRITICAL and
zero MAJOR, and (2) one component-specific claim that a future edit could
plausibly break — a cross-file contract, a documented constant set, or a
reference link that must resolve on disk.

Components covered:
- skills/cpv-cache-validation-skill/SKILL.md
- skills/cpv-devitalize-threats/SKILL.md
- skills/cpv-diagnose-plugin-architecture/SKILL.md
- skills/cpv-migrate-marketplace-architecture/SKILL.md
- skills/cpv-skill-validation-skill/SKILL.md
- skills/verification-before-completion/SKILL.md
- agents/cpv-plugin-diagnoser-agent.md
- agents/cpv-spark-agent.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_diagnose_architecture  # noqa: E402
import cpv_validation_common  # noqa: E402
import remote_validation  # noqa: E402
from convert_agent import COMPANION_SKILL_NAME  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_agent import parse_frontmatter as parse_agent_frontmatter  # noqa: E402
from validate_agent import validate_agent  # noqa: E402
from validate_skill import parse_frontmatter as parse_skill_frontmatter  # noqa: E402
from validate_skill import validate_skill  # noqa: E402

SKILLS_DIR = REPO_ROOT / "skills"
AGENTS_DIR = REPO_ROOT / "agents"

# Markdown inline links pointing into the skill's own references/ folder.
_REF_LINK_RE = re.compile(r"\]\((references/[^)\s#]+)")


def _blocking(report: ValidationReport) -> list[str]:
    """Return the CRITICAL + MAJOR messages of a report, guarding against a vacuous pass."""
    assert report.results, "validator produced no results at all — the assertion below would pass vacuously"
    return [f"{r.level}: {r.message}" for r in report.results if r.level in ("CRITICAL", "MAJOR")]


def _skill_frontmatter(skill_dir: Path) -> dict:
    """Parse and return the frontmatter mapping of a shipped SKILL.md."""
    fm, _body, _end = parse_skill_frontmatter((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
    assert fm is not None, f"{skill_dir.name}/SKILL.md has no parseable frontmatter"
    return fm


def _agent_frontmatter(agent_path: Path) -> dict:
    """Parse and return the frontmatter mapping of a shipped agent .md file."""
    fm, _body, _end = parse_agent_frontmatter(agent_path.read_text(encoding="utf-8"))
    assert fm is not None, f"{agent_path.name} has no parseable frontmatter"
    return fm


class TestCacheValidationSkill:
    """skills/cpv-cache-validation-skill/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped cache-validation skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "cpv-cache-validation-skill")
        assert _blocking(report) == []

    def test_documented_validator_and_launcher_alias_both_exist(self):
        """The skill's documented `cache` launcher alias resolves to the validator script it names."""
        body = (SKILLS_DIR / "cpv-cache-validation-skill" / "SKILL.md").read_text(encoding="utf-8")
        assert "scripts/validate_cache.py" in body, "skill no longer names its validator script"
        assert (SCRIPTS_DIR / "validate_cache.py").is_file()
        assert remote_validation._ALIASES.get("cache") == "validate_cache"


class TestDevitalizeThreatsSkill:
    """skills/cpv-devitalize-threats/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped devitalize-threats skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "cpv-devitalize-threats")
        assert _blocking(report) == []

    def test_every_advertised_transform_has_a_recipe_in_the_catalog(self):
        """Each T1-T9 row the skill advertises has a matching `## T<n>` recipe heading in the catalog reference."""
        skill_dir = SKILLS_DIR / "cpv-devitalize-threats"
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        advertised = sorted({m for m in re.findall(r"^\| (T[1-9]) \|", body, re.MULTILINE)})
        assert advertised == [f"T{n}" for n in range(1, 10)], f"skill advertises {advertised}, expected T1..T9"

        catalog = (skill_dir / "references" / "transform-catalog.md").read_text(encoding="utf-8")
        missing = [t for t in advertised if not re.search(rf"^## {t} ", catalog, re.MULTILINE)]
        assert missing == [], f"advertised transforms with no recipe heading: {missing}"


class TestDiagnosePluginArchitectureSkill:
    """skills/cpv-diagnose-plugin-architecture/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped diagnose-plugin-architecture skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "cpv-diagnose-plugin-architecture")
        assert _blocking(report) == []

    def test_documented_categories_match_the_engine_constants(self):
        """The six JSON `category` values the skill documents equal the engine's CAT_* constant set."""
        body = (SKILLS_DIR / "cpv-diagnose-plugin-architecture" / "SKILL.md").read_text(encoding="utf-8")
        documented = {m for m in re.findall(r"`(RUNTIME_ESSENTIAL|BUILD_SOURCE|RUNTIME_DEP|DEV_ONLY|BUILD_CACHE|UNKNOWN)`", body)}
        engine = {v for k, v in vars(cpv_diagnose_architecture).items() if k.startswith("CAT_") and isinstance(v, str)}
        assert engine, "engine exposes no CAT_* constants — assertion would be vacuous"
        assert documented == engine, f"skill documents {sorted(documented)}, engine defines {sorted(engine)}"


class TestMigrateMarketplaceArchitectureSkill:
    """skills/cpv-migrate-marketplace-architecture/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped migrate-marketplace-architecture skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "cpv-migrate-marketplace-architecture")
        assert _blocking(report) == []

    def test_every_linked_reference_file_exists_on_disk(self):
        """Every `references/...` link in the skill body resolves to a real file."""
        skill_dir = SKILLS_DIR / "cpv-migrate-marketplace-architecture"
        links = sorted(set(_REF_LINK_RE.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8"))))
        assert len(links) >= 5, f"expected the 5 per-layout references to be linked, found {links}"
        missing = [link for link in links if not (skill_dir / link).is_file()]
        assert missing == [], f"dangling reference links: {missing}"


class TestSkillValidationSkill:
    """skills/cpv-skill-validation-skill/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped skill-validation skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "cpv-skill-validation-skill")
        assert _blocking(report) == []

    def test_documented_exit_codes_match_the_shared_constants(self):
        """The skill's documented exit-code table matches cpv_validation_common's EXIT_* constants."""
        body = (SKILLS_DIR / "cpv-skill-validation-skill" / "SKILL.md").read_text(encoding="utf-8")
        documented = dict(re.findall(r"(\d) \((pass|CRITICAL|MAJOR|MINOR|NIT)\)", body))
        expected = {
            str(cpv_validation_common.EXIT_OK): "pass",
            str(cpv_validation_common.EXIT_CRITICAL): "CRITICAL",
            str(cpv_validation_common.EXIT_MAJOR): "MAJOR",
            str(cpv_validation_common.EXIT_MINOR): "MINOR",
            str(cpv_validation_common.EXIT_NIT): "NIT",
        }
        assert documented == expected, f"skill documents {documented}, constants say {expected}"


class TestVerificationBeforeCompletionSkill:
    """skills/verification-before-completion/SKILL.md."""

    def test_shipped_skill_has_no_blocking_findings(self):
        """The shipped verification-before-completion skill validates with zero CRITICAL and zero MAJOR."""
        report = validate_skill(SKILLS_DIR / "verification-before-completion")
        assert _blocking(report) == []

    def test_name_is_the_companion_skill_convert_agent_attaches(self):
        """Its frontmatter name equals convert_agent's COMPANION_SKILL_NAME, as the description claims."""
        skill_dir = SKILLS_DIR / "verification-before-completion"
        fm = _skill_frontmatter(skill_dir)
        assert fm["name"] == skill_dir.name
        assert fm["name"] == COMPANION_SKILL_NAME
        assert "convert_agent.py" in fm["description"], "description no longer states the convert_agent.py contract"


class TestPluginDiagnoserAgent:
    """agents/cpv-plugin-diagnoser-agent.md."""

    def test_shipped_agent_has_no_blocking_findings(self):
        """The shipped plugin-diagnoser agent validates with zero CRITICAL and zero MAJOR."""
        report = validate_agent(AGENTS_DIR / "cpv-plugin-diagnoser-agent.md")
        assert _blocking(report) == []

    def test_every_preloaded_skill_resolves_to_a_shipped_skill(self):
        """Each name in its `skills:` frontmatter has a real skills/<name>/SKILL.md in this plugin."""
        fm = _agent_frontmatter(AGENTS_DIR / "cpv-plugin-diagnoser-agent.md")
        preloads = fm.get("skills") or []
        assert preloads, "agent no longer preloads any skill — assertion would be vacuous"
        missing = [name for name in preloads if not (SKILLS_DIR / name / "SKILL.md").is_file()]
        assert missing == [], f"preloaded skills that do not exist: {missing}"


class TestSparkAgent:
    """agents/cpv-spark-agent.md."""

    def test_shipped_agent_has_no_blocking_findings(self):
        """The shipped cpv-spark agent validates with zero CRITICAL and zero MAJOR."""
        report = validate_agent(AGENTS_DIR / "cpv-spark-agent.md")
        assert _blocking(report) == []

    def test_carries_no_model_pin_as_its_own_description_asserts(self):
        """It ships no `model:` frontmatter key, the CA-04 cache-warmth invariant its description states."""
        fm = _agent_frontmatter(AGENTS_DIR / "cpv-spark-agent.md")
        assert "model" not in fm, f"cpv-spark-agent pins model={fm.get('model')!r}, breaking the CA-04 invariant"
        assert "No `model:` pin" in fm["description"], "description no longer states the CA-04 invariant"
