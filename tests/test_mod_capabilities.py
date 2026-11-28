#!/usr/bin/env python3
"""Tests for cpv_mod_capabilities.py (TRDD-29QP66H2 D1, D2): the mod capability
inventory plus the three risky shapes. Every MAJOR rule is tested two-sided."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_mod_capabilities import (  # noqa: E402
    MAX_MODULE_BYTES,
    RULE_ASUSER,
    RULE_ENV_LOADER,
    RULE_GATE_OPEN,
    RULE_INVENTORY,
    Finding,
    scan_mod_capabilities,
)

# Probe fixture p37-env-set of the mods gap evidence, verbatim.
P37 = r"""export function register(on) {
  on("session.start", async ($, e, next) => { $.env.set("PATH", "/tmp"); await $.process.run("ls"); await $.http.fetch("https://example.com"); return next(e) })
}
"""
# Probe fixture p00-baseline: the pass-through gate every mod author starts from.
P00 = r"""export function register(on) {
  on("tool.call", async ($, e, next) => next(e))
}
"""


def scan(tmp_path: Path, source: str, name: str = "register.js") -> list[Finding]:
    """Write source to hooks/<name> under tmp_path and scan it."""
    module = tmp_path / "hooks" / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return scan_mod_capabilities(module, tmp_path)


def majors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "major"]


def rules(findings: list[Finding]) -> list[str]:
    return [f.rule for f in majors(findings)]


def inventory(findings: list[Finding]) -> str:
    assert findings[0].severity == "info"
    assert findings[0].rule == RULE_INVENTORY
    assert findings[0].line is None
    assert sum(1 for f in findings if f.rule == RULE_INVENTORY) == 1
    return findings[0].message


def gate(event: str, body: str) -> str:
    return f"""export function register(on) {{
  on("{event}", async ($, e, next) => {body})
}}
"""


def test_p37_env_set_path_is_major_and_inventory_matches_native(tmp_path: Path) -> None:
    """p37: PATH write is MAJOR; the inventory lists the same calls, hook and host native prints."""
    findings = scan(tmp_path, P37)
    assert rules(findings) == [RULE_ENV_LOADER]
    assert majors(findings)[0].line == 2
    text = inventory(findings)
    assert "calls: $.env.set, $.http.fetch, $.process.run" in text
    assert "hooks: session.start" in text
    assert "hosts: example.com" in text
    assert "hooks/register.js" in text
    assert "could not decide" not in text


@pytest.mark.parametrize("name", ["PATH", "PYTHONPATH", "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "Path"])
def test_env_set_loader_names_are_major(tmp_path: Path, name: str) -> None:
    """Every loader-affecting variable name is MAJOR."""
    findings = scan(
        tmp_path,
        f"""export function register(on) {{ on("session.start", async ($, e, next) => {{ $.env.set("{name}", "x"); return next(e) }}) }}""",
    )
    assert rules(findings) == [RULE_ENV_LOADER]


@pytest.mark.parametrize("name", ["MY_FLAG", "HOME", "LDAP_URL", "PATHEXT", "DYLD", "NODE_ENV"])
def test_env_set_other_names_are_inventory_only(tmp_path: Path, name: str) -> None:
    """A harmless variable is listed in the inventory and nothing else."""
    findings = scan(
        tmp_path,
        f"""export function register(on) {{ on("session.start", async ($, e, next) => {{ $.env.set("{name}", "1"); return next(e) }}) }}""",
    )
    assert len(findings) == 1
    assert "calls: $.env.set" in inventory(findings)


def test_env_set_object_form(tmp_path: Path) -> None:
    """An object literal naming PATH is MAJOR; an object naming only a harmless key is not."""
    src = """export function register(on) { on("session.start", async ($, e, next) => { $.env.set({ %s: "x" }); return next(e) }) }"""
    assert rules(scan(tmp_path, src % "PATH")) == [RULE_ENV_LOADER]
    assert majors(scan(tmp_path, src % "MY_FLAG")) == []


_Q = chr(34)
_BS = chr(92)
UNDECIDABLE_ENV_NAMES = [
    "name",
    _Q + "PA" + _Q + " + " + _Q + "TH" + _Q,  # concatenation
    _Q + "PA" + _BS + "u0054H" + _Q,  # escaped string: the lexer does not decode it
    "`PA${x}`",
    "{ ...extra }",
    "{ PATH }",
]


@pytest.mark.parametrize("arg", UNDECIDABLE_ENV_NAMES)
def test_env_set_undecidable_name_is_never_major(tmp_path: Path, arg: str) -> None:
    """A computed, concatenated, escaped or shorthand name is reported as could not decide, never MAJOR."""
    src = f"""export function register(on) {{ on("session.start", async ($, e, next) => {{ $.env.set({arg}, "x"); return next(e) }}) }}"""
    findings = scan(tmp_path, src)
    assert majors(findings) == []
    assert "could not decide" in inventory(findings)


@pytest.mark.parametrize("event", ["tool.call", "tool.check", "plugin.register"])
def test_gate_awaiting_without_catch_is_major(tmp_path: Path, event: str) -> None:
    """A gating hook that awaits and has no catch fails open: MAJOR."""
    findings = scan(tmp_path, gate(event, "{ const r = await $.http.fetch(e.url); return next(e) }"))
    assert rules(findings) == [RULE_GATE_OPEN]
    assert majors(findings)[0].line == 2


@pytest.mark.parametrize(
    "body",
    [
        '{ const r = await $.http.fetch(e.url).catch(() => ({ decision: "deny" })); return next(e) }',
        '{ try { const r = await $.http.fetch(e.url); return next(e) } catch (err) { return { decision: "deny" } } }',
        '{ return $.http.fetch(e.url).then(() => next(e)).catch(() => ({ decision: "deny" })) }',
        "next(e)",
        "{ return next(e) }",
    ],
)
def test_gate_with_catch_or_nothing_awaited_is_not_major(tmp_path: Path, body: str) -> None:
    """.catch, try/catch, or a handler that awaits nothing (p00 shape) is not MAJOR."""
    assert majors(scan(tmp_path, gate("tool.call", body))) == []


def test_p00_pass_through_gate_is_not_major(tmp_path: Path) -> None:
    """The p00 baseline (native prints a note for it) stays inventory only."""
    findings = scan(tmp_path, P00)
    assert len(findings) == 1
    assert "hooks: tool.call" in inventory(findings)


def test_gate_try_finally_without_catch_is_major(tmp_path: Path) -> None:
    """try/finally does not catch, so the throw still escapes: MAJOR."""
    body = "{ try { await $.http.fetch(e.url); return next(e) } finally { cleanup() } }"
    assert rules(scan(tmp_path, gate("tool.check", body))) == [RULE_GATE_OPEN]


def test_non_gating_event_awaiting_is_not_major(tmp_path: Path) -> None:
    """Only tool.call, tool.check and plugin.register are gates."""
    assert majors(scan(tmp_path, gate("session.start", "{ await $.http.fetch(e.url); return next(e) }"))) == []


def test_gate_with_matcher_object_uses_last_argument(tmp_path: Path) -> None:
    """p26 shape: a matcher object before the handler; the handler is the last argument."""
    src = """export function register(on) {
  on("tool.call", { tool: "Bash" }, async ($, e, next) => { await $.http.fetch("https://a.example"); return next(e) })
}
"""
    assert rules(scan(tmp_path, src)) == [RULE_GATE_OPEN]


def test_gate_handler_by_reference_is_could_not_decide(tmp_path: Path) -> None:
    """A handler held in a variable cannot be read: no MAJOR, INFO says so."""
    src = """async function h($, e, next) { await $.http.fetch("https://a.example"); return next(e) }
export function register(on) {
  on("tool.call", h)
}
"""
    findings = scan(tmp_path, src)
    assert majors(findings) == []
    assert "not an inline function" in inventory(findings)


def test_register_parameter_name_is_followed(tmp_path: Path) -> None:
    """The hook function is whatever the register function names its first parameter."""
    src = """export const register = (hook) => {
  hook("tool.call", async ($, e, next) => { await $.http.fetch("https://a.example"); return next(e) })
}
"""
    assert rules(scan(tmp_path, src)) == [RULE_GATE_OPEN]


def test_member_call_named_on_is_not_a_hook(tmp_path: Path) -> None:
    """emitter.on("tool.call", ...) is not a mod hook registration."""
    src = """export function register(on) { emitter.on("tool.call", async () => { await x() }) }
"""
    findings = scan(tmp_path, src)
    assert majors(findings) == []
    assert "hooks: none" in inventory(findings)


def test_as_user_true_is_major(tmp_path: Path) -> None:
    """$.prompt.submit with asUser: true is MAJOR."""
    src = """export function register(on) { on("session.start", async ($, e, next) => { await $.prompt.submit("hi", { asUser: true }); return next(e) }) }"""
    findings = scan(tmp_path, src)
    assert rules(findings) == [RULE_ASUSER]
    assert "$.prompt.submit" in inventory(findings)


@pytest.mark.parametrize("opts", ["", ", { asUser: false }", ", { other: 1 }", ", {}"])
def test_as_user_absent_or_false_is_inventory_only(tmp_path: Path, opts: str) -> None:
    """Without asUser: true the submit is listed and nothing else."""
    src = f"""export function register(on) {{ on("session.start", async ($, e, next) => {{ await $.prompt.submit("hi"{opts}); return next(e) }}) }}"""
    findings = scan(tmp_path, src)
    assert len(findings) == 1
    assert "calls: $.prompt.submit" in inventory(findings)


@pytest.mark.parametrize(
    "opts", [", { asUser }", ", { asUser: flag }", ", options", ", { ...base }", ", { [k]: true }"]
)
def test_as_user_undecidable_is_never_major(tmp_path: Path, opts: str) -> None:
    """Shorthand, variable value, variable options, spread or computed key: could not decide, never MAJOR."""
    src = f"""export function register(on) {{ on("session.start", async ($, e, next) => {{ await $.prompt.submit("hi"{opts}); return next(e) }}) }}"""
    findings = scan(tmp_path, src)
    assert majors(findings) == []
    assert "could not decide" in inventory(findings)


def test_module_without_dollar_calls_inventory_says_none(tmp_path: Path) -> None:
    """A module with no $ calls and no hooks gets one INFO that says none."""
    findings = scan(tmp_path, "export function register(on) {}\n")
    assert len(findings) == 1
    text = inventory(findings)
    assert "calls: none" in text
    assert "hooks: none" in text
    assert "hosts: none" in text


def test_dollar_after_member_dot_is_not_a_call_site(tmp_path: Path) -> None:
    """obj.$.env.set(...) belongs to some other object, not the host API."""
    findings = scan(tmp_path, """export function register(on) { lib.$.env.set("PATH", "x") }""")
    assert majors(findings) == []
    assert "calls: none" in inventory(findings)


def test_optional_chaining_and_references_are_could_not_decide(tmp_path: Path) -> None:
    """$?.env.set and a bare $.env.set reference are not literal call sites: no MAJOR, INFO says so."""
    src = """export function register(on) { on("session.start", async ($, e, next) => { $?.env.set("PATH", "x"); const f = $.env.set; return next(e) }) }"""
    findings = scan(tmp_path, src)
    assert majors(findings) == []
    text = inventory(findings)
    assert "optional chaining" in text
    assert "not called directly" in text


def test_call_inside_template_substitution_is_reported(tmp_path: Path) -> None:
    """A $ call inside a template substitution never reaches the token stream; the INFO says so."""
    src = """export function register(on) { on("session.start", async ($, e, next) => { log(`v=${$.env.get("X")}`); return next(e) }) }"""
    text = inventory(scan(tmp_path, src))
    assert "template substitutions were not scanned" in text


def test_hosts_are_distinct_and_literal_only(tmp_path: Path) -> None:
    """Literal hosts are listed once; a variable URL or a relative URL is could not decide."""
    src = """export function register(on) { on("session.start", async ($, e, next) => {
  await $.http.fetch("https://a.example/x"); await $.http.fetch("https://a.example/y"); await $.http.fetch("http://b.example:8080/z")
  await $.http.fetch(url); await $.http.fetch("/relative")
  return next(e) }) }"""
    text = inventory(scan(tmp_path, src))
    assert "hosts: a.example, b.example" in text
    assert text.count("has no literal host") == 2


def test_typescript_module(tmp_path: Path) -> None:
    """A .ts module with type annotations is scanned the same way."""
    src = """export function register(on: any): void {
  on("tool.call", async ($: any, e: any, next: any) => { await $.http.fetch("https://a.example"); $.env.set("PATH", "x"); return next(e) })
}
"""
    # Both findings sit on line 2; compare as sets of rule ids, order is by line only.
    assert sorted(rules(scan(tmp_path, src, "register.ts"))) == sorted([RULE_ENV_LOADER, RULE_GATE_OPEN])


def test_shebang_and_bom_do_not_break_the_scan(tmp_path: Path) -> None:
    """A BOM or a shebang line at the top is ignored."""
    assert rules(scan(tmp_path, chr(0xFEFF) + P37)) == [RULE_ENV_LOADER]
    assert rules(scan(tmp_path, "#!/usr/bin/env node\n" + P37)) == [RULE_ENV_LOADER]


def test_oversized_module_is_one_info(tmp_path: Path) -> None:
    """A module over 2 MiB is not read: one INFO, no exception."""
    findings = scan(tmp_path, "//" + "x" * MAX_MODULE_BYTES + "\n" + P37)
    assert len(findings) == 1
    assert "larger than 2 MiB" in findings[0].message


def test_non_utf8_module_is_one_info(tmp_path: Path) -> None:
    """Invalid UTF-8 is one INFO, no exception."""
    module = tmp_path / "hooks" / "register.js"
    module.parent.mkdir(parents=True)
    module.write_bytes(b"export function register(on) {} // \xff\xfe\n")
    findings = scan_mod_capabilities(module, tmp_path)
    assert len(findings) == 1
    assert "not valid UTF-8" in findings[0].message


@pytest.mark.parametrize(
    "src", ['export function register(on) { $.env.set("PATH", "x"', 'const s = "unterminated\n', "{{{"]
)
def test_unlexable_module_is_one_info_never_major(tmp_path: Path, src: str) -> None:
    """Whatever the lexer rejects is could not decide, never a MAJOR and never an exception."""
    findings = scan(tmp_path, src)
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "could not decide" in findings[0].message


def test_module_outside_plugin_root_shows_file_name(tmp_path: Path) -> None:
    """A module that is not under plugin_root is shown by file name only."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    module = other / "register.js"
    module.write_text(P00, encoding="utf-8")
    root = tmp_path / "plugin"
    root.mkdir()
    assert "mod capabilities of register.js:" in scan_mod_capabilities(module, root)[0].message


def test_lexer_contract_this_scanner_relies_on() -> None:
    """Guards the private cpv_mod_register_scan surface cpv_mod_capabilities relies on:
    Tok fields, bracket matching by token index and the identifier counter."""
    from cpv_mod_register_scan import _Lexer

    lexer = _Lexer("on(a, $.x.y())", False)
    toks = lexer.run(in_template=False)
    assert [t.value for t in toks][:3] == ["on", "(", "a"]
    assert (toks[0].kind, toks[0].line, toks[0].depth, toks[0].nl) == ("ident", 1, 0, True)
    assert toks[lexer.match[1]].value == ")"
    assert lexer.match[1] == len(toks) - 1
    assert lexer.idents["$"] == 1
