"""v5.0.0 — the ship-only-binary canon becomes UNIVERSAL.

Through v4.3.0 the strict escalation required a `cpv.canon: ship-only-binary`
opt-in, so almost nothing was enforced: the plugins most in need of migrating
were exactly the ones that never opted in. The owner's directive is that the
whole fleet aligns on the canon, so enforcement is now the default and
`cpv.canon: none` is the explicit, greppable opt-OUT.

Ordering was the whole design decision and is worth restating: enforcing "ship
only the binary" BEFORE `cpv.attest[]` existed would have forced the fleet to
ship binaries nothing could tie to a source revision. An opaque blob is a worse
outcome than shipped source. v4.3.0 shipped the record; this release turns on
the requirement that makes it checkable.

The asymmetry in `ship_canon_opted_out` is the security-relevant part: a
malformed or unreadable manifest returns False, i.e. ENFORCED. Under an opt-in
a broken manifest degraded to "advisory"; under a mandatory canon it must
degrade to "enforced", or a corrupt manifest would buy an exemption.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_pipeline_profile import (  # noqa: E402
    opts_into_ship_only_binary_canon,
    ship_canon_opted_out,
)


def _plugin(tmp_path: Path, *, canon: str | None = None, compiled: bool = True) -> Path:
    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest: dict = {
        "name": "canon-fixture",
        "description": "fixture for the universal ship-only-binary canon",
        "version": "0.1.0",
        "author": {"name": "T", "email": "t@example.invalid"},
    }
    if canon is not None:
        manifest["cpv"] = {"canon": canon}
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if compiled:
        (root / "src").mkdir()
        (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        (root / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
        (root / "bin").mkdir()
        for name in ("x-darwin-arm64", "x-linux-x86_64", "x-windows-x86_64.exe"):
            (root / "bin" / name).write_bytes(b"\x7fELF fake compiled output\n")
    return root


# ------------------------------------------------------- the gate helper itself


def test_absent_cpv_block_is_enforced(tmp_path: Path) -> None:
    """The default flips: no declaration now means ENFORCED, not exempt."""
    assert ship_canon_opted_out(_plugin(tmp_path, canon=None)) is False


def test_explicit_none_is_the_opt_out(tmp_path: Path) -> None:
    assert ship_canon_opted_out(_plugin(tmp_path, canon="none")) is True


def test_legacy_opt_in_is_still_enforced_and_still_recognised(tmp_path: Path) -> None:
    """An early adopter's declaration must not read as an opt-OUT."""
    root = _plugin(tmp_path, canon="ship-only-binary")
    assert ship_canon_opted_out(root) is False
    assert opts_into_ship_only_binary_canon(root) is True


def test_malformed_canon_value_is_enforced(tmp_path: Path) -> None:
    """A typo must not buy an exemption."""
    assert ship_canon_opted_out(_plugin(tmp_path, canon="ship-only-binaries")) is False


def test_unreadable_manifest_is_enforced(tmp_path: Path) -> None:
    """The security-relevant asymmetry: fail-safe now means ENFORCED."""
    assert ship_canon_opted_out(tmp_path / "does-not-exist") is False


def test_non_dict_cpv_block_is_enforced(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "x", "description": "y", "version": "0.1.0", "cpv": "none"}),
        encoding="utf-8",
    )
    assert ship_canon_opted_out(root) is False


# ------------------------------------------------------------ end-to-end verdict


def _validate(root: Path) -> dict:
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_plugin.py"), str(root), "--json"],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PLUGIN_SKIP_GITHUB_INTEGRITY": "1",
            "CPV_SCAN_CACHE": "0",
            "PYTHONPATH": str(SCRIPTS),
        },
    )
    start = res.stdout.find('{\n  "exit_code"')
    if start == -1:
        start = res.stdout.find("{")
    try:
        report = json.loads(res.stdout[start:])
    except (json.JSONDecodeError, ValueError) as exc:  # pragma: no cover
        pytest.fail(f"no parseable JSON (rc={res.returncode}): {exc}\n{res.stdout[-400:]}")
    assert report.get("results"), "empty report — any absence assertion would be vacuous"
    return report


def _levels_for(report: dict, needle: str) -> list[str]:
    return [str(f.get("level", "")) for f in report["results"] if needle in str(f.get("message", ""))]


def test_unattested_binary_blocks_by_default(tmp_path: Path) -> None:
    """The 4.3.0 WARNING becomes a blocking MAJOR."""
    levels = _levels_for(_validate(_plugin(tmp_path)), "RC-ATTEST-MISSING")
    assert levels, "fixture ships binaries, so the rule must fire at all"
    assert set(levels) == {"MAJOR"}


def test_in_tree_source_blocks_by_default(tmp_path: Path) -> None:
    assert _levels_for(_validate(_plugin(tmp_path)), "RC-SHIP-BINARY-ONLY-STRICT") == ["MAJOR"]


def test_opt_out_keeps_findings_visible_but_advisory(tmp_path: Path) -> None:
    """The opt-out withholds the BLOCK, never the finding."""
    report = _validate(_plugin(tmp_path, canon="none"))
    attest = _levels_for(report, "RC-ATTEST-MISSING")
    assert attest, "the finding must still be reported under an opt-out"
    assert set(attest) == {"WARNING"}
    assert not _levels_for(report, "RC-SHIP-BINARY-ONLY-STRICT")


def test_opt_out_is_announced_not_silent(tmp_path: Path) -> None:
    """A declared exception must appear in the report, or it is a silent pass."""
    assert _levels_for(_validate(_plugin(tmp_path, canon="none")), "RC-SHIP-BINARY-ONLY-OPTOUT") == ["WARNING"]


def test_legacy_declaration_is_reported_as_redundant(tmp_path: Path) -> None:
    report = _validate(_plugin(tmp_path, canon="ship-only-binary"))
    assert _levels_for(report, "RC-SHIP-BINARY-ONLY-DECLARED") == ["INFO"]
    assert set(_levels_for(report, "RC-ATTEST-MISSING")) == {"MAJOR"}


def test_source_only_plugin_is_untouched(tmp_path: Path) -> None:
    """A plugin shipping no compiled artifact must not be dragged into the canon."""
    report = _validate(_plugin(tmp_path, compiled=False))
    assert not _levels_for(report, "RC-ATTEST-MISSING")
    assert not _levels_for(report, "RC-SHIP-BINARY-ONLY-STRICT")


def test_escalated_message_does_not_claim_a_declaration_that_does_not_exist(tmp_path: Path) -> None:
    """The v4 message said "the plugin declares cpv.canon" — under a universal
    canon that is simply false, and a finding that misstates its own trigger
    sends the author to fix the wrong thing."""
    report = _validate(_plugin(tmp_path))
    msgs = [str(f.get("message", "")) for f in report["results"] if "RC-SHIP-BINARY-ONLY-STRICT" in str(f.get("message", ""))]
    assert msgs
    assert "declares cpv.canon: ship-only-binary" not in msgs[0]
    assert "cpv.canon: none" in msgs[0], "the finding must name the escape hatch"
