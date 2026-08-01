"""Issue #185 §1 — per-platform delivery: what the shipped binaries cost every user.

The reporter measured a real install: 154 MB of `bin/` on a Darwin/arm64 host,
of which the host could execute 28.2 MB. 125.8 MB — 79% of the whole install —
was native code for platforms that machine can never run, while the canon's
source-stripping rule was optimising the 1% next to it.

These tests pin BOTH halves of that judgement, because a rule that only ever
fires is a slogan and one that only ever stays silent is decoration:

  - a plugin committing 5 platform variants FIRES `RC-PLATFORM-BLOAT`, with the
    MB and % arithmetic asserted against hand-computed numbers (the number IS
    the argument, so a wrong number is a wrong finding);
  - a fetch-on-install plugin, a single-platform plugin, and a source-only
    plugin all stay SILENT.

Every fixture pins `host_platform` explicitly. A measurement that changes with
the machine running the suite is not a measurement, and CI runs on Linux while
the reporter's case is macOS.

Non-vacuity is proven by NEUTERING the detector (`test_non_vacuity_*`): with
classification stubbed out, the positive assertions must fail to hold — which is
what makes the silent fixtures evidence rather than an empty query.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_platform_delivery as pd  # noqa: E402
from cpv_platform_delivery import (  # noqa: E402
    PLATFORM_BLOAT_MIN_VARIANTS,
    PLATFORM_BLOAT_UNUSABLE_FRACTION,
    analyse_platform_delivery,
    classify_binary_name,
    host_platform_name,
    verify_platform_delivery,
)

ELF = b"\x7fELF\x02\x01\x01\x00"
MAC_ARM = "macOS ARM64 (Apple Silicon)"
MAC_X86 = "macOS x86_64 (Intel)"
LINUX_X86 = "Linux x86_64"
KB = 1024

# The reporter's shape, scaled down: two tools x five targets. Sizes are chosen
# so the arithmetic is checkable by hand rather than by re-running the code.
# On a macOS-arm64 host the runnable pair is 100 + 60 = 160 KB.
FIVE_PLATFORM_SIZES: dict[str, int] = {
    "pss-darwin-arm64": 100,
    "pss-darwin-x86_64": 110,
    "pss-linux-arm64": 100,
    "pss-linux-x86_64": 130,
    "pss-windows-x86_64.exe": 120,
    "pss-nlp-darwin-arm64": 60,
    "pss-nlp-darwin-x86_64": 60,
    "pss-nlp-linux-arm64": 60,
    "pss-nlp-linux-x86_64": 60,
    "pss-nlp-windows-x86_64.exe": 60,
}


def _rules(findings: list[tuple[str, str]]) -> list[str]:
    return sorted(rule for rule, _ in findings)


def _plugin(tmp_path: Path, name: str = "plug") -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "description": "fixture", "version": "0.1.0"}),
        encoding="utf-8",
    )
    return root


def _binary(root: Path, name: str, size_kb: int) -> None:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    payload = ELF + b"\0" * (size_kb * KB - len(ELF))
    (bin_dir / name).write_bytes(payload)


def _five_platform_plugin(tmp_path: Path) -> Path:
    root = _plugin(tmp_path, "pss")
    for name, size_kb in FIVE_PLATFORM_SIZES.items():
        _binary(root, name, size_kb)
    (root / "bin" / "pss").write_text("#!/bin/sh\n# dispatcher\n", encoding="utf-8")
    return root


def _release_installer(root: Path) -> None:
    """A fetch-on-install installer: downloads a release asset AND verifies it.

    Uses `gh release download` rather than a curl pipeline so the fixture cannot
    be mistaken for the pipe-to-shell shape CPV's own scanners exist to catch.
    """
    (root / "install.sh").write_text(
        "#!/bin/sh\nset -eu\n"
        'gh release download "$TAG" --pattern "mytool-$OS-$ARCH"\n'
        'gh release download "$TAG" --pattern "SHA256SUMS"\n'
        'sha256sum -c SHA256SUMS || exit 1\n',
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# Classification — the naming convention, imported from validate_plugin
# --------------------------------------------------------------------------


def test_classify_splits_tool_from_platform() -> None:
    assert classify_binary_name("pss-nlp-darwin-x86_64") == (MAC_X86, "pss-nlp")
    assert classify_binary_name("pss-darwin-arm64") == (MAC_ARM, "pss")
    assert classify_binary_name("pss-linux-x86_64") == (LINUX_X86, "pss")


def test_classify_prefers_the_longest_tail_match() -> None:
    # `-darwin-universal` must not lose to a shorter key, and the `.exe` triple
    # must beat its bare prefix.
    assert classify_binary_name("pss-darwin-universal") == ("macOS Universal", "pss")
    assert classify_binary_name("pss-windows-x86_64.exe") == ("Windows x86_64", "pss")


def test_classify_mirrors_validate_plugin_bare_exe_fallback() -> None:
    # A bare `.exe` carries no platform suffix but is Windows-only beyond doubt;
    # validate_plugin classifies it as plain "Windows" and so does this.
    assert classify_binary_name("tool.exe") == ("Windows", "tool")


def test_classify_leaves_an_unrecognised_name_unattributed() -> None:
    # The two-sided half of the fallback: a `.wasm` is portable, so charging it
    # as foreign-platform waste would be a fabricated number.
    assert classify_binary_name("pss-wasm32.wasm") == (None, "pss-wasm32.wasm")
    assert classify_binary_name("helper") == (None, "helper")


def test_naming_convention_is_validate_plugins_not_a_local_copy() -> None:
    from validate_plugin import BINARY_PLATFORM_SUFFIXES

    for suffix, platform_name in BINARY_PLATFORM_SUFFIXES.items():
        assert classify_binary_name(f"tool{suffix}") == (platform_name, "tool")


def test_host_platform_name_is_a_known_identity_or_none() -> None:
    host = host_platform_name()
    assert host is None or host in {
        MAC_ARM,
        MAC_X86,
        LINUX_X86,
        "Linux ARM64",
        "Windows x86_64",
        "Windows ARM64",
    }


# --------------------------------------------------------------------------
# FIRES — the reporter's shape
# --------------------------------------------------------------------------


def test_five_platform_plugin_fires_platform_bloat(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    findings = verify_platform_delivery(root, host_platform=MAC_ARM)
    assert _rules(findings) == ["RC-PLATFORM-BLOAT"]


def test_five_platform_arithmetic_is_correct(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)

    total_kb = sum(FIVE_PLATFORM_SIZES.values())  # 860
    usable_kb = FIVE_PLATFORM_SIZES["pss-darwin-arm64"] + FIVE_PLATFORM_SIZES["pss-nlp-darwin-arm64"]  # 160
    unusable_kb = total_kb - usable_kb  # 700

    assert data["error"] is None
    assert data["platform_variants"] == 5
    assert data["total_bytes"] == total_kb * KB
    assert data["host_usable_bytes"] == usable_kb * KB
    assert data["unusable_bytes"] == unusable_kb * KB
    assert data["unclassified_bytes"] == 0
    assert data["unusable_fraction"] == unusable_kb / total_kb
    # The dispatcher is a script, not compiled output — it must not be priced.
    assert "bin/pss" not in [b["path"] for b in data["binaries"]]
    assert data["launchers"] == ["bin/pss"]
    assert sorted(data["families"]) == ["pss", "pss-nlp"]
    assert data["families"]["pss"]["bytes"] == 560 * KB


def test_bloat_message_carries_the_real_numbers(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    (_rule, message), = verify_platform_delivery(root, host_platform=MAC_ARM)
    # 700/860 = 81.4%. The number is the argument, so it must be IN the message.
    assert "81.4%" in message
    assert "5 platform variants" in message
    assert "SHA256SUMS" in message
    assert "UNVERIFIED" in message


def test_an_unclassified_binary_stays_in_the_total_but_never_in_the_waste(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    _binary(root, "pss-wasm32.wasm", 40)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["unclassified_bytes"] == 40 * KB
    assert data["total_bytes"] == (sum(FIVE_PLATFORM_SIZES.values()) + 40) * KB
    assert data["unusable_bytes"] == 700 * KB  # unchanged by the wasm


def test_the_priced_host_is_the_one_asked_for(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    on_linux = analyse_platform_delivery(root, host_platform=LINUX_X86)
    assert on_linux["host_usable_bytes"] == (130 + 60) * KB
    assert on_linux["unusable_bytes"] == (860 - 190) * KB


def test_committed_bloat_wins_over_a_release_installer(tmp_path: Path) -> None:
    # A plugin that has BOTH still makes every user carry every binary, so the
    # OK finding must not launder the committed payload.
    root = _five_platform_plugin(tmp_path)
    _release_installer(root)
    assert _rules(verify_platform_delivery(root, host_platform=MAC_ARM)) == ["RC-PLATFORM-BLOAT"]


# --------------------------------------------------------------------------
# STAYS SILENT — the three compliant / inapplicable shapes
# --------------------------------------------------------------------------


def test_fetch_on_install_plugin_does_not_fire_bloat(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "fetcher")
    (root / "bin").mkdir()
    (root / "bin" / "mytool").write_text("#!/bin/sh\n# dispatcher: fetch + verify\n", encoding="utf-8")
    _release_installer(root)

    findings = verify_platform_delivery(root, host_platform=MAC_ARM)
    assert _rules(findings) == ["RC-PLATFORM-DELIVERY-OK"]
    assert "RC-PLATFORM-BLOAT" not in _rules(findings)
    assert "bin/mytool" in findings[0][1]


def test_a_download_without_a_checksum_is_not_blessed(tmp_path: Path) -> None:
    # The two-sided half of DELIVERY-OK: an UNVERIFIED fetch is the one shape
    # that is strictly worse than committing the binary, so it earns no INFO.
    root = _plugin(tmp_path, "unverified")
    (root / "bin").mkdir()
    (root / "bin" / "mytool").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / "install.sh").write_text(
        '#!/bin/sh\ngh release download "$TAG" --pattern "mytool-$OS-$ARCH"\n',
        encoding="utf-8",
    )
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_single_platform_plugin_does_not_fire(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "single")
    _binary(root, "tool-linux-x86_64", 400)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    # Every committed byte IS unusable on this host, so the fraction alone would
    # fire — the variant floor is what keeps a one-target plugin silent.
    assert data["unusable_fraction"] == 1.0
    assert data["platform_variants"] == 1
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_two_platform_plugin_is_still_below_the_variant_floor(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "two")
    _binary(root, "tool-darwin-arm64", 100)
    _binary(root, "tool-linux-x86_64", 400)
    assert analyse_platform_delivery(root, host_platform=MAC_ARM)["platform_variants"] == 2
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_three_platforms_is_the_floor_not_an_off_by_one(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "three")
    _binary(root, "tool-darwin-arm64", 100)
    _binary(root, "tool-linux-x86_64", 100)
    _binary(root, "tool-windows-x86_64.exe", 100)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["platform_variants"] == PLATFORM_BLOAT_MIN_VARIANTS == 3
    assert _rules(verify_platform_delivery(root, host_platform=MAC_ARM)) == ["RC-PLATFORM-BLOAT"]


def test_three_platforms_mostly_usable_stays_below_the_fraction(tmp_path: Path) -> None:
    # The fraction gate is real, not decoration: 3 variants where the host can
    # run the overwhelming majority of the bytes is not bloat.
    root = _plugin(tmp_path, "skewed")
    _binary(root, "tool-darwin-arm64", 900)
    _binary(root, "tool-linux-x86_64", 50)
    _binary(root, "tool-windows-x86_64.exe", 50)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["platform_variants"] == 3
    assert data["unusable_fraction"] < PLATFORM_BLOAT_UNUSABLE_FRACTION
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_source_only_plugin_does_not_fire(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "srconly")
    (root / "src").mkdir()
    (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (root / "Cargo.toml").write_text('[package]\nname = "srconly"\n', encoding="utf-8")
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["total_bytes"] == 0
    assert data["binaries"] == []
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_scripts_only_bin_dir_does_not_fire(tmp_path: Path) -> None:
    root = _plugin(tmp_path, "scripts")
    (root / "bin").mkdir()
    (root / "bin" / "tool").write_text("#!/usr/bin/env python3\nprint('hi')\n", encoding="utf-8")
    assert analyse_platform_delivery(root, host_platform=MAC_ARM)["binaries"] == []
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


# --------------------------------------------------------------------------
# Fail-safe — a scan that did not complete reports nothing
# --------------------------------------------------------------------------


def test_an_unknown_host_reports_nothing_and_fabricates_no_waste(tmp_path: Path) -> None:
    root = _five_platform_plugin(tmp_path)
    data = analyse_platform_delivery(root, host_platform=None)
    assert data["host_platform"] in (None, host_platform_name())
    if data["host_platform"] is None:
        assert data["unusable_bytes"] == 0
        assert verify_platform_delivery(root, host_platform=None) == []


def test_unknown_host_is_priced_as_zero_waste(tmp_path: Path, monkeypatch) -> None:
    root = _five_platform_plugin(tmp_path)
    monkeypatch.setattr(pd, "host_platform_name", lambda: None)
    data = analyse_platform_delivery(root)
    assert data["host_platform"] is None
    assert data["total_bytes"] > 0
    assert data["unusable_bytes"] == 0
    assert data["host_usable_bytes"] == 0
    assert verify_platform_delivery(root) == []


def test_an_io_error_yields_no_finding(tmp_path: Path, monkeypatch) -> None:
    root = _five_platform_plugin(tmp_path)

    def _boom(_root: Path) -> list[Path]:
        raise OSError("disk went away")

    monkeypatch.setattr(pd, "iter_shipped_binaries", _boom)
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["error"] is not None and "OSError" in data["error"]
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_a_missing_plugin_root_yields_no_finding(tmp_path: Path) -> None:
    assert verify_platform_delivery(tmp_path / "nope", host_platform=MAC_ARM) == []


# --------------------------------------------------------------------------
# NON-VACUITY — neuter the detector and the positive assertions must collapse
# --------------------------------------------------------------------------


def test_non_vacuity_neutered_classifier_stops_the_bloat_finding(tmp_path: Path, monkeypatch) -> None:
    """With classification stubbed to 'unrecognised', the 5-platform fixture goes silent.

    That is what makes the silent fixtures above evidence: they are silent
    because of what they SHIP, not because the detector never speaks.
    """
    root = _five_platform_plugin(tmp_path)
    assert _rules(verify_platform_delivery(root, host_platform=MAC_ARM)) == ["RC-PLATFORM-BLOAT"]

    monkeypatch.setattr(pd, "classify_binary_name", lambda name, suffixes=None: (None, name))
    data = analyse_platform_delivery(root, host_platform=MAC_ARM)
    assert data["platform_variants"] == 0
    assert data["unusable_bytes"] == 0
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_non_vacuity_neutered_installer_probe_stops_the_ok_finding(tmp_path: Path, monkeypatch) -> None:
    root = _plugin(tmp_path, "fetcher2")
    (root / "bin").mkdir()
    (root / "bin" / "mytool").write_text("#!/bin/sh\n", encoding="utf-8")
    _release_installer(root)
    assert _rules(verify_platform_delivery(root, host_platform=MAC_ARM)) == ["RC-PLATFORM-DELIVERY-OK"]

    monkeypatch.setattr(pd, "_has_release_asset_installer", lambda _root: False)
    assert verify_platform_delivery(root, host_platform=MAC_ARM) == []


def test_non_vacuity_the_fixture_helper_actually_writes_compiled_artifacts(tmp_path: Path) -> None:
    """Guard the guard: if `_binary` stopped producing detectable artifacts every
    'does not fire' test above would pass while proving nothing."""
    root = _five_platform_plugin(tmp_path)
    from cpv_binary_attestation import iter_shipped_binaries

    shipped = {p.name for p in iter_shipped_binaries(root)}
    assert shipped == set(FIVE_PLATFORM_SIZES)
