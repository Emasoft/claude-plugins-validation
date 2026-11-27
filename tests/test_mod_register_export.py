"""TRDD-775XK7HE: a Claude Code mod's hooks module must export `register` by name.

Each rejection rule R1-R7 has a positive case and a matched accepted control
that differs in one variable. Expectations mirror `claude plugin validate`
2.1.332 on the evidence fixtures named in each docstring (fixture ids of the
TRDD-775XK7HE evidence matrix). Real files in tmp_path, no mocks; the module
code is never executed.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_hook import HookValidationReport, validate_hooks_data  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "validate_hook.py"
UNDECIDED = "could not statically decide the `register` export"
PASSED = "exports `register`"
R1 = "no export named `register`"
R2 = "`register` is re-exported from another file"
R3 = "`register` is not exported here by name"
R4 = "`register` is exported as something other than a const function"
R5 = "`register` is exported as a class"
R6 = "which is not a function or const function declared at the top of this file"
R7 = "which is not declared in this file"


def _write(root: Path, modules: dict[str, str | bytes], siblings: dict[str, str] | None = None) -> Path:
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "hooks.json").write_text(json.dumps({"modules": [f"./{name}" for name in modules]}))
    for name, body in {**modules, **(siblings or {})}.items():
        if isinstance(body, bytes):
            (hooks / name).write_bytes(body)
        else:
            (hooks / name).write_text(body, encoding="utf-8")
    return hooks / "hooks.json"


def _check(root: Path, body: str | bytes, name: str = "register.js", siblings: dict[str, str] | None = None):
    hooks_json = _write(root, {name: body}, siblings)
    report = HookValidationReport(hook_path=str(hooks_json))
    return validate_hooks_data({"modules": [f"./{name}"]}, root, report)


def _msgs(report, level: str) -> list[str]:
    return [r.message for r in report.results if r.level == level and r.message.startswith("mods hooks module")]


def _rejected(report, needle: str) -> bool:
    majors = _msgs(report, "MAJOR")
    return len(majors) == 1 and needle in majors[0]


def _accepted(report) -> bool:
    return not _msgs(report, "MAJOR") and any(PASSED in m for m in _msgs(report, "PASSED"))


# -- R1: no export named register -------------------------------------------


def test_r1_marker_only(tmp_path):
    """R1 (C09): a module exporting only `marker` is refused."""
    assert _rejected(_check(tmp_path, "export const marker = 1\n"), R1)


def test_r1_control_direct_function(tmp_path):
    """R1 control (C01): `export function register` is accepted."""
    assert _accepted(_check(tmp_path, "export function register(on) {}\n"))


def test_r1_commented_export_decoy(tmp_path):
    """R1 (X01): an export inside a line comment does not count."""
    body = "// export function register(on) {}\nexport const marker = 1\n"
    assert _rejected(_check(tmp_path, body), R1)


def test_r1_control_commonjs_suffix_with_esm_export(tmp_path):
    """R1 control (Y15): a .cjs module with an ES `export function register` is accepted."""
    assert _accepted(_check(tmp_path, "export function register(on) {}\n", name="register.cjs"))


def test_r1_commonjs_assignment(tmp_path):
    """R1 (C10 .cjs): `module.exports.register = ...` is not an ES export."""
    body = "module.exports.register = function (on) {}\n"
    assert _rejected(_check(tmp_path, body, name="register.cjs"), R1)


# -- R2: re-export from another file -----------------------------------------


def test_r2_named_reexport(tmp_path):
    """R2 (C05a): `export { register } from` is refused even when the target has it."""
    siblings = {"other.js": "export function register(on) {}\n"}
    assert _rejected(_check(tmp_path, 'export { register } from "./other.js"\n', siblings=siblings), R2)


def test_r2_control_local_export_list(tmp_path):
    """R2 control (X05): the same export list without `from` is accepted."""
    assert _accepted(_check(tmp_path, "function register(on) {}\nexport { register }\n"))


# -- R3: export * -------------------------------------------------------------


def test_r3_star_reexport(tmp_path):
    """R3 (C06a): `export *` is not followed, even when the target exports register."""
    siblings = {"other.js": "export function register(on) {}\n"}
    assert _rejected(_check(tmp_path, 'export * from "./other.js"\n', siblings=siblings), R3)


def test_r3_control_direct_plus_star(tmp_path):
    """R3 control (Z12): a direct register export next to `export *` is accepted."""
    body = 'export function register(on) {}\nexport * from "./other.js"\n'
    assert _accepted(_check(tmp_path, body, siblings={"other.js": "export const marker = 1\n"}))


# -- R4: not a const function --------------------------------------------------


def test_r4_const_number(tmp_path):
    """R4 (C08): `export const register = 1` is refused."""
    assert _rejected(_check(tmp_path, "export const register = 1\n"), R4)


def test_r4_control_const_arrow(tmp_path):
    """R4 control (C02): `export const register = (on) => {}` is accepted."""
    assert _accepted(_check(tmp_path, "export const register = (on) => {}\n"))


def test_r4_let_function_expression(tmp_path):
    """R4 (X04): `export let register = function` is refused (let, not const)."""
    assert _rejected(_check(tmp_path, "export let register = function (on) {}\n"), R4)


def test_r4_control_const_function_expression(tmp_path):
    """R4 control (Y01): the same initializer under `const` is accepted."""
    assert _accepted(_check(tmp_path, "export const register = function (on) {}\n"))


def test_r4_ts_enum(tmp_path):
    """R4 (Z22): a TS `export enum register` compiles to a var and is refused."""
    assert _rejected(_check(tmp_path, "export enum register { A }\n", name="register.ts"), R4)


def test_r4_control_ts_paren_as_cast(tmp_path):
    """R4 control (Z18): a parenthesised arrow with an `as` cast is accepted in .ts."""
    body = "export const register = ((on: unknown) => {}) as (on: unknown) => void\n"
    assert _accepted(_check(tmp_path, body, name="register.ts"))


# -- R5: class ----------------------------------------------------------------


def test_r5_class(tmp_path):
    """R5 (X06): `export class register` is refused."""
    assert _rejected(_check(tmp_path, "export class register {}\n"), R5)


def test_r5_control_async_function(tmp_path):
    """R5 control (X03): `export async function register` is accepted."""
    assert _accepted(_check(tmp_path, "export async function register(on) {}\n"))


# -- R6: export-list alias of a non-function binding ---------------------------


def test_r6_alias_to_let(tmp_path):
    """R6 (Y04): an alias of a `let` arrow is refused."""
    assert _rejected(_check(tmp_path, "let r = (on) => {}\nexport { r as register }\n"), R6)


def test_r6_control_alias_to_const(tmp_path):
    """R6 control (Y03): an alias of a `const` arrow is accepted."""
    assert _accepted(_check(tmp_path, "const r = (on) => {}\nexport { r as register }\n"))


def test_r6_alias_to_import(tmp_path):
    """R6 (Y08): re-exporting an imported binding through a local list is refused."""
    body = 'import { register } from "./other.js"\nexport { register }\n'
    siblings = {"other.js": "export function register(on) {}\n"}
    assert _rejected(_check(tmp_path, body, siblings=siblings), R6)


# -- R7: export list names an undeclared local ---------------------------------


def test_r7_undeclared_alias(tmp_path):
    """R7 (Z16): `export { r as register }` with no `r` anywhere does not parse."""
    assert _rejected(_check(tmp_path, "export { r as register }\n"), R7)


def test_r7_control_declared_alias(tmp_path):
    """R7 control (C03): the same export list with `function r` declared is accepted."""
    assert _accepted(_check(tmp_path, "function r(on) {}\nexport { r as register }\n"))


# -- TS erasure ---------------------------------------------------------------


def test_ts_type_only_export_is_r1(tmp_path):
    """TS erasure (C07): `export type register = ...` exports nothing at runtime."""
    body = "export type register = (on: unknown) => void\n"
    assert _rejected(_check(tmp_path, body, name="register.ts"), R1)


def test_ts_control_typed_const(tmp_path):
    """TS erasure control (X15): an annotated const arrow is accepted."""
    body = 'import type { Register } from "claude-code"\nexport const register: Register = (on) => {}\n'
    assert _accepted(_check(tmp_path, body, name="register.ts"))


# -- scanner robustness (no false MAJOR) ---------------------------------------


def test_regex_literal_with_quotes_before_export(tmp_path):
    """A regex literal holding quotes and a slash does not hide the export."""
    body = "const re = /['\"\\\\/]/g\nexport function register(on) {}\n"
    assert _accepted(_check(tmp_path, body))


def test_template_with_brace_string_and_nested_template(tmp_path):
    """A template with `${ "}" }` and a nested template does not hide the export."""
    body = 'const t = `a ${ "}" } b ${ `inner ${1}` } c`\nexport function register(on) {}\n'
    assert _accepted(_check(tmp_path, body))


def test_tsx_jsx_text_apostrophe(tmp_path):
    """W-tsx: an apostrophe in JSX text is lexed leniently and the module is accepted."""
    body = (
        "export function register(on) {\n"
        "  const el = <Text>it is the mod&apos;s pane, don't worry</Text>\n"
        "  return el\n}\n"
    )
    assert _accepted(_check(tmp_path, body, name="register.tsx"))


def test_tsx_jsx_guard_downgrades_absence_to_info(tmp_path):
    """JSX guard: a `/*` in JSX text that swallows a real export gives INFO, not MAJOR."""
    body = (
        "const note = <Text>see /* the docs</Text>\n"
        "export function register(on) {}\n"
        "const tail = <Text>end */</Text>\n"
    )
    r = _check(tmp_path, body, name="register.tsx")
    assert not _msgs(r, "MAJOR")
    assert any(UNDECIDED in m for m in _msgs(r, "INFO"))


# -- gates: when the scan runs and when it stays undecided ---------------------


def test_non_utf8_module_is_info(tmp_path):
    """A module that is not valid UTF-8 is INFO, never a blocking finding."""
    r = _check(tmp_path, b"export function register(on) {}\n// \xff\n")
    assert not _msgs(r, "MAJOR")
    assert any(UNDECIDED in m for m in _msgs(r, "INFO"))


def test_oversize_module_is_info(tmp_path):
    """A module above 2 MiB is INFO, not scanned."""
    r = _check(tmp_path, b"export const marker = 1\n" + b" " * (2 * 1024 * 1024))
    assert not _msgs(r, "MAJOR")
    assert any("larger than 2 MiB" in m for m in _msgs(r, "INFO"))


def test_missing_module_has_no_register_finding(tmp_path):
    """A missing module draws only the not-found MAJOR, no register finding."""
    (tmp_path / "hooks").mkdir()
    report = HookValidationReport(hook_path=str(tmp_path / "hooks" / "hooks.json"))
    r = validate_hooks_data({"modules": ["./register.js"]}, tmp_path, report)
    majors = [x.message for x in r.results if x.level == "MAJOR"]
    assert len(majors) == 1 and "mods hooks module not found" in majors[0]



def test_unlisted_extension_module_is_not_scanned(tmp_path):
    """An existing in-root `.txt` module draws only the extension MAJOR, no register finding."""
    r = _check(tmp_path, "export const marker = 1\n", name="register.txt")
    majors = [x.message for x in r.results if x.level == "MAJOR"]
    assert len(majors) == 1 and "not one of the documented" in majors[0]
    assert not _msgs(r, "PASSED") and not _msgs(r, "INFO")


def test_symlink_outside_root_is_not_scanned(tmp_path):
    """A module symlinked outside the plugin root draws only the outside-root MAJOR."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.js").write_text("export const marker = 1\n")
    root = tmp_path / "plugin"
    root.mkdir()
    hooks_json = _write(root, {})
    (root / "hooks" / "register.js").symlink_to(outside / "evil.js")
    report = HookValidationReport(hook_path=str(hooks_json))
    r = validate_hooks_data({"modules": ["./register.js"]}, root, report)
    majors = [x.message for x in r.results if x.level == "MAJOR"]
    assert len(majors) == 1 and "resolves outside" in majors[0]


def test_multi_entry_flags_only_the_bad_module(tmp_path):
    """Two modules, one valid and one R1: one register MAJOR naming ./b.js plus the second-entry MAJOR."""
    # Native 2.1.332 refuses a second `modules` entry outright ("names one
    # hooks module per plugin", probe 2026-11-27), so this per-module MAJOR
    # can never land on a natively accepted plugin.
    hooks_json = _write(tmp_path, {"a.js": "export function register(on) {}\n", "b.js": "export const marker = 1\n"})
    report = HookValidationReport(hook_path=str(hooks_json))
    r = validate_hooks_data({"modules": ["./a.js", "./b.js"]}, tmp_path, report)
    majors = _msgs(r, "MAJOR")
    register_majors = [m for m in majors if R1 in m]
    count_majors = [x.message for x in r.results if x.level == "MAJOR" and "one hooks module per plugin" in x.message]
    assert len(register_majors) == 1 and "./b.js" in register_majors[0]
    assert len(count_majors) == 1 and "2 entries" in count_majors[0]
    assert len(majors) == 1


# -- CLI exit codes ------------------------------------------------------------


def _cli(root: Path, *extra: str) -> int:
    hooks_json = root / "hooks" / "hooks.json"
    cmd = [sys.executable, str(SCRIPT), str(hooks_json), "--plugin-root", str(root), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode


def test_cli_r1_exits_2_plain_and_strict(tmp_path):
    """CLI: an R1 module exits 2 (MAJOR) with and without --strict."""
    _write(tmp_path, {"register.js": "export const marker = 1\n"})
    assert (_cli(tmp_path), _cli(tmp_path, "--strict")) == (2, 2)


def test_cli_control_valid_module_exits_0(tmp_path):
    """CLI control: a valid module exits 0 with and without --strict."""
    _write(tmp_path, {"register.js": "export function register(on) {}\n"})
    assert (_cli(tmp_path), _cli(tmp_path, "--strict")) == (0, 0)
