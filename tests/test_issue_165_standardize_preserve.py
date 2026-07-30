"""Issue #165 — standardize must (B3) migrate an EXISTING publish.py to mint the
`{name}--v{version}` dependency-resolution tag, and (B4) merge rather than clobber
the canon config files.

Every rule is tested TWO-SIDED on a REAL plugin directory written to disk (no
mocking of the code under test):

  * POSITIVE — a bad input is fixed.
  * NEGATIVE — an already-correct input comes back byte-identical (no-op).

Plus an explicit IDEMPOTENCE test (run twice => same bytes) and a test proving
`--fix` never OVERWRITES scripts/publish.py with the canonical template.

WHY B3 matters: since Claude Code 2.1.110 a version-constrained plugin dependency
resolves ONLY against a `{name}--v{version}` tag (DOUBLE hyphen). A publish
pipeline that mints only `vX.Y.Z` therefore ships releases nobody can depend on —
every dependent fails to install with `no-matching-tag` and is DISABLED.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _force_template_skip_reason,
    _merge_canon_json,
    _merge_canon_yaml,
    _publish_py_has_dependency_tag_stage,
    migrate_publish_py_dependency_tag,
)

# ---------------------------------------------------------------------------
# Fixtures — real files on disk
# ---------------------------------------------------------------------------

# A faithful reduction of the PRE-v2.156 canonical publish.py: it tags `v{ver}`
# and pushes it atomically, but never mints the dependency-resolution tag. This
# is the exact shape the 11 blocked plugins ship.
LEGACY_PUBLISH_PY = '''\
#!/usr/bin/env python3
"""Publish pipeline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=str(cwd), check=True)


def git_with_retry(argv: list[str], cwd: str, capture_output: bool = False) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def _local_tag_exists(root: Path, tag: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True, text=True, cwd=str(root), check=False,
    )
    return r.returncode == 0


# The release stage. Commits, tags, pushes.
def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Step 10: Commit, tag, push."""
    tag = f"v{new_ver}"
    tag_exists = _local_tag_exists(root, tag)

    if dry_run:
        print(f"  Would push (atomic): origin HEAD {tag}")
        return

    run(["git", "add", "-A"], cwd=root)
    run(["git", "commit", "-m", f"chore: bump version to {new_ver}"], cwd=root)

    if not tag_exists:
        run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)

    print(f"  $ git push --atomic origin HEAD {tag}")
    git_with_retry(
        ["git", "push", "--atomic", "origin", "HEAD", tag],
        cwd=str(root), capture_output=False,
    )


def main() -> int:
    stage_commit_and_push(Path.cwd(), "1.0.0", False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# The POST-v2.156 canonical shape: already mints the dependency tag. A migration
# run over this must be a strict no-op (the negative control for B3).
CANONICAL_PUBLISH_PY = LEGACY_PUBLISH_PY.replace(
    '    tag = f"v{new_ver}"\n',
    '    tag = f"v{new_ver}"\n'
    "    dep_tag = _dependency_tag_name(root, new_ver)\n",
).replace(
    '        ["git", "push", "--atomic", "origin", "HEAD", tag],\n',
    '        ["git", "push", "--atomic", "origin", "HEAD", tag] + ([dep_tag] if dep_tag else []),\n',
).replace(
    "# The release stage. Commits, tags, pushes.\n",
    'def _dependency_tag_name(root: Path, new_ver: str) -> str | None:\n'
    '    """The `{plugin-name}--v{version}` tag."""\n'
    '    pj = root / ".claude-plugin" / "plugin.json"\n'
    "    name = json.loads(pj.read_text(encoding=\"utf-8\")).get(\"name\")\n"
    '    return f"{name}--v{new_ver}" if name else None\n'
    "\n"
    "\n"
    "# The release stage. Commits, tags, pushes.\n",
)


def _make_plugin(tmp_path: Path, name: str = "my-plugin", publish_py: str | None = None) -> Path:
    """Write a minimal REAL plugin tree to disk and return its root."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": "test"}) + "\n",
        encoding="utf-8",
    )
    if publish_py is not None:
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "publish.py").write_text(publish_py, encoding="utf-8")
    return root


# ===========================================================================
# B3 — dependency-resolution tag injection
# ===========================================================================


def test_legacy_publish_py_gains_the_dependency_tag_stage(tmp_path: Path) -> None:
    """POSITIVE: a publish.py that tags only `v{ver}` gains the `{name}--v{ver}` stage."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    publish = root / "scripts" / "publish.py"
    assert not _publish_py_has_dependency_tag_stage(publish.read_text(encoding="utf-8"))

    notes = migrate_publish_py_dependency_tag(root)

    body = publish.read_text(encoding="utf-8")
    assert notes and "injected" in notes[0]
    assert _publish_py_has_dependency_tag_stage(body)
    assert "_cpv_dependency_push_refs(root, new_ver)" in body
    assert 'f"{name}--v{new_ver}"' in body


def test_injected_publish_py_still_compiles(tmp_path: Path) -> None:
    """POSITIVE: the migrated publish.py is syntactically valid Python."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    migrate_publish_py_dependency_tag(root)
    publish = root / "scripts" / "publish.py"

    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(publish)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_injected_tag_name_comes_from_the_manifest_not_the_directory(tmp_path: Path) -> None:
    """POSITIVE: the tag name is derived from plugin.json, never the folder name."""
    root = tmp_path / "some-checkout-dir"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "claude-menu-system", "version": "0.2.1"}) + "\n", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "publish.py").write_text(LEGACY_PUBLISH_PY, encoding="utf-8")

    migrate_publish_py_dependency_tag(root)

    # Execute the injected helper against the real manifest to prove the derived name.
    sys.path.insert(0, str(root / "scripts"))
    try:
        spec_src = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
        namespace: dict[str, object] = {"__name__": "injected_publish"}
        exec(compile(spec_src, "publish.py", "exec"), namespace)  # noqa: S102
        tag_name = namespace["_cpv_dependency_tag_name"](root, "0.2.1")  # type: ignore[operator]
    finally:
        sys.path.remove(str(root / "scripts"))

    assert tag_name == "claude-menu-system--v0.2.1"
    assert "some-checkout-dir" not in str(tag_name)


def test_double_hyphen_separator_is_used(tmp_path: Path) -> None:
    """POSITIVE: the separator is `--v` — a single `-v` resolves nothing."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    migrate_publish_py_dependency_tag(root)
    body = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert '--v{new_ver}' in body


def test_canonical_publish_py_is_untouched(tmp_path: Path) -> None:
    """NEGATIVE: a publish.py that already mints the dependency tag is a no-op."""
    root = _make_plugin(tmp_path, publish_py=CANONICAL_PUBLISH_PY)
    publish = root / "scripts" / "publish.py"
    before = publish.read_bytes()

    notes = migrate_publish_py_dependency_tag(root)

    assert notes == []
    assert publish.read_bytes() == before


def test_publish_py_that_tags_nothing_is_untouched(tmp_path: Path) -> None:
    """NEGATIVE: a publish.py with no tagging stage is left alone (nothing to migrate)."""
    body = "#!/usr/bin/env python3\nprint('no tags here')\n"
    root = _make_plugin(tmp_path, publish_py=body)
    publish = root / "scripts" / "publish.py"

    notes = migrate_publish_py_dependency_tag(root)

    assert notes == []
    assert publish.read_text(encoding="utf-8") == body


def test_absent_publish_py_is_a_no_op(tmp_path: Path) -> None:
    """NEGATIVE: a plugin with no scripts/publish.py reports nothing and creates nothing."""
    root = _make_plugin(tmp_path)
    assert migrate_publish_py_dependency_tag(root) == []
    assert not (root / "scripts" / "publish.py").exists()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """IDEMPOTENCE: running the migration twice injects the stage exactly once."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    publish = root / "scripts" / "publish.py"

    first_notes = migrate_publish_py_dependency_tag(root)
    after_first = publish.read_bytes()
    second_notes = migrate_publish_py_dependency_tag(root)
    after_second = publish.read_bytes()

    assert first_notes and second_notes == []
    assert after_first == after_second
    assert after_second.decode().count("def _cpv_dependency_tag_name") == 1
    assert after_second.decode().count("_cpv_dependency_push_refs(root, new_ver)") == 1


def test_dry_run_reports_but_writes_nothing(tmp_path: Path) -> None:
    """NEGATIVE (write side): --dry-run reports the migration without touching the file."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    publish = root / "scripts" / "publish.py"
    before = publish.read_bytes()

    notes = migrate_publish_py_dependency_tag(root, dry_run=True)

    assert notes and notes[0].startswith("[dry-run]")
    assert publish.read_bytes() == before


def test_unrecognisable_publish_py_is_reported_not_half_migrated(tmp_path: Path) -> None:
    """FAIL-FAST: a publish.py whose push shape is unknown is surfaced, never partially edited."""
    body = (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import subprocess\n"
        "from pathlib import Path\n"
        "\n\n"
        "def release(root: Path, new_ver: str) -> None:\n"
        '    subprocess.run(["git", "tag", "-a", f"v{new_ver}"], cwd=str(root), check=True)\n'
        '    subprocess.run(["git", "push", "origin", "--tags"], cwd=str(root), check=True)\n'
    )
    root = _make_plugin(tmp_path, publish_py=body)
    publish = root / "scripts" / "publish.py"

    notes = migrate_publish_py_dependency_tag(root)

    assert notes and "CANNOT be migrated automatically" in notes[0]
    assert publish.read_text(encoding="utf-8") == body


def test_migrated_pipeline_creates_both_tags_in_a_real_git_repo(tmp_path: Path) -> None:
    """POSITIVE (end-to-end, real git): the migrated publish.py tags BOTH refs.

    The only proof that matters — execute the injected code against a real repo and
    read the real tag list. `v1.2.3` alone is un-dependable; `my-plugin--v1.2.3` is
    what Claude Code's resolver actually looks for.
    """
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "init"],
    ):
        subprocess.run(argv, cwd=str(root), check=True, capture_output=True)

    migrate_publish_py_dependency_tag(root)

    # Execute the migrated module and drive its real tag-creating helper.
    namespace: dict[str, object] = {"__name__": "injected_publish"}
    src = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    exec(compile(src, "publish.py", "exec"), namespace)  # noqa: S102
    subprocess.run(["git", "tag", "-a", "v1.2.3", "-m", "Release v1.2.3"], cwd=str(root), check=True)
    refs = namespace["_cpv_dependency_push_refs"](root, "1.2.3")  # type: ignore[operator]

    tags = subprocess.run(
        ["git", "tag", "--list"], cwd=str(root), capture_output=True, text=True, check=True
    ).stdout.split()

    assert refs == ["my-plugin--v1.2.3"]
    assert "v1.2.3" in tags
    assert "my-plugin--v1.2.3" in tags, "the dependency-resolution tag was never created"
    # Running it again must not fail on an existing tag (idempotent tag creation).
    assert namespace["_cpv_dependency_push_refs"](root, "1.2.3") == ["my-plugin--v1.2.3"]  # type: ignore[operator]


def test_migration_runs_under_plain_fix_not_only_force_templates(tmp_path: Path) -> None:
    """POSITIVE: a plain `--fix` (no --force-templates) migrates publish.py."""
    root = _make_plugin(tmp_path, publish_py=LEGACY_PUBLISH_PY)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "standardize_plugin.py"), str(root), "--fix"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "NO_COLOR": "1", "HOME": str(tmp_path)},
    )
    body = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert _publish_py_has_dependency_tag_stage(body), result.stdout + result.stderr
    assert "[dep-tag]" in result.stdout


def test_plain_fix_does_not_overwrite_publish_py(tmp_path: Path) -> None:
    """NEGATIVE: `--fix` never replaces an existing publish.py with the canonical template.

    The plugin's own code (its custom marker function) must survive the run — only the
    dependency-tag stage may be added. This is the #145/#140 profile-aware protection
    that issue #165 must not regress.
    """
    custom = LEGACY_PUBLISH_PY + '\n\ndef _plugin_specific_marker() -> str:\n    return "keep me"\n'
    root = _make_plugin(tmp_path, publish_py=custom)

    subprocess.run(
        [sys.executable, str(SCRIPTS / "standardize_plugin.py"), str(root), "--fix"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "NO_COLOR": "1", "HOME": str(tmp_path)},
    )

    body = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert "_plugin_specific_marker" in body
    assert '"""Publish pipeline."""' in body


def test_freshly_scaffolded_canonical_publish_py_is_never_double_patched(tmp_path: Path) -> None:
    """NEGATIVE (real template): the CURRENT canonical publish.py already mints the tag.

    Drift guard: a plugin scaffolded today must be a strict no-op for this migration.
    If the generator ever LOSES the dependency-tag stage, this test fails loudly
    instead of the migration silently patching every freshly-generated plugin.
    """
    from generate_plugin_repo import PluginParams, gen_publish_py

    canon = gen_publish_py(
        PluginParams(
            name="my-plugin",
            description="d",
            author="a",
            author_email="a@example.com",
            github_owner="o",
            version="1.0.0",
        )
    )
    assert _publish_py_has_dependency_tag_stage(canon), "the canonical template lost the dependency tag"

    root = _make_plugin(tmp_path, publish_py=canon)
    publish = root / "scripts" / "publish.py"
    before = publish.read_bytes()

    assert migrate_publish_py_dependency_tag(root) == []
    assert publish.read_bytes() == before


# ===========================================================================
# B4 — merge (never clobber) the canon config files
# ===========================================================================


def test_json_merge_preserves_a_custom_key(tmp_path: Path) -> None:
    """POSITIVE: a plugin's own `MD010` suppression survives the canon merge.

    Load-bearing: that plugin's skill documents Makefile recipes, which REQUIRE
    literal tabs — without the suppression markdownlint blocks `--strict`.
    """
    plugin_cfg = json.dumps({"MD013": True, "MD010": {"code_blocks": False}}, indent=2) + "\n"
    canon_cfg = json.dumps({"MD013": False, "MD024": False}, indent=2) + "\n"

    merged_text, preserved = _merge_canon_json(plugin_cfg, canon_cfg)

    assert merged_text is not None
    merged = json.loads(merged_text)
    assert merged["MD010"] == {"code_blocks": False}  # plugin's own key kept
    assert merged["MD013"] is False  # canon wins on a canon key
    assert merged["MD024"] is False  # canon key merged IN
    assert preserved == ["MD010"]


def test_json_merge_is_a_no_op_when_there_is_nothing_custom(tmp_path: Path) -> None:
    """NEGATIVE: a plugin file with only canon keys yields canon and preserves nothing."""
    canon_cfg = json.dumps({"MD013": False, "MD024": False}, indent=2) + "\n"
    plugin_cfg = json.dumps({"MD013": True}, indent=2) + "\n"

    merged_text, preserved = _merge_canon_json(plugin_cfg, canon_cfg)

    assert preserved == []
    assert merged_text is not None
    assert json.loads(merged_text) == {"MD013": False, "MD024": False}


def test_json_merge_declines_a_non_object_config() -> None:
    """NEGATIVE: an unparseable / non-object config has no keys to preserve."""
    assert _merge_canon_json("[1, 2, 3]", "{}") == (None, [])
    assert _merge_canon_json("{not json", "{}") == (None, [])


def test_force_templates_merges_markdownlint_json(tmp_path: Path) -> None:
    """POSITIVE (end-to-end): --force-templates keeps MD010 while importing canon keys."""
    root = _make_plugin(tmp_path)
    cfg = root / ".markdownlint.json"
    cfg.write_text(json.dumps({"MD010": {"code_blocks": False}}, indent=2) + "\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "standardize_plugin.py"),
            str(root),
            "--fix",
            "--force-templates",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "NO_COLOR": "1", "HOME": str(tmp_path)},
    )

    merged = json.loads(cfg.read_text(encoding="utf-8"))
    assert merged["MD010"] == {"code_blocks": False}, "the load-bearing suppression was clobbered"
    assert merged["MD013"] is False, "canon key was not merged in"


def test_yaml_stale_plugin_still_receives_the_canon_keys_it_lacks(tmp_path: Path) -> None:
    """NEGATIVE: a merely-STALE file still gets canon's newer keys imported.

    Preserving the author's values must not degrade into "never refresh anything":
    a key canon adds and the plugin does not have is still pulled in.
    """
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n  - SPELL_CSPELL\n"
    plugin = "APPLY_FIXES: none\n"

    merged, _kept, added = _merge_canon_yaml(plugin, canon)

    assert added == ["ENABLE_LINTERS"]
    assert "SPELL_CSPELL" in merged


def test_yaml_merge_preserves_a_custom_value_inside_a_canon_key(tmp_path: Path) -> None:
    """POSITIVE: the REAL #165 shape — a custom VALUE inside a key canon also declares.

    This is the case a custom-KEY detector is structurally blind to, and it is the one
    actually reported: the author extended canon's REPOSITORY_CHECKOV_ARGUMENTS with
    ",CKV_DOCKER_2" rather than adding a new key. The value and its rationale comment
    must both survive a --force-templates run.
    """
    plugin_yaml = (
        "APPLY_FIXES: none\n"
        "\n"
        "# Checkov — skip workflow-level permission checks (we set permissions per-job).\n"
        "# Also skip CKV_DOCKER_2 (HEALTHCHECK): every Dockerfile here is an ephemeral\n"
        "# run-once container, so a HEALTHCHECK is inapplicable.\n"
        'REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1,CKV_DOCKER_2"\n'
    )
    canon = (
        "APPLY_FIXES: none\n"
        "\n"
        "# Checkov — skip workflow-level permission checks.\n"
        'REPOSITORY_CHECKOV_ARGUMENTS: "--skip-check CKV2_GHA_1"\n'
        "\n"
        "ENABLE_LINTERS:\n"
        "  - PYTHON_RUFF\n"
    )

    merged, kept, added = _merge_canon_yaml(plugin_yaml, canon)

    # The suppression and the reason it exists both survive.
    assert "CKV_DOCKER_2" in merged
    assert "ephemeral" in merged
    # Canon's competing value did NOT overwrite the author's.
    assert '"--skip-check CKV2_GHA_1"\n' not in merged
    assert "REPOSITORY_CHECKOV_ARGUMENTS" in kept
    # A canon key the plugin lacked is still imported.
    assert "ENABLE_LINTERS" in added
    assert "PYTHON_RUFF" in merged


def test_yaml_merge_preserves_a_custom_top_level_key_and_its_comments(tmp_path: Path) -> None:
    """POSITIVE: a top-level key canon does not declare survives, with its comments."""
    plugin_yaml = (
        "APPLY_FIXES: none\n"
        "\n"
        "# We handle PR comments ourselves.\n"
        "GITHUB_COMMENT_REPORTER: false\n"
    )
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    merged, _kept, added = _merge_canon_yaml(plugin_yaml, canon)

    assert "GITHUB_COMMENT_REPORTER: false" in merged
    assert "We handle PR comments ourselves." in merged
    assert "ENABLE_LINTERS" in added


def test_yaml_merge_is_a_noop_on_a_canon_identical_file(tmp_path: Path) -> None:
    """NEGATIVE: a file already at canon is returned byte-identical (no false churn)."""
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    merged, kept, added = _merge_canon_yaml(canon, canon)

    assert merged == canon
    assert kept == []
    assert added == []


def test_yaml_merge_is_idempotent(tmp_path: Path) -> None:
    """NEGATIVE: merging twice adds the canon keys exactly once."""
    plugin_yaml = "APPLY_FIXES: none\n"
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    once, _k1, _a1 = _merge_canon_yaml(plugin_yaml, canon)
    twice, _k2, added2 = _merge_canon_yaml(once, canon)

    assert twice == once
    assert added2 == []
    assert once.count("ENABLE_LINTERS") == 1


def test_yaml_merge_output_is_parseable_yaml(tmp_path: Path) -> None:
    """NEGATIVE: the merged file is still valid YAML (an append must not corrupt it)."""
    yaml = pytest.importorskip("yaml")
    plugin_yaml = "APPLY_FIXES: none\n# keep me\nGITHUB_COMMENT_REPORTER: false\n"
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    merged, _kept, _added = _merge_canon_yaml(plugin_yaml, canon)
    parsed = yaml.safe_load(merged)

    assert parsed["GITHUB_COMMENT_REPORTER"] is False
    assert parsed["ENABLE_LINTERS"] == ["PYTHON_RUFF"]


def test_force_template_no_longer_skips_yaml_it_merges_instead(tmp_path: Path) -> None:
    """NEGATIVE: the YAML path returns no skip reason — the caller merges it."""
    root = _make_plugin(tmp_path)
    cfg = root / ".mega-linter.yml"
    cfg.write_text("APPLY_FIXES: none\nGITHUB_COMMENT_REPORTER: false\n", encoding="utf-8")
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    assert (
        _force_template_skip_reason(cfg, ".mega-linter.yml", canon, set(), root, "standard")
        is None
    )


def test_force_overwrite_proceeds_for_a_yaml_with_no_custom_keys(tmp_path: Path) -> None:
    """NEGATIVE: a canon-only .mega-linter.yml is NOT skipped — it still gets refreshed."""
    root = _make_plugin(tmp_path)
    cfg = root / ".mega-linter.yml"
    cfg.write_text("APPLY_FIXES: all\n", encoding="utf-8")
    canon = "APPLY_FIXES: none\nENABLE_LINTERS:\n  - PYTHON_RUFF\n"

    assert (
        _force_template_skip_reason(cfg, ".mega-linter.yml", canon, set(), root, "standard")
        is None
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
