"""CLAUDE.md's inventory table declares itself authoritative — pin the numbers that are.

WHY THIS EXISTS. CLAUDE.md opens by calling its inventory load-bearing and telling
every agent to keep it current. Nothing enforced that, and the version row drifted
TWICE before anyone noticed: it read `5.16.2` against an actual `5.17.0` (the
v5.17.0 publish updated `plugin.json` and not the document that claims to be the
reference), and the test-file row read `506` against an actual `516`.

A stale number in a document that asserts its own authority is worse than no
document: the next reader trusts it precisely because of that assertion, and the
error propagates into every count derived from it. `publish.py`'s
`check_version_consistency` compares `pyproject.toml` against `plugin.json` and
has never looked at CLAUDE.md, which is exactly how the drift survived a release.

The version row is pinned EXACTLY because it has a single machine-readable source
of truth. The test-file row is pinned with a TOLERANCE rather than exactly — see
`test_claude_md_test_file_count_is_not_wildly_stale` for why an exact pin there
would be a maintenance trap rather than a guard.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"

# The row is `| **version** | `5.18.0` | ... |` — the backticked cell is the claim.
_VERSION_ROW_RE = re.compile(r"^\|\s*\*\*version\*\*\s*\|\s*`([^`]+)`\s*\|", re.MULTILINE)
_TEST_FILES_ROW_RE = re.compile(r"^\|\s*\*\*test files\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", re.MULTILINE)


def _claude_md() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def _declared_version() -> str:
    m = _VERSION_ROW_RE.search(_claude_md())
    # An anchor that stops matching would make every assertion below pass
    # vacuously, which is the failure mode this whole file guards against.
    assert m is not None, "the CLAUDE.md version row no longer matches its anchor — fix the regex, do not delete the test"
    return m.group(1)


def _declared_test_file_count() -> int:
    m = _TEST_FILES_ROW_RE.search(_claude_md())
    assert m is not None, "the CLAUDE.md test-files row no longer matches its anchor — fix the regex, do not delete the test"
    return int(m.group(1))


def _actual_version() -> str:
    return str(json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"])


def _actual_test_file_count() -> int:
    """Counted EXACTLY as the row's own documented command counts.

    That row documents a `pathlib` one-liner, not a shell glob, so this is a bare
    `glob` on purpose. Do NOT add the `is_file()` / dotfile filtering `_count_entries`
    applies: those exist to reproduce SHELL semantics for the `ls`-documented rows,
    and adding them here would make the guard disagree with the instruction the row
    hands a human — the exact divergence this file exists to prevent. If the counting
    should change, change the documented command and this function together.
    """
    return len(list((REPO_ROOT / "tests").glob("test_*.py")))


def test_claude_md_version_row_matches_the_manifest() -> None:
    """The one number with a single source of truth, so it is pinned exactly."""
    # Both this anchor and `publish.py`'s bumper take the FIRST match, so a second
    # version row would leave one of them silently un-maintained. Pinning uniqueness
    # is cheaper than discovering which one lost: dropping a uniqueness guard is the
    # edit this repo already recorded as "the edit that bites" (v5.12.0).
    # Read the row FIRST. `_declared_version` carries the "fix the regex, do not
    # delete the test" message, and putting the count check above it would let the
    # count assert grab the ZERO case — failing with a message about a duplicate row
    # in the one scenario where the anchor guidance is what the reader needs.
    declared = _declared_version()
    rows = len(_VERSION_ROW_RE.findall(_claude_md()))
    assert rows == 1, (
        f"expected exactly one inventory version row in CLAUDE.md, found {rows}. "
        "A second REAL row means publish.py bumps only the first and the rest drift "
        "unwatched. An EXAMPLE row in a fenced block counts too — this matches "
        "anywhere in the file, so indent it or drop its leading pipe."
    )
    # The bumper carries its OWN regex, in another file. Nothing else fails when the
    # two stop agreeing, and the failure is silent in the direction that matters: a
    # reformatted row the test regex still matches but the bumper's does not makes
    # `update_claude_md_version_row` report a (deliberately non-fatal) SKIP, and the
    # row quietly stops being maintained — the exact drift this file exists to end,
    # restored through the back door.
    # Reaching for a private name is correct-by-necessity here: a copy of the pattern
    # would recreate the drift this assertion exists to catch. `getattr` so a rename
    # fails with the pairing named rather than a bare AttributeError.
    bumper_re = getattr(publish, "_CLAUDE_MD_VERSION_ROW_RE", None)
    assert bumper_re is not None, (
        "publish.py no longer exposes _CLAUDE_MD_VERSION_ROW_RE — this pin reads it "
        "so the bumper and the guard cannot drift apart; re-point it, do not delete it"
    )
    bumper = bumper_re.search(_claude_md())
    assert bumper is not None, (
        "publish.py's version-row regex no longer matches CLAUDE.md, so the bump "
        "would silently skip the row — fix the regex, do not delete this assertion"
    )
    assert bumper.group(2) == declared, (
        f"the two version-row regexes disagree: the pin reads {declared!r}, "
        f"publish.py's bumper reads {bumper.group(2)!r}"
    )
    actual = _actual_version()
    assert declared == actual, (
        f"CLAUDE.md's inventory claims version {declared!r} but "
        f".claude-plugin/plugin.json says {actual!r}. CLAUDE.md declares itself "
        "authoritative, so a reader will believe the wrong one. Update the row in "
        "the same change that bumps the manifest."
    )


# Drift allowed in EITHER direction between a declared count and the tree: ordinary
# lag between "someone added a component" and "someone refreshed the row", and the
# mirror case where a deletion leaves the row briefly ahead. Deliberately small —
# the only drift this repo has actually observed was ~10, so a window at or above
# that would not have caught the defect this file exists for.
_COUNT_DRIFT_ALLOWANCE = 3

class _Counted(NamedTuple):
    """How one inventory row's number is derived from the tree."""

    directory: str
    glob: str
    want_dir: bool


# The other rows the header calls load-bearing. Each is `| **<label>** | **<n>** |`.
# Counted the way the row's own documented command counts, so the guard and the
# instruction to a human cannot disagree — which is why this is not a bare
# `Path.glob`. A shell `*` does not match a dotfile and `ls -d skills/*/` yields
# DIRECTORIES only; `Path.glob` does neither. So a dot-entry, or a loose FILE in a
# directory row, is counted by the guard and not by the command the row hands a
# human: the guard fails a row that is RIGHT, and its message sends them to
# re-derive a number that already matches. The case that motivated this was
# `skills/.DS_Store` (untracked, gitignored, never ships), which made a bare glob
# count 53 against a correct 52 — but that file is one `rm` away from gone, so the
# rule is stated generally here and the fixture test plants its own dot-entry.
_COUNTED_ROWS: dict[str, _Counted] = {
    "commands": _Counted("commands", "*.md", want_dir=False),
    "agents": _Counted("agents", "*.md", want_dir=False),
    "skills": _Counted("skills", "*", want_dir=True),
    "scripts": _Counted("scripts", "*.py", want_dir=False),
}


def _count_entries(counted: _Counted, root: Path = REPO_ROOT) -> int:
    """Count a row's entries the way its documented shell command counts them."""
    base = root / counted.directory
    return sum(
        1
        for p in base.glob(counted.glob)
        if not p.name.startswith(".") and (p.is_dir() if counted.want_dir else p.is_file())
    )


def test_claude_md_test_file_count_is_not_stale() -> None:
    """ASYMMETRIC on purpose: stale-downward is lag, ahead-of-actual is fabrication.

    An exact pin would be a trap — every commit adding a test would fail, so the row
    would be edited reflexively to whatever number turns the suite green, which is
    the incrementing-without-re-deriving habit that let `506` survive a release. A
    guard that trains the behaviour it exists to prevent is worse than none.

    But a SYMMETRIC `abs(declared - actual) <= N` was the wrong correction, and it
    was the first shape shipped here. It tolerates the row running AHEAD of the
    tree, a state with no legitimate cause: you cannot lag into a number larger than
    reality, you can only assert one you did not measure. That is exactly the error
    this file was written after — a `5.18.0` version row committed before the bump
    existed, and a back-calculated `516` written into the row being repaired for
    containing unverified numbers.

    A HARD `declared <= actual` edge was the second wrong shape here, and it is worth
    naming because it looked like the fix. It fails the moment someone DELETES a test
    file: the row then leads the tree through no fault of the author, the suite goes
    red, and the message accuses them of fabricating a number they never touched. The
    cheapest way back to green is to edit the row to whatever passes — the exact
    reflexive edit this guard exists to prevent. It also got the costs backwards,
    making the benign direction (deletion) loud and the other benign direction (lag)
    silent.

    So the allowance is SYMMETRIC, with the two directions given DIFFERENT messages,
    because they mean different things and want different fixes. What is given up is
    catching a fabrication of 1-3 — the region where churn is likeliest and invention
    least likely. The two real incidents behind this file were both large (a version
    row committed before its bump existed; a `506` that was stale by ~10), and both
    are still caught.
    """
    declared = _declared_test_file_count()
    actual = _actual_test_file_count()
    drift = declared - actual
    if drift > _COUNT_DRIFT_ALLOWANCE:
        raise AssertionError(
            f"CLAUDE.md claims {declared} test files but the tree has {actual} — the "
            f"row LEADS the tree by {drift}. If test files were deleted, re-derive the "
            "row from the tree. If they were not, this is a number nobody measured."
        )
    if -drift > _COUNT_DRIFT_ALLOWANCE:
        raise AssertionError(
            f"CLAUDE.md claims {declared} test files, the tree has {actual} "
            f"(stale by {-drift}). RE-DERIVE it with the command in that row — do "
            "not increment the previous entry, which is how the stale 506 survived "
            "a release."
        )


def test_the_other_load_bearing_rows_track_the_tree() -> None:
    """commands / agents / skills / scripts — unpinned until now, and the header overclaimed.

    CLAUDE.md's header justifies calling these counts load-bearing by asserting that
    "README, the menu doc, and `test_*_preflight` tests assert against reality".
    Measured: `test_consolidation_v211.py` pins the command SET (an allowlist of
    filenames, which is stronger than a count) but NOTHING compared any of these rows
    to the tree. The claim was partly false — and an unpinned row whose document
    asserts it is pinned is exactly the condition under which the version row drifted
    twice unnoticed.

    All four read correctly when this was written, so this pins a currently-true
    state rather than repairing a defect.

    `skills` was left out of the first version of this table, and the reason given —
    that its row counts DIRECTORIES rather than a glob of files, so folding it in
    would "silently count the wrong thing" — described a real hazard and drew the
    wrong conclusion from it. The row was then simply UNGUARDED, which is the state
    this file was written to end, and 52 is the number quoted in agent prompts and
    the menu doc, so it is the highest-traffic one on the table. The hazard is real
    and is handled where it belongs, in `_count_entries`, by counting the way each
    row's own documented command counts.
    """
    text = _claude_md()
    for label, counted in _COUNTED_ROWS.items():
        row = re.search(rf"^\|\s*\*\*{re.escape(label)}\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|", text, re.MULTILINE)
        assert row is not None, f"the CLAUDE.md {label!r} row no longer matches its anchor — fix the regex, do not delete the test"
        declared = int(row.group(1))
        actual = _count_entries(counted)
        drift = declared - actual
        assert abs(drift) <= _COUNT_DRIFT_ALLOWANCE, (
            f"CLAUDE.md claims {declared} {label}, the tree has {actual} "
            f"({'row leads by ' + str(drift) if drift > 0 else 'stale by ' + str(-drift)}). "
            "RE-DERIVE it from the tree with the command in that row."
        )


def test_the_anchors_actually_match_something() -> None:
    """A guard for the guards: a regex that matches nothing passes every test above."""
    text = _claude_md()
    assert _VERSION_ROW_RE.search(text) is not None
    assert _TEST_FILES_ROW_RE.search(text) is not None


# ── The other half of the invariant: who KEEPS the version row correct ─────────
#
# The pin above asserts the row equals plugin.json. Nothing made it TRUE — the row
# was updated by hand after a release, so the publish that bumped plugin.json left
# the row one version behind and the very next publish's test gate would reject a
# clean tree. `publish.py::update_claude_md_version_row` closes that loop by bumping
# the row in the same stage as the manifest; these tests live here, beside the
# assertion they satisfy, because separating them is how the pairing gets lost.

# COST, accepted deliberately: this file's other tests are cheap dependency-free doc
# pins, and importing publish.py means a syntax error there fails COLLECTION of all
# of them, reporting the inventory tests as broken when publish.py is what broke.
# The alternative — copying the bumper's regex here — recreates the exact drift the
# cross-check below exists to catch, which is the worse trade.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import publish  # noqa: E402


def test_publish_bump_rewrites_the_version_row(tmp_path: Path) -> None:
    """The bump moves the row, and the result still matches this file's own anchor."""
    md = tmp_path / "CLAUDE.md"
    md.write_text(
        "| Thing | Count | Where |\n| **version** | `1.2.3` | manifest |\n| **agents** | **4** | x |\n",
        encoding="utf-8",
    )
    ok, msg = publish.update_claude_md_version_row(tmp_path, "1.2.4")
    assert ok and "1.2.3 → 1.2.4" in msg
    after = md.read_text(encoding="utf-8")
    row = _VERSION_ROW_RE.search(after)
    assert row is not None and row.group(1) == "1.2.4"
    assert "| **agents** | **4** | x |" in after, "the bump must touch only the version row"


def test_publish_bump_is_a_non_fatal_skip_when_there_is_no_row(tmp_path: Path) -> None:
    """A plugin without a CLAUDE.md inventory must still bump — and nothing is invented."""
    ok, msg = publish.update_claude_md_version_row(tmp_path, "9.9.9")
    assert ok and "absent" in msg
    assert not (tmp_path / "CLAUDE.md").exists(), "a missing CLAUDE.md must not be created"

    md = tmp_path / "CLAUDE.md"
    original = "# Notes\n\nNo inventory table here.\n"
    md.write_text(original, encoding="utf-8")
    ok, msg = publish.update_claude_md_version_row(tmp_path, "9.9.9")
    assert ok and "no version row" in msg
    assert md.read_text(encoding="utf-8") == original, "an unrecognised file must be left byte-identical"


def test_publish_bump_is_idempotent(tmp_path: Path) -> None:
    """Re-running at the same version reports it and rewrites nothing."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("| **version** | `2.0.0` | manifest |\n", encoding="utf-8")
    before = md.read_text(encoding="utf-8")
    ok, msg = publish.update_claude_md_version_row(tmp_path, "2.0.0")
    assert ok and "already 2.0.0" in msg
    assert md.read_text(encoding="utf-8") == before


def test_do_bump_actually_moves_the_row(tmp_path: Path) -> None:
    """A helper nothing calls is the drift it was written to stop, with extra steps.

    Deliberately BEHAVIOURAL rather than a source-text check for the call. A
    `"update_claude_md_version_row(...)" in source` assertion cannot tell a call from
    a mention, so it stays green when someone comments the call out during a debug
    session — the exact vacuous-source-pin shape this repo has shipped three times
    (v5.14.1, v5.5.0, v4.3.0). It would also go red on a correct refactor that
    dispatches the updaters through a tuple. Driving `do_bump` proves the wiring
    the only way that cannot be faked: the row moves, or it does not.

    Running the REAL `do_bump` also runs `update_python_versions`, which rewrites
    every `__version__` under the root it is given — so the safety of this test rests
    on that root staying `tmp_path`. Checked rather than assumed: `publish._get_gi`
    caches `GitignoreFilter` in `_gi_cache` KEYED BY RESOLVED PATH, not as a
    module-global singleton, so a caller that already built a filter for the real
    checkout cannot hand this one a repo-rooted walker. Had it been a singleton, a
    worker that touched the real root first would have made this test rewrite
    `__version__` across `scripts/` mid-suite, and `git add -u` would have staged it
    into the release commit.
    """
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "probe", "version": "1.2.3"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text(
        "| Thing | Count |\n| **version** | `1.2.3` | manifest |\n", encoding="utf-8"
    )

    assert publish.do_bump(tmp_path, "1.2.4") is True
    after = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    row = _VERSION_ROW_RE.search(after)
    assert row is not None and row.group(1) == "1.2.4", (
        "do_bump moved the manifest but not the CLAUDE.md row — that is the drift "
        "that leaves the row a release behind and makes the NEXT publish's test gate "
        "reject a clean tree"
    )
    # Control: the bump it is paired with really did happen, so a do_bump that
    # silently did nothing at all cannot satisfy the assertion above.
    assert json.loads((tmp_path / ".claude-plugin" / "plugin.json").read_text())["version"] == "1.2.4"


def test_counted_rows_covers_every_row_the_header_calls_load_bearing() -> None:
    """A row absent from this table is UNGUARDED — which is how `skills` drifted unwatched."""
    assert set(_COUNTED_ROWS) == {"commands", "agents", "skills", "scripts"}, (
        f"_COUNTED_ROWS covers {sorted(_COUNTED_ROWS)}; a row missing from it is a row "
        "nothing compares to the tree, which is how `skills` drifted unwatched"
    )


def test_count_entries_counts_files_the_way_a_shell_glob_does(tmp_path: Path) -> None:
    """A file row must skip dotfiles and directories, because `ls commands/*.md` does."""
    d = tmp_path / "commands"
    (d / "sub.md").mkdir(parents=True)  # a DIRECTORY whose name matches the glob
    (d / "one.md").touch()
    (d / ".hidden.md").touch()
    (d / "notes.txt").touch()
    row = _Counted("commands", "*.md", want_dir=False)
    assert _count_entries(row, root=tmp_path) == 1
    # Positive control: the counter is not simply returning a constant.
    (d / "two.md").touch()
    assert _count_entries(row, root=tmp_path) == 2


def test_count_entries_counts_directories_the_way_ls_d_does(tmp_path: Path) -> None:
    """A directory row must skip loose files and dot-entries, because `ls -d skills/*/` does."""
    s = tmp_path / "skills"
    (s / "alpha").mkdir(parents=True)
    (s / "beta").mkdir()
    (s / ".DS_Store").touch()
    (s / "loose.md").touch()
    row = _Counted("skills", "*", want_dir=True)
    assert _count_entries(row, root=tmp_path) == 2
    (s / "gamma").mkdir()
    assert _count_entries(row, root=tmp_path) == 3


def test_a_bare_glob_would_overcount_a_directory_row(tmp_path: Path) -> None:
    """Mutation proof for the filtering, stated so it holds on EVERY machine.

    An earlier version of this test asserted the divergence against the real tree —
    `skills/.DS_Store` exists here, so a bare `Path.glob("*")` counts 53 against a
    correct row of 52. That reproduced the real motivating case and was the wrong
    test to gate on TWICE over: a fresh CI checkout has no `.DS_Store`, so the
    contrast silently evaporates and the assertion passes having compared nothing;
    and pinning that row EXACTLY contradicted `_COUNT_DRIFT_ALLOWANCE` three
    functions above, whose whole argument is that an exact count pin trains the
    reflexive row edit it exists to prevent.

    A fixture that always contains the dot-entry proves the same thing everywhere,
    and proves it about the counting rather than about one machine's filesystem.
    """
    s = tmp_path / "skills"
    (s / "alpha").mkdir(parents=True)
    (s / "beta").mkdir()
    (s / ".DS_Store").touch()
    bare = len(list(s.glob("*")))
    counted = _count_entries(_Counted("skills", "*", want_dir=True), root=tmp_path)
    assert bare == 3, "precondition: the bare glob must actually see the dot-entry"
    assert counted == 2, "the filtering is what a `ls -d skills/*/` count would report"
    assert bare > counted, "a bare glob over-counts here; it must never under-count"
