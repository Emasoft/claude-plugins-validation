"""Module-body rules C1, C5, C6, C7, C8 of the mods gap list (TRDD-29QP66H2 batch 2).

`claude plugin validate` 2.1.332 errors on an unknown event in on(...) (C1), on the mods API `$`
used other than as `$.noun.method(...)` (C5), on a `$.state` reference the types contract does not
declare (C6), on a top-level `export` of the contract that is not a type (C7), and on a Client
element whose module is missing or misnamed (C8). Each case copies the 2.1.332 verdict of a fixture
of reports_dev/mods-gap (p08 ...) or a probe of reports_dev/mods-expert/batch2/final-20261128/probe
(e-*, d-*, d2-*, s-*, t-*, c-*), named in its id. Real files in tmp_path, real scanner calls, no
mocks; nothing is executed and `claude` is never called.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_mod_register_scan import _EVENT_NAMES, _EVENT_NAMESPACES, scan_module_body, scan_types_contract  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402
from validate_plugin import validate_manifest  # noqa: E402

H = "async ($, e, next) => next(e)"
NOT_EVENT = "is not an event"
C5_TAG = "$ is always spelled $.noun.event(...) at the call site"
C6 = "is not declared"
C7 = "a contract exports types and nothing else"
C8 = "Client module"


def reg(body: str, pre: str = "") -> str:
    """A hooks module with a valid `register` export around `body`."""
    return f"{pre}export function register(on) {{\n{body}\n}}\n"


def hook(body: str, params: str = "$, e, next") -> str:
    """A module whose session.start hook runs `body`."""
    return reg(f"  on(\"session.start\", async ({params}) => {{ {body}; return next(e) }})")


def check(root: Path, source: str, name: str = "register.js", extra: dict[str, str] | None = None) -> HookValidationReport:
    """Write the manifest, hooks/hooks.json, the module and its siblings (keys relative to root), validate."""
    files = {".claude-plugin/plugin.json": json.dumps({"name": "demo"}), **(extra or {})}
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "hooks.json").write_text(json.dumps({"modules": [f"./{name}"]}))
    (hooks / name).write_text(source, encoding="utf-8")
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    report = HookValidationReport(hook_path=str(hooks / "hooks.json"))
    return validate_hooks_data({"modules": [f"./{name}"]}, root, report)


def levels(report: HookValidationReport | ValidationReport, needle: str) -> list[str]:
    """Levels of every finding whose message holds `needle`."""
    return [r.level for r in report.results if needle in r.message and r.level != "PASSED"]


# -- C1: the event of on(...) (native: p08, p23 and probes e-*) --------------------------------


@pytest.mark.parametrize(
    ("event", "sentence"),
    [
        pytest.param("tool.cal", "\"tool.cal\" is not an event", id="p08-misspelled"),
        pytest.param("classic.Bogus", "\"classic.Bogus\" is not an event", id="p23-classic"),
        pytest.param("ui.ask", "\"ui.ask\" is not an event", id="e-ui-ask"),
        pytest.param("bogus", "\"bogus\" is not an event", id="e-dotless"),
        pytest.param("!tool.cal", "\"tool.cal\" is not an event", id="e-neg-unknown"),
        pytest.param("*.call", "is not an event name or a pattern", id="e-star-call"),
        pytest.param("!*", "it selects no event", id="e-neg-star"),
        pytest.param("!!tool.call", "\"!\" negates once", id="e-double-neg"),
    ],
)
def test_c1_unknown_event_is_major(tmp_path: Path, event: str, sentence: str) -> None:
    """An event name or pattern native refuses is a MAJOR with the native sentence."""
    report = check(tmp_path, reg(f"  on({json.dumps(event)}, {H})"))
    assert levels(report, sentence) == ["MAJOR"]


@pytest.mark.parametrize(
    "event",
    [
        pytest.param("tool.call", id="p00-known"),
        pytest.param("classic.Stop", id="p24-classic"),
        pytest.param("fs.write", id="p36-api-event"),
        pytest.param("*", id="p38-star"),
        pytest.param("tool.*", id="e-all-globs"),
        pytest.param("!tool.describe", id="e-all-negations"),
        pytest.param("foo.bar", id="e-unknown-head"),
        pytest.param("Tool.call", id="e-case-head"),
    ],
)
def test_c1_accepted_event_passes(tmp_path: Path, event: str) -> None:
    """A name or pattern native accepts, or one under a namespace another plugin may add, gives no C1."""
    report = check(tmp_path, reg(f"  on({json.dumps(event)}, {H})"))
    assert levels(report, NOT_EVENT) == [] and levels(report, "pattern") == []


def test_c1_reads_the_first_parameter_of_register(tmp_path: Path) -> None:
    """Native reads on as the first parameter of register whatever its name (probe e-param-hook)."""
    report = check(tmp_path, f"export function register(hook) {{\n  hook(\"tool.cal\", {H})\n}}\n")
    assert levels(report, "\"tool.cal\" is not an event") == ["MAJOR"]


def test_c1_ignores_an_on_outside_register(tmp_path: Path) -> None:
    """A module-level on is not the register parameter: native passes it (probe e-top-level)."""
    source = f"const on = (k) => k\non(\"tool.cal\")\nexport function register(on) {{\n  on(\"tool.call\", {H})\n}}\n"
    assert levels(check(tmp_path, source), NOT_EVENT) == []


def test_c1_skips_a_shadowed_on(tmp_path: Path) -> None:
    """An on declared again inside register is not the register parameter (native: its own message, e-shadow-inner)."""
    source = reg("  on(\"session.start\", async ($, e, next) => { const on = (k, v) => k; on(\"tool.cal\", 1); return next(e) })")
    assert levels(check(tmp_path, source), NOT_EVENT) == []


def test_c1_event_table_is_the_2_1_332_list() -> None:
    """The event table holds the 171 EventName literals of the 2.1.332 types under 28 namespaces."""
    assert len(_EVENT_NAMES) == 171 and len(_EVENT_NAMESPACES) == 28
    assert {"turn.step", "ui.view", "wasm.compile", "classic.WorktreeRemove", "agent.call"} <= _EVENT_NAMES


# -- C5: $ at the call site (native: p09, p10 and probes d-*) ---------------------------------


@pytest.mark.parametrize(
    ("body", "sentence", "params"),
    [
        pytest.param("const f = $.fs; await f.read(\"x\")", "$.fs is used as a value", "$, e, next", id="p09-alias-noun"),
        pytest.param("await $[\"fs\"].read(\"x\")", "a computed or optional member access on $", "$, e, next", id="p10-computed"),
        pytest.param("await $?.fs.read(\"x\")", "a computed or optional member access on $", "$, e, next", id="d-optional-root"),
        pytest.param("const x = $", "$ itself is bound to a name", "$, e, next", id="d-alias-bare"),
        pytest.param("const r = $.fs.read", "$.fs.read is used as a value", "$, e, next", id="d-method-ref"),
        pytest.param("const p = $.plugin", "$.plugin is used other than", "$, e, next", id="d-plugin-noun"),
        pytest.param("const b = $ === undefined", "used in a BinaryExpression", "$, e, next", id="d-eq-root"),
        pytest.param("const f = api.fs", "$.fs is used as a value", "api, e, next", id="d-other-param-alias"),
    ],
)
def test_c5_dollar_used_as_a_value_is_major(tmp_path: Path, body: str, sentence: str, params: str) -> None:
    """A form of $ native refuses is a MAJOR with the native sentence."""
    report = check(tmp_path, hook(body, params))
    assert levels(report, sentence) == ["MAJOR"]


@pytest.mark.parametrize(
    ("source", "name"),
    [
        pytest.param(hook("await $.fs.read(\"x\")"), "register.js", id="call-site"),
        pytest.param(hook("const r = $.plugin.root; const n = $.plugin.name"), "register.js", id="d-plugin-root"),
        pytest.param("async function h($) { return $.fs.read(\"x\") }\n" + hook("await h($)"), "register.js", id="p31-passed"),
        pytest.param(hook("await ($).fs.read(\"x\")"), "register.js", id="d-paren-root"),
        pytest.param(
            "const $ = { a: 1 }\nconst y = $[\"a\"]\n" + hook("await api.fs.read(\"x\")", "api, e, next"), "register.js", id="d-module-dollar"
        ),
        pytest.param(hook("const v = await ($.state.get as any)(x)", "$: any, e: any, next: any"), "register.ts", id="d2-ts-cast-callee"),
        pytest.param(
            reg("  on(\"ui.render\", async ($, e, next) => <Text>answers $.session.usage with no breakdown</Text>)"),
            "register.tsx",
            id="d2-jsx-text",
        ),
        pytest.param(
            reg("  on(\"ui.render\", async ($, e, next) => <Box><Text>($.fs)</Text></Box>)"),
            "register.tsx",
            id="d2-jsx-text-paren",
        ),
    ],
)
def test_c5_call_site_forms_pass(tmp_path: Path, source: str, name: str) -> None:
    """$.noun.method(...), $.plugin.name/root, a passed $, a module-level $ and prose give no C5."""
    assert levels(check(tmp_path, source, name), C5_TAG) == []


# -- C6: $.state references vs the types contract (native: p11b, p12b, p12d, probes s-*) -------

DTS = "declare module \"claude-code\" {{\n  interface PluginState {{\n    \"demo\": {{ {keys} }}\n  }}\n}}\n"
ATOM = "import { atom, update } from \"claude-code\"\nconst c = atom({ plugin: \"demo\", key: \"count\" }, 0)\n"
NAMED = json.dumps({"name": "demo", "types": "./types/index.d.ts"})


@pytest.mark.parametrize(
    ("extra", "label"),
    [
        pytest.param({}, "the manifest\x27s types contract", id="p11b-no-contract"),
        pytest.param(
            {".claude-plugin/plugin.json": NAMED, "types/index.d.ts": DTS.format(keys="other: number")},
            "the manifest\x27s types contract",
            id="p12d-named-contract",
        ),
        pytest.param({"types/index.d.ts": DTS.format(keys="other: number")}, "the types contract at ./types/index.d.ts", id="s3-default-contract"),
        pytest.param({"types/index.d.ts": DTS.format(keys="[k: string]: number")}, "the types contract at ./types/index.d.ts", id="s-index-sig"),
    ],
)
def test_c6_undeclared_state_key_is_reported(tmp_path: Path, extra: dict[str, str], label: str) -> None:
    """A reference of this plugin whose key the contract does not name is reported with the native label."""
    report = check(tmp_path, ATOM + hook("await update($, c, (v) => v + 1)"), extra=extra)
    assert levels(report, f"demo.count is not declared: {label}") == ["MAJOR"]


@pytest.mark.parametrize(
    ("source", "extra"),
    [
        pytest.param(ATOM + hook("await update($, c, (v) => v + 1)"), {"types/index.d.ts": DTS.format(keys="count: number")}, id="p12b-declared"),
        pytest.param(hook("const v = $.state.get({ plugin: \"someone-else\", key: \"k\" })"), {}, id="s-other-plugin"),
        pytest.param(
            hook("const v = $.state.get({ plugin: \"demo\", key: \"k\" })"),
            {"types/index.d.ts": DTS.format(keys="readonly \"k\"?: number")},
            id="s-readonly-quoted-optional",
        ),
        pytest.param(
            hook("const v = $.state.get({ plugin: \"demo\", key: \"k\" })"),
            {"types/index.d.ts": "declare module \"claude-code\" {\n  interface PluginState { \"demo\": DemoState }\n}\n"},
            id="unreadable-entry-decides-nothing",
        ),
    ],
)
def test_c6_declared_or_undecidable_reference_passes(tmp_path: Path, source: str, extra: dict[str, str]) -> None:
    """A declared key, another plugin reference, or an entry the reader cannot parse gives no C6."""
    assert levels(check(tmp_path, source, extra=extra), C6) == []


# -- C7: top-level exports of the types contract (native: p12, p15, probes t-*) ---------------


def manifest_report(root: Path, contract: str) -> ValidationReport:
    """validate_manifest on a plugin whose manifest names ./types/index.d.ts holding `contract`."""
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "version": "0.1.0", "types": "./types/index.d.ts"}))
    (root / "types").mkdir(exist_ok=True)
    (root / "types" / "index.d.ts").write_text(contract, encoding="utf-8")
    report = ValidationReport()
    validate_manifest(root, report)
    return report


@pytest.mark.parametrize(
    "contract",
    [
        pytest.param("export {}\n", id="p15-export-empty"),
        pytest.param("export declare const x: number\n", id="t-export-declare-const"),
        pytest.param("export * from \"./a\"\n", id="t-export-star"),
        pytest.param("export default interface X { a: number }\n", id="t-export-default"),
    ],
)
def test_c7_non_type_export_is_major(tmp_path: Path, contract: str) -> None:
    """A top-level export not followed by type or interface is a MAJOR on the contract."""
    assert levels(manifest_report(tmp_path, contract), C7) == ["MAJOR"]


@pytest.mark.parametrize(
    "contract",
    [
        pytest.param("export type X = number\nexport interface Y { a: X }\n", id="t-export-type-interface"),
        pytest.param("export\n  type X = number\n", id="t-export-multiline"),
        pytest.param("declare module \"claude-code\" {\n  export const x: number\n}\n", id="nested-export-not-top-level"),
        pytest.param("// export const x = 1\nexport type X = number\n", id="t-comment-export"),
    ],
)
def test_c7_type_exports_pass(tmp_path: Path, contract: str) -> None:
    """Type exports, a split export type, a nested export and a commented export give no C7."""
    assert levels(manifest_report(tmp_path, contract), C7) == []


def test_c7_scan_reports_line_and_word() -> None:
    """The contract scan names the line and the word after each refused export."""
    assert scan_types_contract("export type A = 1\nexport {}\n").exports == ((2, "{"),)


# -- C8: the module of a Client element (native: p56, p57, probes c-*) ------------------------


def ui(module: str) -> str:
    """A ui.render hook drawing a Client element whose module is `module` (source text)."""
    return reg(
        f"  on(\"ui.render\", {{ component: \"Pane\" }}, async ($, e, next) => {{\n"
        f"    const {{ Client }} = $.ui.resolve(e)\n    return Client({{ module: {module}, key: \"c\" }})\n  }})"
    )


@pytest.mark.parametrize(
    ("module", "extra", "sentence"),
    [
        pytest.param("\"./missing-client.js\"", {}, "hooks/missing-client.js: no such file", id="p56-missing"),
        pytest.param("\"./client.js\"", {"hooks/client.ts": "export {}\n"}, "hooks/client.js: no such file", id="c-js-to-ts"),
        pytest.param("\"client.js\"", {"hooks/client.js": "export {}\n"}, "is not relative", id="c-bare"),
        pytest.param("\"./client\"", {"hooks/client.js": "export {}\n"}, "is not named like code", id="c-extless"),
        pytest.param("\"../../outside.js\"", {}, "leaves the plugin\x27s folder", id="c-outside"),
    ],
)
def test_c8_bad_client_module_is_major(tmp_path: Path, module: str, extra: dict[str, str], sentence: str) -> None:
    """A Client module native refuses is a MAJOR with the native sentence."""
    assert levels(check(tmp_path, ui(module), extra=extra), sentence) == ["MAJOR"]


@pytest.mark.parametrize(
    ("source", "name", "extra"),
    [
        pytest.param(ui("\"./client.js\""), "register.js", {"hooks/client.js": "export {}\n"}, id="p57-ok"),
        pytest.param(ui("\"../client.js\""), "register.js", {"client.js": "export {}\n"}, id="c-plugin-root-file"),
        pytest.param(
            reg("  on(\"ui.render\", async ($: any, e: any, next: any) => <Client module=\"./client.js\" key=\"c\" />)"),
            "register.tsx",
            {"hooks/client.js": "export {}\n"},
            id="c-jsx-ok",
        ),
    ],
)
def test_c8_existing_client_module_passes(tmp_path: Path, source: str, name: str, extra: dict[str, str]) -> None:
    """A relative, code-named Client module that exists inside the plugin gives no C8."""
    assert levels(check(tmp_path, source, name, extra), C8) == []


def test_c8_jsx_missing_client_is_collected() -> None:
    """The scan collects the module of a JSX Client element (probe c-jsx-missing)."""
    source = reg("  on(\"ui.render\", async ($: any, e: any, next: any) => <Client module=\"./missing.js\" />)")
    assert [c.spec for c in scan_module_body(source, ".tsx").client_modules] == ["./missing.js"]
