"""CC v2.1.284 spec sync: `slides` command, 6 env vars, 14 managed-only keys,
4 managed-only removals (scope re-verified against the raw doc), `settings`
manifest field.

Every "now accepted/known" assertion is paired with a positive control proving
the detector still fires, so no acceptance test passes vacuously.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cc_scope_rules  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
from validate_command import validate_command  # noqa: E402
from validate_plugin import validate_manifest  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402

NEW_BUILTIN_SLASH = ["slides"]
NEW_ENV_VARS = [
    "CLAUDE_CODE_PLUGIN_DIRS",
    "CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUT",
    "CLAUDE_CODE_DISABLE_SUBSTITUTION_RM_PROMPT",
    "CLAUDE_CODE_DISABLE_POWERSHELL_CMD_RM_DENY",
    "VERTEX_REGION_CLAUDE_5_5_SONNET",
    "VERTEX_REGION_CLAUDE_5_5_OPUS",
]
ADDED_MANAGED_ONLY_KEYS = [
    "browserExternalPageTools",
    "claudeMd",
    "disableBrowserExternalNavigation",
    "disableDesktopLocalSessions",
    "disableMobileSimulatorTools",
    "disableSideloadFlags",
    "forceLoginGatewayUrl",
    "managedSourcesBehavior",
    "modelPricing",
    "policyHelper",
    "requiredMaximumVersion",
    "requiredMinimumVersion",
    "sshHostAllowlist",
    "strictPluginOnlyCustomization",
]
REMOVED_MANAGED_ONLY_KEYS = [
    "allowedMcpServers",
    "deniedMcpServers",
    "forceLoginOrgUUID",
]
# Re-added post-review (R8): the key's scope is PER-VALUE — only the "gateway"
# value is managed-enforced; claudeai/console are honored from any file. The
# per-key set cannot express that, so the ambiguous→KEEP direction wins.
RE_ADDED_PER_VALUE_KEYS = ["forceLoginMethod"]


# ────────────────────────────── BUILTIN_SLASH_COMMANDS ──────────────────────


def test_slides_is_builtin() -> None:
    """/slides (bundled Claude Slides skill command, commands.md:142) is built-in."""
    assert "slides" in cvc.BUILTIN_SLASH_COMMANDS


def test_slides_collision_now_warns(tmp_path: Path) -> None:
    """A plugin command named `slides` NOW draws the built-in collision WARNING.

    The set previously lacked `slides` (commands.md:142, bundled Slides skill
    command), so a plugin shipping commands/slides.md silently collided — the
    FN direction. Note: the apply spec's test line said the add "stops drawing"
    the warning, which inverts the check's semantics; the sibling 236_240 sync
    (test_collision_warns_end_to_end) and the scan report's FN framing are the
    authoritative reading.
    """
    cmd = tmp_path / "slides.md"
    cmd.write_text("---\ndescription: Plugin command shadowing the Slides skill command.\n---\nBody.\n")
    report = validate_command(cmd)
    assert any("built-in" in r.message.lower() for r in report.results), [
        r.message for r in report.results
    ]


def test_control_preexisting_builtin_still_warns(tmp_path: Path) -> None:
    """Control: an established built-in name (`plan`) still draws the WARNING."""
    cmd = tmp_path / "plan.md"
    cmd.write_text("---\ndescription: Probe command shadowing a built-in name.\n---\nBody.\n")
    report = validate_command(cmd)
    assert any("built-in" in r.message.lower() for r in report.results)


def test_control_non_builtin_is_silent(tmp_path: Path) -> None:
    """Control: a name no built-in uses draws no collision finding."""
    cmd = tmp_path / "zzz-not-a-builtin-284.md"
    cmd.write_text("---\ndescription: Probe command with a name no built-in uses.\n---\nBody.\n")
    report = validate_command(cmd)
    assert not any("built-in" in r.message.lower() for r in report.results)


# ───────────────────────────── VALID_PLUGIN_ENV_VARS ────────────────────────


@pytest.mark.parametrize("name", NEW_ENV_VARS)
def test_new_env_var_is_valid(name: str) -> None:
    """Each v2.1.280–284 in-window doc var clears the unknown-env-var check."""
    assert cvc.is_valid_plugin_env_var(name), name


@pytest.mark.parametrize(
    "name",
    [
        "CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUTX",
        "VERTEX_REGION_CLAUDE_5_5_HAIKU",  # sibling model not documented
        "CLAUDE_CODE_PLUGIN_DIRECTORY",  # near-miss of CLAUDE_CODE_PLUGIN_DIRS
    ],
)
def test_control_bogus_env_var_still_unknown(name: str) -> None:
    """Control: the set was widened by six names, not opened up."""
    assert not cvc.is_valid_plugin_env_var(name), name


# ────────────────────────────── MANAGED_ONLY_KEYS ───────────────────────────


@pytest.mark.parametrize("key", ADDED_MANAGED_ONLY_KEYS)
def test_added_key_is_managed_only(key: str) -> None:
    """Each doc-managed key (settings-reference Managed-scope row) is in the set."""
    assert key in cc_scope_rules.MANAGED_ONLY_KEYS, key


@pytest.mark.parametrize("key", ADDED_MANAGED_ONLY_KEYS)
def test_added_key_in_project_scope_is_major(tmp_path: Path, key: str) -> None:
    """A project-scope value of an added managed-only key draws the MAJOR."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({key: True}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    hits = [r for r in report.results if key in r.message]
    assert hits, f"{key} drew no finding in project scope"
    assert any(r.level == "MAJOR" for r in hits), [(r.level, r.message) for r in hits]


@pytest.mark.parametrize("key", REMOVED_MANAGED_ONLY_KEYS)
def test_removed_key_no_longer_flagged_in_project_scope(tmp_path: Path, key: str) -> None:
    """Scope row AND section body say Any file — project placement is honored.

    Verified 2026-09-29 against the raw settings-reference.md fetch: the
    allowed/denied lists merge across files ("deploy in managed to enforce"),
    and forceLoginOrgUUID pre-selects the org from a non-managed single UUID.
    """
    assert key not in cc_scope_rules.MANAGED_ONLY_KEYS, key
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({key: [] if "Mcp" in key else "claudeai"}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    blocking = [
        r
        for r in report.results
        if key in r.message and r.level in ("CRITICAL", "MAJOR", "MINOR", "NIT")
    ]
    assert not blocking, [(r.level, r.message) for r in blocking]


@pytest.mark.parametrize("key", REMOVED_MANAGED_ONLY_KEYS)
def test_removed_key_still_known(key: str) -> None:
    """Control: removal was from MANAGED_ONLY_KEYS only — the keys stay known
    (a bare typo variant still draws the unknown-key hint)."""
    assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS, key
    assert key + "x" not in cc_scope_rules.KNOWN_SETTINGS_KEYS


@pytest.mark.parametrize("key", RE_ADDED_PER_VALUE_KEYS)
def test_per_value_key_back_in_managed_only(key: str) -> None:
    """forceLoginMethod is back in MANAGED_ONLY_KEYS: only its "gateway" value
    is managed-enforced per settings-reference.md + managed-settings.md, and a
    per-key set cannot express per-value scope — ambiguous→KEEP wins (R8)."""
    assert key in cc_scope_rules.MANAGED_ONLY_KEYS, key


def test_control_managed_key_still_major(tmp_path: Path) -> None:
    """Control: an untouched managed-only key still MAJORs in project scope."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"allowManagedHooksOnly": True}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    assert any(
        "allowManagedHooksOnly" in r.message and r.level == "MAJOR" for r in report.results
    )


def test_readded_key_gateway_value_majors_end_to_end(tmp_path: Path) -> None:
    """End-to-end control for the R8 re-add (membership alone is the
    vacuous-source-pin shape): a project file carrying forceLoginMethod:"gateway"
    — the one genuinely managed-only value — draws the MAJOR through the real
    validator, not just via set membership."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"forceLoginMethod": "gateway"}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    assert any(
        "forceLoginMethod" in r.message and r.level == "MAJOR" for r in report.results
    )


def test_readded_key_known_false_major_documented(tmp_path: Path) -> None:
    """The re-add's known trade (R9 Q1): claudeai/console from a project file
    are honored by CC but draw the MAJOR anyway — the per-key set cannot express
    per-value scope. Pin the false MAJOR as a KNOWN limitation so a later
    per-value rule (TRDD-2JJD4NA0) flips exactly this assertion."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"forceLoginMethod": "claudeai"}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    assert any(
        "forceLoginMethod" in r.message and r.level == "MAJOR" for r in report.results
    )


# ───────────────────────────── manifest known_fields ────────────────────────


def _plugin(tmp_path: Path, extra: dict[str, object]) -> Path:
    (tmp_path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    base: dict[str, object] = {"name": "demo", "version": "1.0.0", "description": "d"}
    base.update(extra)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(base), encoding="utf-8"
    )
    return tmp_path


def test_settings_manifest_field_not_unknown(tmp_path: Path) -> None:
    """A manifest declaring `settings` (manifest-reference.md:139) draws no
    unknown-field WARNING; the doc says only agent + subagentStatusLine take
    effect, and a root-level settings.json takes precedence — no shape check."""
    report = cvc.ValidationReport()
    validate_manifest(_plugin(tmp_path, {"settings": {"agent": "op"}}), report)
    assert not [r for r in report.results if "Unknown manifest field 'settings'" in r.message]


def test_control_bogus_manifest_field_still_warns(tmp_path: Path) -> None:
    """Control: a genuinely unknown manifest field still warns."""
    report = cvc.ValidationReport()
    validate_manifest(_plugin(tmp_path, {"settings284": {}}), report)
    hits = [r for r in report.results if "Unknown manifest field 'settings284'" in r.message]
    assert hits, "bogus key drew no unknown-field finding"
    assert any(r.level == "WARNING" for r in hits)
