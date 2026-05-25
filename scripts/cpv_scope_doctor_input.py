#!/usr/bin/env python3
"""Scope-aware doctor input resolver (TRDD-a175f78d §1-2).

This module is the LOCAL-only sibling of ``cpv_marketplace_input``.
The scope-aware doctor skills (`cpv-batch-scope-diagnose`,
`cpv-batch-scope-fix`, `cpv-batch-scope-diagnose-and-fix`) diagnose
the **Claude installation** (user / project / local-scope
extensions in `~/.claude/` + `<project>/.claude/`) and therefore
REQUIRE local filesystem access. URL inputs cannot reach
`~/.claude/` and are explicitly rejected with a CRITICAL message.

The resolver provides:

* ``resolve_scope_inputs(input_spec, *, default_to_pwd=True)`` —
  wraps ``cpv_marketplace_input.resolve`` with ``allow_url=False``
  pinned internally. Remote shapes raise immediately with a clear
  remediation hint; empty input falls back to ``$PWD`` when
  ``default_to_pwd`` is true.
* ``parse_scope_flag(scope)`` — validates one of ``user``,
  ``project``, ``local``, ``full`` (the four documented values)
  and returns the canonical lowercase form.
* ``URL_REJECTED_MESSAGE`` — the verbatim CRITICAL message
  surfaced when the caller passes a remote shape.

Iron rule: ambiguity is a CRITICAL error. URL inputs raise
``InputResolutionError`` carrying the standard remediation hint —
callers should surface the message verbatim without re-wrapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_marketplace_input import (  # noqa: E402
    InputResolutionError,
    ResolvedInput,
    is_url_shape,
    resolve,
)

ScopeKind = Literal["user", "project", "local", "full"]
_VALID_SCOPES: frozenset[str] = frozenset({"user", "project", "local", "full"})

URL_REJECTED_MESSAGE = (
    "ERROR: cpv-batch-scope-* skills require LOCAL project paths.\n"
    "       A valid Claude installation (~/.claude/) is necessary to\n"
    "       diagnose user/project/local-scope extensions. URL inputs\n"
    "       cannot reach the filesystem state of a Claude installation.\n"
    "       Use cpv-batch-validate or cpv-batch-security-audit for\n"
    "       source-tree scans of remote plugins."
)


def parse_scope_flag(scope: str | None) -> ScopeKind:
    """Validate ``--scope`` and return the canonical lowercase form.

    Accepts the four documented values: ``user``, ``project``,
    ``local``, ``full``. Default (when ``None``) is ``full`` per
    TRDD-a175f78d §2. Any other value raises ``InputResolutionError``
    with a clear remediation hint.
    """
    if scope is None:
        return "full"
    s = scope.strip().lower()
    if s not in _VALID_SCOPES:
        raise InputResolutionError(
            f"Invalid --scope value {scope!r}. Accepted: {', '.join(sorted(_VALID_SCOPES))}. Default: full."
        )
    return s  # type: ignore[return-value]


def resolve_scope_inputs(
    input_spec: str | list[str] | None,
    *,
    default_to_pwd: bool = True,
) -> list[ResolvedInput]:
    """Resolve the scope-doctor input spec.

    * ``None`` or empty → fall back to ``$PWD`` if ``default_to_pwd=True``.
    * Otherwise routes through ``cpv_marketplace_input.resolve`` with
      the URL-rejection flag set. Remote shapes raise immediately.

    The returned ``ResolvedInput`` list is what the doctor agents
    consume — one entry per project folder, with ``kind="plugin"``
    or ``kind="file"`` or ``kind="skill"`` per local-shape
    classification. Marketplaces ARE allowed (they expand to per-plugin
    entries the same way), but the typical scope-doctor input is a
    single project folder.
    """
    if input_spec is None or (isinstance(input_spec, str) and not input_spec.strip()):
        if default_to_pwd:
            cwd = str(Path.cwd().resolve())
            return resolve(cwd, allow_url=False)
        raise InputResolutionError(
            "no input given and default-to-PWD disabled. Pass a project folder path or set default_to_pwd=True."
        )
    if isinstance(input_spec, str) and is_url_shape(input_spec):
        raise InputResolutionError(URL_REJECTED_MESSAGE)
    if isinstance(input_spec, list):
        for spec in input_spec:
            if is_url_shape(spec):
                raise InputResolutionError(URL_REJECTED_MESSAGE)
    return resolve(input_spec, allow_url=False)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scope-aware doctor input resolver — local-only, scope-flag validator."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Project folder paths / list-file (@/path/to/list.txt) / comma-separated. Default: $PWD when no inputs.",
    )
    parser.add_argument(
        "--scope",
        choices=sorted(_VALID_SCOPES),
        default="full",
        help="Scope to diagnose (default: full).",
    )
    parser.add_argument(
        "--no-default-pwd",
        action="store_true",
        help="Disable the empty-input → $PWD fallback (raises instead).",
    )
    args = parser.parse_args(argv)

    try:
        scope = parse_scope_flag(args.scope)
    except InputResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        if args.inputs:
            resolved = resolve_scope_inputs(args.inputs, default_to_pwd=not args.no_default_pwd)
        else:
            resolved = resolve_scope_inputs(None, default_to_pwd=not args.no_default_pwd)
    except InputResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "scope": scope,
                "input_count": len(resolved),
                "inputs": [
                    {
                        "display_name": r.display_name,
                        "abs_path": str(r.abs_path),
                        "kind": r.kind,
                    }
                    for r in resolved
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_cli())
