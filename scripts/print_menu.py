#!/usr/bin/env python3
"""CPV menu emitter — fixed (pre-baked) + dynamic (minimal-payload) menus (TRDD-ef3fc7d8).

Menus are emitted zero-token by the claude-menu-system Stop hook (TRDD-4de479a0).
This script lets an orchestrator queue a menu while sending the MINIMUM amount of
inline data, so the queue Bash card stays tiny:

    print_menu.py fixed <N> [--dir <skill-menus-dir>]
        Load the pre-baked fixed menu whose filename starts with ``<N>-`` from the
        skill's ``skill-menus/`` dir and queue it verbatim. The agent sends only an
        index — no inline JSON. The dir resolves from ``$CPV_SKILL_MENUS_DIR`` (the
        skill body exports it once) or ``--dir``.

    print_menu.py dynamic '<json>'
        Build a dynamic choice menu from a bare list of detected things (files,
        paths, plugin names, URLs, project folders, marketplaces). The agent sends
        ONLY the entries; this script sorts them alphabetically, numbers them
        ``1..N``, and AUTO-APPENDS the standard fixed footer (``P`` type-a-path,
        ``A`` ask, ``B`` back, ``M`` main menu, ``0`` exit). ``<json>`` is a JSON
        array of entries, or an object ``{entries, extra_options?, header?,
        footer?, slug?}``.

    print_menu.py dynamic --from-file <path>
        Same as above but the entries (+ optional extra_options/header/footer) come
        from a JSON file — for special dynamic lists that need extra options.

    print_menu.py - | <spec.json>
        Low-level escape hatch: queue a complete, hand-authored CMS spec read from
        stdin (``-``) or a file path.

Fixed-key contract (TRDD-4de479a0): numbers ``1..N`` are the DYNAMIC positional
list; letters are FIXED actions/navigation. The orchestrator routes the user's
typed key from its own skill-documented map; this script sets ``renumber: false``
(via ``write_menu``) so claude-menu-system keeps every key verbatim.

The orchestrator MUST end its turn after calling this — the hook emits post-turn.
Never print the menu inline.

The bridge core (``resolve_cms_root`` / ``write_menu`` / ``MenuSystemUnavailable``)
lives in this module (relocated from the retired ``cpv_menu.py`` in TRDD-ef3fc7d8
Phase 5 — one canonical script, no legacy duplicate).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Canonical install location of the menu-system plugin. We ONLY glob this exact
# path — never anything outside the plugin cache — so a crafted spec can't make
# us resolve an arbitrary directory.
_CMS_MARKETPLACE = "emasoft-plugins"
_CMS_PLUGIN = "claude-menu-system"

_INSTALL_HINT = "Install it:\n  claude plugin install Emasoft/claude-menu-system@emasoft-plugins\n  /reload-plugins"


class MenuSystemUnavailable(RuntimeError):
    """Raised when claude-menu-system is not installed.

    CPV renders every menu through that plugin's Stop-hook emitter and has no
    inline fallback renderer, so this is a hard, fail-fast error.
    """


def _default_cache_base() -> Path:
    return Path.home() / ".claude" / "plugins" / "cache" / _CMS_MARKETPLACE / _CMS_PLUGIN


def _version_key(version_dir: Path) -> tuple[int, object]:
    """Sort key that orders dotted numeric versions correctly (0.1.10 > 0.1.5).

    Falls back to a lexicographic tie-breaker for non-numeric version names so
    a stray directory never crashes resolution.
    """
    parts = version_dir.name.split(".")
    try:
        return (0, tuple(int(p) for p in parts))
    except ValueError:
        return (1, version_dir.name)


def resolve_cms_root(cache_base: Path | None = None) -> Path:
    """Return the newest installed claude-menu-system version directory.

    A version dir qualifies only if it ships ``scripts/menu_write.py`` (so a
    half-extracted install is skipped). Fails fast with an install hint if no
    usable version is present.

    Args:
        cache_base: override the canonical cache path (tests pass a temp dir).
    """
    base = cache_base if cache_base is not None else _default_cache_base()
    if not base.is_dir():
        raise MenuSystemUnavailable(
            f"claude-menu-system is not installed (looked in {base}). CPV renders "
            f"menus via that plugin's Stop-hook emitter and has no inline fallback.\n" + _INSTALL_HINT
        )
    usable = [d for d in base.iterdir() if d.is_dir() and (d / "scripts" / "menu_write.py").is_file()]
    if not usable:
        raise MenuSystemUnavailable(
            f"claude-menu-system is present at {base} but no version ships "
            f"scripts/menu_write.py. Reinstall:\n" + _INSTALL_HINT
        )
    return max(usable, key=_version_key)


def write_menu(spec: dict, *, cache_base: Path | None = None) -> Path:
    """Queue a menu spec for the claude-menu-system Stop hook to emit.

    Enforces CPV's fixed-key convention: ``renumber`` defaults to ``false`` so
    claude-menu-system keeps the caller's keys verbatim (it otherwise renumbers
    the rows that survive after dropping ``disabled`` ones). The caller owns the
    stable key→action_id contract and routes the user's reply from it directly.

    Args:
        spec: a claude-menu-system spec dict (``spec_version``, ``mode``,
            ``plugin``, ``slug``, plus the per-mode fields). ``renumber`` is set
            to ``false`` unless the caller already specified it.
        cache_base: test override for the CMS cache path.

    Returns the queue file path that ``menu_write.py`` allocated.

    Raises:
        MenuSystemUnavailable: claude-menu-system is not installed.
        RuntimeError: ``menu_write.py`` exited non-zero (invalid spec, etc.).
    """
    cms = resolve_cms_root(cache_base=cache_base)
    menu_write = cms / "scripts" / "menu_write.py"

    # Fixed-key convention: never renumber unless the caller explicitly opted in.
    spec_out = {**spec, "renumber": spec.get("renumber", False)}

    tf = tempfile.NamedTemporaryFile("w", suffix=".cpv-menu.json", delete=False, encoding="utf-8")
    try:
        json.dump(spec_out, tf)
        tf.flush()
        tf.close()
        proc = subprocess.run(
            [sys.executable, str(menu_write), tf.name],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        Path(tf.name).unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"menu_write.py failed (exit {proc.returncode}): {proc.stderr.strip()}")

    return Path(proc.stdout.strip())


__all__ = [
    "MenuSystemUnavailable",
    "resolve_cms_root",
    "write_menu",
    "assemble_dynamic_spec",
    "load_fixed_spec",
]

_SKILL_MENUS_ENV = "CPV_SKILL_MENUS_DIR"
_DEFAULT_PLUGIN = "claude-plugins-validation"

# Standard fixed footer auto-appended to every dynamic menu. SINGLE source of
# truth for the dynamic footer; letters never collide with the numeric entry keys.
_PATH_OPTION = {"key": "P", "action_id": "type_path", "label": "Type a path explicitly"}
_STANDARD_NAV = (
    {"key": "A", "action_id": "ask", "label": "Ask"},
    {"key": "B", "action_id": "back", "label": "Back"},
    {"key": "M", "action_id": "main", "label": "Main menu"},
    {"key": "0", "action_id": "exit", "label": "Exit"},
)
# Keys an author-supplied extra_option may NOT reuse (the auto-appended ones).
_RESERVED_DYNAMIC_KEYS = {"P", "A", "B", "M", "0"}

_DEFAULT_DYNAMIC_HEADER = "Pick one (or type a path):"
_DEFAULT_DYNAMIC_FOOTER = "Type a number, a letter, or paste a path:"

_USAGE = (
    "usage:\n"
    "  print_menu.py fixed <N> [--dir <skill-menus-dir>]\n"
    "  print_menu.py dynamic '<json-entries>' [--slug S] [--header H] [--footer F]\n"
    "  print_menu.py dynamic --from-file <path>\n"
    "  print_menu.py - | <spec.json>"
)


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic-menu assembly (pure — no CMS / IO)
# ─────────────────────────────────────────────────────────────────────────────


def _normalise_entry(item: object) -> dict[str, str]:
    """A dynamic entry is a plain string (the label) or ``{label, action_id?}``."""
    if isinstance(item, str):
        if not item:
            raise ValueError("dynamic entry string must be non-empty")
        return {"label": item, "action_id": item}
    if isinstance(item, dict):
        label = item.get("label")
        if not isinstance(label, str) or not label:
            raise ValueError(f"dynamic entry object needs a non-empty 'label': {item!r}")
        action_id = item.get("action_id", label)
        if not isinstance(action_id, str) or not action_id:
            raise ValueError(f"dynamic entry 'action_id' must be a non-empty string: {item!r}")
        return {"label": label, "action_id": action_id}
    raise ValueError(f"dynamic entry must be a string or object, got {type(item).__name__}")


def _validate_extra_option(opt: object) -> dict[str, str]:
    """An extra_option is a fixed letter row inserted before the standard nav."""
    if not isinstance(opt, dict):
        raise ValueError(f"extra_option must be an object, got {type(opt).__name__}")
    key = opt.get("key")
    if not isinstance(key, str) or len(key) != 1 or not key.isalpha():
        raise ValueError(f"extra_option 'key' must be a single letter: {opt!r}")
    ukey = key.upper()
    if ukey in _RESERVED_DYNAMIC_KEYS:
        raise ValueError(f"extra_option key {key!r} collides with a reserved dynamic key (P/A/B/M/0)")
    label = opt.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(f"extra_option needs a non-empty 'label': {opt!r}")
    action_id = opt.get("action_id", label)
    if not isinstance(action_id, str) or not action_id:
        raise ValueError(f"extra_option 'action_id' must be a non-empty string: {opt!r}")
    return {"key": ukey, "action_id": action_id, "label": label}


def assemble_dynamic_spec(
    entries: list[object],
    *,
    extra_options: list[object] | None = None,
    header: str | None = None,
    footer: str | None = None,
    slug: str = "dynamic",
    plugin: str = _DEFAULT_PLUGIN,
) -> dict:
    """Build a complete fixed-key menu spec from bare dynamic entries.

    Entries are sorted alphabetically (case-insensitive, stable) and numbered
    ``1..N`` (the DYNAMIC positional list). Then the standard ``P`` type-a-path
    row, any caller ``extra_options`` (fixed letter rows), then the standard nav
    footer ``A``/``B``/``M``/``0``. Letters never collide with the numbers — the
    TRDD-4de479a0 contract. Duplicate keys fail fast.

    ``entries`` is trusted to be a list (the CLI validates JSON shape at the
    untrusted boundary before calling this).
    """
    norm = [_normalise_entry(e) for e in entries]
    norm.sort(key=lambda e: e["label"].casefold())

    rows: list[dict[str, str]] = [
        {"key": str(i), "action_id": e["action_id"], "label": e["label"]} for i, e in enumerate(norm, start=1)
    ]
    rows.append(dict(_PATH_OPTION))
    for opt in extra_options or []:
        rows.append(_validate_extra_option(opt))
    rows.extend(dict(r) for r in _STANDARD_NAV)

    keys = [r["key"] for r in rows]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    if dups:
        raise ValueError(f"duplicate menu keys after assembly: {dups}")

    return {
        "spec_version": 1,
        "mode": "menu",
        "plugin": plugin,
        "slug": slug,
        "header": header or _DEFAULT_DYNAMIC_HEADER,
        "rows": rows,
        "footer": footer or _DEFAULT_DYNAMIC_FOOTER,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fixed-menu loading
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_skill_menus_dir(override: str | None = None) -> Path:
    if override:
        d = Path(override).expanduser()
    else:
        env = os.environ.get(_SKILL_MENUS_ENV, "").strip()
        if not env:
            raise ValueError(
                f"no skill-menus dir: set ${_SKILL_MENUS_ENV} "
                f'(export {_SKILL_MENUS_ENV}="$CLAUDE_PLUGIN_ROOT/skills/<skill>/skill-menus") '
                f"or pass --dir <path>"
            )
        d = Path(env).expanduser()
    if not d.is_dir():
        raise ValueError(f"skill-menus dir not found: {d}")
    return d


def load_fixed_spec(index: int, *, dir_override: str | None = None) -> dict:
    """Load the pre-baked fixed menu whose filename starts with ``<index>-``.

    Files are named ``<NN>-<name>.json``; matched by INTEGER prefix so zero-padding
    is tolerated (``6`` matches both ``06-foo.json`` and ``6-foo.json``). Exactly
    one match is required — zero or many fail fast.
    """
    d = _resolve_skill_menus_dir(dir_override)
    matches = []
    for f in sorted(d.glob("*.json")):
        prefix = f.name.split("-", 1)[0]
        try:
            if int(prefix) == index:
                matches.append(f)
        except ValueError:
            continue  # not a numbered fixed-menu file
    if not matches:
        raise ValueError(f"no fixed menu with index {index} in {d}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous fixed menu index {index} in {d}: {[m.name for m in matches]}")
    spec = json.loads(matches[0].read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError(f"fixed menu {matches[0].name} is not a JSON object")
    return spec


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _next_value(args: list[str], i: int) -> str:
    if i + 1 >= len(args):
        raise ValueError(f"{args[i]} needs a value")
    return args[i + 1]


def _cli_fixed(args: list[str]) -> dict:
    if not args:
        raise ValueError("usage: print_menu.py fixed <N> [--dir <path>]")
    try:
        index = int(args[0])
    except ValueError:
        raise ValueError(f"fixed index must be an integer, got {args[0]!r}") from None
    dir_override: str | None = None
    rest = args[1:]
    if rest:
        if rest[0] == "--dir" and len(rest) == 2:
            dir_override = rest[1]
        else:
            raise ValueError("usage: print_menu.py fixed <N> [--dir <path>]")
    return load_fixed_spec(index, dir_override=dir_override)


def _cli_dynamic(args: list[str]) -> dict:
    payload: str | None = None
    from_file: str | None = None
    slug: str | None = None
    header: str | None = None
    footer: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--from-file":
            from_file = _next_value(args, i)
            i += 2
        elif a == "--slug":
            slug = _next_value(args, i)
            i += 2
        elif a == "--header":
            header = _next_value(args, i)
            i += 2
        elif a == "--footer":
            footer = _next_value(args, i)
            i += 2
        elif a.startswith("--"):
            raise ValueError(f"unknown flag: {a}")
        else:
            if payload is not None:
                raise ValueError("dynamic takes at most one inline json payload")
            payload = a
            i += 1

    if from_file and payload:
        raise ValueError("dynamic: pass inline json OR --from-file, not both")

    extra_options: list[object] | None = None
    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
    elif payload is not None:
        data = json.loads(payload)
    else:
        raise ValueError("usage: print_menu.py dynamic '<json>' | --from-file <path>")

    if isinstance(data, list):
        entries: list[object] = data
    elif isinstance(data, dict):
        entries = data.get("entries", [])
        extra_options = data.get("extra_options")
        header = header or data.get("header")
        footer = footer or data.get("footer")
        slug = slug or data.get("slug")  # flag wins, then file, then default (below)
    else:
        raise ValueError("dynamic json must be a list of entries or an object")

    # Validate JSON shape at this untrusted boundary so assemble can trust its types.
    if not isinstance(entries, list):
        raise ValueError(f"dynamic 'entries' must be a list, got {type(entries).__name__}")
    if extra_options is not None and not isinstance(extra_options, list):
        raise ValueError(f"dynamic 'extra_options' must be a list, got {type(extra_options).__name__}")

    return assemble_dynamic_spec(
        entries, extra_options=extra_options, header=header, footer=footer, slug=slug or "dynamic"
    )


def _cli_raw(source: str) -> dict:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    spec = json.loads(raw)
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    return spec


def _cli(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(_USAGE, file=sys.stderr)
        return 2
    cmd = argv[1]
    try:
        if cmd == "fixed":
            spec = _cli_fixed(argv[2:])
        elif cmd == "dynamic":
            spec = _cli_dynamic(argv[2:])
        else:
            spec = _cli_raw(cmd)  # '-' (stdin) or a spec file path
    except (ValueError, OSError) as exc:
        # json.JSONDecodeError is a ValueError subclass — already covered.
        print(f"print_menu ({cmd}): {exc}", file=sys.stderr)
        return 2
    try:
        queue_path = write_menu(spec)
    except MenuSystemUnavailable as exc:
        print(f"print_menu: {exc}", file=sys.stderr)
        return 5
    except RuntimeError as exc:
        print(f"print_menu: {exc}", file=sys.stderr)
        return 3
    print(queue_path)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
