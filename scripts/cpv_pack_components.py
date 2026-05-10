#!/usr/bin/env python3
"""Pack standalone Claude Code components into a single installable plugin.

Use cases:
- Convert a folder of components saved from a Claude Code session into a
  proper plugin shape (one that actually installs and loads).
- Roll multiple skills/agents/commands from disparate projects into a
  new shared plugin.
- Recover from "Phase 0 plugin-shape detection" refusals (skill at root,
  loose agents, etc.) by wrapping the detected components into a real
  plugin.

The script is the engine behind the menu-driven multi-select in §3.4.8
(Create → Pack components into a plugin) — see
`skills/cpv-main-menu-skill/references/menu-tree.md`.

CLI:
  uv run cpv_pack_components.py <source-dir> <target-dir>
    --name <plugin-name> --description <text>
    --author <name> --author-email <email>
    [--github-owner <name>] [--marketplace <name>]
    [--include type=name,name [type=name,name ...]]
    [--exclude type=name,name [type=name,name ...]]
    [--all] [--list-only]
    [--language python|js|ts|...] [--strip-dev | --no-strip-dev]
    [--add-to-marketplace <path>] [--create-marketplace <path>]
    [--json] [--dry-run]

Component types discovered:
  skill, agent, command, hook, mcp, lsp, monitor, output-style

Exit codes:
  0 OK
  1 invalid args / source not found
  2 source has no detectable components
  3 selection conflict (duplicate names, --include + --all, etc.)
  4 scaffolding failed
  5 marketplace op failed

JSON mode emits one JSON object on stdout (no human prose) and exits with
the same codes — designed for remote API integration. Schema is stable
and documented in `cpv_pack_components_schema.json` (planned).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Reuse the existing scaffolder + slurp engine.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_plugin_repo as gpr  # noqa: E402

# ── ANSI colours (reuse generate_plugin_repo's gates) ─────────────────────────
GREEN = gpr.GREEN
YELLOW = gpr.YELLOW
RED = gpr.RED
BOLD = gpr.BOLD
NC = gpr.NC

# ── Discovery ────────────────────────────────────────────────────────────────


@dataclass
class Component:
    """One discovered component in a source directory."""

    type: str  # one of: skill, agent, command, hook, mcp, lsp, monitor, output-style
    name: str  # logical name (skill folder name, agent file stem, etc.)
    src: Path  # absolute path to the file or directory carrying this component

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "name": self.name, "src": str(self.src)}


# Type → standard plugin folder/file mapping. Used by both discovery (where
# do we look?) and packing (where do we copy to?). Keep in sync with
# `skills/plugin-validation-skill/references/shape-detection.md`.
_TYPE_TO_PLUGIN_PATH: dict[str, str] = {
    "skill": "skills/{name}/",
    "agent": "agents/{name}.md",
    "command": "commands/{name}.md",
    "hook": "hooks/hooks.json",
    "mcp": ".mcp.json",
    "lsp": ".lsp.json",
    "monitor": "monitors/monitors.json",
    "output-style": "output-styles/{name}.md",
}

VALID_TYPES = tuple(_TYPE_TO_PLUGIN_PATH.keys())


def _classify_md(path: Path) -> str:
    """Return 'skill' / 'command' / 'agent' for a .md file.

    Delegates to generate_plugin_repo._classify_md so the heuristic stays
    in one place. SKILL.md → skill; frontmatter has `allowed-tools:`
    → command; otherwise agent.
    """
    return gpr._classify_md(path)


def discover(source: Path) -> list[Component]:
    """Walk `source` and return every detectable component.

    Detection is conservative — only files that look like genuine
    components are reported. Random `.md` files inside nested folders
    are ignored unless they sit in a recognisable location (e.g.
    `agents/<x>.md`, `commands/<x>.md`, `output-styles/<x>.md`).
    """
    found: list[Component] = []

    # 1. SKILL.md at root → standalone skill (folder name = parent or
    #    the name in frontmatter).
    root_skill = source / "SKILL.md"
    if root_skill.is_file():
        fm = gpr._read_md_frontmatter(root_skill)
        name = fm.get("name") or source.name or "imported-skill"
        found.append(Component(type="skill", name=name, src=source))

    # 2. skills/<name>/SKILL.md (one entry per nested skill).
    skills_dir = source / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").is_file():
                found.append(Component(type="skill", name=child.name, src=child))

    # 3. agents/<name>.md (one per agent file).
    agents_dir = source / "agents"
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.md")):
            found.append(Component(type="agent", name=f.stem, src=f))

    # 4. commands/<name>.md (one per command file). Also detects
    #    misclassified .md files in agents/ that are commands per
    #    frontmatter (the user may have dropped them in the wrong dir).
    commands_dir = source / "commands"
    if commands_dir.is_dir():
        for f in sorted(commands_dir.glob("*.md")):
            found.append(Component(type="command", name=f.stem, src=f))

    # 5. Loose .md files at root → classify by frontmatter and bucket.
    #    Skip SKILL.md (already handled) and any .md inside subdirs.
    for f in sorted(source.glob("*.md")):
        if f.name == "SKILL.md":
            continue
        kind = _classify_md(f)
        if kind == "skill":
            # A non-SKILL.md classified as skill is weird — skip.
            continue
        found.append(Component(type=kind, name=f.stem, src=f))

    # 6. hooks/hooks.json → exactly one hook config per source.
    hooks_json = source / "hooks" / "hooks.json"
    if hooks_json.is_file():
        found.append(Component(type="hook", name="hooks", src=hooks_json))

    # 7. .mcp.json at root.
    mcp_json = source / ".mcp.json"
    if mcp_json.is_file():
        found.append(Component(type="mcp", name="mcp", src=mcp_json))

    # 8. .lsp.json at root.
    lsp_json = source / ".lsp.json"
    if lsp_json.is_file():
        found.append(Component(type="lsp", name="lsp", src=lsp_json))

    # 9. monitors/monitors.json.
    monitors_json = source / "monitors" / "monitors.json"
    if monitors_json.is_file():
        found.append(Component(type="monitor", name="monitors", src=monitors_json))

    # 10. output-styles/<name>.md (one per file).
    output_styles_dir = source / "output-styles"
    if output_styles_dir.is_dir():
        for f in sorted(output_styles_dir.glob("*.md")):
            found.append(Component(type="output-style", name=f.stem, src=f))

    return found


# ── Selection ────────────────────────────────────────────────────────────────


@dataclass
class Selection:
    """User-driven include/exclude filter applied to discovered components."""

    include_all: bool = False
    include: dict[str, set[str]] = field(default_factory=dict)
    exclude: dict[str, set[str]] = field(default_factory=dict)

    @staticmethod
    def parse_filter(arg: str) -> tuple[str, list[str]]:
        """Parse `type=name1,name2` → ('type', ['name1', 'name2']).

        Empty names list means "all of this type".
        """
        if "=" not in arg:
            raise ValueError(f"--include / --exclude expects type=name,...; got {arg!r}")
        kind, _, names = arg.partition("=")
        kind = kind.strip()
        if kind not in VALID_TYPES:
            raise ValueError(f"unknown component type {kind!r}; valid: {', '.join(VALID_TYPES)}")
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        return kind, name_list

    def add_include(self, arg: str) -> None:
        kind, names = self.parse_filter(arg)
        self.include.setdefault(kind, set()).update(names or {"*"})

    def add_exclude(self, arg: str) -> None:
        kind, names = self.parse_filter(arg)
        self.exclude.setdefault(kind, set()).update(names or {"*"})

    def matches(self, component: Component) -> bool:
        """True if this component passes the filter."""
        excluded = self.exclude.get(component.type, set())
        if "*" in excluded or component.name in excluded:
            return False
        if self.include_all:
            return True
        included = self.include.get(component.type, set())
        if not included:
            return False
        return "*" in included or component.name in included


def apply_selection(components: list[Component], selection: Selection) -> list[Component]:
    """Filter components per selection. Preserves discovery order."""
    return [c for c in components if selection.matches(c)]


# ── Validation of selected components ────────────────────────────────────────


def validate_selection(selected: list[Component]) -> list[str]:
    """Return a list of human-readable problems with the selection.

    Empty list → selection is valid. Non-empty → caller MUST refuse to
    pack (otherwise we'd produce an unloadable plugin). Errors include:

    - Duplicate names within a type (e.g. two `agents/foo.md`).
    - Multiple hooks/mcp/lsp/monitor configs (each is singleton at root).
    - Empty selection.
    """
    problems: list[str] = []
    if not selected:
        problems.append("no components selected — refusing to scaffold an empty plugin")
        return problems

    # Per-type duplicate-name detection.
    by_type: dict[str, list[str]] = {}
    for c in selected:
        by_type.setdefault(c.type, []).append(c.name)
    for kind, names in by_type.items():
        seen: set[str] = set()
        for n in names:
            if n in seen:
                problems.append(f"duplicate {kind} {n!r} in selection")
            seen.add(n)

    # Singleton root configs — at most one allowed.
    for singleton in ("hook", "mcp", "lsp", "monitor"):
        if len(by_type.get(singleton, [])) > 1:
            problems.append(f"more than one {singleton} config selected; only one per plugin is allowed at root")
    return problems


# ── Pack: write components into target plugin tree ───────────────────────────


def _write_singleton(target_root: Path, src: Path, dest_rel: str, dry_run: bool) -> int:
    """Copy a singleton config file (hooks.json, .mcp.json, .lsp.json,
    monitors.json) into the plugin root. Creates parent dirs.

    Returns 1 on copy, 0 on dry-run.
    """
    dest = target_root / dest_rel
    if dry_run:
        print(f"  [pack] (dry-run) {src} → {dest_rel}")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"  [pack] {src} → {dest_rel}")
    return 1


def pack(target_root: Path, selected: list[Component], dry_run: bool) -> int:
    """Copy every selected component into its standard location under
    `target_root`. Returns the count of files copied.

    For skill/agent/command/mcp the existing `gpr._slurp_one` is reused.
    Hook/lsp/monitor/output-style are handled inline.
    """
    total = 0
    for c in selected:
        if c.type in ("skill", "agent", "command", "mcp"):
            if dry_run:
                dest = _TYPE_TO_PLUGIN_PATH[c.type].format(name=c.name)
                print(f"  [pack] (dry-run) {c.src} → {dest}")
                continue
            total += gpr._slurp_one(target_root, c.src, c.type)
        elif c.type == "hook":
            total += _write_singleton(target_root, c.src, "hooks/hooks.json", dry_run)
        elif c.type == "lsp":
            total += _write_singleton(target_root, c.src, ".lsp.json", dry_run)
        elif c.type == "monitor":
            total += _write_singleton(target_root, c.src, "monitors/monitors.json", dry_run)
        elif c.type == "output-style":
            dest_rel = f"output-styles/{c.name}.md"
            total += _write_singleton(target_root, c.src, dest_rel, dry_run)
        else:  # pragma: no cover — guarded by VALID_TYPES at parse time
            raise ValueError(f"unknown component type {c.type!r}")
    return total


# ── Marketplace integration ─────────────────────────────────────────────────


def add_to_marketplace(marketplace_root: Path, plugin_params: gpr.PluginParams) -> None:
    """Append a plugin entry to an existing marketplace's `plugins` list.

    Idempotent: if an entry with the same name already exists, replaces
    it (same behaviour as `cpv-link-plugin-to-marketplace`). Raises on
    invalid marketplace.json.
    """
    mkt_json_path = marketplace_root / ".claude-plugin" / "marketplace.json"
    if not mkt_json_path.is_file():
        raise FileNotFoundError(f"no marketplace.json at {mkt_json_path}")
    data = json.loads(mkt_json_path.read_text(encoding="utf-8"))
    plugins = data.setdefault("plugins", [])
    new_entry: dict[str, object] = {"name": plugin_params.name}
    if plugin_params.github_owner:
        new_entry["source"] = {
            "source": "github",
            "repo": f"{plugin_params.github_owner}/{plugin_params.repo_name}",
        }
    else:
        new_entry["source"] = f"./plugins/{plugin_params.name}"
    # Replace existing entry with same name (idempotent).
    plugins[:] = [p for p in plugins if p.get("name") != plugin_params.name]
    plugins.append(new_entry)
    mkt_json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def create_marketplace(target: Path, plugin_params: gpr.PluginParams) -> None:
    """Bootstrap a minimal marketplace at `target` and add the plugin to
    its plugin list. Owner is derived from `plugin_params.github_owner`.
    """
    target.mkdir(parents=True, exist_ok=True)
    (target / ".claude-plugin").mkdir(exist_ok=True)
    mkt_json = {
        "name": target.name,
        "owner": {"name": plugin_params.author, "email": plugin_params.author_email},
        "plugins": [],
    }
    (target / ".claude-plugin" / "marketplace.json").write_text(json.dumps(mkt_json, indent=2) + "\n", encoding="utf-8")
    add_to_marketplace(target, plugin_params)


# ── CLI ──────────────────────────────────────────────────────────────────────


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise SystemExit(f"plugin name must match {_NAME_RE.pattern}; got {name!r}")


def _emit_json(payload: dict[str, object]) -> None:
    """Emit a single-line JSON object on stdout (remote API mode)."""
    print(json.dumps(payload, separators=(",", ":")))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pack standalone components into a Claude Code plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("source", type=Path, help="Source directory containing components")
    parser.add_argument(
        "target",
        type=Path,
        nargs="?",
        default=None,
        help="Target directory for the new plugin (required unless --list-only)",
    )
    parser.add_argument("--name", help="Plugin name (lowercase + hyphens)")
    parser.add_argument("--description", default="", help="One-line plugin description")
    parser.add_argument("--author", default="", help="Author display name")
    parser.add_argument("--author-email", default="", help="Author email")
    parser.add_argument("--github-owner", default="", help="GitHub account or org")
    parser.add_argument("--marketplace", default="", help="Marketplace name (for install commands)")
    parser.add_argument("--version", default="0.1.0", help="Initial version (default: 0.1.0)")
    parser.add_argument(
        "--language",
        choices=sorted(gpr.VALID_LANGUAGES),
        default="python",
        help="Plugin language (default: python)",
    )
    parser.add_argument(
        "--strip-dev",
        dest="strip_dev",
        action="store_true",
        default=True,
        help="(default) Emit cpv.strip block in plugin.json",
    )
    parser.add_argument(
        "--no-strip-dev", dest="strip_dev", action="store_false", help="Disable dev-stripping config (legacy mode)"
    )
    parser.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="Include every detected component (mutually exclusive with --include)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="TYPE=N1,N2",
        help=(
            "Include named components of TYPE. May repeat. Empty name list = all of TYPE. "
            f"Valid TYPEs: {', '.join(VALID_TYPES)}."
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="TYPE=N1,N2",
        help="Exclude named components from selection (after --include / --all).",
    )
    parser.add_argument(
        "--list-only", action="store_true", help="Print discovered components and exit (no scaffolding)"
    )
    parser.add_argument(
        "--add-to-marketplace",
        type=Path,
        default=None,
        help="After packing, register the plugin in this existing marketplace",
    )
    parser.add_argument(
        "--create-marketplace",
        type=Path,
        default=None,
        help="After packing, create a new marketplace at this path with the plugin",
    )
    parser.add_argument(
        "--json",
        dest="json_mode",
        action="store_true",
        help="Emit a single JSON object on stdout (machine-readable mode)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    out_json: dict[str, object] = {"ok": False}

    if not args.source.is_dir():
        msg = f"source directory does not exist: {args.source}"
        return _fail(args, out_json, code=1, msg=msg)

    components = discover(args.source.resolve())
    out_json["discovered"] = [c.to_dict() for c in components]
    if not components:
        return _fail(args, out_json, code=2, msg="no components detected in source")

    if args.list_only:
        out_json["ok"] = True
        out_json["selected"] = []
        if args.json_mode:
            _emit_json(out_json)
        else:
            _print_components(components, header="Discovered components:")
        return 0

    # Selection
    selection = Selection(include_all=args.include_all)
    if args.include and args.include_all:
        return _fail(args, out_json, code=3, msg="--include and --all are mutually exclusive")
    try:
        for arg in args.include:
            selection.add_include(arg)
        for arg in args.exclude:
            selection.add_exclude(arg)
    except ValueError as exc:
        return _fail(args, out_json, code=1, msg=str(exc))
    if not (args.include_all or args.include):
        # Default: include everything (most ergonomic for menu-driven flow).
        selection.include_all = True

    selected = apply_selection(components, selection)
    out_json["selected"] = [c.to_dict() for c in selected]

    problems = validate_selection(selected)
    if problems:
        return _fail(args, out_json, code=3, msg="; ".join(problems))

    # Plugin params (from CLI args)
    if not args.name:
        return _fail(args, out_json, code=1, msg="--name is required")
    _validate_name(args.name)
    if not args.target:
        return _fail(args, out_json, code=1, msg="target directory is required")

    params = gpr.PluginParams(
        name=args.name,
        description=args.description or f"Plugin packed from {args.source.name}",
        author=args.author or "Anonymous",
        author_email=args.author_email or "anon@example.com",
        github_owner=args.github_owner,
        marketplace=args.marketplace,
        version=args.version,
        language=args.language,
        strip_dev=args.strip_dev,
    )

    target = args.target.resolve()
    out_json["target"] = str(target)

    # Scaffold + pack
    try:
        if args.dry_run:
            print(f"  [pack] (dry-run) would scaffold plugin {params.name} at {target}")
        else:
            gpr.generate_plugin_repo(target, params, dry_run=False)
        copied = pack(target, selected, dry_run=args.dry_run)
        out_json["files_copied"] = copied
    except Exception as exc:  # noqa: BLE001 — surface real reason
        return _fail(args, out_json, code=4, msg=f"scaffold or pack failed: {exc}")

    # Marketplace ops
    try:
        if args.add_to_marketplace:
            if args.dry_run:
                print(f"  [pack] (dry-run) would register {params.name} in {args.add_to_marketplace}")
            else:
                add_to_marketplace(args.add_to_marketplace.resolve(), params)
                out_json["registered_in_marketplace"] = str(args.add_to_marketplace.resolve())
        if args.create_marketplace:
            if args.dry_run:
                print(f"  [pack] (dry-run) would bootstrap marketplace at {args.create_marketplace}")
            else:
                create_marketplace(args.create_marketplace.resolve(), params)
                out_json["created_marketplace"] = str(args.create_marketplace.resolve())
    except Exception as exc:  # noqa: BLE001
        return _fail(args, out_json, code=5, msg=f"marketplace op failed: {exc}")

    out_json["ok"] = True
    if args.json_mode:
        _emit_json(out_json)
    else:
        print(f"\n{GREEN}{BOLD}Done!{NC} Packed {len(selected)} component(s) into {target}")
        if args.add_to_marketplace:
            print(f"  Registered in marketplace: {args.add_to_marketplace}")
        if args.create_marketplace:
            print(f"  Bootstrapped new marketplace at: {args.create_marketplace}")
    return 0


def _fail(args: argparse.Namespace, payload: dict[str, object], *, code: int, msg: str) -> int:
    """Emit a structured failure (JSON or human) and return the exit code."""
    payload["ok"] = False
    payload["exit_code"] = code
    payload["error"] = msg
    if args.json_mode:
        _emit_json(payload)
    else:
        print(f"{RED}✗ {msg}{NC}", file=sys.stderr)
    return code


def _print_components(components: list[Component], *, header: str) -> None:
    print(f"\n{BOLD}{header}{NC}")
    by_type: dict[str, list[Component]] = {}
    for c in components:
        by_type.setdefault(c.type, []).append(c)
    for kind in VALID_TYPES:
        if kind not in by_type:
            continue
        print(f"  {kind}:")
        for c in by_type[kind]:
            print(f"    - {c.name}  ({c.src})")


if __name__ == "__main__":
    sys.exit(main())
