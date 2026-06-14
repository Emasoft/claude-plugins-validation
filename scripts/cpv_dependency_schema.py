#!/usr/bin/env python3
"""Single source of truth for the plugin-dependency element schema.

Both ``validate_plugin.py`` (the ``plugin.json`` ``dependencies`` array) and
``validate_marketplace.py`` (a marketplace plugin-entry's ``dependencies``
array) validate the SAME dependency-element shape, per plugin-dependencies.md
and CPV's v2.22.3 GAP-6 policy (a marketplace entry mirrors the manifest
schema, so its ``dependencies`` field shares the manifest schema). Historically
the two validators carried divergent copies: ``validate_plugin`` accepted both
the bare-string and object forms, while ``validate_marketplace`` rejected the
object form outright (issue #106 — a marketplace entry could not declare the
very ``{name, version}`` form ``validate_plugin`` advises). This module is the
one schema both call so they can never drift again.

Each dependency element is either:
  * a bare string (plugin name only) — kebab-case; an UNVERSIONED bare string
    auto-tracks the latest upstream tag, so it is flagged WARNING (pin a range);
  * a dict ``{name, version?, marketplace?}`` — ``name`` required (kebab),
    ``version`` optional (syntactic semver range), ``marketplace`` optional
    (kebab marketplace name); any other key is an unknown sub-field (MINOR).

A malformed element (neither string nor object, a bad/missing ``name``, an
invalid ``version`` range, a non-kebab ``marketplace``) is MAJOR.

``validate_dependency_element`` is report-agnostic: it returns a list of
``(level, message)`` findings for ONE element + its array index, so each caller
can emit them on its own report type (``validate_plugin`` via
``ValidationReport.major``/``.warning``/``.minor``; ``validate_marketplace`` via
``ValidationResult(level=…, category="plugin", …)``). It deliberately does NOT
perform the cross-marketplace allowlist enforcement (TRDD-20108ab7) — that needs
the hosting-marketplace context and is a plugin-only concern that stays inline
in ``validate_plugin.validate_dependencies``.
"""

from __future__ import annotations

import re
from typing import Any

from cpv_validation_common import Level

# Kebab-case plugin / marketplace name — byte-identical to
# cpv_validation_common.NAME_PATTERN and validate_plugin._PLUGIN_NAME_RE.
# re2-safe: anchored, no lookaround.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

# Minimal syntactic semver-range check for plugin dependencies. Accepts the
# npm-semver-range idioms documented at plugin-dependencies.md:44-52:
#   ~2.1.0, ^2.0, ^2.0.0-0, >=1.4, =2.1.0, 1.2.3, "x.y.z - a.b.c", "a || b".
# The atom regex targets a SINGLE range atom; logical OR is split and each side
# checked. re2-safe: anchored, no lookbehind/lookahead.
_SEMVER_ATOM_RE = re.compile(
    r"""^
    \s*                                           # leading space ok
    (?:                                           # range-kind prefix
        [~^]                                      #   ~ or ^
      | =
      | >=?|<=?                                   #   >, >=, <, <=
    )?
    \s*
    \d+(?:\.\d+){0,2}                             # MAJOR[.MINOR[.PATCH]]
    (?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?       # -prerelease
    (?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?      # +build
    \s*
    $
    """,
    re.VERBOSE,
)

# Hyphen range "x.y.z - a.b.c" (3 tokens separated by a bare dash and spaces).
_SEMVER_HYPHEN_RE = re.compile(
    r"^\s*\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?\s+-\s+\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?\s*$"
)

# Recognized dependency object sub-keys.
DEPENDENCY_SUBKEYS = frozenset({"name", "version", "marketplace"})


def is_valid_semver_range(text: object) -> bool:
    """Return True when ``text`` parses as a syntactic semver range.

    Not a full npm-semver parser — we only guard against obviously-malformed
    strings (empty, spaces inside a single range token, non-ASCII). Valid
    ranges like ``~2.1.0``, ``^2.0``, ``^2.0.0-0``, ``>=1.4``, ``=2.1.0``,
    ``1.2.3``, ``x.y.z - a.b.c``, logical OR chains ``a || b`` all pass.
    """
    if not isinstance(text, str) or not text:
        return False
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    # Logical OR — each side must be a valid range on its own.
    if "||" in text:
        return all(is_valid_semver_range(part) for part in text.split("||"))
    # Hyphen range ("x.y.z - a.b.c") — must be checked before the atom regex
    # since the atom regex does not allow internal whitespace.
    if _SEMVER_HYPHEN_RE.match(text):
        return True
    return bool(_SEMVER_ATOM_RE.match(text))


def validate_dependency_element(index: int, entry: Any) -> list[tuple[Level, str]]:
    """Validate ONE ``dependencies[index]`` element against the canonical schema.

    Returns a list of ``(level, message)`` findings (``level`` ∈
    ``{"MAJOR", "WARNING", "MINOR"}``); an empty list means the element is a
    well-formed object (or a versioned bare string) with nothing to report.
    Report-agnostic so both ``validate_plugin`` and ``validate_marketplace`` can
    emit the findings on their own report types.

    The messages are byte-identical to ``validate_plugin``'s historical
    dependency messages so the existing plugin tests keep passing unchanged. The
    cross-marketplace allowlist enforcement (which needs hosting context) is NOT
    done here — see ``cpv_dependency_schema`` module docstring.
    """
    findings: list[tuple[Level, str]] = []

    # bare string — plugin name only
    if isinstance(entry, str):
        if not _NAME_PATTERN.match(entry):
            findings.append(
                (
                    "MAJOR",
                    f"'dependencies[{index}]' bare-string name '{entry}' is not a valid kebab-case plugin name",
                )
            )
        else:
            # plugin-dependencies.md:9-11: a bare-string dep auto-tracks the
            # latest tag, so the next upstream release can break this plugin
            # without warning. Always flagged WARNING (a plugin cannot
            # self-exempt — TRDD-02e1672b removed the config opt-out).
            findings.append(
                (
                    "WARNING",
                    f"'dependencies[{index}]' = '{entry}' has no version constraint "
                    f"— it auto-tracks the latest tag and the next upstream release "
                    f"can break this plugin without warning. Pin a semver range: "
                    f"{{'name': '{entry}', 'version': '~1.2.0'}} (plugin-dependencies.md:9-11).",
                )
            )
        return findings

    # neither string nor object
    if not isinstance(entry, dict):
        findings.append(
            (
                "MAJOR",
                f"'dependencies[{index}]' must be a string or object, got {type(entry).__name__} "
                "(plugin-dependencies.md:29-50)",
            )
        )
        return findings

    # object form — name required
    if "name" not in entry:
        findings.append(
            (
                "MAJOR",
                f"'dependencies[{index}]' object missing required 'name' field (plugin-dependencies.md:46)",
            )
        )
    else:
        dep_name = entry["name"]
        if not isinstance(dep_name, str) or not _NAME_PATTERN.match(dep_name):
            findings.append(
                (
                    "MAJOR",
                    f"'dependencies[{index}].name' must be a kebab-case plugin name, got {dep_name!r}",
                )
            )

    # version — optional; syntactic range check
    if "version" in entry:
        dep_version = entry["version"]
        if not is_valid_semver_range(dep_version):
            findings.append(
                (
                    "MAJOR",
                    f"'dependencies[{index}].version' is not a valid semver range: {dep_version!r} "
                    "(plugin-dependencies.md:44-52)",
                )
            )

    # marketplace — optional; must be a plugin-style kebab name. NOTE: only the
    # FORMAT is checked here; the cross-marketplace allowlist enforcement
    # (TRDD-20108ab7) stays inline in validate_plugin.validate_dependencies
    # because it needs the hosting-marketplace context.
    if "marketplace" in entry:
        market = entry["marketplace"]
        if not isinstance(market, str) or not _NAME_PATTERN.match(market):
            findings.append(
                (
                    "MAJOR",
                    f"'dependencies[{index}].marketplace' must be a kebab-case marketplace name, got {market!r}",
                )
            )

    # unknown sub-keys — MINOR so authors notice typos
    for extra in sorted(set(entry.keys()) - DEPENDENCY_SUBKEYS):
        findings.append(
            (
                "MINOR",
                f"'dependencies[{index}].{extra}' is not a recognized dependency sub-field "
                "(recognized: name, version, marketplace)",
            )
        )

    return findings
