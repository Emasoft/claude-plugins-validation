"""Audit-fix regression tests — batch B18.

Covers the two code findings the B18 audit raised against
``scripts/_skillaudit_typescript_context.py``:

* HIGH — ``_line_is_function_definition`` suppressed SSRF_ADVANCED for a
  *single-line concise-body* arrow function whose body is itself a genuine
  outbound HTTP call (``const h = (req) => fetch(req.query.url)``). The
  function-definition shape matched, so ``classify()`` returned
  ``safe_literal`` and HID a real SSRF surface (security false-negative).
  The fix keeps such a line VISIBLE (verdict ``unknown``) while leaving real
  function definitions, local-method invocations, block-body arrows, and
  library-client method calls suppressed.

* LOW (#142) — ``_FUNCTION_DEF_RES`` carried a 4th pattern that
  ``_line_is_function_definition`` never iterated past (it used
  ``[:3]``). The method-call-on-object case is already handled by the
  inline ``method_call_re`` (which, unlike the dead pattern, excludes the
  HTTP method names). The dead pattern was removed; the tuple is now 3
  elements and the function iterates the whole tuple.

Every SSRF assertion is TWO-SIDED: the malicious shape must now be flagged
(verdict ``unknown`` → kept visible) AND the benign shapes must stay
suppressed (verdict ``safe_literal``). A one-sided test would pass with a
classifier that suppresses (or flags) everything.

Classifiers are exercised directly (unit level) so the tests are fast and
independent of the SQLite scan cache.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Probes/tests must not read the persistent scan cache.
os.environ.setdefault("CPV_SCAN_CACHE", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _skillaudit_typescript_context as tsctx  # noqa: E402


def _classify_ssrf(line: str) -> str:
    """Classify a single-line SSRF_ADVANCED match (line_idx 0)."""
    return tsctx.classify("server.ts", line, 0, line, "SSRF_ADVANCED")


# --- HIGH (malicious side): genuine concise-arrow SSRF must stay VISIBLE -----

MALICIOUS_CONCISE_ARROW_SSRF = [
    "const handler = async (req) => fetch(req.query.url)",
    "const proxy = (req) => fetch(req.body.target)",
    "let g = async (request) => axios.get(request.query.dest)",
    "var p = (req) => http.get(req.params.url)",
    "const f = (r) => https.get(r.query.u)",
    "const f = (r) => axios(r.body.url)",
]


@pytest.mark.parametrize("line", MALICIOUS_CONCISE_ARROW_SSRF)
def test_concise_arrow_ssrf_is_kept_visible(line: str) -> None:
    """A single-line arrow that inlines a global outbound HTTP call on
    user-controlled data is a real SSRF surface — it must NOT be suppressed.

    This is the original bug: ``_line_is_function_definition`` matched the
    ``const name = (args) =>`` shape and returned True, so SSRF_ADVANCED was
    classified ``safe_literal`` (hidden). The fix returns ``unknown`` so the
    finding falls through to the heuristic chain and stays visible.
    """
    assert _classify_ssrf(line) == "unknown", (
        f"genuine SSRF concise-arrow was suppressed (security false-negative): {line!r}"
    )
    # Guard that would have caught the original bug at the helper level.
    assert tsctx._line_is_function_definition(line) is False, (
        f"_line_is_function_definition must not treat an inlined outbound HTTP call as a benign definition: {line!r}"
    )


# --- HIGH (benign side): real definitions / local invokes stay SUPPRESSED ----

BENIGN_FUNCTION_DEFS = [
    "async handleRequest(request) {",  # async method definition
    "server.handleRequest(request);",  # local-method invocation
    "function handleRequest(request) {",  # named function definition
    "const handler = async (req) => { return 1 }",  # block-body arrow, no net on sig line
]


@pytest.mark.parametrize("line", BENIGN_FUNCTION_DEFS)
def test_real_function_definitions_stay_suppressed(line: str) -> None:
    """Real function/method definitions and local-method invocations (the
    legitimate SSRF_ADVANCED false-positives this helper exists to suppress)
    must STILL be classified ``safe_literal`` after the fix."""
    assert _classify_ssrf(line) == "safe_literal", (
        f"benign function definition / local invoke was incorrectly kept visible: {line!r}"
    )


# Library-client method calls in a concise arrow are suppressed elsewhere via
# the leading-dot method-call path; the fix's negative lookbehind must NOT
# steal them back into the "visible" bucket.
BENIGN_LIBRARY_METHOD_ARROWS = [
    "const fetchUser = (id) => client.users.fetch(id)",  # Discord.js
    "const g = (id) => guild.members.fetch(id)",  # Discord.js
]


@pytest.mark.parametrize("line", BENIGN_LIBRARY_METHOD_ARROWS)
def test_library_client_method_arrows_stay_suppressed(line: str) -> None:
    """A concise arrow whose body is a LIBRARY-CLIENT method call
    (``client.users.fetch(id)``) is not a global HTTP call — it must stay
    ``safe_literal``. The arrow-body detector's ``(?<![.\\w$])`` lookbehind
    excludes these (they have a leading dot)."""
    assert _classify_ssrf(line) == "safe_literal", (
        f"library-client method call in arrow was incorrectly kept visible: {line!r}"
    )


def test_block_body_arrow_with_fetch_on_separate_line_unchanged() -> None:
    """Control: a block-body arrow whose ``fetch`` call lives on a SEPARATE
    body line was already correctly kept visible (``unknown``) and must stay
    that way — the fix only targets the concise (single-line) form."""
    block = "const handler = async (req) => {\n  return fetch(req.query.url)\n}"
    assert tsctx.classify("server.ts", block, 1, "fetch(req.query.url)", "SSRF_ADVANCED") == "unknown"


# --- LOW (#142): dead pattern[3] removed -------------------------------------


def test_function_def_res_has_no_dead_pattern() -> None:
    """``_FUNCTION_DEF_RES`` must contain exactly the 3 patterns that
    ``_line_is_function_definition`` actually iterates.

    The former 4th element (the ``obj.method(`` method-call-on-object regex)
    was dead code: the function used ``[:3]`` so it was never reached, and it
    lacked the HTTP-method-name exclusion the inline ``method_call_re`` has.
    Re-adding a 4th pattern that the function does not consult would
    reintroduce dead code, so this guard pins the count.
    """
    assert len(tsctx._FUNCTION_DEF_RES) == 3, (
        f"expected exactly 3 function-definition patterns (dead pattern[3] removed); got {len(tsctx._FUNCTION_DEF_RES)}"
    )


def test_object_method_call_still_suppressed_via_inline_regex() -> None:
    """Removing the dead pattern must not regress the method-call-on-object
    suppression: a non-HTTP local-method invocation is still treated as a
    function-definition-class line (handled by the inline ``method_call_re``)."""
    # Non-HTTP local method → suppressed (True).
    assert tsctx._line_is_function_definition("server.handleRequest(request);") is True
    # HTTP global call (no leading dot) → NOT suppressed by the method path.
    assert tsctx._line_is_function_definition("fetch(req.query.url)") is False
