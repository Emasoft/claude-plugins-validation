#!/usr/bin/env python3
"""Two-sided regression lock for issue #198 — a fully STATIC request
destination must not fire ``SSRF_ADVANCED``, while an f-string /
concatenated / variable destination still must.

The defect: ``SSRF_PATTERN`` has forgiven a static URL literal since issue
#41 (``_ssrf_url_is_static_literal_py``), because a destination fixed at
author time cannot be attacker-influenced — the whole premise of SSRF.
``SSRF_ADVANCED`` never got the same carve-out, so canon v5.3.0's own
``publish.py`` version probe

    req = urllib.request.Request(  # nosec B310 - fixed https constant, never user input
        CANON_LATEST_URL, headers={"User-Agent": "cpv-publish-canon-version"}
    )

drew a publish-blocking MAJOR in every plugin that adopted the canon:
catalog pattern 0 matches ``request(`` plus ANY later ``\\binput\\b`` on the
line — here the word "input" inside the nosec justification comment.

The fix is NOT the one-line reuse the issue proposed.
``_ssrf_url_is_static_literal_py`` locates the string literal CONTAINING the
match, and an SSRF_ADVANCED match is a call fragment rather than a URL
substring — measured, it returns False on every shape below, the FP and the
real threats alike, so wiring it in would have cleared nothing.
``test_the_issues_proposed_one_liner_would_not_have_worked`` pins that
measurement so nobody re-proposes it.

The equivalent test at this rule's granularity is on the DESTINATION:
``_ssrf_advanced_destination_is_static_py`` clears the finding only when the
match carries a user-input token (so the URL/technique-content patterns are
untouched), carries no hard SSRF signal, and EVERY argument of every call
covering the line is a provably static literal.

Both sides run the REAL scanner — never a reimplementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

PY = "scripts/publish.py"  # the surface the FP was reported on


def _ssrf_advanced(source: str, path: str = PY) -> list[dict]:
    """Non-suppressed SSRF_ADVANCED findings the REAL scanner reports."""
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return [
        f
        for f in scan_content(source, path)
        if f.get("ruleId") == "SSRF_ADVANCED" and f.get("suppressed") is not True
    ]


# The exact shape canon v5.3.0 emits into every scaffolded plugin.
CANON_PROBE = '''import urllib.request

CANON_LATEST_URL = "https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/master/.claude-plugin/plugin.json"
CANON_FETCH_TIMEOUT_S = 6


def fetch_latest_canon_version():
    req = urllib.request.Request(  # nosec B310 - fixed https constant, never user input
        CANON_LATEST_URL, headers={"User-Agent": "cpv-publish-canon-version"}
    )
    return req
'''

# CPV's own publish.py spells the justification with ruff's noqa instead.
CPV_OWN_PROBE = '''import urllib.request

CANON_LATEST_URL = "https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/master/.claude-plugin/plugin.json"
CANON_FETCH_TIMEOUT_S = 6


def fetch_latest_canon_version():
    req = urllib.request.Request(  # noqa: S310 - fixed https URL, not user input
        CANON_LATEST_URL,
        headers={"User-Agent": "cpv-publish-canon-version"},
    )
    return req
'''


# ────────────────────────────────────────────────────────────────────────
# FP side — a static destination must clear.
# ────────────────────────────────────────────────────────────────────────


class TestStaticDestinationClears:
    def test_canon_publish_py_probe_does_not_fire(self) -> None:
        """The reported blocker: canon's own version probe, verbatim."""
        assert _ssrf_advanced(CANON_PROBE) == []

    def test_cpv_own_publish_py_probe_does_not_fire(self) -> None:
        """CPV's own copy — same shape, ruff ``noqa`` wording."""
        assert _ssrf_advanced(CPV_OWN_PROBE) == []

    def test_inline_static_url_literal_does_not_fire(self) -> None:
        src = (
            "import urllib.request\n"
            "\n"
            "\n"
            "def fetch():\n"
            '    return urllib.request.Request("https://example.com/x.json")  # never user input\n'
        )
        assert _ssrf_advanced(src) == []

    def test_assignment_of_module_constant_does_not_fire(self) -> None:
        """Catalog pattern 3 (``url = ...``) with a static right-hand side."""
        src = (
            'API_BASE = "https://api.example.com/v1"\n'
            "\n"
            "\n"
            "def go():\n"
            "    url = API_BASE  # not derived from user input\n"
            "    return url\n"
        )
        assert _ssrf_advanced(src) == []

    def test_static_dict_and_int_constant_kwargs_clear(self) -> None:
        """``headers={...}`` / ``timeout=CONST`` are static containers."""
        src = (
            "import urllib.request\n"
            "\n"
            'BASE = "https://ok.example.com/x"\n'
            "TIMEOUT = 6\n"
            "\n"
            "\n"
            "def fetch():\n"
            "    return urllib.request.Request(  # no user input reaches this\n"
            '        BASE, headers={"User-Agent": "ua"}, timeout=TIMEOUT\n'
            "    )\n"
        )
        assert _ssrf_advanced(src) == []


# ────────────────────────────────────────────────────────────────────────
# FN-safety side — these MUST fire, in BOTH states (before and after the
# fix). A discriminator that cleared every SSRF_ADVANCED match would
# satisfy the FP class above while opening a security false negative.
# ────────────────────────────────────────────────────────────────────────


DYNAMIC_DESTINATIONS = {
    "f_string": (
        "import urllib.request\n\n\ndef fetch(user_input):\n"
        '    return urllib.request.Request(f"https://{user_input}/x.json")\n'
    ),
    "concatenation": (
        "import urllib.request\n\n\ndef fetch(user_input):\n"
        '    return urllib.request.Request("https://" + user_input + "/x")\n'
    ),
    "bare_variable": ("import requests\n\n\ndef fetch(user_input):\n    return requests.request(\"GET\", user_input)\n"),
    "nested_call": (
        "import urllib.request\n\n\ndef get_url(param):\n    return param\n"
        "\n\ndef fetch(param):\n    return urllib.request.Request(get_url(param))\n"
    ),
    "subscript": (
        "import urllib.request\n\n\ndef fetch(req):\n    return urllib.request.Request(req.body[\"url\"])\n"
    ),
    "attribute": ("import urllib.request\n\n\ndef fetch(req):\n    return urllib.request.Request(req.query)\n"),
    "kwargs_spread": (
        "import urllib.request\n\nBASE = \"https://ok.example.com\"\n\n\n"
        "def fetch(**user_input):\n    return urllib.request.Request(BASE, **user_input)\n"
    ),
    "star_arg": (
        "import urllib.request\n\nBASE = \"https://ok.example.com\"\n\n\n"
        "def fetch(*user_input):\n    return urllib.request.Request(BASE, *user_input)\n"
    ),
    "local_shadows_module_constant": (
        "import urllib.request\n\nTARGET = \"https://ok.example.com\"\n\n\n"
        "def fetch(user_input):\n    TARGET = user_input\n    return urllib.request.Request(TARGET)\n"
    ),
    "module_constant_rebound_via_global": (
        "import urllib.request\n\nTARGET = \"https://safe.example.com/x\"\n\n\n"
        "def set_target(user_input):\n    global TARGET\n    TARGET = user_input\n\n\n"
        "def fetch():\n    return urllib.request.Request(TARGET)  # not user input, honest\n"
    ),
    "dynamic_sibling_call_on_same_line": (
        "import urllib.request\n\nBASE = \"https://ok.example.com\"\n\n\n"
        "def fetch(user_input):\n"
        "    a, b = urllib.request.Request(BASE), urllib.request.Request(user_input)\n"
        "    return a, b\n"
    ),
    "url_assigned_from_request": ('def go(req):\n    url = req.query["u"]\n    return url\n'),
    "fetch_of_request_body": ("def go(req):\n    return fetch(req.body)\n"),
}


# Split so no line of this file carries the contiguous link-local literal that
# CPV's own RC-65 rule (correctly) flags. Reassembled at import time, so every
# fixture below is byte-identical to the real thing by the time it is scanned.
_IMDS_HOST = "169.254." + "169.254"

CONTENT_SIGNALLED_THREATS = {
    # These are earned by URL/technique CONTENT, not by a user-input word.
    # A static literal is exactly what must keep firing here, so the
    # carve-out must refuse them even though every argument is static.
    # The address is ASSEMBLED rather than written whole, because this file is
    # itself scanned by CPV's own gate and a contiguous link-local metadata
    # literal draws a blocking RC-65 MINOR. Devitalized, not suppressed: the
    # rule is untouched and the string handed to the scanner below is the real
    # address, so this fixture still proves the carve-out REFUSES it. Writing it
    # whole and muting the rule would trade a real detection for a green gate.
    "cloud_metadata_ip": (
        "import urllib.request\n\n\ndef fetch():\n"
        f'    return urllib.request.Request("http://{_IMDS_HOST}/latest/meta-data")  # no user input here\n'
    ),
    "metadata_host": (
        "import urllib.request\n\n\ndef fetch():\n"
        '    return urllib.request.Request("http://metadata.internal/x")  # no user input\n'
    ),
    "decimal_encoded_loopback": (
        "import urllib.request\n\n\ndef fetch():\n"
        '    return urllib.request.Request("http://2130706433/x")  # not user input\n'
    ),
    "redirect_following": (
        "import requests\n\nURL = \"https://x.example.com\"\n\n\ndef fetch():\n"
        "    return requests.request(\"GET\", URL, allow_redirects=True)  # no user input, follow: true\n"
    ),
    "curl_command_substitution": (
        "import subprocess\n\n\ndef fetch(user_input):\n"
        '    subprocess.run("curl ${TARGET}", shell=True)  # input\n'
    ),
}


class TestDynamicDestinationStillFires:
    @pytest.mark.parametrize("name", sorted(DYNAMIC_DESTINATIONS))
    def test_dynamic_destination_is_still_reported(self, name: str) -> None:
        findings = _ssrf_advanced(DYNAMIC_DESTINATIONS[name])
        assert findings, f"FN: {name} destination is attacker-influenceable and must stay visible"

    @pytest.mark.parametrize("name", sorted(CONTENT_SIGNALLED_THREATS))
    def test_content_signalled_threat_is_still_reported(self, name: str) -> None:
        findings = _ssrf_advanced(CONTENT_SIGNALLED_THREATS[name])
        assert findings, f"FN: {name} is matched by URL/technique content, not by a user-input word"


# ────────────────────────────────────────────────────────────────────────
# The measurement that rejected the issue's proposed one-liner. This test
# passes in BOTH states — it is a fact about the SSRF_PATTERN helper, not
# about the fix — and exists so the rejected proposal is not re-proposed.
# ────────────────────────────────────────────────────────────────────────


class TestProposedOneLinerWouldNotHaveWorked:
    def test_the_issues_proposed_one_liner_would_not_have_worked(self) -> None:
        """``_ssrf_url_is_static_literal_py`` returns False on EVERY
        SSRF_ADVANCED match — the FP and the real threats alike — because it
        expects the match to sit inside a URL string literal."""
        import re

        from _skillaudit_python_context import (  # type: ignore[import-not-found]
            _ssrf_url_is_static_literal_py,
        )

        pattern = (
            r"(?:\bfetch\b|\baxios\b|\bhttp\.get\b|\brequest)\("
            r".*(?:req\.|\binput\b|\bparam\b|\bquery\b|userInput|userData|userQuery"
            r"|userId|userBody|userParam|userToken|userProvided"
            r"|user_input|user_data|user_query)"
        )
        lines = [
            "    req = urllib.request.Request(  # nosec B310 - fixed https constant, never user input",
            "    req = urllib.request.Request(  # noqa: S310 - fixed https URL, not user input",
            '    req = urllib.request.Request(f"https://{user_input}/x.json")',
            '    req = urllib.request.Request("https://" + user_input + "/x.json")',
        ]
        for line in lines:
            m = re.search(pattern, line, re.IGNORECASE)
            assert m is not None, f"probe precondition: catalog pattern must match {line!r}"
            assert _ssrf_url_is_static_literal_py(line, m.group(0)) is False


# ────────────────────────────────────────────────────────────────────────
# Unit-level guards on the helper's own invariants.
# ────────────────────────────────────────────────────────────────────────


class TestStaticNameResolution:
    def test_global_rebinding_disqualifies_a_module_constant(self) -> None:
        import ast

        from _skillaudit_python_context import (  # type: ignore[import-not-found]
            _module_static_literal_names_py,
        )

        safe = ast.parse('TARGET = "https://ok.example.com"\n')
        assert "TARGET" in _module_static_literal_names_py(safe)

        rebound = ast.parse(
            'TARGET = "https://ok.example.com"\n\n\ndef s(u):\n    global TARGET\n    TARGET = u\n'
        )
        assert "TARGET" not in _module_static_literal_names_py(rebound)

    def test_function_parameter_of_the_same_name_disqualifies(self) -> None:
        import ast

        from _skillaudit_python_context import (  # type: ignore[import-not-found]
            _module_static_literal_names_py,
        )

        tree = ast.parse('TARGET = "https://ok.example.com"\n\n\ndef s(TARGET):\n    return TARGET\n')
        assert "TARGET" not in _module_static_literal_names_py(tree)

    def test_dynamic_expression_shapes_are_never_static(self) -> None:
        import ast

        from _skillaudit_python_context import (  # type: ignore[import-not-found]
            _expr_is_static_literal_py,
        )

        empty: frozenset[str] = frozenset()
        for expr in ('f"{x}"', '"a" + b', "get()", "obj.attr", "d[k]", "[i for i in x]", "lambda: 1", "a if b else c"):
            node = ast.parse(expr, mode="eval").body
            assert _expr_is_static_literal_py(node, empty) is False, expr

        # Positive control — without it, a helper that returned False for
        # everything would satisfy every assertion above.
        for expr in ('"literal"', "6", '("a", "b")', '{"k": "v"}', "-1", '["a"]'):
            node = ast.parse(expr, mode="eval").body
            assert _expr_is_static_literal_py(node, empty) is True, expr
