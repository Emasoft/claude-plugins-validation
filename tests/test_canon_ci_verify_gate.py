#!/usr/bin/env python3
"""Canon gains post-release CI verification, and EXISTING plugins receive it.

WHY THE GATE EXISTS. The release push targets the default branch directly and
the maintainer role holds `bypass_mode: always` on the branch ruleset, so GitHub
reports `Bypassed rule violations … required status checks are expected` and
lets it through. The bypass is deliberate — it is what makes a scripted release
possible — but it means the required checks NEVER gate a release: tag, GitHub
release and marketplace notification are all public before CI has said a word.
Until now "CI must be green" lived only in agent PROSE, which is skippable; that
is the identical defect v2.157.0 fixed one gate earlier for `ci-preflight`.

WHY THE MIGRATOR EXISTS. Fixing the generator alone only helps plugins scaffolded
AFTER today. `standardize` never overwrites an existing `publish.py` on a plain
`--fix`, and the plugins that need this most are exactly the ones carrying a
hand-customized publish.py they cannot safely `--force-templates`. Same delivery
lesson as v3.22.0 (#179).

The load-bearing test is `test_migrated_output_is_byte_identical_to_canon`: it is
what stops the migrator and the generator from silently diverging. It has already
earned its keep — the first implementation hard-coded the call-site comment and
drifted from canon on its very first run (em-dash vs ASCII hyphen).
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import generate_plugin_repo as gpr  # noqa: E402
import standardize_plugin as sp  # noqa: E402


class _Params:
    """Minimal PluginParams stand-in — mirrors tests/test_audit_fix_b14.py."""

    name = "canontest"
    description = "d"
    author = "Emasoft"
    repo_url = "https://github.com/Emasoft/canontest"
    cpv_ref_resolved = "v5.1.0"
    cpv_source = "git"


def _canon() -> str:
    return str(gpr.gen_publish_py(_Params()))  # type: ignore[arg-type]


def _pre_v511(canon: str) -> str:
    """A realistic PRE-v5.1.1 publish.py: canon minus the whole CI-verify unit.

    Strips exactly the three pieces the migrator must restore — the stage block,
    its call site, and the TOP-LEVEL `import time`. The import strip is anchored
    on `import sys\\nimport time\\n` rather than a bare replace ON PURPOSE: a bare
    replace also eats a function-LOCAL `import time` that canon legitimately has,
    which would make this fixture test something no real plugin looks like.
    """
    start = canon.find("# How long to wait for the released commit")
    end = canon.find("# -- Main ---")
    assert start > 0 and end > start, "canon anchors moved — update this fixture"
    out = canon[:start] + canon[end:]
    out = re.sub(
        r"    # Runs AFTER the release on purpose.*?\n    stage_verify_ci_green\(root, args\.dry_run\)\n",
        "",
        out,
        flags=re.S,
    )
    out = out.replace("import sys\nimport time\n", "import sys\n")
    assert "stage_verify_ci_green" not in out, "fixture still contains the stage"
    return out


def _plugin_with(tmp_path: Path, publish_src: str) -> Path:
    root = tmp_path / "plug"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "publish.py").write_text(publish_src, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# The emitted canon
# ---------------------------------------------------------------------------
class TestCanonEmitsTheGate:
    def test_canon_compiles(self) -> None:
        """A template that does not parse breaks every plugin CPV scaffolds."""
        ast.parse(_canon())

    def test_canon_defines_and_calls_the_stage(self) -> None:
        canon = _canon()
        assert "def stage_verify_ci_green" in canon
        assert "stage_verify_ci_green(root, args.dry_run)" in canon

    def test_canon_imports_time(self) -> None:
        """Regression lock: the first cut omitted this and every scaffolded
        plugin would have raised NameError on `time.monotonic()` at publish."""
        assert re.search(r"^import time$", _canon(), re.MULTILINE)

    def test_stage_is_defined_before_main_uses_it(self) -> None:
        canon = _canon()
        assert canon.index("def stage_verify_ci_green") < canon.index(
            "stage_verify_ci_green(root, args.dry_run)"
        )

    def test_call_runs_after_the_release(self) -> None:
        """It must verify the commit that actually shipped."""
        canon = _canon()
        assert canon.index("stage_gh_release(root, new_ver, args.dry_run)") < canon.index(
            "stage_verify_ci_green(root, args.dry_run)"
        )

    def test_gate_never_aborts_the_publish(self) -> None:
        """The release is already public by then, so a non-zero exit could not
        un-ship it — it would only discard the report that is the whole point."""
        canon = _canon()
        body = canon[canon.index("def stage_verify_ci_green") :]
        body = body[: body.index("\n# -- Main ---")]
        assert "sys.exit(" not in body, "the CI-verify stage must never abort the publish"

    def test_red_ci_is_reported_loudly(self) -> None:
        canon = _canon()
        body = canon[canon.index("def stage_verify_ci_green") :]
        assert "CI IS RED" in body
        assert "gh run view --log-failed" in body, "must name the follow-up command"

    def test_cannot_check_is_never_reported_as_green(self) -> None:
        """No gh / no runs / timeout must read UNVERIFIED, never as a pass."""
        canon = _canon()
        body = canon[canon.index("def stage_verify_ci_green") :]
        body = body[: body.index("\n# -- Main ---")]
        assert body.count("UNVERIFIED") >= 4

    def test_skipped_conclusion_is_not_a_failure(self) -> None:
        """A dormant optional workflow always reports `skipped` — treating that
        as RED would cry wolf on every single release."""
        canon = _canon()
        body = canon[canon.index("def stage_verify_ci_green") :]
        assert '"skipped"' in body and '"neutral"' in body


# ---------------------------------------------------------------------------
# The migrator — how the 20+ EXISTING plugins actually receive it
# ---------------------------------------------------------------------------
class TestExistingPluginsAreMigrated:
    def test_migrated_output_is_byte_identical_to_canon(self, tmp_path: Path) -> None:
        """THE load-bearing test: migrator and generator cannot silently diverge."""
        canon = _canon()
        root = _plugin_with(tmp_path, _pre_v511(canon))
        sp.migrate_publish_py_ci_verify(root)
        assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == canon

    def test_migrated_file_compiles(self, tmp_path: Path) -> None:
        """A half-migrated publish.py in someone else's repo is the worst outcome."""
        root = _plugin_with(tmp_path, _pre_v511(_canon()))
        sp.migrate_publish_py_ci_verify(root)
        ast.parse((root / "scripts" / "publish.py").read_text(encoding="utf-8"))

    def test_migration_reports_what_it_did(self, tmp_path: Path) -> None:
        root = _plugin_with(tmp_path, _pre_v511(_canon()))
        notes = sp.migrate_publish_py_ci_verify(root)
        assert notes and "CI is green" in notes[0]

    def test_is_idempotent(self, tmp_path: Path) -> None:
        """A second --fix must be a no-op, not a duplicate insertion."""
        root = _plugin_with(tmp_path, _pre_v511(_canon()))
        sp.migrate_publish_py_ci_verify(root)
        first = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert sp.migrate_publish_py_ci_verify(root) == []
        assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == first

    def test_already_canon_file_is_untouched(self, tmp_path: Path) -> None:
        root = _plugin_with(tmp_path, _canon())
        assert sp.migrate_publish_py_ci_verify(root) == []

    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        src = _pre_v511(_canon())
        root = _plugin_with(tmp_path, src)
        notes = sp.migrate_publish_py_ci_verify(root, dry_run=True)
        assert notes and notes[0].startswith("[dry-run]")
        assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == src

    def test_local_import_time_does_not_block_the_toplevel_one(self, tmp_path: Path) -> None:
        """A function-LOCAL `import time` must not be mistaken for the module one.

        Canon genuinely has one, so a naive `"import time" in text` check would
        skip the top-level import and ship a file that NameErrors at publish.
        """
        src = _pre_v511(_canon())
        assert "    import time" in src, "fixture should retain the local import"
        root = _plugin_with(tmp_path, src)
        sp.migrate_publish_py_ci_verify(root)
        out = (root / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert re.search(r"^import time$", out, re.MULTILINE)


# ---------------------------------------------------------------------------
# Refusal half — never half-migrate someone else's repo
# ---------------------------------------------------------------------------
class TestUnrecognisedShapesAreRefusedNotMangled:
    def test_missing_release_anchor_leaves_file_byte_identical(self, tmp_path: Path) -> None:
        src = _pre_v511(_canon()).replace(
            "    stage_gh_release(root, new_ver, args.dry_run)\n", "    pass  # custom release\n"
        )
        root = _plugin_with(tmp_path, src)
        notes = sp.migrate_publish_py_ci_verify(root)
        assert notes and "NOT" in notes[0]
        assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == src

    def test_no_publish_py_is_silent(self, tmp_path: Path) -> None:
        root = tmp_path / "plug"
        (root / "scripts").mkdir(parents=True)
        assert sp.migrate_publish_py_ci_verify(root) == []

    def test_unrelated_file_is_not_mangled(self, tmp_path: Path) -> None:
        src = "print('not a publish pipeline')\n"
        root = _plugin_with(tmp_path, src)
        sp.migrate_publish_py_ci_verify(root)
        assert (root / "scripts" / "publish.py").read_text(encoding="utf-8") == src


def test_migrator_is_wired_into_the_plain_fix_path() -> None:
    """It must run on ANY --fix, not only --force-templates.

    The plugins that need this are exactly the ones with a customized publish.py
    they cannot safely force-overwrite; gating it behind --force-templates would
    mean it never reaches them.
    """
    src = (scripts_dir / "standardize_plugin.py").read_text(encoding="utf-8")
    assert "migrate_publish_py_ci_verify(plugin_path, dry_run=dry_run)" in src
    if "--force-templates" in src:
        call = src.index("migrate_publish_py_ci_verify(plugin_path")
        window = src[max(0, call - 2000) : call]
        assert "force_templates" not in window.split("def ")[-1], (
            "the CI-verify migrator must not be gated behind --force-templates"
        )


@pytest.mark.parametrize("fn", ["stage_verify_ci_green", "CI_VERIFY_TIMEOUT_S"])
def test_canon_symbols_present(fn: str) -> None:
    assert fn in _canon()
