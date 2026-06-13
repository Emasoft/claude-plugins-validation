#!/usr/bin/env python3
"""HTML context classifier for SkillAudit (issue #105).

Given an HTML (``.html`` / ``.htm``) file plus the line index a SkillAudit
regex matched, decide whether a ``SUPPLY_CHAIN`` match is a benign pinned
import from a reputable CDN — the legitimate zero-install delivery
mechanism for a self-contained single-file HTML artifact — or a genuine
supply-chain threat.

The single FP class this classifier addresses is the ``SUPPLY_CHAIN`` rule
firing on a pinned ESM import from a reputable CDN host inside a
self-contained ``.html`` artifact, e.g. a ``<script type="module">`` that
does ``import mermaid from '<reputable-cdn-host>/npm/mermaid@11/...'``.

CPV ALREADY classifies that exact shape as benign inside a fenced
``html`` code block in markdown (``_skillaudit_markdown_context``). This
classifier reuses the SAME reputable-CDN host allowlist
(``_is_known_cdn_import`` / ``_KNOWN_CDN_HOST_RE``) and the SAME
official-install-pipe allowlist (``_is_official_install_pipe``) by
importing them — never copying the host list — so the allowlist stays a
single source of truth. Without this, the identical pinned import is
benign in a ``html`` fence in SKILL.md but a blocking MAJOR in a ``.html``
file (the inconsistency the reporter hit).

Conservative by construction:

* Only ``SUPPLY_CHAIN`` matches are ever suppressed. Every other rule —
  execution / secret / exfil / decode (an inline ``<script>`` that
  evaluates fetched remote code, or exfiltrates to a webhook host) — falls
  through to ``"unknown"`` so the heuristic chain runs unchanged and the
  finding still fires.
* The import host must be in the reputable-CDN allowlist. An unknown-host
  import (``import x from '<attacker-host>/steal.js'``) is NOT on the
  allowlist, so ``_is_known_cdn_import`` returns False and it falls through
  and fires.
* re2-safe (the reused patterns carry no lookbehind / lookahead), so the
  classifier behaves identically with and without ``google-re2``.
"""

from __future__ import annotations

from typing import Literal

ContextVerdict = Literal["safe_literal", "unknown"]


def classify(
    file_path: str,
    content: str,
    line_idx: int,
    match: str,
    rule_id: str,
) -> ContextVerdict:
    """Classify a SkillAudit match on an HTML source line.

    Returns ``"safe_literal"`` only for a ``SUPPLY_CHAIN`` match that is a
    pinned import from a reputable CDN host (or the canonical
    official-host install-pipe shape); ``"unknown"`` for everything else so
    the regular heuristic chain decides (the iron rule stays in force —
    execution / secret / exfil rules in HTML still fire).
    """
    if not file_path.lower().endswith((".html", ".htm")):
        return "unknown"

    # The only HTML FP this classifier owns is the SUPPLY_CHAIN CDN import.
    # A real remote-code load (eval-of-fetch, an unknown-host import, a
    # webhook exfil) is a DIFFERENT rule id, so it never reaches here.
    if rule_id != "SUPPLY_CHAIN":
        return "unknown"

    lines = content.splitlines()
    if not (0 <= line_idx < len(lines)):
        return "unknown"
    line = lines[line_idx]

    # Reuse the markdown classifier's reputable-CDN host allowlist + the
    # official-install-pipe allowlist (single source of truth — do NOT copy
    # the host list). A pinned ESM import from a reputable CDN, or the
    # canonical official-host install pipe, is the legitimate zero-install
    # delivery mechanism for a self-contained HTML artifact.
    try:
        from _skillaudit_markdown_context import (  # type: ignore[import-not-found]
            _is_known_cdn_import,
            _is_official_install_pipe,
        )
    except ImportError:
        return "unknown"

    if _is_known_cdn_import(line, match) or _is_official_install_pipe(line):
        return "safe_literal"

    return "unknown"
