"""ai-maestro#62 — the four canon requirements the fleet asked for, two-sided.

1. CHANGELOG history must survive a publish, and the step must be idempotent.
   `-o` OVERWRITES the file, so `--unreleased ... -o CHANGELOG.md` left a
   changelog containing only the release just generated. Measured when the
   report was confirmed: 6 of 7 plugin repos on the reporting host were down to
   ONE section, the canon's own repo having lost 380. `--prepend` is the WRONG
   fix (it accumulates), which is why the idempotency assertion is separate:
   "prior sections survive" alone is satisfied by `--prepend` too.
2. `--canon-version` reports installed-vs-latest canon and NEVER fails.
3. Pushed tags are verified ON THE REMOTE, not assumed from the stage.
4. The release is proven INSTALLABLE by an actual install.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as gen  # noqa: E402
import publish as own_publish  # noqa: E402
import standardize_plugin as std  # noqa: E402


def _params() -> gen.PluginParams:
    return gen.PluginParams(
        name="demo-plugin",
        description="demo",
        author="Emasoft",
        author_email="713559+Emasoft@users.noreply.github.com",
    )


def _canon_publish_py() -> str:
    return gen.gen_publish_py(_params())


# ── 1. CHANGELOG history ────────────────────────────────────────────────────


def test_emitted_changelog_call_has_no_unreleased_flag() -> None:
    """The emitted CHANGELOG.md call must not carry --unreleased (it would overwrite history)."""
    body = _canon_publish_py()
    assert '["git-cliff", "--bump", "--tag", tag, "-o", "CHANGELOG.md"]' in body
    assert '"--unreleased", "--tag", tag, "-o", "CHANGELOG.md"' not in body


def test_own_publish_changelog_call_has_no_unreleased_flag() -> None:
    """CPV's own pipeline carries the same fix — the canon owner must not ship the defect."""
    src = (SCRIPTS / "publish.py").read_text(encoding="utf-8")
    assert '[cliff_bin, "--bump", "--tag", tag_name, "-o", "CHANGELOG.md"]' in src
    assert '"--unreleased", "--tag", tag_name, "-o", "CHANGELOG.md"' not in src


def test_release_notes_extraction_still_uses_unreleased() -> None:
    """--unreleased still belongs on the release-NOTES call, which writes a separate file."""
    src = (SCRIPTS / "publish.py").read_text(encoding="utf-8")
    assert '"--unreleased"' in src, "the notes extraction must still scope to the unreleased window"


def test_changelog_regeneration_is_idempotent_and_keeps_history(tmp_path: Path) -> None:
    """Against a real git repo: the fixed command keeps prior tags and is byte-identical on re-run."""
    if not _have("git-cliff"):
        pytest.skip("git-cliff not installed")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "cliff.toml").write_text(_MINIMAL_CLIFF, encoding="utf-8")
    for i, ver in enumerate(("0.1.0", "0.2.0"), start=1):
        (repo / f"f{i}.txt").write_text("x", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"feat: thing {i}")
        _git(repo, "tag", f"v{ver}")
    (repo / "f3.txt").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feat: thing 3")

    fixed = ["git-cliff", "--bump", "--tag", "v0.3.0", "-o", "CHANGELOG.md"]
    subprocess.run(fixed, cwd=repo, check=True, capture_output=True)
    first = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    subprocess.run(fixed, cwd=repo, check=True, capture_output=True)
    second = (repo / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "0.1.0" in first and "0.2.0" in first, "prior releases must survive the bump"
    assert first == second, "re-running the step must be idempotent (this is what kills --prepend)"

    # Two-sided: the OLD command reproduces the defect on the same repo.
    subprocess.run(
        ["git-cliff", "--bump", "--unreleased", "--tag", "v0.3.0", "-o", "CHANGELOG.md"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    broken = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "0.1.0" not in broken, "the old command must destroy history (proves the test can fail)"


# ── 2. --canon-version ──────────────────────────────────────────────────────


def test_emitted_publish_bakes_the_canon_version() -> None:
    """A scaffolded publish.py carries the CPV version it was generated from, not the placeholder."""
    body = _canon_publish_py()
    assert 'CANON_VERSION = "0.0.0-unpinned"' not in body, "the placeholder must be rewritten"
    assert "CANON_VERSION = " in body


def test_emitted_canon_version_runs_offline_and_exits_zero(tmp_path: Path) -> None:
    """Executed for real with the fetch pointed at an unroutable URL: exact report, exit 0."""
    script = tmp_path / "publish.py"
    body = _canon_publish_py().replace(
        'CANON_LATEST_URL = "https://raw.githubusercontent.com/Emasoft/claude-plugins-validation/master/.claude-plugin/plugin.json"',
        'CANON_LATEST_URL = "https://127.0.0.1:9/nope.json"',
    ).replace("CANON_FETCH_TIMEOUT_S = 6", "CANON_FETCH_TIMEOUT_S = 1")
    script.write_text(body, encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(script), "--canon-version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert res.returncode == 0, f"info command must never fail: {res.stderr}"
    assert "Emasoft CPV Plugin Publishing Pipeline Canon" in res.stdout
    assert "* Installed Canon Version:" in res.stdout
    assert "unknown (could not reach GitHub)" in res.stdout
    assert 'Run "/cpv-agent update the canon" to update' in res.stdout


def test_own_canon_version_reports_up_to_date_when_equal(capsys) -> None:
    """When installed == latest the report says so instead of nagging to update."""
    own_publish.print_canon_version.__globals__["fetch_latest_canon_version"]  # noqa: B018 - presence
    original = own_publish.fetch_latest_canon_version
    try:
        own_publish.fetch_latest_canon_version = lambda: "9.9.9"  # type: ignore[assignment]
        rc = own_publish.print_canon_version("9.9.9")
        out = capsys.readouterr().out
        assert rc == 0
        assert "The canon is up to date." in out
        assert "/cpv-agent update the canon" not in out
    finally:
        own_publish.fetch_latest_canon_version = original  # type: ignore[assignment]


def test_own_canon_version_nags_when_behind(capsys) -> None:
    """When the installed canon is older, the update hint is printed verbatim."""
    original = own_publish.fetch_latest_canon_version
    try:
        own_publish.fetch_latest_canon_version = lambda: "9.9.9"  # type: ignore[assignment]
        rc = own_publish.print_canon_version("1.0.0")
        out = capsys.readouterr().out
        assert rc == 0
        assert 'Run "/cpv-agent update the canon" to update' in out
        assert "the plugin to the latest canon." in out
    finally:
        own_publish.fetch_latest_canon_version = original  # type: ignore[assignment]


def test_fetch_latest_canon_version_never_raises() -> None:
    """Any network failure returns None rather than propagating — info must not crash."""
    original_url = own_publish.CANON_LATEST_URL
    original_timeout = own_publish.CANON_FETCH_TIMEOUT_S
    try:
        own_publish.CANON_LATEST_URL = "https://127.0.0.1:9/nope.json"
        own_publish.CANON_FETCH_TIMEOUT_S = 1
        assert own_publish.fetch_latest_canon_version() is None
    finally:
        own_publish.CANON_LATEST_URL = original_url
        own_publish.CANON_FETCH_TIMEOUT_S = original_timeout


# ── 3 + 4. remote tag verification and the install smoke-test ───────────────


def test_own_push_stage_verifies_tags_on_the_remote() -> None:
    """The push stage asks the REMOTE whether each tag landed (prove the tag, not the stage)."""
    src = (SCRIPTS / "publish.py").read_text(encoding="utf-8")
    assert "for verify_tag in (tag_name," in src
    assert "_remote_tag_exists(plugin_root, verify_tag)" in src
    assert "Could NOT verify" in src, "an unverifiable tag must be reported, never silently green"


def test_install_smoke_skips_without_claude_cli_and_never_claims_a_pass(tmp_path: Path, monkeypatch, capsys) -> None:
    """No claude CLI (the normal CI case) → SKIPPED with the reason, explicitly not a pass."""
    monkeypatch.setattr(own_publish.shutil, "which", lambda _name: None)
    rc = own_publish.stage_install_smoke(tmp_path, "1.2.3")
    out = capsys.readouterr().out
    assert rc == 0
    assert "SKIPPED" in out
    assert "NOT a pass" in out


def test_install_smoke_opt_out_env(tmp_path: Path, monkeypatch, capsys) -> None:
    """CPV_PUBLISH_SKIP_INSTALL_SMOKE=1 skips the gate entirely."""
    monkeypatch.setenv("CPV_PUBLISH_SKIP_INSTALL_SMOKE", "1")
    rc = own_publish.stage_install_smoke(tmp_path, "1.2.3")
    assert rc == 0
    assert "CPV_PUBLISH_SKIP_INSTALL_SMOKE=1" in capsys.readouterr().out


def test_install_smoke_resolves_this_repo_marketplace() -> None:
    """Layout resolution finds CPV's real marketplace name rather than guessing one."""
    assert own_publish._resolve_marketplace_name(SCRIPTS.parent) == "emasoft-plugins"


def test_install_smoke_never_guesses_a_marketplace(tmp_path: Path) -> None:
    """An unwired plugin resolves to None — installing from a guessed marketplace proves nothing."""
    assert own_publish._resolve_marketplace_name(tmp_path) is None


def test_gate_15_is_registered_in_the_gate_list() -> None:
    """Gate 15 is declared, so `--print-gates` documents the install proof."""
    names = [name for name, _desc in own_publish.GATES]
    assert "Gate 15" in names


# ── migrators (delivery to plugins that already have a publish.py) ──────────


def test_changelog_migrator_fixes_an_old_publish_py(tmp_path: Path) -> None:
    """An old-shape publish.py loses --unreleased on the CHANGELOG call under a plain --fix."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text(
        "def stage_changelog():\n"
        '    run(\n        ["git-cliff", "--bump", "--unreleased", "--tag", tag, "-o", "CHANGELOG.md"],\n    )\n',
        encoding="utf-8",
    )
    notes = std.migrate_publish_py_changelog_history(tmp_path)
    text = pub.read_text(encoding="utf-8")
    assert notes and "CHANGELOG" in notes[0]
    assert '["git-cliff", "--bump", "--tag", tag, "-o", "CHANGELOG.md"]' in text
    assert "--unreleased" not in text


def test_changelog_migrator_is_idempotent(tmp_path: Path) -> None:
    """Re-running the migrator on an already-fixed file changes nothing and reports nothing."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text('    run(["git-cliff", "--bump", "--tag", tag, "-o", "CHANGELOG.md"],)\n', encoding="utf-8")
    before = pub.read_text(encoding="utf-8")
    assert std.migrate_publish_py_changelog_history(tmp_path) == []
    assert pub.read_text(encoding="utf-8") == before


def test_changelog_migrator_reports_an_unrecognised_shape(tmp_path: Path) -> None:
    """An unrecognisable changelog step is left byte-identical and reported, never half-rewritten."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text("def stage_changelog():\n    something_else()\n", encoding="utf-8")
    before = pub.read_text(encoding="utf-8")
    notes = std.migrate_publish_py_changelog_history(tmp_path)
    assert notes and "unrecognised" in notes[0]
    assert pub.read_text(encoding="utf-8") == before


def test_canon_version_migrator_adds_the_command(tmp_path: Path) -> None:
    """A publish.py without CANON_VERSION gains the block, the flag and the early return together."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text(_canon_without_canon_version(), encoding="utf-8")
    notes = std.migrate_publish_py_canon_version(tmp_path)
    text = pub.read_text(encoding="utf-8")
    assert notes and "canon-version" in notes[0]
    assert "CANON_VERSION" in text
    assert 'parser.add_argument("--canon-version"' in text
    assert "if args.canon_version:" in text
    assert "return print_canon_version()" in text
    compile(text, str(pub), "exec")  # the migrated file must still be valid Python


def test_canon_version_migrator_is_idempotent(tmp_path: Path) -> None:
    """Running the canon-version migrator twice leaves the file untouched the second time."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text(_canon_without_canon_version(), encoding="utf-8")
    std.migrate_publish_py_canon_version(tmp_path)
    once = pub.read_text(encoding="utf-8")
    assert std.migrate_publish_py_canon_version(tmp_path) == []
    assert pub.read_text(encoding="utf-8") == once


def test_canon_version_migrator_reports_unrecognised_main(tmp_path: Path) -> None:
    """A publish.py whose main() shape is unknown is left byte-identical and reported."""
    pub = tmp_path / "scripts" / "publish.py"
    pub.parent.mkdir(parents=True)
    pub.write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    before = pub.read_text(encoding="utf-8")
    notes = std.migrate_publish_py_canon_version(tmp_path)
    assert notes and "unrecognised" in notes[0]
    assert pub.read_text(encoding="utf-8") == before


# ── helpers ────────────────────────────────────────────────────────────────

_MINIMAL_CLIFF = """[changelog]
header = ""
body = '''
## [{{ version | trim_start_matches(pat="v") }}]
{% for commit in commits %}- {{ commit.message }}
{% endfor %}'''
trim = true
[git]
conventional_commits = true
filter_unconventional = false
"""


def _have(binary: str) -> bool:
    import shutil as _sh

    return _sh.which(binary) is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _canon_without_canon_version() -> str:
    """Canon publish.py with the canon-version feature stripped — the pre-v5.3.0 shape."""
    body = _canon_publish_py()
    start = body.index(std._CANON_VER_START)
    end = body.index(std._CANON_VER_END, start)
    body = body[:start] + body[end:]
    body = body.replace(
        '    parser.add_argument("--canon-version", action="store_true",\n'
        '                        help="Report the installed vs latest CPV publish-canon version and exit")\n',
        "",
    )
    marker = "    if args.canon_version:\n        return print_canon_version()\n\n"
    return body.replace(marker, "")


def test_the_stripped_fixture_really_lacks_the_feature() -> None:
    """Guard the guard: the pre-v5.3.0 fixture must genuinely lack canon-version, or the
    migrator tests would pass vacuously against an already-featured file."""
    stripped = _canon_without_canon_version()
    assert "CANON_VERSION" not in stripped
    assert "--canon-version" not in stripped
    assert "args.canon_version" not in stripped


def test_emitted_template_declares_a_valid_manifest_version() -> None:
    """The baked CANON_VERSION matches CPV's own manifest version — the two cannot disagree."""
    manifest = json.loads((SCRIPTS.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert f'CANON_VERSION = "{manifest["version"]}"' in _canon_publish_py()
