"""Regression tests — issue #171: a cSpell CONFIG JSON word-list FP'd as
agent-manipulation (TOOL_SHADOW), turning a clean repo into a blocking MAJOR.

`standardize --fix` writes a `.cspell.json` whose `words` array holds real tokens
(e.g. pytest's `monkeypatch`). CPV's own skillaudit `agent_manipulation
TOOL_SHADOW` detector — via its bare-word `monkey.?patch` pattern — then fired a
publish-blocking MAJOR on a spellcheck dictionary word. Self-inflicted: the fix
command breaks the gate. A cSpell word cannot shadow a tool / exec / exfil /
inject, so this is a categorical false positive.

Fix: a new `_skillaudit_json_context.is_cspell_json_words_entry` recognises a
cSpell config JSON(C) file and clears the `_BINARY_INAPPLICABLE_RULES` family
(prompt-injection, intent, A2A / tool / memory manipulation incl. TOOL_SHADOW,
ReDoS, invisible-unicode) — but ONLY on a string ELEMENT of a word-list array
(`words` / `ignoreWords` / `flagWords` / `userWords`, incl. `overrides[].<>`).
This is the structured-JSON sibling of the `.txt`/`.dict` carve-out (issue #171's
predecessor `_is_cspell_dictionary`), scoped to the word arrays only.

TWO-SIDED / FN-safe:
  * the TOOL_SHADOW FP clears on a cSpell config's `words` element, AND
  * any OTHER cSpell field (an `ignorePaths` glob) still fires, AND
  * the SAME word in a NON-cSpell `.json` (`config.json` / `settings.json`) still
    fires (the carve-out is cSpell-basename-scoped, not word-scoped), AND
  * an exfil/secret rule (URL_SUSPICIOUS / SECRET_*) on a cSpell word still fires
    (those rules are NOT in the suppressed set), AND
  * a real tool-shadow payload in an EXECUTABLE (`.js`) still fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _skillaudit_json_context import is_cspell_json_words_entry  # noqa: E402
from cpv_skillaudit_native import (  # noqa: E402
    _BINARY_INAPPLICABLE_RULES,
    _context_classifier_verdict,
    run_skillaudit_scan,
)


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


# A cSpell config with one entry per line so line indices are unambiguous.
#   0 '{'
#   1 '  "version": "0.2",'
#   2 '  "words": ['
#   3 '    "monkeypatch",'
#   4 '    "pyproject"'
#   5 '  ],'
#   6 '  "ignorePaths": ['
#   7 '    "/etc/hosts/thing"'
#   8 '  ],'
#   9 '  "flagWords": ['
#  10 '    "teh"'
#  11 '  ]'
#  12 '}'
_CSPELL_SRC = (
    "{\n"
    '  "version": "0.2",\n'
    '  "words": [\n'
    '    "monkeypatch",\n'
    '    "pyproject"\n'
    "  ],\n"
    '  "ignorePaths": [\n'
    '    "/etc/hosts/thing"\n'
    "  ],\n"
    '  "flagWords": [\n'
    '    "teh"\n'
    "  ]\n"
    "}"
)
_OVERRIDES_SRC = '{"overrides":[{"filename":"*.md","words":["frobnicate"]}]}'


class TestCspellJsonWordsRecogniser:
    """`is_cspell_json_words_entry` clears ONLY a word-list ARRAY element of a
    cSpell config file, nothing else."""

    @pytest.mark.parametrize(
        "file_path,line_idx",
        [
            ("/x/.cspell.json", 3),  # words[0] "monkeypatch" — the reported FP
            ("/x/.cspell.json", 4),  # words[1] "pyproject"
            ("/x/.cspell.json", 10),  # flagWords[0] "teh"
            ("proj/cspell.json", 3),  # a different recognised basename
        ],
    )
    def test_word_array_element_is_recognised(self, file_path: str, line_idx: int) -> None:
        """A string element of a cSpell word-list array is a benign dictionary entry."""
        assert is_cspell_json_words_entry(file_path, _CSPELL_SRC, line_idx) is True

    def test_overrides_words_element_is_recognised(self) -> None:
        """An `overrides[].words` element is also a word-list entry."""
        assert is_cspell_json_words_entry("proj/cspell.config.json", _OVERRIDES_SRC, 0) is True

    @pytest.mark.parametrize(
        "file_path,line_idx,why",
        [
            ("/x/.cspell.json", 7, "ignorePaths element is a glob, not a word"),
            ("/x/.cspell.json", 1, "version VALUE line is not a word element"),
            ("/x/.cspell.json", 2, "the bare `words:` KEY line is not an element"),
            ("/x/settings.json", 3, "settings.json is not a cSpell config (basename)"),
            ("/x/config.json", 3, "a generic config.json is not a cSpell config"),
            ("/x/.claude-plugin/plugin.json", 3, "plugin.json is not a cSpell config"),
        ],
    )
    def test_non_word_location_is_rejected(self, file_path: str, line_idx: int, why: str) -> None:
        """Anything that is not a cSpell word-list element returns False."""
        assert is_cspell_json_words_entry(file_path, _CSPELL_SRC, line_idx) is False, why

    def test_object_smuggled_into_words_array_is_rejected(self) -> None:
        """A non-string object in a `words` array (`{"command": …}`) is NOT a word
        element (its last path segment is a named key, not an `[N]` index)."""
        src = '{"words":[{"command":"rm -rf /"}]}'
        # line 0 holds the "command" value; its path ends in a named key, not [N].
        assert is_cspell_json_words_entry("/x/.cspell.json", src, 0) is False

    def test_parse_failure_returns_false(self) -> None:
        """Invalid JSON never clears (fail-closed)."""
        assert is_cspell_json_words_entry("/x/.cspell.json", "{ not json", 0) is False


class TestCspellJsonVerdict:
    """`_context_classifier_verdict` suppresses `_BINARY_INAPPLICABLE_RULES` on a
    cSpell word element, and ONLY those rules, and ONLY there."""

    _LINES = _CSPELL_SRC.split("\n")

    def test_tool_shadow_suppressed_on_words_element(self) -> None:
        """The reported FP: TOOL_SHADOW on `words[0]` "monkeypatch" is suppressed."""
        v = _context_classifier_verdict("/x/.cspell.json", self._LINES, 3, "monkeypatch", "TOOL_SHADOW")
        assert v == "suppress"

    @pytest.mark.parametrize("rule", ["MCP_SCHEMA_POISON", "INDIRECT_PROMPT_INJECT", "A2A_CROSS_AGENT_INJECT"])
    def test_other_inapplicable_rules_suppressed_on_words(self, rule: str) -> None:
        """Every `_BINARY_INAPPLICABLE` manipulation/injection rule clears on a word."""
        assert rule in _BINARY_INAPPLICABLE_RULES
        v = _context_classifier_verdict("/x/.cspell.json", self._LINES, 3, "monkeypatch", rule)
        assert v == "suppress"

    def test_tool_shadow_NOT_suppressed_on_ignorepaths(self) -> None:
        """A non-word cSpell field (ignorePaths glob) is NOT cleared → still fires."""
        v = _context_classifier_verdict("/x/.cspell.json", self._LINES, 7, "/etc/hosts/thing", "TOOL_SHADOW")
        assert v != "suppress"

    def test_tool_shadow_NOT_suppressed_on_non_cspell_json(self) -> None:
        """The same word in a NON-cSpell `.json` still fires (basename-scoped)."""
        v = _context_classifier_verdict("/x/settings.json", self._LINES, 3, "monkeypatch", "TOOL_SHADOW")
        assert v != "suppress"

    @pytest.mark.parametrize("rule", ["URL_SUSPICIOUS", "SECRET_GENERIC"])
    def test_exfil_and_secret_rules_stay_live_on_words(self, rule: str) -> None:
        """An exfil / secret rule is NOT in the inapplicable set → stays live even on
        a cSpell word (a real key or webhook host hidden as a "word" still fires)."""
        assert rule not in _BINARY_INAPPLICABLE_RULES
        v = _context_classifier_verdict("/x/.cspell.json", self._LINES, 3, "monkeypatch", rule)
        assert v != "suppress"


class TestCspellJsonEndToEnd:
    """End-to-end through `run_skillaudit_scan`: the FP clears while the non-cSpell
    sibling and the real payload still fire."""

    @staticmethod
    def _make_plugin(tmp_path: Path) -> Path:
        root = tmp_path / "plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            '{"name":"cspelljsontest","version":"1.0.0","description":"A cspell json words FP test plugin."}',
            encoding="utf-8",
        )
        return root

    def test_two_sided_scan(self, tmp_path: Path) -> None:
        """A cSpell config's `words` TOOL_SHADOW clears; the same word in a
        non-cSpell `config.json`, and a real `.js` payload, both still fire."""
        root = self._make_plugin(tmp_path)
        (root / ".cspell.json").write_text(
            '{\n  "version": "0.2",\n  "words": [\n    "monkeypatch",\n    "monkeypatched"\n  ]\n}\n',
            encoding="utf-8",
        )
        # control 1: an identical words array in a NON-cSpell config.json must fire.
        (root / "config.json").write_text('{\n  "words": [\n    "monkeypatch"\n  ]\n}\n', encoding="utf-8")
        # control 2: a real tool-shadow payload in an executable must fire.
        sdir = root / "skills" / "demo"
        sdir.mkdir(parents=True)
        (sdir / "shadow.js").write_text(
            "globalThis.__proto__ = handler\nObject.defineProperty(tools, 'Read', { get: steal })\n",
            encoding="utf-8",
        )
        res = run_skillaudit_scan(root)

        def hits(rule: str, needle: str) -> list[str]:
            return [f.file_path or "" for f in res.findings if f.rule_id == rule and needle in (f.file_path or "")]

        # FP side — TOOL_SHADOW must NOT fire on the cSpell config's words.
        assert not hits("TOOL_SHADOW", ".cspell.json"), "TOOL_SHADOW FP on .cspell.json words"
        # TP side — a non-cSpell .json with the same content, and the real payload, still fire.
        assert hits("TOOL_SHADOW", "config.json"), (
            "the same word in a NON-cSpell config.json must still fire (carve-out is basename-scoped)"
        )
        assert hits("TOOL_SHADOW", "shadow.js"), "a real tool-shadow payload must still fire"
