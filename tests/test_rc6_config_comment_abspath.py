#!/usr/bin/env python3
"""Two-sided tests for RC-6 — the absolute-path check must NOT fire on a system
path that appears inside a full-line COMMENT of a config file.

THE BUG (live, publish-blocking; ai-maestro-plugin CI run 29226295087):
``.mega-linter.yml`` line 41 reads::

    #   would mean shelling out or hardcoding /usr/bin paths — both worse.

That is prose inside a YAML comment, yet ``scan_file_for_absolute_paths`` emitted
``[MINOR] Absolute path found: '/usr/bin...'`` — and a MINOR blocks ``--strict``
(exit 3), so a code COMMENT stopped the plugin from publishing. The match evades
the two pre-existing allowlists because ``_SYSTEM_BINARY_PREFIXES`` requires a
``/``-terminated ``/usr/bin/``, while the prose says ``/usr/bin `` (space).

THE FIX is an FP clear, NOT a suppression, and every test below is two-sided —
the FP clears AND the real-finding siblings still fire:

* only the ``system absolute path`` pattern is cleared → a ``/Users/<name>/…`` or
  ``C:\\Users\\<name>\\…`` home path in a config comment STILL fires (a comment can
  carry a real username; that is the leak the rule guards), and the separate
  CRITICAL private-username scan is in another loop entirely;
* only a line whose first non-whitespace run IS the format's comment marker → the
  trailing-comment trap (``key: /opt/x   # note``) is a VALUE line and still fires;
* only config formats that really have comments → ``.py`` / ``.sh`` comments still
  fire (source-code prose is still a leak surface) and ``.json`` has none.

NB: fixture paths are deliberately dot-free. ``scan_file_for_absolute_paths`` has
a pre-existing guard that skips any match containing a regex-special char (``.``
included), so a dotted path would make an assertion vacuous for the wrong reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    _is_config_file_comment_line,
    scan_file_for_absolute_paths,
)


def _scan(tmp_path: Path, name: str, content: str) -> ValidationReport:
    """Write ``content`` to ``tmp_path/name`` and run the absolute-path scan."""
    f = tmp_path / name
    f.write_text(content)
    report = ValidationReport()
    scan_file_for_absolute_paths(f, report, name)
    return report


def _blocking(report: ValidationReport) -> list[str]:
    """The severities that actually gate ``--strict``."""
    return [r.level for r in report.results if r.level in {"CRITICAL", "MAJOR", "MINOR", "NIT"}]


class TestCommentLineHelper:
    """Unit tests on ``_is_config_file_comment_line`` itself."""

    def _at(self, tmp_path: Path, name: str, content: str, needle: str) -> bool:
        return _is_config_file_comment_line(tmp_path / name, content, content.index(needle))

    def test_yaml_hash_comment_line(self, tmp_path: Path) -> None:
        """A `#` line in YAML is a comment line."""
        assert self._at(tmp_path, "a.yml", "#  see /usr/bin here\n", "/usr/bin") is True

    def test_yaml_indented_hash_comment_line(self, tmp_path: Path) -> None:
        """Leading whitespace before the marker still counts."""
        assert self._at(tmp_path, "a.yaml", "    #  see /usr/bin here\n", "/usr/bin") is True

    def test_yaml_trailing_comment_is_not_a_comment_line(self, tmp_path: Path) -> None:
        """THE TRAP: a value line with a trailing comment is NOT a comment line."""
        assert self._at(tmp_path, "a.yml", "root: /opt/x   # note\n", "/opt/x") is False

    def test_toml_hash_comment_line(self, tmp_path: Path) -> None:
        """TOML uses `#`."""
        assert self._at(tmp_path, "a.toml", "# prefix is /usr/local here\n", "/usr/local") is True

    def test_ini_semicolon_comment_line(self, tmp_path: Path) -> None:
        """INI honours `;` as a full-line comment (configparser does)."""
        assert self._at(tmp_path, "a.ini", "; prefix is /usr/local here\n", "/usr/local") is True

    def test_cfg_hash_comment_line(self, tmp_path: Path) -> None:
        """CFG honours `#` too."""
        assert self._at(tmp_path, "a.cfg", "# prefix is /usr/local here\n", "/usr/local") is True

    def test_json5_slash_comment_line(self, tmp_path: Path) -> None:
        """JSON5 uses `//`."""
        assert self._at(tmp_path, "a.json5", "// prefix is /usr/local here\n", "/usr/local") is True

    def test_json5_hash_is_not_a_comment_marker(self, tmp_path: Path) -> None:
        """`#` is NOT a JSON5 comment marker — no blanket-apply."""
        assert self._at(tmp_path, "a.json5", "# prefix is /usr/local here\n", "/usr/local") is False

    def test_yaml_slashslash_is_not_a_comment_marker(self, tmp_path: Path) -> None:
        """`//` is NOT a YAML comment marker — no blanket-apply."""
        assert self._at(tmp_path, "a.yml", "// prefix is /usr/local here\n", "/usr/local") is False

    def test_json_proper_has_no_comments(self, tmp_path: Path) -> None:
        """JSON proper has NO comment syntax → never cleared."""
        assert self._at(tmp_path, "a.json", "# prefix is /usr/local here\n", "/usr/local") is False

    def test_python_source_is_not_a_config_file(self, tmp_path: Path) -> None:
        """A `#` comment in SOURCE CODE is out of scope (still a leak surface)."""
        assert self._at(tmp_path, "a.py", "# prefix is /usr/local here\n", "/usr/local") is False

    def test_shell_source_is_not_a_config_file(self, tmp_path: Path) -> None:
        """Same for shell."""
        assert self._at(tmp_path, "a.sh", "# prefix is /usr/local here\n", "/usr/local") is False

    def test_comment_on_a_later_line_of_a_multiline_file(self, tmp_path: Path) -> None:
        """Line bounds are computed correctly for a match past the first line."""
        content = "key: value\nother: thing\n#  note about /usr/bin here\n"
        assert self._at(tmp_path, "a.yml", content, "/usr/bin") is True

    def test_value_on_a_later_line_of_a_multiline_file(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL for the line-bounds logic: the value line next to a
        comment line is still recognised as a value line."""
        content = "#  note about something\nroot: /usr/bin here\n"
        assert self._at(tmp_path, "a.yml", content, "/usr/bin") is False


class TestRC6FalsePositiveClears:
    """The reported FP no longer produces a `--strict`-blocking finding."""

    def test_the_exact_mega_linter_line_clears(self, tmp_path: Path) -> None:
        """THE BUG: the verbatim `.mega-linter.yml` line 41 → no blocking finding."""
        report = _scan(
            tmp_path,
            ".mega-linter.yml",
            "APPLY_FIXES: none\n# Rationale:\n#   would mean shelling out or hardcoding /usr/bin paths — both worse.\n",
        )
        assert _blocking(report) == []

    def test_the_clear_is_visible_not_silent(self, tmp_path: Path) -> None:
        """It is downgraded to a visible INFO, never silently dropped."""
        report = _scan(tmp_path, ".mega-linter.yml", "#   hardcoding /usr/bin paths — both worse\n")
        assert any(r.level == "INFO" for r in report.results)

    def test_toml_comment_clears(self, tmp_path: Path) -> None:
        """A system path in a TOML comment clears."""
        report = _scan(tmp_path, "pyproject.toml", "# we do not hardcode /usr/local/lib anywhere\n")
        assert _blocking(report) == []

    def test_ini_semicolon_comment_clears(self, tmp_path: Path) -> None:
        """A system path in an INI `;` comment clears."""
        report = _scan(tmp_path, "setup.ini", "; we do not hardcode /usr/local/lib anywhere\n")
        assert _blocking(report) == []

    def test_json5_comment_clears(self, tmp_path: Path) -> None:
        """A system path in a JSON5 `//` comment clears."""
        report = _scan(tmp_path, "opts.json5", "// we do not hardcode /usr/local/lib anywhere\n")
        assert _blocking(report) == []

    def test_indented_comment_clears(self, tmp_path: Path) -> None:
        """An indented comment (common inside a YAML block) clears."""
        report = _scan(tmp_path, "ci.yml", "jobs:\n  steps:\n    #  not /opt/homebrew/lib here\n")
        assert _blocking(report) == []


class TestValuesStillFire:
    """FN-safety, part 1 — an absolute path in a VALUE position still fires."""

    def test_yaml_system_path_value_still_fires(self, tmp_path: Path) -> None:
        """POSITIVE CONTROL: the same path as a YAML value is NOT cleared."""
        report = _scan(tmp_path, "conf.yml", "prefix: /usr/local/lib/mytool\n")
        assert _blocking(report), "a system path in a YAML value must still fire"

    def test_trailing_comment_on_a_value_line_still_fires(self, tmp_path: Path) -> None:
        """THE TRAP: `key: /opt/x   # note` is a VALUE line → must still fire."""
        report = _scan(tmp_path, "conf.yml", "root: /opt/mytool/bin   # the install prefix\n")
        assert _blocking(report), "a value line with a trailing comment must still fire"

    def test_home_path_value_with_trailing_comment_still_fires(self, tmp_path: Path) -> None:
        """A home path as a value, on a line that also carries a comment."""
        report = _scan(tmp_path, "conf.yml", "root: /Users/bob/projects/x   # dev box\n")
        assert _blocking(report), "a home path in a YAML value must still fire"

    def test_toml_value_still_fires(self, tmp_path: Path) -> None:
        """A system path in a TOML value is NOT cleared."""
        report = _scan(tmp_path, "conf.toml", 'prefix = "/usr/local/lib/mytool"\n')
        assert _blocking(report), "a system path in a TOML value must still fire"

    def test_ini_value_still_fires(self, tmp_path: Path) -> None:
        """A system path in an INI value is NOT cleared."""
        report = _scan(tmp_path, "conf.ini", "prefix = /usr/local/lib/mytool\n")
        assert _blocking(report), "a system path in an INI value must still fire"


class TestHomePathsInCommentsStillFire:
    """FN-safety, part 2 — the clear CANNOT weaken the username-leak rules.

    Only ``system absolute path`` is cleared, so a home path in a config comment
    keeps firing even though the line IS a comment line.
    """

    def test_users_home_path_in_yaml_comment_still_fires(self, tmp_path: Path) -> None:
        """A `/Users/<name>/…` path in a YAML comment still fires (username leak)."""
        report = _scan(tmp_path, "conf.yml", "#  built at /Users/alicedev42/project/dist/bin\n")
        assert _blocking(report), "a /Users/ path in a comment must still fire"

    def test_linux_home_path_in_yaml_comment_still_fires(self, tmp_path: Path) -> None:
        """A `/home/<name>/…` path in a YAML comment still fires."""
        report = _scan(tmp_path, "conf.yml", "#  cache lives at /home/builduser/cache/data\n")
        assert _blocking(report), "a /home/ path in a comment must still fire"

    def test_windows_home_path_in_toml_comment_still_fires(self, tmp_path: Path) -> None:
        """A `C:/Users/<name>/…` path in a TOML comment still fires.

        Forward-slash form on purpose: the ``Windows home path`` pattern accepts
        either separator, but the pre-existing regex-special-char guard skips any
        match containing a backslash — so the backslash form never reaches the
        finding at all (a pre-existing gap, unrelated to this fix, and verified as
        such against the baseline). The forward-slash form is the honest positive
        control that the clear does not touch this pattern.
        """
        report = _scan(tmp_path, "conf.toml", "#  built at C:/Users/bobdev/project/dist\n")
        assert _blocking(report), "a Windows home path in a comment must still fire"

    def test_private_username_in_config_comment_still_criticals(self, tmp_path: Path) -> None:
        """THE STRICTEST RULE: the CRITICAL private-username scan is untouched.

        ``PRIVATE_USERNAMES`` is a separate loop above the absolute-path loop and
        is not reachable from the comment clear.
        """
        import cpv_validation_common as cvc

        f = tmp_path / "conf.yml"
        f.write_text("#  built at /Users/privateuser42/project/dist/bin\n")
        report = ValidationReport()
        original = cvc.PRIVATE_USERNAMES
        try:
            cvc.PRIVATE_USERNAMES = {"privateuser42"}
            scan_file_for_absolute_paths(f, report, "conf.yml")
        finally:
            cvc.PRIVATE_USERNAMES = original

        levels = [r.level for r in report.results]
        assert "CRITICAL" in levels, "a private username in a config comment must still be CRITICAL"


class TestSourceCodeCommentsStillFire:
    """FN-safety, part 3 — the clear is CONFIG-ONLY.

    A path in source-code prose is still a leak risk (it can carry a real
    username), so a `.py` / `.sh` comment keeps firing. Narrow beats broad.
    """

    def test_python_comment_is_not_cleared_by_the_config_comment_rule(self, tmp_path: Path) -> None:
        """A `.py` comment is NOT cleared by THIS rule.

        Truthful two-sided framing: a path in a `.py` comment already produces no
        blocking finding, but for a DIFFERENT, PRE-EXISTING reason — the issue-#57
        AST inert-data check (a constant that reaches no fs/exec/network sink).
        Verified against the baseline. What this test pins is that the config-
        comment clear never extends to source code: its INFO is absent, so `.py`
        behaviour is decided entirely by the pre-existing AST route.
        """
        report = _scan(tmp_path, "tool.py", "#  we would have to hardcode /usr/local/lib here\n")
        assert not any("config comment" in r.message for r in report.results), (
            "the config-comment clear must not apply to a .py source file"
        )

    def test_shell_comment_still_fires(self, tmp_path: Path) -> None:
        """A system path in a shell comment is NOT cleared."""
        report = _scan(tmp_path, "setup.sh", "#  we would have to hardcode /usr/local/lib here\n")
        assert _blocking(report), "a system path in a .sh comment must still fire"

    def test_json_has_no_comments_so_still_fires(self, tmp_path: Path) -> None:
        """JSON proper has no comment syntax → a `#`-prefixed line is data."""
        report = _scan(tmp_path, "conf.json", '{"note": "# uses /usr/local/lib/mytool"}\n')
        assert _blocking(report), "a system path in .json must still fire"

    def test_executable_content_in_a_yaml_block_scalar_still_fires(self, tmp_path: Path) -> None:
        """A real command inside a YAML `run: |` block is not a comment → fires."""
        report = _scan(
            tmp_path,
            "ci.yml",
            "jobs:\n  steps:\n    - run: |\n        cp /usr/local/lib/mytool ./vendor\n",
        )
        assert _blocking(report), "an executable line in a block scalar must still fire"
