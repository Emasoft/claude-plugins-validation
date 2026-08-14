"""Four canonical-pipeline defects, fixed in the GENERATOR — the high-impact instance.

Every plugin CPV scaffolds inherits whatever `gen_publish_py()` / `gen_cliff_toml()`
emit, so each test here reads the EMITTED artifact, never the generator source. That
distinction is load-bearing: a prior release shipped a `NameError` in the emitted
template because only the generator had been read.

* #199 — the emitted canon-version probe carried a trailing comment ending in the
  word "input" on the `urllib.request.Request(` line, which COMPLETES the
  SSRF_ADVANCED needle (a request-call token plus the word "input"). The canon emitted code its
  own validator blocked. The word moved off the call line; the `# nosec B310`
  marker stayed ON it, because bandit only honours it there — trading one gate for
  another would not be a fix.
* #205 (derived defect) — `stage_gh_release` passed the WHOLE CHANGELOG.md as one
  release's `--notes-file`. Since `--unreleased` was dropped from the changelog
  call (ai-maestro#62) that file is full history, so every scaffolded plugin
  published its entire history as every release's body — and GitHub caps a release
  body at 125,000 characters, so a long-lived plugin eventually fails
  `gh release create` AFTER its tag is public. Notes are now rendered separately.
* #202 — a bare `@word` in a commit subject survives git-cliff into the release
  body, where it linkifies and PAGES a live account. A cliff.toml postprocessor
  backticks it in the RENDERED output (a hand edit of CHANGELOG.md is undone by
  the next run).
* #209 (emitted half) — the install smoke uninstalled with `--keep-data` and never
  verified the local-scope record left `installed_plugins.json`, so an orphan
  pointing at a deleted temp dir accumulated silently.

Every "the fix works" assertion is paired with a control proving the probe can
fail: a suppression test with no positive control passes vacuously.
"""

from __future__ import annotations

import ast
import builtins
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as gen  # noqa: E402

# The pre-fix shape of the #199 line, verbatim from canon 5.4.0. Used as the
# positive control: the probes MUST fire on it, or they prove nothing.
PRE_FIX_REQUEST_LINE = (
    "    req = urllib.request.Request(  # nosec B310 - fixed https constant, never user input\n"
)


def _params() -> gen.PluginParams:
    return gen.PluginParams(
        name="demo-plugin",
        description="demo",
        author="Emasoft",
        author_email="713559+Emasoft@users.noreply.github.com",
    )


def _emitted_publish() -> str:
    return gen.gen_publish_py(_params())


def _emitted_cliff() -> str:
    return gen.gen_cliff_toml(_params())


def _func_source(src: str, name: str) -> str:
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    segment = ast.get_source_segment(src, node)
    assert segment, f"could not extract {name} from the emitted publish.py"
    return segment


def _ssrf_patterns() -> list[re.Pattern[str]]:
    """The REAL catalog patterns — not a paraphrase of them."""
    catalog = json.loads((SCRIPTS / "rules" / "skillaudit_patterns.json").read_text(encoding="utf-8"))

    def find(node: object) -> dict | None:
        if isinstance(node, dict):
            if node.get("id") == "SSRF_ADVANCED":
                return node
            for value in node.values():
                hit = find(value)
                if hit:
                    return hit
        elif isinstance(node, list):
            for value in node:
                hit = find(value)
                if hit:
                    return hit
        return None

    rule = find(catalog)
    assert rule is not None, "SSRF_ADVANCED is gone from the catalog — this suite needs updating"
    return [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in rule["patterns"]]


# ─────────────────────────────────────────────────────────────────────────────
# #199 — the emitted canon-version probe must not complete the SSRF needle
# ─────────────────────────────────────────────────────────────────────────────
def test_the_probe_fires_on_the_pre_fix_line() -> None:
    """POSITIVE CONTROL. Without it the next test could pass on a broken probe."""
    assert any(p.search(PRE_FIX_REQUEST_LINE) for p in _ssrf_patterns())


def test_the_emitted_publish_py_matches_no_ssrf_pattern() -> None:
    """The whole emitted file, line by line, against the real catalog patterns."""
    patterns = _ssrf_patterns()
    hits = [
        (i + 1, line)
        for i, line in enumerate(_emitted_publish().splitlines())
        if any(p.search(line) for p in patterns)
    ]
    assert hits == [], f"emitted canon still matches SSRF_ADVANCED: {hits}"


def test_the_nosec_marker_stays_on_the_call_line() -> None:
    """bandit honours `# nosec` only on the offending line — moving it is not a fix.

    Reading the word off the line while losing the B310 suppression would trade a
    CPV finding for a bandit finding in every scaffolded plugin.
    """
    src = _func_source(_emitted_publish(), "fetch_latest_canon_version")
    call_line = next(ln for ln in src.splitlines() if "urllib.request.Request(" in ln)
    assert "# nosec B310" in call_line
    assert "input" not in call_line


def test_the_rationale_is_preserved_above_the_call() -> None:
    """The MEANING must survive the reword, or the next reader deletes the nosec."""
    src = _func_source(_emitted_publish(), "fetch_latest_canon_version")
    head = src[: src.index("urllib.request.Request(")]
    assert "https literal" in head
    assert "caller-supplied" in head


# ─────────────────────────────────────────────────────────────────────────────
# #205 — release notes are THIS release's section, never the whole changelog
# ─────────────────────────────────────────────────────────────────────────────
def test_the_changelog_call_still_renders_full_history() -> None:
    """ai-maestro#62 must not be regressed by the notes work: `-o` overwrites."""
    body = _func_source(_emitted_publish(), "stage_changelog")
    assert '["git-cliff", "--bump", "--tag", tag, "-o", "CHANGELOG.md"]' in body
    argv = body[body.index('["git-cliff"') : body.index("]", body.index('["git-cliff"')) + 1]
    assert "--unreleased" not in argv


def test_the_notes_call_is_a_separate_unreleased_render() -> None:
    """`--unreleased` belongs to the NOTES file and nowhere else."""
    body = _func_source(_emitted_publish(), "_write_release_notes")
    assert '"git-cliff", "--unreleased", "--tag", tag, "--strip", "all", "-o", str(notes)' in body
    assert "CHANGELOG.md" not in body.split('"""', 2)[-1], "the notes render must not touch CHANGELOG.md"


def test_the_gh_release_never_passes_the_changelog_as_notes() -> None:
    """The defect itself: `--notes-file CHANGELOG.md` published the whole history.

    Asserted on the ARGV CONSTRUCTION, not on whether the string "CHANGELOG.md"
    appears anywhere in the function. The first version of this test grepped the
    whole body and failed on the fix's own explanatory COMMENT — a comment that
    names an anchor is indistinguishable from the anchor, and this repo has been
    bitten by that exact shape three times in one session before. What matters is
    what gets EXECUTED, so read the argv lines and nothing else.
    """
    body = _func_source(_emitted_publish(), "stage_gh_release")
    code = body.split('"""', 2)[-1]
    executable = "\n".join(ln for ln in code.splitlines() if not ln.lstrip().startswith("#"))
    assert '"--notes-file", str(notes_file)' in executable
    assert "--notes-file" not in executable.replace('"--notes-file", str(notes_file)', ""), (
        "there must be exactly one --notes-file argument, and it must be the notes file"
    )
    assert "CHANGELOG.md" not in executable, "the release body must never be the full-history changelog"


def test_the_two_notes_paths_are_spelled_identically() -> None:
    """The duplication is deliberate (each stage stands alone) — pin it against drift.

    A drifted second spelling would make stage_gh_release look for a file
    stage_changelog never wrote, silently falling back to --generate-notes forever.
    """
    emitted = _emitted_publish()
    spelling = 'root / "reports" / "publish" / f"release-notes-{new_ver}.md"'
    assert _func_source(emitted, "_write_release_notes").count(spelling) == 1
    assert _func_source(emitted, "stage_gh_release").count(spelling) == 1


def test_the_notes_file_lands_outside_the_tracked_tree() -> None:
    """It must be gitignored, or the release commit can sweep it in (#186)."""
    emitted = _emitted_publish()
    assert 'root / "reports" / "publish"' in emitted
    gitignore = gen.gen_gitignore(_params())
    assert re.search(r"^reports/$", gitignore, re.MULTILINE), (
        "reports/ must stay gitignored — it is what makes the notes path safe"
    )


def _run_stage_gh_release(root: Path, gh_returncode: int = 0) -> list[str]:
    """Exec the EMITTED stage_gh_release with gh stubbed; return the argv it built."""
    src = _func_source(_emitted_publish(), "stage_gh_release")
    captured: list[list[str]] = []

    class _Result:
        returncode = gh_returncode
        stdout = ""
        stderr = ""

    def _gh(args: list[str], **_kw: object) -> _Result:
        captured.append(list(args))
        return _Result()

    ns: dict = {
        "sys": sys,
        "re": re,
        "Path": Path,
        "cprint": lambda *a, **k: None,
        "gh_with_retry": _gh,
        "shutil": type("S", (), {"which": staticmethod(lambda _x: "/usr/bin/gh")}),
        "_resolve_owner_repo": lambda _root: ("o", "r"),
        "_ensure_gh_auth": lambda _o, _r: None,
    }
    for color in ("RED", "GREEN", "YELLOW", "BLUE", "NC", "BOLD"):
        ns[color] = ""
    exec(compile(ast.parse(src), "<stage>", "exec"), ns)  # noqa: S102 - executing our own emitted canon
    ns["stage_gh_release"](root, "1.2.3", False)
    assert captured, "gh was never invoked"
    return captured[0]


def _write_notes(root: Path, text: str) -> Path:
    notes = root / "reports" / "publish" / "release-notes-1.2.3.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(text, encoding="utf-8")
    return notes


def test_a_rendered_notes_file_is_used(tmp_path: Path) -> None:
    notes = _write_notes(tmp_path, "### Features\n\n- something\n")
    argv = _run_stage_gh_release(tmp_path)
    assert "--notes-file" in argv
    assert str(notes) in argv
    assert "--generate-notes" not in argv


def test_no_notes_file_falls_back_to_generate_notes(tmp_path: Path) -> None:
    """git-cliff absent / no cliff.toml / render failed — never an ambiguous slice."""
    argv = _run_stage_gh_release(tmp_path)
    assert "--generate-notes" in argv
    assert "--notes-file" not in argv


def test_an_empty_notes_file_falls_back_to_generate_notes(tmp_path: Path) -> None:
    """A zero-byte render must not publish an empty release body."""
    _write_notes(tmp_path, "   \n\n")
    argv = _run_stage_gh_release(tmp_path)
    assert "--generate-notes" in argv
    assert "--notes-file" not in argv


def test_a_changelog_alone_is_never_used_as_notes(tmp_path: Path) -> None:
    """THE #205 REGRESSION LOCK: a present CHANGELOG.md must not be reached for."""
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0]\n- old\n", encoding="utf-8")
    argv = _run_stage_gh_release(tmp_path)
    assert "--generate-notes" in argv
    assert not any("CHANGELOG.md" in a for a in argv)


def test_the_two_gh_notes_flags_are_never_passed_together(tmp_path: Path) -> None:
    """Passing both is undefined across gh versions."""
    _write_notes(tmp_path, "- something\n")
    argv = _run_stage_gh_release(tmp_path)
    assert not ("--notes-file" in argv and "--generate-notes" in argv)


# ─────────────────────────────────────────────────────────────────────────────
# #202 — the cliff.toml postprocessor
# ─────────────────────────────────────────────────────────────────────────────
def _postprocessors() -> list[dict[str, str]]:
    data = tomllib.loads(_emitted_cliff())
    entries = data["changelog"]["postprocessors"]
    assert isinstance(entries, list)
    return [dict(e) for e in entries]


def test_the_emitted_cliff_toml_parses_and_carries_a_postprocessor() -> None:
    entries = _postprocessors()
    assert entries, "postprocessors = [] leaves every adopter's release notes able to page"
    assert all("pattern" in e and "replace" in e for e in entries)


def test_the_pattern_uses_no_lookaround() -> None:
    """git-cliff compiles it with Rust `regex`, which rejects lookaround outright.

    A pattern that fails to compile is not a weaker guard, it is NO guard — and
    git-cliff would surface it as a config error at release time.
    """
    for entry in _postprocessors():
        assert "(?=" not in entry["pattern"]
        assert "(?!" not in entry["pattern"]
        assert "(?<" not in entry["pattern"]


def _apply_postprocessors(text: str) -> str:
    """Apply the emitted postprocessors in order, the way git-cliff does."""
    out = text
    for entry in _postprocessors():
        pattern = re.compile(entry["pattern"])
        replacement = re.sub(r"\$\{(\d+)\}", r"\\\1", entry["replace"])
        out = pattern.sub(replacement, out)
    return out


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        # WRAPPED — these page a live account when rendered in a release body.
        ("thanks @janitor for the report", "thanks `@janitor` for the report"),
        ("see (@carol) about it", "see (`@carol`) about it"),
        ("credit @dave.", "credit `@dave`."),
        ("@alice opened it", "`@alice` opened it"),
        ("ping @alice @bob @carol @dave", "ping `@alice` `@bob` `@carol` `@dave`"),
        # UNTOUCHED — none of these page anyone, and mangling them is a regression.
        ("mail user@example.com", "mail user@example.com"),
        ("bump actions/checkout@v4", "bump actions/checkout@v4"),
        ("leave `@backticked` alone", "leave `@backticked` alone"),
        ("add @types/node", "add @types/node"),
        ("use @lru_cache", "use @lru_cache"),
        ("nothing to see here", "nothing to see here"),
    ],
)
def test_the_postprocessor_wraps_only_what_pages(subject: str, expected: str) -> None:
    assert _apply_postprocessors(subject) == expected


def test_the_postprocessor_is_idempotent() -> None:
    """git-cliff re-renders the whole changelog on every publish."""
    once = _apply_postprocessors("thanks @janitor and @alice @bob")
    assert _apply_postprocessors(once) == once


def test_without_the_postprocessor_the_mention_survives() -> None:
    """NON-VACUITY: prove the fixture text really would page without the fix."""
    assert "`@janitor`" not in "thanks @janitor for the report"


@pytest.mark.skipif(
    shutil.which("git-cliff") is None or shutil.which("git") is None,
    reason="needs the real git-cliff + git to prove the Rust regex behaves as modelled",
)
def test_real_git_cliff_end_to_end(tmp_path: Path) -> None:
    """The model above is Python `re`; this is git-cliff's own Rust engine.

    It also proves #205 end to end: the notes hold THIS release's section only,
    while CHANGELOG.md keeps every prior one.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "cliff.toml").write_text(_emitted_cliff(), encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=60)

    git("init", "-q", ".")
    git("config", "user.name", "Emasoft")
    git("config", "user.email", "713559+Emasoft@users.noreply.github.com")

    def commit(subject: str, body: str) -> None:
        (repo / "f.txt").write_text(body, encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", subject)

    commit("feat: the first release feature", "1")
    subprocess.run(["git", "tag", "-a", "v0.1.0", "-m", "v0.1.0"], cwd=repo, check=True,
                   capture_output=True, timeout=60)
    commit("feat: thanks @janitor for the report", "2")
    commit("fix: bump actions/checkout@v4 and mail user@example.com", "3")
    commit("fix: ping @alice @bob and (@carol), see @dave.", "4")
    commit("chore: leave `@backticked` alone, add @types/node and @lru_cache", "5")

    notes = repo / "reports" / "publish" / "release-notes-0.2.0.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git-cliff", "--bump", "--tag", "v0.2.0", "-o", "CHANGELOG.md"],
                   cwd=repo, check=True, capture_output=True, timeout=300)
    subprocess.run(["git-cliff", "--unreleased", "--tag", "v0.2.0", "--strip", "all",
                    "-o", str(notes)], cwd=repo, check=True, capture_output=True, timeout=300)

    changelog_text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    notes_text = notes.read_text(encoding="utf-8")

    # #205: full history in the changelog, this release alone in the notes.
    assert "[0.1.0]" in changelog_text and "[0.2.0]" in changelog_text
    assert "[0.2.0]" in notes_text
    assert "[0.1.0]" not in notes_text
    assert len(notes_text) < len(changelog_text)

    # #202: the release body cannot page anyone.
    assert "`@janitor`" in notes_text
    assert "`@alice` `@bob`" in notes_text
    assert "(`@carol`)" in notes_text
    assert "`@dave`." in notes_text
    assert "user@example.com" in notes_text and "`@example`" not in notes_text
    assert "actions/checkout@v4" in notes_text
    assert "@types/node" in notes_text and "`@types`" not in notes_text
    assert "@lru_cache" in notes_text and "`@lru`" not in notes_text
    assert "``@backticked``" not in notes_text
    # The catch-all must model what GitHub actually LINKIFIES, not every `@`.
    # A handle is alphanumeric-plus-hyphen, at a word boundary, and NOT followed
    # by `/` — measured with `gh api markdown`, which is why `@types/node`,
    # `@lru_cache`, `actions/checkout@v4` and `user@example.com` are all inert
    # and are deliberately left bare above. An earlier version of this line used
    # a bare `@[A-Za-z0-9]` and therefore contradicted the four assertions
    # directly above it: it flagged `@types/node`, which pages nobody. A guard
    # that fires on correct output is a guard that gets deleted.
    paging = re.search(r"(?:^|[^\w`/@])@[A-Za-z0-9][A-Za-z0-9-]*(?![\w/-])", notes_text)
    assert not paging, f"a bare mention that GitHub would linkify survived:\n{notes_text}"


# ─────────────────────────────────────────────────────────────────────────────
# #209 — the install smoke must ASSERT its own registry cleanup
# ─────────────────────────────────────────────────────────────────────────────
def _smoke_ns(registry: Path) -> dict:
    """Exec the EMITTED registry helpers with the registry path redirected."""
    emitted = _emitted_publish()
    ns: dict = {
        "json": json,
        "os": os,
        "Path": Path,
        "cprint": print,
        "_INSTALLED_PLUGINS_REGISTRY": registry,
    }
    for color in ("RED", "GREEN", "YELLOW", "BLUE", "NC", "BOLD"):
        ns[color] = ""
    for name in ("_smoke_records_still_registered", "_report_smoke_registry_orphan"):
        exec(compile(ast.parse(_func_source(emitted, name)), "<helper>", "exec"), ns)  # noqa: S102
    return ns


TARGET = "demo-plugin@emasoft-plugins"


def _registry_file(tmp_path: Path, records: list[dict], key: str = TARGET) -> Path:
    path = tmp_path / "installed_plugins.json"
    path.write_text(json.dumps({"version": 1, "plugins": {key: records}}), encoding="utf-8")
    return path


def test_the_emitted_smoke_checks_the_registry_after_uninstalling() -> None:
    src = _func_source(_emitted_publish(), "stage_install_smoke")
    assert "_report_smoke_registry_orphan(target, tmp)" in src
    # …and the flags that make the uninstall safe are untouched: --keep-data
    # protects the author's real USER-scope data dir, which is why the registry
    # record needed a separate check in the first place.
    assert '"--keep-data"' in src
    assert src.count('"--scope", "local"') >= 2


def test_a_surviving_record_is_reported(tmp_path: Path) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(tmp_path, [{"scope": "local", "projectPath": str(smoke)}])
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) == [str(smoke)]


def test_a_clean_uninstall_reports_nothing(tmp_path: Path) -> None:
    """CONTROL: a check that fired on a clean run is noise, and noise gets ignored."""
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(tmp_path, [])
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) == []


def test_another_projects_record_is_not_this_runs_orphan(tmp_path: Path) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    other = tmp_path / "someones-real-project"
    other.mkdir()
    registry = _registry_file(tmp_path, [{"scope": "local", "projectPath": str(other)}])
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) == []


def test_another_plugins_smoke_record_is_not_this_runs_orphan(tmp_path: Path) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(
        tmp_path, [{"scope": "local", "projectPath": str(smoke)}], key="other-plugin@other-mkt"
    )
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) == []


def test_the_path_compare_is_resolved_not_literal(tmp_path: Path) -> None:
    """macOS hands out /var/folders/... and the registry records /private/var/...

    A raw string compare finds nothing on exactly the platform that reported this.
    """
    real = tmp_path / "real-smoke-dir"
    real.mkdir()
    link = tmp_path / "linked-smoke-dir"
    link.symlink_to(real, target_is_directory=True)
    registry = _registry_file(tmp_path, [{"scope": "local", "projectPath": str(real)}])
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(link), registry) == [str(real)]


@pytest.mark.parametrize("content", ["", "{not json", '{"plugins": []}', "[]"])
def test_an_unusable_registry_is_unknown_never_clean(tmp_path: Path, content: str) -> None:
    """cannot-check is not a pass: None, never an empty list."""
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = tmp_path / "installed_plugins.json"
    registry.write_text(content, encoding="utf-8")
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) is None


def test_a_missing_registry_is_unknown_never_clean(tmp_path: Path) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = tmp_path / "installed_plugins.json"
    ns = _smoke_ns(registry)
    assert ns["_smoke_records_still_registered"](TARGET, str(smoke), registry) is None


def test_the_reporter_names_the_orphan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(tmp_path, [{"scope": "local", "projectPath": str(smoke)}])
    ns = _smoke_ns(registry)
    ns["_report_smoke_registry_orphan"](TARGET, str(smoke))
    out = capsys.readouterr().out
    assert str(smoke) in out
    assert "209" in out


def test_the_reporter_is_silent_on_a_clean_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(tmp_path, [])
    ns = _smoke_ns(registry)
    ns["_report_smoke_registry_orphan"](TARGET, str(smoke))
    assert capsys.readouterr().out == ""


def test_the_reporter_says_unverified_when_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    ns = _smoke_ns(tmp_path / "installed_plugins.json")
    ns["_report_smoke_registry_orphan"](TARGET, str(smoke))
    assert "UNVERIFIED" in capsys.readouterr().out


def test_the_check_is_read_only(tmp_path: Path) -> None:
    """A publish that edits Claude Code's shared state to tidy up is worse than the orphan."""
    smoke = tmp_path / "plugin-install-smoke-abc"
    smoke.mkdir()
    registry = _registry_file(tmp_path, [{"scope": "local", "projectPath": str(smoke)}])
    before = registry.read_bytes()
    ns = _smoke_ns(registry)
    ns["_report_smoke_registry_orphan"](TARGET, str(smoke))
    assert registry.read_bytes() == before


def test_the_registry_check_never_raises(tmp_path: Path) -> None:
    """NON-FATAL by construction: the release is already public when this runs."""
    registry = tmp_path / "installed_plugins.json"
    registry.write_text(json.dumps({"plugins": {TARGET: [{"projectPath": 42}, "junk", None]}}),
                        encoding="utf-8")
    ns = _smoke_ns(registry)
    ns["_report_smoke_registry_orphan"](TARGET, str(tmp_path / "nonexistent-smoke-dir"))


# ─────────────────────────────────────────────────────────────────────────────
# The emitted file has to actually RUN in someone else's repo
# ─────────────────────────────────────────────────────────────────────────────
def test_the_emitted_publish_py_compiles() -> None:
    compile(_emitted_publish(), "<publish>", "exec")


@pytest.mark.parametrize(
    "func",
    [
        "_write_release_notes",
        "stage_changelog",
        "stage_gh_release",
        "_smoke_records_still_registered",
        "_report_smoke_registry_orphan",
        "stage_install_smoke",
        "fetch_latest_canon_version",
    ],
)
def test_every_free_name_resolves_in_the_emitted_file(func: str) -> None:
    """A missing import is a NameError at publish time that `ast.parse` cannot see.

    The v5.4.0 lesson: the ported install smoke used `tempfile`, which the emitted
    template does not import at module level, and every scaffolded plugin would
    have failed at the least recoverable moment.
    """
    src = _emitted_publish()
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
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top.add(node.target.id)

    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func)
    local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
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
        elif isinstance(node, ast.FunctionDef) and node is not fn:
            local.add(node.name)
            local |= {a.arg for a in node.args.args}

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
