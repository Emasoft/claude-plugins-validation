#!/usr/bin/env python3
"""CPVPPC render_spec — generate design/specs/cpvppc.md from canon.json.

DETERMINISTIC by design: no dates, no timestamps, stable ordering. The
committed spec must equal the re-rendered output byte for byte — a v5.18.0
lesson: date stamps make equality tests fail at midnight — so this file emits
only the canon content itself.
"""

from __future__ import annotations

import json
from pathlib import Path


def _spec_path() -> Path:
    return Path(__file__).resolve().parents[2] / "design" / "specs" / "cpvppc.md"


def render_spec(canon: dict) -> str:
    """Render the human-readable canon spec from the manifest. Pure function."""
    lines: list[str] = []
    lines.append("# CPVPPC — the publishing-pipeline canon (generated)")
    lines.append("")
    lines.append("This file is GENERATED from `scripts/cpvppc/canon.json`. Do not edit it by")
    lines.append("hand — edit the manifest and re-render. A test asserts the committed copy")
    lines.append("equals the rendered output.")
    lines.append("")
    lines.append(f"Canon version: {canon['canon_version']}")
    lines.append("")
    lines.append("## Fact types")
    lines.append("")
    for ft in canon["fact_types"]:
        lines.append(f"- `{ft}`")
    lines.append("")
    lines.append(canon.get("fact_type_registry", "").strip())
    lines.append("")
    lines.append("## Assertions")
    lines.append("")
    for a in canon["assertions"]:
        fact = a["fact"]
        args = json.dumps(fact.get("args", {}), sort_keys=True, ensure_ascii=False)
        lines.append(f"### {a['id']}")
        lines.append("")
        lines.append(f"- Scope: `{a['scope']}`")
        lines.append(f"- Since: {a['since']}")
        lines.append(f"- Applies when: {a['applies_when']}")
        lines.append(f"- Fact: `{fact['type']}` {args}")
        lines.append(f"- Fix: {a['fix']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    canon_path = Path(__file__).resolve().parent / "canon.json"
    canon = json.loads(canon_path.read_text(encoding="utf-8"))
    target = _spec_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_spec(canon), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
