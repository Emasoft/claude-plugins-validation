"""RC-3 — the cspell local↔CI SPELL parity hole, closed from both ends.

THE DEFECT. CPV's canonical `.mega-linter.yml` ENABLES `SPELL_CSPELL` but shipped
NO project dictionary. In CI, cspell then had nothing that knows a plugin's own
proper nouns (its name, its agent/skill/command names, project vocabulary like
`wikimem` or `TRDD`), so every one of them was an "Unknown word" → RED Lint job
(3 observed failures, 8 and 33 errors). And the LOCAL preflight KNOWINGLY SKIPPED
the cspell probe when no config existed — a bare local cspell would false-block on
ordinary tech terms — so the author's preflight said GREEN and GitHub CI then said
RED. Local-green / CI-red, invisible until CI ran: the worst shape of defect.

THE FIX, both halves — either alone leaves the hole open:

1. `standardize_plugin.provision_cspell_config` EMITS the canonical `.cspell.json`,
   seeded from the plugin's own name + agent/skill/command names + the CPV/
   AI-Maestro/tech term set. cspell auto-discovers it and CI's Mega-Linter cspell
   reads that SAME file, so the two runs agree BY CONSTRUCTION.
2. `cpv_ci_preflight._gate_cspell` STOPS SKIPPING: with a config present it runs
   cspell for real and FAILs on a genuine misspelling; with SPELL enabled and NO
   config it reports the parity DEFECT (FAIL + the mechanical `standardize --fix`
   remediation) instead of waving it through.

EVERY assertion here is TWO-SIDED — a "the gate no longer fires" test is worthless
without the positive control that it still fires on a real defect:

* dictionary present + clean spelling → gate PASSES  (the FP side)
* dictionary present + REAL misspelling → gate still FAILS  (the positive control)
* SPELL enabled + no dictionary → FAIL, naming the remediation
* SPELL not enabled → PASS-skip (never invents a gate the plugin did not opt into)
* cspell binary absent → WARNING (a missing tool must NEVER false-block)

The provisioner half is likewise two-sided: it CREATES when absent, AUGMENTS when
present, and NEVER clobbers a config the author owns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_ci_preflight  # noqa: E402
import standardize_plugin  # noqa: E402
from cpv_ci_preflight import PreflightResult  # noqa: E402

CSPELL_ID = cpv_ci_preflight._CSPELL_LINTER_ID


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_plugin(tmp_path: Path, name: str = "wiki-mem-plugin") -> Path:
    """A minimal plugin tree with the proper nouns CI's cspell would flag."""
    root = tmp_path / name
    cp = root / ".claude-plugin"
    cp.mkdir(parents=True)
    (cp / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "t",
                "author": "Emasoft",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "agents").mkdir()
    (root / "agents" / "plugin-devitalizer.md").write_text("# devitalizer\n", encoding="utf-8")
    (root / "commands").mkdir()
    (root / "commands" / "cpv-main-menu.md").write_text("# menu\n", encoding="utf-8")
    (root / "skills" / "harden-and-redact").mkdir(parents=True)
    (root / "skills" / "harden-and-redact" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    return root


def _write_mega_linter(root: Path, linters: list[str]) -> None:
    body = "APPLY_FIXES: none\nENABLE_LINTERS:\n" + "".join(f"  - {x}\n" for x in linters)
    (root / ".mega-linter.yml").write_text(body, encoding="utf-8")


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_cspell(
    monkeypatch: pytest.MonkeyPatch, *, present: bool, proc: _FakeProc | None = None
) -> list[list[str]]:
    """Mock which()/subprocess.run for the preflight; return the argv calls made."""
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if present else None

    def fake_run(argv: list[str], **_kw: object) -> _FakeProc:
        calls.append(argv)
        return proc or _FakeProc(0)

    monkeypatch.setattr(cpv_ci_preflight.shutil, "which", fake_which)
    monkeypatch.setattr(cpv_ci_preflight.subprocess, "run", fake_run)
    return calls


def _finding(result: PreflightResult, gate: str = "cspell"):  # type: ignore[no-untyped-def]
    matches = [f for f in result.findings if f.gate == gate]
    assert len(matches) == 1, f"expected exactly one {gate} finding, got {len(matches)}"
    return matches[0]


def _gate(root: Path) -> PreflightResult:
    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_cspell(result, {CSPELL_ID})
    return result


# ===========================================================================
# THE TWO-SIDED GATE CONTRACT (the heart of RC-3)
# ===========================================================================


def test_dictionary_present_and_clean_spelling_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(i) A plugin WITH the emitted dictionary and clean spelling → gate PASSES.

    The probe must actually RUN cspell (no more skipping) and, on a clean exit,
    report PASS. This is the FP side of the pair."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    standardize_plugin.provision_cspell_config(root)
    calls = _patch_cspell(monkeypatch, present=True, proc=_FakeProc(0))

    f = _finding(_gate(root))

    assert f.severity == "PASS"
    assert calls, "with a dictionary present the probe MUST invoke cspell — not skip it"
    assert calls[0][1] == "lint"


def test_dictionary_present_but_real_misspelling_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(ii) POSITIVE CONTROL — a plugin with a REAL misspelling → gate still FAILS.

    Shipping the dictionary must not become a way to silence cspell. A non-zero
    cspell exit is surfaced as FAIL with the offending line, exactly as CI would."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    standardize_plugin.provision_cspell_config(root)
    _patch_cspell(
        monkeypatch,
        present=True,
        proc=_FakeProc(1, stdout="README.md:3:5 - Unknown word (teh)\n"),
    )

    f = _finding(_gate(root))

    assert f.severity == "FAIL"
    assert "Unknown word (teh)" in f.message


def test_spell_enabled_but_no_dictionary_is_a_reported_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPELL enabled + NO dictionary → FAIL naming the remediation. THE parity hole.

    It must no longer be a silent skip. The tool is still never invoked (a bare
    cspell would false-block on tech terms) — the defect is reported statically."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])  # no .cspell.json provisioned
    calls = _patch_cspell(monkeypatch, present=True, proc=_FakeProc(0))

    f = _finding(_gate(root))

    assert f.severity == "FAIL"
    assert "standardize --fix" in f.message
    assert calls == [], "a bare cspell (no dictionary) must never be invoked"


def test_spell_not_enabled_never_invents_a_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPELL_CSPELL absent from ENABLE_LINTERS → PASS-skip, dictionary or not.

    CPV must not invent a blocking gate the plugin's CI does not run."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["PYTHON_RUFF"])
    calls = _patch_cspell(monkeypatch, present=True, proc=_FakeProc(1))

    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_cspell(result, {"PYTHON_RUFF"})
    f = _finding(result)

    assert f.severity == "PASS"
    assert "not enabled" in f.message
    assert calls == []


def test_missing_cspell_binary_degrades_to_warning_never_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dictionary present but cspell NOT on PATH → non-blocking WARNING.

    An agent box without the cspell binary must NEVER be false-blocked; CI still
    enforces the linter."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    standardize_plugin.provision_cspell_config(root)
    _patch_cspell(monkeypatch, present=False)

    f = _finding(_gate(root))

    assert f.severity == "WARNING"


def test_no_megalinter_config_at_all_passes_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `.mega-linter.yml` (enabled=None) → PASS-skip. CI runs no cspell."""
    root = _make_plugin(tmp_path)
    _patch_cspell(monkeypatch, present=True, proc=_FakeProc(1))

    result = PreflightResult(plugin_path=root)
    cpv_ci_preflight._gate_cspell(result, None)

    assert _finding(result).severity == "PASS"


@pytest.mark.parametrize("config_name", [".cspell.json", "project-words.txt", "cspell.config.yaml"])
def test_any_recognized_config_form_unblocks_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_name: str
) -> None:
    """ANY cspell config form the author owns satisfies the gate — the probe runs.

    The defect is "no dictionary at all", never "not OUR dictionary": CPV must not
    force its own config on a plugin that already has one."""
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    (root / config_name).write_text("words: []\n", encoding="utf-8")
    calls = _patch_cspell(monkeypatch, present=True, proc=_FakeProc(0))

    f = _finding(_gate(root))

    assert f.severity == "PASS"
    assert calls, "an author-owned config must unblock the probe, not skip it"


# ===========================================================================
# THE EMITTED DICTIONARY — it must actually contain what CI flags
# ===========================================================================


def test_emitted_dictionary_carries_the_plugins_own_proper_nouns(tmp_path: Path) -> None:
    """The seeded words cover exactly what a generic dictionary cannot know: the
    plugin's name, its agents, its commands, its skills, and its author."""
    root = _make_plugin(tmp_path)
    standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))
    words = {w.lower() for w in cfg["words"]}

    for token in ("wiki", "plugin", "devitalizer", "emasoft", "redact", "harden", "menu"):
        assert token in words, f"{token!r} (a plugin proper noun) missing from the dictionary"


def test_emitted_dictionary_carries_the_tech_terms_that_would_false_block(
    tmp_path: Path,
) -> None:
    """The never-false-block half: the ordinary tech terms a bare cspell trips on
    are seeded, so turning the probe ON cannot flag words CI passes. Because CI
    reads this same file, it can never be stricter than local.

    Every token below was OBSERVED as an "Unknown word" from the real cspell run on
    an undictionaried plugin (the RC-3 CI failure, reproduced) — this is not a
    guessed list. `uvx` is deliberately NOT here: it is 3 chars and cspell's default
    `minWordLength` is 4, so it is never flagged (measured) and seeding it would be
    dead weight."""
    root = _make_plugin(tmp_path)
    standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))
    words = {w.lower() for w in cfg["words"]}

    for token in ("pyproject", "venv", "pipefail", "mypy", "pytest", "shellcheck", "toplevel", "endfor"):
        assert token in words, f"{token!r} would false-block a CI-green plugin"


def test_emitted_dictionary_does_not_whitelist_actual_misspellings(tmp_path: Path) -> None:
    """POSITIVE CONTROL for the dictionary itself: it seeds vocabulary, it does not
    blanket-accept typos. A genuine misspelling stays unknown → CI (and now the
    local probe) still catch it."""
    root = _make_plugin(tmp_path)
    standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))
    words = {w.lower() for w in cfg["words"]}

    for typo in ("teh", "recieve", "seperate", "existance", "occured"):
        assert typo not in words, f"the dictionary must not whitelist the typo {typo!r}"


def test_emitted_dictionary_is_valid_json_and_self_consistent(tmp_path: Path) -> None:
    """The emitted file must parse (cspell hard-errors on an unparseable config —
    which would CREATE the CI failure this provisioner exists to prevent) and must
    ignore the paths CI's Mega-Linter filters, so both sides see the same files."""
    root = _make_plugin(tmp_path)
    standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))

    assert cfg["version"] == "0.2"
    assert cfg["useGitignore"] is True, "CI only spell-checks tracked files"
    assert "**/*.json" in cfg["ignorePaths"], "CI's SPELL_CSPELL_FILTER_REGEX_EXCLUDE skips .json"
    assert "**/uv.lock" in cfg["ignorePaths"]
    assert cfg["words"] == sorted(cfg["words"]), "words must be sorted for a stable diff"
    assert len(cfg["words"]) == len(set(cfg["words"])), "no duplicate words"
    # No `dictionaries` key: naming a dict package the local install lacks would
    # emit a diagnostic CI does not have — the opposite of parity.
    assert "dictionaries" not in cfg


# ===========================================================================
# THE PROVISIONER — create / augment / NEVER clobber
# ===========================================================================


def test_provisioner_creates_the_dictionary_when_absent(tmp_path: Path) -> None:
    """No config at all → CREATE `.cspell.json`."""
    root = _make_plugin(tmp_path)
    assert not (root / ".cspell.json").exists()

    notes = standardize_plugin.provision_cspell_config(root)

    assert (root / ".cspell.json").is_file()
    assert any("created" in n for n in notes)


def test_provisioner_augments_without_clobbering_the_authors_config(tmp_path: Path) -> None:
    """An existing `.cspell.json` is AUGMENTED, never overwritten: the author's own
    words, their other keys, and their settings all survive verbatim."""
    root = _make_plugin(tmp_path)
    (root / ".cspell.json").write_text(
        json.dumps(
            {
                "version": "0.2",
                "language": "en",
                "words": ["zzzauthorword"],
                "ignoreRegExpList": ["/^\\s*#.*$/"],
                "allowCompoundWords": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    notes = standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))
    words = {w.lower() for w in cfg["words"]}

    assert any("augmented" in n for n in notes)
    # The author's content is untouched …
    assert "zzzauthorword" in words
    assert cfg["ignoreRegExpList"] == ["/^\\s*#.*$/"]
    assert cfg["allowCompoundWords"] is True
    # … and the plugin terms CI would flag are now covered.
    assert "devitalizer" in words
    assert "pyproject" in words


def test_provisioner_is_idempotent(tmp_path: Path) -> None:
    """A second run is a no-op: no notes, no byte changes. (A provisioner that
    re-appends on every --fix would grow the file without bound.)"""
    root = _make_plugin(tmp_path)
    standardize_plugin.provision_cspell_config(root)
    first = (root / ".cspell.json").read_text(encoding="utf-8")

    notes = standardize_plugin.provision_cspell_config(root)

    assert notes == []
    assert (root / ".cspell.json").read_text(encoding="utf-8") == first


def test_provisioner_adds_a_words_list_when_the_config_has_none(tmp_path: Path) -> None:
    """An existing `.cspell.json` with NO `words` key gains one; other keys survive."""
    root = _make_plugin(tmp_path)
    (root / ".cspell.json").write_text(
        json.dumps({"version": "0.2", "allowCompoundWords": True}, indent=2) + "\n",
        encoding="utf-8",
    )

    standardize_plugin.provision_cspell_config(root)
    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))

    assert cfg["allowCompoundWords"] is True
    assert "devitalizer" in {w.lower() for w in cfg["words"]}


@pytest.mark.parametrize("other", ["cspell.config.yaml", "project-words.txt", ".cspell.yml"])
def test_provisioner_never_touches_another_config_form(tmp_path: Path, other: str) -> None:
    """The author owns a cspell config in ANOTHER form → leave it completely alone
    and do NOT write a second, ambiguous `.cspell.json` beside it (cspell discovers
    exactly one config)."""
    root = _make_plugin(tmp_path)
    original = "words:\n  - authorsword\n"
    (root / other).write_text(original, encoding="utf-8")

    notes = standardize_plugin.provision_cspell_config(root)

    assert notes == []
    assert not (root / ".cspell.json").exists()
    assert (root / other).read_text(encoding="utf-8") == original


def test_provisioner_surfaces_but_never_mutates_an_unparseable_config(tmp_path: Path) -> None:
    """A `.cspell.json` we cannot parse is left byte-identical AND reported.

    Two-sided: rewriting it could corrupt it further (cspell would then hard-error
    on EVERY file — the exact CI failure this provisioner prevents), but staying
    SILENT would let the audit report PASS on a broken dictionary. So: never touch,
    always surface."""
    root = _make_plugin(tmp_path)
    broken = '{"words": ["a",,]}\n'
    (root / ".cspell.json").write_text(broken, encoding="utf-8")

    notes = standardize_plugin.provision_cspell_config(root)

    assert (root / ".cspell.json").read_text(encoding="utf-8") == broken, "must not be rewritten"
    assert any("not valid JSON" in n for n in notes), "a corrupt dictionary must not be silent"
    assert standardize_plugin.audit_cspell_config(root)[0].status == "WARN"


@pytest.mark.parametrize(
    "shape",
    [
        '{"words": ["alpha"]}\n',  # tight single-line array
        '{"words": []}\n',  # empty array
        '{\n  "words": [\n  ]\n}\n',  # empty, multi-line
        '{\n  "words": [\n    "alpha"\n  ]\n}\n',  # the canonical pretty shape
    ],
)
def test_augmented_config_stays_valid_json(tmp_path: Path, shape: str) -> None:
    """Regression: the augment path is a TEXT edit (to preserve the author's
    formatting), so it must not emit a trailing comma — invalid JSON would make
    cspell hard-error on every file, creating the CI failure we are preventing.
    Covers the tight single-line, empty, and pretty array shapes."""
    root = _make_plugin(tmp_path)
    (root / ".cspell.json").write_text(shape, encoding="utf-8")

    standardize_plugin.provision_cspell_config(root)

    cfg = json.loads((root / ".cspell.json").read_text(encoding="utf-8"))  # must not raise
    assert "devitalizer" in {w.lower() for w in cfg["words"]}


def test_provisioner_dry_run_never_mutates(tmp_path: Path) -> None:
    """The AUDIT path reports what --fix would do and writes nothing."""
    root = _make_plugin(tmp_path)

    notes = standardize_plugin.provision_cspell_config(root, dry_run=True)

    assert notes and "run --fix" in notes[0]
    assert not (root / ".cspell.json").exists()


def test_audit_reports_warn_then_pass(tmp_path: Path) -> None:
    """`audit_cspell_config` WARNs on the missing dictionary and PASSes once the
    provisioner has run — the audit text is sourced from the provisioner itself, so
    the two can never drift."""
    root = _make_plugin(tmp_path)

    before = standardize_plugin.audit_cspell_config(root)
    assert [i.status for i in before] == ["WARN"]

    standardize_plugin.provision_cspell_config(root)

    after = standardize_plugin.audit_cspell_config(root)
    assert [i.status for i in after] == ["PASS"]


def test_config_name_tuples_stay_in_sync() -> None:
    """The preflight's "does this plugin have a config?" gate and the tuple
    standardize provisions against MUST be the same list. A drift means standardize
    writes a second, ambiguous config beside one the probe already recognized."""
    assert tuple(standardize_plugin._CSPELL_CONFIG_NAMES) == tuple(
        cpv_ci_preflight._CSPELL_CONFIG_NAMES
    )


# ===========================================================================
# REAL cspell (no mock) — skipped when the binary is absent
# ===========================================================================


@pytest.mark.skipif(shutil.which("cspell") is None, reason="cspell binary not installed")
def test_real_cspell_two_sided_against_the_emitted_dictionary(tmp_path: Path) -> None:
    """The end-to-end proof with the REAL tool, both sides:

    * the emitted dictionary makes a plugin full of proper nouns + tech terms CLEAN
      (exit 0) — so turning the probe on cannot false-block a CI-green plugin;
    * a genuine misspelling in the same tree still exits NON-ZERO — so the
      dictionary is vocabulary, not a mute button.
    """
    root = _make_plugin(tmp_path)
    _write_mega_linter(root, ["SPELL_CSPELL"])
    (root / "README.md").write_text(
        "# wiki-mem-plugin\n\n"
        "Run `uvx` against the pyproject in a venv. The plugin-devitalizer agent\n"
        "reads the TRDD and the wikimem notes with `set -o pipefail`.\n",
        encoding="utf-8",
    )
    standardize_plugin.provision_cspell_config(root)

    argv = cpv_ci_preflight._argv_cspell(shutil.which("cspell") or "cspell", root)
    clean = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=120)
    assert clean.returncode == 0, f"the emitted dictionary false-blocks:\n{clean.stdout}"

    (root / "README.md").write_text(
        "# wiki-mem-plugin\n\nThis sentence has a mispeled wrod in it.\n", encoding="utf-8"
    )
    dirty = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=120)
    assert dirty.returncode != 0, "a real misspelling must still fail cspell"
