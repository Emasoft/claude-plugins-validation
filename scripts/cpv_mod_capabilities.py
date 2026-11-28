#!/usr/bin/env python3
"""Static capability inventory and risky-shape scan for a Claude Code mod hooks module.

TRDD-29QP66H2, gap items D1 and D2 (owner decision 2026-11-28, "Inventory + risky"):
one INFO per module listing every literal `$.noun.method(` call, every hook event
the module registers and every literal host passed to `$.http.fetch`, plus one finding
for each of three risky shapes (MAJOR unless stated):

- MOD-CAP-ENV-SET-LOADER: `$.env.set` of PATH, PYTHONPATH, NODE_OPTIONS, LD_* or DYLD_*
  (names compared case-insensitively, because Windows spells PATH as Path);
- MOD-CAP-GATE-FAILS-OPEN (WARNING, see check_gate): a gating hook (tool.call, tool.check,
  plugin.register) whose inline handler awaits work and holds no `catch` anywhere (a catch
  chained as `on(...).catch(h)` counts), so a throw makes the host let the call through;
- MOD-CAP-ASUSER-SUBMIT (WARNING by owner decision 2026-11-28, after the held-out sweep found it in
  335 of 7418 accepted modules): `$.prompt.submit(..., { asUser: true })`.

Fail-open contract (same as cpv_mod_register_scan): the module is only ever lexed,
never executed, imported or evaluated, and no sibling file is opened. Whatever the
scanner cannot decide (a computed env name, a handler passed by reference, options
held in a variable, a spread, a call inside a template substitution, a lexer
failure) is reported as "could not decide" inside the INFO, never as a MAJOR.

Only the literal shape `$.noun.method(` counts; aliases of `$` are a separate rule.

Relies on cpv_mod_register_scan: Tok (kind, value, line, depth, nl), _Lexer(src, jsx)
with .run(in_template=False), .match (opener index to closer index), .idents, the
_Undecided exception and the _ESCAPED_STRING_MARK prefix. There is no public
tokenizer there yet; all private access is confined to _lex below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from cpv_mod_register_scan import _ESCAPED_STRING_MARK, JSX_SUFFIXES, Tok, _Lexer, _Undecided

Severity = Literal["info", "warning", "major"]

RULE_INVENTORY = "MOD-CAP-INVENTORY"
RULE_ENV_LOADER = "MOD-CAP-ENV-SET-LOADER"
RULE_GATE_OPEN = "MOD-CAP-GATE-FAILS-OPEN"
RULE_ASUSER = "MOD-CAP-ASUSER-SUBMIT"

# Same 2 MiB bound validate_hook applies before it reads a hooks module.
MAX_MODULE_BYTES = 2 * 1024 * 1024
GATING_EVENTS = frozenset({"tool.call", "tool.check", "plugin.register"})
_LOADER_ENV_NAMES = frozenset({"PATH", "PYTHONPATH", "NODE_OPTIONS"})
_LOADER_ENV_PREFIXES = ("LD_", "DYLD_")
_BOM = chr(0xFEFF)
_MEMBER_DOTS = frozenset({".", "?."})
_MAX_UNDECIDED_LISTED = 10


@dataclass(frozen=True)
class Finding:
    """One capability finding. line is the 1-based source line, None when the
    finding concerns the whole module."""

    severity: Severity
    rule: str
    message: str
    line: int | None


@dataclass(frozen=True)
class _Lexed:
    toks: list[Tok]
    match: dict[int, int]
    dollar_total: int  # every `$` identifier, template substitutions included


def _lex(source: str, suffix: str) -> _Lexed | str:
    """Tokenize a module, or return the reason it could not be tokenized.

    Mirrors the source preparation of scan_register_export (BOM strip, shebang
    line blanked) and its fail-open handling of nesting too deep for Python.
    """
    src = source[1:] if source.startswith(_BOM) else source
    if src.startswith("#!"):
        newline = src.find("\n")
        src = "" if newline < 0 else " " * newline + src[newline:]
    try:
        lexer = _Lexer(src, suffix in JSX_SUFFIXES)
        toks = lexer.run(in_template=False)
    except _Undecided as exc:
        return str(exc)
    except RecursionError:
        return "nesting too deep for the scanner"
    return _Lexed(toks, lexer.match, lexer.idents["$"])


def _is_loader_env_name(name: str) -> bool:
    upper = name.upper()
    return upper in _LOADER_ENV_NAMES or upper.startswith(_LOADER_ENV_PREFIXES)


class _ModScan:
    """One pass over the token stream of one module."""

    def __init__(self, lexed: _Lexed) -> None:
        self.t = lexed.toks
        self.match = lexed.match
        self.dollar_total = lexed.dollar_total
        self.calls: set[str] = set()
        self.events: set[str] = set()
        self.hosts: list[str] = []
        self.undecided: list[str] = []
        self.flagged: list[Finding] = []

    # -- token helpers -------------------------------------------------
    def is_p(self, j: int, value: str) -> bool:
        tk = self.t[j] if 0 <= j < len(self.t) else None
        return tk is not None and tk.kind == "punct" and tk.value == value

    def is_id(self, j: int, value: str | None = None) -> bool:
        tk = self.t[j] if 0 <= j < len(self.t) else None
        return tk is not None and tk.kind == "ident" and (value is None or tk.value == value)

    def after_member_dot(self, j: int) -> bool:
        return j > 0 and self.t[j - 1].kind == "punct" and self.t[j - 1].value in _MEMBER_DOTS

    def arg_ranges(self, open_idx: int) -> list[tuple[int, int]] | None:
        """Token index ranges [start, end) of the comma separated items between
        a matched opener and its closer; None when the opener has no match."""
        close = self.match.get(open_idx)
        if close is None:
            return None
        base = self.t[open_idx].depth + 1
        ranges: list[tuple[int, int]] = []
        start = open_idx + 1
        for k in range(open_idx + 1, close):
            tk = self.t[k]
            if tk.depth == base and tk.kind == "punct" and tk.value == ",":
                ranges.append((start, k))
                start = k + 1
        if start < close:
            ranges.append((start, close))
        return ranges

    def literal(self, rng: tuple[int, int]) -> str | None:
        """The value of a range that is exactly one plain string token."""
        start, end = rng
        if end - start != 1:
            return None
        tk = self.t[start]
        if tk.kind != "str" or tk.value.startswith(_ESCAPED_STRING_MARK):
            return None
        return tk.value

    def is_object_literal(self, rng: tuple[int, int]) -> bool:
        start, end = rng
        return self.is_p(start, "{") and self.match.get(start) == end - 1

    def object_keys(self, brace: int) -> list[str] | None:
        """Keys of an object literal, or None when any property is not a plain
        `key: value` pair (shorthand, spread, computed key, method)."""
        props = self.arg_ranges(brace)
        if props is None:
            return None
        keys: list[str] = []
        for start, _end in props:
            tk = self.t[start]
            if tk.kind not in ("ident", "str") or not self.is_p(start + 1, ":"):
                return None
            if tk.value.startswith(_ESCAPED_STRING_MARK):
                return None
            keys.append(tk.value)
        return keys

    def note(self, text: str) -> None:
        self.undecided.append(text)

    # -- passes ---------------------------------------------------------
    def run(self) -> None:
        self.scan_dollar_calls()
        self.scan_hooks()

    def scan_dollar_calls(self) -> None:
        seen = 0
        for i, tk in enumerate(self.t):
            if tk.kind != "ident" or tk.value != "$":
                continue
            seen += 1
            if self.after_member_dot(i):
                continue
            if self.is_p(i + 1, "?."):
                self.note(f"optional chaining on $ at line {tk.line}")
                continue
            if not (self.is_p(i + 1, ".") and self.is_id(i + 2) and self.is_p(i + 3, ".") and self.is_id(i + 4)):
                continue
            name = f"$.{self.t[i + 2].value}.{self.t[i + 4].value}"
            if not self.is_p(i + 5, "("):
                self.note(f"`{name}` is not called directly at line {tk.line} (alias, longer chain or reference)")
                continue
            self.calls.add(name)
            args = self.arg_ranges(i + 5)
            if args is None:
                self.note(f"`{name}` call at line {tk.line} has unmatched brackets")
                continue
            if name == "$.env.set":
                self.check_env_set(tk.line, args)
            elif name == "$.prompt.submit":
                self.check_submit(tk.line, args)
            elif name == "$.http.fetch":
                self.check_fetch(tk.line, args)
        if self.dollar_total > seen:
            self.note(f"{self.dollar_total - seen} `$` identifier(s) inside template substitutions were not scanned")

    def check_env_set(self, line: int, args: list[tuple[int, int]]) -> None:
        if not args:
            self.note(f"`$.env.set` at line {line} has no arguments")
            return
        names: list[str]
        literal = self.literal(args[0])
        if literal is not None:
            names = [literal]
        elif self.is_object_literal(args[0]):
            keys = self.object_keys(args[0][0])
            if keys is None:
                self.note(f"`$.env.set` object at line {line} has a key that is not a plain name")
                return
            names = keys
        else:
            self.note(f"`$.env.set` name at line {line} is not a plain string literal")
            return
        for name in names:
            if _is_loader_env_name(name):
                self.flagged.append(
                    Finding(
                        "major",
                        RULE_ENV_LOADER,
                        f"`$.env.set` writes {name} (line {line}): the mod changes how every later process "
                        "resolves programs or libraries; a mod that must do this should say so in its README "
                        "and the user should review it",
                        line,
                    )
                )

    def check_submit(self, line: int, args: list[tuple[int, int]]) -> None:
        for index, rng in enumerate(args):
            if self.is_object_literal(rng):
                state = self.as_user_state(rng[0])
                if state == "true":
                    self.flagged.append(
                        Finding(
                            "warning",
                            RULE_ASUSER,
                            f"`$.prompt.submit` with asUser: true (line {line}): the text reaches the "
                            "model as if the user had typed it",
                            line,
                        )
                    )
                elif state not in ("false", "absent"):
                    self.note(f"`$.prompt.submit` at line {line}: asUser is {state}")
            elif index >= 1:
                self.note(f"`$.prompt.submit` options at line {line} are not an object literal")

    def as_user_state(self, brace: int) -> str:
        """ "true", "false", "absent", or a reason the value cannot be read."""
        props = self.arg_ranges(brace)
        if props is None:
            return "in an object with unmatched brackets"
        state = "absent"
        for start, end in props:
            tk = self.t[start]
            if tk.kind == "punct" and tk.value in ("...", "["):
                return "possibly set by a spread or computed key"
            if tk.kind not in ("ident", "str") or tk.value != "asUser":
                continue
            if end - start == 3 and self.is_p(start + 1, ":") and self.is_id(start + 2):
                value = self.t[start + 2].value
                if value in ("true", "false"):
                    state = value
                    continue
            return "not a plain true or false literal"
        return state

    def check_fetch(self, line: int, args: list[tuple[int, int]]) -> None:
        literal = self.literal(args[0]) if args else None
        host: str | None = None
        if literal is not None:
            try:
                host = urlsplit(literal).hostname
            except ValueError:
                host = None
        if host is None:
            self.note(f"`$.http.fetch` URL at line {line} has no literal host")
        elif host not in self.hosts:
            self.hosts.append(host)

    def on_names(self) -> set[str]:
        """The hook registration function name: `on` plus the first parameter of
        the register function, whatever it is called."""
        names = {"on"}
        for i, tk in enumerate(self.t):
            if tk.kind != "ident" or tk.value != "register":
                continue
            j = i + 1
            declared = self.is_id(i - 1, "function")
            if self.is_p(j, "="):
                j += 1
                if self.is_id(j, "async"):
                    j += 1
                if self.is_id(j, "function"):
                    j += 1
                declared = True
            if declared and self.is_p(j, "(") and self.is_id(j + 1):
                names.add(self.t[j + 1].value)
        return names

    def scan_hooks(self) -> None:
        names = self.on_names()
        for i, tk in enumerate(self.t):
            if tk.kind != "ident" or tk.value not in names or not self.is_p(i + 1, "("):
                continue
            if self.after_member_dot(i) or self.is_id(i - 1, "function"):
                continue
            args = self.arg_ranges(i + 1)
            if args is None:
                self.note(f"`{tk.value}(` call at line {tk.line} has unmatched brackets")
                continue
            if not args:
                continue
            event = self.literal(args[0])
            if event is None:
                self.note(f"hook event at line {tk.line} is not a plain string literal")
                continue
            self.events.add(event)
            if event in GATING_EVENTS:
                # `on(...).catch(handler)` registers the hook error handler: arg_ranges
                # returned a list, so the opener has a closer.
                close = self.match[i + 1]
                chained_catch = self.is_p(close + 1, ".") and self.is_id(close + 2, "catch")
                self.check_gate(tk.line, event, args, chained_catch)

    def check_gate(self, line: int, event: str, args: list[tuple[int, int]], chained_catch: bool) -> None:
        if len(args) < 2:
            return
        start, end = args[-1]
        inline = any(
            (self.t[k].kind == "punct" and self.t[k].value == "=>") or self.is_id(k, "function")
            for k in range(start, end)
        )
        if not inline:
            self.note(f"`{event}` handler at line {line} is not an inline function")
            return
        has_catch = chained_catch or any(self.is_id(k, "catch") for k in range(start, end))
        awaits = any(
            (self.is_id(k, "await") and not self.after_member_dot(k))
            or (self.is_id(k, "then") and self.after_member_dot(k) and self.is_p(k + 1, "("))
            for k in range(start, end)
        )
        if awaits and not has_catch:
            self.flagged.append(
                Finding(
                    # WARNING, not MAJOR: held-out sweep of 7418 accepted catalogue modules found the shape in
                    # 1556 of them (21%, 2416 hooks), so MAJOR would fail one accepted mod in five; owner decides.
                    "warning",
                    RULE_GATE_OPEN,
                    f"`{event}` hook at line {line} awaits work with no catch (no try/catch, no .catch): "
                    "a throw or rejection fails open and the gated call goes through; "
                    "wrap the body in try/catch and return a deny decision on error",
                    line,
                )
            )

    def inventory(self, shown: str) -> Finding:
        calls = ", ".join(sorted(self.calls)) or "none"
        hooks = ", ".join(sorted(self.events)) or "none"
        hosts = ", ".join(self.hosts) or "none"
        message = f"mod capabilities of {shown}: calls: {calls}; hooks: {hooks}; hosts: {hosts}"
        if self.undecided:
            listed = self.undecided[:_MAX_UNDECIDED_LISTED]
            extra = len(self.undecided) - len(listed)
            tail = f" (and {extra} more)" if extra > 0 else ""
            message += "; could not decide: " + "; ".join(listed) + tail
        return Finding("info", RULE_INVENTORY, message, None)


def _display_path(module_path: Path, plugin_root: Path) -> str:
    try:
        return module_path.resolve().relative_to(plugin_root.resolve()).as_posix()
    except ValueError:
        return module_path.name


def scan_mod_capabilities(module_path: Path, plugin_root: Path) -> list[Finding]:
    """Scan one mod hooks module: the inventory INFO first, then the MAJOR
    findings in source order. Never raises for module content: an oversized,
    non-UTF-8 or unlexable module yields one INFO saying it could not decide."""
    shown = _display_path(module_path, plugin_root)
    prefix = f"mod capabilities of {shown}: could not decide ("
    if module_path.stat().st_size > MAX_MODULE_BYTES:
        return [Finding("info", RULE_INVENTORY, prefix + "file larger than 2 MiB)", None)]
    try:
        source = module_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return [Finding("info", RULE_INVENTORY, prefix + "not valid UTF-8)", None)]
    lexed = _lex(source, module_path.suffix)
    if isinstance(lexed, str):
        return [Finding("info", RULE_INVENTORY, prefix + lexed + ")", None)]
    scan = _ModScan(lexed)
    scan.run()
    return [scan.inventory(shown), *sorted(scan.flagged, key=lambda f: f.line or 0)]
