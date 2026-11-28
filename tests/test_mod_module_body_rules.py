"""Module-body rules C2, C3, C4, C9 of the mods gap list (TRDD-29QP66H2, owner decision 2026-11-28).

`claude plugin validate` 2.1.332 reads a hooks module beyond its `register` export. CPV mirrors
four of those native errors offline: a telemetry hook with no collector (C2), a `turn.step` or
`process.spawn` hook that is not an async generator (C3), an import outside the allowed set (C4),
and nesting deeper than 2000 scopes (C9). Every rule has native-error cases and native-pass
controls, each docstring names the fixture id (p06, p14, ...) of reports_dev/mods-gap or the probe
of reports_dev/5290/probe whose 2.1.332 verdict it copies. Real files in tmp_path, no mocks; the
module code is never executed, and `claude` is never called.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402

H = "async ($, e, next) => next(e)"
GEN = "async function* ($, e, next) { yield* next(e) }"

C2 = 'stands on the telemetry stream "anthropic"'
C3 = "is not an async generator"
C3_INFO = "could not statically decide whether the hook"
C4S = 'a hooks module imports its own files by relative path and "claude-code", nothing else'
C4D = "a dynamic import()"
C4O = "is outside the plugin's folder"
C4M = "no such file"
C4N = "is not named like code"
C9 = "code nested too deep to scan"


def reg(body: str, pre: str = "") -> str:
    """A hooks module with a valid `register` export around `body`."""
    return f"{pre}export function register(on) {{\n{body}\n}}\n"


def check(
    root: Path, source: str, name: str = "register.js", extra: dict[str, str] | None = None
) -> HookValidationReport:
    """Write hooks/hooks.json, the module and its siblings (keys relative to the plugin root), validate."""
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "hooks.json").write_text(json.dumps({"modules": [f"./{name}"]}))
    (hooks / name).write_text(source, encoding="utf-8")
    for rel, body in (extra or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    report = HookValidationReport(hook_path=str(hooks / "hooks.json"))
    return validate_hooks_data({"modules": [f"./{name}"]}, root, report)


def hits(report: HookValidationReport, needle: str) -> list[tuple[str, str]]:
    """(level, message) of every finding whose message holds `needle`."""
    return [(r.level, r.message) for r in report.results if needle in r.message and r.level != "PASSED"]


def levels(report: HookValidationReport, needle: str) -> list[str]:
    return [level for level, _ in hits(report, needle)]


# -- C2: a telemetry hook must name the collector (native: p06 error, p07 pass) ---------------


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(f'on("telemetry.log", {H})', id="p06-no-matcher"),
        pytest.param(f'on("telemetry.mark", {H})', id="mark-no-matcher"),
        pytest.param(f"on(\x27telemetry.log\x27, {H})", id="single-quote"),
        pytest.param(f'on("telemetry.log", {{}}, {H})', id="empty-matcher"),
        pytest.param(f'on("telemetry.log", {{ x: 1 }}, {H})', id="matcher-without-to"),
        pytest.param(f'on("telemetry.log", {{ to: "anthropic" }}, {H})', id="to-anthropic"),
        pytest.param(f'on("telemetry.log", {{ "to": "anthropic" }}, {H})', id="quoted-key-anthropic"),
        pytest.param(f'on("telemetry.log", {{ to: ["anthropic", "collector"] }}, {H})', id="list-with-anthropic"),
        pytest.param(f'const inner = () => {{ on("telemetry.log", {H}) }}\n  inner()', id="nested-in-arrow"),
        pytest.param(
            'function h($, e, next) { return next(e) }\n  on("telemetry.log", h)', id="identifier-hook-no-matcher"
        ),
    ],
)
def test_c2_telemetry_without_collector(tmp_path, call):
    """C2 (p06 and probes): a telemetry hook that stands on the anthropic stream is a finding."""
    assert levels(check(tmp_path, reg(f"  {call}")), C2) == ["MAJOR"]


def test_c2_typescript_module(tmp_path):
    """C2 (probe t-ts): the rule also reads a .ts module."""
    assert levels(check(tmp_path, reg(f'  on("telemetry.log", {H})'), "register.ts"), C2) == ["MAJOR"]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(f'on("telemetry.log", {{ to: "collector" }}, {H})', id="p07-collector"),
        pytest.param(f'on("telemetry.mark", {{ to: "collector" }}, {H})', id="mark-collector"),
        pytest.param(f'on("telemetry.log", {{ to: ["collector"] }}, {H})', id="list-collector"),
        pytest.param(f'on("telemetry.log", {{ to: [] }}, {H})', id="empty-list"),
        pytest.param(f'on("telemetry.log", {{ "to": "collector" }}, {H})', id="quoted-key"),
        pytest.param(f'on("telemetry.log", {{ to: "collector", x: 1 }}, {H})', id="extra-key"),
        pytest.param(f'on("tool.call", {H})', id="other-event-no-matcher"),
        pytest.param(f'on("*", {H})', id="wildcard-event"),
        pytest.param(f'emitter.on("telemetry.log", {H})', id="method-call-is-not-a-registration"),
        pytest.param(f'on("telemetry.log", {{ to: "other" }}, {H})', id="other-destination-is-another-native-message"),
        pytest.param(
            f'const m = {{ to: "collector" }}\n  on("telemetry.log", m, {H})',
            id="matcher-variable-is-another-native-message",
        ),
        pytest.param(
            f'const m = {{ to: "collector" }}\n  on("telemetry.log", {{ ...m }}, {H})',
            id="spread-is-another-native-message",
        ),
        pytest.param(
            f'const to = "collector"\n  on("telemetry.log", {{ to }}, {H})', id="shorthand-is-another-native-message"
        ),
        pytest.param(f'const ev = "telemetry.log"\n  on(ev, {H})', id="event-variable-is-another-native-message"),
    ],
)
def test_c2_controls_no_finding(tmp_path, call):
    """C2 controls (p07 and probes): forms native accepts, or rejects with another message, give no C2 finding."""
    assert hits(check(tmp_path, reg(f"  {call}")), C2) == []


# -- C3: turn.step and process.spawn hooks are async generators (native: p14, p34) -----------


@pytest.mark.parametrize("event", ["turn.step", "process.spawn"])
@pytest.mark.parametrize(
    "hook",
    [
        pytest.param(H, id="async-arrow"),
        pytest.param("($, e, next) => next(e)", id="sync-arrow"),
        pytest.param("async e => e", id="arrow-without-parens"),
        pytest.param("async function ($, e, next) { return next(e) }", id="async-function"),
        pytest.param("function ($, e, next) { return next(e) }", id="plain-function"),
        pytest.param("function* ($, e, next) { yield 1 }", id="sync-generator"),
    ],
)
def test_c3_function_literal_not_async_generator(tmp_path, event, hook):
    """C3 (p14 turn.step, p34 process.spawn and probes): a function literal that is not `async function*` is a finding."""
    assert levels(check(tmp_path, reg(f'  on("{event}", {hook})')), C3) == ["MAJOR"]


def test_c3_matcher_then_non_generator(tmp_path):
    """C3 (probe g-matcher-nongen): the hook is the LAST argument, after a matcher."""
    assert levels(check(tmp_path, reg(f'  on("turn.step", {{}}, {H})')), C3) == ["MAJOR"]


def test_c3_typed_arrow_in_typescript(tmp_path):
    """C3 (probe g2-ts-arrow-typed): a TypeScript arrow with a return type is a finding."""
    hook = "async ($: any, e: any, next: any): Promise<any> => next(e)"
    assert levels(check(tmp_path, reg(f'  on("turn.step", {hook})'), "register.ts"), C3) == ["MAJOR"]


@pytest.mark.parametrize(
    "decl,use",
    [
        pytest.param("async function g($, e, next) { return next(e) }", "g", id="async-function-declaration"),
        pytest.param("function* g($, e, next) { yield 1 }", "g", id="sync-generator-declaration"),
        pytest.param("const g = async ($, e, next) => next(e)", "g", id="const-arrow"),
        pytest.param("const g = async function ($, e, next) { return next(e) }", "g", id="const-function-expression"),
    ],
)
def test_c3_identifier_resolved_to_non_generator(tmp_path, decl, use):
    """C3 (probes g-ident-nongen, g-ident-const-arrow, g2-sync-gen-decl, g2-const-fn-expr): native resolves the name."""
    assert levels(check(tmp_path, reg(f'  on("turn.step", {use})', pre=decl + "\n")), C3) == ["MAJOR"]


@pytest.mark.parametrize(
    "hook",
    [
        pytest.param(GEN, id="anonymous"),
        pytest.param("async function* g($, e, next) { yield* next(e) }", id="named"),
        pytest.param("(async function* ($, e, next) { yield* next(e) })", id="parenthesised-is-not-decided"),
        pytest.param("o.h", id="member-is-another-native-message"),
        pytest.param("mk()", id="call-is-another-native-message"),
    ],
)
def test_c3_controls_no_finding(tmp_path, hook):
    """C3 controls (probes g-asyncgen-ok, g-asyncgen-named, g2-turn-step-paren-gen, g-member, g-call): no finding."""
    report = check(tmp_path, reg(f'  on("turn.step", {hook})'))
    assert hits(report, C3) == [] and levels(report, C3_INFO) == []


def test_c3_control_matcher_then_generator(tmp_path):
    """C3 control (probe g-matcher-gen, g-spawn-gen): a generator after a matcher, and on process.spawn."""
    body = f'  on("turn.step", {{}}, {GEN})\n  on("process.spawn", {GEN})'
    assert hits(check(tmp_path, reg(body)), C3) == []


def test_c3_control_typescript_generator(tmp_path):
    """C3 control (probe g-ts-gen): a typed async generator in .ts."""
    hook = "async function* ($: any, e: any, next: any) { yield* next(e) }"
    assert hits(check(tmp_path, reg(f'  on("turn.step", {hook})'), "register.ts"), C3) == []


@pytest.mark.parametrize(
    "decl",
    [
        pytest.param("async function* g($, e, next) { yield* next(e) }\n", id="declaration-before"),
        pytest.param("export async function* g($, e, next) { yield* next(e) }\n", id="exported-declaration"),
        pytest.param("const g = async function* ($, e, next) { yield* next(e) }\n", id="const-generator-expression"),
    ],
)
def test_c3_control_identifier_resolved_to_generator(tmp_path, decl):
    """C3 control (probes g-ident-gen, g2-exported-gen, g2-const-asyncgen): the name resolves to an async generator."""
    report = check(tmp_path, reg('  on("turn.step", g)', pre=decl))
    assert hits(report, C3) == [] and levels(report, C3_INFO) == []


def test_c3_control_generator_declared_after_use(tmp_path):
    """C3 control (probe g2-decl-after): a top-level declaration is hoisted, so use before declaration resolves."""
    source = reg('  on("turn.step", g)') + "async function* g($, e, next) { yield* next(e) }\n"
    assert hits(check(tmp_path, source), C3) == []


def test_c3_typescript_annotated_const(tmp_path):
    """C3 (found on a natively accepted catalogue mod): `const g: Hook<"turn.step"> = async function* ...` resolves to a generator; an annotated arrow is a finding."""
    gen = reg(
        '  on("turn.step", g)', pre='const g: Hook<"turn.step"> = async function* ($, e, next) { yield* next(e) }\n'
    )
    report = check(tmp_path, gen, "register.ts")
    assert hits(report, C3) == [] and levels(report, C3_INFO) == []
    arrow = reg('  on("turn.step", g)', pre='const g: Hook<"turn.step"> = async ($, e, next) => next(e)\n')
    assert levels(check(tmp_path, arrow, "register.ts"), C3) == ["MAJOR"]


def test_c3_control_other_events_need_no_generator(tmp_path):
    """C3 control (probe g-other-event-nongen-ok): `tool.call` takes a plain async function."""
    assert hits(check(tmp_path, reg(f'  on("tool.call", {H})')), C3) == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(reg('  on("turn.step", imported)', pre='import { imported } from "./h.js"\n'), id="imported"),
        pytest.param(reg('  on("turn.step", g)', pre="let g = async function* () {}\n"), id="let-binding"),
        pytest.param(
            reg('  async function* g($, e, next) { yield* next(e) }\n  on("turn.step", g)'),
            id="declared-inside-register",
        ),
        pytest.param(reg('  on("turn.step", missing)'), id="undeclared"),
    ],
)
def test_c3_identifier_not_resolvable_is_info(tmp_path, source):
    """C3 (probes g2-import-ident, g2-let-asyncgen, g2-local-decl, g-ident-unresolved): the scanner cannot decide, so INFO, no MAJOR."""
    report = check(tmp_path, source, extra={"hooks/h.js": "export async function* imported() {}\n"})
    assert levels(report, C3_INFO) == ["INFO"] and hits(report, C3) == []


# -- C4: imports of a hooks module (native: p21 p45 p47 p48 p49 p50 errors, p22 pass) ----------


@pytest.mark.parametrize(
    "stmt",
    [
        pytest.param('import lodash from "lodash"', id="p21-npm"),
        pytest.param('import fs from "node:fs"', id="p45-node-prefix"),
        pytest.param('import fs from "fs"', id="node-bare"),
        pytest.param('import t from "bun:test"', id="bun-prefix"),
        pytest.param('import "lodash"', id="side-effect-npm"),
        pytest.param('import x from "@scope/pkg"', id="scoped-package"),
        pytest.param('import x from "https://example.com/x.js"', id="https-url"),
        pytest.param('import x from "file:///tmp/x.js"', id="file-url"),
        pytest.param('import x from "#alias"', id="hash-alias"),
        pytest.param('import x from "/tmp/x.js"', id="absolute-path"),
        pytest.param('import x from "claude-code/extra"', id="claude-code-subpath"),
        pytest.param('import x from ""', id="empty-specifier"),
        pytest.param('export { a } from "lodash"', id="export-from-npm"),
        pytest.param('export * from "lodash"', id="export-star-npm"),
        pytest.param('import {\n  a,\n  b,\n} from "lodash"', id="multi-line"),
        pytest.param('import * as l from "lodash"', id="namespace"),
    ],
)
def test_c4_specifier_not_allowed(tmp_path, stmt):
    """C4 (p21, p45 and probes): only relative files and "claude-code" may be imported."""
    assert levels(check(tmp_path, reg(f'  on("tool.call", {H})', pre=stmt + "\n")), C4S) == ["MAJOR"]


@pytest.mark.parametrize(
    "stmt",
    [
        pytest.param('import { Foo } from "lodash"', id="named-unused"),
        pytest.param('import { a, type B } from "lodash"', id="mixed-inline-type"),
        pytest.param('export { A } from "lodash"', id="export-from"),
    ],
)
def test_c4_typescript_value_imports_are_not_erased(tmp_path, stmt):
    """C4 (probes j-ts-unused-named, j-ts-mixed-inline, j-ts-export-from): TypeScript keeps an import that is not marked as a type."""
    assert levels(check(tmp_path, reg(f'  on("tool.call", {H})', pre=stmt + "\n"), "register.ts"), C4S) == ["MAJOR"]


def test_c4_dynamic_import_typescript_value_position(tmp_path):
    """C4 (p47 in a .ts module): `await import(...)` is still a finding, and so is a bare statement."""
    for call in ('await import("./x.js")', 'return import("./x.js")'):
        body = f'  on("session.start", async ($, e, next) => {{ {call}; return next(e) }})'
        assert levels(check(tmp_path, reg(body), "register.ts"), C4D) == ["MAJOR"], call


def test_c4_control_typescript_import_type_is_erased(tmp_path):
    """C4 control: `$: import("claude-code").EngineInterface` is a TS type, erased and accepted (2 natively accepted catalogue mods)."""
    pre = 'async function servers($: import("claude-code").EngineInterface): Promise<void> {}\nconst bridge = async ($: import("claude-code").EngineInterface) => 1\ntype T = import("claude-code").EngineInterface\n'
    assert hits(check(tmp_path, reg(f'  on("tool.call", {H})', pre=pre), "register.ts"), C4D) == []
    assert hits(check(tmp_path, reg(f'  on("tool.call", {H})', pre=pre), "register.tsx"), C4D) == []


def test_c4_dynamic_import(tmp_path):
    """C4 (p47): a dynamic import() is a finding, also with a variable or "claude-code" as the target."""
    for target in ('"./x.js"', "p", '"claude-code"'):
        body = f'  on("session.start", async ($, e, next) => {{ await import({target}); return next(e) }})'
        assert levels(check(tmp_path, reg(body)), C4D) == ["MAJOR"], target


@pytest.mark.parametrize(
    "stmt",
    [
        pytest.param('import { something } from "claude-code"', id="claude-code"),
        pytest.param('import * as cc from "claude-code"', id="claude-code-namespace"),
        pytest.param('export * from "claude-code"', id="export-star-claude-code"),
        pytest.param('const l = require("lodash")', id="require-call"),
        pytest.param("const u = import.meta.url", id="import-meta"),
        pytest.param('// import x from "lodash"', id="in-comment"),
        pytest.param('const s = "import y from \\"lodash\\""', id="in-string"),
        pytest.param('const s = `import y from "lodash"`', id="in-template"),
        pytest.param('obj.import("x")', id="method-named-import"),
    ],
)
def test_c4_controls_no_finding(tmp_path, stmt):
    """C4 controls (probes i-claude-code, i-require-call, i-meta-url, i-in-string-comment): native accepts these."""
    report = check(tmp_path, reg(f'  on("tool.call", {H})', pre=stmt + "\n"))
    assert hits(report, C4S) == [] and hits(report, C4D) == []


@pytest.mark.parametrize(
    "stmt",
    [
        pytest.param('import type { Register } from "claude-code"', id="type-claude-code"),
        pytest.param('import type { Foo } from "lodash"', id="type-npm"),
        pytest.param('import { type Foo } from "lodash"', id="all-inline-type-npm"),
        pytest.param('import { type Foo, type Bar } from "lodash"', id="all-inline-type-two"),
        pytest.param('import type * as l from "lodash"', id="type-namespace"),
        pytest.param('export type { A } from "lodash"', id="export-type-from"),
        pytest.param('import fs = require("fs")', id="import-equals-require"),
        pytest.param('import type { X } from "./nope"', id="type-of-a-missing-file"),
        pytest.param('import type x from "./data.json"', id="type-of-json"),
    ],
)
def test_c4_typescript_erased_imports_no_finding(tmp_path, stmt):
    """C4 controls (probes i-npm-type-ts, i-npm-typeinline-ts, j-ts-type-ns, j-ts-export-type-from, i-require-ts): erased by the TS compile."""
    report = check(tmp_path, reg(f'  on("tool.call", {H})', pre=stmt + "\n"), "register.ts")
    assert [hits(report, n) for n in (C4S, C4O, C4M, C4N)] == [[], [], [], []]


def test_c4_p22_relative_import_resolves(tmp_path):
    """C4 control (p22): a relative import of an existing sibling is accepted."""
    report = check(
        tmp_path,
        reg(f'  on("tool.call", {H})', pre='import { x } from "./helper.js"\n'),
        extra={"hooks/helper.js": "export const x = 1\n"},
    )
    assert [hits(report, n) for n in (C4S, C4O, C4M, C4N)] == [[], [], [], []]


@pytest.mark.parametrize(
    "spec,name,files",
    [
        pytest.param("./helper", "register.js", {"hooks/helper.js": "x"}, id="extensionless"),
        pytest.param("./helper.js", "register.js", {"hooks/helper.ts": "x"}, id="js-specifier-ts-file"),
        pytest.param("./helper.js", "register.ts", {"hooks/helper.tsx": "x"}, id="js-specifier-tsx-file"),
        pytest.param("./helper.mjs", "register.ts", {"hooks/helper.mts": "x"}, id="mjs-specifier-mts-file"),
        pytest.param("./sub", "register.js", {"hooks/sub/index.js": "x"}, id="directory-index"),
        pytest.param("./sub/", "register.js", {"hooks/sub/index.js": "x"}, id="directory-trailing-slash"),
        pytest.param("./sub.js", "register.js", {"hooks/sub.js/index.js": "x"}, id="directory-named-like-a-file"),
        pytest.param("./", "register.js", {"hooks/index.js": "x"}, id="dot-slash-index"),
        pytest.param("../lib/helper.js", "register.js", {"lib/helper.js": "x"}, id="parent-inside-plugin"),
        pytest.param("./sub/../helper.js", "register.js", {"hooks/helper.js": "x"}, id="dotdot-roundtrip"),
        pytest.param("../helper2.js", "register.js", {"helper2.js": "x"}, id="plugin-root-file"),
        pytest.param("../.hidden/h.js", "register.js", {".hidden/h.js": "x"}, id="dot-directory"),
    ],
)
def test_c4_controls_relative_resolution(tmp_path, spec, name, files):
    """C4 controls (probes i-rel-extless, i-ts-js-to-ts, i-rel-dir-index, i-rel-dot-slash, i-rel-parent-inside): native resolves these."""
    report = check(tmp_path, reg(f'  on("tool.call", {H})', pre=f'import {{ x }} from "{spec}"\n'), name, files)
    assert [hits(report, n) for n in (C4O, C4M, C4N)] == [[], [], []]


def test_c4_outside_plugin(tmp_path):
    """C4 (p48): a relative import that leaves the plugin folder is a finding, whether or not the file exists."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (tmp_path / "outside.js").write_text("export const x = 1\n")
    for spec in ("../../outside.js", "../../../nothing.js"):
        source = reg(f'  on("tool.call", {H})', pre=f'import {{ x }} from "{spec}"\n')
        assert levels(check(plugin, source), C4O) == ["MAJOR"], spec


@pytest.mark.parametrize(
    "spec,files",
    [
        pytest.param("./nope.js", {}, id="p49-missing"),
        pytest.param("./nope", {}, id="missing-extensionless"),
        pytest.param(".", {}, id="dot-without-index"),
        pytest.param("..", {}, id="dotdot-without-index"),
        pytest.param("./sub", {"hooks/sub/readme.txt": "x"}, id="directory-without-index"),
        pytest.param("./a.md", {}, id="missing-non-code-file"),
        pytest.param("./data", {"hooks/data.json": "{}"}, id="extensionless-never-finds-json"),
    ],
)
def test_c4_missing_file(tmp_path, spec, files):
    """C4 (p49 and probes j-rel-missing-extless, j-rel-dot, j-rel-dir-noindex): native says "no such file"."""
    source = reg(f'  on("tool.call", {H})', pre=f'import {{ x }} from "{spec}"\n')
    assert levels(check(tmp_path, source, extra=files), C4M) == ["WARNING"]


@pytest.mark.parametrize(
    "spec,files",
    [
        pytest.param("./data.json", {"hooks/data.json": "{}"}, id="p50-json"),
        pytest.param("./a.md", {"hooks/a.md": "x"}, id="markdown"),
        pytest.param("./a.css", {"hooks/a.css": "x"}, id="css"),
    ],
)
def test_c4_existing_file_not_named_like_code(tmp_path, spec, files):
    """C4 (p50 and probes i-rel-md, i-rel-css): the file exists but its extension is not .ts .tsx .jsx .js .mjs .cjs .mts .cts."""
    source = reg(f'  on("tool.call", {H})', pre=f'import x from "{spec}"\n')
    assert levels(check(tmp_path, source, extra=files), C4N) == ["WARNING"]


# -- C9: nesting deeper than 2000 scopes (native: p44 error) ------------------------------------

PRE = '  on("tool.call", async ($, e, next) => next(e));\n'


def nested(opener: str, closer: str, n: int) -> str:
    return reg(PRE + "  " + opener * n + closer * n)


@pytest.mark.parametrize(
    "opener,closer",
    [
        pytest.param("{", "}", id="p44-blocks"),
        pytest.param("(()=>{", "})()", id="arrow-bodies"),
        pytest.param("while(0){", "}", id="while-bodies"),
        pytest.param("if(1){", "}", id="if-bodies"),
    ],
)
def test_c9_boundary(tmp_path, opener, closer):
    """C9 (p44 and probes d-brace, e-arrowbody, e-while, e-if): register body plus 1999 inner scopes passes, plus 2000 errors."""
    assert hits(check(tmp_path, nested(opener, closer, 1999)), C9) == []
    assert levels(check(tmp_path, nested(opener, closer, 2000)), C9) == ["MAJOR"]


def test_c9_p44_two_thousand_and_one_blocks(tmp_path):
    """C9 (p44): 2001 nested blocks inside `register` is a finding."""
    assert levels(check(tmp_path, nested("{", "}", 2001)), C9) == ["MAJOR"]


def test_c9_typescript_module(tmp_path):
    """C9 (probes e-brace-ts-2000, e-brace-tsx-2000): the limit applies to .ts and .tsx modules too."""
    assert levels(check(tmp_path, nested("{", "}", 2000), "register.ts"), C9) == ["MAJOR"]
    assert hits(check(tmp_path, nested("{", "}", 1999), "register.tsx"), C9) == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param("  const v = " + "(" * 2500 + "1" + ")" * 2500, id="e-paren-2500"),
        pytest.param("  const v = " + "[" * 2500 + "1" + "]" * 2500, id="e-array-2500"),
        pytest.param("  const o = " + "{a:" * 2500 + "1" + "}" * 2500, id="d-objlit-2500"),
        pytest.param('  const s = "' + "{" * 2100 + '"', id="e-brace-in-string"),
        pytest.param("  // " + "{" * 2100, id="e-brace-in-comment"),
    ],
)
def test_c9_controls_not_scopes(tmp_path, body):
    """C9 controls (probes e-paren, e-array, d-objlit, e-brace-in-string, e-brace-in-comment): native accepts these, so no finding."""
    assert hits(check(tmp_path, reg(PRE + body)), C9) == []
