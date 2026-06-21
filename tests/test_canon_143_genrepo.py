"""Two-sided tests for GitHub issue #143 — canonical-pipeline local-gate ↔ CI
gate-PARITY for the jscpd copy-paste dimension.

The generated ``publish.py --gate`` (run by the strict pre-push hook) used to run
ruff (Gate 2) but NOT the jscpd copy-paste check that the generated ``ci.yml``
Mega-Linter Lint job enforces (``COPYPASTE_JSCPD --threshold 5``). A publish could
therefore pass every local gate, exit 0, bump+tag+push+release, and only THEN have
CI fail on jscpd duplication — a green gate did not predict green CI.

The fix (single source of truth + graceful degradation, the #129 pattern):

* a NEW ``.jscpd.json`` (``gen_jscpd_json``) read by BOTH CI's Mega-Linter jscpd
  AND the local gate (jscpd auto-discovers ``.jscpd.json``), so the threshold +
  ignore list have exactly one definition;
* a NEW Gate 2b in the publish.py template (``gen_publish_py``, ``--gate`` mode)
  that probes jscpd with ``--version`` first → WARNs+skips on a tool-unavailable
  case (NEVER false-blocks a push), and BLOCKs only when jscpd actually ran and
  returned non-zero (over-threshold).

Every guard is TWO-SIDED: a present-thing is asserted PRESENT and the matching
old/broken/missing form is asserted ABSENT, so a regression in either direction
fails a test.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_jscpd_json,
    gen_mega_linter_yml,
    gen_publish_py,
)


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


# ── .jscpd.json: emitted, valid JSON, threshold 5, dev/fixture ignores ───────


def test_jscpd_json_parses_as_json() -> None:
    """``.jscpd.json`` is valid JSON (a malformed config silently disables jscpd)."""
    data = json.loads(gen_jscpd_json(_params()))
    assert isinstance(data, dict), "jscpd config must be a JSON object"


def test_jscpd_json_threshold_is_5() -> None:
    """The jscpd threshold is 5 — exact parity with the Mega-Linter argument."""
    data = json.loads(gen_jscpd_json(_params()))
    assert data.get("threshold") == 5, f"expected threshold 5, got {data.get('threshold')!r}"


def test_jscpd_json_threshold_is_not_zero() -> None:
    """Negative side: the threshold is NOT 0 (0 is too strict — bans all dup)."""
    data = json.loads(gen_jscpd_json(_params()))
    assert data.get("threshold") != 0, "threshold 0 would over-block every plugin"


def test_jscpd_json_ignores_dev_and_fixture_dirs() -> None:
    """The ``ignore`` list carries the dev-dir + test-fixture globs (mirrors
    ``.mega-linter.yml`` FILTER_REGEX_EXCLUDE) so neither gate counts a dup in a
    dev/scratch/fixture tree."""
    data = json.loads(gen_jscpd_json(_params()))
    ignore = data.get("ignore")
    assert isinstance(ignore, list) and ignore, "ignore must be a non-empty list"
    required = [
        "**/scripts_dev/**",
        "**/docs_dev/**",
        "**/tests/fixtures/**",
        "**/fixtures/**",
        "**/node_modules/**",
        "**/.git/**",
    ]
    missing = [g for g in required if g not in ignore]
    assert not missing, f"jscpd ignore list missing required globs: {missing}"


def test_jscpd_json_does_not_ignore_real_source() -> None:
    """Negative side: the ignore list does NOT blanket-skip the real source dirs
    (``scripts/``/``skills/``) — that would make the check meaningless."""
    data = json.loads(gen_jscpd_json(_params()))
    ignore = set(data.get("ignore", []))
    for never in ("**/scripts/**", "**/skills/**", "scripts/", "**"):
        assert never not in ignore, f"ignore must not blanket-skip real source: {never!r}"


# ── publish.py --gate: jscpd gate with probe + degrade-WARN + BLOCK branches ──

# The generated publish.py is one raw-string template; the gate code lives in it
# verbatim, so we assert against substrings/patterns of the generated text (the
# canon-142 test style — no real subprocess).
_GATE_BANNER_RE = re.compile(r"\[G2b\] Copy-paste check \(jscpd")
_PROBE_RE = re.compile(r'base_cmd \+ \["--version"\]')
_BLOCK_RE = re.compile(r"BLOCKED: jscpd found copy-paste duplication")


def test_gate_contains_jscpd_gate_banner() -> None:
    """The ``--gate`` text contains the jscpd Gate 2b banner."""
    text = gen_publish_py(_params())
    assert _GATE_BANNER_RE.search(text), "publish.py --gate is missing the [G2b] jscpd gate"


def test_gate_jscpd_uses_version_probe() -> None:
    """Gate 2b probes ``jscpd --version`` FIRST (distinguishes unavailable→WARN
    from ran-and-found-dupes→BLOCK)."""
    text = gen_publish_py(_params())
    assert _PROBE_RE.search(text), "jscpd gate must probe with --version before running"


def test_gate_jscpd_has_block_branch() -> None:
    """Gate 2b BLOCKs (``return 1``) when jscpd actually ran and found duplication
    over the threshold — the over-threshold path is enforced, not just warned."""
    text = gen_publish_py(_params())
    block = _BLOCK_RE.search(text)
    assert block is not None, "jscpd gate must have a BLOCKED over-threshold branch"
    # The BLOCK must be followed by a `return 1` (it actually fails the gate).
    tail = text[block.start():block.start() + 400]
    assert "return 1" in tail, "the jscpd BLOCK branch must `return 1` to fail the gate"


def test_gate_jscpd_degrades_when_tools_absent() -> None:
    """When NEITHER jscpd NOR npx is found, Gate 2b takes the non-blocking
    WARNING path — it does NOT hard-block a push on a missing tool (issue #143).

    Asserted against the generated TEXT (not a real subprocess): the
    ``base_cmd is None`` branch emits a SKIPPED WARNING and does NOT ``return 1``.
    """
    text = gen_publish_py(_params())
    # Locate the tool-absent branch and the next branch boundary (the `else:`).
    none_branch = re.search(r"if base_cmd is None:(.*?)\n    else:", text, re.DOTALL)
    assert none_branch is not None, "expected the `if base_cmd is None:` degrade branch"
    body = none_branch.group(1)
    assert "WARNING: jscpd/npx not found" in body, "degrade branch must WARN on missing tools"
    assert "SKIPPED locally" in body, "degrade branch must say the check was SKIPPED"
    assert "return 1" not in body, "the tool-absent degrade branch must NOT block the push"


def test_gate_jscpd_probe_failure_degrades() -> None:
    """When the probe runs but jscpd cannot actually start (npx fetch/install
    failed), Gate 2b WARNs+skips — it must NOT block on an install failure."""
    text = gen_publish_py(_params())
    probe_fail = re.search(r"if probe\.returncode != 0:(.*?)\n        else:", text, re.DOTALL)
    assert probe_fail is not None, "expected the probe-failure degrade branch"
    body = probe_fail.group(1)
    assert "WARNING: jscpd could not run" in body, "probe-failure branch must WARN"
    assert "return 1" not in body, "the probe-failure degrade branch must NOT block the push"


def test_gate_help_and_docstring_mention_jscpd() -> None:
    """The ``--gate`` help text and the Gate-stages docstring both mention the
    jscpd / copy-paste gate (so an adopter sees it documented)."""
    text = gen_publish_py(_params())
    assert "copy-paste (jscpd)" in text, "--gate help must mention the copy-paste (jscpd) gate"
    assert "G2b. Copy-paste check (jscpd" in text, "docstring Gate stages must list G2b jscpd"


def test_gate_jscpd_present_for_submodule_build_profile() -> None:
    """Negative-coverage of the profile path: the jscpd gate is present in the
    non-default ``submodule-build`` profile too (the gate is profile-independent —
    it is in the shared standard body the submodule profile extends)."""
    text = gen_publish_py(_params(), "submodule-build")
    assert _GATE_BANNER_RE.search(text), "submodule-build publish.py is missing the jscpd gate"
    assert _BLOCK_RE.search(text), "submodule-build jscpd gate must keep its BLOCK branch"


# ── ci.yml side: Mega-Linter still enforces COPYPASTE_JSCPD (the CI half) ─────


def test_mega_linter_still_enables_copypaste_jscpd() -> None:
    """The CI half of parity: ``.mega-linter.yml`` still enables ``COPYPASTE_JSCPD``
    and pins ``--threshold 5`` — the gate is parity WITH this, not a replacement."""
    ml = gen_mega_linter_yml(_params())
    assert "COPYPASTE_JSCPD" in ml, ".mega-linter.yml must keep COPYPASTE_JSCPD enabled"
    assert "--threshold 5" in ml, ".mega-linter.yml jscpd threshold must stay 5 (parity)"


def test_mega_linter_and_jscpd_thresholds_agree() -> None:
    """Both halves agree on threshold 5 — the local gate's ``.jscpd.json`` and the
    CI Mega-Linter argument can never silently diverge."""
    jscpd_threshold = json.loads(gen_jscpd_json(_params()))["threshold"]
    ml = gen_mega_linter_yml(_params())
    m = re.search(r"COPYPASTE_JSCPD_ARGUMENTS:\s*\"--threshold (\d+)\"", ml)
    assert m is not None, ".mega-linter.yml must declare COPYPASTE_JSCPD_ARGUMENTS --threshold N"
    assert int(m.group(1)) == jscpd_threshold == 5, (
        f"threshold mismatch: .jscpd.json={jscpd_threshold}, mega-linter={m.group(1)}"
    )
