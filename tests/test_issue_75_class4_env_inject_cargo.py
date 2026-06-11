"""Regression tests for issue #75 (class 4) — ENV_INJECTION false-positive on a
build-OUTPUT / download-CACHE directory var.

A plugin doing standard Rust build hygiene redirects Cargo's build output to a
tmp dir so artifacts don't land in the plugin tree and trip CPV's private-path
scan::

    os.environ["CARGO_TARGET_DIR"] = str(
        Path(tempfile.gettempdir())
        / ("publish-cargo-target-" + hashlib.sha256(str(root).encode()).hexdigest()[:12])
    )

The catalog rule ``ENV_INJECTION`` (``os\\.environ\\[.*\\]\\s*=[^=]``) matches every
env-var assignment; the discrimination is delegated to the Python context
classifier. The fix adds a POSITIVE allowlist of build-output / download-cache
dir vars (``_ENV_BUILD_OUTPUT_VARS``) guarded by a controlled-value-shape check
(``_is_safe_build_env_set``). Redirecting any allowlisted var only changes WHERE
artifacts/cache are written; none is consulted as a code-load, library-search,
or executable-search path at runtime, so even an attacker value cannot cause
code execution.

FN-safe property — the allowlist is positive and excludes every runtime-hijack
var, so ``LD_PRELOAD`` / ``PATH`` / ``PYTHONPATH`` / ``NODE_OPTIONS`` / ``DYLD_*``
/ ``GIT_SSH_COMMAND`` can NEVER be suppressed regardless of value shape::

    _ENV_BUILD_OUTPUT_VARS & _ENV_HIJACK_VARS == frozenset()   # asserted below

Every case is TWO-SIDED: the build-hygiene FP clears AND a real-threat sibling
(a hijack var, an attacker bare-name value, an interpolating f-string, or a
deliberately-excluded borderline var like ``CARGO_HOME``/``TMPDIR``/``GOPATH``)
still fires.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from _skillaudit_python_context import (  # noqa: E402
    _ENV_BUILD_OUTPUT_VARS,
    _ENV_HIJACK_VARS,
    _is_safe_build_env_set,
)
from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh.

    The v2.104.0 cache keys on (content_hash, catalog_hash, version, ext) —
    NOT the classifier code — so without this a same-version classifier change
    would be masked by a cache hit.
    """
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _visible_env_injection_lines(content: str, file_path: str) -> set[int]:
    """1-based line numbers where ENV_INJECTION is ACTIONABLE (not suppressed).

    Mirrors the filter ``run_skillaudit_scan`` applies before findings reach
    the publish gate.
    """
    return {
        h.get("line")
        for h in scan_content(content, file_path)
        if h.get("ruleId") == "ENV_INJECTION" and not h.get("suppressed")
    }


# ---------------------------------------------------------------------------
# §5 table A — MUST CLEAR (suppressed) — build-output / cache hygiene
# ---------------------------------------------------------------------------
_CLEAR_LINES = [
    # The exact janitor shape — the literal key AND the value opener `str(`
    # live on this one line; no multi-line buffering needed.
    'os.environ["CARGO_TARGET_DIR"] = str(',
    'os.environ["CARGO_TARGET_DIR"] = "/tmp/build"',
    "os.environ['GOCACHE'] = Path(tempfile.gettempdir()) / 'gc'",
    'os.environ["UV_CACHE_DIR"] = tempfile.mkdtemp()',
    'os.environ["PIP_CACHE_DIR"] = os.path.join(tmp, "pipc")',
    'os.environ["npm_config_cache"] = str(cache_dir)',
    # leading-indent variant (the real publish.py shape is indented)
    '    os.environ["CCACHE_DIR"] = str(tmp / "cc")',
    'os.environ["NPM_CONFIG_CACHE"] = pathlib.Path(tmp)',
]

# ---------------------------------------------------------------------------
# §5 table B — MUST FIRE (stay visible) — hijack / attacker / borderline
# ---------------------------------------------------------------------------
_FIRE_LINES = [
    # genuine runtime-hijack vars — never allowlisted, fire regardless of value
    ('os.environ["LD_PRELOAD"] = "/tmp/evil.so"', "library-load hijack"),
    ("os.environ['PATH'] = attacker", "exec-path hijack"),
    ('os.environ["PYTHONPATH"] = str(evil_dir)', "hijack var even with str()"),
    ('os.environ["NODE_OPTIONS"] = "--require /tmp/x.js"', "node preload hijack"),
    ('os.environ["DYLD_INSERT_LIBRARIES"] = lib', "macOS lib-inject hijack"),
    ('os.environ["GIT_SSH_COMMAND"] = cmd', "ssh-command hijack"),
    # allowlisted KEY but uncontrolled value shape
    ('os.environ["CARGO_TARGET_DIR"] = attacker', "allowlisted key but bare-name value"),
    ('os.environ["CARGO_TARGET_DIR"] = f"{user_input}/t"', "f-string interpolates input"),
    # deliberately-excluded borderline vars (code/tool-resolution semantics)
    ('os.environ["CARGO_HOME"] = str(tmp)', "NOT in allowlist (has bin/)"),
    ('os.environ["TMPDIR"] = str(tmp)', "NOT in allowlist (exec-drop surface)"),
    ('os.environ["GOPATH"] = str(tmp)', "NOT in allowlist (has bin/)"),
]


@pytest.mark.parametrize("line", _CLEAR_LINES)
def test_build_output_env_set_is_suppressed(line: str) -> None:
    """A build-output/cache dir var set to a controlled path/literal is hygiene,
    not injection — ``_is_safe_build_env_set`` returns True (suppressed)."""
    assert _is_safe_build_env_set(line) is True, f"build hygiene should clear: {line!r}"


@pytest.mark.parametrize("line,reason", _FIRE_LINES)
def test_hijack_and_uncontrolled_env_set_keeps_firing(line: str, reason: str) -> None:
    """A runtime-hijack var, an uncontrolled value, or a deliberately-excluded
    borderline var is NEVER suppressed — ``_is_safe_build_env_set`` returns
    False so the ENV_INJECTION finding stays visible."""
    assert _is_safe_build_env_set(line) is False, f"must keep firing ({reason}): {line!r}"


def test_allowlist_excludes_every_runtime_hijack_var() -> None:
    """The structural FN-safety invariant: the build-output allowlist shares NO
    member with the runtime-hijack-var set. If this ever becomes non-empty, a
    hijack var could be suppressed — a real security regression."""
    assert _ENV_BUILD_OUTPUT_VARS & _ENV_HIJACK_VARS == frozenset()


def test_allowlist_excludes_known_borderline_vars() -> None:
    """Vars with code/tool-resolution semantics are intentionally OUT of the
    allowlist so they stay visible (documented exclusions in §4)."""
    for excluded in ("CARGO_HOME", "GOPATH", "TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME"):
        assert excluded not in _ENV_BUILD_OUTPUT_VARS, f"{excluded} must stay visible"


# ---------------------------------------------------------------------------
# End-to-end through the real classifier (scan_content), mirroring §3.
# ---------------------------------------------------------------------------

# The exact issue #75 fixture content: build hygiene (line 4, the `str(` opener)
# plus two must-fire hijack controls (line 7 LD_PRELOAD, line 8 PATH=attacker).
_FIXTURE_PY = (
    "import os, tempfile, hashlib\n"  # 1
    "from pathlib import Path\n"  # 2
    "def redirect(root):\n"  # 3
    '    os.environ["CARGO_TARGET_DIR"] = str(\n'  # 4 — build hygiene (CLEAR)
    '        Path(tempfile.gettempdir()) / ("t-" + hashlib.sha256(str(root).encode()).hexdigest()[:12])\n'  # 5
    "    )\n"  # 6
    '    os.environ["LD_PRELOAD"] = "/tmp/evil.so"\n'  # 7 — hijack (FIRE)
    '    os.environ["PATH"] = attacker\n'  # 8 — hijack (FIRE)
)


def test_e2e_cargo_target_dir_cleared_hijack_vars_fire() -> None:
    """Through the real ``scan_content`` classifier on the fixture content: the
    CARGO_TARGET_DIR build-hygiene line is NOT visible, while LD_PRELOAD and
    PATH=attacker STILL fire (the two-sided contract end-to-end)."""
    visible = _visible_env_injection_lines(_FIXTURE_PY, "scripts/publish_like.py")
    assert 4 not in visible, f"CARGO_TARGET_DIR build hygiene must clear, got visible={sorted(visible)}"
    assert 7 in visible, f"LD_PRELOAD hijack must still fire, got visible={sorted(visible)}"
    assert 8 in visible, f"PATH=attacker hijack must still fire, got visible={sorted(visible)}"
