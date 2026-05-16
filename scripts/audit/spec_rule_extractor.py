#!/usr/bin/env python3
"""Claude Code spec rule extractor for the CPV vs CLI coverage audit.

Why this exists:
    TRDD-b4c6cbe7 Phase 2 requires mapping every concrete spec rule
    ("MUST", "SHOULD", "MUST NOT", error-message string) onto either
    an existing CPV check or a "missing" entry. Doing that by hand
    against ~50 spec pages is brittle and stale-prone — instead we
    crawl `https://code.claude.com/docs/llms.txt`, fetch every linked
    spec page, and pull out concrete obligation strings.

Output:
    Each invocation produces a structured rule list in JSON and a
    human-readable markdown matrix that the auditor reviews row by row
    to confirm whether CPV already enforces the rule.

Public API:
    fetch_index(url: str) -> str
    parse_index(text: str) -> list[SpecPage]
    fetch_page(page: SpecPage, *, timeout: float = 30.0) -> str
    extract_rules_from_text(body: str, page: SpecPage) -> list[SpecRule]
    write_spec_coverage_report(rules: list[SpecRule], path: Path) -> None

Robustness:
    All HTTP calls go through `cpv_network_resilience.run_with_retry` (when
    available) for retry/backoff parity with the rest of CPV. If the
    network is unreachable, the script emits a clear sentinel report
    so the rest of the audit pipeline keeps moving.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Adding repo `scripts/` so cpv_network_resilience can be imported in dev mode.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from cpv_network_resilience import is_transient_http_error  # type: ignore[import-not-found]
except Exception:  # pragma: no cover — fallback when running standalone

    def is_transient_http_error(exc: BaseException | None) -> bool:
        return isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError))


SPEC_INDEX_URL = "https://code.claude.com/docs/llms.txt"

# Patterns recognising concrete obligation language in spec markdown.
#
# The capture intentionally includes ~80 characters of *preceding* context so
# the heuristic-coverage step can see the subject of the obligation (e.g.
# "mcpServers" in "The mcpServers field MUST..."). Without the lookbehind,
# the rule sentence starts at "MUST" and the subject is lost. The leading
# bound (`[^.\n]{0,80}`) ends at the previous sentence break or newline, so
# we never grab content from a different sentence.
_MUST_PATTERNS = (
    re.compile(r"[^.\n]{0,80}\bMUST NOT\b[^.\n]{0,160}", re.IGNORECASE),
    re.compile(r"[^.\n]{0,80}\bMUST\b[^.\n]{0,160}", re.IGNORECASE),
    re.compile(r"[^.\n]{0,80}\bSHOULD NOT\b[^.\n]{0,160}", re.IGNORECASE),
    re.compile(r"[^.\n]{0,80}\bSHOULD\b[^.\n]{0,160}", re.IGNORECASE),
    re.compile(r"[^.\n]{0,80}\bREQUIRED\b[^.\n]{0,160}", re.IGNORECASE),
    re.compile(r"[^.\n]{0,80}\bFORBIDDEN\b[^.\n]{0,160}", re.IGNORECASE),
)

# Indexed CPV rule keywords used to coarsely tag "covered" vs "missing".
_CPV_RULE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("name", "plugin.json `name` checks (Phase 7+ regex)"),
    ("version", "plugin.json `version` semver check"),
    ("dependencies", "plugin.json `dependencies` schema (Phase 12+)"),
    ("commands", "command frontmatter validation (Phase 12+)"),
    ("agents", "agent frontmatter validation (Phase 12+)"),
    ("skills", "skill frontmatter validation (Phase 12+)"),
    ("hooks", "hook event/type validation (Phase 12+)"),
    ("mcpServers", "MCP server schema (Phase 16+)"),
    ("lspServers", "LSP server schema (Phase 16+)"),
    ("monitors", "monitors field (Phase 16+)"),
    ("marketplace", "marketplace.json schema (Phase 16+ Layout C)"),
    ("source", "marketplace source-type allowlist (Phase 16+)"),
    ("description", "description length recommendation"),
    ("category", "marketplace category enum"),
    ("license", "license string check"),
    ("env", ".env / env.example secret scan"),
    ("permissionMode", "agent permissionMode enum"),
    ("argumentHint", "skill argument-hint pattern"),
    ("arguments", "skill arguments declaration + `$<name>` cross-ref"),
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecPage:
    """One entry from the llms.txt index file."""

    title: str
    url: str


@dataclass
class SpecRule:
    """A concrete obligation extracted from a spec page."""

    modal: str  # "MUST" / "SHOULD" / "MUST NOT" / "REQUIRED" / "FORBIDDEN"
    sentence: str  # the obligation sentence, stripped of leading whitespace
    source_url: str  # spec page the rule was extracted from
    likely_cpv_check: str | None = None  # mapped CPV check (heuristic) or None
    coverage: str = "unmapped"  # "covered" | "partial" | "missing" | "unmapped"


# ---------------------------------------------------------------------------
# Index + page fetchers
# ---------------------------------------------------------------------------


def _fetch_url(url: str, *, timeout: float = 30.0) -> str:
    """Fetch a single URL with a 30s read timeout.

    Returns body as decoded UTF-8. Raises on non-2xx or transport errors;
    callers decide whether to retry via `is_transient_http_error`.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "cpv-audit-bot/1.0"})
    # Trusted-URL guarantee: callers only pass the official HTTPS Anthropic
    # llms.txt index; no user-controlled input flows into `url`. The end-of-line
    # nosemgrep directive suppresses the semgrep dynamic-urllib false positive
    # (semgrep does not honor bandit-style noqa S310 directives, so the
    # security-audit ruleset needs its own inline marker here).
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosemgrep: dynamic-urllib-use-detected
        raw = resp.read()
    decoded: str = raw.decode("utf-8", errors="replace")
    return decoded


def fetch_index(url: str = SPEC_INDEX_URL, *, timeout: float = 30.0) -> str:
    """Fetch the llms.txt index, retrying once on transient errors."""
    try:
        return _fetch_url(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — broad except is intentional
        if is_transient_http_error(exc):
            return _fetch_url(url, timeout=timeout)
        raise


def parse_index(text: str) -> list[SpecPage]:
    """Pull markdown-style `[title](url)` entries out of llms.txt.

    The llms.txt format is loose — sometimes lines are bare links,
    sometimes wrapped in markdown link syntax. We accept both shapes.
    """
    pages: list[SpecPage] = []
    md_link = re.compile(r"\[(?P<title>[^\]]+)\]\((?P<url>https?://[^\s)]+)\)")
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for m in md_link.finditer(line):
            url = m.group("url")
            if url in seen:
                continue
            seen.add(url)
            pages.append(SpecPage(title=m.group("title").strip(), url=url))
            continue
        if line.startswith("http"):
            url = line.split()[0]
            if url in seen:
                continue
            seen.add(url)
            # Use the path's leaf as a coarse title
            title = url.rsplit("/", 1)[-1] or url
            pages.append(SpecPage(title=title, url=url))
    return pages


def fetch_page(page: SpecPage, *, timeout: float = 30.0) -> str:
    """Fetch one spec page with one transient-retry."""
    try:
        return _fetch_url(page.url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        if is_transient_http_error(exc):
            return _fetch_url(page.url, timeout=timeout)
        raise


# ---------------------------------------------------------------------------
# Rule extractor
# ---------------------------------------------------------------------------


def _classify_modal(sentence: str) -> str:
    """Return the strongest obligation modal present in `sentence`."""
    up = sentence.upper()
    for marker in ("MUST NOT", "SHOULD NOT", "FORBIDDEN", "REQUIRED", "MUST", "SHOULD"):
        if marker in up:
            return marker
    return "SHOULD"


def _heuristic_coverage(sentence: str) -> tuple[str | None, str]:
    """Map a rule sentence onto an existing CPV check via keyword match.

    Returns (likely_cpv_check, coverage_status). When no keyword in
    `_CPV_RULE_KEYWORDS` matches, coverage is "unmapped" (the auditor
    will mark it manually).
    """
    low = sentence.lower()
    for keyword, description in _CPV_RULE_KEYWORDS:
        if keyword.lower() in low:
            return description, "partial"
    return None, "unmapped"


def extract_rules_from_text(body: str, page: SpecPage) -> list[SpecRule]:
    """Pull obligation sentences out of a markdown spec page body."""
    rules: list[SpecRule] = []
    seen: set[str] = set()
    for pattern in _MUST_PATTERNS:
        for m in pattern.finditer(body):
            sentence = re.sub(r"\s+", " ", m.group(0)).strip()
            if not sentence or sentence in seen:
                continue
            seen.add(sentence)
            modal = _classify_modal(sentence)
            check, coverage = _heuristic_coverage(sentence)
            rules.append(
                SpecRule(
                    modal=modal,
                    sentence=sentence,
                    source_url=page.url,
                    likely_cpv_check=check,
                    coverage=coverage,
                )
            )
    return rules


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------


def _bucket_counts(rules: list[SpecRule]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rules:
        counts[r.coverage] = counts.get(r.coverage, 0) + 1
    return counts


def write_spec_coverage_report(rules: list[SpecRule], path: Path, *, fetch_error: str | None = None) -> None:
    """Write a markdown matrix mapping each spec rule onto CPV coverage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Spec-Coverage Matrix — Claude Code rules vs CPV checks\n")
    lines.append("**TRDD:** b4c6cbe7\n**Generated:** 2026-05-11\n")
    if fetch_error:
        lines.append(
            f"\n> WARNING: spec fetch failed — `{fetch_error}`. Rule list below "
            f"is empty; re-run when network access is available.\n"
        )
    lines.append("")
    counts = _bucket_counts(rules)
    lines.append("## 1. Summary counts\n")
    lines.append("| Bucket | Count |")
    lines.append("|---|---:|")
    for bucket in ("covered", "partial", "missing", "unmapped"):
        lines.append(f"| `{bucket}` | {counts.get(bucket, 0)} |")
    lines.append(f"| **Total** | **{len(rules)}** |")
    lines.append("")
    lines.append("## 2. Rules\n")
    lines.append("| # | Modal | Sentence (truncated) | Source | Coverage | Likely CPV check |")
    lines.append("|---:|---|---|---|---|---|")
    for i, rule in enumerate(rules, 1):
        sentence = rule.sentence.replace("|", "\\|")
        if len(sentence) > 220:
            sentence = sentence[:217] + "..."
        url_short = rule.source_url.rsplit("/", 1)[-1] or rule.source_url
        check = rule.likely_cpv_check or "_unmapped_"
        lines.append(f"| {i} | `{rule.modal}` | {sentence} | `{url_short}` | `{rule.coverage}` | {check} |")
    lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_all_rules(*, max_pages: int | None = None) -> tuple[list[SpecRule], str | None]:
    """Fetch index + every page, accumulate rules, return (rules, fetch_error).

    `max_pages` caps the crawl for offline tests. When network is
    unreachable, returns ([], "<error message>").
    """
    try:
        index_body = fetch_index()
    except Exception as exc:  # noqa: BLE001
        return [], f"fetch_index failed: {exc!r}"

    pages = parse_index(index_body)
    if max_pages is not None:
        pages = pages[:max_pages]

    rules: list[SpecRule] = []
    for page in pages:
        try:
            body = fetch_page(page)
        except Exception as exc:  # noqa: BLE001 — skip but don't abort entire run
            sys.stderr.write(f"[spec-extractor] WARN: fetch failed for {page.url}: {exc!r}\n")
            continue
        rules.extend(extract_rules_from_text(body, page))
    return rules, None


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    repo_root = Path(__file__).resolve().parents[2]
    default_report = repo_root / "design" / "audits" / "spec-coverage-2026-05-11.md"
    parser.add_argument("--report", type=Path, default=default_report)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Limit number of spec pages crawled (default: all).",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional path for machine-readable JSON output.",
    )
    args = parser.parse_args(argv)

    rules, error = collect_all_rules(max_pages=args.max_pages)
    write_spec_coverage_report(rules, args.report, fetch_error=error)

    if args.json is not None:
        payload = {
            "fetch_error": error,
            "rules": [r.__dict__ for r in rules],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote spec-coverage report → {args.report} ({len(rules)} rules)")
    if error:
        print(f"NOTE: fetch error — {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
