r"""Regression tests for the REGEX_DOS false positive on LINEAR regexes built
with template literals (client report, two shapes).

(a) ``install-rules.mjs``::

        const id = "[0-9A-Za-z-]+";
        new RegExp(`^v?(\\d+)…(?:-(${id}(?:\\.${id})*))?(?:\\+${id}(?:\\.${id})*)?$`)

(b) ``kinds.ts``::

        const PRRD_META_ENTRY = "[a-z][a-z0-9-]*:[^\\s,{}]+";
        new RegExp(`…(?:, *${PRRD_META_ENTRY})*$`)

Both repeat a group that starts with a mandatory literal delimiter (``.`` /
``,``) no other token in the group can match, so they are unambiguous and
linear. The proof (``_new_regexp_is_provably_linear``) resolves ``${NAME}``
against same-file ``const`` strings and clears such delimiter-anchored groups.
Every shape that is NOT provably linear must keep firing. Real scanner, no mocks.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _redos_hits(content: str, file_path: str = "scripts/x.mjs") -> list[dict]:
    """Actionable (non-suppressed) REGEX_DOS findings for ``content``."""
    return [f for f in scan_content(content, file_path) if f.get("ruleId") == "REGEX_DOS" and not f.get("suppressed")]


def _catalog_pattern_matches(content: str) -> bool:
    """True iff some REGEX_DOS catalog pattern (read from the shipped JSON) hits a line: the pre-proof baseline."""
    catalog = json.loads((SCRIPTS_DIR / "rules" / "skillaudit_patterns.json").read_text(encoding="utf-8"))
    patterns = next(r["patterns"] for r in catalog["rules"] if r["id"] == "REGEX_DOS")
    return any(re.search(p, line) for p in patterns for line in content.split("\n"))


_SEMVER_ID = 'const id = "[0-9A-Za-z-]+";\n'
_SEMVER_RE = (
    r"const SEMVER = new RegExp(`^v?(\\d+)\\.(\\d+)\\.(\\d+)"
    r"(?:-(${id}(?:\\.${id})*))?(?:\\+${id}(?:\\.${id})*)?$`);" + "\n"
)
_PRRD_ENTRY = 'const PRRD_META_ENTRY = "[a-z][a-z0-9-]*:[^\\\\s,{}]+";\n'


def test_semver_template_literal_shape_a_clears() -> None:
    """Shape (a): ``(?:\\.${id})*`` with ``const id`` resolved is linear, no REGEX_DOS."""
    src = _SEMVER_ID + _SEMVER_RE
    assert _catalog_pattern_matches(src), "baseline: the catalog REGEX_DOS pattern must hit this source"
    hits = _redos_hits(src)
    assert not hits, f"delimiter-anchored semver regex must not fire: {hits!r}"


def test_prrd_meta_entry_shape_b_multiline_clears() -> None:
    """Shape (b): ``(?:, *${PRRD_META_ENTRY})*`` spread over two lines is linear, no REGEX_DOS."""
    src = _PRRD_ENTRY + (
        "const RE = new RegExp(`^PRRD +(?:G|S)\\\\d+(?:\\\\.\\\\d+)?` +\n  `(?:, *${PRRD_META_ENTRY})*$`);\n"
    )
    assert _catalog_pattern_matches(src), "baseline: the catalog REGEX_DOS pattern must hit this source"
    hits = _redos_hits(src, "scripts/kinds.ts")
    assert not hits, f"delimiter-anchored comma-list regex must not fire: {hits!r}"


def test_prrd_meta_entry_shape_b_single_line_clears() -> None:
    """Shape (b) on one line in a ``.ts`` file is linear, no REGEX_DOS."""
    src = _PRRD_ENTRY + "const RE = new RegExp(`^x` + `(?:, *${PRRD_META_ENTRY})*$`);\n"
    assert _catalog_pattern_matches(src), "baseline: the catalog REGEX_DOS pattern must hit this source"
    hits = _redos_hits(src, "scripts/kinds.ts")
    assert not hits, f"single-line comma-list regex must not fire: {hits!r}"


def test_unresolved_interpolation_nested_quantifier_fires() -> None:
    """``(${x}+)+`` with ``x`` not defined in the file stays catastrophic and fires."""
    assert _redos_hits("const re = new RegExp(`(${x}+)+$`);\n")


def test_unresolved_interpolation_in_delimited_group_fires() -> None:
    """``(?:\\.${id})*`` with NO ``const id`` in the file cannot be proven linear: fires."""
    assert _redos_hits(r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n")


def test_resolved_atom_that_can_match_the_delimiter_fires() -> None:
    """``const id = "[.a-z]+"`` can match ``.``, so ``(?:\\.${id})*`` is ambiguous and fires."""
    src = 'const id = "[.a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_literal_nested_quantifier_still_fires() -> None:
    """A plain ``(a+)+`` template literal still fires."""
    assert _redos_hits("const re = new RegExp(`(a+)+$`);\n")


def test_optional_first_char_group_still_fires() -> None:
    """``(?:,? *${id})*``: the leading ``,`` is optional, so the group is not delimiter-anchored."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:,? *${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_redeclared_const_is_not_resolved() -> None:
    """Two ``const id`` declarations (shadowing) leave ``${id}`` unresolved, so the shape fires."""
    src = 'const id = "[a-z]+";\n{ const id = "[.a-z]+"; }\n' + r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_non_literal_const_initialiser_is_not_resolved() -> None:
    """``const id = "[a-z]" + "+"`` is not a bare string literal: unresolved, shape fires."""
    src = 'const id = "[a-z]" + "+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_alternation_in_delimited_group_fires() -> None:
    """``(?:\\.${id}|x)*`` has a top-level ``|``: never delimiter-anchored, still fires."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id}|x)*$`);" + "\n"
    assert _redos_hits(src)


def test_backreference_in_delimited_group_fires() -> None:
    """``(?:\\.${id}\\1)*`` contains a backreference escape we cannot evaluate: still fires."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id}\\1)*$`);" + "\n"
    assert _redos_hits(src)


def test_quantified_lookahead_group_fires() -> None:
    """A quantified lookahead ``(?=\\.${id})*`` is never exempted as delimiter-anchored."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?=\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_escaped_dot_inside_class_fires() -> None:
    """``const id = "[\\\\.a-z]+"`` (value ``[\\.a-z]``) matches ``.``, so the group is ambiguous."""
    src = 'const id = "[\\\\.a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_lookbehind_group_fires() -> None:
    """A quantified lookbehind ``(?<=\\.${id})*`` is never exempted as delimiter-anchored."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?<=\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_word_class_atom_matching_underscore_delimiter_fires() -> None:
    """``(?:_\\w+)*``: ``\\w`` can match the ``_`` delimiter, so the group is ambiguous and fires."""
    assert _redos_hits(r"const re = new RegExp(`^\\d+(?:_\\w+)*$`);" + "\n")


def test_whitespace_class_atom_matching_space_delimiter_fires() -> None:
    """``(?: \\s+${id})*``: ``\\s`` can match the space delimiter, so the group fires."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?: \\s+${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_class_range_covering_delimiter_fires() -> None:
    """``const id = "[,-.]+"``: the range ``,``..``.`` covers the ``.`` delimiter, so the group fires."""
    src = 'const id = "[,-.]+";\n' + r"const re = new RegExp(`^\\d+(?:\\.${id})*$`);" + "\n"
    assert _redos_hits(src)


def test_lazy_wildcard_in_delimited_group_fires() -> None:
    """``(?:,.*?${id})*``: the lazy ``.*?`` wildcard can match the ``,`` delimiter, so the group fires."""
    src = 'const id = "[a-z]+";\n' + r"const re = new RegExp(`^\\d+(?:,.*?${id})*$`);" + "\n"
    assert _redos_hits(src)
