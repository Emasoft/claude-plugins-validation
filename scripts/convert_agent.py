#!/usr/bin/env python3
"""Convert ONE source agent into any of the three agent architectures
(TRDD-XUNZQ70I, ``design/specs/agent-closure-and-variants.md`` §1.1, §1.2, §5).

    convert_agent.py <agent.md> --to all-in-one   [--out DIR] [--name NAME] [--force]
    convert_agent.py <agent.md> --to one-for-all  [--out DIR] [--name NAME] [--force]
    convert_agent.py <agent.md> --to plugin-omni  [--out DIR] [--name NAME] [--force]

THE INLINING PROHIBITION — the rule everything else here follows from
=====================================================================
**A skill's content is NEVER copied into an agent.** Not concatenated, not
duplicated, not embedded. An agent REFERENCES skills by name in its ``skills:``
frontmatter and nowhere else.

The reason is single-source-of-truth, and it outranks any token argument: a skill
has to stay INDEPENDENT so it can be shared by many agents and edited, fixed, or
updated ONCE. An inlined copy is a second source that silently rots the moment
the original changes, and with N agents inlining it there are N stale copies and
no signal that any of them drifted. Hence the mechanical acceptance test: no
emitted agent body may contain a substring of any closure skill's body beyond its
NAME (``tests/test_convert_agent.py::TestNoInlining``).

That prohibition is what makes the three architectures far MORE ALIKE than
different:

============  =================================  ===========================
architecture  ``skills:`` frontmatter lists       skills execute in
============  =================================  ===========================
ALL-IN-ONE    every reachable closure skill      the same agent
ONE-FOR-ALL   every reachable closure skill      a separate subagent per skill
PLUGIN-OMNI   the plugin's ``the-skills-menu``   resolved at runtime from the
              plus the companion                 menu
============  =================================  ===========================

**ALL-IN-ONE and ONE-FOR-ALL differ in exactly ONE thing: WHERE a skill runs.**
The frontmatter list and the routing body are the same construction; ONE-FOR-ALL
additionally adds three keys IN PLACE to each shared skill's own frontmatter.

THREE TRAPS, each doc-verified — get one wrong and the output is broken
======================================================================
1. **``agent:`` ALONE DOES NOTHING.** ``context: fork`` is what forks a subagent;
   ``agent:`` only selects WHICH subagent type once fork is already set
   (``skills.md``: *"Which subagent type to use when ``context: fork`` is set"*).
   An earlier draft of the spec claimed ``agent:`` was the mechanism; it was
   WRONG. So ``--to one-for-all`` always writes ``context: fork``, and ``agent:``
   only when ``--node-agent`` asks for it.
2. **``background`` defaults to ``true``**, so a forked skill returns NOTHING
   inline — its result arrives as a notification. A routing graph that threads one
   node's output into the next step therefore needs ``background: false``
   (Claude Code **v2.1.218+**). Without it you get a graph whose steps appear to
   run and silently deliver nothing downstream, which is worse than a hard
   failure because it looks fine.
3. **``skills:`` is NOT valid inside a skill** — it is agent-only (verified
   against ``cpv_validation_common.SKILL_FRONTMATTER_FIELDS``). A node therefore
   CANNOT carry its own skill list, which is why the choice tree lives in the
   AGENT's body for every mode.

Reuse, never reimplement
========================
* the skill set comes from ``cpv_agent_closure.resolve_agent_closure`` — the
  closure SSOT. This module never re-derives it;
* the fence parser is ``cpv_tool_permission_match.iter_fenced_blocks``;
* the tool-grant grammar is ``cpv_tool_permission_match.parse_declared_tools`` /
  ``granted_builtin_tools`` / ``declared_tool_names``;
* the frontmatter parser is ``validate_agent.parse_frontmatter``;
* the ``the-skills-menu`` catalog shape is
  ``standardize_plugin._render_skills_menu_catalog`` (itself built on
  ``generate_plugin_repo.gen_the_skills_menu_skill``), and a catalog row is
  appended by ``add_component._register_in_the_skills_menu``.

WHY EVERY EMITTED AGENT MUST PASS ``validate_agent`` WITH NO BLOCKING FINDING
============================================================================
A generator that emits an unresolvable preload (AC1) or an un-preloadable one
(AC5) has produced a BROKEN agent — the preload is silently dropped at dispatch
and only a debug-log line records it. So the skill set is filtered against the
same facts the AC findings check, and every exclusion is REPORTED rather than
silently dropped:

* a name that resolves in no skill root → excluded (would be AC1);
* a skill that cannot be preloaded (``disable-model-invocation: true``, or the
  bundled user-only ``verify`` / ``code-review``) → excluded (would be AC5);
* a FOREIGN-namespaced reference → kept out of ``skills:`` (we cannot prove which
  plugin's skill it is) but still routed to at runtime;
* a reference that is DEAD in the source (its ``Skill`` gate is shut) → excluded,
  with the remedy named.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from cpv_agent_closure import (
    AgentClosure,
    find_plugin_root,
    plugin_namespace,
    resolve_agent_closure,
    skill_blocks_preloading,
    skill_object_invocation_matcher,
)
from cpv_tool_permission_match import (
    declared_tool_names,
    granted_builtin_tools,
    iter_fenced_blocks,
    parse_declared_tools,
)

#: The three architectures, spelled exactly as the spec's canonical vocabulary.
MODES: tuple[str, ...] = ("all-in-one", "one-for-all", "plugin-omni")

#: MANDATORY companion skill on every generated variant (spec §1.2). Its Iron Law
#: — no completion claim without fresh verification evidence — is exactly the
#: failure mode a multi-skill or multi-node agent is most prone to, because a node
#: REPORTING success is not evidence that the step happened.
COMPANION_SKILL_NAME = "verification-before-completion"

#: Where the companion's text comes from when the target plugin has none. Kept as
#: ONE source; a second copy embedded in this file would drift the first time the
#: template changed.
COMPANION_TEMPLATE_REL = Path("design/specs/verification-before-completion.template.md")

#: A plugin's runtime skill catalog is named ``<prefix>the-skills-menu`` (CPV's own
#: is ``cpv-the-skills-menu``); matched by suffix so any plugin's spelling is
#: recognised instead of only CPV's.
MENU_SKILL_SUFFIX = "the-skills-menu"

#: The name ``_render_skills_menu_catalog`` generates when a plugin has no menu.
GENERATED_MENU_SKILL_NAME = "cpv-the-skills-menu"

#: ``background: false`` — the key that makes a forked node deliver its output
#: inline instead of as a notification — needs this Claude Code version.
BACKGROUND_FALSE_MIN_CC_VERSION = "2.1.218"

#: The frontmatter keys ``--to one-for-all`` adds to each node skill, IN PLACE.
_NODE_CONTEXT_VALUE = "fork"
_NODE_BACKGROUND_VALUE = "false"

#: A top-level frontmatter key: an identifier at COLUMN 0 followed by ``:``. Block
#: -scalar bodies and nested mappings are indented, so they can never match — the
#: same anchoring the D1 duplicate-key detector uses.
_TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_.\-]+)[ \t]*:(.*)$")

#: A markdown ATX heading (outside a fence) — the branch signal read out of the
#: SOURCE agent's own structure.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

#: Longest routing hint carried into a table cell. A hint is a pointer, not a
#: paragraph; truncating also keeps the emitted body far away from ever holding a
#: long verbatim run of any other document.
_HINT_LIMIT = 110

#: A ``Skill(...)`` invocation appearing in a line we are about to CARRY into the
#: emitted body. It must go: a carried invocation would become a REAL runtime
#: reference in the new agent, and if it named a skill that does not resolve there
#: the generator would have manufactured an AC2 MAJOR out of the source's prose.
#: Deliberately BLUNTER than the closure's detection grammar — this is display
#: sanitisation, where over-matching is safe and under-matching is not.
_SKILL_CALL_IN_HINT_RE = re.compile(r"`?\bSkill\s*\([^)\n]*\)`?")

_LOG_PREFIX = "  [convert-agent]"


# ---------------------------------------------------------------------------
# The data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutedSkill:
    """One row of the emitted routing table.

    ``when`` is derived from the SOURCE AGENT's own body (the document being
    converted), NEVER from the skill's content — see the inlining prohibition.
    Empty when the source gave no hint.
    """

    name: str
    branch: str
    when: str


@dataclass(frozen=True)
class NodeConversion:
    """A ``--to one-for-all`` node: one shared skill about to gain fork keys."""

    name: str
    skill_md: str
    added: tuple[str, ...]
    changed: tuple[tuple[str, str, str], ...]
    """``(key, existing value, desired value)`` — a conflict needing ``--force``."""
    other_agents: tuple[str, ...]
    """Agent FILES other than the source that also reach this skill. Adding
    ``context: fork`` changes execution for every one of them."""

    @property
    def shared(self) -> bool:
        return bool(self.other_agents)

    @property
    def needs_write(self) -> bool:
        return bool(self.added or self.changed)


@dataclass
class ConversionResult:
    """Everything the conversion did, or refused to do, and why."""

    mode: str
    source: str | None = None
    agent_name: str = ""
    agent_path: str | None = None
    plugin_root: str | None = None
    skill_roots: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    routed: tuple[RoutedSkill, ...] = ()
    nodes: tuple[NodeConversion, ...] = ()
    nodes_written: tuple[str, ...] = ()
    excluded: tuple[tuple[str, str], ...] = ()
    dropped_frontmatter: tuple[str, ...] = ()
    companion_path: str | None = None
    companion_created: bool = False
    menu_name: str | None = None
    menu_created: bool = False
    menu_row_added: bool = False
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    written: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "source": self.source,
            "agent_name": self.agent_name,
            "agent_path": self.agent_path,
            "plugin_root": self.plugin_root,
            "skill_roots": list(self.skill_roots),
            "written": self.written,
            "skills": list(self.skills),
            "routed": [{"name": r.name, "branch": r.branch, "when": r.when} for r in self.routed],
            "nodes": [
                {
                    "name": n.name,
                    "skill_md": n.skill_md,
                    "added": list(n.added),
                    "changed": [
                        {"key": k, "existing": old, "desired": new} for k, old, new in n.changed
                    ],
                    "shared": n.shared,
                    "other_agents": list(n.other_agents),
                }
                for n in self.nodes
            ],
            "nodes_written": list(self.nodes_written),
            "excluded": [{"name": n, "reason": r} for n, r in self.excluded],
            "dropped_frontmatter": list(self.dropped_frontmatter),
            "companion": {
                "name": COMPANION_SKILL_NAME,
                "path": self.companion_path,
                "created": self.companion_created,
            },
            "menu": {
                "name": self.menu_name,
                "created": self.menu_created,
                "row_added": self.menu_row_added,
            },
            "notes": list(self.notes),
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def read_markdown_parts(path: Path) -> tuple[dict[str, Any], str] | None:
    """``(frontmatter, body)`` for a component file, or None when unreadable.

    Deferred import of ``validate_agent.parse_frontmatter`` so this module shares
    the ONE frontmatter parser without paying (or risking) a module-scope import
    of the whole agent validator.

    A file with NO (or unparseable) frontmatter yields ``({}, whole file)`` — the
    body is still the thing we route on, and the component validator is the surface
    that reports the malformed header.
    """
    from validate_agent import parse_frontmatter  # noqa: PLC0415

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        frontmatter, body, _ = parse_frontmatter(content)
    except (yaml.YAMLError, ValueError):
        return None
    if frontmatter is None:
        return {}, content
    if not isinstance(frontmatter, dict):
        # Frontmatter that parsed to a scalar or a list — no fields to read.
        return {}, body
    return frontmatter, body


def _fenced_lines(body: str) -> set[int]:
    """1-based body lines inside a fenced block, projected from the ONE fence parser."""
    flagged: set[int] = set()
    for block in iter_fenced_blocks(body):
        flagged.update(range(block.open_line, block.close_line + 1))
    return flagged


def _sanitize_inline(text: str, limit: int = _HINT_LIMIT) -> str:
    """Collapse ``text`` into ONE table-cell-safe line.

    Escapes ``|`` (an unescaped pipe would break out of the cell and corrupt the
    table), drops leading list/quote/heading markers so a carried bullet does not
    read as a bullet, and truncates — a routing hint is a pointer, not a
    paragraph.
    """
    one = " ".join(text.split())
    one = re.sub(r"^(?:[-*+]\s+|>\s*|#{1,6}\s+|\d+[.)]\s+)+", "", one).strip()
    one = one.replace("|", r"\|")
    if len(one) > limit:
        one = one[: limit - 1].rstrip() + "…"
    return one


def _headings(body: str) -> list[tuple[int, str]]:
    """``(1-based line, heading text)`` for every ATX heading outside a fence."""
    fenced = _fenced_lines(body)
    found: list[tuple[int, str]] = []
    for idx, line in enumerate(body.splitlines(), start=1):
        if idx in fenced:
            continue
        match = _HEADING_RE.match(line)
        if match:
            found.append((idx, match.group(2).strip()))
    return found


def _name_mention_re(name: str) -> re.Pattern[str]:
    """Hyphen-aware, case-insensitive matcher for one skill NAME.

    Hyphen-aware because a plain ``\\b`` treats ``-`` as a boundary, so
    ``foo-skill`` would count as a mention of ``skill`` — a DIFFERENT skill's name
    accepted as evidence. Mirrors ``cpv_agent_closure.body_mentions_skill_name``.
    """
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", re.IGNORECASE)


def hint_from_line(line: str, name: str) -> str:
    """Turn the source line that mentions ``name`` into a routing hint.

    Two transforms, each fixing a defect seen on a real agent:

    * a TABLE ROW (``| Per-error fix steps | Skill({...}) |``) is reduced to the
      cell that does NOT name the skill — that cell is the "when", and carrying the
      whole row produced an unreadable pipe-escaped mess;
    * every ``Skill(...)`` invocation is REMOVED, because carrying one would make
      the emitted agent invoke that skill for real — and if the name did not
      resolve there, the generator would have manufactured an AC2 MAJOR out of the
      source's own prose.
    """
    text = line.strip()
    if text.startswith("|"):
        cells = [c.strip() for c in text.strip("|").split("|")]
        pattern = _name_mention_re(name)
        candidates = [c for c in cells if c and not pattern.search(c)]
        cleaned = [c for c in (_SKILL_CALL_IN_HINT_RE.sub("", c).strip() for c in candidates) if c]
        text = cleaned[0] if cleaned else " ".join(candidates)
    text = _SKILL_CALL_IN_HINT_RE.sub("", text)
    return _sanitize_inline(text)


def _first_mention(body: str, name: str) -> tuple[int, str] | None:
    """First ``(1-based line, line text)`` mentioning ``name`` outside a fence."""
    pattern = _name_mention_re(name)
    fenced = _fenced_lines(body)
    for idx, line in enumerate(body.splitlines(), start=1):
        if idx in fenced:
            continue
        if pattern.search(line):
            return idx, line
    return None


def route_skills(source_body: str, names: Sequence[str]) -> list[RoutedSkill]:
    """Build the routing rows for ``names`` from the SOURCE agent's structure.

    For each skill: find where the source first mentions it (outside fences), take
    the nearest PRECEDING heading as its branch, and the mentioning line as the
    routing hint. A skill the source never mentions (it came from ``skills:``
    frontmatter only) gets no branch and no hint — the spec's instruction is
    explicit that where the source gives no ordering we emit a flat
    "choose by intent" table rather than INVENTING a sequence.

    Nothing here reads a skill's own file, so no skill content can leak into the
    emitted body.
    """
    headings = _headings(source_body)
    rows: list[RoutedSkill] = []
    for name in names:
        mention = _first_mention(source_body, name)
        if mention is None:
            rows.append(RoutedSkill(name=name, branch="", when=""))
            continue
        line_no, line_text = mention
        branch = ""
        for heading_line, heading_text in headings:
            if heading_line < line_no:
                branch = heading_text
            else:
                break
        rows.append(
            RoutedSkill(
                name=name,
                branch=_sanitize_inline(branch, 80),
                when=hint_from_line(line_text, name),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# The companion skill (spec §1.2) — the AC1 interaction
# ---------------------------------------------------------------------------


def companion_template_candidates() -> list[Path]:
    """Where the companion template may live, most authoritative first.

    ``scripts/`` is the only directory the wheel ships, so an installed CPV has no
    ``design/`` tree; ``$CLAUDE_PLUGIN_ROOT`` covers the installed-plugin case and
    the repo-relative path covers a source checkout. When NEITHER resolves we fail
    LOUDLY rather than invent a companion — emitting an agent that preloads a
    skill we did not write is exactly the AC1 MAJOR this generator exists to avoid.
    """
    candidates: list[Path] = []
    plugin_root_env = os.environ.get("CLAUDE_PLUGIN_ROOT", "").strip()
    if plugin_root_env:
        candidates.append(Path(plugin_root_env) / COMPANION_TEMPLATE_REL)
    candidates.append(Path(__file__).resolve().parent.parent / COMPANION_TEMPLATE_REL)
    return candidates


def ensure_companion_skill(plugin_root: Path, *, dry_run: bool = False) -> tuple[Path, bool, str | None]:
    """Guarantee ``<plugin_root>/skills/verification-before-completion/SKILL.md``.

    Returns ``(path, created, error)``.

    NEVER overwrites an existing companion — the user may have adapted it, and a
    generator that clobbered an adapted skill would destroy work while claiming
    success. That is also why the check is on the exact plugin-relative path
    rather than "does any search root have one": a machine-scope copy would make
    the answer depend on whose box this ran on.
    """
    target = plugin_root / "skills" / COMPANION_SKILL_NAME / "SKILL.md"
    if target.is_file():
        return target, False, None
    template: Path | None = None
    for candidate in companion_template_candidates():
        try:
            if candidate.is_file():
                template = candidate
                break
        except OSError:
            continue
    if template is None:
        tried = ", ".join(str(c) for c in companion_template_candidates())
        return (
            target,
            False,
            f"the {COMPANION_SKILL_NAME!r} companion skill is missing from the target plugin and the "
            f"bundled template could not be found (tried: {tried}). Every generated variant must carry "
            f"that skill, and preloading a name that does not resolve is a MAJOR — ship the template or "
            f"add the skill by hand, then re-run.",
        )
    try:
        text = template.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return target, False, f"could not read the companion template {template}: {exc}"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return target, True, None


# ---------------------------------------------------------------------------
# The plugin's the-skills-menu (PLUGIN-OMNI)
# ---------------------------------------------------------------------------


def find_menu_skill(plugin_root: Path) -> tuple[str, Path] | None:
    """The plugin's existing ``*the-skills-menu`` skill as ``(name, SKILL.md)``.

    Matched by NAME SUFFIX so any plugin's prefix is recognised, and the
    ``-create`` migrator sibling is never mistaken for the catalog itself.
    """
    skills_dir = plugin_root / "skills"
    try:
        entries = sorted(skills_dir.iterdir())
    except OSError:
        return None
    for entry in entries:
        skill_md = entry / "SKILL.md"
        try:
            if not entry.is_dir() or not skill_md.is_file():
                continue
        except OSError:
            continue
        parts = read_markdown_parts(skill_md)
        declared = ""
        if parts is not None:
            raw = parts[0].get("name")
            if isinstance(raw, str):
                declared = raw.strip()
        name = declared or entry.name
        if name.endswith(MENU_SKILL_SUFFIX) and not name.endswith(f"{MENU_SKILL_SUFFIX}-create"):
            return name, skill_md
    return None


def ensure_menu_skill(plugin_root: Path, *, dry_run: bool = False) -> tuple[str | None, bool, bool, str | None]:
    """Guarantee a POPULATED ``the-skills-menu`` for PLUGIN-OMNI.

    Returns ``(menu_name, created, companion_row_added, error)``.

    An EMPTY catalog is never acceptable here: a PLUGIN-OMNI agent whose only
    skill is an empty menu is INERT while looking perfectly correct. So when the
    plugin has no menu we generate one FROM THE REAL ``skills/`` INVENTORY
    (reusing ``standardize_plugin``'s renderer, which is itself built on the
    scaffolder's catalog shape), and when it has zero operational skills we return
    an error instead of writing a shell.

    An EXISTING menu is never rewritten — only, when it does not list the
    companion skill, does it gain ONE additive row (spec §1.2: the menu must list
    the companion too). That is the surgical, idempotent shape this repo already
    uses for in-place migrations, and it cannot touch a row the author owns.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:  # pragma: no cover - import-path bootstrap
        sys.path.insert(0, scripts_dir)
    from add_component import _register_in_the_skills_menu  # noqa: PLC0415
    from standardize_plugin import (  # noqa: PLC0415
        _params_from_manifest,
        _read_plugin_json,
        _render_skills_menu_catalog,
        scan_plugin_skills_inventory,
    )

    existing = find_menu_skill(plugin_root)
    if existing is not None:
        name, skill_md = existing
        try:
            menu_text = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return name, False, False, f"could not read the existing menu {skill_md}: {exc}"
        row_added = False
        if not _name_mention_re(COMPANION_SKILL_NAME).search(menu_text):
            if dry_run:
                row_added = True
            else:
                row_added = _register_in_the_skills_menu(
                    plugin_root,
                    COMPANION_SKILL_NAME,
                    "Iron Law — no completion claim without fresh verification evidence.",
                    catalog=skill_md,
                )
        return name, False, row_added, None

    inventory = scan_plugin_skills_inventory(plugin_root)
    # The companion is written BEFORE this runs, so it is already in the
    # inventory — and a catalog whose only entry is the verification companion is
    # an EMPTY catalog in every sense that matters: it offers the agent no
    # capability at all. Count operational skills only.
    operational = [entry for entry in inventory if entry[0] != COMPANION_SKILL_NAME]
    if not operational:
        return (
            None,
            False,
            False,
            f"{plugin_root} has no operational skills under skills/, so a the-skills-menu catalog "
            f"would be EMPTY and the PLUGIN-OMNI agent would be inert while looking correct. Ship at "
            f"least one skill (the {COMPANION_SKILL_NAME!r} companion alone is not a capability) or "
            f"use --to all-in-one.",
        )
    manifest = _read_plugin_json(plugin_root)
    if not isinstance(manifest.get("name"), str) or not manifest.get("name", "").strip():
        # A pre-publish source may have no manifest at all; the directory name is
        # the only honest fallback and beats the renderer's "unknown-plugin".
        manifest = {**manifest, "name": plugin_root.name}
    params = _params_from_manifest(manifest)
    target = plugin_root / "skills" / GENERATED_MENU_SKILL_NAME / "SKILL.md"
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_skills_menu_catalog(params, inventory), encoding="utf-8")
    return GENERATED_MENU_SKILL_NAME, True, False, None


# ---------------------------------------------------------------------------
# Tool-gate handling — all three modes need the §1 `Skill` gate OPEN
# ---------------------------------------------------------------------------


def ensure_skill_tool(tools: Any) -> tuple[Any, bool]:
    """Return ``(tools, changed)`` with the ``Skill`` tool granted.

    An ABSENT ``tools:`` field is left ABSENT: the agent then inherits every
    session tool, so the gate is ALREADY open, and materialising an explicit list
    would silently REVOKE every other tool the source relied on. That is the
    behaviour-preserving reading of "``Skill`` is added to ``tools``" — the point
    of the rule is an open gate, not a literal key.
    """
    rules = parse_declared_tools(tools)
    if rules is None:
        return None, False
    if "Skill" in granted_builtin_tools(rules):
        return tools, False
    if isinstance(tools, list):
        return [*tools, "Skill"], True
    if isinstance(tools, str):
        stripped = tools.rstrip().rstrip(",")
        return (f"{stripped}, Skill" if stripped else "Skill"), True
    # A non-list / non-string `tools:` is malformed; the agent validator reports
    # the shape. Normalising it here would hide that, so leave it alone.
    return tools, False


def strip_skill_from_disallowed(disallowed: Any) -> tuple[Any, bool]:
    """Remove ``Skill`` from a ``disallowedTools`` value.

    ``disallowedTools`` is applied FIRST, so a denied ``Skill`` shuts the gate
    whatever ``tools`` says — leaving it in place would make every routed
    invocation DEAD.
    """
    rules = parse_declared_tools(disallowed)
    if rules is None:
        return None, False
    kept = [rule for rule in rules if "Skill" not in declared_tool_names([rule])]
    if len(kept) == len(rules):
        return disallowed, False
    return (kept or None), True


# ---------------------------------------------------------------------------
# ONE-FOR-ALL — the IN-PLACE node conversion
# ---------------------------------------------------------------------------


def _frontmatter_span(text: str) -> tuple[int, int] | None:
    """``(first, last)`` indices into ``text.splitlines(keepends=True)`` covering the
    frontmatter BODY lines (between the two ``---`` delimiters), or None."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return 1, idx
    return None


def _existing_top_level_keys(text: str) -> dict[str, str]:
    span = _frontmatter_span(text)
    if span is None:
        return {}
    first, last = span
    lines = text.splitlines(keepends=True)
    found: dict[str, str] = {}
    for line in lines[first:last]:
        match = _TOP_LEVEL_KEY_RE.match(line.rstrip("\r\n"))
        if match:
            found.setdefault(match.group(1), match.group(2).strip())
    return found


def _normalized_value(raw: str) -> str:
    """The comparable form of a frontmatter scalar (quotes and comment stripped)."""
    value = raw.strip()
    if value.startswith(("'", '"')) and len(value) >= 2 and value[0] == value[-1]:
        value = value[1:-1].strip()
    return value.lower()


def plan_node_frontmatter(
    text: str, *, node_agent: str | None
) -> tuple[dict[str, str], list[tuple[str, str, str]]] | None:
    """``(keys to ADD, key CONFLICTS)`` for one node skill, or None with no frontmatter.

    A conflict is a key that already exists with a DIFFERENT value. It is reported
    and needs ``--force``, because re-writing an author's explicit ``context:`` is
    a decision, not a mechanical fix. A key that already carries the desired value
    is a no-op, so re-running is idempotent — and no key is ever ADDED twice,
    which would be a duplicate-key MAJOR (YAML silently keeps the LAST one).
    """
    if _frontmatter_span(text) is None:
        return None
    existing = _existing_top_level_keys(text)
    desired: dict[str, str] = {
        "context": _NODE_CONTEXT_VALUE,
        "background": _NODE_BACKGROUND_VALUE,
    }
    if node_agent:
        desired["agent"] = node_agent
    to_add: dict[str, str] = {}
    conflicts: list[tuple[str, str, str]] = []
    for key, want in desired.items():
        if key not in existing:
            to_add[key] = want
        elif _normalized_value(existing[key]) != want.lower():
            conflicts.append((key, existing[key], want))
    return to_add, conflicts


def apply_node_frontmatter(
    text: str, to_add: dict[str, str], conflicts: Sequence[tuple[str, str, str]]
) -> str:
    """Write the fork keys into ``text``'s frontmatter and change NOTHING else.

    Operates on ``splitlines(keepends=True)`` so every untouched line — and the
    file's line endings — survive byte-identically; a ``"\\n".join`` rebuild would
    silently normalise CRLF across the whole file.
    """
    span = _frontmatter_span(text)
    if span is None:  # pragma: no cover - callers check first
        return text
    first, last = span
    lines = text.splitlines(keepends=True)
    newline = "\n"
    if lines[last].endswith("\r\n"):
        newline = "\r\n"

    replacements = {key: want for key, _old, want in conflicts}
    out: list[str] = list(lines[:first])
    for line in lines[first:last]:
        match = _TOP_LEVEL_KEY_RE.match(line.rstrip("\r\n"))
        if match and match.group(1) in replacements:
            ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            out.append(f"{match.group(1)}: {replacements[match.group(1)]}{ending}")
        else:
            out.append(line)
    for key, want in to_add.items():
        out.append(f"{key}: {want}{newline}")
    out.extend(lines[last:])
    return "".join(out)


def _agents_dirs(plugin_root: Path) -> list[Path]:
    return [d for d in (plugin_root / "agents",) if d.is_dir()]


def other_agents_reaching(
    plugin_root: Path,
    skill_paths: Sequence[str],
    *,
    exclude: Sequence[Path],
    roots: Sequence[Path],
) -> dict[str, list[str]]:
    """Map each skill SKILL.md path → the other agent files that reach it.

    Keyed on the RESOLVED path, not the name: two spellings can index the same
    file, and it is the FILE whose execution ``context: fork`` changes.

    The reach test is the closure SSOT, not a ``skills:`` grep — an agent that
    INVOKES the skill at runtime is affected by the fork just as much as one that
    preloads it, and only the closure sees both.
    """
    excluded = {p.resolve() for p in exclude}
    wanted = set(skill_paths)
    hits: dict[str, list[str]] = {path: [] for path in skill_paths}
    for agents_dir in _agents_dirs(plugin_root):
        for agent_md in sorted(agents_dir.glob("*.md")):
            if agent_md.resolve() in excluded:
                continue
            closure = resolve_agent_closure(agent_md, roots=roots)
            reached: set[str] = {
                ref.resolved_path
                for ref in closure.refs
                if ref.reachable and ref.resolved_path is not None and ref.resolved_path in wanted
            }
            for path in sorted(reached):
                hits[path].append(str(agent_md))
    return hits


# ---------------------------------------------------------------------------
# Rendering the variant agent
# ---------------------------------------------------------------------------

_MODE_LABEL = {
    "all-in-one": "ALL-IN-ONE",
    "one-for-all": "ONE-FOR-ALL",
    "plugin-omni": "PLUGIN-OMNI",
}

_MODE_HOW = {
    "all-in-one": (
        "- Your `skills:` list IS the preload — each named skill's full content is "
        "injected into every invocation of this agent, so all of it is available "
        "from turn 1.\n"
        "- The skills run **in this agent**. There is no subagent and no hand-off.\n"
    ),
    "one-for-all": (
        "- Your `skills:` list IS the preload — each named skill's full content is "
        "injected into every invocation of this agent, so all of it is available "
        "from turn 1.\n"
        "- Each listed skill runs in a **separate subagent** with minimal context. "
        "That is the skill's OWN `context: fork` doing the forking, so you invoke a "
        "node exactly as you would invoke any skill; you do not spawn it yourself.\n"
        "- Each node also carries `background: false`, so its result comes back to "
        "you INLINE and you can thread it into the next step. Without that key a "
        "forked skill returns nothing inline and its result arrives as a "
        "notification — the step would appear to run and deliver nothing. Needs "
        f"Claude Code v{BACKGROUND_FALSE_MIN_CC_VERSION}+.\n"
    ),
    "plugin-omni": (
        "- Your `skills:` list holds exactly TWO entries: the plugin's runtime skill "
        "catalog and the verification companion. Everything else is resolved AT "
        "RUNTIME from the catalog.\n"
        "- Read the catalog, pick the ONE skill the task needs, invoke it, and wait "
        "for it to return before picking another.\n"
    ),
}


def _routing_sections(routed: Sequence[RoutedSkill]) -> str:
    """The routing tables — the SHARED body layer of all-in-one and one-for-all.

    Grouped by the branch each skill was found under in the source agent, in
    source order; skills the source never mentioned land in a final flat
    "Choose by intent" group instead of an invented sequence.
    """
    groups: dict[str, list[RoutedSkill]] = {}
    for row in routed:
        groups.setdefault(row.branch, []).append(row)

    lines: list[str] = []
    lines.append(
        "Reach for a skill when its row says to. A row whose hint is `—` had no "
        "routing hint in the source agent — load the skill and follow its own "
        "description and instructions (this table deliberately does not copy them).\n"
    )
    multi = len(groups) > 1 or any(branch for branch in groups)
    for branch, rows in groups.items():
        if multi:
            lines.append(f"### {branch or 'Choose by intent'}\n")
        lines.append("| # | Skill | When to reach for it |")
        lines.append("|---|-------|----------------------|")
        for i, row in enumerate(rows, start=1):
            lines.append(f"| {i} | `{row.name}` | {row.when or '—'} |")
        lines.append("")
    return "\n".join(lines)


def _nodes_section(nodes: Sequence[NodeConversion], node_agent: str | None) -> str:
    lines = [
        "Each skill below runs as its OWN subagent. The fork is declared in the "
        "skill's own frontmatter, IN PLACE — there is no private copy of a skill "
        "and never will be, so a fix to a skill reaches every agent that lists it.\n",
        "| # | Node (skill) | Frontmatter it carries |",
        "|---|--------------|------------------------|",
    ]
    carried = "`context: fork`, `background: false`" + (f", `agent: {node_agent}`" if node_agent else "")
    for i, node in enumerate(nodes, start=1):
        lines.append(f"| {i} | `{node.name}` | {carried} |")
    lines.append("")
    lines.append(
        "`context: fork` is what forks the subagent — `agent:` alone does NOTHING, "
        "it only selects WHICH subagent type once fork is set."
    )
    return "\n".join(lines)


def _examples_section(agent_name: str, mode: str, first_skill: str, menu_name: str | None) -> str:
    """Two <example> blocks — Anthropic's trigger-quality pattern.

    Every literal invocation stays inside a fenced block, because a
    ``Skill({skill: "<placeholder>"})`` outside one would be read as a REAL
    runtime reference and then reported as naming a skill that does not exist.
    """
    if mode == "plugin-omni":
        second = (
            f"assistant: I read `{menu_name}`, pick the one skill the task needs, invoke it, "
            f"and report what it returns."
        )
    elif mode == "one-for-all":
        second = (
            f"assistant: I go to the routing table and reach for `{first_skill}`, which runs as "
            f"its own subagent and hands its output back to me for the next step."
        )
    else:
        second = (
            f"assistant: I already hold every skill I need, so I go straight to the routing "
            f"table and reach for `{first_skill}`."
        )
    return (
        "<example>\n"
        f"Context: the user needs the work this agent owns and dispatches `{agent_name}`.\n"
        "user: Handle this for me end to end.\n"
        f"{second}\n"
        "<commentary>\n"
        "Routing happens in this body; the capability lives in the skills, which are "
        "referenced by name and never copied here.\n"
        "</commentary>\n"
        "</example>\n"
        "\n"
        "<example>\n"
        "Context: the agent is about to report that it finished.\n"
        "user: Is it done?\n"
        f"assistant: Not until `{COMPANION_SKILL_NAME}` is satisfied — I run the check "
        "first and quote its output, then answer.\n"
        "<commentary>\n"
        "A node (or a skill) REPORTING success is not evidence that the step happened. "
        "That is why the companion skill is mandatory on every variant.\n"
        "</commentary>\n"
        "</example>\n"
    )


def render_variant_agent(
    *,
    mode: str,
    agent_name: str,
    scope_label: str,
    source_label: str | None,
    tools: Any,
    disallowed: Any,
    skills: Sequence[str],
    routed: Sequence[RoutedSkill],
    nodes: Sequence[NodeConversion] = (),
    node_agent: str | None = None,
    menu_name: str | None = None,
    notes: Sequence[str] = (),
) -> str:
    """Render the whole variant agent ``.md``.

    The frontmatter is emitted through ``yaml.safe_dump`` so a description or a
    tool list containing a YAML metacharacter is quoted correctly instead of
    producing a file that parses into something else.

    Deliberately NOT carried over from the source: ``model:`` (CA-04 — an agent
    must inherit the session model) and every other source field, each of which is
    REPORTED as dropped so the author can re-add it on purpose.
    """
    label = _MODE_LABEL[mode]
    if mode == "plugin-omni":
        description = (
            f"{label} agent for {scope_label} — preloads only the plugin's skill catalog "
            f"({menu_name}) plus {COMPANION_SKILL_NAME}, and resolves every other skill at "
            f"runtime from that catalog. Use when you want one entry point whose skill set can "
            f"grow without touching the agent."
        )
    elif mode == "one-for-all":
        description = (
            f"{label} agent for {scope_label} — preloads {len(skills)} skill(s) by name and runs "
            f"each of them in its OWN subagent with minimal context, threading each step's output "
            f"into the next. Use when you want a skill-per-subagent graph instead of one agent "
            f"doing everything itself."
        )
    else:
        description = (
            f"{label} agent for {scope_label} — preloads {len(skills)} skill(s) by name so the whole "
            f"set is in context from turn 1, and routes to each one from its own body. Use when you "
            f"want a single agent that is ready immediately with every skill it needs."
        )

    frontmatter: dict[str, Any] = {"name": agent_name, "description": description}
    if tools is not None:
        frontmatter["tools"] = tools
    if disallowed is not None:
        frontmatter["disallowedTools"] = disallowed
    frontmatter["skills"] = list(skills)
    fm_yaml = yaml.safe_dump(
        frontmatter, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=False
    )

    origin = f"the `{source_label}` agent" if source_label else scope_label
    parts: list[str] = [
        f"---\n{fm_yaml}---\n",
        f"\n# {agent_name}\n\n",
        f"You are the {label} variant of {origin}. Every skill you need is listed BY NAME in "
        f"your `skills:` frontmatter, so you never have to go looking for it — and no skill's "
        f"content is copied into this file, so fixing a skill once fixes it for every agent "
        f"that lists it.\n\n",
        "## How your skills work\n\n",
        _MODE_HOW[mode],
        "- A skill lives in `skills/<name>/SKILL.md`. That file is the single source of truth "
        "for what the skill does; this body only says WHEN to reach for it.\n\n",
        "## Workflow — skill routing\n\n",
    ]

    if mode == "plugin-omni":
        parts.append(
            f"1. Read `{menu_name}` — it lists every skill this plugin ships, what each one does, "
            "and when to use it.\n"
            "2. Pick the ONE skill the task needs and invoke it with the `Skill` tool "
            "(the invocation syntax is in the fence below).\n"
            "3. Wait for it to return before picking another skill, then relay its result.\n"
            f"4. Before you claim anything is finished, satisfy `{COMPANION_SKILL_NAME}`.\n\n"
            "```text\n"
            'Skill({skill: "<plugin>:<name>"})\n'
            "```\n\n"
        )
    else:
        parts.append(_routing_sections(routed))
        parts.append("\n")

    if mode == "one-for-all" and nodes:
        parts.append("## Nodes\n\n")
        parts.append(_nodes_section(nodes, node_agent))
        parts.append("\n\n")

    parts.append("## Before you claim completion\n\n")
    parts.append(
        f"`{COMPANION_SKILL_NAME}` is preloaded for a reason: a step (or a node) REPORTING "
        f"success is not evidence that it happened. Run the verification it names, read the real "
        f"output, and quote it. No completion claim without fresh verification evidence.\n\n"
    )
    parts.append("## Examples\n\n")
    parts.append(
        _examples_section(agent_name, mode, routed[0].name if routed else COMPANION_SKILL_NAME, menu_name)
    )
    parts.append("\n## Provenance\n\n")
    generator = "convert_agent.py" if source_label else "create_mono_agent.py"
    parts.append(f"- Generated by `{generator}` (`--to {mode}`) for {scope_label}.\n")
    parts.append(
        "- Skills are REFERENCED BY NAME only. Nothing in this body is a copy of a skill's "
        "content, and nothing here may become one.\n"
    )
    for note in notes:
        parts.append(f"- {note}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# The conversion itself
# ---------------------------------------------------------------------------


def target_plugin_root(agent_path: Path) -> Path | None:
    """The plugin that owns ``agent_path``.

    Falls back to the parent of an ``agents/`` directory when no manifest is found:
    CPV must work on an UNINSTALLED, marketplace-less, possibly manifest-less
    pre-publish source, and that shape still has a ``skills/`` sibling.
    """
    root = find_plugin_root(agent_path)
    if root is not None:
        return root
    parent = agent_path.resolve().parent
    if parent.name.lower() == "agents":
        return parent.parent
    return None


def _closure_skill_names(
    closure: AgentClosure, local_ns: str | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Partition the closure into ``(preloadable, excluded)``.

    ``preloadable`` is ``(name, resolved_path)`` in closure order, de-duplicated.
    ``excluded`` is ``(name, reason)`` — every exclusion is reported, never
    silently dropped, because a silently missing skill is exactly the failure this
    generator exists to prevent.

    A reason NEVER contains an absolute path. Reasons are echoed into the emitted
    agent's provenance, and a developer's absolute path in a shipped component is a
    hardcoded-user-path MAJOR (and a privacy leak) — the resolved roots belong in
    the CLI report, which nobody commits.
    """
    keep: list[tuple[str, str]] = []
    excluded: list[tuple[str, str]] = []
    seen: set[str] = set()
    seen_excluded: set[str] = set()

    def exclude(name: str, reason: str) -> None:
        if name not in seen_excluded and name not in seen:
            seen_excluded.add(name)
            excluded.append((name, reason))

    for ref in closure.refs:
        if ref.name in seen:
            continue
        is_local = ref.namespace is None or (local_ns is not None and ref.namespace == local_ns)
        if not is_local:
            exclude(
                ref.name,
                f"namespaced to another plugin ({ref.namespace!r}), so it cannot be verified locally "
                f"— left out of 'skills:' and routed at runtime instead",
            )
            continue
        if not ref.reachable:
            exclude(
                ref.name,
                "UNREACHABLE in the source agent (its 'Skill' tool gate is shut, so that invocation "
                "is dead). Add 'Skill' to the source's tools and re-run to include it",
            )
            continue
        if ref.resolved_path is None:
            exclude(
                ref.name,
                f"resolves in none of the {len(closure.skill_roots)} skill search root(s) — preloading it "
                f"would be a silently-dropped preload (AC1)",
            )
            continue
        reason = skill_blocks_preloading(ref.name, Path(ref.resolved_path))
        if reason is not None:
            exclude(ref.name, f"cannot be preloaded: {reason} (AC5)")
            continue
        seen.add(ref.name)
        keep.append((ref.name, ref.resolved_path))
    return keep, excluded


def convert(
    agent_path: Path,
    mode: str,
    *,
    out_dir: Path | None = None,
    name: str | None = None,
    force: bool = False,
    roots: Sequence[Path] | None = None,
    node_agent: str | None = None,
    dry_run: bool = False,
) -> ConversionResult:
    """Convert ONE source agent into the ``mode`` architecture.

    TWO PHASES, and the split is load-bearing: PHASE 1 PLANS everything (probing the
    companion skill and the menu with ``dry_run=True``), PHASE 2 writes. Nothing is
    written until every refusal condition has been decided, so a refused conversion
    leaves the tree untouched instead of half-converted — an earlier draft created
    the companion skill and only THEN refused on a shared skill, which is exactly
    the half-applied state the refusal exists to prevent.
    """
    result = ConversionResult(mode=mode, source=str(agent_path))
    if mode not in MODES:
        result.errors.append(f"unknown mode {mode!r} (expected one of: {', '.join(MODES)})")
        return result
    if not agent_path.is_file():
        result.errors.append(f"{agent_path} is not a file")
        return result

    parts = read_markdown_parts(agent_path)
    if parts is None:
        result.errors.append(f"{agent_path} could not be read or parsed")
        return result
    source_fm, source_body = parts

    plugin_root = target_plugin_root(agent_path)
    if plugin_root is None:
        result.errors.append(
            f"could not locate the plugin that owns {agent_path} (no .claude-plugin/plugin.json above "
            f"it and it does not live in an agents/ directory), so there is nowhere to ensure the "
            f"{COMPANION_SKILL_NAME!r} companion skill"
        )
        return result
    result.plugin_root = str(plugin_root)

    source_name = source_fm.get("name")
    source_stem = source_name.strip() if isinstance(source_name, str) and source_name.strip() else agent_path.stem
    agent_name = name or f"{source_stem}-{mode}"
    result.agent_name = agent_name

    # ``_validate_name`` raises SystemExit (it is a CLI guard against a --name that
    # would write OUTSIDE the plugin). Convert it to a reported error so a library
    # caller gets a result object instead of a process exit.
    from add_component import _validate_name  # noqa: PLC0415

    try:
        _validate_name(agent_name, "agent")
    except SystemExit as exc:
        result.errors.append(str(exc))
        return result

    out_parent = out_dir if out_dir is not None else plugin_root / "agents"
    out_path = out_parent / f"{agent_name}.md"
    result.agent_path = str(out_path)
    if out_path.is_file() and not force:
        result.errors.append(f"{out_path} already exists. Pass --force to overwrite.")
        return result
    if out_path.resolve() == agent_path.resolve():
        result.errors.append("the output path is the SOURCE agent — pass --name or --out")
        return result

    closure = resolve_agent_closure(agent_path, roots=roots)
    root_list = [Path(r) for r in closure.skill_roots]
    result.skill_roots = closure.skill_roots
    local_ns = plugin_namespace(find_plugin_root(agent_path))
    keep, excluded = _closure_skill_names(closure, local_ns)
    result.excluded = tuple(excluded)

    # --- PHASE 1, probe only: can the companion be ensured at all? ------------
    companion_path, companion_created, companion_error = ensure_companion_skill(plugin_root, dry_run=True)
    if companion_error:
        result.errors.append(companion_error)
        return result
    result.companion_path = str(companion_path)
    result.companion_created = companion_created

    if mode == "plugin-omni":
        menu_name, menu_created, row_added, menu_error = ensure_menu_skill(plugin_root, dry_run=True)
        if menu_error:
            result.errors.append(menu_error)
            return result
        if menu_name is None:
            # Belt and braces: ensure_menu_skill returns a name or an error, but a
            # None slipping through here would emit `skills: [null, ...]`.
            result.errors.append("the plugin's the-skills-menu skill could not be resolved or created")
            return result
        result.menu_name = menu_name
        result.menu_created = menu_created
        result.menu_row_added = row_added
        skills = [menu_name, COMPANION_SKILL_NAME]
        routed: list[RoutedSkill] = []
    else:
        if not keep:
            result.errors.append(
                "the closure of this agent contains NO preloadable skill, so the variant would be an "
                "empty shell. Every candidate is listed above with the reason it was excluded; fix "
                "those (or point --skills-root at the right skills/ directory) and re-run."
            )
            return result
        skills = [n for n, _p in keep]
        routed = route_skills(source_body, skills)
        if COMPANION_SKILL_NAME not in skills:
            skills.append(COMPANION_SKILL_NAME)
    result.skills = tuple(skills)
    result.routed = tuple(routed)

    # --- ONE-FOR-ALL: plan the IN-PLACE node conversions ---------------------
    node_texts: dict[str, str] = {}
    if mode == "one-for-all":
        skill_paths = [p for _n, p in keep]
        others = other_agents_reaching(
            plugin_root, skill_paths, exclude=[agent_path, out_path], roots=root_list
        )
        nodes: list[NodeConversion] = []
        for skill_name, skill_path in keep:
            try:
                text = Path(skill_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                result.errors.append(f"{skill_path} could not be read, so it cannot become a node: {exc}")
                return result
            plan = plan_node_frontmatter(text, node_agent=node_agent)
            if plan is None:
                result.errors.append(
                    f"{skill_path} has no YAML frontmatter block, so 'context: fork' cannot be added to "
                    f"it — a skill without frontmatter cannot become a node. Fix the skill first."
                )
                return result
            to_add, conflicts = plan
            skill_parts = read_markdown_parts(Path(skill_path))
            skill_body = "" if skill_parts is None else skill_parts[1]
            if _node_self_invokes(skill_name, skill_body, local_ns):
                result.errors.append(
                    f"{skill_path} invokes ITSELF with Skill(), and a 'context: fork' skill that "
                    f"re-invokes itself is the v2.1.145 infinite-loop antipattern (a blocking finding). "
                    f"Restructure that skill into a helper first, then re-run."
                )
                return result
            nodes.append(
                NodeConversion(
                    name=skill_name,
                    skill_md=skill_path,
                    added=tuple(to_add),
                    changed=tuple(conflicts),
                    other_agents=tuple(others.get(skill_path, ())),
                )
            )
            if to_add or conflicts:
                node_texts[skill_path] = apply_node_frontmatter(text, to_add, conflicts)
        result.nodes = tuple(nodes)

        shared_pending = [n for n in nodes if n.shared and n.needs_write]
        if shared_pending and not force:
            listing = "; ".join(
                f"{n.name} (also reached by {len(n.other_agents)} other agent(s): "
                f"{', '.join(Path(a).name for a in n.other_agents)})"
                for n in shared_pending
            )
            result.errors.append(
                "REFUSING to mutate a SHARED skill without --force. Adding 'context: fork' to a skill "
                "changes how it executes for EVERY agent that lists it, and there is no private copy to "
                f"change instead: {listing}. Re-run with --force once that is what you intend."
            )
            return result
        conflicted = [n for n in nodes if n.changed]
        if conflicted and not force:
            listing = "; ".join(
                f"{n.name}: " + ", ".join(f"{k} is {old!r}, wants {new!r}" for k, old, new in n.changed)
                for n in conflicted
            )
            result.errors.append(
                f"REFUSING to overwrite an explicit frontmatter value without --force: {listing}."
            )
            return result

    # --- notes ---------------------------------------------------------------
    tools, tools_changed = ensure_skill_tool(source_fm.get("tools"))
    disallowed, disallowed_changed = strip_skill_from_disallowed(source_fm.get("disallowedTools"))
    notes: list[str] = []
    if tools_changed:
        notes.append("`Skill` was added to `tools` — all three architectures need that gate open.")
    elif source_fm.get("tools") is None:
        notes.append(
            "The source declares no `tools:`, so this agent inherits every session tool (including "
            "`Skill`) and the gate is already open — materialising a list here would have REVOKED "
            "every other tool."
        )
    if disallowed_changed:
        notes.append(
            "`Skill` was removed from `disallowedTools` — a denied `Skill` is applied FIRST and would "
            "have made every routed invocation dead."
        )
    if out_dir is not None and plugin_root not in out_path.resolve().parents:
        # A skill name resolves relative to the AGENT's own location, so an agent
        # written outside its plugin can no longer see that plugin's skills/ and its
        # every preload reads as unresolvable.
        notes.append(
            "This agent was written OUTSIDE its plugin, so its skill names no longer resolve from "
            "its own location — move it under the plugin's agents/ directory before dispatching it, "
            "or validate it with an explicit --skills-root."
        )
    if mode == "one-for-all":
        notes.append(
            f"Each node carries `background: false`, which needs Claude Code "
            f"v{BACKGROUND_FALSE_MIN_CC_VERSION}+. Without it a forked skill returns nothing inline."
        )
    # Group the exclusions by REASON instead of one note per skill: a real agent
    # produced eight near-identical lines, which buried the notes that mattered.
    by_reason: dict[str, list[str]] = {}
    for skill_name, reason in excluded:
        by_reason.setdefault(reason, []).append(skill_name)
    for reason, names in by_reason.items():
        listed = ", ".join(f"`{n}`" for n in names)
        notes.append(f"NOT preloaded ({reason}): {listed}.")

    dropped = tuple(
        key for key in source_fm if key not in ("name", "description", "tools", "disallowedTools", "skills")
    )
    result.dropped_frontmatter = dropped
    if dropped:
        notes.append(
            "Source frontmatter key(s) NOT carried over (re-add deliberately if you need them): "
            + ", ".join(sorted(dropped))
            + ". `model:` is never carried — an agent must inherit the session model (CA-04)."
        )
    result.notes = notes

    # The manifest NAME, not the directory name: a checkout is often named
    # differently from the plugin it holds, and the manifest name is what a user
    # (and a namespaced Skill() reference) actually sees.
    scope_label = f"the `{plugin_namespace(plugin_root) or plugin_root.name}` plugin"
    text = render_variant_agent(
        mode=mode,
        agent_name=agent_name,
        scope_label=scope_label,
        source_label=source_stem,
        tools=tools,
        disallowed=disallowed,
        skills=skills,
        routed=routed,
        nodes=result.nodes,
        node_agent=node_agent,
        menu_name=result.menu_name,
        notes=notes,
    )

    # --- PHASE 2, commit: every refusal is behind us, so now we write ---------
    if not dry_run:
        # The companion goes first: the menu catalog is POPULATED from the real
        # skills/ inventory, so it can only list the companion once that exists.
        _, result.companion_created, _ = ensure_companion_skill(plugin_root)
        if mode == "plugin-omni":
            _menu, result.menu_created, result.menu_row_added, _ = ensure_menu_skill(plugin_root)
        for path, new_text in node_texts.items():
            Path(path).write_text(new_text, encoding="utf-8")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        result.written = True
        result.nodes_written = tuple(sorted(node_texts))
    return result


def _node_self_invokes(name: str, body: str, local_ns: str | None) -> bool:
    """Does this skill invoke ITSELF with ``Skill({skill: ...})``?

    Checked in BOTH the bare and the plugin-namespaced spelling, using the ONE
    promoted matcher. A ``context: fork`` skill that re-invokes itself is the
    v2.1.145 antipattern and a blocking finding, so the conversion must refuse
    rather than emit a plugin that no longer validates.
    """
    if skill_object_invocation_matcher(name).search(body):
        return True
    if local_ns and skill_object_invocation_matcher(f"{local_ns}:{name}").search(body):
        return True
    return False


# ---------------------------------------------------------------------------
# Reporting + CLI
# ---------------------------------------------------------------------------


def render_report(result: ConversionResult) -> str:
    """The human report. Numbered rows so a reader can refer to one by index."""
    lines: list[str] = []
    head = "converted" if result.written else ("planned" if result.ok else "REFUSED")
    lines.append(f"{_LOG_PREFIX} --to {result.mode}: {head}")
    if result.source:
        lines.append(f"{_LOG_PREFIX} source: {result.source}")
    if result.agent_path:
        lines.append(f"{_LOG_PREFIX} agent:  {result.agent_path}")
    if result.skill_roots:
        # The roots live HERE and never in the emitted agent — an absolute developer
        # path inside a shipped component is a hardcoded-user-path MAJOR.
        lines.append(f"{_LOG_PREFIX} skill roots: {', '.join(result.skill_roots)}")
    if result.skills:
        lines.append(f"{_LOG_PREFIX} skills: ({len(result.skills)}) {', '.join(result.skills)}")
    if result.companion_path:
        state = "created" if result.companion_created else "already present"
        lines.append(f"{_LOG_PREFIX} companion {COMPANION_SKILL_NAME}: {state} ({result.companion_path})")
    if result.menu_name:
        state = "generated from the real skills/ inventory" if result.menu_created else "already present"
        row = " (+ companion row appended)" if result.menu_row_added else ""
        lines.append(f"{_LOG_PREFIX} menu {result.menu_name}: {state}{row}")
    for i, node in enumerate(result.nodes, start=1):
        shared = (
            f"SHARED with {len(node.other_agents)} other agent(s): "
            + ", ".join(Path(a).name for a in node.other_agents)
            if node.other_agents
            else "not shared"
        )
        change = ", ".join(node.added) or "no change needed"
        if node.changed:
            change += " / conflicts: " + ", ".join(f"{k}={old!r}->{new!r}" for k, old, new in node.changed)
        lines.append(f"{_LOG_PREFIX} node {i}. {node.name}: {change} — {shared}")
    for i, (name, reason) in enumerate(result.excluded, start=1):
        lines.append(f"{_LOG_PREFIX} excluded {i}. {name}: {reason}")
    for i, note in enumerate(result.notes, start=1):
        lines.append(f"{_LOG_PREFIX} note {i}. {note}")
    for i, err in enumerate(result.errors, start=1):
        lines.append(f"{_LOG_PREFIX} ERROR {i}. {err}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert ONE agent into an ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Skills are always REFERENCED BY NAME; no mode ever copies a skill's content into an "
            "agent. See design/specs/agent-closure-and-variants.md §1.1."
        ),
    )
    parser.add_argument("agent", type=Path, help="the source agent .md to convert")
    parser.add_argument("--to", dest="mode", required=True, choices=MODES, help="target architecture")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: <plugin>/agents)")
    parser.add_argument("--name", default="", help="name of the generated agent (default: <source>-<mode>)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output AND consent to mutating a SHARED skill (one-for-all)",
    )
    parser.add_argument(
        "--skills-root",
        action="append",
        type=Path,
        default=None,
        dest="skills_roots",
        metavar="PATH",
        help="explicit skill search root (repeatable); makes the closure hermetic",
    )
    parser.add_argument(
        "--node-agent",
        default="",
        metavar="TYPE",
        help=(
            "one-for-all only: also set 'agent: TYPE' on each node (e.g. Explore, which additionally "
            "skips CLAUDE.md). OPTIONAL — 'context: fork' is what forks; 'agent:' alone does nothing."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would happen; write nothing")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON on stdout")
    args = parser.parse_args(argv)

    if args.skills_roots:
        for root in args.skills_roots:
            if not root.is_dir():
                print(f"Error: --skills-root {root} is not a directory", file=sys.stderr)
                return 2

    result = convert(
        args.agent,
        args.mode,
        out_dir=args.out,
        name=args.name or None,
        force=args.force,
        roots=args.skills_roots,
        node_agent=args.node_agent or None,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        # A refusal goes to stderr so a caller piping stdout still SEES it.
        print(render_report(result), file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
