"""Issue #185 §5/§6/§7 — the three checks the ship-only-binary canon shipped without.

Every assertion here is two-sided: each positive finding has a MINIMAL-MUTATION
sibling (one field blanked, one file moved, one line removed) proving the same code
path stays silent on the compliant shape. A one-sided suppression test passes
vacuously the moment the detector stops firing at all, and a one-sided detection
test cannot tell an accurate rule from a blanket complaint.

The load-bearing cases:

* `test_offline_scan_does_not_read_as_clean` — a check that could not run must never
  be indistinguishable from a check that ran and found nothing.
* `test_indeterminate_answer_is_unverified_not_rot` — a rate-limited API tells us
  nothing about the commit, and manufacturing a rot finding out of a flaky network
  is how a rule earns being ignored.
* `test_transpiled_pair_the_extension_rule_cannot_see` — the §7 defect verbatim: the
  existing rule maps `.rs`/`.go`/`.c`, so a `dist/` bundle built from `.ts` draws
  nothing from it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ship_canon  # noqa: E402
from cpv_ship_canon import (  # noqa: E402
    EXTRACT_REQUIRED_FIELDS,
    classify_build_role,
    verify_binary_licences,
    verify_build_roles,
    verify_extract_records,
)

ELF = b"\x7fELF\x02\x01\x01\x00"
GOOD_SHA = "a" * 40
GOOD_URL = "https://github.com/acme/engine"


def _rules(findings: list[tuple[str, str]]) -> list[str]:
    return sorted(rule for rule, _ in findings)


def _plugin(
    tmp_path: Path,
    *,
    name: str = "plug",
    cpv: dict[str, Any] | None = None,
    files: dict[str, str] | None = None,
    binaries: dict[str, bytes] | None = None,
    manifest_text: str | None = None,
) -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    if manifest_text is not None:
        (root / ".claude-plugin" / "plugin.json").write_text(manifest_text, encoding="utf-8")
    else:
        manifest: dict[str, Any] = {"name": name, "description": "fixture", "version": "0.1.0"}
        if cpv is not None:
            manifest["cpv"] = cpv
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, text in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for rel, blob in (binaries or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    return root


def _extract(**over: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"path": "rust", "url": GOOD_URL, "sha": GOOD_SHA}
    record.update(over)
    return record


def _with_records(tmp_path: Path, *records: Any, name: str = "plug") -> Path:
    return _plugin(tmp_path, name=name, cpv={"strip": {"extract": list(records)}})


# ---------------------------------------------------------------------------
# §5 — record shape
# ---------------------------------------------------------------------------


class TestExtractRecordShape:
    def test_wellformed_record_is_not_malformed(self, tmp_path: Path) -> None:
        """The two-sided partner of every MALFORMED case below."""
        root = _with_records(tmp_path, _extract())
        assert "RC-EXTRACT-MALFORMED" not in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("field", EXTRACT_REQUIRED_FIELDS)
    def test_each_required_field_is_enforced(self, tmp_path: Path, field: str) -> None:
        record = _extract()
        del record[field]
        root = _with_records(tmp_path, record)
        assert "RC-EXTRACT-MALFORMED" in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("field", EXTRACT_REQUIRED_FIELDS)
    def test_a_blank_field_is_as_bad_as_a_missing_one(self, tmp_path: Path, field: str) -> None:
        root = _with_records(tmp_path, _extract(**{field: "   "}))
        assert "RC-EXTRACT-MALFORMED" in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("field", EXTRACT_REQUIRED_FIELDS)
    def test_a_non_string_field_is_malformed(self, tmp_path: Path, field: str) -> None:
        root = _with_records(tmp_path, _extract(**{field: 7}))
        assert "RC-EXTRACT-MALFORMED" in _rules(verify_extract_records(root))

    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:acme/engine.git",
            "http://github.com/acme/engine",
            "https://gitlab.com/acme/engine",
            "https://user:token@github.com/acme/engine",
            "https://github.com/acme/engine/../evil",
            "https://github.com/acme",
            "https://github.com/acme/engine/sub",
            "https://github.com:8443/acme/engine",
            "",
        ],
    )
    def test_non_canonical_url_is_malformed(self, tmp_path: Path, url: str) -> None:
        root = _with_records(tmp_path, _extract(url=url))
        assert "RC-EXTRACT-MALFORMED" in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("url", [GOOD_URL, GOOD_URL + ".git", "https://github.com/a1/b_2.x"])
    def test_canonical_url_is_accepted(self, tmp_path: Path, url: str) -> None:
        root = _with_records(tmp_path, _extract(url=url))
        assert "RC-EXTRACT-MALFORMED" not in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("sha", ["abcdef", "a" * 41, "z" * 40, "not-a-sha", "a" * 64])
    def test_sha_outside_the_grammar_is_malformed(self, tmp_path: Path, sha: str) -> None:
        root = _with_records(tmp_path, _extract(sha=sha))
        assert "RC-EXTRACT-MALFORMED" in _rules(verify_extract_records(root))

    @pytest.mark.parametrize("sha", ["abcdef1", "A" * 40, "0" * 12, GOOD_SHA])
    def test_sha_inside_the_grammar_is_accepted(self, tmp_path: Path, sha: str) -> None:
        root = _with_records(tmp_path, _extract(sha=sha))
        assert "RC-EXTRACT-MALFORMED" not in _rules(verify_extract_records(root))

    def test_pre_strip_declaration_is_not_a_record(self, tmp_path: Path) -> None:
        """`{src, submodule}` is what an AUTHOR writes to configure the strip. Nothing
        has been extracted yet, so there is no reference that could have rotted —
        flagging it would report every plugin that adopted the canon but has not run
        it."""
        root = _with_records(tmp_path, {"src": "rust", "submodule": "rust"})
        assert verify_extract_records(root) == []

    def test_a_half_written_record_cannot_hide_in_the_declaration_lane(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: one record-shaped key appears, so the
        entry is judged as a record and its missing siblings are reported."""
        root = _with_records(tmp_path, {"src": "rust", "submodule": "rust", "url": GOOD_URL})
        assert _rules(verify_extract_records(root)) == ["RC-EXTRACT-MALFORMED"]

    def test_extract_that_is_not_an_array_is_malformed(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, cpv={"strip": {"extract": {"path": "rust"}}})
        assert _rules(verify_extract_records(root)) == ["RC-EXTRACT-MALFORMED"]

    @pytest.mark.parametrize("item", ["rust", 5, None, ["rust"]])
    def test_entry_that_is_not_an_object_is_malformed(self, tmp_path: Path, item: Any) -> None:
        root = _with_records(tmp_path, item)
        assert _rules(verify_extract_records(root)) == ["RC-EXTRACT-MALFORMED"]

    @pytest.mark.parametrize(
        "cpv",
        [None, {}, {"strip": {}}, {"strip": {"extract": []}}, {"strip": "yes"}, {"canon": "ship-only-binary"}],
    )
    def test_a_plugin_with_no_records_reports_nothing(self, tmp_path: Path, cpv: Any) -> None:
        root = _plugin(tmp_path, cpv=cpv)
        assert verify_extract_records(root) == []

    def test_unparseable_manifest_is_fail_safe(self, tmp_path: Path) -> None:
        """An IO/parse failure yields NO finding — a validator that invents findings
        from a broken read produces alarms nobody can distinguish from real ones."""
        root = _plugin(tmp_path, manifest_text="{ this is not json")
        assert verify_extract_records(root) == []

    def test_missing_manifest_is_fail_safe(self, tmp_path: Path) -> None:
        root = tmp_path / "bare"
        root.mkdir()
        assert verify_extract_records(root) == []

    def test_every_bad_record_is_reported_not_just_the_first(self, tmp_path: Path) -> None:
        root = _with_records(tmp_path, _extract(sha="nope"), _extract(url="ssh://x"), _extract())
        assert _rules(verify_extract_records(root)).count("RC-EXTRACT-MALFORMED") == 2


# ---------------------------------------------------------------------------
# §5 — offline default and the network probe
# ---------------------------------------------------------------------------


class TestExtractNetwork:
    def test_offline_scan_does_not_read_as_clean(self, tmp_path: Path) -> None:
        """THE offline-by-default invariant: the sha was never contacted, and saying
        so is the difference between 'unknown' and 'verified'."""
        root = _with_records(tmp_path, _extract())
        findings = verify_extract_records(root, network=False)
        assert _rules(findings) == ["RC-EXTRACT-UNVERIFIED"]
        assert "NOT" in findings[0][1] and "offline" in findings[0][1]

    def test_offline_is_the_default(self, tmp_path: Path) -> None:
        root = _with_records(tmp_path, _extract())
        assert _rules(verify_extract_records(root)) == ["RC-EXTRACT-UNVERIFIED"]

    def test_offline_never_contacts_the_network(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(owner: str, repo: str, sha: str) -> bool | None:
            raise AssertionError("offline mode must not reach the network")

        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", _boom)
        root = _with_records(tmp_path, _extract())
        assert _rules(verify_extract_records(root)) == ["RC-EXTRACT-UNVERIFIED"]

    def test_a_plugin_with_no_records_emits_no_unverified_noise(self, tmp_path: Path) -> None:
        """Minimal-mutation sibling of the offline case: remove the record and the
        advisory disappears, so UNVERIFIED can never become background noise."""
        root = _plugin(tmp_path, cpv={"strip": {"extract": []}})
        assert verify_extract_records(root, network=False) == []

    def test_reachable_commit_is_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", lambda o, r, s: True)
        root = _with_records(tmp_path, _extract())
        assert verify_extract_records(root, network=True) == []

    def test_unreachable_commit_is_rot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Minimal mutation of the case above: the same record, one different answer."""
        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", lambda o, r, s: False)
        root = _with_records(tmp_path, _extract())
        findings = verify_extract_records(root, network=True)
        assert _rules(findings) == ["RC-EXTRACT-ROT"]
        assert "rust" in findings[0][1] and GOOD_URL in findings[0][1]

    def test_indeterminate_answer_is_unverified_not_rot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rate limit or a proxy error says nothing about the commit. Reporting rot
        from it would be a fabricated finding; reporting nothing would be a false
        clean."""
        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", lambda o, r, s: None)
        root = _with_records(tmp_path, _extract())
        assert _rules(verify_extract_records(root, network=True)) == ["RC-EXTRACT-UNVERIFIED"]

    def test_a_malformed_record_is_never_contacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(owner: str, repo: str, sha: str) -> bool | None:
            raise AssertionError("a record that failed validation must not be fetched")

        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", _boom)
        root = _with_records(tmp_path, _extract(url="git@github.com:acme/engine.git"))
        assert _rules(verify_extract_records(root, network=True)) == ["RC-EXTRACT-MALFORMED"]

    def test_the_probe_targets_only_the_recorded_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, str, str]] = []

        def _record(owner: str, repo: str, sha: str) -> bool:
            seen.append((owner, repo, sha))
            return True

        monkeypatch.setattr(cpv_ship_canon, "_github_commit_exists", _record)
        root = _with_records(tmp_path, _extract(url=GOOD_URL + ".git"))
        verify_extract_records(root, network=True)
        assert seen == [("acme", "engine", GOOD_SHA)]

    @pytest.mark.parametrize(
        "status,expected",
        [(200, True), (204, True)],
    )
    def test_probe_reports_success(self, monkeypatch: pytest.MonkeyPatch, status: int, expected: bool) -> None:
        class _Response:
            def __init__(self) -> None:
                self.status = status

            def __enter__(self) -> "_Response":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        monkeypatch.setattr(cpv_ship_canon.urllib.request, "urlopen", lambda *a, **k: _Response())
        assert cpv_ship_canon._github_commit_exists("acme", "engine", GOOD_SHA) is expected

    @pytest.mark.parametrize("code,expected", [(404, False), (422, False), (403, None), (500, None)])
    def test_probe_only_treats_a_definitive_negative_as_rot(
        self, monkeypatch: pytest.MonkeyPatch, code: int, expected: bool | None
    ) -> None:
        def _raise(*args: object, **kwargs: object) -> None:
            raise urllib.error.HTTPError("u", code, "msg", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(cpv_ship_canon.urllib.request, "urlopen", _raise)
        assert cpv_ship_canon._github_commit_exists("acme", "engine", GOOD_SHA) is expected

    def test_probe_survives_a_transport_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*args: object, **kwargs: object) -> None:
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(cpv_ship_canon.urllib.request, "urlopen", _raise)
        assert cpv_ship_canon._github_commit_exists("acme", "engine", GOOD_SHA) is None


# ---------------------------------------------------------------------------
# §6 — licence gating
# ---------------------------------------------------------------------------


class TestBinaryLicences:
    def test_binary_without_any_licence_is_reported(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"})
        findings = verify_binary_licences(root)
        assert _rules(findings) == ["RC-BINARY-NO-LICENCE"]
        assert "bin/tool-linux-x86_64" in findings[0][1]

    @pytest.mark.parametrize(
        "name",
        [
            "LICENSE",
            "LICENSE.md",
            "LICENSE.txt",
            "LICENCE",
            "licence.md",
            "license",
            "LICENSE-MIT",
            "LICENSE_APACHE.txt",
            "COPYING",
            "COPYING.LESSER",
            "NOTICE",
            "NOTICE.txt",
        ],
    )
    def test_a_licence_at_the_root_clears_it(self, tmp_path: Path, name: str) -> None:
        """Minimal mutation of the case above: one file added, nothing else."""
        root = _plugin(tmp_path, files={name: "MIT"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"})
        assert verify_binary_licences(root) == []

    def test_a_licence_beside_the_binaries_clears_it(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path, files={"bin/LICENSE": "MIT"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"}
        )
        assert verify_binary_licences(root) == []

    def test_a_populated_licenses_directory_clears_it(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path, files={"LICENSES/MIT.txt": "MIT"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"}
        )
        assert verify_binary_licences(root) == []

    def test_an_empty_licenses_directory_does_not_clear_it(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: same directory, no text in it. An empty
        directory states no terms."""
        root = _plugin(tmp_path, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"})
        (root / "LICENSES").mkdir()
        assert _rules(verify_binary_licences(root)) == ["RC-BINARY-NO-LICENCE"]

    def test_a_licence_buried_in_a_subdirectory_does_not_clear_it(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path, files={"docs/LICENSE": "MIT"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"}
        )
        assert _rules(verify_binary_licences(root)) == ["RC-BINARY-NO-LICENCE"]

    def test_moving_that_licence_to_the_root_clears_it(self, tmp_path: Path) -> None:
        """The one-file mutation of the case above."""
        root = _plugin(
            tmp_path, files={"LICENSE": "MIT"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"}
        )
        assert verify_binary_licences(root) == []

    @pytest.mark.parametrize("name", ["README.md", "licensing-notes.md", "CHANGELOG.md"])
    def test_an_unrelated_file_is_not_a_licence(self, tmp_path: Path, name: str) -> None:
        root = _plugin(tmp_path, files={name: "x"}, binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"})
        assert _rules(verify_binary_licences(root)) == ["RC-BINARY-NO-LICENCE"]

    def test_a_source_only_plugin_is_out_of_scope(self, tmp_path: Path) -> None:
        """No compiled artifact ships, so the licence question is the repository's,
        not the artifact's — this rule stays silent."""
        root = _plugin(tmp_path, files={"scripts/run.py": "print(1)"})
        assert verify_binary_licences(root) == []

    def test_adding_one_binary_brings_it_into_scope(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above."""
        root = _plugin(
            tmp_path,
            files={"scripts/run.py": "print(1)"},
            binaries={"bin/tool-linux-x86_64": ELF + b"BUILD"},
        )
        assert _rules(verify_binary_licences(root)) == ["RC-BINARY-NO-LICENCE"]

    def test_a_launcher_script_is_not_a_shipped_binary(self, tmp_path: Path) -> None:
        """Reuses the attestation module's magic-byte detector, so a shell launcher in
        bin/ does not drag a source-only plugin into scope."""
        root = _plugin(tmp_path, files={"bin/launcher.sh": "#!/bin/sh\necho hi\n"})
        assert verify_binary_licences(root) == []


# ---------------------------------------------------------------------------
# §7 — build-graph roles
# ---------------------------------------------------------------------------

TSCONFIG = json.dumps({"include": ["src/**/*"], "compilerOptions": {"outDir": "dist"}})


class TestBuildRoleClassification:
    def test_transpiled_pair_the_extension_rule_cannot_see(self, tmp_path: Path) -> None:
        """§7 verbatim: the existing compiled-source rule maps `.rs`/`.go`/`.c`/`.cs`,
        so a dist/ bundle whose source is `.ts` is invisible to it. Role beats
        extension — both halves of the build graph ship here."""
        root = _plugin(
            tmp_path,
            files={
                "tsconfig.json": TSCONFIG,
                "src/index.ts": "export const x = 1;\n",
                "dist/index.js": "export const x = 1;\n",
            },
        )
        findings = verify_build_roles(root)
        assert _rules(findings) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]
        assert "dist/index.js" in findings[0][1] and "src/index.ts" in findings[0][1]

    def test_shipping_only_the_output_is_clean(self, tmp_path: Path) -> None:
        """Minimal mutation: drop the source half."""
        root = _plugin(tmp_path, files={"tsconfig.json": TSCONFIG, "dist/index.js": "x\n"})
        assert verify_build_roles(root) == []

    def test_shipping_only_the_source_is_clean(self, tmp_path: Path) -> None:
        """Minimal mutation: drop the output half."""
        root = _plugin(tmp_path, files={"tsconfig.json": TSCONFIG, "src/index.ts": "x\n"})
        assert verify_build_roles(root) == []

    def test_roles_are_reported_not_just_the_finding(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={"tsconfig.json": TSCONFIG, "src/index.ts": "x\n", "dist/index.js": "x\n"},
        )
        roles = classify_build_role(root)
        assert "dist/index.js" in roles["outputs"]
        assert "src/index.ts" in roles["inputs"]
        assert "src/index.ts" not in roles["outputs"]
        assert "dist" in roles["output_prefixes"]

    def test_a_minified_bundle_is_output_wherever_it_sits(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, files={"src/app.ts": "x\n", "assets/app.min.js": "x\n"})
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_the_same_file_unminified_outside_a_build_dir_is_not_output(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: `.min.js` -> `.js`. Nothing then marks
        it as generated, so the pair is not asserted."""
        root = _plugin(tmp_path, files={"src/app.ts": "x\n", "assets/app.js": "x\n"})
        assert verify_build_roles(root) == []

    def test_a_sourcemap_sibling_marks_its_file_as_output(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={"src/app.ts": "x\n", "lib/app.js": "x\n", "lib/app.js.map": "{}"},
        )
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_without_the_sourcemap_the_same_tree_is_clean(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: one file removed."""
        root = _plugin(tmp_path, files={"src/app.ts": "x\n", "lib/app.js": "x\n"})
        assert verify_build_roles(root) == []

    def test_a_declared_outdir_marks_output_in_an_unconventional_directory(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                "tsconfig.json": json.dumps({"include": ["src"], "compilerOptions": {"outDir": "compiled"}}),
                "src/index.ts": "x\n",
                "compiled/index.js": "x\n",
            },
        )
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_without_that_declaration_the_directory_means_nothing(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: the outDir line is gone, so nothing
        says `compiled/` holds build output and the rule does not guess."""
        root = _plugin(
            tmp_path,
            files={
                "tsconfig.json": json.dumps({"include": ["src"]}),
                "src/index.ts": "x\n",
                "compiled/index.js": "x\n",
            },
        )
        assert verify_build_roles(root) == []

    def test_source_shipped_inside_the_output_directory_is_caught(self, tmp_path: Path) -> None:
        """A `.ts` under `dist/` is source that was shipped into the output dir —
        classifying it as output by location would erase the finding."""
        root = _plugin(tmp_path, files={"dist/app.ts": "x\n", "dist/app.js": "x\n"})
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_a_different_stem_is_not_a_generating_source(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path, files={"tsconfig.json": TSCONFIG, "src/index.ts": "x\n", "dist/vendor.js": "x\n"}
        )
        assert verify_build_roles(root) == []

    def test_a_same_stem_file_outside_every_input_root_is_not_paired(self, tmp_path: Path) -> None:
        """`test/utils.ts` shares a stem with `dist/utils.js` without generating it.
        Location has to agree too, or the rule becomes a stem collision detector."""
        root = _plugin(tmp_path, files={"test/utils.ts": "x\n", "dist/utils.js": "x\n"})
        assert verify_build_roles(root) == []

    def test_the_same_file_under_a_declared_input_root_is_paired(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: same two files, source moved into the
        root the build declares it reads from."""
        root = _plugin(
            tmp_path, files={"tsconfig.json": TSCONFIG, "src/utils.ts": "x\n", "dist/utils.js": "x\n"}
        )
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_a_build_script_path_establishes_an_input_root(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                "package.json": json.dumps(
                    {"name": "p", "scripts": {"build": "esbuild app/main.ts --outfile=dist/main.js"}}
                ),
                "app/main.ts": "x\n",
                "dist/main.js": "x\n",
            },
        )
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_a_non_build_script_does_not(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: the same command under a `lint` key is
        not evidence of where the build reads from."""
        root = _plugin(
            tmp_path,
            files={
                "package.json": json.dumps(
                    {"name": "p", "scripts": {"lint": "eslint app/main.ts --fix"}}
                ),
                "app/main.ts": "x\n",
                "dist/main.js": "x\n",
            },
        )
        assert verify_build_roles(root) == []

    def test_the_css_family_pairs(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, files={"src/theme.scss": "x\n", "dist/theme.css": "x\n"})
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_a_cross_family_stem_match_does_not_pair(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: `.scss` -> `.ts`. A TypeScript file does
        not generate a stylesheet, so the stem match is a coincidence."""
        root = _plugin(tmp_path, files={"src/theme.ts": "x\n", "dist/theme.css": "x\n"})
        assert verify_build_roles(root) == []

    def test_a_compiled_language_pair_is_left_to_the_existing_rule(self, tmp_path: Path) -> None:
        """Deliberately out of scope: a compiled source shipped beside its binary is
        already RC-SHIP-BINARY-ONLY's finding, and reporting it twice would train
        readers to skim both."""
        root = _plugin(
            tmp_path,
            files={"Cargo.toml": "[package]\nname='t'\n", "src/tool.rs": "fn main(){}\n"},
            binaries={"target/release/tool": ELF + b"BUILD"},
        )
        assert verify_build_roles(root) == []

    def test_every_pair_is_reported_not_just_the_first(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                "tsconfig.json": TSCONFIG,
                "src/a.ts": "x\n",
                "src/b.ts": "x\n",
                "dist/a.js": "x\n",
                "dist/b.js": "x\n",
            },
        )
        assert len(verify_build_roles(root)) == 2

    def test_a_nested_workspace_output_directory_is_recognised(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                "packages/ui/tsconfig.json": TSCONFIG,
                "packages/ui/dist/index.js": "x\n",
                "packages/ui/dist/index.ts": "x\n",
            },
        )
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_an_empty_plugin_classifies_to_nothing(self, tmp_path: Path) -> None:
        root = tmp_path / "bare"
        root.mkdir()
        roles = classify_build_role(root)
        assert roles == {
            "outputs": [],
            "inputs": [],
            "input_roots": [],
            "output_prefixes": [],
            "findings": [],
        }

    @pytest.mark.parametrize("config", ["tsconfig.json", "package.json"])
    def test_an_unparseable_build_config_is_fail_safe(self, tmp_path: Path, config: str) -> None:
        """A JSONC tsconfig (comments) will not parse. That must yield NO signal
        rather than a guessed one — and never a crash."""
        root = _plugin(
            tmp_path,
            files={config: "// a comment\n{ broken", "src/index.ts": "x\n", "assets/index.js": "x\n"},
        )
        assert verify_build_roles(root) == []

    def test_a_config_that_is_not_an_object_is_fail_safe(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, files={"tsconfig.json": "[1, 2, 3]", "src/index.ts": "x\n"})
        assert verify_build_roles(root) == []


class TestShippedSurface:
    """The role check must judge what SHIPS, not what happens to be on disk."""

    @staticmethod
    def _git(root: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    @pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the shipped-surface test")
    def test_a_gitignored_build_directory_does_not_ship(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                ".gitignore": "dist/\n",
                "tsconfig.json": TSCONFIG,
                "src/index.ts": "x\n",
                "dist/index.js": "x\n",
            },
        )
        self._git(root, "init")
        self._git(root, "add", "-A")
        assert verify_build_roles(root) == []

    @pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the shipped-surface test")
    def test_force_adding_it_makes_it_ship_again(self, tmp_path: Path) -> None:
        """Minimal mutation of the case above: one `git add -f`. A .gitignore entry
        does not untrack an already-tracked file, so a tracked-and-ignored build
        output still ships — and is still reported."""
        root = _plugin(
            tmp_path,
            files={
                ".gitignore": "dist/\n",
                "tsconfig.json": TSCONFIG,
                "src/index.ts": "x\n",
                "dist/index.js": "x\n",
            },
        )
        self._git(root, "init")
        self._git(root, "add", "-A")
        self._git(root, "add", "-f", "dist/index.js")
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_a_non_repository_tree_is_scanned_whole(self, tmp_path: Path) -> None:
        """The downloaded-tarball / pre-install-scan case: no git answer, so the
        filesystem is the shipped surface."""
        root = _plugin(tmp_path, files={"tsconfig.json": TSCONFIG, "src/i.ts": "x\n", "dist/i.js": "x\n"})
        assert _rules(verify_build_roles(root)) == ["RC-BUILD-OUTPUT-SHIPS-SOURCE"]

    def test_vendored_trees_are_not_walked(self, tmp_path: Path) -> None:
        root = _plugin(
            tmp_path,
            files={
                "node_modules/dep/dist/index.js": "x\n",
                "node_modules/dep/src/index.ts": "x\n",
            },
        )
        assert verify_build_roles(root) == []


class TestCliContract:
    def test_a_clean_plugin_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = _plugin(tmp_path, files={"scripts/run.py": "print(1)"})
        assert cpv_ship_canon.main([str(root)]) == 0
        assert "OK" in capsys.readouterr().out

    def test_a_finding_exits_one(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        root = _plugin(tmp_path, binaries={"bin/tool": ELF + b"BUILD"})
        assert cpv_ship_canon.main([str(root)]) == 1
        assert "RC-BINARY-NO-LICENCE" in capsys.readouterr().out

    def test_unverified_alone_does_not_fail_the_run(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """UNVERIFIED reports what was NOT checked. It must be visible without being
        the reason a caller sees a failure."""
        root = _with_records(tmp_path, _extract())
        assert cpv_ship_canon.main([str(root)]) == 0
        assert "RC-EXTRACT-UNVERIFIED" in capsys.readouterr().out
