#!/usr/bin/env python3
"""Single source of truth for "what KIND of surface is this path?" — issue #191.

Ten separately-closed false-positive issues shared one root cause: in every case
the detector was right about the bytes and wrong about the FILE. CPV answers "is
this dangerous?" per detector, and each detector re-derives — or forgets to
re-derive — "is this file even the kind of thing where that would be dangerous?"
So each fix taught ONE detector about ONE kind of file and the next detector had
to relearn it.

This module holds that classification once. It deliberately does NOT decide
severity: which rule FAMILIES apply to a surface is a security ruling that lives
with the detectors (see ``cpv_skillaudit_native``'s INTENT-class branch, which
keeps prompt-injection rules at declared severity on instruction-loadable
surfaces because prose IS the delivery vector for them). Centralising the path
question is safe; centralising the severity question would let one misclassified
path mute every detector at once.

WHY THIS MODULE EXISTS AT ALL — the duplication was not theoretical.
``cpv_skillaudit_native`` and ``_skillaudit_markdown_context`` each carried a
copy, kept in sync "via a parity test in tests/test_skillaudit_doc_only_parity.py".
**That test did not exist.** In its absence the copies diverged: the native one
was fixed (audit MED #8) to strip a literal ``./`` prefix, while the mirror kept
``str.lstrip("./")`` — a CHARACTER-SET strip that turns ``.specs/evil.md`` into
``specs/evil.md``. The mirror therefore classified five dotfile-directory shapes
as inert documentation that the native classifier correctly did not:

    .specs/evil.md  .docs/evil.md  .doc/x.md  .guides/x.md  .wiki/x.md

On those paths the markdown context demoted execution-class matches and gave
bash fences the doc-only treatment — in directories an attacker names freely.
This module ends that class of drift by construction: there is one definition,
so there is nothing to keep in sync.

This module imports nothing from CPV, which is what makes it importable from
both sides of the dispatcher without the circular import that motivated the
mirror in the first place.
"""

from __future__ import annotations

from typing import Final

# Markdown basenames Claude Code MAY load as agent instructions. Never doc-only,
# wherever they live — a SKILL.md inside references/ is still instruction-loadable.
INSTRUCTION_LOADABLE_BASENAMES: Final[frozenset[str]] = frozenset(
    {"skill.md", "claude.md", "agents.md"}
)

# Pure-documentation basenames — prose here cannot reach an agent as instructions.
DOC_ONLY_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        "readme.md",
        "changelog.md",
        "contributing.md",
        "license.md",
        "license",
        "code_of_conduct.md",
        "security.md",
        "support.md",
        "authors.md",
        "maintainers.md",
        "history.md",
        "release-notes.md",
        "releasenotes.md",
        "release_notes.md",
        "examples.md",
        "example.md",
        "usage.md",
        "commandline-usage.md",
        "commandline_usage.md",
        "cli-usage.md",
        "todo.md",
        "todos.md",
        "roadmap.md",
        "notes.md",
        "faq.md",
        "design.md",
        "architecture.md",
        "internals.md",
        "advanced.md",
        "migration.md",
        "upgrade.md",
        "troubleshooting.md",
    }
)

# Directory subtrees that are pure documentation.
#
# ``references/`` is deliberately ABSENT and must stay absent: Anthropic Agent
# Skills load ``skills/<name>/references/*.md`` on demand, so a SKILL.md saying
# "follow the recipe in references/x.md" makes that file part of the agent's
# instruction surface. Treating it as inert docs let an attacker hide an
# executable payload there and leave only a pointer in SKILL.md.
DOC_ONLY_DIR_PREFIXES: Final[tuple[str, ...]] = (
    "docs/",
    "doc/",
    "examples/",
    "example/",
    "changelog/",
    # Development-standards docs are guidelines for contributors, not runtime
    # instructions (r05 ananddtyagi FP iter1, 2026-05-27).
    "standards/",
    "standard/",
    "guides/",
    "guide/",
    "tutorials/",
    "tutorial/",
    "wiki/",
    "specs/",
    "spec/",
    "specifications/",
)


def normalize_path(file_path: str) -> str:
    """Lower-case, forward-slash a path and strip one LITERAL leading ``./``.

    The literal strip is the whole point. ``str.lstrip("./")`` strips a CHARACTER
    SET, so it eats every leading ``.`` and ``/`` — turning ``.specs/evil.md``
    into ``specs/evil.md`` and making an attacker-named dotfile directory match
    the doc-only prefixes. Any future normalisation belongs here and nowhere
    else, so a fix cannot again reach one caller and miss another.
    """
    norm = file_path.replace("\\", "/").lower()
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def is_documentation_only_path(file_path: str) -> bool:
    """True iff ``file_path`` is a pure-documentation surface.

    Conservative by construction: a path is documentation-only when its basename
    is on the doc-only allowlist OR it sits under a doc-only subtree, AND its
    basename is not instruction-loadable. Everything else — unknown ``.md`` at
    plugin root, ``agents/``, ``commands/``, ``.claude/rules/`` — is NOT
    doc-only, so the detectors' normal behaviour stands. Unknown means "treat as
    live", which is the fail-closed direction.
    """
    if not file_path:
        return False
    norm = normalize_path(file_path)
    if not norm:
        return False
    basename = norm.split("/")[-1]
    if basename in INSTRUCTION_LOADABLE_BASENAMES:
        return False
    if basename in DOC_ONLY_BASENAMES:
        return True
    return any(norm.startswith(p) or ("/" + p) in ("/" + norm) for p in DOC_ONLY_DIR_PREFIXES)


def is_instruction_loadable_path(file_path: str) -> bool:
    """True iff ``file_path`` MAY be loaded as agent instructions.

    The exact complement of :func:`is_documentation_only_path` for non-empty
    paths. An empty path is neither — it is not a surface at all.
    """
    if not file_path:
        return False
    return not is_documentation_only_path(file_path)
