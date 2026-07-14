#!/usr/bin/env python3
"""Issue #168 — RC-DEP-TAG-PIPELINE keyed on the VARIABLE NAME, not the tag SHAPE.

The old predicate was::

    creates_dep_tag = "--v" in body and ("dependency_tag" in body or "dep_tag" in body)

Wrong twice over. ``"--v" in body`` is decorative — every publish.py contains
``--verbose`` — which left the VARIABLE NAME as the entire discriminator. A plugin
that correctly builds ``{name}--v{version}`` from its manifest but names the local
``resolver_tag`` was told it "never" tags, while its releases had carried the tag
for months. That inverts the incentive: hard-code the name and pass, derive it from
the manifest (what we tell people to do) and get flagged.

The fix keys on the CONSTRUCTION SHAPE (``publish_py_creates_dependency_tag``), and
adds a ground-truth override: a repo that already carries a real ``{name}--v*`` tag
has demonstrably shipped resolver tags, so Signal 1 stays quiet regardless of what
the static read of publish.py concludes.

Two-sided throughout: every "stays quiet" case is paired with a "still fires" one,
so neither a dead detector nor a noisy one can pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _findings(root: Path, code: str = "RC-DEP-TAG") -> list[str]:
    from cpv_validation_common import ValidationReport  # type: ignore[import-not-found]
    from validate_plugin import check_dependency_resolution_tags  # type: ignore[import-not-found]

    report = ValidationReport()
    check_dependency_resolution_tags(root, report)
    return [str(getattr(r, "message", r)) for r in report.results if code in str(getattr(r, "message", r))]


def _pipeline_findings(root: Path) -> list[str]:
    return _findings(root, "RC-DEP-TAG-PIPELINE")


def _plugin(tmp_path: Path, name: str = "demo-plugin") -> Path:
    root = tmp_path / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "description": "d"}), encoding="utf-8"
    )
    return root


def _publish_py(root: Path, tag_stage: str) -> None:
    """A publish.py whose plain-tag stage matches the REAL fleet shape (verified
    against /tmp/fleet-pub: `run(["git", "tag", "-a", tag, ...], cwd=root)`), plus
    whatever dependency-tag stage the case under test supplies."""
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "publish.py").write_text(
        "import argparse\n"
        "\n"
        "def main(root, new_ver):\n"
        '    p = argparse.ArgumentParser()\n'
        '    p.add_argument("--verbose", action="store_true")\n'
        '    tag = f"v{new_ver}"\n'
        '    run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], cwd=root)\n'
        f"{tag_stage}"
        '    run(["git", "push", "--atomic", "origin", "HEAD", tag], cwd=root)\n',
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _git_repo(root: Path, tags: list[str]) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    (root / "f.txt").write_text("x", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-qm", "c")
    for t in tags:
        _git(root, "tag", t)


# ── The FIRES side: a pipeline that genuinely never builds the tag ───────────


class TestStillFires:
    def test_plain_tag_only_fires(self, tmp_path: Path) -> None:
        """The real shape of the 11 fleet publish.py files that DO lack the stage."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")
        found = _pipeline_findings(root)
        assert len(found) == 1, found
        assert "never" in found[0]

    def test_verbose_flag_alone_does_not_satisfy_the_detector(self, tmp_path: Path) -> None:
        """`--verbose` is the decorative conjunct the old predicate leaned on. A file
        that has it but builds no tag must STILL fire — otherwise the shape-based
        predicate has merely re-introduced the bug from the other direction."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    print("--verbose --version --verify")\n')
        assert len(_pipeline_findings(root)) == 1

    def test_a_variable_merely_NAMED_dep_tag_does_not_clear_it(self, tmp_path: Path) -> None:
        """The mirror image of #168: the NAME must not be able to clear the finding
        any more than it could raise one. A `dep_tag` that is a plain `v{ver}` is
        still an undependable pipeline."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    dep_tag = f"v{new_ver}"  # not a resolver tag\n')
        assert len(_pipeline_findings(root)) == 1


# ── The CLEARED side: every construction shape, whatever the variable is called ──


class TestConstructionShapesClear:
    def test_fstring_named_dep_tag(self, tmp_path: Path) -> None:
        """ai-maestro-plugin.py's real shape."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    dep_tag = f"{get_plugin_name(root)}--v{new_ver}"\n')
        assert _pipeline_findings(root) == []

    def test_fstring_named_resolver_tag_THE_BUG(self, tmp_path: Path) -> None:
        """ai-maestro-maintainer-agent.py's real shape — the file issue #168 is about.
        Correct pipeline, non-canonical variable name, previously reported as never
        tagging while its releases carried the tag."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    resolver_tag = f"{get_plugin_name(root)}--v{new_ver}"\n')
        assert _pipeline_findings(root) == []

    def test_fstring_arbitrary_variable_name(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    my_own_name = f"{name}--v{new_ver}"\n')
        assert _pipeline_findings(root) == []

    def test_string_concatenation(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    t = name + "--v" + new_ver\n')
        assert _pipeline_findings(root) == []

    def test_percent_format(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage='    t = "%s--v%s" % (name, new_ver)\n')
        assert _pipeline_findings(root) == []


class TestCliFlagsAreNotTagConstruction:
    """`--verbose` / `--version` / `--verify` must never READ as a dependency tag —
    that would silence the finding on the 11 fleet files that legitimately lack it."""

    def test_flags_alone_are_not_a_dep_tag(self) -> None:
        from cpv_validation_common import (  # type: ignore[import-not-found]
            publish_py_creates_dependency_tag,
        )

        for flag in ("--verbose", "--version", "--verify", "-v --verbose\n--version"):
            assert publish_py_creates_dependency_tag(flag) is False, flag

    def test_real_construction_shapes_are(self) -> None:
        from cpv_validation_common import (  # type: ignore[import-not-found]
            publish_py_creates_dependency_tag,
        )

        for shape in (
            'f"{name}--v{ver}"',
            'name + "--v" + ver',
            '"%s--v%s" % (name, ver)',
            "f'{name}--v{ver}'",
        ):
            assert publish_py_creates_dependency_tag(shape) is True, shape


# ── Ground truth beats static analysis: an already-tagged repo ───────────────


class TestShippedTagSilencesSignal1:
    def test_real_resolver_tag_present_no_pipeline_warning(self, tmp_path: Path) -> None:
        """A repo carrying `{name}--v*` has demonstrably shipped resolver tags. Even
        with a publish.py the static read cannot recognise, it is not missing them."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")  # would fire on the static read alone
        _git_repo(root, ["v1.0.0", "demo-plugin--v1.0.0"])
        assert _findings(root) == []

    def test_without_the_tag_the_same_repo_fires_both_signals(self, tmp_path: Path) -> None:
        """Positive control for the test above: identical repo MINUS the resolver tag
        must still fire — otherwise the override is silencing everything."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")
        _git_repo(root, ["v1.0.0"])
        found = _findings(root)
        assert any("RC-DEP-TAG-PIPELINE" in f for f in found), found
        assert any("RC-DEP-TAG-MISSING" in f for f in found), found

    def test_single_hyphen_near_miss_is_not_a_real_tag(self, tmp_path: Path) -> None:
        """`{name}-v1.0.0` resolves nothing — it must NOT count as ground truth."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")
        _git_repo(root, ["v1.0.0", "demo-plugin-v1.0.0"])
        found = _findings(root)
        assert any("RC-DEP-TAG-PIPELINE" in f for f in found), found
        assert any("SINGLE hyphen" in f for f in found), found

    def test_no_git_repo_still_fires_the_pipeline_signal(self, tmp_path: Path) -> None:
        """Fail-quiet on tags must not become fail-quiet on the finding: an
        UNINSTALLED, tag-less source is exactly what Signal 1 exists to cover."""
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")
        assert len(_pipeline_findings(root)) == 1


# ── Remediation text: the advice must be runnable ────────────────────────────


class TestRemediationText:
    """Both findings used to advise `standardize --fix --force-templates` (the one
    flag a customized plugin cannot safely run) and `claude plugin tag --push`
    (whose argument is a PATH, not a tag name — as advice, a silent no-op)."""

    def _both_messages(self, tmp_path: Path) -> list[str]:
        root = _plugin(tmp_path)
        _publish_py(root, tag_stage="")
        _git_repo(root, ["v1.0.0"])
        found = _findings(root)
        assert len(found) == 2, found
        return found

    def test_both_advise_a_plain_fix(self, tmp_path: Path) -> None:
        for msg in self._both_messages(tmp_path):
            assert "--fix" in msg, msg

    def test_neither_advises_force_templates(self, tmp_path: Path) -> None:
        for msg in self._both_messages(tmp_path):
            assert "--force-templates" not in msg, msg

    def test_neither_advises_claude_plugin_tag_push(self, tmp_path: Path) -> None:
        for msg in self._both_messages(tmp_path):
            assert "plugin tag --push" not in msg, msg
