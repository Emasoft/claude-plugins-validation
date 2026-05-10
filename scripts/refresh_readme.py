#!/usr/bin/env python3
"""Refresh the AUTO-* marker blocks in a plugin's README.md.

Currently emits a single AUTO-COMPONENTS block listing the plugin's
agents, skills, commands, hooks, and MCP servers — auto-discovered from
the filesystem so the README cannot drift out of sync with what actually
ships.

Usage:
    uv run python scripts/refresh_readme.py [plugin-path]
    uv run python scripts/refresh_readme.py [plugin-path] --check

`--check` exits 1 if the README would change (use as a CI gate).

If markers are missing from README.md, the block is appended at the end.
The user can then move the block wherever they want — subsequent runs
preserve placement and only update the body between the markers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cpv_management_common import (  # noqa: E402
    detect_components,
    render_components_table,
    replace_marker_block,
)


def refresh(plugin_root: Path, *, check_only: bool = False) -> int:
    readme = plugin_root / "README.md"
    if not readme.is_file():
        if check_only:
            print(f"  [check] README.md missing at {plugin_root}", file=sys.stderr)
            return 1
        # Bootstrap a tiny README so the marker has a home.
        readme.write_text(
            f"# {plugin_root.name}\n\nClaude Code plugin.\n\n",
            encoding="utf-8",
        )
        print(f"  [bootstrap] created README.md at {readme}")

    components = detect_components(plugin_root)
    table = render_components_table(components)

    if check_only:
        # Dry-run: read the file and compare.
        text = readme.read_text(encoding="utf-8")
        from cpv_management_common import _re_marker  # type: ignore[attr-defined]

        marker_id = "COMPONENTS"
        begin = f"<!-- BEGIN AUTO-{marker_id} -->"
        end = f"<!-- END AUTO-{marker_id} -->"
        pattern = _re_marker.compile(
            f"{_re_marker.escape(begin)}(.*?){_re_marker.escape(end)}",
            flags=_re_marker.DOTALL,
        )
        match = pattern.search(text)
        if match is None:
            print("  [check] README.md missing AUTO-COMPONENTS markers — would append.")
            return 1
        if match.group(1).strip() != table.strip():
            print("  [check] README.md AUTO-COMPONENTS block is stale — would update.")
            return 1
        print("  [check] README.md is up to date.")
        return 0

    changed, status = replace_marker_block(
        readme,
        "COMPONENTS",
        table,
        create_if_missing=True,
    )
    if changed:
        print(f"  [refresh-readme] {readme}: {status}")
    else:
        print(f"  [refresh-readme] {readme}: {status} (no changes)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Refresh AUTO-* marker blocks in README.md.")
    p.add_argument("plugin_root", nargs="?", default=".", type=Path, help="Path to the plugin root (default: cwd).")
    p.add_argument("--check", action="store_true", help="Exit 1 if README would change. Use as a CI gate.")
    args = p.parse_args()
    return refresh(args.plugin_root.resolve(), check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
