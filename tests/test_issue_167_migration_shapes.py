"""Issue #167 — the #165 dependency-tag migration must handle EVERY release-push shape.

The #165 migration was anchored on ONE regex (`["git","push","--atomic","origin",
"HEAD",tag]`) and one def-signature check (parameters literally named `root` + `new_ver`).
Measured against the real fleet, that rewrote only 4 of 13 publish.py files. The other
nine were SILENTLY SKIPPED — and a silent skip is the exact defect #165 exists to fix:
the plugin keeps shipping releases nothing can depend on, and nothing says so.

Four release-push shapes exist in the wild:

  A  ["git","push","--atomic","origin","HEAD", tag, dep_tag]        (already migrated)
  B  ["git","push","--atomic","origin","HEAD", tag, resolver_tag]   (ditto, other name)
  C  ["git","push","--atomic","origin","HEAD", tag]
  D  ["git","push","origin","HEAD"] THEN ["git","push","origin", f"v{new_version}"]

and the enclosing function is not always `stage_commit_and_push(root, new_ver)` — it may
be a parameterless `main()` binding `plugin_root`/`new_version` as locals, or a
`_git_push(new_version)` reading a module-level `REPO_ROOT`.

Every rule below is tested TWO-SIDED:

  * POSITIVE — a shape that must migrate does, the output compiles, and the emitted call
    binds the names that are actually in scope.
  * NEGATIVE — a file that must NOT be rewritten comes back byte-identical, and one that
    CANNOT be migrated is reported LOUDLY (a non-empty note), never half-edited.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _inject_dependency_tag_stage,
    _publish_py_has_dependency_tag_stage,
    migrate_publish_py_dependency_tag,
)

# ---------------------------------------------------------------------------
# Fixtures — faithful reductions of the REAL fleet publish.py shapes.
# ---------------------------------------------------------------------------

_PREAMBLE = """\
#!/usr/bin/env python3
\"\"\"Publish pipeline.\"\"\"

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=str(cwd), check=True)
"""

# Shape C — the canonical pre-v2.156 single-call push, `--atomic`, tag bound to `tag`,
# inside `stage_commit_and_push(root, new_ver)`.
SHAPE_C = (
    _PREAMBLE
    + '''

# The release stage. Commits, tags, pushes.
def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Commit, tag, push."""
    tag = f"v{new_ver}"
    run(["git", "commit", "-m", f"release: v{new_ver}"], cwd=root)
    run(["git", "tag", "-a", tag, "-m", tag], cwd=root)
    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=root)
'''
)

# Shape D — TWO pushes (HEAD, then the tag) from a PARAMETERLESS `main()`; the names the
# helper needs are LOCALS (`plugin_root`, `new_version`), not parameters. Six real fleet
# plugins are shaped like this, and the pre-#167 migration refused every one of them.
SHAPE_D = (
    _PREAMBLE
    + '''

def main() -> int:
    """Run the release."""
    git_root = Path.cwd()
    plugin_root = Path.cwd()
    current = "1.0.0"
    new_version = "1.0.1"
    run(["git", "commit", "-m", f"release: v{new_version}"], cwd=git_root)
    run(["git", "tag", "-a", f"v{new_version}", "-m", current], cwd=git_root)
    run(["git", "push", "origin", "HEAD"], cwd=git_root)
    run(["git", "push", "origin", f"v{new_version}"], cwd=git_root)
    return 0
'''
)

# Shape C with the root as a MODULE-LEVEL constant and no root parameter at all — the
# real `ai-maestro-visual-communicator-plugin` vintage.
SHAPE_MODULE_ROOT = (
    _PREAMBLE
    + '''
REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_push(new_version: str) -> None:
    """Push the release."""
    tag = f"v{new_version}"
    run(["git", "tag", "-a", tag, "-m", tag], cwd=REPO_ROOT)
    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=REPO_ROOT)
'''
)


def _already_migrated(tag_var: str) -> str:
    """A publish.py that ALREADY mints the dependency tag, under an arbitrary var name.

    The name is the whole point: the pre-#167 predicate keyed on `dependency_tag`/
    `dep_tag`, so a plugin that derived the tag correctly but called it `resolver_tag`
    was reported as never emitting it — and would have been injected with a SECOND
    stage, pushing two conflicting refs.
    """
    return (
        _PREAMBLE
        + f'''

def stage_commit_and_push(root: Path, new_ver: str, dry_run: bool) -> None:
    """Commit, tag, push."""
    tag = f"v{{new_ver}}"
    name = json.loads((root / ".claude-plugin" / "plugin.json").read_text()).get("name")
    {tag_var} = f"{{name}}--v{{new_ver}}"
    run(["git", "tag", "-a", tag, "-m", tag], cwd=root)
    run(["git", "tag", "-a", {tag_var}, "-m", {tag_var}], cwd=root)
    run(["git", "push", "--atomic", "origin", "HEAD", tag, {tag_var}], cwd=root)
'''
    )


def _make_plugin(tmp_path: Path, publish_py: str, name: str = "my-plugin") -> Path:
    """Write a minimal REAL plugin tree to disk and return its root."""
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": "t"}) + "\n", encoding="utf-8"
    )
    (root / "scripts").mkdir()
    (root / "scripts" / "publish.py").write_text(publish_py, encoding="utf-8")
    return root


def _emitted_call(body: str) -> str:
    """The injected CALL SITE (not the helper's own `def` line)."""
    for line in body.splitlines():
        if "_cpv_dependency_push_refs(" in line and not line.lstrip().startswith("def "):
            return line.strip()
    return ""


# ===========================================================================
# POSITIVE — every un-migrated shape is rewritten
# ===========================================================================


def test_shape_c_single_atomic_push_migrates() -> None:
    """POSITIVE: the canonical `--atomic ... HEAD, tag` push gains the stage."""
    new_text, note = _inject_dependency_tag_stage(SHAPE_C)

    assert new_text is not None
    assert "injected" in note
    assert "_cpv_dependency_push_refs(root, new_ver)" in _emitted_call(new_text)
    compile(new_text, "publish.py", "exec")


def test_shape_d_two_call_push_from_parameterless_main_migrates() -> None:
    """POSITIVE: the two-call shape migrates — the fix's whole reason for existing.

    Six real fleet plugins push HEAD and the tag in SEPARATE calls from a parameterless
    `main()`. The pre-#167 anchor matched neither call and its def-scope check found no
    `root`/`new_ver` parameters, so all six were silently skipped.
    """
    new_text, note = _inject_dependency_tag_stage(SHAPE_D)

    assert new_text is not None
    assert "injected" in note
    # The names are LOCALS of `main()`, detected from the push's real scope.
    assert "_cpv_dependency_push_refs(plugin_root, new_version)" in _emitted_call(new_text)
    compile(new_text, "publish.py", "exec")


def test_shape_d_extends_the_tag_push_and_leaves_the_head_push_alone() -> None:
    """POSITIVE: only the TAG push is extended — the HEAD-only push is not a tag ref."""
    new_text, _ = _inject_dependency_tag_stage(SHAPE_D)

    assert new_text is not None
    head_push = [ln for ln in new_text.splitlines() if '"push", "origin", "HEAD"' in ln]
    tag_push = [ln for ln in new_text.splitlines() if 'f"v{new_version}"' in ln and '"push"' in ln]
    assert len(head_push) == 1 and "_cpv_dependency_push_refs" not in head_push[0]
    assert len(tag_push) == 1 and "_cpv_dependency_push_refs" in tag_push[0]


def test_module_level_root_constant_is_used_when_no_root_parameter_exists() -> None:
    """POSITIVE: a root held in a module GLOBAL (`REPO_ROOT`) is found and used.

    `_git_push(new_version)` takes no root at all, so a parameter-only scope check
    refuses it. The global is in scope at the push, so the migration binds to it.
    """
    new_text, _ = _inject_dependency_tag_stage(SHAPE_MODULE_ROOT)

    assert new_text is not None
    assert "_cpv_dependency_push_refs(REPO_ROOT, new_version)" in _emitted_call(new_text)
    compile(new_text, "publish.py", "exec")


def test_injected_helpers_land_at_module_level_below_the_imports() -> None:
    """POSITIVE: the helpers are inserted above the pushing def, never above the imports.

    They call json/subprocess and annotate with Path — placed above the module's own
    imports they could not resolve, and the file would not run.
    """
    new_text, _ = _inject_dependency_tag_stage(SHAPE_D)

    assert new_text is not None
    lines = new_text.splitlines()
    import_idx = next(i for i, ln in enumerate(lines) if ln.startswith("import subprocess"))
    helper_idx = next(i for i, ln in enumerate(lines) if ln.startswith("def _cpv_dependency_tag_name"))
    def_idx = next(i for i, ln in enumerate(lines) if ln.startswith("def main("))
    assert import_idx < helper_idx < def_idx


def test_every_migrated_shape_compiles_and_carries_the_helper() -> None:
    """POSITIVE: no shape is left half-migrated — output parses AND has the helper."""
    for label, src in (("C", SHAPE_C), ("D", SHAPE_D), ("module-root", SHAPE_MODULE_ROOT)):
        new_text, note = _inject_dependency_tag_stage(src)
        assert new_text is not None, f"shape {label} was refused: {note}"
        compile(new_text, "publish.py", "exec")
        assert "def _cpv_dependency_tag_name" in new_text
        assert 'f"{name}--v{new_ver}"' in new_text


# ===========================================================================
# NEGATIVE — an already-correct file is a byte-identical no-op
# ===========================================================================


def test_already_migrated_under_any_variable_name_is_a_no_op() -> None:
    """NEGATIVE: the stage is detected by SHAPE, not by the variable's NAME.

    `dep_tag`, `resolver_tag`, `whatever` — all build `{name}--v{ver}` and all must be
    left alone. Keying on the name would double-inject the stage into a file that
    already has it, and the release would push two conflicting tag refs.
    """
    for tag_var in ("dep_tag", "dependency_tag", "resolver_tag", "whatever_ref"):
        body = _already_migrated(tag_var)
        assert _publish_py_has_dependency_tag_stage(body), tag_var

        new_text, note = _inject_dependency_tag_stage(body)

        assert new_text is None, f"{tag_var} was rewritten — it already ships the tag"
        assert note == "", f"{tag_var} produced a spurious note: {note!r}"


def test_already_migrated_file_on_disk_comes_back_byte_identical(tmp_path: Path) -> None:
    """NEGATIVE (write side): a no-op writes nothing at all."""
    root = _make_plugin(tmp_path, _already_migrated("resolver_tag"))
    publish = root / "scripts" / "publish.py"
    before = publish.read_bytes()

    notes = migrate_publish_py_dependency_tag(root)

    assert notes == []
    assert publish.read_bytes() == before


# ===========================================================================
# NEGATIVE — an unmigratable file is LOUD, never half-edited
# ===========================================================================


def test_no_recognisable_push_is_reported_loudly_not_skipped_silently() -> None:
    """NEGATIVE: a bulk `git push --tags` has no ref list to extend — refuse + REPORT.

    The note MUST be non-empty. A silent skip is the exact defect #165/#167 exist to
    fix: the plugin keeps shipping un-dependable releases and nothing tells anyone.
    """
    body = _PREAMBLE + '''

def release(root: Path, new_ver: str) -> None:
    """Tag and push."""
    run(["git", "tag", "-a", f"v{new_ver}", "-m", "r"], cwd=root)
    run(["git", "push", "origin", "--tags"], cwd=root)
'''

    new_text, note = _inject_dependency_tag_stage(body)

    assert new_text is None
    assert note, "an unmigratable publish.py MUST still be reported"
    assert "CANNOT be migrated automatically" in note
    assert "release-push shape was not recognised" in note


def test_ambiguous_release_pushes_are_refused_never_half_migrated() -> None:
    """NEGATIVE: two equally-plausible tag pushes — extend neither, report instead.

    Guessing which one ships the release would report success while the plugin still
    published an un-dependable release from the other.
    """
    body = _PREAMBLE + '''

def release(root: Path, new_ver: str) -> None:
    """Tag and push, twice."""
    tag = f"v{new_ver}"
    run(["git", "tag", "-a", tag, "-m", tag], cwd=root)
    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=root)
    run(["git", "push", "--atomic", "upstream", "HEAD", tag], cwd=root)
'''

    new_text, note = _inject_dependency_tag_stage(body)

    assert new_text is None
    assert "2 `git push` argv carrying the release tag found, expected exactly 1" in note


def test_scope_without_a_root_name_is_refused_and_named() -> None:
    """NEGATIVE: no root-ish name anywhere in scope — refuse, and say which names it wanted."""
    body = _PREAMBLE + '''

def release(where: Path, new_ver: str) -> None:
    """Tag and push."""
    tag = f"v{new_ver}"
    run(["git", "tag", "-a", tag, "-m", tag], cwd=where)
    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=where)
'''

    new_text, note = _inject_dependency_tag_stage(body)

    assert new_text is None
    assert "no root name" in note
    assert "plugin_root" in note and "git_root" in note


def test_scope_without_a_version_name_is_refused_and_named() -> None:
    """NEGATIVE: no version-ish name in scope — refuse rather than bind to a wrong name."""
    body = _PREAMBLE + '''

def release(root: Path, bump: str) -> None:
    """Tag and push."""
    tag = f"v{bump}"
    run(["git", "tag", "-a", tag, "-m", tag], cwd=root)
    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=root)
'''

    new_text, note = _inject_dependency_tag_stage(body)

    assert new_text is None
    assert "no version name" in note
    assert "new_version" in note


def test_unparseable_publish_py_is_refused_not_corrupted() -> None:
    """NEGATIVE: a file that is not valid Python is reported, never edited."""
    body = _PREAMBLE + '\ndef broken(root: Path, new_ver: str) -> None\n    run(["git", "tag"], cwd=root)\n'

    new_text, note = _inject_dependency_tag_stage(body)

    assert new_text is None
    assert "does not parse as Python" in note


def test_publish_py_that_tags_nothing_is_a_silent_no_op() -> None:
    """NEGATIVE: no tagging stage at all = no release to make dependable = nothing to say."""
    new_text, note = _inject_dependency_tag_stage("print('hello')\n")

    assert new_text is None
    assert note == ""


# ===========================================================================
# The remediation text — the two shortcuts a reader would reach for are BOTH wrong
# ===========================================================================


def test_manual_fix_text_does_not_prescribe_the_two_broken_shortcuts() -> None:
    """The old remediation advised `claude plugin tag --push` or `--force-templates`.

    Both are wrong and the fleet was explicitly told NOT to use them: `claude plugin tag`
    takes a plugin PATH, not a tag name (so it silently mints nothing), and
    `--force-templates` OVERWRITES a customized publish.py — which is precisely why a
    customized plugin cannot run it, i.e. exactly the plugins that need this migration.
    """
    _, note = _inject_dependency_tag_stage(
        _PREAMBLE + '\ndef r(root: Path, new_ver: str) -> None:\n'
        '    run(["git", "tag", "-a", f"v{new_ver}"], cwd=root)\n'
        '    run(["git", "push", "origin", "--tags"], cwd=root)\n'
    )

    assert "claude plugin tag --push" not in note
    assert "do NOT run `claude plugin tag`" in note
    assert "Do NOT run `standardize --force-templates`" in note
    # It must tell the maintainer what to actually DO.
    assert "ADD THE STAGE BY HAND" in note
    assert ".claude-plugin/plugin.json" in note
    assert "SAME `git push`" in note


# ===========================================================================
# IDEMPOTENCE — the second run changes nothing
# ===========================================================================


def test_migration_is_idempotent_on_every_shape(tmp_path: Path) -> None:
    """A second `--fix` sees the stage it injected and does nothing."""
    for i, src in enumerate((SHAPE_C, SHAPE_D, SHAPE_MODULE_ROOT)):
        root = _make_plugin(tmp_path, src, name=f"p{i}")
        publish = root / "scripts" / "publish.py"

        first = migrate_publish_py_dependency_tag(root)
        after_first = publish.read_bytes()
        second = migrate_publish_py_dependency_tag(root)
        after_second = publish.read_bytes()

        assert first and "injected" in first[0]
        assert second == [], f"shape {i} was migrated twice: {second}"
        assert after_first == after_second
        assert after_second.decode().count("def _cpv_dependency_tag_name") == 1


def test_end_to_end_shape_d_plugin_on_disk_is_migrated_and_compiles(tmp_path: Path) -> None:
    """E2E: the real entry point rewrites a shape-D plugin, and the result is valid Python."""
    root = _make_plugin(tmp_path, SHAPE_D)
    publish = root / "scripts" / "publish.py"

    notes = migrate_publish_py_dependency_tag(root)

    body = publish.read_text(encoding="utf-8")
    assert notes and "injected" in notes[0]
    assert _publish_py_has_dependency_tag_stage(body)
    compile(body, str(publish), "exec")
