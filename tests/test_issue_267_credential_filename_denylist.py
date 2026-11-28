"""Issue #267 (CREDENTIAL_DISCOVERY half): a credential filename in a deny-list is a name comparison.

The catalog pattern for `.git-credentials` fired MAJOR on a deny-list such as
`[".netrc", ".git-credentials"].includes(name)`. A stealer uses the same literal, so the
TypeScript context classifier now clears it only when the literal is a string element of an
array / Set used in the file only through `.includes(` / `.has(` / `.indexOf(` AND the file has
no read / enumeration / exec / outbound-body sink anywhere. Real `scan_content` calls; no mocks.
"""

from __future__ import annotations

import cpv_skillaudit_native as native
import pytest

TOOL_GATE = """import { basename } from 'node:path'

export function restrictedReason(absolute: string, home: string): string | undefined {
  const name = basename(absolute)
  if (absolute.startsWith(home + '/.ssh')) return 'ssh'
  if (['.netrc', '.npmrc', '.pypirc', '.pgpass', '.git-credentials'].includes(name)) return 'credential file'
  return undefined
}
"""

DISCOVER = """import { readdirSync, readFileSync } from 'node:fs'
export function steal(home: string) {
  for (const n of ['.netrc', '.npmrc', '.pypirc', '.pgpass', '.git-credentials']) {
    try { return readFileSync(home + '/' + n, 'utf8') } catch {}
  }
  return readdirSync(home + '/.ssh')
}
"""

NAMED_DENY = """const DENY = [".env", ".git-credentials", "id_rsa"];
export function check(name: string): string | undefined {
  if (DENY.includes(name)) return "blocked";
  return undefined;
}
"""


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scan is computed, never served from the on-disk scan cache."""
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _live(source: str, path: str = "hooks/tool-gate.ts") -> list[int]:
    """Line numbers of live (unsuppressed) CREDENTIAL_DISCOVERY findings."""
    findings = native.scan_content(source, path)
    return [f["line"] for f in findings if f["ruleId"] == "CREDENTIAL_DISCOVERY" and not f.get("suppressed")]


def test_inline_deny_list_with_includes_draws_no_finding() -> None:
    """The reported tool-gate.ts shape (inline array + `.includes(name)`, no sink) is clean."""
    assert _live(TOOL_GATE) == []


def test_named_deny_list_with_includes_draws_no_finding() -> None:
    """`const DENY = [...]` used only as `DENY.includes(name)` is clean."""
    assert _live(NAMED_DENY) == []


def test_multiline_named_deny_list_draws_no_finding() -> None:
    """A multi-line array with comments, used only via `.includes`, is clean."""
    src = (
        "const DENY = [\n"
        "  // credential stores\n"
        '  ".env",\n'
        '  ".git-credentials",\n'
        '  "id_rsa",\n'
        "]\n"
        "export const blocked = (n: string) => DENY.includes(n)\n"
    )
    assert _live(src) == []


def test_named_set_with_has_draws_no_finding() -> None:
    """`const DENY = new Set([...])` used only as `DENY.has(name)` is clean."""
    src = 'const DENY = new Set([".env", ".git-credentials"]);\nexport const hit = (n: string) => DENY.has(n);\n'
    assert _live(src) == []


def test_ctrl_discover_with_read_sink_still_fires() -> None:
    """The stealer control (same literal list, readFileSync + readdirSync) keeps firing."""
    assert _live(DISCOVER, "ctrl/discover.ts") != []


def test_deny_list_plus_read_of_the_credential_file_still_fires() -> None:
    """The tool-gate shape plus a real `readFileSync(.git-credentials)` fires: the sink is file-level."""
    src = TOOL_GATE + '\nexport const leak = () => fs.readFileSync(path.join(home, ".git-credentials"))\n'
    assert _live(src) != []


@pytest.mark.parametrize(
    "sink",
    [
        "readFileSync(p)",
        "await fs.promises.readFile(p)",
        "createReadStream(p)",
        "readdirSync(dir)",
        'globSync("*")',
        'fs.openSync(p, "r")',
        "Bun.file(p).text()",
        "Deno.readTextFile(p)",
        'execSync("cat " + p)',
        'spawn("type", [p])',
        'fetch(url, { method: "POST", body: data })',
    ],
)
def test_any_sink_anywhere_in_the_file_keeps_firing(sink: str) -> None:
    """One read / enumeration / exec / outbound-body sink anywhere keeps the finding live."""
    assert _live(NAMED_DENY + f"\nexport async function other(p: string) {{ return {sink} }}\n") != []


def test_plain_path_concatenation_still_fires() -> None:
    """`home + "/.git-credentials"` is not an array element: fires."""
    assert _live('const p = home + "/.git-credentials"\nexport default p\n') != []


@pytest.mark.parametrize(
    "src",
    [
        # array iterated, not membership-tested
        'const DENY = [".git-credentials"];\nexport const all = () => DENY.map((n) => n.length);\n',
        'const DENY = [".git-credentials"];\nfor (const n of DENY) { console.log(n) }\n',
        # exported: other files may use it for anything
        'export const DENY = [".git-credentials"];\nexport const hit = (n: string) => DENY.includes(n);\n',
        # non-literal element: the list is built from a path
        'const DENY = [home + "/.git-credentials"];\nexport const hit = (n: string) => DENY.includes(n);\n',
        # declared but never used
        'const DENY = [".git-credentials"];\n',
        # array passed on to another function
        'run([".git-credentials"]);\n',
        # extra non-membership use of the name
        'const DENY = [".git-credentials"];\nexport const hit = (n: string) => DENY.includes(n);\nexport const copy = [...DENY];\n',
        # chained call on the literal
        'export const all = [".git-credentials"].map((n) => n.toUpperCase());\n',
    ],
)
def test_other_array_shapes_still_fire(src: str) -> None:
    """Anything other than a membership-only literal list keeps firing."""
    assert _live(src) != []
