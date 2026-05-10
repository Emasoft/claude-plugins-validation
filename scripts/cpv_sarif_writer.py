#!/usr/bin/env python3
"""SARIF 2.1.0 emitter for CPV validation findings (RC-105).

SARIF (Static Analysis Results Interchange Format) is the OASIS standard
for static-analyzer output. GitHub code scanning and most modern CI
dashboards consume it natively.

This module converts a CPV `ValidationReport` (or any iterable of
`ValidationResult`) into a SARIF 2.1.0 dict and writes it to disk.

Schema reference: https://docs.oasis-open.org/sarif/sarif/v2.1.0/

Severity mapping (CPV → SARIF):
    CRITICAL → error
    MAJOR    → error
    MINOR    → warning
    WARNING  → warning
    NIT      → note
    INFO     → note
    PASSED   → (skipped — SARIF carries findings, not passes)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# CPV severity → SARIF level
_SARIF_LEVEL = {
    "CRITICAL": "error",
    "MAJOR": "error",
    "MINOR": "warning",
    "WARNING": "warning",
    "NIT": "note",
    "INFO": "note",
}

# Severities that DO emit a finding (PASSED is skipped)
_EMITTED_LEVELS = frozenset(_SARIF_LEVEL.keys())

# CPV rule ID prefixes that should be lifted out of message text into ruleId.
# Order matters — most specific first.
_RULE_ID_RE = re.compile(r"\b((?:RC|CPV|GAP)-[0-9A-Z]+(?:-[0-9A-Z]+)*)\b")

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"


def _extract_rule_id(message: str) -> str:
    """Pull the leading CPV/RC/GAP rule id out of a message, or fall back.

    >>> _extract_rule_id("RC-69 dangerous eval pattern")
    'RC-69'
    >>> _extract_rule_id("Hardcoded user path /Users/foo detected")
    'CPV-GENERIC'
    """
    m = _RULE_ID_RE.search(message)
    return m.group(1) if m else "CPV-GENERIC"


def _result_to_sarif(
    result_dict: dict[str, Any],
    plugin_root: Path,
) -> dict[str, Any] | None:
    """Convert one ValidationResult-shaped dict into a SARIF result entry.

    Returns None if the level should not be emitted (e.g., PASSED).
    """
    level = str(result_dict.get("level", "")).upper()
    if level not in _EMITTED_LEVELS:
        return None

    message = str(result_dict.get("message", ""))
    rule_id = _extract_rule_id(message)

    sarif_result: dict[str, Any] = {
        "ruleId": rule_id,
        "level": _SARIF_LEVEL[level],
        "message": {"text": message},
        "properties": {"cpv_severity": level},
    }

    file_path = result_dict.get("file")
    if file_path:
        # SARIF wants a URI relative to a srcroot. We embed POSIX paths.
        try:
            rel = Path(file_path).resolve().relative_to(plugin_root.resolve())
            uri = rel.as_posix()
        except (ValueError, OSError):
            uri = str(file_path)

        physical_loc: dict[str, Any] = {
            "artifactLocation": {"uri": uri, "uriBaseId": "%SRCROOT%"},
        }
        line = result_dict.get("line")
        if line is not None:
            try:
                line_int = int(line)
                if line_int >= 1:
                    physical_loc["region"] = {"startLine": line_int}
            except (TypeError, ValueError):
                pass

        sarif_result["locations"] = [{"physicalLocation": physical_loc}]

    if result_dict.get("category"):
        sarif_result["properties"]["category"] = result_dict["category"]
    if result_dict.get("phase"):
        sarif_result["properties"]["phase"] = result_dict["phase"]
    if result_dict.get("suggestion"):
        sarif_result["fixes"] = [{"description": {"text": result_dict["suggestion"]}}]

    return sarif_result


def _collect_rule_descriptors(sarif_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the tool.driver.rules[] table from emitted ruleIds."""
    seen: dict[str, dict[str, Any]] = {}
    for r in sarif_results:
        rid = r.get("ruleId", "CPV-GENERIC")
        if rid in seen:
            continue
        seen[rid] = {
            "id": rid,
            "name": rid,
            "shortDescription": {"text": rid},
            "defaultConfiguration": {"level": r.get("level", "warning")},
        }
    return list(seen.values())


def results_to_sarif(
    results: Iterable[Any],
    plugin_root: Path,
    tool_name: str = "claude-plugins-validation",
    tool_version: str = "0.0.0",
    information_uri: str = "https://github.com/Emasoft/claude-plugins-validation",
) -> dict[str, Any]:
    """Convert an iterable of ValidationResult (or .to_dict()'d shapes) into SARIF.

    Accepts either ValidationResult objects (anything with .to_dict()) or
    plain dicts. Returns a complete SARIF 2.1.0 log dict.
    """
    sarif_results: list[dict[str, Any]] = []
    for item in results:
        d: dict[str, Any]
        if isinstance(item, dict):
            d = item
        elif hasattr(item, "to_dict") and callable(item.to_dict):
            raw = item.to_dict()
            if not isinstance(raw, dict):
                continue
            d = dict(raw)
        else:
            continue
        sr = _result_to_sarif(d, plugin_root)
        if sr is not None:
            sarif_results.append(sr)

    rules = _collect_rule_descriptors(sarif_results)

    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "informationUri": information_uri,
                        "rules": rules,
                    }
                },
                "originalUriBaseIds": {"%SRCROOT%": {"uri": plugin_root.resolve().as_uri() + "/"}},
                "results": sarif_results,
            }
        ],
    }


def write_sarif(
    results: Iterable[Any],
    output_path: Path,
    plugin_root: Path,
    tool_name: str = "claude-plugins-validation",
    tool_version: str = "0.0.0",
    information_uri: str = "https://github.com/Emasoft/claude-plugins-validation",
) -> Path:
    """Serialize results to a SARIF file. Returns the resolved output path."""
    sarif = results_to_sarif(
        results,
        plugin_root,
        tool_name=tool_name,
        tool_version=tool_version,
        information_uri=information_uri,
    )
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(sarif, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_path.resolve()
