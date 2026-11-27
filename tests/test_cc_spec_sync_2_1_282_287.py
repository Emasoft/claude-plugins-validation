#!/usr/bin/env python3
"""CC spec sync 2.1.282-287 (TRDD-PHQZ1XAD) - mods-related validator surface.

Pins, per script:
- validate_hook.py: `modules` in hooks.json is a Claude Mod (hooks-OR-modules
  top-level relax, `modules` array shape/extension/existence checks).
- validate_mcp.py + cc_scope_rules.py: bareElicitationCapability server field,
  allowedProviders managed-only key, prependPlugins/appendPlugins and the two
  mods guard keys as known settings keys.
- cpv_validation_common.py + validate_marketplace.py: new built-in slash
  commands, CLAUDE_CODE_DISABLE_WEB_FETCH, non-marketplace source ids and the
  claudeai- prefix / spelling-variant reserved-name rules.

Every no-finding acceptance is paired with a positive control proving the
detector still fires.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402

# ---------------------------------------------------------------------------
# WP1 — Claude Mods `modules` in hooks.json (v2.1.287)
# ---------------------------------------------------------------------------
from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402


def _wp1_levels(report):
    return [r.level for r in report.results]


# ---------------------------------------------------------------------------
# WP3 — validate_mcp.py + cc_scope_rules.py v2.1.285-287 (TRDD-PHQZ1XAD)
# ---------------------------------------------------------------------------
import cc_scope_rules  # noqa: E402
from validate_mcp import validate_mcp_config, validate_mcp_server  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402

WP3_MANAGED_ONLY_SETTINGS_KEYS = [
    "allowedProviders",
]
# The two mods guard options mirror pluginConfigs' own registration (brief B):
# known top-level keys, scope semantics enforced under pluginConfigs at runtime.
WP3_PLUGINCONFIGS_GUARD_KEYS = [
    "allowManagedModsOnly",
    "allowModsToOverrideDenyRules",
]
WP3_ANY_FILE_SETTINGS_KEYS = [
    "prependPlugins",
    "appendPlugins",
]


def _wp3_unknown_hint_results(report):
    return [r for r in report.results if "not a known Claude Code settings key" in r.message]


class TestWP3McpSettings:
    """WP3 (TRDD-PHQZ1XAD): v2.1.287 MCP server field + v2.1.285-287 settings keys.

    Script registrations landed in eb6530b1; this class pins them and adds the
    one missing validator behavior (non-bool bareElicitationCapability WARNING).
    Every no-finding acceptance is paired with a positive control proving the
    detector still fires.
    """

    # ---- bareElicitationCapability (v2.1.287, mcp.md) -------------------

    def test_bare_elicitation_capability_is_known_field(self):
        """The v2.1.287 server entry field draws no unknown-field warning."""
        report = ValidationReport()
        validate_mcp_server(
            "s", {"command": "n", "args": [], "bareElicitationCapability": True}, report
        )
        unknown = [r.message for r in report.results if "Unknown field" in r.message]
        assert not unknown, unknown

    def test_bare_elicitation_capability_nonbool_warns(self):
        """A non-bool bareElicitationCapability draws the typed-field WARNING."""
        report = ValidationReport()
        validate_mcp_server(
            "s", {"command": "n", "args": [], "bareElicitationCapability": "yes"}, report
        )
        warnings = [r for r in report.results if r.level == "WARNING"]
        assert any(
            "bareElicitationCapability" in w.message and "boolean" in w.message for w in warnings
        ), [(r.level, r.message) for r in report.results]

    def test_bogus_field_still_warns(self):
        """Positive control: a genuinely unknown field still draws the warning."""
        report = ValidationReport()
        validate_mcp_server("s", {"command": "n", "args": [], "totallyBogus": 1}, report)
        assert any("Unknown field 'totallyBogus'" in r.message for r in report.results)

    def test_mcp_json_fixture_end_to_end(self, tmp_path):
        """The brief's .mcp.json fixture validates with no unknown-field warning."""
        cfg = tmp_path / ".mcp.json"
        cfg.write_text(
            json.dumps(
                {"mcpServers": {"s": {"command": "n", "args": [], "bareElicitationCapability": True}}}
            ),
            encoding="utf-8",
        )
        report = validate_mcp_config(cfg)
        unknown = [r.message for r in report.results if "Unknown field" in r.message]
        assert not unknown, unknown

    # ---- settings keys (v2.1.285 allowedProviders + v2.1.287 mods) ------

    def test_allowed_providers_is_managed_only(self):
        """The v2.1.285 provider allowlist is registered managed-only."""
        assert "allowedProviders" in cc_scope_rules.MANAGED_ONLY_KEYS

    def test_mods_guard_keys_known_not_managed_only(self):
        """The two mods guards mirror pluginConfigs: known keys, not in the
        managed-only set (their scope binds under pluginConfigs at runtime)."""
        for key in WP3_PLUGINCONFIGS_GUARD_KEYS:
            assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS, key
            assert key not in cc_scope_rules.MANAGED_ONLY_KEYS, key

    def test_any_file_keys_are_known_not_managed_only(self):
        """prependPlugins/appendPlugins are known user-or-managed list keys."""
        for key in WP3_ANY_FILE_SETTINGS_KEYS:
            assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS, key
            assert key not in cc_scope_rules.MANAGED_ONLY_KEYS, key

    def test_plugin_list_keys_not_managed_only(self):
        """prependPlugins/appendPlugins are user-or-managed lists, NOT managed-only."""
        for key in WP3_ANY_FILE_SETTINGS_KEYS:
            assert key not in cc_scope_rules.MANAGED_ONLY_KEYS, key

    def test_managed_only_key_no_unknown_hint_in_project(self, tmp_path):
        """allowedProviders in a project fixture draws its scope MAJOR and the
        unknown-key INFO does NOT fire for it (MO keys are hint-excluded)."""
        for key in WP3_MANAGED_ONLY_SETTINGS_KEYS:
            f = tmp_path / f"settings-{key}.json"
            f.write_text(json.dumps({key: True}), encoding="utf-8")
            report = ValidationReport()
            validate_settings_json_project_scope(f, report)
            assert not _wp3_unknown_hint_results(report), (key, [(r.level, r.message) for r in report.results])
            assert any(r.level == "MAJOR" and key in r.message for r in report.results), key

    def test_known_keys_no_unknown_hint_in_project(self, tmp_path):
        """The four known mods/provider keys at project scope draw no unknown-key hint."""
        for key in WP3_PLUGINCONFIGS_GUARD_KEYS + WP3_ANY_FILE_SETTINGS_KEYS:
            f = tmp_path / f"settings-{key}.json"
            f.write_text(json.dumps({key: ["some-plugin"]}), encoding="utf-8")
            report = ValidationReport()
            validate_settings_json_project_scope(f, report)
            assert not _wp3_unknown_hint_results(report), (key, [(r.level, r.message) for r in report.results])

    def test_bogus_key_still_hints(self, tmp_path):
        """Positive control: a near-miss spelling still draws the unknown-key hint."""
        f = tmp_path / "settings.json"
        f.write_text(json.dumps({"allowedProviderss": True}), encoding="utf-8")
        report = ValidationReport()
        validate_settings_json_project_scope(f, report)
        assert _wp3_unknown_hint_results(report), [(r.level, r.message) for r in report.results]


class TestWP1HooksModules:
    """WP1 (TRDD-PHQZ1XAD): a `modules`-keyed hooks.json is a Claude Mod.

    Two-sided per the 213-216 convention: every accept case has a reject
    sibling. Pure-mod form (mods-create.md:136-141): {"description",
    "modules":[...]} with NO `hooks` key.
    """

    def test_pure_mod_hooks_json_no_critical_no_unknown_field(self, tmp_path):
        """A pure-mod hooks.json draws NO critical and NO unknown-field warning."""
        (tmp_path / "hooks").mkdir()
        (tmp_path / "hooks" / "register.js").write_text("export function register(on, options) {}\n")
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({"description": "d", "modules": ["./register.js"]}, tmp_path, report)
        assert "CRITICAL" not in _wp1_levels(r), [x.message for x in r.results]
        assert not any("Unknown top-level field" in x.message for x in r.results)

    def test_unknown_field_still_warns_suppression_control(self, tmp_path):
        """Suppression positive control: a bogus top-level key STILL warns."""
        (tmp_path / "hooks").mkdir()
        (tmp_path / "hooks" / "register.js").write_text("x\n")
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        data = {"description": "d", "modules": ["./register.js"], "bogusKey": 1}
        r = validate_hooks_data(data, tmp_path, report)
        assert any("Unknown top-level field 'bogusKey'" in x.message for x in r.results)

    def test_neither_hooks_nor_modules_is_critical(self, tmp_path):
        """hooks-OR-modules control: NEITHER key present stays CRITICAL."""
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({}, tmp_path, report)
        assert any("Missing required" in x.message for x in r.results if x.level == "CRITICAL")

    def test_modules_not_a_list_critical(self, tmp_path):
        """A non-list `modules` value is CRITICAL."""
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({"modules": "register.js"}, tmp_path, report)
        assert any("'modules' must be an array" in x.message for x in r.results if x.level == "CRITICAL")

    def test_empty_modules_list_major_not_critical(self, tmp_path):
        """An empty `modules` list stays at the committed item-8 MAJOR, not CRITICAL."""
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({"modules": []}, tmp_path, report)
        assert "CRITICAL" not in _wp1_levels(r), [x.message for x in r.results]
        assert any("empty" in x.message for x in r.results if x.level == "MAJOR")

    def test_module_element_non_string_critical(self, tmp_path):
        """A non-string element of `modules` is CRITICAL."""
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({"modules": [42]}, tmp_path, report)
        assert any("must be a string path" in x.message for x in r.results if x.level == "CRITICAL")

    def test_module_element_empty_string_critical(self, tmp_path):
        """An empty-string element is CRITICAL (TRDD-PHQZ1XAD WP1)."""
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r = validate_hooks_data({"modules": [""]}, tmp_path, report)
        assert any("must be a non-empty path string" in x.message for x in r.results if x.level == "CRITICAL")
    # REFUSAL, not the old pass-through.
    def test_bad_extension_major_ts_clean(self, tmp_path):
        """Bad extension draws MAJOR (oracle-probed: CC errors on it, TRDD-TLCJ9LPE); a .ts element draws none."""
        (tmp_path / "hooks").mkdir()
        (tmp_path / "hooks" / "mod.js2").write_text("x\n")
        (tmp_path / "hooks" / "reg.ts").write_text("x\n")
        report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r_bad = validate_hooks_data({"modules": ["./mod.js2"]}, tmp_path, report)
        assert any("not one of the documented" in x.message for x in r_bad.results if x.level == "MAJOR")
        report2 = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
        r_ok = validate_hooks_data({"modules": ["./reg.ts"]}, tmp_path, report2)
        assert not any("not one of the documented" in x.message for x in r_ok.results if x.level == "MAJOR")

    def test_inline_hooks_object_with_modules_no_critical(self, tmp_path):
        """SSOT: an inline hooks OBJECT with modules + no hooks does not critical."""
        report = HookValidationReport(hook_path=".claude-plugin/plugin.json")
        r = validate_hooks_data({"modules": ["./register.js"]}, tmp_path, report)
        assert "CRITICAL" not in _wp1_levels(r), [x.message for x in r.results]

    def test_file_based_missing_module_major_and_existing_clean(self, tmp_path):
        """File-based hooks.json: existing register.js clean; missing is MAJOR."""
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text(json.dumps({"description": "d", "modules": ["./register.js"]}))
        report = HookValidationReport(hook_path=str(hooks_dir / "hooks.json"))
        r_missing = validate_hooks_data({"description": "d", "modules": ["./register.js"]}, tmp_path, report)
        assert any("mods hooks module not found" in x.message for x in r_missing.results if x.level == "MAJOR")
        (hooks_dir / "register.js").write_text("export function register(on, options) {}\n")
        report2 = HookValidationReport(hook_path=str(hooks_dir / "hooks.json"))
        r_ok = validate_hooks_data({"description": "d", "modules": ["./register.js"]}, tmp_path, report2)
        assert "MAJOR" not in _wp1_levels(r_ok), [x.message for x in r_ok.results]


class TestWP4ConstantsMarketplace:
    """WP4 pins: built-in slash commands, env-var set, marketplace reserved names.

    Every "now refused/warns" assertion has a paired control proving the
    set. The banner's divergence lines are historical.
    """

    # ── BUILTIN_SLASH_COMMANDS (cpv_validation_common.py) ────────────────────

    def test_new_builtins_in_set(self):
        """The three documented v2.1.282-287 commands are collision-guarded."""
        from cpv_validation_common import BUILTIN_SLASH_COMMANDS

        for name in ("plugin-authoring", "artifact-capabilities", "artifact-diagramming"):
            assert name in BUILTIN_SLASH_COMMANDS, name

    def test_plugin_authoring_collision_warns(self, tmp_path):
        """A plugin command named plugin-authoring draws the built-in collision WARNING."""
        from validate_command import validate_command

        cmd = tmp_path / "plugin-authoring.md"
        cmd.write_text(
            "---\ndescription: Plugin command shadowing the v2.1.287 built-in.\n---\nBody.\n"
        )
        report = validate_command(cmd)
        assert any(
            "built-in" in r.message.lower() and r.level == "WARNING" for r in report.results
        ), [r.message for r in report.results]

    def test_artifact_capabilities_collision_warns(self, tmp_path):
        """A plugin command named artifact-capabilities draws the collision WARNING."""
        from validate_command import validate_command

        cmd = tmp_path / "artifact-capabilities.md"
        cmd.write_text(
            "---\ndescription: Plugin command shadowing the bundled skill.\n---\nBody.\n"
        )
        report = validate_command(cmd)
        assert any(
            "built-in" in r.message.lower() and r.level == "WARNING" for r in report.results
        ), [r.message for r in report.results]

    def test_artifact_diagramming_collision_warns(self, tmp_path):
        """A plugin command named artifact-diagramming draws the collision WARNING."""
        from validate_command import validate_command

        cmd = tmp_path / "artifact-diagramming.md"
        cmd.write_text(
            "---\ndescription: Plugin command shadowing the diagramming skill.\n---\nBody.\n"
        )
        report = validate_command(cmd)
        assert any(
            "built-in" in r.message.lower() and r.level == "WARNING" for r in report.results
        ), [r.message for r in report.results]

    def test_control_preexisting_builtin_verify_still_warns(self, tmp_path):
        """Control: an established built-in name (`verify`) still draws the WARNING."""
        from validate_command import validate_command

        cmd = tmp_path / "verify.md"
        cmd.write_text(
            "---\ndescription: Probe command shadowing an older built-in.\n---\nBody.\n"
        )
        report = validate_command(cmd)
        assert any(
            "built-in" in r.message.lower() and r.level == "WARNING" for r in report.results
        ), [r.message for r in report.results]

    def test_control_non_builtin_clean(self, tmp_path):
        """Control: a name no built-in uses draws no collision finding."""
        from validate_command import validate_command

        cmd = tmp_path / "zzz-not-a-builtin-wp4.md"
        cmd.write_text(
            "---\ndescription: Probe command with a name no built-in uses.\n---\nBody.\n"
        )
        report = validate_command(cmd)
        assert not any(
            "built-in" in r.message.lower() for r in report.results
        ), [r.message for r in report.results]

    # ── VALID_PLUGIN_ENV_VARS (cpv_validation_common.py) ─────────────────────

    def test_disable_web_fetch_documented_var_valid(self):
        """CLAUDE_CODE_DISABLE_WEB_FETCH is in VALID_PLUGIN_ENV_VARS (v2.1.285, brief decision (j))."""
        from cpv_validation_common import is_valid_plugin_env_var

        assert is_valid_plugin_env_var("CLAUDE_CODE_DISABLE_WEB_FETCH")

    def test_control_bogus_env_var_still_unknown(self):
        """Control: a truly bogus variable is still rejected."""
        from cpv_validation_common import is_valid_plugin_env_var

        assert not is_valid_plugin_env_var("CLAUDE_CODE_DISABLE_WEB_FETCH_TYPO_XQ")

    def test_control_skip_ruled_vars_still_unknown(self):
        """Control: the curation-ruled SKIP vars remain out (decision locked)."""
        from cpv_validation_common import is_valid_plugin_env_var

        for name in (
            "DISABLE_AUTH_REFRESH_LOCK",
            "DISABLE_MODEL_ACCESS_FALLBACK",
            "SKIP_MODEL_ACCESS_MEMORY",
            "NONSTREAMING_TIMEOUT_RETRIES",
        ):
            assert not is_valid_plugin_env_var(name), name

    # ── Marketplace reserved names (validate_marketplace.py) ─────────────────

    def test_non_marketplace_source_ids_refused(self):
        """Each documented non-marketplace source id is a CRITICAL refusal."""
        from validate_marketplace import validate_marketplace_name

        for name in ("inline", "builtin", "skills-dir", "synced", "claude-plugin-test"):
            results = validate_marketplace_name(name, "test.json")
            assert any(
                r.level == "CRITICAL" and "reserved" in r.message for r in results
            ), (name, [(r.level, r.message) for r in results])

    def test_claudeai_prefix_refused(self):
        """A name starting with claudeai- is refused (v2.1.287)."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claudeai-test", "test.json")
        assert any(r.level == "CRITICAL" for r in results), [
            r.message for r in results
        ]

    def test_spelling_variant_dots_refused(self):
        """claude.code.plugins is refused as a spelling variant of claude-code-plugins."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("claude.code.plugins", "test.json")
        assert any(
            r.level == "CRITICAL" and "spelling variant" in r.message for r in results
        ), [(r.level, r.message) for r in results]

    def test_control_clean_name_clean(self):
        """Control: an ordinary name draws no reserved/impersonation finding."""
        from validate_marketplace import validate_marketplace_name

        results = validate_marketplace_name("zzz-clean-name-wp4", "test.json")
        blocking = [
            r
            for r in results
            if r.level in ("CRITICAL", "MAJOR")
            and ("reserved" in r.message or "impersonate" in r.message or "refused" in r.message)
        ]
        assert not blocking, [(r.level, r.message) for r in blocking]
