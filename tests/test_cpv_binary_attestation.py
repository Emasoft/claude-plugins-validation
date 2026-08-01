"""Issue #185 §2/§3 — a shipped binary must carry a record tying it to its source.

The ship-only-binary canon mandates shipping compiled output that CPV's five
security scanners cannot read. Attestation is the compensating control: without
it the canon converts a verifiable artifact into an opaque one, because nothing
ties the shipped bytes to any source revision.

The load-bearing case is `test_rebuilt_binary_without_updated_record_is_caught`,
which reproduces the near-miss the reporter hit on the day they filed: a rebuilt
binary was about to ship while its source change was never committed, so the
shipped bytes would have contained code that existed in no repository. They
caught it only because the source was still in-tree. Under ship-only-binary
there is no in-tree source, so this check is the only thing left that can see it.

Every assertion is two-sided: each finding has a sibling proving the same code
path stays silent on the compliant shape, so the check cannot decay into either
a rubber stamp or a blanket complaint.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from cpv_binary_attestation import (  # noqa: E402
    ATTEST_REQUIRED_FIELDS,
    emit_attestations,
    iter_shipped_binaries,
    load_attestations,
    verify_attestations,
)

ELF = b"\x7fELF\x02\x01\x01\x00"


def _rules(findings: list[tuple[str, str]]) -> list[str]:
    return sorted(r for r, _ in findings)


def _plugin(tmp_path: Path, *, binary: bytes | None = ELF + b"BUILD-1", attest=None) -> Path:
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest: dict = {"name": "plug", "description": "fixture", "version": "0.1.0"}
    if attest is not None:
        manifest["cpv"] = {"attest": attest}
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    if binary is not None:
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / "tool-linux-x86_64").write_bytes(binary)
    return root


def _record(root: Path, rel: str = "bin/tool-linux-x86_64", **over) -> dict:
    digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    rec = {
        "path": rel,
        "sha256": digest,
        "source_commit": "a" * 40,
        "toolchain": "rustc 1.83.0",
        "build_cmd": "cargo build --release",
    }
    rec.update(over)
    return rec


class TestDetectionScope:
    def test_compiled_artifact_is_detected(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        assert [p.name for p in iter_shipped_binaries(root)] == ["tool-linux-x86_64"]

    def test_shell_launcher_beside_binaries_is_not_attested(self, tmp_path: Path) -> None:
        """Two-sided: a script is scannable source, so demanding a record for it
        would make the check noise. The scanners already read it."""
        root = _plugin(tmp_path)
        (root / "bin" / "launcher.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
        assert [p.name for p in iter_shipped_binaries(root)] == ["tool-linux-x86_64"]

    def test_plugin_shipping_no_binary_needs_no_attestation(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, binary=None)
        assert iter_shipped_binaries(root) == []
        assert verify_attestations(root) == []

    @pytest.mark.parametrize("suffix", [".exe", ".dll", ".so", ".dylib", ".wasm"])
    def test_compiled_suffixes_are_detected_without_magic(self, tmp_path: Path, suffix: str) -> None:
        root = _plugin(tmp_path, binary=None)
        (root / "bin").mkdir(exist_ok=True)
        (root / "bin" / f"tool{suffix}").write_bytes(b"not-really-elf")
        assert [p.name for p in iter_shipped_binaries(root)] == [f"tool{suffix}"]


class TestVerification:
    def test_binary_with_no_record_is_reported(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        assert _rules(verify_attestations(root)) == ["RC-ATTEST-MISSING"]

    def test_complete_matching_record_is_clean(self, tmp_path: Path) -> None:
        """Two-sided partner of every finding below."""
        root = _plugin(tmp_path)
        root_with = _plugin(tmp_path, attest=[_record(root)])
        assert verify_attestations(root_with) == []

    def test_rebuilt_binary_without_updated_record_is_caught(self, tmp_path: Path) -> None:
        """THE reporter's near-miss: bytes changed, record did not.

        Under ship-only-binary there is no in-tree source left to be visibly
        out of sync, so this is the only remaining signal that the shipped
        bytes correspond to no recorded source revision.
        """
        root = _plugin(tmp_path)
        stale = _record(root)
        (root / "bin" / "tool-linux-x86_64").write_bytes(ELF + b"REBUILT-uncommitted")
        root2 = _plugin(tmp_path, binary=ELF + b"REBUILT-uncommitted", attest=[stale])
        assert _rules(verify_attestations(root2)) == ["RC-ATTEST-MISMATCH"]

    def test_record_for_a_file_that_does_not_ship_is_reported(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        rec = _record(root)
        orphan = dict(rec, path="bin/tool-windows-x86_64.exe")
        root2 = _plugin(tmp_path, attest=[rec, orphan])
        assert _rules(verify_attestations(root2)) == ["RC-ATTEST-ORPHAN"]


class TestSchema:
    @pytest.mark.parametrize("field", ATTEST_REQUIRED_FIELDS)
    def test_each_required_field_is_enforced(self, tmp_path: Path, field: str) -> None:
        root = _plugin(tmp_path)
        rec = _record(root)
        rec[field] = ""
        root2 = _plugin(tmp_path, attest=[rec])
        rules = _rules(verify_attestations(root2))
        assert "RC-ATTEST-MALFORMED" in rules

    def test_a_partial_record_does_not_silently_satisfy_the_binary(self, tmp_path: Path) -> None:
        """A rejected record must not also count as coverage — otherwise an
        incomplete entry would launder a binary past the MISSING check."""
        root = _plugin(tmp_path)
        rec = _record(root)
        rec["build_cmd"] = ""
        root2 = _plugin(tmp_path, attest=[rec])
        rules = _rules(verify_attestations(root2))
        assert rules == ["RC-ATTEST-MALFORMED", "RC-ATTEST-MISSING"]

    @pytest.mark.parametrize("bad", ["deadbeef", "z" * 64, "A" * 63])
    def test_malformed_sha256_is_rejected(self, tmp_path: Path, bad: str) -> None:
        root = _plugin(tmp_path)
        root2 = _plugin(tmp_path, attest=[_record(root, sha256=bad)])
        assert "RC-ATTEST-MALFORMED" in _rules(verify_attestations(root2))

    @pytest.mark.parametrize("bad", ["abc1234", "not-a-commit", "g" * 40])
    def test_abbreviated_or_invalid_commit_is_rejected(self, tmp_path: Path, bad: str) -> None:
        """A short sha is ambiguous and can become unreachable after GC — the
        record has to name a full object id to stay resolvable."""
        root = _plugin(tmp_path)
        root2 = _plugin(tmp_path, attest=[_record(root, source_commit=bad)])
        assert "RC-ATTEST-MALFORMED" in _rules(verify_attestations(root2))

    def test_a_full_sha1_commit_is_accepted(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        root2 = _plugin(tmp_path, attest=[_record(root, source_commit="b" * 40)])
        assert verify_attestations(root2) == []

    def test_non_array_attest_block_is_reported_not_ignored(self, tmp_path: Path) -> None:
        """cannot-check is not clean: a malformed block must never read as
        'this plugin has no attestations and that is fine'."""
        root = _plugin(tmp_path, attest={"path": "bin/x"})
        entries, errors = load_attestations(root)
        assert entries == [] and errors
        assert "RC-ATTEST-MALFORMED" in _rules(verify_attestations(root))


class TestEmit:
    def test_emit_covers_every_shipped_binary_with_a_real_hash(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        records = emit_attestations(root)
        assert [r["path"] for r in records] == ["bin/tool-linux-x86_64"]
        expected = hashlib.sha256((root / "bin" / "tool-linux-x86_64").read_bytes()).hexdigest()
        assert records[0]["sha256"] == expected

    def test_emitted_record_is_not_self_satisfying(self, tmp_path: Path) -> None:
        """An un-edited emission must NOT validate: toolchain/build_cmd are
        placeholders a human has to replace. If emit produced a passing record,
        the gate would certify a build command nobody ever supplied."""
        root = _plugin(tmp_path)
        root2 = _plugin(tmp_path, attest=emit_attestations(root))
        assert "RC-ATTEST-MALFORMED" in _rules(verify_attestations(root2))
