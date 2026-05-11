#!/usr/bin/env python3
"""Static-analysis audit of CPV creation/migration agents vs spec rules.

Why this exists (TRDD-b4c6cbe7 §4.4):
    The user's escalation: "agents are missing huge surface areas both
    in creation and validation phases." We need to know — for each
    in-scope agent — what spec topics it explicitly addresses vs what
    it leaves implicit (and therefore likely to produce CLI-invalid
    output on the first try).

What this is NOT:
    - This is NOT a runtime test of an agent (the kraken workflow says
      "you can't actually launch a sub-agent"). It is a static-analysis
      pass that reads each agent's markdown body + the skill bodies
      it loads, and scores which spec topics are mentioned.
    - "Mentioned" is a much weaker signal than "implemented" — a row
      tagged `mentions: yes` may still produce broken output. But a
      row tagged `mentions: no` is a near-certain gap.

In-scope agents (per TRDD §4.4):
    1. plugin-creator  (agents/plugin-creator.md)
    2. plugin-fixer    (agents/plugin-fixer.md)
    3. marketplace-fixer (agents/marketplace-fixer.md)
    4. cpv-upgrade-plugin  (commands/cpv-upgrade-plugin.md — slash command)
    5. cpv-migrate-marketplace (commands/cpv-migrate-marketplace.md — slash command)
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Agent registry — paths are repo-relative
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTarget:
    """One in-scope agent or slash command."""

    label: str  # "plugin-creator"
    kind: str  # "agent" | "command"
    body_path: str  # repo-relative path to the .md
    notes: str = ""  # free-form description of role


AGENT_TARGETS: tuple[AgentTarget, ...] = (
    AgentTarget(
        label="plugin-creator",
        kind="agent",
        body_path="agents/plugin-creator.md",
        notes="Scaffolds new plugin or marketplace repos from scratch.",
    ),
    AgentTarget(
        label="plugin-fixer",
        kind="agent",
        body_path="agents/plugin-fixer.md",
        notes="Applies fix recipes from a validation report.",
    ),
    AgentTarget(
        label="marketplace-fixer",
        kind="agent",
        body_path="agents/marketplace-fixer.md",
        notes="Applies fix recipes against a marketplace.json.",
    ),
    AgentTarget(
        label="cpv-upgrade-plugin",
        kind="command",
        body_path="commands/cpv-upgrade-plugin.md",
        notes="Upgrades an existing plugin to current CPV pipeline standards.",
    ),
    AgentTarget(
        label="cpv-migrate-marketplace",
        kind="command",
        body_path="commands/cpv-migrate-marketplace.md",
        notes="Normalises an existing marketplace.json (source.url -> source.repo, etc.).",
    ),
)


# ---------------------------------------------------------------------------
# Spec topics — the surface CPV must teach its agents to handle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecTopic:
    """One spec-level concern an agent must address (or it produces broken output)."""

    topic: str  # short label used as table column
    keywords: tuple[str, ...]  # regex-or'd keyword set checked against body
    severity: str  # "CRITICAL" | "MAJOR" | "MINOR"
    rationale: str  # why this topic matters

    def matches(self, body: str) -> bool:
        """True iff any keyword (case-insensitive substring) appears in `body`."""
        low = body.lower()
        return any(k.lower() in low for k in self.keywords)


SPEC_TOPICS: tuple[SpecTopic, ...] = (
    # ── Core manifest fields (every plugin needs these) ────────────────────
    SpecTopic(
        topic="plugin-name-kebab",
        keywords=("kebab", "kebab-case", "name regex", '"name"'),
        severity="CRITICAL",
        rationale="plugin.json.name must be kebab-case; CLI rejects underscores/CamelCase",
    ),
    SpecTopic(
        topic="plugin-version-semver",
        keywords=("semver", "version", "X.Y.Z"),
        severity="CRITICAL",
        rationale="plugin.json.version must be valid semver; CLI rejects free strings",
    ),
    SpecTopic(
        topic="unknown-root-keys",
        keywords=("unrecognized key", "unknown root", "extra key", "schema strict"),
        severity="CRITICAL",
        rationale="CLI rejects unknown top-level keys in plugin.json (e.g. our own `cpv`)",
    ),
    SpecTopic(
        topic="author-object-shape",
        keywords=('"author"', "author object", "{name", "author = {"),
        severity="MAJOR",
        rationale="CLI requires author as object with name/email/url, not bare string",
    ),
    # ── Hooks ──────────────────────────────────────────────────────────────
    SpecTopic(
        topic="hooks-event-coverage",
        keywords=("PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit"),
        severity="MAJOR",
        rationale="Hook events differ per Claude Code version; agent must use spec-aligned names",
    ),
    SpecTopic(
        topic="hooks-type-coverage",
        keywords=("hook type", "mcp_tool", "type: command", "type: prompt", "type: http"),
        severity="MAJOR",
        rationale="5 hook types as of v2.1.118 — agent must scope command/prompt/http/mcp_tool/agent correctly",
    ),
    SpecTopic(
        topic="hook-script-resolve",
        keywords=("hook script", "${CLAUDE_PLUGIN_ROOT}", "hook path"),
        severity="MAJOR",
        rationale="Hook command paths must resolve; ${CLAUDE_PLUGIN_ROOT} required for portability",
    ),
    # ── Skills ────────────────────────────────────────────────────────────
    SpecTopic(
        topic="skill-frontmatter-fields",
        keywords=("name:", "description:", "user-invocable", "allowed-tools"),
        severity="MAJOR",
        rationale="Skill frontmatter has 15 valid fields; agents typically miss `arguments` + `paths`",
    ),
    SpecTopic(
        topic="skill-argument-substitution",
        keywords=("$<", "$ARGUMENTS", "argument-hint", "arguments:"),
        severity="MAJOR",
        rationale="`$<name>` substitution requires matching `arguments:` declaration (v2.1.121)",
    ),
    SpecTopic(
        topic="skill-paths-field",
        keywords=("paths:", "paths field", "skill paths"),
        severity="MINOR",
        rationale="`paths:` field declares bundled resources; often omitted by agents",
    ),
    # ── Agents (the meta case — agents emitting agents) ───────────────────
    SpecTopic(
        topic="agent-frontmatter",
        keywords=("agent frontmatter", "agent yaml", "model: sonnet", "model: opus"),
        severity="MAJOR",
        rationale="Agent frontmatter has 15+ fields; agents miss `permissionMode`, `effort`",
    ),
    SpecTopic(
        topic="agent-color-named",
        keywords=("color: red", "color: blue", "named color", "agent color"),
        severity="MINOR",
        rationale="Agent `color:` should be one of 8 named colors; hex codes NIT but accepted",
    ),
    # ── MCP / LSP servers ─────────────────────────────────────────────────
    SpecTopic(
        topic="mcpServers-schema",
        keywords=("mcpServers", "mcp server", "mcp.json", "stdio", "url-based"),
        severity="MAJOR",
        rationale="MCP server schema has per-server fields (command, args, url, env, headersHelper)",
    ),
    SpecTopic(
        topic="lspServers-schema",
        keywords=("lspServers", "lsp server", "lsp.json"),
        severity="MINOR",
        rationale="LSP server shape often missed entirely — most agents don't mention it",
    ),
    # ── Marketplace specifics ─────────────────────────────────────────────
    SpecTopic(
        topic="marketplace-source-types",
        keywords=("source: github", "source: url", "source: git-subdir", "source: npm", "source: relative-path"),
        severity="MAJOR",
        rationale="6 valid source types — agent must scope each one correctly per layout",
    ),
    SpecTopic(
        topic="marketplace-source-shape-mismatch",
        keywords=("source.url", "source.repo", "url → repo", "url -> repo"),
        severity="MAJOR",
        rationale="Older marketplaces use source.url; canonical is source.repo for type=github",
    ),
    SpecTopic(
        topic="marketplace-name-equals-plugin",
        keywords=("plugin name must equal", "name must match", "name parity", "names disagree"),
        severity="CRITICAL",
        rationale="marketplace.json.plugins[].name MUST equal upstream plugin.json.name",
    ),
    # ── Layout-specific ───────────────────────────────────────────────────
    SpecTopic(
        topic="layout-a-b-c-awareness",
        keywords=("layout a", "layout b", "layout c", "marketplace-in-plugin", "hub and spoke"),
        severity="MAJOR",
        rationale="Three valid layouts (A/B/C); agent must scope marketplace.json placement correctly",
    ),
    # ── Repo hygiene ──────────────────────────────────────────────────────
    SpecTopic(
        topic="gitignore-defaults",
        keywords=(".gitignore", "gitignore defaults", "/reports/", "/reports_dev/"),
        severity="MINOR",
        rationale=".gitignore must exclude /reports/ + /reports_dev/ per agent-reports-location rule",
    ),
    SpecTopic(
        topic="env-example-no-secrets",
        keywords=("env.example", "no secrets", "redact secrets", "secret scan"),
        severity="MAJOR",
        rationale=".env.example must not embed real values; agents often paste placeholder API keys",
    ),
    SpecTopic(
        topic="license-presence",
        keywords=("LICENSE", "license file", "SPDX", "MIT", "Apache"),
        severity="MINOR",
        rationale="LICENSE file with recognised SPDX identifier expected by marketplace hosts",
    ),
    SpecTopic(
        topic="readme-install-command",
        keywords=("/plugin install", "install command", "canonical name"),
        severity="MINOR",
        rationale="README install command must use canonical plugin name from plugin.json",
    ),
    # ── Pipeline ─────────────────────────────────────────────────────────
    SpecTopic(
        topic="publish-py-idempotent",
        keywords=("publish.py", "idempotent publish", "_local_tag_exists"),
        severity="MAJOR",
        rationale="publish.py must be idempotent — re-runs MUST NOT double-tag or republish",
    ),
    SpecTopic(
        topic="cross-platform-paths",
        keywords=("pathlib", "Path(", "cross-platform", "os.path", "subprocess.run"),
        severity="MINOR",
        rationale="All shipped scripts must use pathlib + subprocess.run, not bash globs",
    ),
)


# ---------------------------------------------------------------------------
# Skill resolution (agents load skills; we want to score the union)
# ---------------------------------------------------------------------------


_SKILLS_BLOCK_RE = re.compile(r"^skills:\s*\n((?:\s*-\s+[\w-]+\s*\n)+)", re.MULTILINE)
_SKILL_LINE_RE = re.compile(r"^\s*-\s+([\w-]+)\s*$", re.MULTILINE)


def _agent_skill_names(body: str) -> list[str]:
    """Pull the `skills: - foo - bar` block out of agent frontmatter."""
    m = _SKILLS_BLOCK_RE.search(body)
    if m is None:
        return []
    block = m.group(1)
    return _SKILL_LINE_RE.findall(block)


def _skill_body_text(skill_name: str, repo_root: Path) -> str:
    """Best-effort read of a skill's body content for keyword scoring.

    Skill layout is one of:
        skills/<name>/SKILL.md
        skills/<name>.md
    """
    candidates = (
        repo_root / "skills" / skill_name / "SKILL.md",
        repo_root / "skills" / f"{skill_name}.md",
    )
    for cand in candidates:
        if cand.is_file():
            try:
                return cand.read_text(encoding="utf-8")
            except OSError:
                return ""
    return ""


def _full_agent_surface(target: AgentTarget, repo_root: Path) -> tuple[str, list[str]]:
    """Concatenate the agent body + every loaded skill body.

    Returns (full_text, missing_skill_names). A keyword-match against
    `full_text` therefore reflects the *effective* surface of the agent,
    including what its skills teach it.
    """
    body_path = repo_root / target.body_path
    if not body_path.is_file():
        return "", []
    body = body_path.read_text(encoding="utf-8")
    skills = _agent_skill_names(body)
    parts = [body]
    missing: list[str] = []
    for skill in skills:
        text = _skill_body_text(skill, repo_root)
        if not text:
            missing.append(skill)
            continue
        parts.append(text)
    return "\n\n---\n\n".join(parts), missing


# ---------------------------------------------------------------------------
# Audit run
# ---------------------------------------------------------------------------


@dataclass
class AgentAuditRow:
    """One agent × one topic = one cell of the audit matrix."""

    agent: str
    topic: SpecTopic
    mentioned: bool


@dataclass
class AgentReport:
    """All findings for a single agent."""

    target: AgentTarget
    rows: list[AgentAuditRow] = field(default_factory=list)
    missing_skills: list[str] = field(default_factory=list)
    body_missing: bool = False


def audit_agents(repo_root: Path) -> list[AgentReport]:
    """Run the static audit across every agent in AGENT_TARGETS."""
    reports: list[AgentReport] = []
    for target in AGENT_TARGETS:
        body_path = repo_root / target.body_path
        if not body_path.is_file():
            reports.append(AgentReport(target=target, body_missing=True))
            continue
        full_text, missing = _full_agent_surface(target, repo_root)
        rows = [
            AgentAuditRow(agent=target.label, topic=topic, mentioned=topic.matches(full_text)) for topic in SPEC_TOPICS
        ]
        reports.append(AgentReport(target=target, rows=rows, missing_skills=missing))
    return reports


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def write_agent_emission_report(reports: list[AgentReport], path: Path) -> None:
    """Write the agent-emission audit markdown."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Agent-Emission Audit — Static surface analysis\n")
    lines.append("**TRDD:** b4c6cbe7\n**Generated:** 2026-05-11\n")
    lines.append(
        "\n> This is a STATIC analysis. A row tagged `yes` means the agent "
        "(or one of its loaded skills) at least *mentions* the topic. It does "
        "NOT prove the agent emits spec-correct output. A row tagged `no` is "
        "a near-certain gap — the agent has no explicit guidance on the topic.\n"
    )

    lines.append("## 1. Per-agent missing-skill audit\n")
    lines.append("| Agent | Body present? | Skills missing (cannot resolve) |")
    lines.append("|---|:---:|---|")
    for r in reports:
        body_present = "no" if r.body_missing else "yes"
        missing = ", ".join(f"`{s}`" for s in r.missing_skills) or "_none_"
        lines.append(f"| `{r.target.label}` ({r.target.kind}) | {body_present} | {missing} |")
    lines.append("")

    lines.append("## 2. Coverage matrix (agent × topic)\n")
    header = "| Topic | Severity | " + " | ".join(f"`{r.target.label}`" for r in reports) + " |"
    sep = "|---|---|" + "---|" * len(reports)
    lines.append(header)
    lines.append(sep)

    # Index rows by topic for column-major display
    by_topic: dict[str, list[bool]] = {}
    for topic in SPEC_TOPICS:
        by_topic[topic.topic] = []
        for r in reports:
            if r.body_missing:
                by_topic[topic.topic].append(False)
                continue
            row = next(x for x in r.rows if x.topic.topic == topic.topic)
            by_topic[topic.topic].append(row.mentioned)

    for topic in SPEC_TOPICS:
        marks = " | ".join("yes" if m else "**no**" for m in by_topic[topic.topic])
        lines.append(f"| `{topic.topic}` | `{topic.severity}` | {marks} |")
    lines.append("")

    lines.append("## 3. Gap roll-up per agent\n")
    for r in reports:
        if r.body_missing:
            lines.append(f"### `{r.target.label}` — body missing\n")
            lines.append(f"_File `{r.target.body_path}` not found in repo._\n")
            continue
        gaps = [row for row in r.rows if not row.mentioned]
        crit = [row for row in gaps if row.topic.severity == "CRITICAL"]
        major = [row for row in gaps if row.topic.severity == "MAJOR"]
        minor = [row for row in gaps if row.topic.severity == "MINOR"]
        lines.append(f"### `{r.target.label}` ({r.target.kind})\n")
        lines.append(f"_{r.target.notes}_\n")
        lines.append(f"**Gap counts:** CRITICAL={len(crit)} MAJOR={len(major)} MINOR={len(minor)}\n")
        if crit:
            lines.append("\n**Critical gaps (highest priority for child TRDDs):**")
            for row in crit:
                lines.append(f"- `{row.topic.topic}` — {row.topic.rationale}")
        if major:
            lines.append("\n**Major gaps:**")
            for row in major:
                lines.append(f"- `{row.topic.topic}` — {row.topic.rationale}")
        if minor:
            lines.append("\n**Minor gaps:**")
            for row in minor:
                lines.append(f"- `{row.topic.topic}` — {row.topic.rationale}")
        lines.append("")

    lines.append("## 4. Cross-agent gap leaderboard\n")
    lines.append(
        "Topics whose `no` count is highest are the most under-covered across "
        "the agent fleet — they are the strongest child-TRDD candidates.\n"
    )
    lines.append("| Topic | Severity | `no` count | Agents missing it |")
    lines.append("|---|---|---:|---|")
    leaderboard: list[tuple[SpecTopic, int, list[str]]] = []
    for topic in SPEC_TOPICS:
        no_agents: list[str] = []
        for r in reports:
            if r.body_missing:
                no_agents.append(f"{r.target.label}(no-body)")
                continue
            row = next(x for x in r.rows if x.topic.topic == topic.topic)
            if not row.mentioned:
                no_agents.append(r.target.label)
        leaderboard.append((topic, len(no_agents), no_agents))
    # Sort by severity weight then count, desc
    sev_weight = {"CRITICAL": 3, "MAJOR": 2, "MINOR": 1}
    leaderboard.sort(key=lambda x: (sev_weight.get(x[0].severity, 0), x[1]), reverse=True)
    for topic, count, agents in leaderboard:
        lines.append(f"| `{topic.topic}` | `{topic.severity}` | {count} | {', '.join(agents) or '_none_'} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root_default = Path(__file__).resolve().parents[2]
    default_report = repo_root_default / "design" / "audits" / "agent-emission-2026-05-11.md"
    parser.add_argument("--repo-root", type=Path, default=repo_root_default)
    parser.add_argument("--report", type=Path, default=default_report)
    args = parser.parse_args(argv)

    reports = audit_agents(args.repo_root)
    write_agent_emission_report(reports, args.report)
    print(f"Wrote agent-emission report → {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
