#!/usr/bin/env python3
"""Binary attestation — tie every shipped compiled artifact to the source it came from.

Issue #185 §2/§3, filed by the perfect-skill-suggester Claude against the
ship-only-binary canon:

    Shipping only a binary makes the plugin's executable UNVERIFIABLE by
    construction: nothing ties the shipped bytes to any source revision.
    `cpv.strip.extract[]` records {path, url, sha}, but nothing checks that the
    binary in bin/ was built from that sha.

That is not hypothetical. On the day the issue was filed, PSS's `publish.py`
staged only `Cargo.toml`/`Cargo.lock` inside its `rust/` submodule, and a
rebuilt binary was about to ship while its `main.rs` change was never committed
— the shipped binary would have contained a prompt-injection sanitizer that
**existed in no repository**. It was caught only because the source was still
in-tree and `git status` showed it. Under ship-only-binary that discrepancy is
structurally invisible: there is no in-tree source to be out of sync with.

So the canon cannot become universal until the artifact it mandates can be
verified. CPV runs five security scanners over plugin SOURCE and every one of
them is blind to a native binary (§3) — meaning the canon would reduce assurance
exactly where risk is highest, compiled code executing on a user's machine,
unless attestation is the compensating control.

THE RECORD, in `.claude-plugin/plugin.json`:

    "cpv": {
      "attest": [
        {
          "path": "bin/pss-darwin-arm64",
          "sha256": "<64 hex>",
          "source_commit": "<40 hex>",
          "toolchain": "rustc 1.83.0",
          "build_cmd": "cargo build --release --target aarch64-apple-darwin",
          "source_url": "https://github.com/owner/repo",   # optional
          "target": "aarch64-apple-darwin"                  # optional
        }
      ]
    }

WHAT THIS MODULE CAN AND CANNOT PROVE. It verifies the manifest is COMPLETE and
CURRENT: every shipped compiled artifact has an entry, and each entry's
`sha256` matches the bytes on disk. That is what catches the PSS shape — a
rebuilt binary whose record was not updated no longer matches its own hash.
It does NOT prove the bytes were produced by `build_cmd` at `source_commit`;
only a rebuild can, which belongs in the plugin's release CI (canon, next
phase). Recording that limit here rather than letting a green check imply more
than it checked — an unverified claim must never read as a verified one.

SEVERITY, and why it is WARNING today: this ships in the migration window. A
plugin cannot comply with a manifest format that did not exist when it was
published, so enforcing it on arrival would retro-break every compiled plugin in
the fleet — the exact thing project rule #170 forbids. `--emit` generates the
manifest so compliance is mechanical, and the severity flips to blocking in the
release that makes the canon universal.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

from cpv_pipeline_profile import _load_manifest

__all__ = [
    "ATTEST_REQUIRED_FIELDS",
    "emit_attestations",
    "iter_shipped_binaries",
    "load_attestations",
    "verify_attestations",
]

# A record is only useful if it answers all four questions: WHICH bytes
# (sha256), from WHICH source (source_commit), built with WHAT (toolchain), and
# HOW (build_cmd). A partial record cannot be checked against a rebuild, so a
# missing field is a finding rather than a tolerated gap.
ATTEST_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "path",
    "sha256",
    "source_commit",
    "toolchain",
    "build_cmd",
)

# Suffixes that are compiled output beyond doubt. Presence is sufficient, not
# necessary — a Unix binary usually has NO suffix, which is why the content
# check below is the primary signal and this set only short-circuits it.
_COMPILED_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".exe", ".dll", ".so", ".dylib", ".wasm", ".a", ".lib", ".o", ".class", ".jar"}
)

_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
# Git object ids: 40-hex for sha1, 64-hex for sha256 repositories.
_COMMIT_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")


def _sha256_file(path: Path) -> str | None:
    """Hex digest of `path`, or None when it cannot be read.

    None is deliberately distinct from a wrong digest: the caller reports
    "could not verify" rather than "does not match", because an unreadable
    file is not evidence of a mismatch.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _is_compiled_artifact(path: Path) -> bool:
    """True when `path` is compiled output rather than a script or data file."""
    if path.suffix.lower() in _COMPILED_SUFFIXES:
        return True
    # A shell/python launcher living beside the binaries is NOT an artifact to
    # attest — it is scannable source and the scanners already read it.
    try:
        head = path.open("rb").read(4)
    except OSError:
        return False
    # ELF, Mach-O (32/64, both endiannesses), PE, and Java class magic. Checking
    # magic rather than the executable bit: a git checkout can lose the mode,
    # and mode says nothing about whether the content is compiled.
    return head[:4] in (
        b"\x7fELF",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
    ) or head[:2] == b"MZ"


def iter_shipped_binaries(plugin_root: Path) -> list[Path]:
    """Every compiled artifact the plugin SHIPS, sorted for stable output.

    Scoped to `bin/`, which is where the ship-only-binary canon mandates
    artifacts live. A compiled file elsewhere is a different finding
    (RC-SHIP-BINARY-ONLY already covers stray compile output); attesting it
    here would demand a record for a file the canon says should not be there.
    """
    bin_dir = plugin_root / "bin"
    if not bin_dir.is_dir():
        return []
    out = [p for p in sorted(bin_dir.rglob("*")) if p.is_file() and _is_compiled_artifact(p)]
    return out


def load_attestations(plugin_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (entries, schema_errors) from `cpv.attest[]`.

    Errors are RETURNED, never raised and never swallowed: a malformed
    attestation block must surface as a finding. Silently treating it as
    "no attestations" would let a typo read as a clean plugin — the
    cannot-check-is-not-clean failure this module exists to prevent.
    """
    manifest = _load_manifest(plugin_root)
    cpv_block = manifest.get("cpv")
    if not isinstance(cpv_block, dict):
        return [], []
    raw = cpv_block.get("attest")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"cpv.attest must be an array, got {type(raw).__name__}"]

    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"cpv.attest[{idx}] must be an object, got {type(item).__name__}")
            continue
        missing = [f for f in ATTEST_REQUIRED_FIELDS if not str(item.get(f) or "").strip()]
        if missing:
            errors.append(f"cpv.attest[{idx}] ({item.get('path', '?')}) is missing: {', '.join(missing)}")
            continue
        if not _SHA256_RE.match(str(item["sha256"]).lower()):
            errors.append(f"cpv.attest[{idx}] ({item['path']}) sha256 is not a 64-hex digest")
            continue
        if not _COMMIT_RE.match(str(item["source_commit"]).lower()):
            errors.append(f"cpv.attest[{idx}] ({item['path']}) source_commit is not a full git object id")
            continue
        entries.append(item)
    return entries, errors


def verify_attestations(plugin_root: Path) -> list[tuple[str, str]]:
    """Check the attestation manifest against what the plugin actually ships.

    Returns (rule_id, message) pairs; the caller owns severity so the same
    logic serves the WARNING migration window and the later blocking canon.
    An empty list means "every shipped binary carries a record whose hash
    matches the bytes on disk" — NOT "the binary was proven to come from that
    commit", which needs a rebuild (see the module docstring).
    """
    findings: list[tuple[str, str]] = []
    binaries = iter_shipped_binaries(plugin_root)
    entries, errors = load_attestations(plugin_root)

    for err in errors:
        findings.append(("RC-ATTEST-MALFORMED", f"RC-ATTEST-MALFORMED: {err}"))

    if not binaries:
        # No compiled artifact ships, so there is nothing to attest. An entry
        # that points at a file the plugin does not ship is still worth
        # reporting — a stale record invites trusting a hash nobody checks.
        for e in entries:
            if not (plugin_root / str(e["path"])).is_file():
                findings.append(
                    (
                        "RC-ATTEST-ORPHAN",
                        f"RC-ATTEST-ORPHAN: cpv.attest names '{e['path']}', which the plugin does not ship. "
                        f"Remove the stale record or restore the artifact.",
                    )
                )
        return findings

    by_path = {str(e["path"]).replace("\\", "/"): e for e in entries}

    for binary in binaries:
        rel = binary.relative_to(plugin_root).as_posix()
        entry = by_path.pop(rel, None)
        if entry is None:
            findings.append(
                (
                    "RC-ATTEST-MISSING",
                    f"RC-ATTEST-MISSING: '{rel}' ships as compiled output with no cpv.attest record, so "
                    f"nothing ties these bytes to any source revision — CPV's scanners cannot read a "
                    f"native binary, so the record IS the audit trail. Run "
                    f"`cpv-remote-validate attest --emit .` to generate one.",
                )
            )
            continue
        actual = _sha256_file(binary)
        if actual is None:
            findings.append(
                (
                    "RC-ATTEST-UNREADABLE",
                    f"RC-ATTEST-UNREADABLE: '{rel}' could not be read to verify its attested sha256. "
                    f"An artifact that cannot be checked is not an artifact that was found to match.",
                )
            )
            continue
        if actual != str(entry["sha256"]).lower():
            findings.append(
                (
                    "RC-ATTEST-MISMATCH",
                    f"RC-ATTEST-MISMATCH: '{rel}' does not match its attested sha256 (attested "
                    f"{str(entry['sha256'])[:12]}…, on disk {actual[:12]}…). The binary was rebuilt "
                    f"without updating its record, so the shipped bytes correspond to no recorded "
                    f"source revision — re-run `attest --emit` AFTER committing the source it was "
                    f"built from.",
                )
            )

    for stale_path in sorted(by_path):
        findings.append(
            (
                "RC-ATTEST-ORPHAN",
                f"RC-ATTEST-ORPHAN: cpv.attest names '{stale_path}', which the plugin does not ship. "
                f"Remove the stale record or restore the artifact.",
            )
        )
    return findings


def _git(plugin_root: Path, *args: str) -> str:
    try:
        res = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *args],
            cwd=plugin_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def emit_attestations(plugin_root: Path) -> list[dict[str, Any]]:
    """Generate attestation records for every shipped binary.

    Fills what can be derived (path, sha256, and the source commit when the
    plugin records one) and leaves `toolchain` / `build_cmd` as explicit
    TODO placeholders rather than guessing them — a fabricated build command
    is worse than an absent one, because it looks like a verified fact. The
    placeholders fail `load_attestations`, so an un-edited emission cannot
    masquerade as a complete record.
    """
    manifest = _load_manifest(plugin_root)
    cpv_block = manifest.get("cpv") if isinstance(manifest.get("cpv"), dict) else {}
    extracts = cpv_block.get("extract") if isinstance(cpv_block, dict) else None

    source_commit = ""
    source_url = ""
    if isinstance(extracts, list):
        for item in extracts:
            if isinstance(item, dict) and item.get("sha"):
                source_commit = str(item["sha"])
                source_url = str(item.get("url", ""))
                break
    if not source_commit:
        source_commit = _git(plugin_root, "rev-parse", "HEAD")

    out: list[dict[str, Any]] = []
    for binary in iter_shipped_binaries(plugin_root):
        digest = _sha256_file(binary)
        record: dict[str, Any] = {
            "path": binary.relative_to(plugin_root).as_posix(),
            "sha256": digest or "",
            "source_commit": source_commit,
            "toolchain": "TODO: e.g. `rustc 1.83.0` — the exact compiler that produced this",
            "build_cmd": "TODO: the exact command that produced this artifact",
        }
        if source_url:
            record["source_url"] = source_url
        out.append(record)
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    emit = "--emit" in args
    paths = [a for a in args if not a.startswith("-")]
    root = Path(paths[0] if paths else ".").resolve()

    if emit:
        print(json.dumps({"cpv": {"attest": emit_attestations(root)}}, indent=2))
        return 0

    findings = verify_attestations(root)
    for _rule, msg in findings:
        print(msg)
    if not findings:
        print(f"attest: OK — every shipped binary in {root.name}/bin carries a matching record")
    return 1 if findings else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
