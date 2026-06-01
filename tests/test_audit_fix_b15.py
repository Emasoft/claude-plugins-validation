"""Audit-fix regression tests for validate_documentation.py + validate_skill.py (batch b15).

Each REAL finding gets two-sided coverage: a guard that would have caught the
original bug, plus an assertion of the corrected behavior (and, where the bug
only manifests on a case-sensitive filesystem, a simulation so the test is
deterministic on case-insensitive macOS/APFS too).

validate_documentation.py:
- #87  validate_readme_exists checked only {README.md, readme.md} while
       _find_readme accepted {README.md, readme.md, Readme.md}. On a
       case-sensitive filesystem a plugin shipping "Readme.md" got a spurious
       "README.md is missing" WARNING from one function while the content
       checks (using _find_readme) found it — now both agree (MINOR, not
       missing-WARNING).
- #158 validate_changelog_exists checked CHANGELOG.md, then re-listed
       CHANGELOG.md as the first loop element — a dead first iteration that
       could only fail again. The loop now starts at "changelog.md".
- #175 validate_heading_hierarchy scanned raw lines, so an ATX-looking
       "# comment" / "### comment" INSIDE a fenced code block produced a
       spurious "hierarchy skip" WARNING. It now uses the shared fence-aware
       iterator and skips in-fence lines.

validate_skill.py:
- #20  validate_skill passed the FULL file content as the `body` arg of
       validate_description_field, so `body.strip()` was always truthy and the
       "No 'description' field and no body content for fallback" MAJOR could
       never fire (silently demoted to the INFO fallback branch). The parsed
       body is now passed.
- #165 validate_frontmatter had a bare `if frontmatter is None: return None`
       after the malformed-frontmatter branch — unreachable, because by that
       point content.startswith("---") is guaranteed True so the prior branch
       already returned for every None case. The dead check was removed; the
       malformed-frontmatter CRITICAL still fires.

#93 (validate_skill_content counts total lines from the full file, not the
body) was REFUTED — see test_refuted_93_full_file_line_count_is_intentional:
the canonical comprehensive validator (validate_token_budget) does the
identical full-file count by explicit design ("line count is layout, not token
cost"), so counting the whole SKILL.md file is intended, not a bug.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

os.environ.setdefault("CPV_SCAN_CACHE", "0")

# Add scripts directory to path for imports
scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import validate_documentation as vd  # noqa: E402
import validate_skill as vs  # noqa: E402


def _levels(report, level: str) -> list[str]:
    """Return the messages of every result at the given severity level."""
    return [r.message for r in report.results if r.level == level]


# ---------------------------------------------------------------------------
# #87 — validate_readme_exists and _find_readme must use the same name set
# ---------------------------------------------------------------------------
class _FakePath:
    """Minimal Path stand-in for simulating a case-sensitive filesystem.

    ``/`` yields the literal child filename; ``exists()`` is True only for the
    exact names in ``present`` — i.e. case-sensitive, unlike macOS/APFS.
    """

    def __init__(self, name: str, present: frozenset[str]) -> None:
        self.name = name
        self._present = present

    def __truediv__(self, other: str) -> "_FakePath":
        return _FakePath(other, self._present)

    def exists(self) -> bool:
        return self.name in self._present


def test_fix_87_readme_variant_consistent_on_case_sensitive_fs() -> None:
    """A 'Readme.md'-only plugin yields a MINOR (not a missing-WARNING)."""
    present = frozenset({"Readme.md"})
    plug = _FakePath("plugin_root", present)

    report = vd.DocumentationValidationReport()
    result = vd.validate_readme_exists(plug, report)  # type: ignore[arg-type]

    # _find_readme accepts Readme.md, so validate_readme_exists must NOT claim
    # the README is missing — both agree it exists.
    assert vd._find_readme(plug) is not None  # type: ignore[arg-type]
    assert result is True
    assert _levels(report, "WARNING") == []
    minors = _levels(report, "MINOR")
    assert len(minors) == 1
    assert "non-canonical case" in minors[0]
    assert "Readme.md" in minors[0]


def test_guard_87_truly_missing_readme_still_warns() -> None:
    """No README of any spelling still returns False with a missing-WARNING."""
    plug = _FakePath("plugin_root", frozenset())  # nothing present
    report = vd.DocumentationValidationReport()
    result = vd.validate_readme_exists(plug, report)  # type: ignore[arg-type]
    assert result is False
    assert any("missing" in m for m in _levels(report, "WARNING"))


def test_guard_87_name_sets_are_identical() -> None:
    """validate_readme_exists must not hard-code a README name subset.

    The fix delegates detection to _find_readme; this guard fails if a future
    edit reintroduces a divergent literal name list inside validate_readme_exists.
    """
    src = inspect.getsource(vd.validate_readme_exists)
    assert "_find_readme(plugin_path)" in src
    # The old, drift-prone hard-coded lowercase-only variant must be gone.
    assert 'plugin_path / "readme.md"' not in src


# ---------------------------------------------------------------------------
# #158 — changelog loop must not re-test the already-checked CHANGELOG.md
# ---------------------------------------------------------------------------
def test_fix_158_changelog_loop_drops_dead_first_iteration() -> None:
    """The variant loop no longer leads with the already-tested CHANGELOG.md."""
    src = inspect.getsource(vd.validate_changelog_exists)
    loop_line = next(line for line in src.splitlines() if "for variant in" in line)
    # CHANGELOG.md is tested above the loop; re-listing it made iteration 1 dead.
    assert "CHANGELOG.md" not in loop_line
    assert "changelog.md" in loop_line


def test_guard_158_history_md_still_detected(tmp_path: Path) -> None:
    """A HISTORY.md changelog variant is still recognized (loop not over-trimmed)."""
    (tmp_path / "HISTORY.md").write_text("# History\n", encoding="utf-8")
    report = vd.DocumentationValidationReport()
    vd.validate_changelog_exists(tmp_path, report)
    assert any("HISTORY.md" in m for m in _levels(report, "PASSED"))


# ---------------------------------------------------------------------------
# #175 — heading hierarchy must be fence-aware
# ---------------------------------------------------------------------------
def test_fix_175_heading_inside_fence_not_flagged(tmp_path: Path) -> None:
    """An ATX-looking comment inside a code fence is not a hierarchy skip."""
    md = "# Top\n\n```bash\n# shell comment, not a heading\n### deep comment inside the fence\n```\n\n## Section two\n"
    (tmp_path / "README.md").write_text(md, encoding="utf-8")
    report = vd.DocumentationValidationReport()
    vd.validate_heading_hierarchy(tmp_path, report)
    assert _levels(report, "WARNING") == []


def test_guard_175_real_skip_outside_fence_still_flagged(tmp_path: Path) -> None:
    """A genuine h1 -> h3 skip OUTSIDE any fence is still reported."""
    md = "# Top\n\n### Real skip\n"
    (tmp_path / "README.md").write_text(md, encoding="utf-8")
    report = vd.DocumentationValidationReport()
    vd.validate_heading_hierarchy(tmp_path, report)
    warnings = _levels(report, "WARNING")
    assert any("Heading hierarchy skip" in m for m in warnings)


def test_guard_175_tilde_fence_also_handled(tmp_path: Path) -> None:
    """A ~~~-fenced block also suppresses in-fence pseudo-headings."""
    md = "# Top\n\n~~~\n### inside tilde fence\n~~~\n\n## Two\n"
    (tmp_path / "README.md").write_text(md, encoding="utf-8")
    report = vd.DocumentationValidationReport()
    vd.validate_heading_hierarchy(tmp_path, report)
    assert _levels(report, "WARNING") == []


# ---------------------------------------------------------------------------
# #20 — validate_description_field must receive the body, not the full content
# ---------------------------------------------------------------------------
def _write_skill(tmp_path: Path, text: str) -> Path:
    (tmp_path / "SKILL.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_fix_20_no_description_empty_body_emits_major(tmp_path: Path) -> None:
    """Missing description + empty body raises the MAJOR (no longer demoted)."""
    skill = _write_skill(tmp_path, "---\nname: foo\n---\n\n")
    report = vs.validate_skill(skill)
    majors = _levels(report, "MAJOR")
    assert any("no body content for fallback" in m for m in majors)
    # The wrong INFO fallback ("will use first paragraph") must NOT appear.
    assert not any("first paragraph" in m for m in _levels(report, "INFO"))


def test_guard_20_no_description_with_body_emits_info(tmp_path: Path) -> None:
    """Missing description but real body keeps the INFO fallback (not MAJOR)."""
    skill = _write_skill(tmp_path, "---\nname: foo\n---\n\nThis skill does a thing.\n")
    report = vs.validate_skill(skill)
    assert any("first paragraph" in m for m in _levels(report, "INFO"))
    assert not any("no body content for fallback" in m for m in _levels(report, "MAJOR"))


def test_guard_20_description_field_receives_parsed_body() -> None:
    """validate_skill must pass the parsed body (not raw content) to the desc check."""
    src = inspect.getsource(vs.validate_skill)
    assert "validate_description_field(frontmatter, _body, report)" in src
    assert "validate_description_field(frontmatter, content, report)" not in src


# ---------------------------------------------------------------------------
# #165 — dead second None-check in validate_frontmatter removed
# ---------------------------------------------------------------------------
def test_fix_165_dead_none_check_removed() -> None:
    """The unreachable bare `if frontmatter is None: return None` is gone."""
    src = inspect.getsource(vs.validate_frontmatter)
    # Count only real `if frontmatter is None:` STATEMENTS (a stripped line that
    # starts with `if` and ends with `:`) — not prose mentions inside comments,
    # so the explanatory comment that quotes the removed line doesn't inflate it.
    none_checks = [
        line
        for line in (raw.strip() for raw in src.splitlines())
        if line.startswith("if ") and "frontmatter is None" in line and line.endswith(":")
    ]
    assert len(none_checks) == 1


def test_guard_165_malformed_frontmatter_still_critical(tmp_path: Path) -> None:
    """Removing dead code must not change the malformed-frontmatter CRITICAL."""
    skill = _write_skill(tmp_path, "---\nname: [unclosed\n")
    report = vs.validate_skill(skill)
    assert any("Malformed YAML" in m for m in _levels(report, "CRITICAL"))


def test_guard_165_valid_frontmatter_passes(tmp_path: Path) -> None:
    """Well-formed frontmatter still produces the 'Valid YAML frontmatter' PASS."""
    skill = _write_skill(
        tmp_path, "---\nname: foo\ndescription: A real description here. Use when testing.\n---\n\nBody text.\n"
    )
    report = vs.validate_skill(skill)
    assert any("Valid YAML frontmatter" in m for m in _levels(report, "PASSED"))


# ---------------------------------------------------------------------------
# #93 — REFUTED: full-file line count is intentional, matches canonical validator
# ---------------------------------------------------------------------------
def test_refuted_93_full_file_line_count_is_intentional() -> None:
    """validate_skill_content's full-file line count matches the canonical validator.

    The audit flagged ``total_lines = content.count("\\n") + 1`` (full file
    incl. frontmatter) as an 'inflated count'. But the canonical comprehensive
    validator (validate_token_budget) computes the SAME full-file count by
    explicit design — its comment states the line-count guard is a structural
    'layout, not token cost' check on the whole SKILL.md file. So counting the
    frontmatter is intended and consistent, not a bug. This test pins that
    intent: both validators count the full content identically.
    """
    body_src = inspect.getsource(vs.validate_skill_content)
    assert 'total_lines = content.count("\\n") + 1' in body_src

    import validate_skill_comprehensive as vsc

    canon_src = inspect.getsource(vsc.validate_token_budget)
    assert 'total_lines = content.count("\\n") + 1' in canon_src
    # The canonical validator receives BOTH content and body and still chooses
    # the full content for the line count — the deliberate-by-design signal.
    assert "def validate_token_budget(" in canon_src
    assert "body" in canon_src
