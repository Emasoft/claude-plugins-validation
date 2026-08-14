#!/usr/bin/env python3
"""Issue #210 — a config file that is JSONC by its OWNING TOOL's definition.

REPORTED SYMPTOM: CPV emitted ``[MAJOR] JSON syntax error in
pyrightconfig.json`` — a publish blocker — for a file pyright itself reads
with 0 errors, 0 warnings. `pyrightconfig.json` is JSONC: `//` line comments
are documented and supported. So the two tools disagreed about what JSON IS,
and the disagreement only surfaced at release time. The trap the reporter hit
is worth restating: the `"//": "note"` key form makes pyright warn on EVERY
run (`unrecognized setting`), real line comments silence pyright and blocked
CPV instead — the only configuration satisfying both forbade documenting the
file at all.

THE FIX routes only KNOWN-JSONC paths through CPV's existing JSONC parser
(`cpv_management_common.load_jsonc` — the one
`validate_settings_marketplace` / `manage_doctor` / `validate_cache` already
use). No second parser: a second copy drifts, and a drifted copy is how a
validator accepts on one path what it rejects on another.

TWO-SIDED, and the negative half is the load-bearing half — an over-wide
JSONC set would be a FALSE NEGATIVE in the validator (CPV passing a file the
owning tool rejects), which is strictly worse than the over-strict MAJOR
being fixed here. So:

  * a JSONC-by-definition config with comments / trailing commas CLEARS;
  * a plain `.json` (package.json, plugin.json, a marketplace manifest) with
    the SAME `//` comment still fires MAJOR;
  * a GENUINELY malformed JSONC file (missing comma, unclosed brace) still
    fires MAJOR — comments are tolerated, broken structure is not.

The last two groups pass BOTH pre- and post-fix: they are the controls that
stop a "fix" which simply stopped parsing anything from looking correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tests/conftest.py adds scripts/ to sys.path; defensive duplicate so this file
# works when collected in isolation.
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_lint_engine import lint_json  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# A comment plus a trailing comma — the two things strict JSON rejects and
# every JSONC grammar accepts.
JSONC_BODY = '{\n  // why this setting exists\n  "strict": true,\n}\n'

# The reporter's file, verbatim from issue #210.
ISSUE_210_PYRIGHTCONFIG = """{
  // venvPath+venv: without them pyright resolves imports against its OWN interpreter
  "venvPath": ".",
  "venv": ".venv",
  "include": ["scripts", "tests"],
  "pythonVersion": "3.11"
}
"""


def _lint(root: Path, *paths: Path) -> tuple[bool, list[str]]:
    report = ValidationReport()
    ok = lint_json(root, list(paths), report)
    return ok, [r.message for r in report.results if r.level == "MAJOR"]


def _write(root: Path, relpath: str, body: str) -> Path:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


class TestJsoncConfigsClear:
    """Files their owning tool defines as JSONC must not block a publish."""

    @pytest.mark.parametrize(
        "relpath",
        [
            "pyrightconfig.json",  # pyright
            "tsconfig.json",  # TypeScript
            "tsconfig.build.json",  # TypeScript, project-suffixed
            "jsconfig.json",  # TypeScript
            ".vscode/settings.json",  # VS Code
            ".vscode/launch.json",  # VS Code
            ".eslintrc.json",  # ESLint legacy config
            ".markdownlint.json",  # markdownlint(-cli2), jsonc-parser
            "tools/config.jsonc",  # JSONC by extension, anywhere
        ],
    )
    def test_comments_and_trailing_commas_clear(self, tmp_path: Path, relpath: str) -> None:
        target = _write(tmp_path, relpath, JSONC_BODY)
        ok, majors = _lint(tmp_path, target)
        assert majors == [], f"{relpath} is JSONC by definition but was reported: {majors}"
        assert ok is True

    def test_issue_210_reproducer_clears(self, tmp_path: Path) -> None:
        """The exact file from the report — 0 pyright errors, was 1 CPV MAJOR."""
        target = _write(tmp_path, "pyrightconfig.json", ISSUE_210_PYRIGHTCONFIG)
        ok, majors = _lint(tmp_path, target)
        assert majors == []
        assert ok is True

    def test_block_comments_clear(self, tmp_path: Path) -> None:
        target = _write(tmp_path, "tsconfig.json", '{\n  /* multi\n     line */\n  "strict": true\n}\n')
        ok, majors = _lint(tmp_path, target)
        assert majors == []
        assert ok is True


class TestPlainJsonStaysStrict:
    """CONTROL (passes pre- and post-fix): strict JSON stays strict JSON.

    These are the files whose owning format has no comment grammar at all —
    accepting a comment here would make CPV pass something Claude Code / npm
    would refuse to load, turning an over-strict MAJOR into a silent breakage.
    """

    @pytest.mark.parametrize(
        "relpath",
        [
            "package.json",
            "plugin.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            "data.json",
            "scripts/config.json",
        ],
    )
    def test_comment_in_plain_json_still_majors(self, tmp_path: Path, relpath: str) -> None:
        target = _write(tmp_path, relpath, JSONC_BODY)
        ok, majors = _lint(tmp_path, target)
        assert ok is False
        assert len(majors) == 1
        assert "JSON syntax error" in majors[0]
        # Not parsed as JSONC — the message must not claim a grammar it did
        # not use, or an author will "fix" the wrong thing.
        assert "parsed as JSONC" not in majors[0]

    def test_valid_plain_json_passes(self, tmp_path: Path) -> None:
        target = _write(tmp_path, "package.json", '{"name": "demo"}\n')
        ok, majors = _lint(tmp_path, target)
        assert ok is True
        assert majors == []

    def test_malformed_plain_json_still_majors(self, tmp_path: Path) -> None:
        target = _write(tmp_path, "package.json", '{"a": ,\n')
        ok, majors = _lint(tmp_path, target)
        assert ok is False
        assert any("JSON syntax error" in m for m in majors)


class TestGenuineJsoncErrorsStillBlock:
    """Comments are tolerated; broken structure is not.

    `test_malformed_jsonc_still_majors` is a CONTROL — it passes pre- AND
    post-fix, and it is what stops a "fix" that simply stopped checking these
    files from looking correct. The message-label test beside it asserts the
    new behaviour and therefore only passes post-fix.
    """

    @pytest.mark.parametrize(
        ("case", "body"),
        [
            ("missing comma between members", '{\n  // note\n  "venvPath": "."\n  "venv": ".venv"\n}\n'),
            ("unclosed brace", '{\n  // note\n  "venvPath": "."\n'),
            ("stray closing brace", '{\n  "a": 1\n}}\n'),
            ("bare word value", '{\n  // note\n  "a": nope\n}\n'),
        ],
    )
    def test_malformed_jsonc_still_majors(self, tmp_path: Path, case: str, body: str) -> None:
        target = _write(tmp_path, "pyrightconfig.json", body)
        ok, majors = _lint(tmp_path, target)
        assert ok is False, f"malformed JSONC ({case}) must still block"
        assert len(majors) == 1
        assert "JSON syntax error" in majors[0]

    def test_malformed_jsonc_message_names_the_grammar_used(self, tmp_path: Path) -> None:
        """An author told their JSONC is bad must also be told it was read as JSONC."""
        target = _write(tmp_path, "pyrightconfig.json", '{\n  // note\n  "venvPath": "."\n  "venv": ".venv"\n}\n')
        _, majors = _lint(tmp_path, target)
        assert len(majors) == 1
        assert "parsed as JSONC" in majors[0]

    def test_reported_error_points_past_the_comment(self, tmp_path: Path) -> None:
        """The line reported is the real defect's, not the comment's.

        Pre-fix, every commented config was blamed at the comment (line 2).
        Post-fix that line number means something again: `//` comments keep
        their newlines through the stripper, so the missing comma on line 4 is
        reported as line 4.
        """
        target = _write(
            tmp_path,
            "pyrightconfig.json",
            '{\n  // a real syntax error follows\n  "venvPath": "."\n  "venv": ".venv"\n}\n',
        )
        report = ValidationReport()
        lint_json(tmp_path, [target], report)
        majors = [r for r in report.results if r.level == "MAJOR"]
        assert len(majors) == 1
        assert majors[0].line == 4


class TestMixedTreeVerdict:
    """One tree, both kinds — only the genuinely-wrong file blocks."""

    def test_only_the_plain_json_blocks(self, tmp_path: Path) -> None:
        jsonc = _write(tmp_path, "pyrightconfig.json", ISSUE_210_PYRIGHTCONFIG)
        vscode = _write(tmp_path, ".vscode/settings.json", JSONC_BODY)
        plain = _write(tmp_path, "package.json", JSONC_BODY)
        ok, majors = _lint(tmp_path, jsonc, vscode, plain)
        assert ok is False
        assert len(majors) == 1
        assert "package.json" in majors[0]


class TestSingleParserReuse:
    """The JSONC path goes through CPV's ONE parser, not a private copy."""

    def test_jsonc_file_is_parsed_by_cpv_management_common(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import cpv_management_common

        seen: list[Path] = []
        real = cpv_management_common.load_jsonc

        def spy(path: Path):  # noqa: ANN202
            seen.append(Path(path))
            return real(path)

        monkeypatch.setattr(cpv_management_common, "load_jsonc", spy)

        jsonc = _write(tmp_path, "pyrightconfig.json", JSONC_BODY)
        plain = _write(tmp_path, "package.json", '{"name": "demo"}\n')
        ok, majors = _lint(tmp_path, jsonc, plain)

        assert ok is True
        assert majors == []
        assert [p.name for p in seen] == ["pyrightconfig.json"], (
            "the shared JSONC parser must handle the JSONC file — and ONLY it"
        )


class TestIsJsoncPathClassifier:
    """Unit-level table for the name/location-only classifier.

    Imported inside the tests on purpose: the helper is new, so a reverted
    engine must fail THESE tests without breaking module import for the
    controls above (a collection error would make every control 'fail' too and
    destroy the non-vacuity measurement).
    """

    @pytest.mark.parametrize(
        ("relpath", "expected"),
        [
            ("pyrightconfig.json", True),
            ("PyrightConfig.json", True),  # case-insensitive
            ("tsconfig.json", True),
            ("tsconfig.build.json", True),
            ("jsconfig.app.json", True),
            (".vscode/settings.json", True),
            (".vscode/nested/thing.json", True),
            ("anything.jsonc", True),
            ("package.json", False),
            ("plugin.json", False),
            (".claude-plugin/plugin.json", False),
            ("settings.json", False),  # not under .vscode/
            ("vscode/settings.json", False),  # no leading dot — different dir
            ("tsconfiguration.json", False),  # prefix must be `tsconfig.`
            ("nested/pyrightconfig.json", True),  # a pyright config anywhere is one
        ],
    )
    def test_classification(self, tmp_path: Path, relpath: str, expected: bool) -> None:
        from cpv_lint_engine import _is_jsonc_path

        target = _write(tmp_path, relpath, "{}\n")
        assert _is_jsonc_path(tmp_path, target) is expected

    def test_path_outside_the_repo_root_is_still_classified(self, tmp_path: Path) -> None:
        """The `relative_to` fallback must not silently answer 'not JSONC'."""
        from cpv_lint_engine import _is_jsonc_path

        outside = tmp_path / "outside"
        outside.mkdir()
        target = _write(outside, ".vscode/settings.json", "{}\n")
        root = tmp_path / "repo"
        root.mkdir()
        assert _is_jsonc_path(root, target) is True
