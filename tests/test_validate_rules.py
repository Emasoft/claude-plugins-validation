"""Tests for validate_rules.py - Rules Validator.

Tests cover:
- validate_rule_file: Single rule file validation (encoding, secrets, paths, frontmatter)
- validate_rules_directory: Directory-level validation with token budgeting
- _validate_frontmatter: Frontmatter field validation for rule files
- estimate_tokens: Language-aware token estimation
- _classify_char: Character classification for token estimation
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport
from validate_rules import (
    _classify_char,
    _validate_frontmatter,
    estimate_tokens,
    validate_rule_file,
    validate_rules_directory,
)


class TestClassifyChar:
    """Tests for _classify_char function."""

    def test_latin_ascii(self):
        """_classify_char returns 'latin' for standard ASCII letters."""
        assert _classify_char("a") == "latin"
        assert _classify_char("Z") == "latin"
        assert _classify_char("5") == "latin"

    def test_cjk_ideograph(self):
        """_classify_char returns 'cjk' for CJK unified ideographs (Chinese/Japanese kanji)."""
        assert _classify_char("\u4e00") == "cjk"  # first CJK unified ideograph
        assert _classify_char("\u6f22") == "cjk"  # kanji for 'han'


class TestEstimateTokens:
    """Tests for estimate_tokens function."""

    def test_pure_latin_text(self):
        """estimate_tokens uses ~0.25 tokens/char ratio for Latin text (4 chars per token)."""
        text = "This is a simple English sentence with 48 characters!"
        tokens, counts = estimate_tokens(text)
        assert tokens > 0
        assert counts["latin"] > 0
        assert counts["cjk"] == 0
        assert counts["kana"] == 0

    def test_cjk_text_higher_token_ratio(self):
        """estimate_tokens assigns higher token ratio to CJK text (~1 token per character)."""
        latin_text = "abcd"  # 4 chars -> ~1 token at 0.25 ratio
        cjk_text = "\u4e00\u4e01\u4e02\u4e03"  # 4 CJK chars -> ~4 tokens at 1.0 ratio
        latin_tokens, _ = estimate_tokens(latin_text)
        cjk_tokens, _ = estimate_tokens(cjk_text)
        assert cjk_tokens > latin_tokens


class TestValidateFrontmatter:
    """Tests for _validate_frontmatter function."""

    def test_valid_paths_field(self):
        """_validate_frontmatter accepts valid 'paths' field with string array."""
        frontmatter = {"paths": ["src/**/*.py", "tests/*.py"]}
        report = ValidationReport()
        _validate_frontmatter(frontmatter, report, "rules/my-rule.md")
        warning_messages = [r.message for r in report.results if r.level in ("WARNING", "MAJOR")]
        assert len(warning_messages) == 0

    def test_unknown_frontmatter_field_warns(self):
        """_validate_frontmatter warns about unknown frontmatter fields."""
        frontmatter = {"paths": ["src/**"], "author": "Someone"}
        report = ValidationReport()
        _validate_frontmatter(frontmatter, report, "rules/my-rule.md")
        warning_messages = [r.message for r in report.results if r.level == "WARNING"]
        assert any("Unknown frontmatter field" in m and "author" in m for m in warning_messages)


class TestValidateRuleFile:
    """Tests for validate_rule_file function."""

    def test_valid_rule_file(self, tmp_path):
        """validate_rule_file passes for a valid UTF-8 markdown rule file with content."""
        rule_file = tmp_path / "my-rule.md"
        rule_file.write_text("---\npaths:\n  - src/**\n---\n\n# My Rule\n\nFollow this convention.\n", encoding="utf-8")
        report = ValidationReport()
        content = validate_rule_file(rule_file, report, "rules/my-rule.md")
        assert len(content) > 0
        passed_messages = [r.message for r in report.results if r.level == "PASSED"]
        assert any("Rule file validated" in m for m in passed_messages)

    def test_empty_rule_file(self, tmp_path):
        """validate_rule_file flags an empty rule file as MINOR issue."""
        rule_file = tmp_path / "empty.md"
        rule_file.write_text("", encoding="utf-8")
        report = ValidationReport()
        content = validate_rule_file(rule_file, report, "rules/empty.md")
        assert content == ""
        minor_messages = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m.lower() for m in minor_messages)


class TestValidateRulesDirectory:
    """Tests for validate_rules_directory function."""

    def test_valid_rules_directory(self, tmp_path):
        """validate_rules_directory passes for a directory with valid rule files within token budget."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "rule1.md").write_text("# Rule 1\n\nDo this thing.\n", encoding="utf-8")
        (rules_dir / "rule2.md").write_text("# Rule 2\n\nDo that thing.\n", encoding="utf-8")
        report = validate_rules_directory(rules_dir)
        passed_messages = [r.message for r in report.results if r.level == "PASSED"]
        assert any("within budget" in m for m in passed_messages)

    def test_nonexistent_rules_directory(self, tmp_path):
        """validate_rules_directory reports INFO for non-existent directory."""
        report = validate_rules_directory(tmp_path / "no-rules")
        info_messages = [r.message for r in report.results if r.level == "INFO"]
        assert any("No rules" in m for m in info_messages)


# =============================================================================
# Additional tests targeting uncovered lines
# =============================================================================

from validate_rules import (
    _dominant_language,
    main,
    print_json,
    print_results,
)


class TestClassifyCharAdditional:
    """Additional tests for _classify_char covering kana, hangul, jamo, and non-Latin scripts."""

    def test_kana_hiragana(self):
        """_classify_char returns 'kana' for Japanese hiragana characters (line 95)."""
        # U+3042 = hiragana 'a' (あ), falls in 0x3040-0x309F range
        assert _classify_char("\u3042") == "kana"
        # U+306B = hiragana 'ni' (に)
        assert _classify_char("\u306b") == "kana"

    def test_kana_katakana(self):
        """_classify_char returns 'kana' for Japanese katakana characters (line 95)."""
        # U+30A2 = katakana 'a' (ア), falls in 0x30A0-0x30FF range
        assert _classify_char("\u30a2") == "kana"

    def test_hangul_syllable(self):
        """_classify_char returns 'cjk' for Korean hangul syllables (line 99)."""
        # U+AC00 = first hangul syllable '가', falls in 0xAC00-0xD7AF range
        assert _classify_char("\uac00") == "cjk"
        # U+D7A3 = last hangul syllable
        assert _classify_char("\ud7a3") == "cjk"

    def test_korean_jamo(self):
        """_classify_char returns 'cjk' for Korean jamo characters (line 103)."""
        # U+1100 = hangul choseong kiyeok, falls in 0x1100-0x11FF range
        assert _classify_char("\u1100") == "cjk"
        # U+3131 = hangul letter kiyeok, falls in 0x3130-0x318F range
        assert _classify_char("\u3131") == "cjk"

    def test_other_script_cyrillic(self):
        """_classify_char returns 'other_script' for Cyrillic letters above U+024F (line 110)."""
        # U+0410 = Cyrillic capital A (А), cp > 0x024F and is a letter
        assert _classify_char("\u0410") == "other_script"
        # U+0430 = Cyrillic small a (а)
        assert _classify_char("\u0430") == "other_script"

    def test_other_script_arabic(self):
        """_classify_char returns 'other_script' for Arabic script characters (line 110)."""
        # U+0627 = Arabic letter alef (ا)
        assert _classify_char("\u0627") == "other_script"


class TestDominantLanguage:
    """Tests for _dominant_language function."""

    def test_empty_counts(self):
        """_dominant_language returns 'empty' when all character counts are zero (line 145)."""
        counts = {"cjk": 0, "kana": 0, "other_script": 0, "latin": 0}
        assert _dominant_language(counts) == "empty"

    def test_cjk_heavy(self):
        """_dominant_language returns 'CJK-heavy' when CJK+kana exceed 30% of total (line 151)."""
        # 40 CJK + 10 kana = 50 out of 100 total = 50% > 30%
        counts = {"cjk": 40, "kana": 10, "other_script": 0, "latin": 50}
        assert _dominant_language(counts) == "CJK-heavy"

    def test_non_latin(self):
        """_dominant_language returns 'non-Latin' when other_script exceeds 30% of total (line 153)."""
        # 40 other_script out of 100 total = 40% > 30%, with CJK below threshold
        counts = {"cjk": 0, "kana": 0, "other_script": 40, "latin": 60}
        assert _dominant_language(counts) == "non-Latin"

    def test_latin_dominant(self):
        """_dominant_language returns 'Latin' when text is predominantly Latin."""
        counts = {"cjk": 2, "kana": 0, "other_script": 3, "latin": 95}
        assert _dominant_language(counts) == "Latin"


class TestValidateRuleFileAdditional:
    """Additional tests for validate_rule_file covering error paths."""

    def test_unreadable_file(self, tmp_path):
        """validate_rule_file reports MAJOR when file cannot be read (lines 171-173)."""
        nonexistent = tmp_path / "ghost.md"
        report = ValidationReport()
        content = validate_rule_file(nonexistent, report, "rules/ghost.md")
        assert content == ""
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Cannot read" in m for m in major_messages)

    def test_invalid_utf8_file(self, tmp_path):
        """validate_rule_file reports MAJOR for non-UTF-8 encoded file (lines 180-182)."""
        rule_file = tmp_path / "bad-encoding.md"
        # Write bytes that are invalid UTF-8: 0xC0 0xAF is overlong encoding, 0xFE is never valid
        rule_file.write_bytes(b"\xfe\xff\x80\x81 some text \xc0\xaf")
        report = ValidationReport()
        content = validate_rule_file(rule_file, report, "rules/bad-encoding.md")
        assert content == ""
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("UTF-8" in m for m in major_messages)

    def test_frontmatter_not_a_mapping(self, tmp_path):
        """validate_rule_file reports MINOR when frontmatter is a scalar, not a dict (lines 199)."""
        rule_file = tmp_path / "scalar-fm.md"
        # YAML frontmatter that parses to a string, not a dict
        rule_file.write_text("---\njust a plain string\n---\n\n# Content\n\nSome body text.\n", encoding="utf-8")
        report = ValidationReport()
        content = validate_rule_file(rule_file, report, "rules/scalar-fm.md")
        assert len(content) > 0
        minor_messages = [r.message for r in report.results if r.level == "MINOR"]
        assert any("not a YAML mapping" in m for m in minor_messages)

    def test_invalid_yaml_frontmatter(self, tmp_path):
        """validate_rule_file reports MAJOR for malformed YAML frontmatter (lines 200-201)."""
        rule_file = tmp_path / "bad-yaml.md"
        # Malformed YAML: tab character in indentation is ambiguous, use unbalanced braces
        rule_file.write_text("---\nkey: [unclosed bracket\n---\n\n# Content here\n", encoding="utf-8")
        report = ValidationReport()
        validate_rule_file(rule_file, report, "rules/bad-yaml.md")
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Invalid YAML" in m for m in major_messages)

    def test_frontmatter_only_no_body(self, tmp_path):
        """validate_rule_file reports MINOR when file has frontmatter but empty body (line 209)."""
        rule_file = tmp_path / "fm-only.md"
        rule_file.write_text("---\npaths:\n  - src/**\n---\n   \n", encoding="utf-8")
        report = ValidationReport()
        validate_rule_file(rule_file, report, "rules/fm-only.md")
        minor_messages = [r.message for r in report.results if r.level == "MINOR"]
        assert any("no content body" in m for m in minor_messages)

    def test_secret_detection(self, tmp_path):
        """validate_rule_file reports CRITICAL when a secret pattern is found (line 214)."""
        rule_file = tmp_path / "secret-rule.md"
        # Include a fake AWS access key pattern: AKIA followed by 16 uppercase alphanumeric chars
        rule_file.write_text(
            "# My Rule\n\nUse this key: AKIAIOSFODNN7EXAMPLE\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_rule_file(rule_file, report, "rules/secret-rule.md")
        critical_messages = [r.message for r in report.results if r.level == "CRITICAL"]
        assert any("secret" in m.lower() or "AWS" in m for m in critical_messages)

    def test_private_path_detection(self, tmp_path):
        """validate_rule_file reports MAJOR when a private user path is found (line 220)."""
        rule_file = tmp_path / "private-path-rule.md"
        rule_file.write_text(
            "# My Rule\n\nFiles are stored at /Users/johndoe/projects/my-app\n",
            encoding="utf-8",
        )
        report = ValidationReport()
        validate_rule_file(rule_file, report, "rules/private-path-rule.md")
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("Private path" in m or "path" in m.lower() for m in major_messages)


class TestValidateFrontmatterAdditional:
    """Additional tests for _validate_frontmatter covering paths validation edge cases."""

    def test_paths_not_a_list(self):
        """_validate_frontmatter reports MAJOR when 'paths' is a string instead of list (line 240)."""
        frontmatter = {"paths": "src/**/*.py"}
        report = ValidationReport()
        _validate_frontmatter(frontmatter, report, "rules/bad-paths.md")
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be an array" in m for m in major_messages)

    def test_paths_entry_not_string(self):
        """_validate_frontmatter reports MAJOR when a paths entry is not a string (line 244)."""
        frontmatter = {"paths": [123, True]}
        report = ValidationReport()
        _validate_frontmatter(frontmatter, report, "rules/int-paths.md")
        major_messages = [r.message for r in report.results if r.level == "MAJOR"]
        assert any("must be a string" in m and "int" in m for m in major_messages)

    def test_paths_entry_empty_string(self):
        """_validate_frontmatter reports MINOR when a paths entry is an empty string (line 246)."""
        frontmatter = {"paths": ["src/**", "  ", "tests/*.py"]}
        report = ValidationReport()
        _validate_frontmatter(frontmatter, report, "rules/empty-entry.md")
        minor_messages = [r.message for r in report.results if r.level == "MINOR"]
        assert any("empty" in m.lower() for m in minor_messages)


class TestValidateRulesDirectoryAdditional:
    """Additional tests for validate_rules_directory covering token budget scenarios."""

    def test_empty_rules_directory(self, tmp_path):
        """validate_rules_directory reports INFO when directory exists but has no .md files (lines 275-276)."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        # Create a non-.md file so directory is not empty but has no rules
        (rules_dir / "readme.txt").write_text("not a rule", encoding="utf-8")
        report = validate_rules_directory(rules_dir)
        info_messages = [r.message for r in report.results if r.level == "INFO"]
        assert any("No rule files" in m for m in info_messages)

    def test_plugin_root_relative_paths(self, tmp_path):
        """validate_rules_directory uses plugin_root for relative path display (line 284)."""
        plugin_root = tmp_path / "my-plugin"
        rules_dir = plugin_root / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "coding-style.md").write_text("# Coding Style\n\nUse consistent formatting.\n", encoding="utf-8")
        report = validate_rules_directory(rules_dir, plugin_root=plugin_root)
        # The rel_path in passed messages should be relative to plugin_root
        passed_messages = [r for r in report.results if r.level == "PASSED" and r.file]
        assert any("rules/coding-style.md" in (r.file or "") for r in passed_messages)

    def test_token_budget_exceeded(self, tmp_path):
        """validate_rules_directory warns when combined token count exceeds MAX_RULES_TOKENS (line 297)."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        # MAX_RULES_TOKENS = 10_000; at 0.25 tokens/char for Latin, need ~40_001 chars to exceed
        big_content = "# Big Rule\n\n" + ("x" * 45_000) + "\n"
        (rules_dir / "huge-rule.md").write_text(big_content, encoding="utf-8")
        report = validate_rules_directory(rules_dir)
        warning_messages = [r.message for r in report.results if r.level == "WARNING"]
        assert any("exceeds" in m for m in warning_messages)

    def test_token_budget_approaching(self, tmp_path):
        """validate_rules_directory warns when token count approaches 80% of budget (line 304)."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        # Need tokens between 8000 (80%) and 10000. At 0.25 tokens/char, need ~34_000 chars
        content = "# Rule\n\n" + ("y" * 34_000) + "\n"
        (rules_dir / "medium-rule.md").write_text(content, encoding="utf-8")
        report = validate_rules_directory(rules_dir)
        warning_messages = [r.message for r in report.results if r.level == "WARNING"]
        assert any("approaching" in m for m in warning_messages)


class TestPrintResults:
    """Tests for print_results output function."""

    def test_print_results_with_issues(self, capsys):
        """print_results outputs formatted report with CRITICAL/MAJOR/MINOR counts (lines 325-377)."""
        report = ValidationReport()
        report.critical("A critical problem", "rules/bad.md")
        report.major("A major problem", "rules/warn.md")
        report.minor("A minor problem", "rules/minor.md")
        report.passed("Something passed", "rules/good.md")
        print_results(report, verbose=False)
        captured = capsys.readouterr()
        assert "Rules Validation Report" in captured.out
        assert "CRITICAL" in captured.out
        assert "MAJOR" in captured.out
        assert "MINOR" in captured.out
        # Non-verbose should not show PASSED details
        assert "Something passed" not in captured.out

    def test_print_results_verbose(self, capsys):
        """print_results in verbose mode shows PASSED and INFO details (lines 350-352)."""
        report = ValidationReport()
        report.info("Informational note")
        report.passed("All good", "rules/ok.md")
        print_results(report, verbose=True)
        captured = capsys.readouterr()
        assert "Informational note" in captured.out
        assert "All good" in captured.out
        assert "INFO" in captured.out
        assert "PASSED" in captured.out

    def test_print_results_all_passed(self, capsys):
        """print_results shows success message when exit_code is 0 (line 369)."""
        report = ValidationReport()
        report.passed("Everything fine")
        print_results(report, verbose=False)
        captured = capsys.readouterr()
        assert "All rules checks passed" in captured.out


class TestPrintJson:
    """Tests for print_json output function."""

    def test_print_json_structure(self, capsys):
        """print_json outputs valid JSON with exit_code, counts, and results (lines 382-395)."""
        import json as json_mod

        report = ValidationReport()
        report.critical("Secret found", "rules/secret.md")
        report.warning("Token budget high")
        report.passed("Rule validated", "rules/ok.md")
        print_json(report)
        captured = capsys.readouterr()
        data = json_mod.loads(captured.out)
        assert "exit_code" in data
        assert data["exit_code"] == 1  # CRITICAL -> exit code 1
        assert data["counts"]["critical"] == 1
        assert data["counts"]["warning"] == 1
        assert data["counts"]["passed"] == 1
        assert len(data["results"]) == 3


class TestMain:
    """Tests for main() entry point function."""

    def test_main_nonexistent_path(self, monkeypatch, capsys):
        """main() returns 1 and prints error for nonexistent path (lines 413-415)."""
        monkeypatch.setattr("sys.argv", ["validate_rules", "/nonexistent/path/to/rules"])
        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_main_not_a_directory(self, tmp_path, monkeypatch, capsys):
        """main() returns 1 when path is a file, not a directory (lines 424-426)."""
        a_file = tmp_path / "not-a-dir.txt"
        a_file.write_text("hello", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_rules", str(a_file)])
        result = main()
        assert result == 1
        captured = capsys.readouterr()
        assert "not a directory" in captured.err

    def test_main_json_output(self, tmp_path, monkeypatch, capsys):
        """main() with --json flag produces JSON output (lines 430-431)."""
        import json as json_mod

        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "simple.md").write_text("# Simple\n\nA rule.\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_rules", str(tmp_path), "--json"])
        result = main()
        captured = capsys.readouterr()
        data = json_mod.loads(captured.out)
        assert "exit_code" in data
        assert result == data["exit_code"]

    def test_main_plugin_root_with_rules_subdir(self, tmp_path, monkeypatch, capsys):
        """main() detects rules/ subdir when given plugin root path (lines 418-420)."""
        plugin_root = tmp_path / "my-plugin"
        rules_dir = plugin_root / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "style.md").write_text("# Style Guide\n\nBe consistent.\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_rules", str(plugin_root)])
        result = main()
        assert result == 0

    def test_main_strict_mode(self, tmp_path, monkeypatch, capsys):
        """main() with --strict returns exit_code_strict which blocks on NIT issues (line 435-437)."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "good.md").write_text("# Good Rule\n\nThis is fine.\n", encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["validate_rules", str(rules_dir), "--strict"])
        result = main()
        # No issues at all, so strict mode also returns 0
        assert result == 0
