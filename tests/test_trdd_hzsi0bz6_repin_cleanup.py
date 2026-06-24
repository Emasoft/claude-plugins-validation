"""Tests for TRDD-HZSI0BZ6 F3 — standardize re-pins a stale/invalid CPV ref and
removes a superseded validate.yml on a plain ``--fix``.

The two pieces this covers:

* **Re-pin (new):** a plugin migrated by an OLD CPV pins
  ``git+https://github.com/Emasoft/claude-plugins-validation@main`` in its
  ``.github/workflows/*.yml`` — but CPV's default branch is ``master``, so
  ``uvx --from git+…@main`` 404s and the workflow red-CIs forever.
  ``--force-templates`` already re-pins ci/release/notify (they are in
  ``_FORCE_TEMPLATE_FILES``), but a plain ``--fix`` never touched an existing
  workflow. ``repin_stale_cpv_ref`` now rewrites ONLY the stale CPV ref to the
  current resolved ref, on ANY ``--fix``.

* **validate.yml removal (already wired — verified here):** the removal already
  runs on any ``--fix`` that has/installs ci.yml; these tests pin that it fires
  on a plain ``--fix`` (no ``--force-templates``).

Every test is two-sided: the stale ref is re-pinned AND a VALID ref
(``master`` / ``v<semver>`` / SHA) is left byte-identical; the CPV validate.yml
is removed AND an unrelated ``validate.yml`` is preserved.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from standardize_plugin import (  # noqa: E402
    _CPV_REF_PIN_RE,
    _cpv_ref_is_valid,
    _repin_workflow_text,
    fix_missing_files,
    repin_stale_cpv_ref,
    run_audit,
)

# A minimal canonical ci.yml validate step that pins the CPV ref. The leading
# `@` ref is what gets re-pinned; everything else must come back untouched.
_CI_YML_TEMPLATE = """\
name: CI
on: [push, pull_request]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run plugin validation (remote CPV, --strict)
        run: |
          uvx --from git+https://github.com/Emasoft/claude-plugins-validation@{ref} \\
            --with pyyaml cpv-remote-validate plugin . --strict
"""


def _make_plugin(tmp_path: Path, ci_ref: str | None = None) -> Path:
    """Scaffold a minimal plugin tree; optionally with a ci.yml pinning ``ci_ref``."""
    root = tmp_path / "plug"
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "plug", "version": "0.1.0"}', encoding="utf-8"
    )
    if ci_ref is not None:
        wf = root / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "ci.yml").write_text(_CI_YML_TEMPLATE.format(ref=ci_ref), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# _cpv_ref_is_valid — the CIP-6 rule (the bad-ref classifier)
# ---------------------------------------------------------------------------


def test_master_is_valid() -> None:
    """master (CPV's default branch) is a resolvable ref."""
    assert _cpv_ref_is_valid("master") is True


def test_semver_tags_are_valid() -> None:
    """A v<semver> tag (incl. pre-release/build metadata) is valid."""
    assert _cpv_ref_is_valid("v2.146.0") is True
    assert _cpv_ref_is_valid("v2.146.0-rc.1") is True
    assert _cpv_ref_is_valid("v10.0.1+build.5") is True


def test_commit_shas_are_valid() -> None:
    """A 7-40 hex commit SHA (abbreviated or full) is valid."""
    assert _cpv_ref_is_valid("abc1234") is True
    assert _cpv_ref_is_valid("a" * 40) is True
    assert _cpv_ref_is_valid("0123456789abcdef0123456789abcdef01234567") is True


def test_main_and_branch_names_are_invalid() -> None:
    """main / develop / HEAD / a feature branch are STALE — they get re-pinned."""
    for bad in ("main", "develop", "HEAD", "feature-x", "release/1.0"):
        assert _cpv_ref_is_valid(bad) is False, bad


def test_short_or_overlong_sha_is_invalid() -> None:
    """A <7-char hex / >40-char hex string is not a valid SHA."""
    assert _cpv_ref_is_valid("abc12") is False  # too short
    assert _cpv_ref_is_valid("a" * 41) is False  # too long
    assert _cpv_ref_is_valid("v2.146") is False  # not a full v<semver>


# ---------------------------------------------------------------------------
# _repin_workflow_text — surgical in-place rewrite (two-sided on raw text)
# ---------------------------------------------------------------------------


def test_repin_text_rewrites_main_to_resolved() -> None:
    """A `@main` pin is rewritten to the resolved ref; the stale set reports it."""
    text = _CI_YML_TEMPLATE.format(ref="main")
    new_text, stale = _repin_workflow_text(text, "v2.146.0")
    assert stale == {"main"}
    assert "@v2.146.0" in new_text
    assert "@main" not in new_text
    # Only the ref changed — the rest of the workflow is otherwise preserved.
    assert new_text == text.replace("@main", "@v2.146.0")


def test_repin_text_leaves_valid_ref_byte_identical() -> None:
    """A workflow already pinned at a VALID ref comes back byte-identical, empty set."""
    for good in ("master", "v2.146.0", "abc1234def"):
        text = _CI_YML_TEMPLATE.format(ref=good)
        new_text, stale = _repin_workflow_text(text, "v2.999.0")
        assert stale == set(), good
        assert new_text == text, good


def test_repin_text_dotgit_url_form() -> None:
    """The `…claude-plugins-validation.git@<ref>` URL form is also re-pinned."""
    text = "uvx --from git+https://github.com/Emasoft/claude-plugins-validation.git@main x"
    new_text, stale = _repin_workflow_text(text, "v2.146.0")
    assert stale == {"main"}
    assert new_text == "uvx --from git+https://github.com/Emasoft/claude-plugins-validation.git@v2.146.0 x"


def test_repin_text_only_touches_cpv_ref_not_other_actions() -> None:
    """A sibling non-CPV action ref on the same workflow is never rewritten."""
    text = (
        "      - uses: actions/checkout@v4\n"
        "      - run: uvx --from git+https://github.com/Emasoft/claude-plugins-validation@main z\n"
    )
    new_text, stale = _repin_workflow_text(text, "v2.146.0")
    assert stale == {"main"}
    assert "actions/checkout@v4" in new_text  # untouched
    assert "claude-plugins-validation@v2.146.0" in new_text


def test_repin_regex_captures_ref_without_trailing_noise() -> None:
    """The ref capture stops at whitespace / quote / `#` (no bleed into the ref)."""
    m = _CPV_REF_PIN_RE.search(
        "git+https://github.com/Emasoft/claude-plugins-validation@main#egg=x"
    )
    assert m is not None
    assert m.group("ref") == "main"


# ---------------------------------------------------------------------------
# repin_stale_cpv_ref — file-level, on disk
# ---------------------------------------------------------------------------


def test_repin_no_workflows_dir_is_noop(tmp_path: Path) -> None:
    """A plugin with no .github/workflows yields no notes (clean no-op)."""
    root = _make_plugin(tmp_path, ci_ref=None)
    assert repin_stale_cpv_ref(root) == []


def test_repin_stale_ref_rewrites_file(tmp_path: Path) -> None:
    """A ci.yml pinned `@main` is rewritten on disk to the resolved ref."""
    root = _make_plugin(tmp_path, ci_ref="main")
    notes = repin_stale_cpv_ref(root)
    assert notes, "expected a re-pin note"
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "@main" not in ci
    assert "claude-plugins-validation@" in ci  # still pinned, at a valid ref


def test_repin_valid_ref_leaves_file_untouched(tmp_path: Path) -> None:
    """A ci.yml pinned at a valid tag is NOT rewritten (no note, byte-identical)."""
    root = _make_plugin(tmp_path, ci_ref="v2.140.0")
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    notes = repin_stale_cpv_ref(root)
    after = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert notes == []
    assert before == after


def test_repin_dry_run_does_not_write(tmp_path: Path) -> None:
    """dry-run reports the stale ref but leaves the file unchanged on disk."""
    root = _make_plugin(tmp_path, ci_ref="main")
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    notes = repin_stale_cpv_ref(root, dry_run=True)
    after = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert any("would re-pin" in n for n in notes)
    assert before == after  # nothing written in dry-run


# ---------------------------------------------------------------------------
# fix_missing_files — the re-pin runs on a PLAIN --fix (force_templates=False)
# ---------------------------------------------------------------------------


def test_plain_fix_repins_stale_ref(tmp_path: Path) -> None:
    """A plain --fix (force_templates=False) re-pins an existing stale ci.yml."""
    root = _make_plugin(tmp_path, ci_ref="main")
    results = run_audit(root)
    fix_missing_files(root, results, dry_run=False, force_templates=False)
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "@main" not in ci, "plain --fix must re-pin the stale @main"


def test_plain_fix_preserves_valid_ref(tmp_path: Path) -> None:
    """A plain --fix leaves a correctly-pinned ci.yml byte-identical (two-sided)."""
    root = _make_plugin(tmp_path, ci_ref="v2.135.0")
    before = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    results = run_audit(root)
    fix_missing_files(root, results, dry_run=False, force_templates=False)
    after = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert before == after


# ---------------------------------------------------------------------------
# validate.yml removal already runs on a PLAIN --fix (verify the existing wiring)
# ---------------------------------------------------------------------------

_VALIDATE_YML_CPV = """\
name: Plugin Validation
on: [push]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uvx --with pyyaml cpv-remote-validate plugin . --strict
"""

_VALIDATE_YML_UNRELATED = """\
name: Schema Check
on: [push]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: ./my-own-schema-validator.sh
"""


def test_plain_fix_removes_superseded_validate_yml(tmp_path: Path) -> None:
    """A plain --fix removes the CPV-shipped validate.yml when ci.yml is present."""
    root = _make_plugin(tmp_path, ci_ref="v2.140.0")  # ci.yml present + valid ref
    wf = root / ".github" / "workflows"
    (wf / "validate.yml").write_text(_VALIDATE_YML_CPV, encoding="utf-8")
    results = run_audit(root)
    fix_missing_files(root, results, dry_run=False, force_templates=False)
    assert not (wf / "validate.yml").is_file(), "superseded validate.yml must be removed on plain --fix"
    # Safe-deleted, not destroyed.
    assert (root / "scripts_dev" / "superseded-workflows" / "validate.yml").is_file()


def test_plain_fix_preserves_unrelated_validate_yml(tmp_path: Path) -> None:
    """An unrelated validate.yml (not CPV-shipped) is NEVER removed (two-sided)."""
    root = _make_plugin(tmp_path, ci_ref="v2.140.0")
    wf = root / ".github" / "workflows"
    (wf / "validate.yml").write_text(_VALIDATE_YML_UNRELATED, encoding="utf-8")
    results = run_audit(root)
    fix_missing_files(root, results, dry_run=False, force_templates=False)
    assert (wf / "validate.yml").is_file(), "an unrelated validate.yml must be preserved"
