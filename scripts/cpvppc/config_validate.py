#!/usr/bin/env python3
"""Self-contained validator for cpvppc.yaml against config_schema.json.

jsonschema is NOT a runtime dependency (probed 2026-09-29: not importable in
the runtime env, and P2 must add none), so this module implements exactly the
draft-07 keyword subset the schema uses: const, enum, type, pattern,
required, properties, additionalProperties (false | schema), items,
minProperties, propertyNames.pattern, oneOf, if/then. Unknown keywords
($schema, title) are ignored. The schema FILE stays the single source of
truth — this module only interprets it, so a schema edit needs no code change.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).resolve().parent / "config_schema.json"
# Must equal the schema's schema_version const (pinned by test).
SCHEMA_VERSION = "0.1.0"


def load_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return data


def _type_ok(value: Any, name: str) -> bool:
    # bool is a subclass of int in Python — exclude it from number/integer.
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    return False


def validate_against_schema(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errs: list[str] = []
    if "const" in schema and value != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, n) for n in names):
            errs.append(f"{path}: expected type {schema['type']}, got {type(value).__name__}")
            return errs
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        errs.append(f"{path}: {value!r} does not match {schema['pattern']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            errs.append(f"{path}: {value} is below the minimum {schema['minimum']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}: missing required key {req!r}")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                errs += validate_against_schema(v, props[k], f"{path}.{k}")
            elif addl is False:
                errs.append(f"{path}: unknown key {k!r}")
            elif isinstance(addl, dict):
                errs += validate_against_schema(v, addl, f"{path}.{k}")
        pn = schema.get("propertyNames")
        if isinstance(pn, dict) and "pattern" in pn:
            for k in value:
                if not re.search(pn["pattern"], k):
                    errs.append(f"{path}: key {k!r} does not match {pn['pattern']!r}")
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errs.append(f"{path}: needs at least {schema['minProperties']} entries")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errs += validate_against_schema(item, schema["items"], f"{path}[{i}]")
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        passing = sum(1 for b in branches if not validate_against_schema(value, b, path))
        if passing != 1:
            errs.append(f"{path}: must match exactly one allowed shape ({passing} matched)")
    if "if" in schema:
        cond_ok = not validate_against_schema(value, schema["if"], path)
        target = schema.get("then") if cond_ok else schema.get("else")
        if isinstance(target, dict):
            errs += validate_against_schema(value, target, path)
    return errs


def read_config(repo: Path) -> tuple[dict | None, str]:
    """Read + parse <repo>/cpvppc.yaml. Returns (doc, "") or (None, reason)."""
    path = repo / "cpvppc.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, "cpvppc.yaml is not valid UTF-8"
    except OSError:
        return None, "missing cpvppc.yaml"
    try:
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415 — lazy, matches verify.py
    except ImportError:
        return None, "cannot run: PyYAML not available"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, f"cpvppc.yaml is not valid YAML: {e}"
    if not isinstance(doc, dict):
        return None, "cpvppc.yaml is not a YAML mapping"
    return doc, ""


def validate_config(doc: dict) -> list[str]:
    """Validate a parsed config against the bundled schema."""
    return validate_against_schema(doc, load_schema())
