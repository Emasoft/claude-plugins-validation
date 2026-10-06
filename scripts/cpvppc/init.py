#!/usr/bin/env python3
"""CPVPPC init — infer a repo's shape and write cpvppc.yaml + .cpvppc-lock.json.

Static reads only; never executes repo code. The config carries only what was
DETECTED (principle 7: everything inferred is written explicitly into the
lock). The trust core is not configurable — no knob here can weaken a gate
(the schema rejects unknown keys outright).

CLI: python -m cpvppc.init <repo-root> [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover — direct-script invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EXIT_OK = 0
EXIT_ERROR = 2


def _canon_version() -> str:
    canon = json.loads((Path(__file__).resolve().parent / "canon.json").read_text(encoding="utf-8"))
    return str(canon["canon_version"])


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _load_yaml(text: str) -> Any:
    import yaml  # type: ignore[import-not-found]  # noqa: PLC0415 — lazy, matches verify.py

    return yaml.safe_load(text)


# --- shape detection (pure functions over a repo root) --------------------------


def _detect_kind(repo: Path) -> str:
    has_plugin = (repo / ".claude-plugin" / "plugin.json").is_file() or (repo / "plugin.json").is_file()
    has_mkt = (repo / ".claude-plugin" / "marketplace.json").is_file() or (repo / "marketplace.json").is_file()
    if has_plugin and has_mkt:
        return "plugin+marketplace"
    if has_plugin:
        return "plugin"
    if has_mkt:
        return "marketplace"
    return "plugin"  # a repo with neither defaults to the plugin shape


def _toolchain_python(repo: Path) -> str:
    text = _read_text(repo / ".python-version")
    if text:
        v = text.strip().splitlines()[0].strip() if text.strip() else ""
        if v:
            return v
    return "3.12"


def _toolchain_node(repo: Path) -> str:
    text = _read_text(repo / ".nvmrc")
    if text:
        v = text.strip().splitlines()[0].strip().lstrip("v") if text.strip() else ""
        if v:
            return v
    return "20"


def _cargo_bin_outputs(repo: Path, unit_path: str) -> list[dict[str, str]]:
    """[[bin]] names from the unit's Cargo.toml → bin outputs."""
    text = _read_text(repo / unit_path / "Cargo.toml")
    if text is None:
        return []
    names = re.findall(r"\[\[bin\]\][^\[]*?name\s*=\s*\"([^\"]+)\"", text, re.S)
    return [{"name": n, "kind": "bin"} for n in names]


def _node_bundler(repo: Path, unit_path: str) -> str | None:
    pkg = _read_text(repo / unit_path / "package.json")
    if pkg is None:
        return None
    try:
        data = json.loads(pkg)
    except json.JSONDecodeError:
        return None
    dev = data.get("devDependencies", {}) or {}
    for cand in ("esbuild", "tsdown"):
        if cand in dev:
            return cand
    for cand in ("vite",):
        if cand in dev:
            return cand
    return "bun" if "bun" in (data.get("scripts", {}) or {}).get("build", "") else None


def _node_test_runner(repo: Path, unit_path: str) -> str | None:
    pkg = _read_text(repo / unit_path / "package.json")
    if pkg is None:
        return None
    try:
        data = json.loads(pkg)
    except json.JSONDecodeError:
        return None
    dev = data.get("devDependencies", {}) or {}
    if "vitest" in dev:
        return "vitest"
    if "bun" in dev:
        return "bun test"
    return None


def _lockfile_for(repo: Path, unit_path: str, adapter: str) -> dict[str, str] | None:
    candidates = {
        "python-uv": "uv.lock",
        "node-ts": "package-lock.json",
        "rust-cargo": "Cargo.lock",
        "go": "go.sum",
    }
    name = candidates.get(adapter)
    if name and (repo / unit_path / name).is_file():
        return {"lockfile": f"{unit_path}/{name}".lstrip("./"), "strategy": "bundled"}
    return None


def _bin_launchers(repo: Path) -> list[dict[str, str]]:
    bindir = repo / "bin"
    if not bindir.is_dir():
        return []
    return [{"name": p.name, "output": p.name} for p in sorted(bindir.iterdir()) if p.is_file() and p.suffix == ""]


def _marketplace_targets(repo: Path, plugin_name: str | None) -> list[dict[str, str]]:
    """targets.marketplaces[] from .github/workflows/notify-marketplace.yml."""
    text = _read_text(repo / ".github" / "workflows" / "notify-marketplace.yml")
    if text is None:
        return []
    doc = _load_yaml(text)
    if not isinstance(doc, dict):
        return []
    # The env block may sit at the document level (CPV's own workflow) or on a job.
    env = dict(doc.get("env") or {})
    for job in (doc.get("jobs") or {}).values():
        if isinstance(job, dict) and isinstance(job.get("env"), dict):
            env.update(job["env"])
    owner = str(env.get("MARKETPLACE_OWNER", "")).strip()
    mrepo = str(env.get("MARKETPLACE_REPO", "")).strip()
    if not owner or not mrepo:
        return []
    return [{"owner": owner, "repo": mrepo, "entry_name": plugin_name or "plugin"}]


def _plugin_name(repo: Path) -> str | None:
    for rel in (".claude-plugin/plugin.json", "plugin.json"):
        text = _read_text(repo / rel)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        name = data.get("name")
        if isinstance(name, str) and name:
            return name
    return None


def detect_config(repo: Path) -> dict[str, Any]:
    """The full inferred config dict. Pure function of the tree."""
    cfg: dict[str, Any] = {
        "schema_version": _canon_version(),
        "canon": {"version": _canon_version()},
        "repo": {"kind": _detect_kind(repo)},
    }
    units: list[dict[str, Any]] = []

    has_py = any((repo / "scripts").glob("*.py")) if (repo / "scripts").is_dir() else False
    if has_py:
        u: dict[str, Any] = {
            "name": "scripts",
            "path": "scripts",
            "adapter": "python-uv",
            "toolchain": {"version": _toolchain_python(repo)},
        }
        lf = _lockfile_for(repo, "scripts", "python-uv")
        if lf:
            u["deps"] = lf
        units.append(u)

    # JS/TS must be in a node-ts unit (plan 3.1 rule 4) so it is type-checked.
    # The unit path is the nearest directory holding a package.json above the
    # .ts/.js files (e.g. scripts/llm-ext, hooks), falling back to the dir itself.
    node_units: dict[str, dict[str, Any]] = {}
    for d in ("hooks", "scripts"):
        base = repo / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in (".ts", ".js", ".mts", ".mjs"):
                continue
            rel = p.relative_to(repo)
            unit_path = rel.parent
            while not (repo / unit_path / "package.json").is_file() and len(unit_path.parts) > 1:
                unit_path = unit_path.parent
            if str(unit_path) == ".":
                unit_path = rel.parent
            upath = str(unit_path)
            if upath in node_units:
                continue
            u = {
                "name": "hooks" if d == "hooks" else "scripts-node",
                "path": upath,
                "adapter": "node-ts",
                "toolchain": {"version": _toolchain_node(repo)},
            }
            opts: dict[str, Any] = {}
            bundler = _node_bundler(repo, upath)
            if bundler:
                opts["bundler"] = bundler
            test = _node_test_runner(repo, upath)
            if test:
                opts["test"] = test
            if opts:
                u["adapter_options"] = opts
            lf = _lockfile_for(repo, upath, "node-ts")
            if lf:
                u["deps"] = lf
            node_units[upath] = u
    units.extend(node_units.values())

    for cargo_dir in ("rust", "."):
        cdir = repo / cargo_dir
        if not (cdir / "Cargo.toml").is_file():
            continue
        u = {
            "name": "engine" if cargo_dir == "rust" else "crate",
            "path": cargo_dir,
            "adapter": "rust-cargo",
            "toolchain": {"version": "stable"},
        }
        outputs = _cargo_bin_outputs(repo, cargo_dir)
        if outputs:
            u["build"] = {"kind": "compiled", "outputs": outputs, "targets": []}
        lf = _lockfile_for(repo, cargo_dir, "rust-cargo")
        if lf:
            u["deps"] = lf
        units.append(u)
        break

    if units:
        cfg["units"] = units

    if (repo / "bin").is_dir() and any((repo / "bin").iterdir()):
        cfg["artifacts"] = {"delivery": "committed-bin"}
        launchers = _bin_launchers(repo)
        if launchers:
            cfg["runtime"] = {"launchers": launchers}

    cfg["targets"] = {"marketplaces": _marketplace_targets(repo, _plugin_name(repo))}
    return cfg


# --- rendering ------------------------------------------------------------------


def _q(v: str) -> str:
    return json.dumps(v)  # YAML-compatible double-quoted scalar


def _flow(d: dict[str, Any]) -> str:
    parts = []
    for k, v in d.items():
        if isinstance(v, dict):
            parts.append(f"{k}: {_flow(v)}")
        elif isinstance(v, list):
            inner = ", ".join(_flow(x) if isinstance(x, dict) else _q(str(x)) for x in v)
            parts.append(f"{k}: [{inner}]")
        elif isinstance(v, bool):
            parts.append(f"{k}: {'true' if v else 'false'}")
        else:
            parts.append(f"{k}: {_q(str(v))}")
    return "{" + ", ".join(parts) + "}"


_SIMPLE_UNIT_KEYS = {"name", "path", "adapter", "toolchain"}


def render_config(cfg: dict[str, Any]) -> str:
    """The commented cpvppc.yaml text. A plain plugin renders ~6 content lines."""
    lines = [
        "# CPVPPC publishing-pipeline canon — generated by `cpvppc init <repo>`.",
        "# Author-editable SHAPE only; the trust core is not configurable. Validated",
        "# against scripts/cpvppc/config_schema.json: unknown keys are INVALID (plan",
        "# threat T1 — no skip/allow/ignore knob exists). After editing, regenerate",
        "# the lock: python -m cpvppc.init <repo> --force.",
        f"schema_version: {_q(cfg['schema_version'])}",
        f"canon: {_flow(cfg['canon'])}",
        f"repo: {_flow(cfg['repo'])}",
    ]
    units = cfg.get("units", [])
    if units:
        lines.append("units:")
        for u in units:
            if set(u) <= _SIMPLE_UNIT_KEYS:
                lines.append(f"  - {_flow(u)}")
            else:
                lines.append(f"  - name: {_q(u['name'])}")
                for k, v in u.items():
                    if k == "name":
                        continue
                    if isinstance(v, dict):
                        lines.append(f"    {k}: {_flow(v)}")
                    elif isinstance(v, list):
                        if v and all(isinstance(x, dict) for x in v):
                            inner = ", ".join(_flow(x) for x in v)
                            lines.append(f"    {k}: [{inner}]")
                        else:
                            inner = ", ".join(_q(str(x)) for x in v)
                            lines.append(f"    {k}: [{inner}]")
                    else:
                        lines.append(f"    {k}: {_q(str(v))}")
    targets = cfg.get("targets", {}).get("marketplaces", [])
    lines.append("targets:")
    if targets:
        inner = ", ".join(_flow(t) for t in targets)
        lines.append(f"  marketplaces: [{inner}]")
    else:
        lines.append("  marketplaces: []")
    for section in ("artifacts", "runtime"):
        if section in cfg:
            lines.append(f"{section}: {_flow(cfg[section])}")
    if cfg.get("writes_outside_data"):
        inner = ", ".join(_q(str(x)) for x in cfg["writes_outside_data"])
        lines.append(f"writes_outside_data: [{inner}]")
    return "\n".join(lines) + "\n"


def render_lock(cfg: dict[str, Any], config_bytes: bytes) -> dict[str, Any]:
    """Canonical lock: config sha, canon version, EVERY inferred field explicit."""
    return {
        "canon_version": _canon_version(),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "config": cfg,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def init_repo(repo: Path, force: bool = False) -> int:
    cfg_path = repo / "cpvppc.yaml"
    if cfg_path.exists() and not force:
        print(f"error: {cfg_path} already exists — pass --force to overwrite", file=sys.stderr)
        return EXIT_ERROR
    cfg = detect_config(repo)
    text = render_config(cfg)
    raw = text.encode("utf-8")
    lock = render_lock(cfg, raw)
    cfg_path.write_bytes(raw)
    (repo / ".cpvppc-lock.json").write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {cfg_path}")
    print(f"wrote {repo / '.cpvppc-lock.json'}")
    print(f"repo.kind={cfg['repo']['kind']} units={len(cfg.get('units', []))} "
          f"marketplaces={len(cfg['targets']['marketplaces'])}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cpvppc-init", description=__doc__)
    ap.add_argument("repo", type=Path, help="repo root to initialise")
    ap.add_argument("--force", action="store_true", help="overwrite an existing cpvppc.yaml")
    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return EXIT_ERROR
    return init_repo(repo, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
