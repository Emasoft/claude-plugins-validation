"""Issue #271: SUPPLY_CHAIN must not fire on TypeScript array types ending in "Window".

The `window[...] =` and `globalThis[...] =` patterns flag an assignment to a
property of a global object. Compiled case-insensitively with no word boundary,
they matched the `Window[] =` inside `let chosen: Pass2Window[] = [];`. The
patterns now need a word boundary and a non-empty index, which may hold one
nested bracket, so the real
assignment still fires and a typed empty-array declaration does not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cpv_skillaudit_native import scan_content  # noqa: E402


def _supply_chain_lines(src: str, path: str) -> list[int]:
    return [f["line"] for f in scan_content(src, path) if f.get("ruleId") == "SUPPLY_CHAIN"]


@pytest.mark.parametrize("path", ["src/a.ts", "src/a.js"])
@pytest.mark.parametrize(
    "line",
    [
        "    let chosen: Pass2Window[] = [];",
        "    let chosen: ScoredWindow[] = [];",
        "    const panes: Window[] = [];",
        "    const panes: Window[] = []; a[0] = 1;",
    ],
)
def test_typed_empty_array_is_not_supply_chain(line: str, path: str) -> None:
    """A typed empty-array declaration is not a global-object assignment."""
    assert _supply_chain_lines(f"{line}\n", path) == []


@pytest.mark.parametrize("path", ["src/a.ts", "src/a.js"])
@pytest.mark.parametrize(
    "line",
    [
        'window["payload"] = loadRemote();',
        "window[key] = value;",
        "globalThis[name] = injected;",
        "window[keys[0]] = x;",
        "globalThis[a[b]] = c;",
    ],
)
def test_global_object_assignment_still_fires(line: str, path: str) -> None:
    """Assigning a property of window or globalThis is still a SUPPLY_CHAIN finding."""
    assert _supply_chain_lines(f"{line}\n", path) == [1]
