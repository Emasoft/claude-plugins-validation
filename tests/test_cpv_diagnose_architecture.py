"""Tests for scripts/cpv_diagnose_architecture.py — the lean-plugin diagnostic.

Pins the DETECTION + ADVISORY contract:
  * BUILD_SOURCE / RUNTIME_DEP / BUILD_CACHE / DEV_ONLY classification
  * FN-safety: a ${CLAUDE_PLUGIN_ROOT}-referenced script stays RUNTIME_ESSENTIAL,
    `bin/` is never flagged, `_RESERVED_SRCS` is never flagged
  * git-accuracy: a gitignored+untracked tree is not double-flagged; a TRACKED
    build cache IS surfaced
  * the EXACT JSON output schema (Agent B consumes it as a black box)
  * read-only / exit-0-always CLI behavior

Every assertion is two-sided: it checks the thing IS classified as expected AND
that a contrasting thing is NOT.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cpv_diagnose_architecture as cda  # noqa: E402

# ── Fixture builders ─────────────────────────────────────────────────────────


def _write(root: Path, rel: str, content: str = "x") -> Path:
    """Create a file (with parents) under root."""
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return fp


def _manifest(name: str = "demo", *, repository: str = "https://github.com/Emasoft/demo") -> str:
    return json.dumps(
        {
            "name": name,
            "version": "0.1.0",
            "description": "x",
            "repository": repository,
        }
    )


def _init_git(root: Path) -> None:
    """Init a git repo, add+commit everything currently present, then return.

    Tests that want a gitignored+untracked file add it AFTER calling this.
    """
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = "t@t"
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init", "--no-gpg-sign"],
        cwd=root,
        check=True,
        env=env,
    )


def _git_add_commit(root: Path, *paths: str) -> None:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = "t@t"
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@t"
    subprocess.run(["git", "add", "-f", *paths], cwd=root, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "more", "--no-gpg-sign"],
        cwd=root,
        check=True,
        env=env,
    )


def _make_rust_plugin(tmp_path: Path, *, init_git: bool = True) -> Path:
    """A rust-submodule-style plugin: a `rust/` build crate that produces `bin/`."""
    root = tmp_path / "rustplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("rustplug"))
    _write(root, "skills/foo/SKILL.md", "---\nname: foo\ndescription: d\n---\nbody")
    _write(root, "commands/run.md", "do it")
    # Build crate (BUILD_SOURCE)
    _write(root, "rust/Cargo.toml", "[package]\nname='x'\n")
    _write(root, "rust/Cargo.lock", "# lock")
    _write(root, "rust/src/main.rs", "fn main(){}\n")
    _write(root, "rust/src/lib.rs", "pub fn f(){}\n")
    # Compiled binary (RUNTIME, must never be flagged)
    _write(root, "bin/rustplug", "ELF...binary...")
    if init_git:
        _init_git(root)
    return root


def _make_node_modules_plugin(tmp_path: Path, *, init_git: bool = True) -> Path:
    """A plugin shipping a node_modules/ dependency tree (RUNTIME_DEP)."""
    root = tmp_path / "nodeplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("nodeplug"))
    _write(root, "skills/foo/SKILL.md", "---\nname: foo\ndescription: d\n---\nbody")
    _write(root, "package.json", json.dumps({"name": "nodeplug", "dependencies": {"left-pad": "1.0.0"}}))
    _write(root, "node_modules/left-pad/index.js", "module.exports=()=>{}")
    _write(root, "node_modules/left-pad/package.json", json.dumps({"name": "left-pad"}))
    if init_git:
        _init_git(root)
    return root


def _make_clean_lean_plugin(tmp_path: Path, *, init_git: bool = True) -> Path:
    """A lean plugin: only runtime components + conventional root files."""
    root = tmp_path / "leanplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("leanplug"))
    _write(root, "skills/foo/SKILL.md", "---\nname: foo\ndescription: d\n---\nbody")
    _write(root, "skills/foo/references/deep.md", "deep")
    _write(root, "commands/run.md", "do it")
    _write(root, "agents/helper.md", "you are helper")
    _write(root, "hooks/hooks.json", json.dumps({"hooks": {}}))
    _write(root, "README.md", "# lean")
    _write(root, "LICENSE", "MIT")
    _write(root, ".gitignore", "reports/\n")
    if init_git:
        _init_git(root)
    return root


# ── BUILD_SOURCE + bin/ FN-safety ─────────────────────────────────────────────


def test_rust_crate_is_build_source(tmp_path: Path) -> None:
    """A rust/ build crate IS classified BUILD_SOURCE."""
    root = _make_rust_plugin(tmp_path)
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("rust/") == cda.CAT_BUILD_SOURCE
    # Two-sided: the rust crate is NOT classified RUNTIME / DEV_ONLY.
    assert cats.get("rust/") != cda.CAT_RUNTIME
    assert cats.get("rust/") != cda.CAT_DEV_ONLY
    # The category value is the documented schema string.
    assert cda.CAT_RUNTIME == "RUNTIME_ESSENTIAL"


def test_bin_is_never_flagged(tmp_path: Path) -> None:
    """`bin/` (compiled binary) is RUNTIME — never appears in findings."""
    root = _make_rust_plugin(tmp_path)
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "bin/" not in finding_paths
    # Two-sided: rust/ (the source that builds bin/) IS flagged, proving the
    # diagnostic is active and the bin/-exclusion is deliberate, not blanket.
    assert "rust/" in finding_paths


def test_build_source_gets_strip_extract_entry(tmp_path: Path) -> None:
    """A BUILD_SOURCE finding carries a concrete cpv.strip.extract[] entry."""
    root = _make_rust_plugin(tmp_path)
    res = cda.diagnose(root)
    rust = next(f for f in res.findings if f.path == "rust/")
    assert rust.strip_extract_entry is not None
    assert rust.strip_extract_entry.src == "rust/"
    assert rust.strip_extract_entry.submodule == "Emasoft/rustplug-rust"
    # Two-sided: the recommendation bucket also lists it.
    srcs = [e.src for e in res.strip_extract]
    assert "rust/" in srcs


# ── git-submodule = already-separated (never re-recommend stripping) ───────────


def test_gitmodules_rust_submodule_is_not_recommended_for_strip(tmp_path: Path) -> None:
    """A `rust/` declared as a git submodule in `.gitmodules` is ALREADY separated.

    Claude Code's shallow-clone install never recurses submodules, so the
    submodule content never ships — even without a `cpv.strip.extract[]` entry.
    So `rust/` must produce NO BUILD_SOURCE strip finding and must NOT appear in
    the strip_extract recommendation bucket (in-memory AND in the JSON contract).
    """
    root = _make_rust_plugin(tmp_path, init_git=False)
    # Declare rust/ as a raw git submodule (no cpv.strip.extract[] entry).
    _write(
        root,
        ".gitmodules",
        '[submodule "rust"]\n\tpath = rust\n\turl = https://github.com/Emasoft/rustplug-rust.git\n',
    )
    _init_git(root)
    res = cda.diagnose(root)

    finding_paths = {f.path for f in res.findings}
    assert "rust/" not in finding_paths, "a submodule rust/ must not be a finding"
    strip_srcs = [e.src for e in res.strip_extract]
    assert "rust/" not in strip_srcs, "a submodule rust/ must not be recommended for strip"
    # The JSON contract (Agent B consumes recommendations.strip_extract) agrees.
    payload = res.to_json_dict()
    json_srcs = [e["src"] for e in payload["recommendations"]["strip_extract"]]
    assert "rust/" not in json_srcs

    # Two-sided control: a node_modules/ in the SAME tree (NOT a submodule) is
    # still classified RUNTIME_DEP, proving the diagnostic is active and the
    # submodule-skip is targeted, not a blanket silencing.
    _write(root, "node_modules/left-pad/index.js", "module.exports=()=>{}")
    _git_add_commit(root, "node_modules/left-pad/index.js")
    res2 = cda.diagnose(root)
    cats2 = {f.path: f.category for f in res2.findings}
    assert cats2.get("node_modules/") == cda.CAT_RUNTIME_DEP
    assert "rust/" not in {f.path for f in res2.findings}


def test_rust_without_gitmodules_is_still_build_source(tmp_path: Path) -> None:
    """The contrast case: an identical `rust/` with NO `.gitmodules` entry STILL
    classifies BUILD_SOURCE and IS recommended for stripping.

    This is the two-sided proof that the submodule-skip is driven by `.gitmodules`
    membership alone — remove that single signal and the same crate is flagged.
    """
    root = _make_rust_plugin(tmp_path)  # identical rust/ crate, no .gitmodules
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("rust/") == cda.CAT_BUILD_SOURCE
    assert "rust/" in [e.src for e in res.strip_extract]


# ── RUNTIME_DEP ───────────────────────────────────────────────────────────────


def test_node_modules_is_runtime_dep(tmp_path: Path) -> None:
    """node_modules/ IS RUNTIME_DEP (install into ${CLAUDE_PLUGIN_DATA})."""
    root = _make_node_modules_plugin(tmp_path)
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("node_modules/") == cda.CAT_RUNTIME_DEP
    # Two-sided: it is NOT a strip candidate (RUNTIME_DEP gets no strip entry).
    nm = next(f for f in res.findings if f.path == "node_modules/")
    assert nm.strip_extract_entry is None


def test_runtime_dep_recommends_data_dir_and_gitignore(tmp_path: Path) -> None:
    """RUNTIME_DEP populates claude_plugin_data AND gitignore_add, not strip_extract."""
    root = _make_node_modules_plugin(tmp_path)
    res = cda.diagnose(root)
    assert "node_modules/" in res.claude_plugin_data
    assert "node_modules/" in res.gitignore_add
    # Two-sided: it must NOT be recommended for a submodule strip.
    assert "node_modules/" not in [e.src for e in res.strip_extract]


# ── BUILD_CACHE + git accuracy ────────────────────────────────────────────────


def test_tracked_build_cache_is_surfaced(tmp_path: Path) -> None:
    """A TRACKED target/ build cache IS surfaced BUILD_CACHE (it ships → invalid)."""
    root = _make_rust_plugin(tmp_path, init_git=False)
    _write(root, "target/debug/app", "compiled")
    _init_git(root)  # commits target/ too → tracked
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("target/") == cda.CAT_BUILD_CACHE
    target = next(f for f in res.findings if f.path == "target/")
    assert target.tracked is True
    assert "target/" in res.gitignore_add
    # Two-sided: the lean bin/ is still not surfaced.
    assert "bin/" not in {f.path for f in res.findings}


def test_gitignored_untracked_cache_is_not_double_flagged(tmp_path: Path) -> None:
    """A gitignored+untracked target/ is already not-shipped → NOT a finding."""
    root = _make_rust_plugin(tmp_path, init_git=False)
    _write(root, ".gitignore", "target/\n")
    _init_git(root)  # commits .gitignore but NOT target/
    # Now create target/ AFTER commit → untracked + gitignored.
    _write(root, "target/debug/app", "compiled")
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "target/" not in finding_paths
    # Two-sided: rust/ (tracked source) IS still flagged — the skip is specific
    # to the gitignored-untracked path, not a blanket suppression.
    assert "rust/" in finding_paths


# ── ${CLAUDE_PLUGIN_ROOT} reference FN-safety ─────────────────────────────────


def test_referenced_script_dir_stays_runtime(tmp_path: Path) -> None:
    """A dir referenced via ${CLAUDE_PLUGIN_ROOT} is RUNTIME — never flagged.

    Uses a NON-reserved dir name (`runtime-helpers/`) so the protection comes
    from the ${CLAUDE_PLUGIN_ROOT} reference, not from _RESERVED_SRCS.
    """
    root = tmp_path / "refplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("refplug"))
    _write(
        root,
        "hooks/hooks.json",
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/runtime-helpers/x.sh"}]}
                    ]
                }
            }
        ),
    )
    _write(root, "runtime-helpers/x.sh", "#!/bin/bash\necho hi\n")
    # Sibling unreferenced dev dir — should still be flagged DEV_ONLY.
    _write(root, "design/notes.md", "design notes")
    _init_git(root)

    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    # The referenced helper dir is RUNTIME → NOT a finding.
    assert "runtime-helpers/" not in finding_paths
    # Two-sided: the unreferenced design/ dir IS flagged DEV_ONLY.
    assert "design/" in finding_paths
    assert next(f for f in res.findings if f.path == "design/").category == cda.CAT_DEV_ONLY


def test_referenced_path_from_mcp_json_stays_runtime(tmp_path: Path) -> None:
    """A path referenced from .mcp.json via ${CLAUDE_PLUGIN_ROOT} is RUNTIME."""
    root = tmp_path / "mcpplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("mcpplug"))
    _write(
        root,
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "srv": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/server-impl/index.js"]}
                }
            }
        ),
    )
    _write(root, "server-impl/index.js", "console.log('x')")
    _write(root, "server-impl/package.json", json.dumps({"name": "srv"}))
    _init_git(root)
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "server-impl/" not in finding_paths
    # Two-sided: prove the diagnostic would have flagged a sibling. Add an
    # unreferenced node_modules and re-diagnose.
    _write(root, "node_modules/dep/index.js", "x")
    _git_add_commit(root, "node_modules")
    res2 = cda.diagnose(root)
    paths2 = {f.path for f in res2.findings}
    assert "server-impl/" not in paths2  # still protected
    assert "node_modules/" in paths2  # unreferenced dep IS flagged


# ── _RESERVED_SRCS FN-safety ──────────────────────────────────────────────────


def test_reserved_srcs_never_flagged(tmp_path: Path) -> None:
    """Every _RESERVED_SRCS dir is RUNTIME — never a strip candidate.

    Even a `scripts/` dir full of build-only-looking files stays runtime,
    because _RESERVED_SRCS is an absolute ship-always guard.
    """
    root = tmp_path / "resplug"
    _write(root, ".claude-plugin/plugin.json", _manifest("resplug"))
    _write(root, "skills/foo/SKILL.md", "---\nname: foo\ndescription: d\n---\nbody")
    _write(root, "scripts/build_thing.py", "print('build')")  # build-looking, but reserved
    _write(root, "templates/tpl.txt", "template")
    # An actual non-reserved DEV dir to prove the diagnostic is active.
    _write(root, "examples/ex.md", "example")
    _init_git(root)
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "scripts/" not in finding_paths
    assert "templates/" not in finding_paths
    # Two-sided: examples/ (non-reserved DEV_ONLY) IS flagged.
    assert "examples/" in finding_paths


# ── Clean / lean plugin ───────────────────────────────────────────────────────


def test_clean_lean_plugin_has_no_findings(tmp_path: Path) -> None:
    """A lean plugin (only runtime components + root files) yields NO findings."""
    root = _make_clean_lean_plugin(tmp_path)
    res = cda.diagnose(root)
    assert res.findings == []
    assert res.strippable_bytes == 0
    assert res.strip_extract == []
    assert res.gitignore_add == []
    assert res.claude_plugin_data == []
    # Two-sided: a non-lean plugin in the same tmp DOES produce findings.
    rust = _make_rust_plugin(tmp_path)
    assert cda.diagnose(rust).findings != []


# ── DEV_ONLY classification + .github low-priority ────────────────────────────


def test_tests_dir_is_dev_only(tmp_path: Path) -> None:
    """A tests/ dir IS DEV_ONLY with a strip recommendation."""
    root = _make_clean_lean_plugin(tmp_path, init_git=False)
    _write(root, "tests/test_x.py", "def test_x(): assert True")
    _init_git(root)
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("tests/") == cda.CAT_DEV_ONLY
    tests_f = next(f for f in res.findings if f.path == "tests/")
    assert tests_f.strip_extract_entry is not None
    # Two-sided: skills/ (runtime) is not in findings.
    assert "skills/" not in cats


def test_github_is_low_priority_no_strip(tmp_path: Path) -> None:
    """`.github/` is surfaced (informational) but NOT a strip candidate and NOT
    counted as recoverable savings."""
    root = _make_clean_lean_plugin(tmp_path, init_git=False)
    _write(root, ".github/workflows/ci.yml", "name: ci\non: push")
    _init_git(root)
    res = cda.diagnose(root)
    gh = [f for f in res.findings if f.path == ".github/"]
    assert len(gh) == 1
    assert gh[0].category == cda.CAT_DEV_ONLY
    # Two-sided distinguishing properties vs a normal DEV_ONLY (tests/):
    assert gh[0].strip_extract_entry is None  # cannot strip CI
    assert ".github/" not in [e.src for e in res.strip_extract]
    # And it contributes nothing to strippable_bytes (since it's the only
    # finding here, strippable must be 0).
    assert res.strippable_bytes == 0


# ── already-stripped awareness ────────────────────────────────────────────────


def test_already_stripped_src_not_reflagged(tmp_path: Path) -> None:
    """A path already in cpv.strip.extract[] is NOT re-recommended."""
    root = tmp_path / "strippedplug"
    manifest = json.dumps(
        {
            "name": "strippedplug",
            "version": "0.1.0",
            "description": "x",
            "repository": "https://github.com/Emasoft/strippedplug",
            "cpv": {
                "strip": {
                    "extract": [
                        {"src": "tests/", "submodule": "Emasoft/strippedplug-tests", "submodule_path": "dev/tests/"}
                    ]
                }
            },
        }
    )
    _write(root, ".claude-plugin/plugin.json", manifest)
    _write(root, "skills/foo/SKILL.md", "---\nname: foo\ndescription: d\n---\nbody")
    _write(root, "tests/test_x.py", "def test_x(): pass")  # still physically present (the pointer)
    _write(root, "design/notes.md", "notes")  # NOT stripped → should flag
    _init_git(root)
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "tests/" not in finding_paths  # already stripped → suppressed
    # Two-sided: design/ (not stripped) IS flagged.
    assert "design/" in finding_paths


# ── vendored subtree FN-safety ────────────────────────────────────────────────


def test_vendored_dir_is_runtime(tmp_path: Path) -> None:
    """A vendored dir name (external/) is treated as runtime, never flagged."""
    root = _make_clean_lean_plugin(tmp_path, init_git=False)
    _write(root, "external/lib/thing.py", "x")
    # also add a non-vendored dev dir for the two-sided check
    _write(root, "docs/guide.md", "guide")
    _init_git(root)
    res = cda.diagnose(root)
    finding_paths = {f.path for f in res.findings}
    assert "external/" not in finding_paths  # vendored → runtime
    # Two-sided: docs/ (non-vendored DEV_ONLY) IS flagged.
    assert "docs/" in finding_paths


# ── JSON schema contract ──────────────────────────────────────────────────────


def test_json_schema_shape(tmp_path: Path) -> None:
    """The JSON dict matches the documented schema exactly (Agent B's contract)."""
    root = _make_rust_plugin(tmp_path)
    res = cda.diagnose(root)
    d = res.to_json_dict()
    # Top-level keys.
    assert set(d.keys()) == {
        "plugin_path",
        "total_tracked_bytes",
        "shipped_runtime_bytes",
        "strippable_bytes",
        "findings",
        "recommendations",
    }
    assert isinstance(d["plugin_path"], str)
    assert isinstance(d["total_tracked_bytes"], int)
    assert isinstance(d["shipped_runtime_bytes"], int)
    assert isinstance(d["strippable_bytes"], int)
    assert isinstance(d["findings"], list)
    # recommendations keys.
    recs = d["recommendations"]
    assert isinstance(recs, dict)
    assert set(recs.keys()) == {"strip_extract", "gitignore_add", "claude_plugin_data"}
    assert isinstance(recs["strip_extract"], list)
    assert isinstance(recs["gitignore_add"], list)
    assert isinstance(recs["claude_plugin_data"], list)
    # Finding shape (rust/ is present).
    finding = next(f for f in d["findings"] if f["path"] == "rust/")
    assert set(finding.keys()) == {
        "path",
        "category",
        "bytes",
        "reason",
        "remediation",
        "strip_extract_entry",
        "tracked",
    }
    assert finding["category"] == "BUILD_SOURCE"
    assert isinstance(finding["bytes"], int)
    assert isinstance(finding["reason"], str)
    assert isinstance(finding["remediation"], str)
    assert isinstance(finding["tracked"], bool)
    # strip_extract_entry is an object with exactly src + submodule.
    sxe = finding["strip_extract_entry"]
    assert isinstance(sxe, dict)
    assert set(sxe.keys()) == {"src", "submodule"}


def test_json_strip_extract_entry_is_null_for_runtime_dep(tmp_path: Path) -> None:
    """A RUNTIME_DEP finding serialises strip_extract_entry as JSON null."""
    root = _make_node_modules_plugin(tmp_path)
    # Round-trip through real JSON so the consumer's parsed view is what we test
    # (this is exactly how Agent B sees it). json.loads is typed Any → iterable.
    d = json.loads(json.dumps(cda.diagnose(root).to_json_dict()))
    nm = next(f for f in d["findings"] if f["path"] == "node_modules/")
    assert nm["strip_extract_entry"] is None
    # Two-sided: a BUILD_SOURCE-style plugin would have a non-null entry.
    rust_root = _make_rust_plugin(root.parent)
    rd = json.loads(json.dumps(cda.diagnose(rust_root).to_json_dict()))
    rust = next(f for f in rd["findings"] if f["path"] == "rust/")
    assert rust["strip_extract_entry"] is not None


# ── CLI contract ──────────────────────────────────────────────────────────────


def test_cli_json_emits_only_json_and_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` writes ONLY valid JSON to stdout and returns 0."""
    root = _make_rust_plugin(tmp_path)
    rc = cda.main([str(root), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)  # stdout must be parseable as a single JSON doc
    assert parsed["plugin_path"] == str(root.resolve())
    assert any(f["path"] == "rust/" for f in parsed["findings"])


def test_cli_human_output_has_numbered_table_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Human output has a leftmost `#` column and the savings summary line."""
    root = _make_rust_plugin(tmp_path)
    rc = cda.main([str(root)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Potential install savings:" in out
    assert " 1  rust/" in out or " 1  " in out  # numbered row
    # Two-sided: a lean plugin says there is nothing to do.
    lean = _make_clean_lean_plugin(tmp_path)
    cda.main([str(lean)])
    out2 = capsys.readouterr().out
    assert "lean" in out2.lower()


def test_cli_exits_0_on_missing_path(capsys: pytest.CaptureFixture[str]) -> None:
    """A non-existent target → empty advisory result, exit 0 (never blocks)."""
    rc = cda.main(["/nonexistent/path/xyz", "--json"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["findings"] == []
    assert parsed["total_tracked_bytes"] == 0


# ── read-only guarantee ───────────────────────────────────────────────────────


def test_diagnose_does_not_mutate_tree(tmp_path: Path) -> None:
    """diagnose() is read-only — the file inventory is unchanged afterwards."""
    root = _make_rust_plugin(tmp_path)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    cda.diagnose(root)
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert before == after


# ── non-git tree behavior ─────────────────────────────────────────────────────


def test_non_git_tree_scans_all(tmp_path: Path) -> None:
    """In a non-git tree, the present tree IS the artifact → everything counts."""
    root = _make_rust_plugin(tmp_path, init_git=False)  # no git
    res = cda.diagnose(root)
    cats = {f.path: f.category for f in res.findings}
    assert cats.get("rust/") == cda.CAT_BUILD_SOURCE
    # tracked flag is True when git is unavailable (we cannot prove unshipped).
    rust = next(f for f in res.findings if f.path == "rust/")
    assert rust.tracked is True
