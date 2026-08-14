"""CC spec-drift sync for the v2.1.225 → v2.1.232 window.

Every assertion here is two-sided: each "this is now accepted / now detected"
case is paired with a control proving the same code path still rejects the
sibling it is supposed to reject. A one-sided sync test passes just as happily
against a validator that accepts everything.

The window's genuinely-new plugin-spec surface, verified against the RAW docs
(``plugin-marketplaces.md`` / ``settings.md``) rather than the changelog
summary, per the recorded spec-drift method:

* **v2.1.229 ``command`` plugin source** — a plugin directory produced by
  running a local command. New source type plus a shape check, because every
  constraint the doc states is a case Claude Code REFUSES at install.
* **v2.1.229 ``disableCommandPluginSources``** — the managed-only kill switch
  for that source.
* **v2.1.232 ``additionalMarketplaces`` / ``allowedMarketplaces``** — accepted
  aliases for two settings keys CPV already knew.
* **v2.1.232 GitLab token families** — nine prefixes the single ``glpat-``
  pattern matched none of.
* **v2.1.232 GitLab marketplace URLs** — which is what makes CPV's
  GitHub-only ``repository`` check an FP rather than merely a narrow rule.

``sandbox.ripgrep`` is deliberately NOT here: it is nested under the already-known
``sandbox`` key, whose sub-keys are tolerated, so no top-level entry is owed —
the ``sandbox.network.strictAllowlist`` precedent from v5.1.0.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cc_scope_rules  # noqa: E402
import cpv_validation_common as cvc  # noqa: E402
import validate_marketplace as vm  # noqa: E402


def _cmd_source(**overrides: object) -> dict[str, object]:
    src: dict[str, object] = {"source": "command", "command": "my-tool claude-plugin-path"}
    src.update(overrides)
    return src


def _check(src: object) -> list[str]:
    """Run the command-source shape check, returning the finding rule ids."""
    results = vm._validate_command_source({"source": src}, "p", "marketplace.json")
    return [r.message.split("]")[0].lstrip("[") for r in results]


# ─────────────────────────────────────────────────────────────────────────────
# v2.1.229 — the `command` source type is recognised at all
# ─────────────────────────────────────────────────────────────────────────────
def test_command_is_a_valid_source_type() -> None:
    assert "command" in vm.VALID_SOURCE_TYPES


def test_an_unknown_source_type_is_still_rejected() -> None:
    """The control. Without it, adding every string to the set would pass above."""
    assert "telepathy" not in vm.VALID_SOURCE_TYPES


def test_command_source_declares_its_documented_fields() -> None:
    assert vm._KNOWN_SOURCE_FIELDS_BY_TYPE["command"] == frozenset({"source", "command", "timeout", "mode"})


def test_command_is_absent_from_source_required_fields_like_archive() -> None:
    """Deliberate, and it follows the ``archive`` precedent rather than diverging.

    Neither type appears in ``SOURCE_REQUIRED_FIELDS``: their required field is
    checked by the per-type shape function, which reports the SPECIFIC rule id a
    fixer can route on. Listing them in both places would emit two findings for
    one defect — and a validator that double-counts trains its reader to skim.
    """
    assert "command" not in vm.SOURCE_REQUIRED_FIELDS
    assert "archive" not in vm.SOURCE_REQUIRED_FIELDS


# ─────────────────────────────────────────────────────────────────────────────
# v2.1.229 — the `command` source shape check
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "src",
    [
        _cmd_source(),
        _cmd_source(timeout=600, mode="link"),
        _cmd_source(timeout=1, mode="copy"),
    ],
    ids=["minimal", "max-timeout-link", "min-timeout-copy"],
)
def test_a_well_formed_command_source_is_clean(src: dict[str, object]) -> None:
    assert _check(src) == []


def test_a_non_command_source_is_untouched() -> None:
    """Scoping control — the check must not fire on somebody else's source type."""
    assert _check({"source": "archive", "url": "https://example.com/p.zip"}) == []


@pytest.mark.parametrize(
    ("src", "rule"),
    [
        ({"source": "command"}, "RC-MKPL-COMMAND-CMD"),
        (_cmd_source(command=""), "RC-MKPL-COMMAND-CMD"),
        (_cmd_source(command="x" * 501), "RC-MKPL-COMMAND-CMD"),
        (_cmd_source(command="my-tool\tp"), "RC-MKPL-COMMAND-CMD"),
        (_cmd_source(command="my-tool p" + " " * 4 + "; rm -rf /"), "RC-MKPL-COMMAND-CMD"),
        (_cmd_source(timeout=601), "RC-MKPL-COMMAND-TIMEOUT"),
        (_cmd_source(timeout=0), "RC-MKPL-COMMAND-TIMEOUT"),
        (_cmd_source(timeout=1.5), "RC-MKPL-COMMAND-TIMEOUT"),
        (_cmd_source(mode="symlink"), "RC-MKPL-COMMAND-MODE"),
    ],
    ids=[
        "no-command",
        "empty-command",
        "over-500-chars",
        "non-printable-ascii",
        "four-space-run",
        "timeout-over-max",
        "timeout-zero",
        "timeout-fractional",
        "unknown-mode",
    ],
)
def test_a_refused_command_source_is_reported(src: dict[str, object], rule: str) -> None:
    assert rule in _check(src)


def test_a_boolean_timeout_is_reported() -> None:
    """``bool`` is a subclass of ``int``, so ``True`` would otherwise read as 1 second.

    Kept as its own case because it is the one that passes a naive
    ``isinstance(timeout, int)`` check — a reviewer scanning the parametrize
    list above would not notice its absence.
    """
    assert "RC-MKPL-COMMAND-TIMEOUT" in _check(_cmd_source(timeout=True))


# ─────────────────────────────────────────────────────────────────────────────
# v2.1.229 / v2.1.232 — settings keys
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "key",
    ["disableCommandPluginSources", "crossSessionInbound", "additionalMarketplaces", "allowedMarketplaces"],
)
def test_new_settings_keys_are_known(key: str) -> None:
    assert key in cc_scope_rules.KNOWN_SETTINGS_KEYS


@pytest.mark.parametrize(
    "typo",
    ["disableCommandPluginSource", "crossSessionInbounds", "additionalMarketplace", "allowedMarketplace"],
)
def test_a_near_miss_typo_is_still_unknown(typo: str) -> None:
    """The control that keeps the set a TYPO DETECTOR rather than a rubber stamp."""
    assert typo not in cc_scope_rules.KNOWN_SETTINGS_KEYS


def test_disable_command_plugin_sources_is_managed_only() -> None:
    """settings.md:267 states "(Managed settings only)".

    It blocks a source type that RUNS a marketplace-declared command on the
    user's machine, so an org that can be overridden from a project file does
    not actually have the kill switch it thinks it has.
    """
    assert "disableCommandPluginSources" in cc_scope_rules.MANAGED_ONLY_KEYS


def test_cross_session_inbound_is_not_managed_only() -> None:
    """Control for the row above — settings.md:729 honors a STRICTER project value."""
    assert "crossSessionInbound" not in cc_scope_rules.MANAGED_ONLY_KEYS


def test_sandbox_ripgrep_is_not_added_as_a_top_level_key() -> None:
    """DELIBERATE OMISSION, pinned with its reason so the next sync does not "fix" it.

    v2.1.232 restricts ``sandbox.ripgrep`` to user/managed/``--settings``
    sources. It is NESTED under ``sandbox``, which is already a known key whose
    sub-keys are tolerated, so no entry is owed here — and a bare ``ripgrep``
    entry at top level would excuse a genuine typo written there.
    """
    assert "ripgrep" not in cc_scope_rules.KNOWN_SETTINGS_KEYS


# ─────────────────────────────────────────────────────────────────────────────
# v2.1.232 — GitLab token families in the secret scanner
# ─────────────────────────────────────────────────────────────────────────────
def _secret_hits(text: str) -> list[str]:
    return [name for pattern, name in cvc.SECRET_PATTERNS if pattern.search(text)]


@pytest.mark.parametrize(
    "prefix",
    ["glrt", "gloas", "glptt", "glagent", "glimt", "glsoat", "glcbt", "glft", "glffct", "gldt"],
)
def test_gitlab_token_families_are_detected(prefix: str) -> None:
    assert _secret_hits(f"{prefix}-abcdefghij1234567890AB")


def test_the_original_glpat_token_still_detects() -> None:
    """Regression control — the new alternation must not have displaced it."""
    assert "GitLab Personal Access Token" in _secret_hits("glpat-abcdefghij1234567890AB")


@pytest.mark.parametrize(
    "text",
    [
        "glrt-short",
        "glxx-abcdefghij1234567890AB",
        "globalthing-abcdefghij1234567890AB",
        "regular prose with no token at all",
    ],
    ids=["too-short", "unknown-prefix", "prefix-is-a-word-fragment", "prose"],
)
def test_non_tokens_do_not_match_the_gitlab_families(text: str) -> None:
    assert not [n for n in _secret_hits(text) if "GitLab" in n]


# ─────────────────────────────────────────────────────────────────────────────
# v2.1.232 — the `repository` check is host-agnostic
# ─────────────────────────────────────────────────────────────────────────────
def _repo_findings(repository: str) -> list[str]:
    results = vm.validate_github_source_required(
        [{"name": "p", "source": "./p", "repository": repository}], "marketplace.json"
    )
    return [r.level for r in results if r.level in ("MINOR", "MAJOR")]


@pytest.mark.parametrize(
    "repository",
    [
        "https://gitlab.com/team/plugins",
        "https://gitlab.com/group/subgroup/nested/plugin",
        "git@gitlab.com:team/plugin.git",
        "git@github.com:owner/repo.git",
        "https://bitbucket.org/team/plugin",
        "ssh://git@git.acme.internal/team/plugin.git",
        "https://github.com/owner/repo",
        "owner/repo",
    ],
    ids=[
        "gitlab-https",
        "gitlab-nested-subgroups",
        "gitlab-ssh",
        "github-ssh",
        "bitbucket",
        "self-hosted-ssh-url",
        "github-https",
        "shorthand",
    ],
)
def test_any_git_host_repository_url_is_accepted(repository: str) -> None:
    """The FP half. A MINOR blocks ``--strict``, so this used to be a publish gate.

    The spec calls ``repository`` a "source code repository URL" with no host
    constraint, and v2.1.232 made bare gitlab.com marketplace URLs clone like
    github.com ones — so demanding GitHub was a gate CPV invented.
    """
    assert _repo_findings(repository) == []


@pytest.mark.parametrize(
    "repository",
    ["not-a-repository-url", "ftp://example.com/x", "a/b/c/d", "just some words"],
    ids=["bare-word", "unusual-scheme", "too-many-segments", "prose"],
)
def test_a_malformed_repository_url_still_fires(repository: str) -> None:
    """The FN half — widening the accepted hosts must not accept everything."""
    assert _repo_findings(repository)
