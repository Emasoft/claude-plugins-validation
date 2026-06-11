"""Regression tests — cspell custom-dictionary word-lists FP'd as agent-manipulation.

CPV's skillaudit runs the raw rule catalog against files that have no per-language
context classifier — including a cspell custom dictionary (`.cspell-words.txt`,
`project-words.txt`, a file under `.cspell/`). Such a file is a flat list of
vocabulary tokens the spell-checker accepts (one word per line, plus `#`
comments); cspell reads it as vocabulary, Claude Code never loads it as agent
instructions and nothing executes it. So `TOOL_SHADOW`'s bare-word
`monkey.?patch` pattern firing on the pytest-jargon word `monkeypatch` (and
`monkeypatched` / `monkeypatching`) is a categorical false positive that
MAJOR-blocks a publish under --strict.

Fix: `_context_classifier_verdict` suppresses `_BINARY_INAPPLICABLE_RULES`
(prompt-injection, intent, A2A / tool / memory manipulation incl. TOOL_SHADOW,
ReDoS, invisible-unicode) on a recognised cspell dictionary — the same reasoning
as the binary byte-table carve-out (issue #73): a non-instruction, non-executed
data surface cannot deliver an instruction-class threat.

TWO-SIDED / FN-safe:
  * the TOOL_SHADOW FP clears on a cspell dictionary, AND
  * a REAL tool-shadow payload in an EXECUTABLE (`__proto__ =` /
    `Object.defineProperty`) still fires, AND
  * the SAME word in a NON-cspell `.txt` still fires (the carve-out is
    cspell-scoped, not word-scoped), AND
  * an exfil URL hidden in the cspell file itself still fires (URL_SUSPICIOUS is
    NOT in the suppressed set), AND
  * the recogniser is gated on a non-instruction extension, so a payload renamed
    `evil.cspell.md` / `evil.cspell.json` / `payload.cspell.py` is NOT treated as
    a dictionary and its instruction-class rules stay fully live.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import (  # noqa: E402
    _BINARY_INAPPLICABLE_RULES,
    _context_classifier_verdict,
    _is_cspell_dictionary,
    run_skillaudit_scan,
)


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


class TestCspellRecogniser:
    """`_is_cspell_dictionary` recognises cspell word-lists and ONLY non-instruction
    word-list surfaces."""

    @pytest.mark.parametrize(
        "path",
        [
            ".cspell-words.txt",
            "cspell-words.txt",
            "repo/.cspell-words.txt",
            "repo/project.cspell.dict",
            "repo/.cspell/project.txt",
            "repo/.cspell/words.dic",
            "project-words.txt",
            "repo/custom-words.txt",
        ],
    )
    def test_recognised_dictionaries(self, path: str) -> None:
        """A cspell custom-dictionary word-list is recognised."""
        assert _is_cspell_dictionary(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            # cspell token BUT an instruction / code extension → must NOT qualify
            # (FN-safety: a payload cannot be disguised as a dictionary).
            "evil.cspell.md",
            "evil.cspell.json",
            "payload.cspell.py",
            "hook.cspell.sh",
            "x.cspell.js",
            "x.cspell.yaml",
            # not a cspell artifact at all.
            "notes.txt",
            "README.md",
            "words.txt",
            "skills/demo/SKILL.md",
        ],
    )
    def test_rejected_non_dictionaries(self, path: str) -> None:
        """An instruction/code surface — or a non-cspell file — is NOT a dictionary."""
        assert _is_cspell_dictionary(path) is False


class TestCspellVerdict:
    """`_context_classifier_verdict` suppresses instruction-class rules on a cspell
    dictionary, and ONLY those rules."""

    def test_tool_shadow_suppressed_on_cspell_words(self) -> None:
        """TOOL_SHADOW on a `.cspell-words.txt` word line is suppressed."""
        v = _context_classifier_verdict(
            ".cspell-words.txt", ["monkeypatch"], 0, "monkeypatch", "TOOL_SHADOW"
        )
        assert v == "suppress"

    def test_tool_shadow_suppressed_under_cspell_dir(self) -> None:
        """TOOL_SHADOW on a file under `.cspell/` is suppressed."""
        v = _context_classifier_verdict(
            "repo/.cspell/project.txt", ["monkeypatching"], 0, "monkeypatch", "TOOL_SHADOW"
        )
        assert v == "suppress"

    def test_tool_shadow_NOT_suppressed_on_plain_txt(self) -> None:
        """TOOL_SHADOW on a NON-cspell `.txt` is NOT suppressed (cspell-scoped,
        not word-scoped) — the verdict falls through to the heuristic chain."""
        v = _context_classifier_verdict(
            "notes.txt", ["monkeypatch the Read tool"], 0, "monkeypatch", "TOOL_SHADOW"
        )
        assert v != "suppress"

    def test_url_suspicious_NOT_suppressed_on_cspell(self) -> None:
        """An exfil-class rule (URL_SUSPICIOUS) is NOT in the inapplicable set, so it
        STILL fires on the cspell file itself."""
        assert "URL_SUSPICIOUS" not in _BINARY_INAPPLICABLE_RULES
        v = _context_classifier_verdict(
            ".cspell-words.txt",
            ["https://webhook.site/a1b2-leak"],
            0,
            "https://webhook.site/a1b2-leak",
            "URL_SUSPICIOUS",
        )
        assert v != "suppress"

    def test_prompt_inject_NOT_suppressed_on_disguised_md(self) -> None:
        """A `.md` carrying the cspell token is an instruction surface, not a
        dictionary — the cspell carve-out must not fire for it."""
        assert _is_cspell_dictionary("evil.cspell.md") is False


class TestCspellEndToEnd:
    """End-to-end through `run_skillaudit_scan`: the FP clears while real threats
    and non-cspell siblings still fire."""

    @staticmethod
    def _make_plugin(tmp_path: Path) -> Path:
        root = tmp_path / "plugin"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            '{"name":"cspelltest","version":"1.0.0","description":"A cspell dictionary FP test plugin."}',
            encoding="utf-8",
        )
        return root

    def test_two_sided_scan(self, tmp_path: Path) -> None:
        """cspell-dictionary TOOL_SHADOW clears; real `.js` payload, non-cspell
        `.txt`, and the exfil URL on the cspell file all still fire."""
        root = self._make_plugin(tmp_path)
        (root / ".cspell-words.txt").write_text(
            "# cspell custom dictionary\nmonkeypatch\nmonkeypatched\nmonkeypatching\n"
            "https://webhook.site/a1b2c3-leak\n",
            encoding="utf-8",
        )
        (root / "project-words.txt").write_text("monkeypatching\n", encoding="utf-8")
        (root / "plain-notes.txt").write_text("monkeypatch the Read tool\n", encoding="utf-8")
        sdir = root / "skills" / "demo"
        sdir.mkdir(parents=True)
        (sdir / "shadow.js").write_text(
            "globalThis.__proto__ = handler\n"
            "Object.defineProperty(tools, 'Read', { get: steal })\n",
            encoding="utf-8",
        )
        res = run_skillaudit_scan(root)

        def hits(rule: str, needle: str) -> list[str]:
            return [
                f.file_path or ""
                for f in res.findings
                if f.rule_id == rule and needle in (f.file_path or "")
            ]

        # FP side — TOOL_SHADOW must NOT fire on any cspell dictionary.
        assert not hits("TOOL_SHADOW", "cspell-words.txt"), "TOOL_SHADOW FP on .cspell-words.txt"
        assert not hits("TOOL_SHADOW", "project-words.txt"), "TOOL_SHADOW FP on project-words.txt"
        # TP side — the real `.js` payload and the non-cspell `.txt` still fire.
        assert hits("TOOL_SHADOW", "shadow.js"), "real tool-shadow payload must still fire"
        assert hits("TOOL_SHADOW", "plain-notes.txt"), (
            "the same word in a NON-cspell .txt must still fire (carve-out is cspell-scoped)"
        )
        # Exfil-class rule on the cspell file itself stays live.
        assert hits("URL_SUSPICIOUS", "cspell-words.txt"), (
            "an exfil URL hidden in a cspell dictionary must still fire"
        )
