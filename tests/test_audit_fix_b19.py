"""Audit batch B19 fixes — regression tests for three CPV scripts.

Covers the findings the full-audit assigned to:
  * scripts/_skillaudit_yaml_context.py  (#59 list-item key paths, #144 brew local-path install)
  * scripts/add_component.py             (#60 table-break insertion, #143 prose-mention false skip)
  * scripts/cpv_batch_planner.py         (#65 file-scope normalisation, #145 filter_findings docstring)

Each fix gets an assertion of the corrected behaviour PLUS a guard that would
have tripped on the original bug. The #144 fix is security-relevant
(``brew install <local-path>`` is arbitrary-code-execution), so it is tested
TWO-SIDED: the malicious local-path/formula installs are now refused
(NOT certified airtight) AND benign tap specs / package installs stay airtight.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import _skillaudit_yaml_context as yaml_ctx  # noqa: E402
import add_component as ac  # noqa: E402
import cpv_batch_planner as planner  # noqa: E402

# NOTE: do NOT importlib.reload() these process-global modules — a reload swaps
# their function objects and breaks pickle-by-reference in later same-process
# (serial-CI) tests. A plain import already loads the current on-disk source.


# ---------------------------------------------------------------------------
# #144 — _run_line_is_airtight_pkg_install must NOT certify a brew install of a
# local Ruby formula path (RCE). TWO-SIDED.
# ---------------------------------------------------------------------------
class TestBrewLocalPathNotAirtight:
    """``brew install <local-path>`` runs an arbitrary local formula → not airtight."""

    def _airtight(self, line: str) -> bool:
        return yaml_ctx._run_line_is_airtight_pkg_install(line)

    # --- malicious side: local-path / formula-file installs must be refused ---
    def test_brew_install_parent_traversal_not_airtight(self) -> None:
        """``brew install ../../evil`` (path traversal to local formula) → NOT airtight."""
        assert self._airtight("brew install ../../evil") is False

    def test_brew_install_dotslash_rb_not_airtight(self) -> None:
        """``brew install ./local.rb`` (relative local formula file) → NOT airtight."""
        assert self._airtight("brew install ./local.rb") is False

    def test_brew_install_absolute_path_not_airtight(self) -> None:
        """``brew install /abs/path/x.rb`` (absolute local formula) → NOT airtight."""
        assert self._airtight("brew install /abs/path/x.rb") is False

    def test_brew_install_bare_rb_file_not_airtight(self) -> None:
        """``brew install formula.rb`` (bare .rb formula file) → NOT airtight."""
        assert self._airtight("brew install formula.rb") is False

    def test_brew_install_relative_dir_not_airtight(self) -> None:
        """``brew install ../relative/path`` → NOT airtight."""
        assert self._airtight("brew install ../relative/path") is False

    def test_brew_local_path_in_chain_not_airtight(self) -> None:
        """A benign segment must NOT launder a malicious local-path brew segment."""
        assert self._airtight("brew install jq && brew install ../../evil") is False

    # --- benign side: legit installs / tap specs stay airtight ---
    def test_brew_install_bare_package_stays_airtight(self) -> None:
        """``brew install jq`` (bare package) stays airtight."""
        assert self._airtight("brew install jq") is True

    def test_brew_install_multi_package_stays_airtight(self) -> None:
        """``brew install jq ripgrep`` stays airtight."""
        assert self._airtight("brew install jq ripgrep") is True

    def test_brew_install_tap_spec_stays_airtight(self) -> None:
        """``brew install user/tap/formula`` (tap spec, bare identifiers) stays airtight."""
        assert self._airtight("brew install user/tap/formula") is True

    def test_brew_install_homebrew_cask_stays_airtight(self) -> None:
        """``brew install homebrew/cask/foo`` (official tap spec) stays airtight."""
        assert self._airtight("brew install homebrew/cask/foo") is True

    def test_benign_brew_chain_stays_airtight(self) -> None:
        """``brew install jq && brew install ripgrep`` stays airtight."""
        assert self._airtight("brew install jq && brew install ripgrep") is True

    def test_apt_install_stays_airtight(self) -> None:
        """Non-brew installs are unaffected — ``sudo apt-get install -y curl`` airtight."""
        assert self._airtight("sudo apt-get install -y curl") is True


# ---------------------------------------------------------------------------
# #59 — _walk_yaml_keys_naive must give sibling keys inside a list item the
# correct (sibling) path and increment the list counter per entry.
# ---------------------------------------------------------------------------
class TestWalkYamlListItemPaths:
    """List-item mapping keys are siblings, and list entries get distinct indices."""

    def _paths(self, src: str) -> dict[int, tuple[str, ...]]:
        return {lineno: path for path, lineno in yaml_ctx._walk_yaml_keys_naive(src)}

    def test_sibling_key_in_list_item_is_sibling_not_nested(self) -> None:
        """``uses:`` after ``- name:`` is a sibling of ``name`` within entry [0]."""
        src = (
            "jobs:\n  build:\n    steps:\n"
            "      - name: checkout\n"
            "        uses: actions/checkout\n"
        )
        paths = self._paths(src)
        assert paths[4] == ("jobs", "build", "steps", "[0]", "name")
        # Original bug nested this under 'name': (..., '[0]', 'name', 'uses').
        assert paths[5] == ("jobs", "build", "steps", "[0]", "uses")

    def test_second_list_entry_gets_index_one(self) -> None:
        """The second ``- name:`` is list entry [1], not stuck at [0]."""
        src = (
            "jobs:\n  build:\n    steps:\n"
            "      - name: a\n"
            "      - name: b\n"
        )
        paths = self._paths(src)
        assert paths[4] == ("jobs", "build", "steps", "[0]", "name")
        # Original bug kept this at [0] because popping the [0] frame dropped
        # the list counter.
        assert paths[5] == ("jobs", "build", "steps", "[1]", "name")

    def test_counter_resets_for_a_separate_list(self) -> None:
        """A new list under a different key restarts indices at [0]."""
        src = "a:\n  - x: 1\n  - x: 2\nb:\n  - y: 1\n"
        paths = self._paths(src)
        assert paths[2] == ("a", "[0]", "x")
        assert paths[3] == ("a", "[1]", "x")
        assert paths[5] == ("b", "[0]", "y")

    def test_plain_nesting_unaffected(self) -> None:
        """Non-list block nesting still produces straightforward dotted paths."""
        src = "meta:\n  title: x\n  keywords: y\n"
        paths = self._paths(src)
        assert paths[1] == ("meta",)
        assert paths[2] == ("meta", "title")
        assert paths[3] == ("meta", "keywords")


# ---------------------------------------------------------------------------
# #65 — derive_scope must return the NORMALISED path for file scopes so the
# same file referenced two ways unifies into a single scope (one file → one
# shard).
# ---------------------------------------------------------------------------
class TestDeriveScopeNormalisation:
    """File-scope paths are normalised, matching the function's own docstring."""

    def test_dotslash_prefix_stripped(self) -> None:
        scope_path, kind = planner.derive_scope("./scripts/foo.py")
        assert (scope_path, kind) == ("scripts/foo.py", planner.SCOPE_KIND_FILE)

    def test_backslashes_converted(self) -> None:
        scope_path, kind = planner.derive_scope(r"dir\foo.py")
        assert (scope_path, kind) == ("dir/foo.py", planner.SCOPE_KIND_FILE)

    def test_same_file_two_refs_unify_into_one_scope(self) -> None:
        """``./scripts/foo.py`` and ``scripts/foo.py`` are ONE scope, not two."""
        findings = [
            planner.Finding(level="MAJOR", message="a", file="./scripts/foo.py", line=1),
            planner.Finding(level="MINOR", message="b", file="scripts/foo.py", line=2),
        ]
        scopes = planner.group_by_scope(findings)
        # Original bug returned the raw file_path → two scopes → two shards on
        # the same file (violates "one file → one shard").
        assert len(scopes) == 1
        assert scopes[0].scope_path == "scripts/foo.py"
        assert scopes[0].count == 2

    def test_skill_scope_still_derived(self) -> None:
        """Skill-dir scope detection is unchanged by the file-scope fix."""
        scope_path, kind = planner.derive_scope("skills/foo/SKILL.md")
        assert (scope_path, kind) == ("skills/foo/", planner.SCOPE_KIND_SKILL_DIR)


# ---------------------------------------------------------------------------
# #145 — filter_findings docstring must describe the real severity-floor
# behaviour. Guard the BEHAVIOUR the corrected docstring now claims.
# ---------------------------------------------------------------------------
class TestFilterFindingsSeverityFloor:
    """At the default MINOR floor, NIT/WARNING/INFO/PASSED are all dropped."""

    def test_default_floor_drops_below_minor(self) -> None:
        results = [
            {"level": "CRITICAL", "file": "a.py", "message": "c"},
            {"level": "MAJOR", "file": "b.py", "message": "m"},
            {"level": "MINOR", "file": "c.py", "message": "mi"},
            {"level": "NIT", "file": "d.py", "message": "n"},
            {"level": "WARNING", "file": "e.py", "message": "w"},
            {"level": "INFO", "file": "f.py", "message": "i"},
            {"level": "PASSED", "file": "g.py", "message": "p"},
        ]
        kept = {f.level for f in planner.filter_findings(results, "minor")}
        assert kept == {"CRITICAL", "MAJOR", "MINOR"}

    def test_docstring_no_longer_claims_only_info_passed_dropped(self) -> None:
        """The corrected docstring describes the severity floor, not just INFO/PASSED."""
        doc = planner.filter_findings.__doc__ or ""
        assert "floor" in doc.lower()
        # The original misleading sentence must be gone.
        assert "INFO/PASSED rows unconditionally" not in doc

    def test_findings_without_file_ref_dropped(self) -> None:
        results = [
            {"level": "MAJOR", "message": "no file ref"},
            {"level": "MAJOR", "file": "x.py", "message": "has file"},
        ]
        kept = planner.filter_findings(results, "minor")
        assert len(kept) == 1
        assert kept[0].file == "x.py"


# ---------------------------------------------------------------------------
# #60 / #143 — _register_in_the_skills_menu must (a) insert the new row
# directly below the last table row (no blank-line break) and (b) only treat a
# name appearing in the Plugin Skills TABLE section as a duplicate.
# ---------------------------------------------------------------------------
class TestRegisterInSkillsMenu:
    """Catalog registration inserts a valid row and scopes the dup-check to the table."""

    _BASE = (
        "---\nname: the-skills-menu\n---\n\n"
        "## Plugin Skills\n\n"
        "| # | Domain | Skills |\n"
        "|---|--------|--------|\n"
        "| 1 | Core | `foo` — does foo |\n"
        "| 2 | Core | `bar` — does bar |\n\n"
        "## Other\nprose\n"
    )

    def _run(self, catalog_body: str, skill: str, desc: str = "a desc") -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as td:
            plugin = Path(td)
            cat = plugin / "skills" / "the-skills-menu" / "SKILL.md"
            cat.parent.mkdir(parents=True)
            cat.write_text(catalog_body, encoding="utf-8")
            modified = ac._register_in_the_skills_menu(plugin, skill, desc)
            return modified, cat.read_text(encoding="utf-8")

    def test_new_row_sits_directly_below_last_table_row(self) -> None:
        """#60 — no blank line separates the new row from the existing table."""
        modified, out = self._run(self._BASE, "newskill")
        assert modified is True
        lines = out.split("\n")
        idx = next(i for i, ln in enumerate(lines) if "`newskill`" in ln)
        above = lines[idx - 1].strip()
        # Original bug left a blank line above the new row (table-breaking).
        assert above.startswith("|") and above != ""

    def test_prose_mention_does_not_block_registration(self) -> None:
        """#143 — a name appearing only in prose must NOT skip registration."""
        cat = (
            "---\nname: the-skills-menu\n---\n"
            "See `mynew` mentioned in prose.\n\n"
            "## Plugin Skills\n\n"
            "| # | Domain | Skills |\n"
            "|---|--------|--------|\n"
            "| 1 | Core | `foo` — does foo |\n"
        )
        modified, out = self._run(cat, "mynew")
        assert modified is True
        assert "| _ | _ | `mynew`" in out

    def test_name_already_in_table_is_skipped(self) -> None:
        """Idempotency preserved: a name already in the table is a no-op."""
        modified, out = self._run(self._BASE, "foo")
        assert modified is False
        assert out == self._BASE

    def test_substring_of_existing_entry_still_registers(self) -> None:
        """Backtick-wrapped match preserved: 'fix' registers despite 'fix-validation'."""
        cat = (
            "---\nname: the-skills-menu\n---\n\n"
            "## Plugin Skills\n\n"
            "| # | Domain | Skills |\n"
            "|---|--------|--------|\n"
            "| 1 | Core | `fix-validation` — fixes |\n"
        )
        modified, out = self._run(cat, "fix")
        assert modified is True
        assert "| _ | _ | `fix`" in out

    def test_fresh_table_when_section_has_none(self) -> None:
        """No table in the section → a fresh header+row table is created."""
        cat = (
            "---\nname: the-skills-menu\n---\n\n"
            "## Plugin Skills\n\nIntro prose, no table yet.\n\n## Other\n"
        )
        modified, out = self._run(cat, "first")
        assert modified is True
        assert "| 1 | (uncategorised) | `first`" in out
