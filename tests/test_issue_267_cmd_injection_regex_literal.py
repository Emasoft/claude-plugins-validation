"""Issue #267 (CMD_INJECTION half): a pipe inside a regex-literal alternation is not a shell pipe.

The catalog pattern `(?:;|\\||&&)\\s*\\b(?:curl|...|bash|sh|...)\\b` fired CRITICAL on
`/^\\s*<(command-|...|task-notification|bash-)/` because `|bash` followed by `-` met the
trailing word boundary. The command word now must be followed by end of line or a non-word, non-hyphen character (only `word-` and `word3` stop matching). Real
`scan_content` calls; no mocks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cpv_skillaudit_native as native
import pytest

CATALOG = Path(__file__).resolve().parent.parent / "scripts" / "rules" / "skillaudit_patterns.json"

REGEX_LITERAL = (
    "  return entry.type === 'user' && entry.isMeta !== true && "
    "!/^\\s*<(command-|local-command|system-reminder|task-notification|bash-)/.test(text)"
)


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scan is computed, never served from the on-disk scan cache."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _injection_lines(line: str, path: str) -> list[int]:
    """Line numbers of live (unsuppressed) CMD_INJECTION findings for a one-line file."""
    findings = native.scan_content(line + "\n", path)
    return [f["line"] for f in findings if f["ruleId"] == "CMD_INJECTION" and not f.get("suppressed")]


def test_regex_literal_alternation_with_bash_is_not_command_injection() -> None:
    """`|bash-)` inside a TypeScript regex literal draws no CMD_INJECTION finding."""
    assert _injection_lines(REGEX_LITERAL, "scripts/replay.ts") == []


@pytest.mark.parametrize(
    "line",
    [
        "echo $x | sh",
        "ls; bash -c id",
        "a && curl http://x",
        "cat x |bash",
        ";sh",
        "(cat x | bash)",
        "cat x | bash;",
        "cat x |bash>out",
        "x|sh<in",
        "a|sh,",
        "x|sh;",
        "(x|sh)",
        "x|sh}",
        "x|sh]",
        "cat x | python",
    ],
)
def test_real_shell_pipes_into_interpreters_still_flagged(line: str) -> None:
    """Genuine `; | &&` followed by a command word and a terminator stays CMD_INJECTION."""
    assert _injection_lines(line, "scripts/x.sh") != []


@pytest.mark.parametrize("line", ["cat x | python3", "cat x | bash-completion"])
def test_command_word_with_trailing_word_character_is_not_flagged(line: str) -> None:
    """`python3` stays unflagged and `bash-completion` (a hyphenated name, not the command) is no longer flagged."""
    assert _injection_lines(line, "scripts/x.sh") == []


def _pipe_pattern() -> re.Pattern[str]:
    """The catalog CMD_INJECTION entry that starts with `(?:;|\\||&&)`, compiled as the scanner does."""
    rules = json.loads(CATALOG.read_bytes())["rules"]
    found = [p for r in rules if r["id"] == "CMD_INJECTION" for p in r["patterns"] if p.startswith("(?:;|\\||&&)")]
    assert len(found) == 1
    return re.compile(found[0], re.IGNORECASE)


@pytest.mark.parametrize(
    "line",
    [
        "echo $x | sh",
        "ls; bash -c id",
        "a && curl http://x",
        "cat x |bash",
        ";sh",
        "(cat x | bash)",
        "cat x | bash;",
        "cat x |bash>out",
        "x|sh<in",
        "a|sh,",
        "x|sh;",
        "(x|sh)",
        "x|sh}",
        "x|sh]",
        "cat x | python",
        "cat x | perl -e 1",
        "cat x | ruby -e 1",
        "cat x | php",
    ],
)
def test_edited_catalog_pattern_itself_matches_real_pipes(line: str) -> None:
    """The edited catalog pattern (not another CMD_INJECTION entry) matches each real pipe, perl and ruby included."""
    assert _pipe_pattern().search(line) is not None


@pytest.mark.parametrize("line", ["cat x | bash-completion", "cat x | python3", REGEX_LITERAL])
def test_edited_catalog_pattern_itself_rejects_non_commands(line: str) -> None:
    """The edited catalog pattern does not match a hyphenated or digit-suffixed name, nor the regex literal."""
    assert _pipe_pattern().search(line) is None
