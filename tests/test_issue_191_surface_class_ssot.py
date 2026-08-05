#!/usr/bin/env python3
"""Issue #191 — one surface classification, and the bypass the duplication hid.

Ten separately-closed FP issues shared one root cause: every detector re-derived
"what kind of file is this?", so each fix taught ONE detector about ONE kind of
file. `cpv_skillaudit_native` and `_skillaudit_markdown_context` each carried a
copy of the doc-only classifier, documented as staying in sync "via a parity test
in tests/test_skillaudit_doc_only_parity.py".

That test did not exist. The copies diverged, and the divergence was a SECURITY
BYPASS, not a cosmetic drift: the native side was fixed (audit MED #8) to strip a
literal "./" prefix; the mirror kept `str.lstrip("./")`, a CHARACTER-SET strip
that turns ".specs/evil.md" into "specs/evil.md" and so matched the `specs/`
doc-only prefix. Measured before the fix, native vs mirror:

    .specs/evil.md   False / True      <- mirror says inert documentation
    .docs/evil.md    False / True
    .doc/x.md        False / True
    .guides/x.md     False / True
    .wiki/x.md       False / True

On those paths the markdown context demoted execution-class matches and gave bash
fences the doc-only treatment — inside directories an attacker names freely.

These tests therefore assert TWO different things, and both are load-bearing: the
behaviour (dotfile dirs are not documentation) and the STRUCTURE (there is one
definition, so the behaviour cannot drift again). A behaviour-only test would
pass again the moment someone reintroduces a second copy that happens to agree
today.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cpv_surface_class as surface  # noqa: E402
from _skillaudit_markdown_context import (  # noqa: E402
    _DOC_ONLY_BASENAMES_MD,
    _DOC_ONLY_DIR_PREFIXES_MD,
    _INSTRUCTION_LOADABLE_BASENAMES_MD,
    _is_documentation_only_path_md,
    _is_instruction_loadable_path_md,
)
from cpv_skillaudit_native import (  # noqa: E402
    _DOC_ONLY_BASENAMES,
    _DOC_ONLY_DIR_PREFIXES,
    _INSTRUCTION_LOADABLE_BASENAMES,
    _is_documentation_only_path,
)

# The exact paths on which the two implementations disagreed.
BYPASS_PATHS = [
    ".specs/evil.md",
    ".docs/evil.md",
    ".doc/x.md",
    ".guides/x.md",
    ".wiki/x.md",
]


# ── The bypass ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", BYPASS_PATHS)
def test_dotfile_directories_are_not_documentation(path):
    """A dotfile directory must never be mistaken for a doc-only subtree.

    `.specs/` is not `specs/`. Treating it as one suppressed prompt-injection
    and demoted execution-class matches in a directory the plugin author — or an
    attacker — names at will.
    """
    assert _is_documentation_only_path(path) is False
    assert _is_documentation_only_path_md(path) is False
    assert _is_instruction_loadable_path_md(path) is True


@pytest.mark.parametrize("path", BYPASS_PATHS)
def test_both_entry_points_agree_on_the_bypass_paths(path):
    assert _is_documentation_only_path(path) == _is_documentation_only_path_md(path)


def test_normalize_strips_only_a_literal_dot_slash():
    """The character-set strip is the defect; pin the literal behaviour.

    `"./docs/x.md"` must lose exactly the leading `./`, and `".docs/x.md"` must
    keep its dot. `str.lstrip("./")` satisfies the first and fails the second,
    which is precisely how this shipped.
    """
    assert surface.normalize_path("./docs/x.md") == "docs/x.md"
    assert surface.normalize_path(".docs/x.md") == ".docs/x.md"
    assert surface.normalize_path(".specs/evil.md") == ".specs/evil.md"


# ── The structure: one definition, not two that agree ────────────────────────


def test_there_is_exactly_one_definition_of_each_dataset():
    """Identity, not equality.

    Equality would pass for two copies that happen to match today — the state
    that shipped the bypass. Identity can only hold while there is one object.
    """
    assert _DOC_ONLY_BASENAMES is surface.DOC_ONLY_BASENAMES
    assert _DOC_ONLY_BASENAMES_MD is surface.DOC_ONLY_BASENAMES
    assert _DOC_ONLY_DIR_PREFIXES is surface.DOC_ONLY_DIR_PREFIXES
    assert _DOC_ONLY_DIR_PREFIXES_MD is surface.DOC_ONLY_DIR_PREFIXES
    assert _INSTRUCTION_LOADABLE_BASENAMES is surface.INSTRUCTION_LOADABLE_BASENAMES
    assert _INSTRUCTION_LOADABLE_BASENAMES_MD is surface.INSTRUCTION_LOADABLE_BASENAMES


def test_there_is_exactly_one_classifier_function():
    assert _is_documentation_only_path_md is surface.is_documentation_only_path
    assert _is_instruction_loadable_path_md is surface.is_instruction_loadable_path


# ── FN-safety: the deliberate rulings must survive centralisation ────────────


@pytest.mark.parametrize(
    "path",
    [
        "skills/a/references/recipe.md",
        "references/recipe.md",
        "skills/a/reference/recipe.md",
    ],
)
def test_references_is_still_not_documentation(path):
    """`references/` is an Agent-Skills load-on-demand surface, never inert.

    A SKILL.md saying "follow the recipe in references/x.md" makes that file part
    of the agent's instruction surface; treating it as docs let an attacker hide
    the payload there and leave only a pointer.
    """
    assert surface.is_documentation_only_path(path) is False


@pytest.mark.parametrize("name", ["SKILL.md", "CLAUDE.md", "AGENTS.md"])
@pytest.mark.parametrize("where", ["", "docs/", "specs/", "wiki/", "skills/a/references/"])
def test_instruction_loadable_basenames_are_never_documentation(name, where):
    """A SKILL.md does not become inert by being filed under docs/."""
    assert surface.is_documentation_only_path(f"{where}{name}") is False


@pytest.mark.parametrize(
    "path",
    ["hooks/pre.sh", "scripts/run.py", "agents/a.md", "commands/c.md", "unknown-thing.md"],
)
def test_unknown_and_executable_surfaces_are_not_documentation(path):
    """Fail-closed: anything not positively identified as docs stays live."""
    assert surface.is_documentation_only_path(path) is False


def test_surface_class_module_imports_only_stdlib():
    """The SSOT must stay stdlib-only, or it becomes a supply-chain hole.

    `cpv_skillaudit_native` carries an iron rule: zero third-party imports, so
    its supply-chain surface is empty. Centralising the classifier meant adding
    `cpv_surface_class` to that module's import allowlist — which is only safe
    while this module is itself clean. Without this test the allowlist entry
    would be a laundering route: add `requests` here and the native scanner
    silently inherits it through an entry that was justified on the grounds that
    it could not.
    """
    body = (SCRIPTS / "cpv_surface_class.py").read_text(encoding="utf-8")
    allowed = {"__future__", "typing"}
    for match in re.finditer(r"^(?:from|import)\s+([A-Za-z_][\w.]*)", body, re.MULTILINE):
        head = match.group(1).split(".")[0]
        assert head in allowed, f"cpv_surface_class must stay stdlib-only, found '{head}'"


def test_empty_path_is_neither():
    assert surface.is_documentation_only_path("") is False
    assert surface.is_instruction_loadable_path("") is False


# ── Positive controls: real documentation must still classify as such ────────


@pytest.mark.parametrize(
    "path",
    ["README.md", "docs/guide.md", "./docs/guide.md", "CHANGELOG.md", "specs/api.md"],
)
def test_real_documentation_still_classifies_as_documentation(path):
    """The FP fixes this layer consolidates must keep working.

    Without these the bypass fix could be "achieved" by classifying nothing as
    documentation, reintroducing all ten original false positives.
    """
    assert surface.is_documentation_only_path(path) is True
