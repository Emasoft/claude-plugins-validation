"""Regression locks for the v5.3.0 code-review findings.

Every test here pins a defect that was CONFIRMED by reproduction, so each one
must fail against the pre-fix code. Grouped by the finding it locks.

The shared discipline: a check that cannot answer must never be rendered as a
verdict in either direction — not as a pass, and not as a failure.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_plugin_repo as gpr  # noqa: E402
import publish as P  # noqa: E402
import standardize_plugin as std  # noqa: E402


def _params() -> gpr.PluginParams:
    return gpr.PluginParams(
        name="sample-plugin",
        description="A sample plugin.",
        author="Emasoft",
        author_email="713559+Emasoft@users.noreply.github.com",
        github_owner="Emasoft",
        marketplace="emasoft-plugins",
    )


def _emitted() -> str:
    return gpr.gen_publish_py(_params())


# ---------------------------------------------------------------------------
# #1 — the release body must be THIS release's section, never the whole file
# ---------------------------------------------------------------------------


def _changelog_step(workflow_text: str) -> str:
    start = workflow_text.index("- name: Generate changelog")
    end = workflow_text.index("\n      - name:", start + 1)
    return workflow_text[start:end]


def _executable_lines(step: str) -> str:
    """The step with COMMENT lines removed.

    Absence assertions must read the code, never the prose. The comment that
    explains why `cp CHANGELOG.md changelog.txt` was removed necessarily
    CONTAINS that string — asserting over the raw step would fail on the
    explanation of the fix, which is finding #7's defect-pinning shape
    reproduced one level up.
    """
    return "\n".join(ln for ln in step.splitlines() if not ln.lstrip().startswith("#"))


def test_own_release_workflow_extracts_one_changelog_section() -> None:
    """CPV's OWN release.yml must extract the matching section, not `cp` the file.

    Measured at v5.3.0: the full CHANGELOG.md is 110,166 chars against GitHub's
    125,000-char release-body limit, so `cp CHANGELOG.md changelog.txt` was a
    few releases away from failing `gh release create` outright — after the tag
    is already public.
    """
    step = _changelog_step((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    assert "awk -v ver=" in step
    assert "cp CHANGELOG.md changelog.txt" not in _executable_lines(step)


def test_own_release_workflow_fallback_is_bounded() -> None:
    """The no-section-match fallback must be the git log, never the full CHANGELOG.

    A fallback to the whole file re-introduces the exact defect the extractor
    exists to prevent, the moment the section-header format drifts.
    """
    step = _executable_lines(
        _changelog_step((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    )
    assert "$GITLOG" in step
    assert "cp CHANGELOG.md" not in step


def test_generated_release_workflow_fallback_is_bounded() -> None:
    """Same bound in the canon every scaffolded plugin inherits."""
    yml = gpr.gen_release_yml(_params())
    assert "awk -v ver=" in yml
    assert "cp CHANGELOG.md changelog.txt" not in yml


def test_the_awk_extractor_matches_this_repos_real_changelog() -> None:
    """Behavioural: the extractor must actually match CPV's OWN header style.

    CPV's CHANGELOG uses the legacy ASCII hyphen (`## [5.3.0] - 2026-08-06`),
    so an extractor matching only the canonical em-dash would silently take the
    fallback on every single release while looking correct.
    """
    changelog = REPO_ROOT / "CHANGELOG.md"
    header = next(
        (ln for ln in changelog.read_text(encoding="utf-8").splitlines() if ln.startswith("## [")),
        None,
    )
    assert header, "CHANGELOG.md has no `## [x.y.z]` section header"
    version = header.split("[", 1)[1].split("]", 1)[0]

    step = _changelog_step((REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    awk_prog = step[step.index("awk -v ver=") : step.index("' CHANGELOG.md")]
    awk_prog = awk_prog[awk_prog.index("'") + 1 :]

    result = subprocess.run(
        ["awk", "-v", f"ver={version}", awk_prog, str(changelog)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0
    extracted = result.stdout
    assert extracted.startswith(f"## [{version}]")
    assert len(extracted) < len(changelog.read_text(encoding="utf-8")) / 10, (
        "the extracted section must be a small fraction of the whole changelog"
    )
    # Exactly ONE section: no later header leaked in.
    assert extracted.count("\n## [") == 0


# ---------------------------------------------------------------------------
# #2 / #3 — the canon-version migrator must prove its anchors before writing
# ---------------------------------------------------------------------------


def _canon_without_canon_version() -> str:
    canon = _emitted()
    i = canon.index(std._CANON_VER_START)
    j = canon.index(std._CANON_VER_END)
    text = canon[:i] + canon[j:]
    text = text.replace(
        'parser.add_argument("--canon-version", action="store_true",',
        'parser.add_argument("--zzz-unused", action="store_true",',
        1,
    )
    text = text.replace("    if args.canon_version:\n        return print_canon_version()\n", "", 1)
    assert "CANON_VERSION" not in text
    return text


def _run_migrator(tmp_path: Path, text: str) -> tuple[list[str], str, str]:
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True, exist_ok=True)
    pub.write_text(text, encoding="utf-8")
    before = pub.read_text(encoding="utf-8")
    notes = std.migrate_publish_py_canon_version(tmp_path)
    return notes, before, pub.read_text(encoding="utf-8")


def test_canon_version_migrator_still_migrates_a_healthy_shape(tmp_path: Path) -> None:
    """NON-VACUITY: the new guard must not refuse the shape it is meant to accept."""
    notes, before, after = _run_migrator(tmp_path, _canon_without_canon_version())
    assert notes and "added `--canon-version`" in notes[0]
    assert after != before
    assert "def print_canon_version" in after
    assert "args.canon_version" in after


def test_canon_version_migrator_refuses_a_custom_main_signature(tmp_path: Path) -> None:
    """#2: a main() the block cannot be inserted into must abort BEFORE any write.

    `str.replace` on an absent needle is a SILENT no-op, so the flag and the
    call were written with no `print_canon_version` — the file still PARSES, so
    the migrated-file-must-compile assertion could not catch it, and the author
    found out via NameError.
    """
    base = _canon_without_canon_version().replace(
        "def main() -> int:", "def main(argv: list[str] | None = None) -> int:", 1
    )
    notes, before, after = _run_migrator(tmp_path, base)
    assert after == before, "must be byte-identical — never half-migrated"
    assert notes and "NOT migrated" in notes[0]
    assert "args.canon_version" not in after, "the flag must not be written without its helper"


def test_canon_version_migrator_refuses_root_before_parse_args(tmp_path: Path) -> None:
    """#3: inserting at an anchor that precedes parse_args() emits `args` undefined.

    Every invocation — --patch, --gate, --print-gates — would die with
    NameError before any gate ran: a routine --fix bricking the pipeline.
    """
    base = _canon_without_canon_version()
    base = base.replace("    root = get_repo_root()\n", "", 1)
    base = base.replace("def main() -> int:\n", "def main() -> int:\n    root = get_repo_root()\n", 1)
    notes, before, after = _run_migrator(tmp_path, base)
    assert after == before, "must be byte-identical — never half-migrated"
    assert notes and "NOT migrated" in notes[0]
    assert "if args.canon_version:" not in after


# ---------------------------------------------------------------------------
# #5 — an offline canon check must not claim the canon is stale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "latest", "expect_update_advice"),
    [
        ("5.3.0", "5.3.0", False),  # current
        ("5.2.0", "5.3.0", True),  # genuinely behind — non-vacuity control
        ("5.3.0", None, False),  # OFFLINE: cannot compare, must NOT advise
        (None, "5.3.0", False),  # unreadable manifest: same
    ],
)
def test_canon_version_report_is_three_state(
    monkeypatch: pytest.MonkeyPatch, installed: str | None, latest: str | None, expect_update_advice: bool
) -> None:
    """"Could not compare" is not a verdict in either direction."""
    monkeypatch.setattr(P, "fetch_latest_canon_version", lambda: latest)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = P.print_canon_version(installed)
    out = buf.getvalue()
    assert rc == 0, "an info command never fails"
    assert ("update the canon" in out) is expect_update_advice


# ---------------------------------------------------------------------------
# #6 — the async-lag note must be earned by evidence
# ---------------------------------------------------------------------------


def test_lag_note_requires_a_version_actually_read() -> None:
    """Install stdout names the plugin, not the semver — so a bare `not in` always fired.

    A note that fires on every successful run carries no information and trains
    the reader to ignore it.
    """
    realistic = "Installing sample-plugin@emasoft-plugins...\nInstalled successfully."
    assert P._semvers_in(realistic) == [], "no version in stdout ⇒ no claim may be made"

    lagging = "Resolved sample-plugin v5.2.0 from emasoft-plugins"
    assert P._semvers_in(lagging) == ["5.2.0"]
    assert "5.3.0" not in P._semvers_in(lagging), "a real lag is still detected"

    current = "Resolved sample-plugin v5.3.0 from emasoft-plugins"
    assert "5.3.0" in P._semvers_in(current), "a current version stays quiet"


# ---------------------------------------------------------------------------
# #4 — an unregistered marketplace is cannot-check, not NOT-INSTALLABLE
# ---------------------------------------------------------------------------


def test_not_in_marketplace_signature_is_specific() -> None:
    assert P._NOT_IN_MARKETPLACE_RE.search(
        "Plugin not found in marketplace emasoft-plugins, try claude plugin marketplace update"
    )
    assert not P._NOT_IN_MARKETPLACE_RE.search("ENOENT: no such file or directory")
    assert not P._NOT_IN_MARKETPLACE_RE.search("error: network unreachable")


def test_marketplace_registration_probe_fails_safe_towards_hard_failure() -> None:
    """A probe that cannot run must NOT downgrade a real failure to SKIPPED."""
    assert P._marketplace_is_registered("/nonexistent/claude-binary-xyz", "any-marketplace") is True


# ---------------------------------------------------------------------------
# #9 — Gate 15 + remote tag verification must reach the EMITTED canon
# ---------------------------------------------------------------------------


def test_emitted_canon_carries_install_smoke_and_tag_verification() -> None:
    """ai-maestro#62 R2/R3 were filed about the FLEET, not about CPV alone."""
    src = _emitted()
    assert "def stage_install_smoke(" in src
    assert "def _remote_tag_exists(" in src
    assert "ls-remote" in src
    assert "stage_install_smoke(root, new_ver, args.dry_run)" in src, "must be wired into main()"


def test_emitted_canon_install_smoke_has_every_name_it_uses() -> None:
    """The v5.1.1 shape: a missing import is a NameError an AST parse cannot see.

    The emitted template does NOT import tempfile at module level, so
    stage_install_smoke must carry its own import or every scaffolded plugin
    NameErrors at publish time.
    """
    src = _emitted()
    tree = ast.parse(src)

    top: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                top.add((alias.asname or alias.name).split(".")[0])
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            top.add(node.name)
        elif isinstance(node, ast.Assign):
            top.update(t.id for t in node.targets if isinstance(t, ast.Name))

    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "stage_install_smoke")
    local = {a.arg for a in fn.args.args}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            local.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.withitem) and isinstance(node.optional_vars, ast.Name):
            local.add(node.optional_vars.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            local.add(node.name)

    import builtins

    unresolved = sorted(
        n.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and n.id not in local
        and n.id not in top
        and not hasattr(builtins, n.id)
    )
    assert unresolved == [], f"undefined at runtime in the emitted plugin: {unresolved}"


def test_emitted_canon_uninstalls_what_the_smoke_test_installed() -> None:
    """#10, in the canon: and with the two flags that stop it destroying a real install."""
    src = _emitted()
    start = src.index("def stage_install_smoke(")
    body = src[start : src.index("\ndef ", start + 1)]
    assert '"uninstall"' in body
    assert '"--keep-data"' in body, "must never drop a data dir shared with the user's real install"
    assert body.count('"--scope", "local"') >= 2, "install AND uninstall must both be local-scope"


def test_emitted_canon_still_compiles() -> None:
    ast.parse(_emitted())


# ---------------------------------------------------------------------------
# #10 — CPV's own Gate 15 must clean up after itself, safely
# ---------------------------------------------------------------------------


def test_own_install_smoke_uninstalls_safely() -> None:
    src = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
    start = src.index("def stage_install_smoke(")
    body = src[start : src.index("\ndef ", start + 1)]
    assert '"uninstall"' in body
    assert '"--keep-data"' in body
    assert body.count('"--scope", "local"') >= 2


# ---------------------------------------------------------------------------
# #7 — the rewritten test must read the ARGV, not the prose
# ---------------------------------------------------------------------------


def test_the_changelog_argv_carries_no_unreleased_flag() -> None:
    """Independent lock on the same property, read straight off the emitted argv."""
    src = _emitted()
    start = src.index("def stage_changelog(")
    body = src[start : src.index("\ndef ", start + 1)]
    argv = body[body.index('["git-cliff"') : body.index("]", body.index('["git-cliff"')) + 1]
    assert "--unreleased" not in argv
    assert re.search(r'"--bump".*"--tag", tag.*"-o", "CHANGELOG\.md"', argv)
