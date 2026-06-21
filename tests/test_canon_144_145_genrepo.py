"""Two-sided tests for GitHub issues #144 + #145 — canon-template QUALITY
(``generate_plugin_repo.py``: ``.markdownlint.json``, ``cliff.toml``, the
generated ``publish.py``).

The upgrade flow (``standardize --fix --force-templates``) shipped canon
template defaults that REGRESS a hand-tuned / clean-validating plugin:

* ``.markdownlint.json`` enabled ``MD024: {siblings_only: true}`` — which flags
  legitimate recurring per-release CHANGELOG section headings (#144 reported a
  clean changelog → 4 errors) — and dropped ``MD025``, so a doc carrying a
  frontmatter ``title:`` AND a body ``# H1`` (the common TRDD shape) trips MD025
  and the ``--strict`` publish gate BLOCKS (#145a);
* ``cliff.toml`` dropped the commit scope + short hash (less-traceable
  changelog) and could render a ``release:`` commit as a ``### Release`` noise
  group (#144);
* the generated ``publish.py`` had to stay ``ruff``-clean (E302 etc.) and keep
  its import-fallback ``# type: ignore[no-redef, misc]`` shims (#145c).

The fixes:

* ``gen_markdownlint_json``: DROP MD024 entirely (``"MD024": false`` — a
  changelog necessarily recurs its section headings, so neither MD024 mode is
  non-hostile; the reporter recommended OFF) and ADD
  ``"MD025": {"front_matter_title": ""}`` (so a frontmatter-titled doc's body
  ``# H1`` is the sole title). A genuine duplicate top-level ``# H1`` is still
  caught by MD025.
* ``gen_cliff_toml``: RESTORE the conditional scope prefix + 7-char short hash
  on each commit line; KEEP the em-dash ``— `` section header (release.yml's
  awk section-extractor keys on it); skip a bare ``release:`` commit so it never
  renders as a ``### Release`` group.
* ``gen_publish_py``: the emitted text is ``ruff``-clean and the shims carry
  ``# type: ignore[no-redef, misc]``.

Every guard is TWO-SIDED: the desired thing is asserted PRESENT/CLEAN and the
matching regressive form is asserted ABSENT/FLAGGED, so a regression in either
direction fails a test. Where the real ``markdownlint`` / ``git-cliff`` binary
is on PATH the test exercises it end-to-end; otherwise it asserts on the
generated config structure / text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_cliff_toml,
    gen_markdownlint_json,
    gen_publish_py,
)


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run_markdownlint(workdir: Path, target: str) -> subprocess.CompletedProcess[str]:
    """Run real ``markdownlint`` from ``workdir`` against a relative ``target``.

    The config and the target MUST live in ``workdir`` and be referenced by
    relative names — markdownlint-cli's ignore handling crashes on a
    cross-volume ``../../`` relative path (observed on this machine), which is a
    cli quirk, not a lint result.
    """
    return subprocess.run(
        ["markdownlint", "-c", ".markdownlint.json", target],
        cwd=str(workdir),
        capture_output=True,
        text=True,
    )


# ───────────────────────── .markdownlint.json ──────────────────────────────


def test_markdownlint_json_parses_as_json() -> None:
    """The generated ``.markdownlint.json`` is valid JSON (a malformed config
    silently disables linting)."""
    data = json.loads(gen_markdownlint_json(_params()))
    assert isinstance(data, dict), "markdownlint config must be a JSON object"


def test_markdownlint_md024_is_off() -> None:
    """MD024 is turned OFF (``false``) — NOT ``siblings_only`` (the #144 FP) and
    NOT left at its strict default (which flags cross-parent recurring headings
    too)."""
    data = json.loads(gen_markdownlint_json(_params()))
    assert data.get("MD024") is False, (
        f"MD024 must be off (False); got {data.get('MD024')!r} "
        "(siblings_only or strict-default both flag valid changelog headings)"
    )


def test_markdownlint_md024_not_siblings_only() -> None:
    """Negative side: the regressive ``MD024: {siblings_only: true}`` config is
    GONE (it newly flagged a clean changelog in #144)."""
    data = json.loads(gen_markdownlint_json(_params()))
    assert data.get("MD024") != {"siblings_only": True}, (
        "MD024 siblings_only must not be reintroduced — it flags recurring "
        "CHANGELOG section headings"
    )


def test_markdownlint_md025_front_matter_title_present() -> None:
    """MD025 is configured with an empty ``front_matter_title`` so a doc with a
    YAML ``title:`` AND a body ``# H1`` does not double-count the title
    (#145a)."""
    data = json.loads(gen_markdownlint_json(_params()))
    assert data.get("MD025") == {"front_matter_title": ""}, (
        f"MD025 must be {{'front_matter_title': ''}}; got {data.get('MD025')!r}"
    )


def test_markdownlint_real_changelog_recurring_headings_clean(tmp_path: Path) -> None:
    """Real markdownlint: a changelog recurring its ``### Features`` /
    ``### Bug Fixes`` headings across ``## [version]`` sections is CLEAN under
    the generated config (the #144 regression)."""
    if _which("markdownlint") is None:
        pytest.skip("markdownlint not on PATH")
    (tmp_path / ".markdownlint.json").write_text(gen_markdownlint_json(_params()))
    (tmp_path / "changelog.md").write_text(
        "# Changelog\n\n"
        "## [2.0.0]\n\n### Features\n\n- thing\n\n### Bug Fixes\n\n- fix\n\n"
        "## [1.0.0]\n\n### Features\n\n- old thing\n\n### Bug Fixes\n\n- old fix\n"
    )
    res = _run_markdownlint(tmp_path, "changelog.md")
    assert res.returncode == 0, (
        f"recurring-heading changelog must lint clean; got:\n{res.stdout}{res.stderr}"
    )


def test_markdownlint_real_sibling_recurring_headings_clean(tmp_path: Path) -> None:
    """Real markdownlint: the EXACT #144 shape — the SAME heading twice under a
    SINGLE ``##`` parent (the case ``siblings_only: true`` flagged) — is CLEAN
    now that MD024 is off."""
    if _which("markdownlint") is None:
        pytest.skip("markdownlint not on PATH")
    (tmp_path / ".markdownlint.json").write_text(gen_markdownlint_json(_params()))
    (tmp_path / "siblings.md").write_text(
        "# Changelog\n\n## [1.0.0]\n\n### Bug Fixes\n\n- a\n\n### Bug Fixes\n\n- b\n"
    )
    res = _run_markdownlint(tmp_path, "siblings.md")
    assert res.returncode == 0, (
        f"same-heading-twice-under-one-parent must lint clean; got:\n{res.stdout}{res.stderr}"
    )


def test_markdownlint_real_frontmatter_title_doc_clean(tmp_path: Path) -> None:
    """Real markdownlint: a doc with a YAML frontmatter ``title:`` AND a body
    ``# H1`` is CLEAN under the generated config (MD025 front_matter_title) —
    the #145a publish-blocker."""
    if _which("markdownlint") is None:
        pytest.skip("markdownlint not on PATH")
    (tmp_path / ".markdownlint.json").write_text(gen_markdownlint_json(_params()))
    (tmp_path / "fmdoc.md").write_text(
        "---\ntitle: My Design Doc\n---\n\n# My Design Doc\n\nBody text here.\n"
    )
    res = _run_markdownlint(tmp_path, "fmdoc.md")
    assert res.returncode == 0, (
        f"frontmatter-titled doc must lint clean; got:\n{res.stdout}{res.stderr}"
    )


def test_markdownlint_real_genuine_duplicate_h1_still_flagged(tmp_path: Path) -> None:
    """Negative side (real markdownlint): a GENUINE duplicate top-level ``# H1``
    is STILL flagged (by MD025) — disabling MD024 must not let two titles slip
    through."""
    if _which("markdownlint") is None:
        pytest.skip("markdownlint not on PATH")
    (tmp_path / ".markdownlint.json").write_text(gen_markdownlint_json(_params()))
    (tmp_path / "dupe.md").write_text(
        "# First Title\n\nSome text.\n\n# Second Title\n\nMore text.\n"
    )
    res = _run_markdownlint(tmp_path, "dupe.md")
    assert res.returncode != 0, "two top-level H1 must still be flagged"
    # markdownlint writes findings to stderr — check both streams.
    assert "MD025" in (res.stdout + res.stderr), (
        f"expected an MD025 finding; got:\n{res.stdout}{res.stderr}"
    )


# ───────────────────────────── cliff.toml ──────────────────────────────────


def test_cliff_keeps_em_dash_section_header() -> None:
    """The em-dash ``— `` section-header separator is KEPT — release.yml's awk
    section-extractor keys on ``## [ver] — date``; reverting it breaks
    extraction."""
    toml = gen_cliff_toml(_params())
    assert ' — {{ timestamp | date(format="%Y-%m-%d") }}' in toml, (
        "the em-dash section-header separator must be preserved"
    )


def test_cliff_renders_commit_scope_conditionally() -> None:
    """The commit line RESTORES the conditional scope prefix
    (``{% if commit.scope %}**{{ commit.scope }}:** {% endif %}``) — #144
    changelog traceability."""
    toml = gen_cliff_toml(_params())
    assert "{% if commit.scope %}**{{ commit.scope }}:** {% endif %}" in toml, (
        "the commit scope prefix must be restored"
    )


def test_cliff_renders_commit_short_hash() -> None:
    """The commit line RESTORES the 7-char short hash
    (``commit.id | truncate(length=7, ...)``) — #144 changelog traceability."""
    toml = gen_cliff_toml(_params())
    assert "commit.id | truncate(length=7" in toml, (
        "the commit short-hash must be restored on each rendered commit line"
    )


def test_cliff_skips_bare_release_commit() -> None:
    """A bare ``release:`` commit is skipped so it never renders as a
    ``### Release`` noise group (#144)."""
    toml = gen_cliff_toml(_params())
    assert '{ message = "^release", skip = true }' in toml, (
        "a `^release` skip parser must be present"
    )


def test_cliff_chore_release_skip_still_present() -> None:
    """Regression guard: the pre-existing ``^chore\\(release\\)`` skip is not
    lost by the ``^release`` addition (both must coexist)."""
    toml = gen_cliff_toml(_params())
    assert "chore\\\\(release\\\\)" in toml, "the chore(release) skip must remain"


def test_cliff_real_git_cliff_renders_scope_hash_and_skips_release(tmp_path: Path) -> None:
    """Real git-cliff end-to-end: a scoped commit renders ``**scope:** Msg
    (hash)``, an unscoped commit renders ``Msg (hash)`` with no scope, and a
    ``release:`` commit produces NO ``### Release`` group."""
    if _which("git-cliff") is None or _which("git") is None:
        pytest.skip("git-cliff/git not on PATH")
    (tmp_path / "cliff.toml").write_text(gen_cliff_toml(_params()))

    def _git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)

    _git("init")
    _git("config", "user.email", "t@e.com")
    _git("config", "user.name", "Test")
    for i, msg in enumerate(
        [
            "feat(auth): add login flow",
            "fix: handle null token",
            "release: v1.0.0",
            "feat: plain unscoped feature",
        ]
    ):
        (tmp_path / f"f{i}").write_text(str(i))
        _git("add", f"f{i}")
        _git("commit", "-m", msg)
    _git("tag", "v1.0.0")

    out = subprocess.run(
        ["git-cliff", "--config", "cliff.toml", "--tag", "v1.0.0"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    # Scope prefix rendered for the scoped commit.
    assert "**auth:** Add login flow" in out, f"scope not rendered; got:\n{out}"
    # An unscoped commit has NO scope prefix but DOES carry a short hash in parens.
    assert "Plain unscoped feature (" in out, f"unscoped commit/hash wrong; got:\n{out}"
    # Short hash present (7-hex in parens) on a rendered line.
    import re as _re

    assert _re.search(r"\([0-9a-f]{7}\)", out), f"no 7-hex short hash rendered; got:\n{out}"
    # The em-dash section header is present (awk-extractable).
    assert "## [1.0.0] — " in out, f"em-dash section header missing; got:\n{out}"
    # The `release:` commit is SKIPPED — no Release group, no its subject line.
    assert "### Release" not in out, f"release: must not form a ### Release group; got:\n{out}"
    assert "v1.0.0" not in out.replace("[1.0.0]", ""), (
        f"the release: commit subject must be skipped; got:\n{out}"
    )


# ───────────────────────── generated publish.py ─────────────────────────────


def test_generated_publish_py_is_ruff_clean(tmp_path: Path) -> None:
    """The generated ``publish.py`` passes ``ruff check`` (E302 two-blank-lines
    between top-level funcs, and every other lint) — #145c."""
    if _which("ruff") is None:
        pytest.skip("ruff not on PATH")
    pub = tmp_path / "publish.py"
    pub.write_text(gen_publish_py(_params()))
    res = subprocess.run(
        ["ruff", "check", str(pub)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"generated publish.py is not ruff-clean:\n{res.stdout}{res.stderr}"
    )


def test_generated_publish_py_submodule_profile_is_ruff_clean(tmp_path: Path) -> None:
    """The ``submodule-build`` profile's generated ``publish.py`` is also
    ruff-clean (the profile extends the same shared standard body)."""
    if _which("ruff") is None:
        pytest.skip("ruff not on PATH")
    pub = tmp_path / "publish_submodule.py"
    pub.write_text(gen_publish_py(_params(), "submodule-build"))
    res = subprocess.run(
        ["ruff", "check", str(pub)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"submodule-build publish.py is not ruff-clean:\n{res.stdout}{res.stderr}"
    )


def test_generated_publish_py_shims_carry_no_redef_misc_ignore() -> None:
    """The import-fallback shims (gh_with_retry / git_with_retry) carry
    ``# type: ignore[no-redef, misc]`` — mypy --strict needs ``misc`` alongside
    ``no-redef`` for the conditional-variant redefinition (v2.138.0 — confirm no
    regression)."""
    text = gen_publish_py(_params())
    assert text.count("# type: ignore[no-redef, misc]") >= 2, (
        "both import-fallback shims must keep the [no-redef, misc] ignore"
    )
    # Negative side: the bare [no-redef] form (which fails mypy --strict) must
    # NOT be how the two shims are annotated.
    assert "def gh_with_retry(cmd, **kwargs):  # type: ignore[no-redef]\n" not in text, (
        "the gh_with_retry shim must not regress to a bare [no-redef] ignore"
    )
