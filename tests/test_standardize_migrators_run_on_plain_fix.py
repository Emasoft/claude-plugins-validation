"""Task w9 item 2 — ``standardize_plugin.fix_missing_files`` must run the
publish.py / workflow migrators on ANY ``--fix``, not only when a standard
file is MISSING.

Before the fix, the function opened with:

    if not missing_files and not force_overwrite:
        print("No fixable missing files.")
        return []

So a plugin that already has every standard file — the common case for a
plugin standardized once and touched again later — hit that early ``return``
and NONE of the migrator block below it ever ran: dep-tag, test-suite-timeout,
changelog, canon-version, ci-verify, trufflehog-path, ci-env
(``remove_inverted_private_usernames``), dev-extra provisioning, the
superseded ``validate.yml`` removal, cpv-ref repin, jscpd/cspell/commitlint
provisioning — despite every one of those migrators documenting itself as
"runs on ANY --fix". ``tests/test_issue_231_trufflehog_path.py`` even had to
work around it by injecting a fake MISSING item just to reach the migrator it
was testing (see that file's ``test_plain_fix_path_runs_the_migrator``
docstring).

Fixed by scoping only the manifest-dependent file-GENERATION half to "there is
missing/force-overwrite work to do"; the migrator block (and the gitignore-entry
top-up) now always runs, dry-run respected.

Two-sided:
  * a plugin with EVERY standard file present but one workflow carrying the
    old inverted ``CLAUDE_PRIVATE_USERNAMES`` line → a plain ``--fix`` (no
    missing files, no ``--force-templates``) still migrates it;
  * a fully current (freshly-scaffolded) plugin → no changes, idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as gen  # noqa: E402
import standardize_plugin as sp  # noqa: E402

_INVERTED_LINE = "CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}"


def _scaffold(tmp_path: Path, name: str = "w9-fix-migrators-sample") -> Path:
    params = gen.PluginParams(
        name=name,
        description="x",
        author="A",
        author_email="a@a.a",
        github_owner="Emasoft",
        marketplace="Emasoft/my-plugins-marketplace",
    )
    target = tmp_path / name
    target.mkdir()
    gen.generate_plugin_repo(target, params)
    return target


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_full_plugin_with_stale_workflow_is_migrated_by_plain_fix(tmp_path: Path) -> None:
    """No MISSING standard file — plain --fix must still run the migrators."""
    target = _scaffold(tmp_path)
    ci_yml = target / ".github" / "workflows" / "ci.yml"
    text = ci_yml.read_text(encoding="utf-8")
    assert _INVERTED_LINE not in text, "fixture no longer covers the #140 defect"

    # Re-introduce the pre-#140 inverted env line inside an env: block that
    # already has a survivor key, so the block itself is untouched.
    poisoned = text.replace(
        "VALIDATE_ALL_CODEBASE: false\n",
        "VALIDATE_ALL_CODEBASE: false\n"
        f"          {_INVERTED_LINE}\n",
        1,
    )
    assert poisoned != text, "anchor line not found in the generated ci.yml"
    ci_yml.write_text(poisoned, encoding="utf-8")

    # Confirm every standard file is present (no MISSING items) so
    # fix_missing_files() takes the has_missing_work == False path.
    results = sp.run_audit(target)
    missing = [r for r in results if r.category == "files" and r.status == "MISSING"]
    assert missing == [], f"fixture is not fully scaffolded: {missing}"

    created = sp.fix_missing_files(target, results=results, force_templates=False)
    assert created == []  # nothing was CREATED — the migrators aren't file-generation

    migrated = ci_yml.read_text(encoding="utf-8")
    assert _INVERTED_LINE not in migrated
    assert "VALIDATE_ALL_CODEBASE: false" in migrated  # the survivor key stayed


def test_fully_current_plugin_is_a_no_op_and_idempotent(tmp_path: Path) -> None:
    """A freshly scaffolded (already-canon) plugin: plain --fix changes nothing."""
    target = _scaffold(tmp_path)
    results = sp.run_audit(target)
    missing = [r for r in results if r.category == "files" and r.status == "MISSING"]
    assert missing == []

    before = _tree_snapshot(target)
    created_1 = sp.fix_missing_files(target, results=results, force_templates=False)
    after_1 = _tree_snapshot(target)
    assert created_1 == []
    assert after_1 == before, "a fully current plugin must not be mutated by a plain --fix"

    # Idempotent: running it again changes nothing either.
    created_2 = sp.fix_missing_files(target, results=results, force_templates=False)
    after_2 = _tree_snapshot(target)
    assert created_2 == []
    assert after_2 == before


def test_dry_run_is_still_respected_with_no_missing_files(tmp_path: Path) -> None:
    """dry_run=True must not write, even on the has_missing_work == False path."""
    target = _scaffold(tmp_path)
    ci_yml = target / ".github" / "workflows" / "ci.yml"
    text = ci_yml.read_text(encoding="utf-8")
    poisoned = text.replace(
        "VALIDATE_ALL_CODEBASE: false\n",
        "VALIDATE_ALL_CODEBASE: false\n"
        f"          {_INVERTED_LINE}\n",
        1,
    )
    assert poisoned != text
    ci_yml.write_text(poisoned, encoding="utf-8")

    results = sp.run_audit(target)
    missing = [r for r in results if r.category == "files" and r.status == "MISSING"]
    assert missing == []

    sp.fix_missing_files(target, results=results, force_templates=False, dry_run=True)
    assert ci_yml.read_text(encoding="utf-8") == poisoned, "dry-run must not write"
