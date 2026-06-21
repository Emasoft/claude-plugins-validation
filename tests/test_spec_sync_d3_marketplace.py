"""D3 spec-sync — marketplace.json schema false-positive fixes (validate_marketplace.py).

Covers the 4 changes from reports/spec-sync/20260622_011403+0200-d3-marketplaces-channels.md
(CHANGE 1-4). Every test is TWO-SIDED: the now-accepted valid shape no longer
draws a spurious MAJOR, AND a genuinely-bogus sibling still MAJORs (so the fix
widened the allowlist without disabling the detector).

Doc citations (plugin-marketplaces.md, current):
- `sha` is a documented optional 40-hex commit-pin on github/url/git-subdir
  sources; `ref` is the documented branch/tag pin (CHANGE 1).
- `registry` is the documented custom npm-registry URL field (CHANGE 2).
- `displayName` (v2.1.143) is a standard-metadata plugin-entry field (CHANGE 3).
- The reserved-name list at plugin-marketplaces.md:164 (CHANGE 4).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Ensure scripts dir is on path (same pattern as test_validate_marketplace.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

A_VALID_SHA = "a" * 40  # documented 40-char hex commit pin


def _source_field_codes(plugin: dict[str, Any]) -> list[str]:
    """Run the source-subfield allowlist check and return the RC codes raised."""
    from validate_marketplace import _validate_known_source_subfields

    results = _validate_known_source_subfields(plugin, "demo-plugin", "marketplace.json")
    return [r.message for r in results if "RC-MKPL-UNKNOWN-SOURCE-FIELD" in r.message]


def _entry_field_codes(plugin: dict[str, Any]) -> list[str]:
    """Run the entry-field allowlist check and return the RC messages raised."""
    from validate_marketplace import _validate_known_entry_fields

    results = _validate_known_entry_fields(plugin, "demo-plugin", "marketplace.json")
    return [r.message for r in results if "RC-MKPL-UNKNOWN-FIELD" in r.message]


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 1 — `sha` (+ `ref` on url) accepted on github / url / git-subdir / git
# ─────────────────────────────────────────────────────────────────────────────
class TestSourceShaAccepted:
    """A 40-hex `sha` commit-pin is a documented source sub-field, not unknown."""

    def test_github_sha_accepted(self):
        """github source pinning a valid sha must NOT draw RC-MKPL-UNKNOWN-SOURCE-FIELD."""
        plugin = {"name": "p", "source": {"source": "github", "repo": "o/r", "sha": A_VALID_SHA}}
        assert _source_field_codes(plugin) == []

    def test_github_ref_and_sha_accepted(self):
        """github source may carry BOTH ref and sha (sha is the effective pin)."""
        plugin = {"name": "p", "source": {"source": "github", "repo": "o/r", "ref": "v1.2.3", "sha": A_VALID_SHA}}
        assert _source_field_codes(plugin) == []

    def test_url_ref_and_sha_accepted(self):
        """url source gains BOTH ref and sha (it lacked both in the old allowlist)."""
        plugin = {"name": "p", "source": {"source": "url", "url": "https://x/y.zip", "ref": "main", "sha": A_VALID_SHA}}
        assert _source_field_codes(plugin) == []

    def test_git_subdir_sha_accepted(self):
        """git-subdir source pinning a sha must NOT draw the unknown-subfield MAJOR."""
        plugin = {
            "name": "p",
            "source": {"source": "git-subdir", "url": "https://x/r.git", "path": "sub", "sha": A_VALID_SHA},
        }
        assert _source_field_codes(plugin) == []

    def test_git_sha_accepted(self):
        """CPV-extension git source (treated like url) also accepts sha."""
        plugin = {"name": "p", "source": {"source": "git", "url": "https://x/r.git", "sha": A_VALID_SHA}}
        assert _source_field_codes(plugin) == []

    def test_bogus_source_subfield_still_flagged(self):
        """A genuinely-unknown source sub-field still MAJORs (detector not disabled)."""
        plugin = {"name": "p", "source": {"source": "github", "repo": "o/r", "totallyBogus": "x"}}
        codes = _source_field_codes(plugin)
        assert len(codes) == 1
        assert "totallyBogus" in codes[0]

    def test_sha_alone_does_not_mask_a_bogus_sibling(self):
        """A valid sha next to a bogus sub-field: only the bogus one is flagged."""
        plugin = {"name": "p", "source": {"source": "github", "repo": "o/r", "sha": A_VALID_SHA, "nope": 1}}
        codes = _source_field_codes(plugin)
        assert len(codes) == 1
        assert "nope" in codes[0]


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 2 — `registry` accepted on the npm source
# ─────────────────────────────────────────────────────────────────────────────
class TestNpmRegistryAccepted:
    """`registry` is the documented custom npm-registry URL sub-field."""

    def test_npm_registry_accepted(self):
        """npm source with a custom registry must NOT draw RC-MKPL-UNKNOWN-SOURCE-FIELD."""
        plugin = {
            "name": "p",
            "source": {"source": "npm", "package": "@scope/pkg", "version": "1.0.0", "registry": "https://npm.example/"},
        }
        assert _source_field_codes(plugin) == []

    def test_npm_bogus_subfield_still_flagged(self):
        """A bogus npm sub-field still MAJORs (registry did not blanket-accept everything)."""
        plugin = {"name": "p", "source": {"source": "npm", "package": "pkg", "bogusNpmField": "x"}}
        codes = _source_field_codes(plugin)
        assert len(codes) == 1
        assert "bogusNpmField" in codes[0]


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 3 — `displayName` accepted as a plugin-entry field
# ─────────────────────────────────────────────────────────────────────────────
class TestDisplayNameAccepted:
    """`displayName` (v2.1.143) is a standard-metadata entry field, not unknown."""

    def test_display_name_accepted(self):
        """A marketplace entry with displayName must NOT draw RC-MKPL-UNKNOWN-FIELD."""
        plugin = {"name": "p", "source": "./p", "displayName": "Pretty Plugin"}
        assert _entry_field_codes(plugin) == []

    def test_unknown_entry_field_still_flagged(self):
        """A genuinely-unknown entry field still MAJORs (allowlist not disabled)."""
        plugin = {"name": "p", "source": "./p", "audience": "internal"}
        codes = _entry_field_codes(plugin)
        assert len(codes) == 1
        assert "audience" in codes[0]

    def test_display_name_in_known_set(self):
        """displayName is now part of the strict entry allowlist constant."""
        from validate_marketplace import _KNOWN_MARKETPLACE_ENTRY_FIELDS

        assert "displayName" in _KNOWN_MARKETPLACE_ENTRY_FIELDS


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE 4 — the 6 newly-reserved marketplace names
# ─────────────────────────────────────────────────────────────────────────────
class TestReservedNames:
    """The reserved-name list matches plugin-marketplaces.md:164 (current)."""

    def test_newly_reserved_names_present(self):
        """All 6 previously-missing reserved names are now flagged reserved."""
        from validate_marketplace import RESERVED_MARKETPLACE_NAMES

        for name in (
            "claude-plugins-community",
            "claude-community",
            "anthropic-agent-skills",
            "claude-for-legal",
            "claude-for-financial-services",
            "financial-services-plugins",
        ):
            assert name in RESERVED_MARKETPLACE_NAMES, name

    def test_non_reserved_name_still_allowed(self):
        """A normal community marketplace name is NOT reserved (not over-blocked)."""
        from validate_marketplace import RESERVED_MARKETPLACE_NAMES

        assert "emasoft-plugins" not in RESERVED_MARKETPLACE_NAMES

    def test_preexisting_reserved_names_retained(self):
        """The pre-existing reserved names were not dropped by the addition."""
        from validate_marketplace import RESERVED_MARKETPLACE_NAMES

        for name in (
            "claude-code-marketplace",
            "anthropic-plugins",
            "agent-skills",
            "life-sciences",
        ):
            assert name in RESERVED_MARKETPLACE_NAMES, name
