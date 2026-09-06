"""Issue #186 — no shipped recipe may teach a bare `git add .` / `-A`.

The rule is not about CPV's own tree: these files are recipes that agents COPY
into other people's repositories, so a bare add here becomes a bare add in a
commit somebody else pushes. A marketplace or plugin tree at commit time
routinely holds `reports/` (private paths, usernames, tokens caught in logs)
and other scratch, and a pushed commit is not recoverable in practice.

`git add -u` is the replacement almost everywhere, and the tempting objection —
"but this job updates submodules, so it needs a broad add" — is a false binary,
measured on a real submodule fixture: after `git submodule update --remote
--merge`, `-u` stages the gitlink AND leaves an untracked file untracked.

Exemptions exist, for two DIFFERENT reasons, and the difference matters when the
next one is proposed:

1. A recipe that CREATES new untracked files into a tree holding nothing else, so
   `-u` would stage nothing at all (layout-a-migration: a fresh /tmp clone).
2. A recipe turning a directory INTO its own repo, where that directory IS the
   plugin, so naming paths fails in both directions — a named path the plugin
   lacks aborts the whole add, and any component not named is silently dropped
   from a repo that is pushed one line later (marketplace-validation, ×2).

An entry is keyed BY PATH, which alone would be a blanket pardon for the file, so
each carries an expected COUNT: a new bare add in an allowlisted file fails until
someone justifies it AND bumps that count — a comment alone leaves it red, which is
the point. Each site must also carry its reason in the comment block attached to it;
a file-wide substring check would let one recipe lose its comment while a sibling's
copy kept the file green.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCAN_DIRS = ("skills", "templates", "references")
# `.py` is here because the shipped SCRIPT templates live under these dirs
# (templates/scripts/*.py, skills/.../references/pre-push-hook.py) and are exactly
# the files most likely to grow a `git add`. The first cut listed `.sh`/`.yaml`,
# which match ZERO files in this tree — written from expectation, not measured.
# They are kept as cheap future-proofing, but `.py` is the one that covers real files.
SCAN_SUFFIXES = (".md", ".yml", ".yaml", ".sh", ".bash", ".py")

# A shell COMMAND, not a prose mention. The command must begin the line or follow a
# shell separator (`&&`, `||`, `;`) — a prose mention is preceded by a backtick, which
# is neither, so `Never run `git add -A`` does not match.
#
# The first cut anchored at `^` and accepted only a bare `.`/`-A`/`--all`. That misses
# four shapes that are all real: `git add "."`, `git add ./`, `git add -A .`, and the
# chained `git init && git add . && git commit` — and CPV already PRINTS that last one
# (generate_plugin_repo.py, outside this scan's scope). None of them is present in the
# scanned tree today, so widening changes no current verdict; it is about the edit six
# months from now, which is the only thing a regression guard is for.
BARE_ADD_RE = re.compile(
    r"(?:^|&&|\|\||;)[ \t]*git[ \t]+add[ \t]+"  # the command, at a real command start
    r"""(?:-A|--all|["']?\.["']?/?)"""  # a bare target: . "." '.' ./ -A --all
    r"(?:[ \t]|$|&|;)"  # ...and nothing else glued to it
)

# path -> (justification substring, how many bare adds that file is allowed)
#
# The COUNT is what keeps a file-level allowlist from becoming a blanket pardon: a
# THIRD bare add appended to an allowlisted file would otherwise inherit the
# exemption silently. The justification must appear once PER allowed site, so one
# recipe cannot lose its reason while a sibling's comment keeps the file green.
ALLOWED: dict[str, tuple[str, int]] = {
    "skills/cpv-migrate-marketplace-architecture/references/layout-a-migration.md": (
        "`git add -u` would stage nothing",
        1,
    ),
    # Two `git init` recipes turning a plugin directory into its own repo. Naming
    # paths was tried and is worse in BOTH directions: a named path the plugin
    # lacks aborts the whole add (exit 128, stages nothing — measured), while any
    # component not named (`hooks/`, `scripts/`, `bin/`, `.mcp.json`, `LICENSE`)
    # is silently omitted from a repo `gh repo create --push` publishes one line
    # later. An under-commit fails silently; that is strictly worse than the
    # untracked sweep #186 forbids. The `git status --short` review step directly
    # above each add is the guard, which is the sanctioned way to satisfy the rule.
    "skills/cpv-plugin-validation-skill/references/marketplace-validation.md": (
        "documented exception to this project's stage-by-name rule",
        2,
    ),
}


def _scan_files() -> list[Path]:
    files: list[Path] = []
    for d in SCAN_DIRS:
        root = REPO_ROOT / d
        if not root.is_dir():
            continue
        files.extend(p for p in root.rglob("*") if p.is_file() and p.suffix in SCAN_SUFFIXES)
    return files


def _hits() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in _scan_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BARE_ADD_RE.search(line):
                out.append((rel, lineno, line.strip()))
    return out


def test_the_scan_is_not_vacuous() -> None:
    """A scan that reads nothing would pass every assertion below."""
    files = _scan_files()
    assert len(files) > 50, f"only {len(files)} files scanned — the scan scope broke"


def test_the_detector_can_fail() -> None:
    """A regex that matches nothing would make the whole file decorative.

    The MUST-MATCH half below is the guard's real coverage claim; the MUST-NOT half
    is what stops it from reddening correct writing, which is how a guard gets
    deleted. Both halves are required — an assertion set with only one is vacuous
    in the direction it omits.
    """
    must_match = [
        "git add .",
        "          git add -A",
        "git add . # with a trailing comment",
        "git add --all",
        # The four shapes the first, line-anchored version silently missed:
        "git init && git add . && git commit -m x",
        "cd repo; git add .",
        'git add "."',
        "git add ./",
        "git add -A .",
    ]
    for line in must_match:
        assert BARE_ADD_RE.search(line), f"detector misses a bare add: {line!r}"

    must_not_match = [
        "git add -u",
        "git add .claude-plugin README.md",
        "git add .gitmodules",
        "git add .claude-plugin/marketplace.json README.md",
        # Prose mentions: the command is preceded by a backtick, which is neither a
        # line start nor a shell separator, so the prohibition text does not self-flag.
        "- **Never run `git add -A`** in a directory",
        "Never use `git add -A` or `git add .` — stage specific files by name",
    ]
    for line in must_not_match:
        assert not BARE_ADD_RE.search(line), f"detector false-positives on: {line!r}"


def test_no_unallowlisted_bare_git_add_in_shipped_recipes() -> None:
    offenders = [(rel, lineno, line) for rel, lineno, line in _hits() if rel not in ALLOWED]
    assert not offenders, (
        "shipped recipe teaches a bare git add (issue #186) — use `git add -u`, or name "
        f"the paths: {offenders}"
    )


def _attached_comment(lines: list[str], lineno: int) -> str:
    """The contiguous comment block immediately above line `lineno` (1-based).

    A fixed line window was tried first and had two defects a constant cannot fix.
    It is brittle: these comment blocks grew three times in one session, and one more
    expansion past the window turns an innocent doc edit red while the failure message
    points at the recipe rather than at the constant. And it is unsound at scale: the
    window is the radius in which a justification belonging to a DIFFERENT recipe can
    satisfy a site that has none of its own — measured in this very file, a window of
    62 would let the second exempt site pass on the first one's comment, restoring the
    file-wide-substring hole the per-site check was written to close.

    Walking the block the author actually attached to the statement has neither
    failure mode: it cannot be outgrown, and it cannot reach past a blank-line gap
    into another recipe.
    """
    i = lineno - 2  # 0-based index of the line directly above the hit
    while i >= 0 and lines[i].lstrip().startswith("#"):
        i -= 1
    return "\n".join(lines[i + 1 : lineno - 1])


def test_every_allowlisted_site_still_states_why() -> None:
    """An exemption that loses its reason is indistinguishable from an oversight.

    The reason is bound to its OWN site, not counted file-wide. Counting was the
    first cut and it is defeatable: site A keeps its comment, site B loses its, and
    any prose paragraph elsewhere in a 700-line reference doc restores the tally —
    green, with an unjustified exemption. Proximity cannot be satisfied by a copy
    somewhere else in the file.
    """
    for rel in ALLOWED:
        assert (REPO_ROOT / rel).is_file(), f"allowlisted path gone: {rel} — drop the entry"

    checked = 0
    for rel, lineno, _ in _hits():
        if rel not in ALLOWED:
            continue  # handled by the offender test; not this one's job
        needle = ALLOWED[rel][0]
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
        assert needle in _attached_comment(lines, lineno), (
            f"{rel}:{lineno} is an allowlisted bare `git add .` but its own attached "
            f"comment block states no reason (looking for {needle!r}) — re-justify "
            "this site or fix the recipe. NOTE the reason must be in the comment "
            "block IMMEDIATELY above, with no blank line and not in markdown prose "
            "above the fence: a blank line ends the block and the reason is not seen"
        )
        checked += 1

    # Without this, a change that stopped producing hits would pass vacuously.
    expected = sum(count for _, count in ALLOWED.values())
    assert checked == expected, f"checked {checked} allowlisted sites, expected {expected}"


def test_a_sites_reason_cannot_be_borrowed_from_a_neighbour() -> None:
    """One site's justification must never satisfy a DIFFERENT site.

    This is the hazard that killed the fixed-window version: a large enough radius
    lets a site with no reason of its own pass on a comment belonging to a recipe
    higher up the file — the file-wide-substring hole, restored at a distance. The
    contiguous-block walk is supposed to make it impossible rather than merely
    unlikely, so that is asserted directly instead of trusted: neutralise every hit's
    own attached comment and the check must fail for that hit, no matter what any
    other site says.

    The neutralisation REPLACES each comment line with a placeholder rather than
    BLANKING it, and that distinction is the whole test. Blanking makes the walk stop
    at the first non-comment line, so `_attached_comment` returns "" and the assertion
    passes for a reason that has nothing to do with borrowing — measured: blanking
    yields an empty block, replacing keeps all 10 lines. The placeholder form keeps
    the block the same SIZE and only removes the needle, so the only way the needle
    can still be found is by reaching outside the block, which is the hazard.

    A size assertion (`len(block) == neutralised`) was tried here and REMOVED. It
    looked like a second net and was a tautology: it re-ran the same upward walk over
    the same predicate on lines this test had just forced to satisfy it, so it held by
    construction — and because any widened walk returns MORE lines, it fired FIRST and
    left the needle assertion, the one the test is named for, unreachable. The
    mutation I ran tripped the size check and I reported that as validating the pair.
    It also fires on the benign both-ends trim below. Dropping it makes the needle
    assertion live: an unbounded walk reaches the neighbour's real comment, finds the
    needle, and fails with a message about borrowing rather than about block sizes.

    WHY NOT REPLACE IT — read this before adding a size check back "for completeness".
    ONE property is claimed: **a walk that reaches far enough outside its block to touch
    ANOTHER justified site fails this test**, finding that site's needle where this
    one's was neutralised. The bound is "reaches a neighbour", not "reaches outside":
    tens of lines of fence and prose separate the two sites in
    `marketplace-validation.md`, so a near overshoot changes nothing. Every earlier
    draft over-quantified this, and exact line numbers are deliberately NOT cited —
    nothing here reads them, so they would decay silently the first time either recipe
    moves.

    It is witnessed by hand-mutating `_attached_comment` to return the whole file — not
    retained as a test — and only via `marketplace-validation.md`; `layout-a-migration.md`
    holds its needle once, so there the needle assertion cannot fail and only
    `assert neutralised` can, the reason itself being checked by
    `test_every_allowlisted_site_still_states_why`. A both-ends trim
    (`lines[i+2 : lineno-2]`) was measured and passes.
    """
    exempt = [(rel, ln) for rel, ln, _ in _hits() if rel in ALLOWED]
    assert exempt, "no allowlisted sites — this test would pass vacuously"

    for rel, lineno in exempt:
        needle = ALLOWED[rel][0]
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
        # Keep THIS site's comment block intact in shape, minus its reason; every
        # other site keeps its own reason untouched.
        i = lineno - 2
        neutralised = 0
        while i >= 0 and lines[i].lstrip().startswith("#"):
            # Keep the original indentation: nothing reads it today, but a walk that
            # later becomes indentation-sensitive would silently see a different block
            # and this test would quietly start measuring something else.
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = indent + "# placeholder"
            neutralised += 1
            i -= 1
        # Independent of the walk UNDER TEST: it counts this test's own loop and never
        # calls `_attached_comment`, so a defective walk can neither satisfy nor break
        # it — unlike the size assertion that was removed, which compared two
        # derivations of the same run. Its job is anti-vacuity for the assertion below:
        # neutralising nothing leaves an empty block, which `needle not in ""` passes
        # for free.
        assert neutralised, f"{rel}:{lineno} has no attached comment block to neutralise"
        block = _attached_comment(lines, lineno)
        assert needle not in block, (
            f"{rel}:{lineno} still finds its justification after its OWN comment block "
            "was neutralised — it is borrowing another site's reason, which is exactly "
            "the hole the per-site check exists to close"
        )


def test_allowlisted_sites_are_actually_used_and_have_not_grown() -> None:
    """A stale entry re-opens the hole; an UNCOUNTED one lets a new site inherit it.

    Allowlisting by path alone is a blanket pardon for the whole file: a third bare
    add appended tomorrow would be exempt and nothing would say so. Pinning the count
    means a new site fails until someone justifies it explicitly.
    """
    counts: dict[str, int] = {}
    for rel, _, _ in _hits():
        counts[rel] = counts.get(rel, 0) + 1

    # ORDERING IS LOAD-BEARING: the stale check must run BEFORE the `counts[rel]`
    # lookups below. An allowlisted path with zero hits is absent from `counts`, so
    # reordering these two blocks turns a named, explained assertion failure into a
    # bare KeyError.
    stale = sorted(set(ALLOWED) - set(counts))
    assert not stale, f"allowlist entries with no bare git add left — remove them: {stale}"

    grown = {
        rel: (counts[rel], expected)
        for rel, (_, expected) in ALLOWED.items()
        if counts[rel] != expected
    }
    assert not grown, (
        "allowlisted file changed its number of bare `git add` sites "
        f"(found, expected): {grown} — justify the new site and bump the count, or fix it"
    )
