#!/usr/bin/env python3
"""Regression lock: the canon pins
``peter-evans/repository-dispatch`` to **v4.0.0** (defensive
"second-latest" policy), NOT v4.0.1, even though v4.0.1 is healthy.

History (CPV v2.107.4): on 2026-05-26 GitHub Actions suffered a
~2h21m platform-wide **authentication outage** (incident
``gnftqj9htp0g``, 10:57Z → 13:18Z) that caused every runner's
``Set up job → Download action repository`` step to fail with::

    An action could not be found at the URI
    'https://codeload.github.com/<owner>/<repo>/tar.gz/<sha>'
    Failed to download archive ... after 1 attempts.

The misleading 404 message looked like per-SHA cache poisoning,
but post-incident investigation confirmed it was an action-
agnostic failure of the runner's auth-to-codeload path (multiple
publishers, multiple SHAs, multiple Azure regions all failed in
the same window). See
``reports/canon-audit/20260526_152411+0200-gha-codeload-investigation.md``
for the full timing-and-evidence record.

The canon's v4.0.1 SHA
``28959ce8df70de7be546dd1250a005dd32156697`` is itself healthy
and would work today. CPV nonetheless **keeps a defensive pin on
v4.0.0** because:

1. The "second-latest pin" policy gives downstream plugins a
   small hedge against future GitHub Actions outages — bleeding-
   edge pins are the most likely to be ``pinact``-bumped right
   before an outage and thus the most likely to be the user-
   visible symptom.
2. v4.0.0 is functionally equivalent for the canon's use case
   (notify-marketplace dispatch).
3. The cost of staying one tag behind is zero; the benefit of
   not being on the bleeding edge during the next incident is
   non-zero.

A future ``pinact`` bump back to v4.0.1 (or later) MUST update
both files in lockstep AND delete this test (the regression
record stays in git history).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = REPO_ROOT / "templates" / "github-workflows" / "notify-marketplace.yml"
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_plugin_repo.py"

# The broken v4.0.1 SHA we never want to see in the canon again until
# we deliberately re-introduce it via pinact.
BROKEN_V401_SHA = "28959ce8df70de7be546dd1250a005dd32156697"

# The known-good v4.0.0 SHA the canon falls back to.
GOOD_V400_SHA = "5fc4efd1a4797ddb68ffd0714a238564e4cc0e6f"

# `repository-dispatch@<40-hex-sha> # <semver-or-tag>` — full canonical
# pin shape; if the canon ever drifts to a tag-only pin
# (`peter-evans/repository-dispatch@v4`) this regex won't match and the
# "MUST exist" assertions below will fail — which is the correct
# behaviour, since the gh-actions rule requires SHA pins for
# third-party actions.
SHA_PIN_RE = re.compile(r"peter-evans/repository-dispatch@(?P<sha>[0-9a-f]{40})\s*#\s*(?P<tag>\S+)")


def test_canon_template_does_not_ship_broken_sha() -> None:
    """templates/github-workflows/notify-marketplace.yml MUST NOT
    contain the v4.0.1 SHA as the pin target."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    # The SHA may still appear in an explanatory comment — that's fine —
    # but it must NOT be the `uses:` target. Anchor by checking the
    # `uses:` line specifically.
    uses_line = next(
        (ln for ln in text.splitlines() if "uses: peter-evans/repository-dispatch@" in ln),
        None,
    )
    assert uses_line is not None, "canon template missing repository-dispatch uses: line"
    assert BROKEN_V401_SHA not in uses_line, (
        f"canon template pin still on broken v4.0.1 SHA {BROKEN_V401_SHA!r}: {uses_line!r}"
    )


def test_canon_template_uses_good_v400_sha() -> None:
    """templates/github-workflows/notify-marketplace.yml MUST pin
    the v4.0.0 SHA (the immediately-prior tagged release)."""
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    uses_line = next(
        (ln for ln in text.splitlines() if "uses: peter-evans/repository-dispatch@" in ln),
        None,
    )
    assert uses_line is not None
    m = SHA_PIN_RE.search(uses_line)
    assert m is not None, f"canon template uses: line not in SHA-pinned shape: {uses_line!r}"
    assert m.group("sha") == GOOD_V400_SHA, (
        f"canon template pinned to {m.group('sha')!r}, expected v4.0.0 SHA {GOOD_V400_SHA!r}"
    )


def test_generator_does_not_ship_broken_sha() -> None:
    """scripts/generate_plugin_repo.py (the notify-marketplace.yml
    codegen branch) MUST NOT contain the v4.0.1 SHA as the pin
    target. Otherwise every newly-generated plugin would ship the
    broken pin."""
    text = GENERATOR_PATH.read_text(encoding="utf-8")
    # The generator embeds the workflow as a multi-line string. Grep
    # for any `uses: peter-evans/repository-dispatch@<sha>` line and
    # check the SHA.
    matches = list(SHA_PIN_RE.finditer(text))
    assert matches, "generator missing repository-dispatch SHA pin"
    for m in matches:
        assert m.group("sha") != BROKEN_V401_SHA, (
            f"generate_plugin_repo.py still emits broken v4.0.1 SHA {BROKEN_V401_SHA!r} (match: {m.group(0)!r})"
        )


def test_generator_uses_good_v400_sha() -> None:
    """scripts/generate_plugin_repo.py must pin to v4.0.0."""
    text = GENERATOR_PATH.read_text(encoding="utf-8")
    matches = list(SHA_PIN_RE.finditer(text))
    assert matches, "generator missing repository-dispatch SHA pin"
    for m in matches:
        assert m.group("sha") == GOOD_V400_SHA, (
            f"generate_plugin_repo.py pinned to {m.group('sha')!r}, expected v4.0.0 SHA {GOOD_V400_SHA!r}"
        )


def test_canon_setup_uv_sha_is_unrelated_to_v810_breakage() -> None:
    """Belt-and-suspenders: the canon's ``astral-sh/setup-uv`` pin
    must NOT be the broken v8.1.0 SHA
    (``08807647e7069bb48b6ef5acd8ec9567f424441b``). The canon ships
    ``e4db8464...`` (v4 floating tag head) — much older and never
    affected by the 2026-05-26 codeload flake. This test pins that
    invariant so a future bump can't accidentally land on the
    poisoned SHA."""
    text = GENERATOR_PATH.read_text(encoding="utf-8")
    broken_v810_sha = "08807647e7069bb48b6ef5acd8ec9567f424441b"
    setup_uv_re = re.compile(r"astral-sh/setup-uv@(?P<sha>[0-9a-f]{40})")
    for m in setup_uv_re.finditer(text):
        assert m.group("sha") != broken_v810_sha, (
            "generate_plugin_repo.py emits the broken v8.1.0 setup-uv SHA — that SHA is unavailable to GHA codeload."
        )
