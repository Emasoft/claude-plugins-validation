#!/usr/bin/env python3
"""Per-platform delivery — measure what a compiled plugin makes every user carry.

Issue #185 §1, filed by the perfect-skill-suggester Claude against the
ship-only-binary canon, with numbers from the plugin that is the canon's own
test case:

    Installed perfect-skill-suggester 3.10.10 on a Darwin/arm64 host.
    Total install 160 MB; bin/ 154 MB (96%); rust/ source 1.7 MB (1.06%).
    This host can execute exactly two of the eleven binaries (28.2 MB).
    125.8 MB — 79% of the entire install — is native code for platforms
    this machine can never run.

So stripping the source saves ~1%, while the multi-platform binary payload the
canon mandates costs ~79%. The canon optimised the wrong axis by roughly two
orders of magnitude, and nothing in CPV could see it: the platform-coverage
check asks whether ENOUGH platforms are covered, never what covering them all
costs the user who can use exactly one.

This module answers the second question. It groups the binaries a plugin ships
into `<tool>-<os>-<arch>` families, prices each family against the HOST, and
reports the fraction of the shipped binary payload the host can never execute.

TWO COMPLIANT SHAPES, and the finding names both (see
`skills/cpv-canonical-pipeline/references/per-platform-delivery.md`):

  - **fetch-on-install** — ship a small dispatcher; the GitHub release carries
    the per-platform assets plus `SHA256SUMS`; the installer downloads only the
    host's binary and VERIFIES it. Smallest install, needs a network at install.
  - **commit-all-platforms** — today's shape. Works offline; costs every user
    every platform.

Neither is a security downgrade PROVIDED the checksum is verified. An
UNVERIFIED download would be strictly worse than committing the binary — it
replaces an artifact a reviewer can hash and pin with one fetched at install
time from a mutable remote — which is why `RC-PLATFORM-DELIVERY-OK` is keyed on
`validate_plugin._has_release_asset_installer`, whose whole point is that it
requires the download AND the checksum step in the same installer.

SEVERITY, and why WARNING: committing every platform is a legitimate,
offline-correct choice, and the canon told authors to make it. A rule that
blocked it would retro-break every compiled plugin in the fleet for doing what
CPV documented (project rule #170). The number is the argument, so the message
carries the real MB and %, and the reader decides.

WHAT IT DOES NOT CLAIM. It prices bytes against ONE host — the machine running
the scan. It is not a statement about the fleet, and a binary it cannot
classify from the shipped name is counted in the total but NEVER in the
unusable numerator, so the reported waste is a floor, not a ceiling.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Final

from cpv_binary_attestation import iter_shipped_binaries

__all__ = [
    "PLATFORM_BLOAT_MIN_VARIANTS",
    "PLATFORM_BLOAT_UNUSABLE_FRACTION",
    "analyse_platform_delivery",
    "classify_binary_name",
    "host_platform_name",
    "verify_platform_delivery",
]

# A plugin shipping one or two platform variants is making a small, defensible
# bet. At three the payload is majority-dead on every host by construction, and
# the delivery question becomes worth asking.
PLATFORM_BLOAT_MIN_VARIANTS: Final[int] = 3

# Three EQUAL variants already put 2/3 of the payload out of reach, so 0.5 is
# below the floor that ≥3 variants can produce — it fires on the shape the issue
# is about while staying a measurement rather than a slogan. Deliberately NO
# absolute-size floor: a floor would silently exempt a plugin whose dead payload
# happens to be small, and the finding never blocks, so the honest move is to
# report the number and let the reader weigh it.
PLATFORM_BLOAT_UNUSABLE_FRACTION: Final[float] = 0.5

# Platform identities as `validate_plugin` spells them, used to decide whether a
# classified binary matches THIS host. These are the VALUES of
# `BINARY_PLATFORM_SUFFIXES`, not a second naming convention: `-darwin-arm64`
# and `-macos-arm64` are two spellings of one platform, and the value string is
# what makes them one.
_MACOS_ARM: Final[str] = "macOS ARM64 (Apple Silicon)"
_MACOS_X86: Final[str] = "macOS x86_64 (Intel)"
_MACOS_UNIVERSAL: Final[str] = "macOS Universal"
_LINUX_ARM: Final[str] = "Linux ARM64"
_LINUX_X86: Final[str] = "Linux x86_64"
_WINDOWS_ARM: Final[str] = "Windows ARM64"
_WINDOWS_X86: Final[str] = "Windows x86_64"
# `validate_plugin` classifies a bare `.exe` that carries no platform suffix as
# plain "Windows" (validate_cross_platform, the `elif item.suffix == ".exe"`
# branch). Mirrored here rather than invented: a bare `.exe` is Windows-only
# beyond doubt, and leaving it unclassified would under-report the waste on
# every non-Windows host.
_WINDOWS_ANY: Final[str] = "Windows"

_ARM_MACHINES: Final[frozenset[str]] = frozenset({"arm64", "aarch64", "armv8", "armv8l"})
_X86_MACHINES: Final[frozenset[str]] = frozenset({"x86_64", "amd64", "x64", "i386", "i686"})


def _platform_suffixes() -> dict[str, str]:
    """The `<tool>-<os>-<arch>` naming convention, IMPORTED from `validate_plugin`.

    IMPORTED, not mirrored, and the import is deliberately LAZY. The convention
    is one fact; a second copy here would drift the first time a target triple
    is added, and a drifted copy of a classifier under-reports silently. The
    laziness is what makes importing safe in both directions: `validate_plugin`
    wires this module in the same way it wires `cpv_binary_attestation` (a
    function-local import), so neither module can deadlock the other at import
    time regardless of which is loaded first.
    """
    from validate_plugin import BINARY_PLATFORM_SUFFIXES  # noqa: PLC0415

    return dict(BINARY_PLATFORM_SUFFIXES)


def _has_release_asset_installer(plugin_root: Path) -> bool:
    """Reuse `validate_plugin`'s fetch-on-install detector — never a second copy.

    It already encodes the property that matters: a release-asset DOWNLOAD and a
    sha256 VERIFY step in the SAME installer. Re-deriving it here would risk a
    copy that accepts a download without its checksum, which is the one shape
    this module must never bless.
    """
    from validate_plugin import _has_release_asset_installer as _impl  # noqa: PLC0415

    return bool(_impl(plugin_root))


def host_platform_name() -> str | None:
    """This host's platform identity, or None when it cannot be determined.

    None is load-bearing: without a host there is no such thing as an unusable
    byte, so the caller reports NOTHING rather than guessing. Claiming waste
    against an unknown host would be a fabricated measurement.
    """
    system = platform.system().strip().lower()
    machine = platform.machine().strip().lower()
    if system == "darwin":
        if machine in _ARM_MACHINES:
            return _MACOS_ARM
        return _MACOS_X86 if machine in _X86_MACHINES else None
    if system == "linux":
        if machine in _ARM_MACHINES:
            return _LINUX_ARM
        return _LINUX_X86 if machine in _X86_MACHINES else None
    if system == "windows":
        if machine in _ARM_MACHINES:
            return _WINDOWS_ARM
        return _WINDOWS_X86 if machine in _X86_MACHINES else None
    return None


def _host_runnable(host: str) -> frozenset[str]:
    """Platform identities THIS host can execute.

    A universal macOS binary runs on either Mac arch, and a bare-`.exe`
    "Windows" binary runs on a Windows host — everything else is an exact match.
    """
    if host in (_MACOS_ARM, _MACOS_X86):
        return frozenset({host, _MACOS_UNIVERSAL})
    if host in (_WINDOWS_ARM, _WINDOWS_X86):
        return frozenset({host, _WINDOWS_ANY})
    return frozenset({host})


def classify_binary_name(name: str, suffixes: dict[str, str] | None = None) -> tuple[str | None, str]:
    """Split a shipped binary's filename into (platform identity, tool name).

    `pss-nlp-darwin-x86_64` -> ("macOS x86_64 (Intel)", "pss-nlp"). The longest
    matching suffix wins, and a match at the very END of the name beats one in
    the middle, so `-darwin-x86_64` is never mistaken for `-darwin-universal`
    and a trailing `.exe` triple is preferred over its bare prefix.

    Returns (None, name) when the name carries no recognised platform — an
    unclassified binary (a `.wasm`, a hand-named artifact) is genuinely NOT
    attributable to a foreign platform, so it must never be charged as waste.
    """
    table = _platform_suffixes() if suffixes is None else suffixes
    low = name.lower()
    best_rank: tuple[int, int] = (-1, -1)
    best: tuple[str | None, str] = (None, name)
    for suffix, platform_name in table.items():
        idx = low.rfind(suffix)
        if idx < 0:
            continue
        ends_at_tail = idx + len(suffix) == len(low)
        rank = (1 if ends_at_tail else 0, len(suffix))
        if rank > best_rank:
            best_rank = rank
            tool = name[:idx].rstrip("-") or name
            best = (platform_name, tool)
    if best[0] is not None:
        return best
    if low.endswith(".exe"):
        return (_WINDOWS_ANY, name[: -len(".exe")])
    return (None, name)


def _launcher_scripts(plugin_root: Path) -> list[str]:
    """Non-compiled files sitting in `bin/` — the dispatcher, when there is one.

    `iter_shipped_binaries` deliberately excludes these (a launcher is scannable
    source, not compiled output), so they are collected separately: their
    presence is what makes a fetch-on-install layout navigable, and naming them
    in the INFO finding is more useful than asserting an abstract shape.
    """
    bin_dir = plugin_root / "bin"
    if not bin_dir.is_dir():
        return []
    compiled = {p.resolve() for p in iter_shipped_binaries(plugin_root)}
    out: list[str] = []
    for path in sorted(bin_dir.rglob("*")):
        if not path.is_file() or path.resolve() in compiled:
            continue
        out.append(path.relative_to(plugin_root).as_posix())
    return out


def _mb(num_bytes: int) -> float:
    return round(num_bytes / (1024 * 1024), 1)


def analyse_platform_delivery(plugin_root: Path, *, host_platform: str | None = None) -> dict[str, Any]:
    """Measure what the plugin's shipped binaries cost a user on THIS host.

    `host_platform` overrides host detection so a caller (and every test) can
    price a fixture against a named host instead of whatever machine happens to
    be running — a measurement that changes with the runner is not a measurement.

    Never raises. On any IO/parse failure the returned dict carries `error` and
    numbers that cannot produce a finding, because a scan that did not complete
    must not be reported as either waste or cleanliness.
    """
    result: dict[str, Any] = {
        "host_platform": None,
        "binaries": [],
        "families": {},
        "platform_variants": 0,
        "total_bytes": 0,
        "host_usable_bytes": 0,
        "unusable_bytes": 0,
        "unclassified_bytes": 0,
        "unusable_fraction": 0.0,
        "fetch_on_install": False,
        "launchers": [],
        "error": None,
    }
    try:
        host = host_platform if host_platform is not None else host_platform_name()
        result["host_platform"] = host
        suffixes = _platform_suffixes()
        runnable = _host_runnable(host) if host else frozenset()

        binaries: list[dict[str, Any]] = []
        families: dict[str, dict[str, Any]] = {}
        variants: set[str] = set()

        for path in iter_shipped_binaries(plugin_root):
            size = path.stat().st_size
            name = path.name
            platform_name, tool = classify_binary_name(name, suffixes)
            usable = platform_name is not None and platform_name in runnable
            binaries.append(
                {
                    "path": path.relative_to(plugin_root).as_posix(),
                    "bytes": size,
                    "platform": platform_name,
                    "tool": tool,
                    "host_usable": usable,
                }
            )
            result["total_bytes"] += size
            if platform_name is None:
                result["unclassified_bytes"] += size
            elif host is None:
                # No host identity means no such thing as an unusable byte. The
                # bytes stay in the total (the user carries them either way) but
                # charging them as waste would be a fabricated measurement.
                pass
            elif usable:
                result["host_usable_bytes"] += size
            else:
                result["unusable_bytes"] += size
            if platform_name is not None:
                variants.add(platform_name)
                fam = families.setdefault(tool, {"platforms": set(), "bytes": 0})
                fam["platforms"].add(platform_name)
                fam["bytes"] += size

        result["binaries"] = binaries
        result["families"] = {
            tool: {"platforms": sorted(data["platforms"]), "bytes": data["bytes"]}
            for tool, data in sorted(families.items())
        }
        result["platform_variants"] = len(variants)
        total = int(result["total_bytes"])
        # Against the TOTAL shipped binary payload, per the issue's own
        # arithmetic — an unclassified binary stays in the denominator because
        # the user carries it either way.
        result["unusable_fraction"] = (int(result["unusable_bytes"]) / total) if total else 0.0
        result["fetch_on_install"] = _has_release_asset_installer(plugin_root)
        result["launchers"] = _launcher_scripts(plugin_root)
    except (OSError, ValueError, TypeError, ImportError, AttributeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def verify_platform_delivery(plugin_root: Path, *, host_platform: str | None = None) -> list[tuple[str, str]]:
    """Report on per-platform delivery. Returns (rule_id, message) pairs.

    The caller owns severity, matching `cpv_binary_attestation.verify_attestations`:
    `RC-PLATFORM-BLOAT` is a WARNING and `RC-PLATFORM-DELIVERY-OK` an INFO, but
    that policy belongs to the validator, not here.

    An empty list means "nothing to say about delivery" — a source-only plugin,
    a single-platform one, an unknown host, or a scan that failed. It is
    explicitly NOT a claim that the delivery shape was examined and approved.
    """
    data = analyse_platform_delivery(plugin_root, host_platform=host_platform)
    if data.get("error"):
        return []

    total = int(data["total_bytes"])
    unusable = int(data["unusable_bytes"])
    variants = int(data["platform_variants"])
    fraction = float(data["unusable_fraction"])
    host = data["host_platform"]

    bloated = bool(
        host
        and total > 0
        and variants >= PLATFORM_BLOAT_MIN_VARIANTS
        and fraction > PLATFORM_BLOAT_UNUSABLE_FRACTION
    )

    if bloated:
        families = data["families"]
        biggest = sorted(families.items(), key=lambda kv: -int(kv[1]["bytes"]))[:3]
        family_note = ", ".join(f"{tool} ({len(info['platforms'])} platforms, {_mb(int(info['bytes']))} MB)" for tool, info in biggest)
        return [
            (
                "RC-PLATFORM-BLOAT",
                f"RC-PLATFORM-BLOAT: bin/ ships {_mb(total)} MB of compiled binaries covering {variants} "
                f"platform variants, but this host ({host}) can execute only {_mb(int(data['host_usable_bytes']))} MB "
                f"of it — {_mb(unusable)} MB ({fraction * 100:.1f}%) is native code for platforms this machine can "
                f"never run, and every user pays for every platform. Largest families: {family_note}. "
                f"Consider fetch-on-install: ship a small dispatcher, attach the per-platform assets plus a "
                f"SHA256SUMS manifest to the release, and download ONLY the host's binary at install time — "
                f"verifying the checksum, because an UNVERIFIED download would be strictly worse than committing "
                f"the binary. Committing every platform stays valid where installs must work offline. See "
                f"skills/cpv-canonical-pipeline/references/per-platform-delivery.md.",
            )
        ]

    if data["fetch_on_install"]:
        launchers = data["launchers"]
        dispatcher = f" via {launchers[0]}" if launchers else ""
        return [
            (
                "RC-PLATFORM-DELIVERY-OK",
                f"RC-PLATFORM-DELIVERY-OK: the plugin fetches its per-platform binary at install time"
                f"{dispatcher}, downloading a release asset and verifying its sha256 — so a user carries only "
                f"the binary their host can run ({_mb(total)} MB committed across {variants} platform variant(s)). "
                f"This is the smallest-install shape and, because the download is checksum-verified, it is not a "
                f"security downgrade against committing the binaries.",
            )
        ]

    return []


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    paths = [a for a in args if not a.startswith("-")]
    root = Path(paths[0] if paths else ".").resolve()

    if as_json:
        import json  # noqa: PLC0415

        data = analyse_platform_delivery(root)
        data["families"] = {k: dict(v) for k, v in data["families"].items()}
        print(json.dumps(data, indent=2))
        return 0

    findings = verify_platform_delivery(root)
    for _rule, message in findings:
        print(message)
    if not findings:
        print(f"platform-delivery: nothing to report for {root.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
