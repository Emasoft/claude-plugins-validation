"""Regression tests for issues #96 + #99 — broken-backtick-path false positives.

``validate_md_file_paths`` flagged BARE backtick code-spans whose top path
segment is not a known plugin-internal prefix as a "Possible broken backtick
path" WARNING. But a bare ```lib/x.ts``` (cross-repo source pointer),
```design/requirements/index.json``` (runtime-output path), or
```design/pdr/GUUID-…md``` (template/example filename) is ambiguous
prose documentation, NOT an in-repo link.

Fix: the ``else`` WARNING branch now warns ONLY when the backtick path carries
explicit relative-link intent (a leading ``./`` or ``../``). A bare cross-repo /
output / placeholder token is left unflagged.

This is TWO-SIDED. UNTOUCHED and still firing:
* the markdown-LINK loop (``[x](./does-not-exist.md)`` → MINOR ``Broken file
  reference``);
* the ``is_plugin_internal`` MINOR branch (```scripts/not-here.py```
  whose top segment IS a plugin prefix → MINOR ``Broken backtick path``);
* a deliberate-relative backtick link (```./also-missing.md``` →
  WARNING).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    validate_md_file_paths,
)


def _make_plugin(tmp_path: Path) -> Path:
    """A minimal valid plugin with an ``agents/router.md`` to write into."""
    plugin = tmp_path / "demo-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "demo-plugin", "version": "0.1.0", "description": "Backtick xref fixture"}\n',
        encoding="utf-8",
    )
    (plugin / "agents").mkdir(parents=True)
    return plugin


def _run(tmp_path: Path, body: str) -> ValidationReport:
    """Write ``agents/router.md`` with ``body`` and validate its paths."""
    plugin = _make_plugin(tmp_path)
    router = plugin / "agents" / "router.md"
    router.write_text(
        "---\nname: router\ndescription: A router agent for the test.\n---\n# router\n" + body,
        encoding="utf-8",
    )
    report = ValidationReport()
    validate_md_file_paths(md_file=router, plugin_root=plugin, report=report)
    return report


def _backtick_warnings(report: ValidationReport, needle: str) -> list:
    return [
        r
        for r in report.results
        if r.level == "WARNING" and "Possible broken backtick path" in r.message and needle in r.message
    ]


# ============================================================================
# (a) FPs must CLEAR — bare prose code-spans
# ============================================================================


class TestBareRefsNotFlagged:
    """Bare cross-repo / runtime-output / template code-spans must NOT warn."""

    def test_bare_cross_repo_ref_not_flagged(self, tmp_path: Path) -> None:
        """#99 — a cross-repo source pointer ```lib/communication-graph.ts```
        in prose is not an in-repo link → no WARNING."""
        report = _run(tmp_path, "This mirrors the server graph (`lib/communication-graph.ts`).\n")
        assert not _backtick_warnings(report, "communication-graph"), (
            f"bare cross-repo ref must not warn: {[r.message for r in report.results]}"
        )

    def test_runtime_output_path_not_flagged(self, tmp_path: Path) -> None:
        """#96 — a runtime-output path ```design/requirements/index.json```
        is not an in-repo link → no WARNING."""
        report = _run(tmp_path, "The design index is written at runtime to `design/requirements/index.json`.\n")
        assert not _backtick_warnings(report, "index.json"), (
            f"runtime-output path must not warn: {[r.message for r in report.results]}"
        )

    def test_template_example_filename_not_flagged(self, tmp_path: Path) -> None:
        """#96 — a template/example filename
        ```design/pdr/GUUID-20250129-0001-feature.md``` is not an
        in-repo link → no WARNING."""
        report = _run(
            tmp_path,
            "A created document follows the `design/pdr/GUUID-20250129-0001-feature.md` naming convention.\n",
        )
        assert not _backtick_warnings(report, "GUUID"), (
            f"template-example filename must not warn: {[r.message for r in report.results]}"
        )


# ============================================================================
# (b) Real broken refs must STILL fire
# ============================================================================


class TestRealBrokenRefsStillFire:
    """The untouched branches still surface genuine broken references."""

    def test_broken_md_link_still_flagged(self, tmp_path: Path) -> None:
        """A broken markdown LINK ``[missing doc](./does-not-exist.md)`` STILL
        fires MINOR ``Broken file reference`` (md-link loop, unchanged)."""
        report = _run(tmp_path, "See [missing doc](./does-not-exist.md) for details.\n")
        hits = [r for r in report.results if "Broken file reference" in r.message and "does-not-exist" in r.message]
        assert hits, f"broken md link must still fire: {[r.message for r in report.results]}"

    def test_relative_backtick_link_still_flagged(self, tmp_path: Path) -> None:
        """A deliberate-relative backtick link ```./also-missing.md```
        STILL warns — the author clearly meant an in-repo link."""
        report = _run(tmp_path, "Also read `./also-missing.md` before proceeding.\n")
        assert _backtick_warnings(report, "also-missing"), (
            f"relative backtick link must still warn: {[r.message for r in report.results]}"
        )

    def test_plugin_internal_backtick_still_minor(self, tmp_path: Path) -> None:
        """A plugin-internal backtick path ```scripts/not-here.py```
        (top segment IS a plugin prefix) STILL fires MINOR ``Broken backtick
        path`` (is_plugin_internal branch, unchanged)."""
        report = _run(tmp_path, "Run the helper at `scripts/not-here.py` first.\n")
        hits = [
            r
            for r in report.results
            if r.level == "MINOR" and "Broken backtick path" in r.message and "not-here" in r.message
        ]
        assert hits, f"plugin-internal broken backtick path must still fire MINOR: {[r.message for r in report.results]}"
