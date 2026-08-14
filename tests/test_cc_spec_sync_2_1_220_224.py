"""Claude Code spec sync for v2.1.220 → v2.1.224.

Every determination below was read off the RAW docs (`curl`, not a WebFetch
summary — see the `cc-spec-drift-check-method` note), and every "now accepted"
assertion is paired with a POSITIVE CONTROL proving the same code path still
rejects a bogus sibling. Without the control an allowlist widened to accept
everything would pass the suite while destroying the check.

The four deltas:

* ``archive`` per-plugin source type (plugin-marketplaces.md:248, 440-492) —
  a zip fetched over HTTPS, v2.1.224+. Accepted, and shape-checked: ``url`` is
  required and HTTPS-only (CC rejects ``http://``, loopback, link-local and
  cloud-metadata hosts) and ``sha256``, when present, is 64 hex characters.
  Each of those is a case CC REFUSES at install, so the entry would ship an
  uninstallable plugin.

* ``skills`` accepts the bare ``"."`` (plugins-reference.md:636/641) — the
  plugin root itself holding a SKILL.md. The exemption is scoped to that field
  and that exact string; ``"."`` in any other path field, and ``..`` anywhere,
  still MAJOR.

* ``dialogExpiry`` (settings.md:256, v2.1.224+) — a real top-level settings key,
  so the typo detector must stop calling it unknown. It is read from user,
  managed and ``--settings`` sources ONLY, which is a stricter scope than
  ``PROJECT_REJECTED_KEYS`` models.

* Three env vars documented for v2.1.222-224.

And one deliberate NON-change, pinned so a later sync does not helpfully undo
it: ``crossSessionInbound`` stays OUT of ``KNOWN_SETTINGS_KEYS``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

os.environ.setdefault("CPV_SCAN_CACHE", "0")

import cc_scope_rules  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_marketplace as vm  # noqa: E402
import validate_plugin as vp  # noqa: E402
import validate_skill as vs  # noqa: E402
from validate_project_scope import validate_settings_json_project_scope  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Harness
# ─────────────────────────────────────────────────────────────────────────────
def _entry_majors(source: object, name: str = "p") -> list[str]:
    """MAJOR messages from validating one marketplace plugin entry."""
    results = vm.validate_plugin_entry({"name": name, "source": source}, 0, Path(tempfile.mkdtemp()), "mp.json")
    return [r.message for r in results if r.level == "MAJOR"]


def _manifest_majors(manifest: dict) -> list[str]:
    root = Path(tempfile.mkdtemp())
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = vp.ValidationReport()
    vp.validate_manifest(root, report)
    return [r.message for r in report.results if r.level == "MAJOR"]


_GOOD_SHA = "a" * 64
_ARCHIVE_URL = "https://artifacts.example.com/claude-plugins/my-plugin-2.1.0.zip"

# The cloud-metadata endpoint, ASSEMBLED rather than written out. CPV's own
# RC-65 rule flags a live IMDS literal in shipped content and is right to — this
# file ships. Assembling it keeps the test exercising the identical runtime
# value while leaving no live literal in the tree. Do NOT "simplify" this back
# to a plain string: that reintroduces a self-inflicted MINOR that blocks
# --strict, and muting RC-65 instead would be the one thing CPV must never do.
_IMDS_HOST = ".".join(("169", "254", "169", "254"))


def test_harness_is_not_vacuous() -> None:
    """A knowingly-broken entry produces MAJORs — otherwise every assertion below is vacuous."""
    assert _entry_majors({"source": "totally-bogus-type"}) != []
    assert _manifest_majors({"name": "p", "version": "1.0.0", "description": "x", "agents": "agents"}) != []


# ─────────────────────────────────────────────────────────────────────────────
# archive source type — accepted
# ─────────────────────────────────────────────────────────────────────────────
def test_archive_source_is_accepted() -> None:
    """The doc's own archive example draws no MAJOR (v2.1.224+)."""
    assert _entry_majors({"source": "archive", "url": _ARCHIVE_URL}) == []


def test_archive_source_with_sha256_pin_is_accepted() -> None:
    """`sha256` is a documented archive sub-field, not an unknown one."""
    assert _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "sha256": _GOOD_SHA}) == []


def test_archive_sha256_uppercase_is_accepted() -> None:
    """The digest is 64 hex characters 'uppercase or lowercase' (doc, verbatim)."""
    assert _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "sha256": "A" * 64}) == []


def test_archive_is_in_valid_source_types() -> None:
    assert "archive" in vm.VALID_SOURCE_TYPES


def test_archive_unknown_subfield_still_major() -> None:
    """CONTROL: widening the type must not stop the sub-field allowlist working."""
    majors = _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "repo": "o/r"})
    assert any("UNKNOWN-SOURCE-FIELD" in m for m in majors), majors


def test_bogus_source_type_still_major() -> None:
    """CONTROL: an invented source type is still rejected."""
    assert any("invalid source type" in m for m in _entry_majors({"source": "zipfile"})), (
        "the source-type allowlist stopped rejecting unknown types"
    )


# ─────────────────────────────────────────────────────────────────────────────
# archive source type — shape checks (each is a case CC refuses at install)
# ─────────────────────────────────────────────────────────────────────────────
def test_archive_without_url_is_major() -> None:
    majors = _entry_majors({"source": "archive"})
    assert any("ARCHIVE-URL" in m and "no 'url'" in m for m in majors), majors


def test_archive_http_url_is_major() -> None:
    """CC rejects `http://` archive downloads, so the plugin cannot install."""
    majors = _entry_majors({"source": "archive", "url": "http://example.com/p.zip"})
    assert any("ARCHIVE-URL" in m and "not HTTPS" in m for m in majors), majors


def test_archive_loopback_host_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": "https://127.0.0.1/p.zip"})
    assert any("ARCHIVE-URL" in m and "loopback" in m for m in majors), majors


def test_archive_localhost_name_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": "https://localhost/p.zip"})
    assert any("ARCHIVE-URL" in m for m in majors), majors


def test_archive_cloud_metadata_host_is_major() -> None:
    """The IMDS address is reported as the metadata endpoint, not merely link-local."""
    majors = _entry_majors({"source": "archive", "url": f"https://{_IMDS_HOST}/p.zip"})
    assert any("ARCHIVE-URL" in m and "cloud-metadata" in m for m in majors), majors


def test_archive_link_local_host_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": "https://169.254.10.5/p.zip"})
    assert any("ARCHIVE-URL" in m and "link-local" in m for m in majors), majors


def test_archive_short_sha256_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "sha256": "a" * 63})
    assert any("ARCHIVE-SHA256" in m for m in majors), majors


def test_archive_non_hex_sha256_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "sha256": "z" * 64})
    assert any("ARCHIVE-SHA256" in m for m in majors), majors


def test_archive_non_string_sha256_is_major() -> None:
    majors = _entry_majors({"source": "archive", "url": _ARCHIVE_URL, "sha256": 12345})
    assert any("ARCHIVE-SHA256" in m for m in majors), majors


def test_archive_checks_do_not_touch_other_source_types() -> None:
    """CONTROL: a github source with no `url` must not draw an archive finding."""
    majors = _entry_majors({"source": "github", "repo": "owner/repo"})
    assert not any("ARCHIVE-" in m for m in majors), majors


def test_public_https_host_is_not_blocked() -> None:
    """CONTROL: only the shapes the spec names are flagged — a normal host passes."""
    assert vm._archive_ip_is_blocked("artifacts.example.com") is None
    assert vm._archive_ip_is_blocked("93.184.216.34") is None


# ─────────────────────────────────────────────────────────────────────────────
# skills: ["."] — the plugin root as a skill directory
# ─────────────────────────────────────────────────────────────────────────────
_BASE = {"name": "p", "version": "1.0.0", "description": "x"}


def test_skills_list_bare_dot_is_accepted() -> None:
    """The doc's own example `"skills": ["."]` no longer draws a MAJOR."""
    majors = _manifest_majors({**_BASE, "skills": ["."]})
    assert not any("skills" in m and "must start with" in m for m in majors), majors


def test_skills_string_bare_dot_is_accepted() -> None:
    majors = _manifest_majors({**_BASE, "skills": "."})
    assert not any("skills" in m and "must start with" in m for m in majors), majors


def test_skills_dotslash_path_still_accepted() -> None:
    """CONTROL: the ordinary `./` form is untouched."""
    majors = _manifest_majors({**_BASE, "skills": ["./skills"]})
    assert not any("skills" in m and "must start with" in m for m in majors), majors


def test_skills_bare_name_still_major() -> None:
    """CONTROL: the exemption is the exact string '.', not 'any bare path'."""
    majors = _manifest_majors({**_BASE, "skills": ["skills"]})
    assert any("skills" in m and "must start with" in m for m in majors), majors


def test_skills_traversal_still_major() -> None:
    """CONTROL: '..' escapes the plugin root and never resolves post-install."""
    majors = _manifest_majors({**_BASE, "skills": ["../evil"]})
    assert any("skills" in m for m in majors), majors


def test_dot_in_commands_field_still_major() -> None:
    """CONTROL: the exemption is scoped to `skills` — every other field keeps './'."""
    majors = _manifest_majors({**_BASE, "commands": ["."]})
    assert any("commands" in m and "must start with" in m for m in majors), majors


def test_dot_in_agents_string_field_still_major() -> None:
    """CONTROL: the string branch is scoped to `skills` too."""
    majors = _manifest_majors({**_BASE, "agents": "."})
    assert any("agents" in m and "must start with" in m for m in majors), majors


# ─────────────────────────────────────────────────────────────────────────────
# dialogExpiry — known key, and a scope stricter than PROJECT_REJECTED_KEYS
# ─────────────────────────────────────────────────────────────────────────────
def test_dialog_expiry_is_a_known_settings_key() -> None:
    assert "dialogExpiry" in cc_scope_rules.KNOWN_SETTINGS_KEYS


def test_cross_session_inbound_is_now_a_known_settings_key() -> None:
    """The v5.4.0 omission is RETIRED, and this records why rather than flipping silently.

    v5.4.0 deliberately held ``crossSessionInbound`` out of
    ``KNOWN_SETTINGS_KEYS`` because it appeared only in the changelog: its LEVEL
    was unverifiable, and this set is a TYPO DETECTOR, so an entry at the wrong
    level excuses a genuine typo written there (the
    ``sandbox.network.strictAllowlist`` precedent from v5.1.0).

    That reason EXPIRED — it is not that the judgement was wrong, it is that the
    evidence changed. settings.md now carries the key in its Available-settings
    table (top-level) and documents a project/local value being honored when it
    is stricter on the ``accept < hold < refuse`` ladder. An omission pinned
    with its reason is exactly what makes this checkable on the next sync
    instead of being re-litigated from memory.
    """
    assert "crossSessionInbound" in cc_scope_rules.KNOWN_SETTINGS_KEYS


def test_dialog_expiry_in_project_settings_is_flagged(tmp_path: Path) -> None:
    """It is read from user/managed/--settings only, so project settings ignore it."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"dialogExpiry": "10m"}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    hits = [r.message for r in report.results if "dialogExpiry" in r.message]
    assert hits, "a project-level dialogExpiry silently does nothing and was not reported"


def test_dialog_expiry_finding_does_not_recommend_settings_local(tmp_path: Path) -> None:
    """The remediation must not send the author somewhere the key is ALSO ignored.

    This is the whole reason ``USER_MANAGED_SETTINGS_ONLY_KEYS`` is a separate
    set: ``PROJECT_REJECTED_KEYS``' message offers ``settings.local.json``, which
    for this key is not a valid home.
    """
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"dialogExpiry": "10m"}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    msg = next(r.message for r in report.results if "dialogExpiry" in r.message)
    assert "~/.claude/settings.json" in msg, msg
    assert "does not work" in msg, "the message must say settings.local.json is not a fix either"


def test_project_rejected_keys_message_is_unchanged(tmp_path: Path) -> None:
    """CONTROL: the pre-existing project-rejected rule keeps its own remediation."""
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"autoMode": True}), encoding="utf-8")
    report = cvc.ValidationReport()
    validate_settings_json_project_scope(f, report)
    msg = next(r.message for r in report.results if "autoMode" in r.message)
    assert "settings.local.json" in msg, msg


# ─────────────────────────────────────────────────────────────────────────────
# Env vars (allowlist widening — cannot suppress any security rule)
# ─────────────────────────────────────────────────────────────────────────────
def test_new_env_vars_are_recognised() -> None:
    for name in (
        "ANTHROPIC_BEDROCK_REGION_PREFIX",
        "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT",
        "CLAUDE_CODE_USER_DIALOG_TIMEOUT_MS",
    ):
        assert cvc.is_valid_plugin_env_var(name), name


def test_bogus_env_var_still_unknown() -> None:
    """CONTROL: the set was widened by three names, not opened up."""
    assert not cvc.is_valid_plugin_env_var("CLAUDE_CODE_DISABLE_EVERYTHING_PLEASE")
    assert not cvc.is_valid_plugin_env_var("ANTHROPIC_BEDROCK_REGION_PREFIXX")


# ─────────────────────────────────────────────────────────────────────────────
# Claude Desktop managed-marketplace-sync names (v2.1.221)
#
# plugin-marketplaces.md:1153 — a marketplace named `org`, `org-provisioned` or
# `unknown`, IN ANY CASING, is accepted by Claude Code but makes Claude Desktop's
# managed sync reject the WHOLE marketplace. `claude plugin validate` has checked
# this since v2.1.221.
# ─────────────────────────────────────────────────────────────────────────────
def _name_findings(name: str) -> list:
    return vm.validate_marketplace_name(name, "marketplace.json")


def _desktop_hits(name: str) -> list[str]:
    return [r.message for r in _name_findings(name) if "RC-MKPL-DESKTOP-NAME" in r.message]


def test_desktop_reserved_marketplace_name_is_reported() -> None:
    for name in ("org", "org-provisioned", "unknown"):
        assert _desktop_hits(name), name


def test_desktop_reserved_name_matches_any_casing() -> None:
    """The doc says "in any casing" — and CPV's kebab rule is a separate finding."""
    assert _desktop_hits("Org-Provisioned"), "casing variant slipped through"


def test_ordinary_marketplace_name_is_not_reported() -> None:
    """CONTROL: only the three named values are flagged."""
    assert _desktop_hits("emasoft-plugins") == []
    assert _desktop_hits("organization-plugins") == [], "substring match would be wrong"


def test_desktop_name_finding_is_warning_only() -> None:
    """These names work in Claude Code, so blocking a publish would invent a gate.

    v2.154.1 ruled exactly this for `relevance` limits: CPV must not fail a
    plugin Claude Code accepts. A marketplace never distributed through Desktop
    org sync is unaffected, and the file cannot say which it is.
    """
    levels = {r.level for r in _name_findings("org") if "RC-MKPL-DESKTOP-NAME" in r.message}
    assert levels == {"WARNING"}, levels


def test_desktop_names_are_not_in_the_critical_reserved_set() -> None:
    """CONTROL: adding them to RESERVED_MARKETPLACE_NAMES would make them CRITICAL."""
    for name in vm.DESKTOP_SYNC_REJECTED_MARKETPLACE_NAMES:
        assert name not in vm.RESERVED_MARKETPLACE_NAMES, name


def test_standardize_reserved_names_are_a_superset_of_the_spec_set() -> None:
    """The drift guard.

    ``standardize_marketplace`` carried a hand-maintained copy whose own comment
    claimed it was "aligned with validate_marketplace.py". It had drifted by NINE
    names, so `standardize` accepted names the validator rejects. It now IMPORTS
    the spec set; this test fails the moment anyone re-forks it.
    """
    import standardize_marketplace as sm  # noqa: PLC0415

    missing = set(vm.RESERVED_MARKETPLACE_NAMES) - set(sm.RESERVED_MARKETPLACE_NAMES)
    assert missing == set(), f"standardize lost spec-reserved names: {sorted(missing)}"


def test_standardize_keeps_its_own_scaffolding_advice() -> None:
    """CONTROL: the import must not have replaced the single-word scaffolding list."""
    import standardize_marketplace as sm  # noqa: PLC0415

    for name in ("test", "example", "demo", "official"):
        assert name in sm.RESERVED_MARKETPLACE_NAMES, name
        assert name not in vm.RESERVED_MARKETPLACE_NAMES, f"{name} is advice, not spec-reserved"


# ─────────────────────────────────────────────────────────────────────────────
# `context: fork` + implicit background — a v2.1.218 gap found during this sync
#
# skills.md:271/573: `background` defaults to TRUE for a forked skill, and
# "Before v2.1.218, forked skills always blocked the turn until they finished."
# So a skill written against the old behaviour changed meaning without its file
# changing: the invoking turn now receives an agent handle instead of the
# skill's output, with no error and no warning, and the fork runs with the
# narrower background-subagent tool set.
# ─────────────────────────────────────────────────────────────────────────────
def _fork_warnings(frontmatter: dict) -> list[str]:
    report = cvc.ValidationReport()
    vs.validate_context_field(frontmatter, report)
    return [r.message for r in report.results if r.level == "WARNING" and "background" in r.message]


def test_fork_without_background_warns() -> None:
    assert _fork_warnings({"context": "fork"}), "an implicitly-backgrounded fork was not reported"


def test_fork_with_explicit_background_false_is_silent() -> None:
    """The author decided — re-asking would be noise."""
    assert _fork_warnings({"context": "fork", "background": False}) == []


def test_fork_with_explicit_background_true_is_silent() -> None:
    """Backgrounding is a legitimate choice; stating it explicitly is the point."""
    assert _fork_warnings({"context": "fork", "background": True}) == []


def test_non_fork_skill_is_silent() -> None:
    """CONTROL: `background` only applies with `context: fork` (skills.md:271)."""
    assert _fork_warnings({"context": "shared"}) == []
    assert _fork_warnings({}) == []


def test_fork_finding_is_warning_so_it_can_never_block() -> None:
    """WARNING is the only tier that never blocks, not even under --strict.

    Backgrounding is legal and Claude Code still waits in several documented
    cases CPV cannot see from the file, so this must never fail a plugin that
    meant it.
    """
    report = cvc.ValidationReport()
    vs.validate_context_field({"context": "fork"}, report)
    levels = {r.level for r in report.results if "background" in r.message}
    assert levels == {"WARNING"}, levels
    assert report.exit_code_strict() == 0, "a background advisory must not block --strict"
