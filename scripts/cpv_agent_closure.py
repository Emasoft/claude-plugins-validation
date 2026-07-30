#!/usr/bin/env python3
"""Agent → skill CLOSURE resolution — the SSOT (TRDD-7KS7KP7U, spec §2).

The gap this exists to close, probed first-hand against v3.24.0: an agent
declaring ``skills: [real-skill, totally-nonexistent-skill-xyz]`` and invoking
``Skill({skill: "another-nonexistent-skill-abc"})`` in its body scored 100/100
with ZERO findings. ``validate_agent.py`` is a single-FILE validator with no
plugin root, so it structurally could not resolve a skill NAME, and
``validate_xref.py`` only matches ``skills/<name>`` PATH shapes and blanks
frontmatter out of its body scan. So an agent's ``skills:`` list was
existence-checked by nothing: a preload that silently does nothing and a runtime
invocation that silently fails both shipped green.

This module answers ONE question: **which skills does this agent actually
reach, and where does each one live?** It emits no findings — the callers
(``validate_agent`` for AC1–AC4, ``cpv_agent_security`` for the scan set,
``convert_agent`` for inlining, ``cpv_agent_eval`` for the cost model) decide
what to do with the answer. Keeping the resolver finding-free is what lets four
workstreams share one definition of "the closure".

THE LOAD-BEARING CORRECTNESS POINT (spec §1). ``skills:`` frontmatter is a
PRE-LOAD HINT, not an ACL, so "the skills accessible to an agent" is NOT the
``skills:`` list:

============  ==========================================  ==========================
origin        definition                                  reachable when
============  ==========================================  ==========================
``preload``   a name in ``skills:`` frontmatter           ALWAYS (injected at start)
``runtime``   a ``Skill(...)`` invocation in the body     only if the ``Skill`` gate
                                                          is open
``transitive`` a skill invoked from a reachable skill     its parent is reachable
                                                          AND the gate is open
============  ==========================================  ==========================

Getting the gate backwards would flag the CORRECT dynamic-router pattern as a
defect: a runtime ``Skill()`` load is the RIGHT thing for a router (v3.18.0
verified that ``skills:`` frontmatter injects a skill's FULL content into every
invocation, so loading on demand is cheaper, not a cache hazard).

Reuse, never reimplement:

* tool-token parsing / normalisation → ``cpv_tool_permission_match``;
* the fence parser → ``cpv_tool_permission_match.iter_fenced_blocks`` (the ONE
  fence tracker in the codebase — this module only PROJECTS its blocks onto line
  numbers, it does not parse fences);
* the frontmatter parser → ``validate_agent.parse_frontmatter`` (deferred import,
  see ``_read_markdown_parts``);
* the ``Skill({skill: "<name>"})`` grammar was PROMOTED here from
  ``validate_skill_comprehensive`` and that validator now imports it back, so
  exactly one grammar exists.

Fail-safe on I/O everywhere: an unreadable or non-UTF-8 file yields no
reference, never an exception. A resolver that raises would take down a whole
directory scan over one bad file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cpv_tool_permission_match import (
    declared_tool_names,
    granted_builtin_tools,
    iter_fenced_blocks,
    parse_declared_tools,
)

# ---------------------------------------------------------------------------
# The `Skill(...)` invocation grammar — the ONE definition
# ---------------------------------------------------------------------------

# A skill-name token: one flat character run, ONE quantifier, no nesting. Written
# this way on purpose — the obvious "kebab" spelling
# ``[A-Za-z0-9]+(?:[.:_-][A-Za-z0-9]+)+`` nests a ``+`` inside a ``+`` and is
# exactly the catastrophic-backtracking SHAPE CPV's own REGEX_DOS rule flags. The
# flat form has no ambiguity about which character belongs to which repetition,
# so it is linear by construction. The shape requirement the nested form encoded
# is enforced in CODE instead, by ``_bare_token_is_a_skill_name``.
_SKILL_NAME_TOKEN = r"[A-Za-z0-9][A-Za-z0-9_.:-]*"

# Separators that make a BARE ``Skill(<name>)`` token a plausible skill name.
# A bare single word is indistinguishable from English — ``Skill(s) are loaded
# dynamically`` would otherwise extract the "skill" ``s`` and then report that it
# does not exist, a fabricated finding on ordinary prose. Skill names are
# kebab-case by convention (``validate_component_name`` enforces it) and the
# namespaced form always carries a ``:``, so requiring one separator costs
# nothing real; a genuinely single-word name is still caught by the object form,
# which is the DOCUMENTED invocation shape.
_BARE_NAME_SEPARATORS = frozenset(".:_-")


def _bare_token_is_a_skill_name(token: str) -> bool:
    """Is a BARE ``Skill(<token>)`` capture plausibly a skill name?

    See :data:`_BARE_NAME_SEPARATORS` for why one separator is required.
    """
    return any(ch in _BARE_NAME_SEPARATORS for ch in token)


# ``Skill({skill: "<name>"})`` / ``skill: '<name>'``. Permissive on the name: the
# explicit ``skill:`` key is unambiguous evidence that the value IS a skill name,
# so a single-word name still resolves here. The negative lookbehind rejects a
# hyphen/word char before ``skill`` so a monitor target (``on-skill-invoke: "x"``)
# and a ``skills:`` list line can never match. Case-insensitive to match the
# promoted original.
_SKILL_OBJECT_REF_RE = re.compile(
    r"(?<![\w-])skill\s*:\s*[\"'](" + _SKILL_NAME_TOKEN + r")[\"']",
    re.IGNORECASE,
)

# ``Skill(cpv:cpv-fix-validation)`` / ``Skill("my-plugin:my-skill --json")``.
# Trailing arguments are allowed (the prompt convention is
# ``Skill(plugin:skill <ARGUMENTS>)``); the argument tail is ONE whitespace char
# followed by a flat non-``)`` run, never ``\\s+[^)]*`` — those two overlap on
# whitespace, which is quadratic on an adversarial line for no gain. The
# lookbehind mirrors ``cpv_tool_permission_match._TOOL_CALL_RE`` so a markdown
# link, a namespaced call (``ns::Skill(``) and a hyphenated identifier don't
# produce a match.
_SKILL_BARE_CALL_RE = re.compile(
    r"(?<![\w.\]:>-])Skill\(\s*[\"']?(" + _SKILL_NAME_TOKEN + r")[\"']?(?:\s[^)\n]*)?\)",
)

#: How deep the transitive walk goes by default (agent refs are depth 1).
DEFAULT_MAX_DEPTH = 3

#: Bounded walk when looking for the plugin manifest above an agent file.
_MANIFEST_WALK_LIMIT = 6


def skill_object_invocation_matcher(name: str) -> re.Pattern[str]:
    """Compile the ``Skill({skill: "<name>"})`` matcher for ONE skill name.

    PROMOTED from ``validate_skill_comprehensive._check_context_fork_self_recursion``
    so the ``skill:``-key grammar has exactly one definition; that validator
    imports this function back.

    ``name`` is escaped, so a name containing a regex metacharacter (``a.b``)
    matches literally instead of turning ``.`` into a wildcard.

    NOTE: the ``/<name>`` slash-command form is deliberately NOT matched, here or
    anywhere else. A bare ``/<name>`` regex false-fires on ordinary prose paths
    and option lists, which is exactly why the original grammar excluded it.
    """
    return re.compile(r"skill\s*:\s*[\"']" + re.escape(name) + r"[\"']", re.IGNORECASE)


def split_skill_ref_name(token: str) -> tuple[str, str | None]:
    """Split a reference token into ``(bare name, namespace or None)``.

    ``"cpv:cpv-fix-validation"`` → ``("cpv-fix-validation", "cpv")``;
    ``"plain-skill"`` → ``("plain-skill", None)``. Only the LAST ``:`` separates,
    so a name is never truncated by an extra colon.
    """
    token = token.strip()
    if ":" not in token:
        return token, None
    namespace, _, bare = token.rpartition(":")
    namespace = namespace.strip()
    bare = bare.strip()
    if not namespace or not bare:
        # A leading/trailing colon is malformed — treat the whole token as the
        # name so the caller reports "does not resolve" rather than silently
        # dropping the reference.
        return token, None
    return bare, namespace


# ---------------------------------------------------------------------------
# The pinned data model (spec §2 — do NOT rename a field or change a type)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillRef:
    """One reference from an agent (or a reachable skill) to a skill."""

    name: str
    """kebab-case name AS WRITTEN (namespace stripped)."""

    namespace: str | None
    """``"<plugin>"`` from ``"<plugin>:<skill>"``, else None."""

    origin: str
    """``"preload"`` | ``"runtime"`` | ``"transitive"``."""

    source_file: str
    """Absolute path of the file holding the reference."""

    line: int
    """1-based line IN ``source_file``; 0 for a frontmatter reference."""

    resolved_path: str | None
    """Absolute path to its SKILL.md, else None."""

    reachable: bool
    """False iff ``origin != "preload"`` and the ``Skill`` gate is shut."""


@dataclass(frozen=True)
class AgentClosure:
    """Everything one agent can reach, plus the evidence for that verdict."""

    agent_path: str
    skill_roots: tuple[str, ...]
    can_load_at_runtime: bool
    tools_declared: tuple[str, ...] | None
    """None == no ``tools:`` field (the agent inherits every session tool)."""
    refs: tuple[SkillRef, ...]
    ambient: tuple[str, ...]
    max_depth_reached: int


# ---------------------------------------------------------------------------
# Search roots + the skill inventory
# ---------------------------------------------------------------------------


def _resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def find_plugin_root(agent_path: Path) -> Path | None:
    """Nearest ancestor of ``agent_path`` carrying ``.claude-plugin/plugin.json``.

    None when there is none — a manifest-less source is NOT an error: CPV must
    validate an uninstalled, marketplace-less plugin source, and
    :func:`skill_search_roots` has a separate ``agents/`` sibling fallback for
    exactly that case.
    """
    here = _resolve(agent_path)
    if here is None:
        return None
    cursor = here.parent
    for _ in range(_MANIFEST_WALK_LIMIT):
        try:
            if (cursor / ".claude-plugin" / "plugin.json").is_file():
                return cursor
        except OSError:
            return None
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return None


def plugin_namespace(plugin_root: Path | None) -> str | None:
    """The plugin's declared ``name``, used to tell a LOCAL namespace from a
    FOREIGN one. None when unknown — which callers must treat as "every
    namespaced reference is foreign", the FP-safe direction (a foreign reference
    may legitimately live in another installed plugin, so it must not produce a
    finding)."""
    if plugin_root is None:
        return None
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _project_root_for(agent_path: Path) -> Path | None:
    """The parent of the nearest ancestor directory literally named ``.claude``.

    That is the shape of a project-scope agent (``<proj>/.claude/agents/x.md``),
    whose sibling skill root is ``<proj>/.claude/skills``.
    """
    here = _resolve(agent_path)
    if here is None:
        return None
    for ancestor in here.parents:
        if ancestor.name == ".claude":
            return ancestor.parent
    return None


def skill_search_roots(
    agent_path: Path,
    *,
    plugin_root: Path | None = None,
    project_root: Path | None = None,
    home: Path | None = None,
) -> list[Path]:
    """Resolve the skill directories in scope for one agent, HIGHEST PRECEDENCE
    FIRST.

    Order (each entry kept only when it is an existing directory, de-duplicated
    by RESOLVED path so a symlinked or repeated location is never scanned twice):

    1. ``<plugin_root>/skills`` — the plugin shipping the agent;
    2. ``<parent-of-agents>/skills`` — the same directory for a manifest-LESS
       source (a pre-publish tree has no marketplace and may have no manifest);
    3. ``<project_root>/.claude/skills`` — project scope;
    4. ``<plugin_root>/.claude/skills`` — a plugin source that also carries
       project-scope skills;
    5. ``<home>/.claude/skills`` — user scope, which applies to every session on
       this machine and is therefore genuinely in scope (same reasoning as
       ``cpv_agent_preflight.resolve_agent_dirs`` including ``~/.claude/agents``).

    Root 5 makes auto-resolution MACHINE-DEPENDENT, and that is a deliberate
    trade: a name that resolves produces no finding, so the common effect is
    fewer findings on the developer's box than in CI. A caller that needs a
    hermetic answer passes explicit roots (``--skills-root``), which suppresses
    auto-resolution entirely.
    """
    resolved_agent = _resolve(agent_path) or agent_path
    pr = plugin_root if plugin_root is not None else find_plugin_root(resolved_agent)
    proj = project_root if project_root is not None else _project_root_for(resolved_agent)

    candidates: list[Path] = []
    if pr is not None:
        candidates.append(pr / "skills")
    parent = resolved_agent.parent
    if parent.name.lower() == "agents":
        candidates.append(parent.parent / "skills")
    if proj is not None:
        candidates.append(proj / ".claude" / "skills")
    if pr is not None:
        candidates.append(pr / ".claude" / "skills")
    candidates.append((home if home is not None else Path.home()) / ".claude" / "skills")

    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        real = _resolve(candidate)
        if real is None or real in seen:
            continue
        try:
            if not real.is_dir():
                continue
        except OSError:
            continue
        seen.add(real)
        roots.append(real)
    return roots


def _read_skill_md_declared_name(skill_md: Path) -> str | None:
    """The ``name`` frontmatter value of a SKILL.md, or None.

    Fail-safe: any read or parse error yields None. The SKILL validator is the
    surface that reports a malformed skill; discovery must never crash on one.
    """
    parts = _read_markdown_parts(skill_md)
    if parts is None:
        return None
    frontmatter, _, _ = parts
    name = frontmatter.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def available_skills(roots: Sequence[Path]) -> dict[str, Path]:
    """Map every skill NAME present in ``roots`` to its ``SKILL.md`` path.

    A skill is ``<root>/<name>/SKILL.md`` (the layout ``validate_plugin``'s
    ``_discover_plugin_skills`` already treats as canonical), plus a root that IS
    itself a skill directory (``<root>/SKILL.md``, the CC v2.1.142 root-level
    skill shape).

    Each skill is indexed under its DIRECTORY name and — when it differs — under
    its frontmatter ``name`` as well. Indexing both spellings can only ever make
    a reference RESOLVE, never fail: the mismatch itself is the skill validator's
    finding, and duplicating it here as "this skill does not exist" would be a
    fabricated finding on a real, present skill.

    Earlier roots win, mirroring the precedence order of
    :func:`skill_search_roots`.
    """
    index: dict[str, Path] = {}
    for root in roots:
        try:
            if not root.is_dir():
                continue
            entries = sorted(root.iterdir())
        except OSError:
            continue

        root_skill_md = root / "SKILL.md"
        try:
            if root_skill_md.is_file():
                declared = _read_skill_md_declared_name(root_skill_md) or root.name
                index.setdefault(declared, root_skill_md)
        except OSError:
            pass

        for entry in entries:
            skill_md = entry / "SKILL.md"
            try:
                if not entry.is_dir() or not skill_md.is_file():
                    continue
            except OSError:
                continue
            index.setdefault(entry.name, skill_md)
            alias = _read_skill_md_declared_name(skill_md)
            if alias and alias != entry.name:
                index.setdefault(alias, skill_md)
    return index


# ---------------------------------------------------------------------------
# The `Skill` tool gate + reference extraction
# ---------------------------------------------------------------------------


def agent_can_load_skills_at_runtime(frontmatter: dict[str, Any]) -> bool:
    """Can this agent invoke the ``Skill`` tool at runtime? (spec §1)

    Straight from ``sub-agents.md`` ("Preload skills into subagents"): *"To
    prevent a subagent from invoking skills entirely, omit ``Skill`` from the
    ``tools`` list **or add it to ``disallowedTools``**."*

    1. ``Skill`` in ``disallowedTools`` → **False**, whatever ``tools`` says.
       ``disallowedTools`` is applied FIRST, so deny wins; an agent with NO
       ``tools`` field but ``disallowedTools: [Skill]`` has a SHUT gate, and
       reading it as open would mis-classify every runtime ref as reachable.
    2. else no ``tools:`` field → **True** (the agent inherits every session tool).
    3. else ``tools`` names ``Skill`` → **True**. The tool NAME is what counts, so
       a specifier-carrying ``Skill(...)`` rule grants it too.
    4. else → **False** — every runtime invocation in that body is DEAD and a
       ``skills:`` preload is the agent's only skill access.

    A ``tools:`` key whose VALUE is null parses as absent, which is the FP-safe
    reading: an open gate raises no finding, while wrongly calling the gate shut
    would flag a legitimate dynamic router.

    The docs also confirm the reachability model this gate serves: *"This field
    [``skills``] controls which skills are preloaded, not which skills the
    subagent can access: without it, the subagent can still discover and invoke
    project, user, and plugin skills through the Skill tool during execution."*
    So ``skills:`` is a PRE-LOAD HINT and the gate — not the list — decides
    runtime reach.
    """
    disallowed = parse_declared_tools(frontmatter.get("disallowedTools"))
    if disallowed and "Skill" in declared_tool_names(disallowed):
        return False
    rules = parse_declared_tools(frontmatter.get("tools"))
    if rules is None:
        return True
    return "Skill" in granted_builtin_tools(rules)


#: Bundled skills that can NEVER be preloaded. ``sub-agents.md``: *"This includes
#: the bundled ``/verify`` and ``/code-review`` skills: only you can run them, so
#: they can't be preloaded either."*
NEVER_PRELOADABLE_SKILLS: frozenset[str] = frozenset({"verify", "code-review"})

# The TRUE spellings of a YAML frontmatter boolean. The full set of ACCEPTED
# spellings is ``cpv_validation_common._FRONTMATTER_BOOL_STRINGS``; there is no
# public truthiness helper there, and a strict ``is True`` test would miss
# ``disable-model-invocation: "yes"``, which Claude Code accepts.
_FRONTMATTER_TRUE_STRINGS: frozenset[str] = frozenset({"true", "yes", "on", "1"})


def _frontmatter_is_true(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _FRONTMATTER_TRUE_STRINGS
    return False


def skill_disables_model_invocation(skill_md: Path | None) -> bool:
    """Does this skill's own frontmatter set ``disable-model-invocation`` true?

    The POSITIVE, file-based half of the AC5 test, exposed separately so a caller
    can tell it apart from the name-based bundled-skill inference: the two have
    different confidence and therefore different severities.

    Fail-safe: None / unreadable / unparseable → False.
    """
    if skill_md is None:
        return False
    parts = _read_markdown_parts(skill_md)
    if parts is None:
        return False
    frontmatter, _, _ = parts
    return _frontmatter_is_true(frontmatter.get("disable-model-invocation"))


def skill_blocks_preloading(name: str, skill_md: Path | None) -> str | None:
    """Why this skill cannot be PRELOADED, or None when it can be.

    ``sub-agents.md``: *"You can't preload skills that set
    ``disable-model-invocation: true``, since preloading draws from the same set
    of skills Claude can invoke."* Such a preload silently does nothing, which is
    exactly the failure class this module exists to catch.

    Fail-safe: an unreadable skill yields None (no reason found), so a preload is
    never called un-preloadable on the strength of an I/O error.

    The FLAG is checked BEFORE the bundled-name list, deliberately. The flag is
    positive, file-based proof, whereas the bundled-name rule is an inference from
    the name alone — and a plugin that ships its OWN ``skills/verify/`` has a name
    COLLISION with the bundled skill, not necessarily a broken preload. Checking
    the flag first means a locally-resolved skill is judged on its own frontmatter,
    and the caller can tell the two cases apart by whether the reference resolved.
    """
    if skill_disables_model_invocation(skill_md):
        return (
            "its own frontmatter sets 'disable-model-invocation: true', and preloading draws "
            "from the same set of skills Claude can invoke"
        )
    if name in NEVER_PRELOADABLE_SKILLS:
        return (
            f"{name!r} is the name of a BUNDLED user-only skill (/verify, /code-review): only the "
            "user can run those, so they can never be preloaded"
        )
    return None


def extract_preloaded_skill_names(frontmatter: dict[str, Any]) -> list[str]:
    """The ``skills:`` frontmatter names, in order, de-duplicated.

    Non-list values and non-string / blank items are skipped: the ``skills``
    field's own type validation reports the malformed shape, and a resolver that
    raised on it would take down the whole scan.
    """
    value = frontmatter.get("skills")
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        token = item.strip()
        if token and token not in names:
            names.append(token)
    return names


def _in_fence_lines(body: str) -> set[int]:
    """1-based body line numbers that sit inside a fenced code block.

    Projected from :func:`iter_fenced_blocks` — the ONE fence parser. A
    ``Skill(...)`` inside a fence is an ILLUSTRATION ("here is how you would
    invoke it"), not an invocation, so it must never become a finding.
    """
    flagged: set[int] = set()
    for block in iter_fenced_blocks(body):
        flagged.update(range(block.open_line, block.close_line + 1))
    return flagged


def body_mentions_skill_name(body: str, name: str) -> bool:
    """Does ``name`` appear anywhere in ``body`` OUTSIDE every fenced block?

    This is the "is this preload actually used?" test (AC4), and it deliberately
    accepts a BARE MENTION rather than only a ``Skill()`` call. An ALL-IN-ONE
    agent preloads every skill it needs and routes to them from a prose table or
    a choice-branch list — a row like ``| cpv-fix-validation | when a finding is
    mechanical |`` IS genuine usage. Requiring a ``Skill()`` call would make the
    advisory express an architecture preference instead of the token-economy fact
    it reports (and would warn on CPV's own canonical output).

    A mention inside a fenced block does NOT count: a fence is an illustration,
    exactly as it is for :func:`extract_runtime_skill_refs`.

    Boundaries are hyphen-aware — ``foo-skill`` is not "mentioned" by
    ``my-foo-skill``, because a plain ``\\b`` treats the ``-`` as a boundary and
    would silently accept a DIFFERENT skill's name as evidence of usage.
    Matching is case-insensitive: a sentence-initial capitalisation is still a
    mention, and over-matching here only ever SUPPRESSES an advisory, never
    fabricates one.
    """
    if not name:
        return False
    pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])", re.IGNORECASE)
    fenced = _in_fence_lines(body)
    for idx, line in enumerate(body.splitlines(), start=1):
        if idx in fenced:
            continue
        if pattern.search(line):
            return True
    return False


def extract_runtime_skill_refs(body: str) -> list[tuple[str, int]]:
    """Every ``Skill(...)`` reference in ``body`` as ``(token, 1-based line)``.

    ``token`` is the name AS WRITTEN, **namespace included** — use
    :func:`split_skill_ref_name` to separate them. Returning the raw token keeps
    the namespace available to the caller; discarding it here would lose the one
    piece of information that distinguishes a local reference from a foreign one.

    Fenced blocks are excluded (see :func:`_in_fence_lines`).
    """
    fenced = _in_fence_lines(body)
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def record(token: str, line_no: int) -> None:
        key = (token, line_no)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    for idx, line in enumerate(body.splitlines(), start=1):
        if idx in fenced:
            continue
        for match in _SKILL_OBJECT_REF_RE.finditer(line):
            record(match.group(1), idx)
        for match in _SKILL_BARE_CALL_RE.finditer(line):
            token = match.group(1)
            # The bare form's shape guard lives here rather than in the regex —
            # see _BARE_NAME_SEPARATORS. Without it, `Skill(s) are loaded` would
            # yield the "skill" `s` and then a fabricated "does not exist".
            if _bare_token_is_a_skill_name(token):
                record(token, idx)
    return refs


# ---------------------------------------------------------------------------
# Closure resolution
# ---------------------------------------------------------------------------


def _read_markdown_parts(path: Path) -> tuple[dict[str, Any], str, int] | None:
    """``(frontmatter, body, 1-based line of the closing ``---``)`` or None.

    Deferred import of ``validate_agent.parse_frontmatter`` so this module has
    ONE frontmatter parser without a module-level import cycle (``validate_agent``
    imports this module at module scope; by the time any function here runs,
    both modules are fully initialised, whichever was imported first).

    None on ANY read/decode failure — the fail-safe contract: an unreadable file
    yields no reference, never an exception.
    """
    from validate_agent import parse_frontmatter  # noqa: PLC0415

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        frontmatter, body, fm_end_line = parse_frontmatter(content)
    except (yaml.YAMLError, ValueError):
        return None
    if frontmatter is None:
        # No (or unparseable) frontmatter: the BODY is still scannable, and a
        # runtime invocation in it is just as real.
        return {}, content, 0
    if not isinstance(frontmatter, dict):
        # Frontmatter that is a scalar or a list — the component validator
        # reports the malformed shape; treat it as "no fields here".
        return {}, body, fm_end_line
    return frontmatter, body, fm_end_line


def _make_ref(
    token: str,
    *,
    origin: str,
    source_file: Path,
    line: int,
    index: dict[str, Path],
    reachable: bool,
) -> SkillRef:
    name, namespace = split_skill_ref_name(token)
    resolved = index.get(name)
    return SkillRef(
        name=name,
        namespace=namespace,
        origin=origin,
        source_file=str(source_file),
        line=line,
        resolved_path=str(resolved) if resolved is not None else None,
        reachable=reachable,
    )


def resolve_agent_closure(
    agent_path: Path,
    *,
    roots: Sequence[Path] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> AgentClosure:
    """Resolve everything ``agent_path`` can reach.

    ``roots=None`` means "resolve them" via :func:`skill_search_roots`. A caller
    that passes ``roots=[]`` deliberately gets an EMPTY inventory and therefore
    an unresolved reference for every name — that state is precisely what the
    caller-side non-vacuity guard exists to handle (a MAJOR "this skill does not
    exist" would be fabricated when the roots, not the agent, are wrong).

    The walk is depth-bounded (agent-level references are depth 1) and
    cycle-safe: a skill is EXPANDED at most once, keyed on its resolved
    ``SKILL.md`` path, so ``A → B → A`` terminates while still RECORDING both
    references.
    """
    root_list = list(roots) if roots is not None else skill_search_roots(agent_path)
    index = available_skills(root_list)
    ambient = tuple(sorted(index))

    parts = _read_markdown_parts(agent_path)
    frontmatter: dict[str, Any] = {} if parts is None else parts[0]
    body = "" if parts is None else parts[1]
    fm_end_line = 0 if parts is None else parts[2]

    gate_open = agent_can_load_skills_at_runtime(frontmatter)
    declared = parse_declared_tools(frontmatter.get("tools"))
    tools_declared = None if declared is None else tuple(declared)

    refs: list[SkillRef] = []
    max_depth_reached = 0
    # Frontier entries are (ref, depth) for refs whose target may be expanded.
    frontier: list[tuple[SkillRef, int]] = []

    for token in extract_preloaded_skill_names(frontmatter):
        # A preload is injected at startup, so it is reachable regardless of the
        # `Skill` grant. line=0 marks "this reference lives in the frontmatter".
        ref = _make_ref(token, origin="preload", source_file=agent_path, line=0, index=index, reachable=True)
        refs.append(ref)
        frontier.append((ref, 1))

    for token, body_line in extract_runtime_skill_refs(body):
        ref = _make_ref(
            token,
            origin="runtime",
            source_file=agent_path,
            line=body_line + fm_end_line,
            index=index,
            reachable=gate_open,
        )
        refs.append(ref)
        frontier.append((ref, 1))

    if refs:
        max_depth_reached = 1

    expanded: set[str] = set()
    while frontier:
        parent_ref, depth = frontier.pop(0)
        if parent_ref.resolved_path is None or depth >= max_depth:
            continue
        if parent_ref.resolved_path in expanded:
            continue
        expanded.add(parent_ref.resolved_path)

        skill_md = Path(parent_ref.resolved_path)
        child_parts = _read_markdown_parts(skill_md)
        if child_parts is None:
            continue
        _, child_body, child_fm_end = child_parts
        child_depth = depth + 1
        for token, body_line in extract_runtime_skill_refs(child_body):
            child = _make_ref(
                token,
                origin="transitive",
                source_file=skill_md,
                line=body_line + child_fm_end,
                index=index,
                # A transitive skill is loaded by the `Skill` tool from inside a
                # reachable parent, so BOTH conditions must hold.
                reachable=gate_open and parent_ref.reachable,
            )
            refs.append(child)
            max_depth_reached = max(max_depth_reached, child_depth)
            frontier.append((child, child_depth))

    return AgentClosure(
        agent_path=str(agent_path),
        skill_roots=tuple(str(r) for r in root_list),
        can_load_at_runtime=gate_open,
        tools_declared=tools_declared,
        refs=tuple(refs),
        ambient=ambient,
        max_depth_reached=max_depth_reached,
    )


#: Subdirectories of a skill that ship executable / instruction content and are
#: therefore part of the scan set (spec §4).
CLOSURE_SUBDIRS: tuple[str, ...] = ("references", "scripts")


def _iter_subdir_files(skill_dir: Path) -> Iterable[Path]:
    for name in CLOSURE_SUBDIRS:
        sub = skill_dir / name
        try:
            if not sub.is_dir():
                continue
            for path in sorted(sub.rglob("*")):
                if path.is_file():
                    yield path
        except OSError:
            continue


def closure_files(closure: AgentClosure) -> list[Path]:
    """Every file a REACHABLE skill of ``closure`` ships: its ``SKILL.md`` plus
    its ``references/**`` and ``scripts/**``.

    Unreachable references are excluded — they cannot execute, so they are not
    part of this set. Callers that must still ACCOUNT for them (the security
    scanner reports them in a separate ``unreachable`` section, because "cannot
    reach" is not "clean") read ``closure.refs`` directly.

    Sorted and de-duplicated, so the result is a stable scan order.
    """
    files: set[Path] = set()
    for ref in closure.refs:
        if not ref.reachable or ref.resolved_path is None:
            continue
        skill_md = Path(ref.resolved_path)
        try:
            if not skill_md.is_file():
                continue
        except OSError:
            continue
        files.add(skill_md)
        files.update(_iter_subdir_files(skill_md.parent))
    return sorted(files)
