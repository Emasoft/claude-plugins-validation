#!/usr/bin/env python3
"""Security-scan ONE agent together with its reachable skill closure (spec §4).

The gap: ``validate_security.py`` targets a PLUGIN (a directory, a GitHub URL, an
archive) or a whole marketplace. There was no way to scan a single agent — and an
agent's real attack surface is NOT its own file. A reachable skill's body enters
the agent's context as INSTRUCTIONS, so every skill the agent can load is part of
what that agent will actually do. Scanning the agent file alone answers a question
nobody asked.

Scan set = the agent ``.md`` PLUS ``closure_files()``: each REACHABLE skill's
``SKILL.md``, its ``references/**`` and its ``scripts/**``.

Three correctness points, each of which would be a real defect if got wrong:

1. **SSOT, never a copy.** The rules, the patterns, the severity mapping, the
   suppression chain and the report contract are all imported. Nothing here
   re-derives a rule or a severity. A second copy of a security grammar drifts,
   and a drifted copy is a FALSE NEGATIVE. The one seam that had to be added is
   a file-set-scoped WRAPPER over the existing engine
   (``cpv_skillaudit_native.run_skillaudit_scan_files``) — one engine, two entry
   points, byte-identical per-file behaviour.

2. **Suppression parity.** ``_iter_scannable_files`` is what applies the
   suppression chain (always-skip dirs, the self-artifact hash skip, the size
   cap, and the gitignored-AND-untracked shipped-surface check). A
   caller-supplied file list bypasses that walker, so ``scan_files`` routes the
   list back through the SAME predicate. Without that, ``agent-security`` would
   report findings the plugin scan correctly skips — a false positive created
   purely by which entry point you used.

3. **Closure files can live OUTSIDE the plugin root.** A reachable skill may be
   user-scope (``~/.claude/skills/<name>/SKILL.md``), and both the suppression
   chain and the reported paths are ROOT-RELATIVE. So the scan set is GROUPED by
   originating root and each group is scanned against ITS OWN root. Passing an
   out-of-root file with the plugin's root would silently disable the
   shipped-surface tier for it and print an unrelatable path.

**"Cannot reach" is not "clean."** A closure skill that is UNREACHABLE (the
``Skill`` gate is shut per spec §1 — ``Skill`` in ``disallowedTools``, or a
``tools:`` list that omits it) cannot execute, so its findings must NOT gate a
publish. They are still REPORTED, in a separate ``unreachable`` section, demoted
to WARNING (the only tier that never blocks, even under ``--strict``) with the
original severity preserved in the message text. Silently dropping them would
hide content that ships in the plugin and goes live the instant the gate opens.

CPV is UNIVERSAL: nothing here depends on an install slug, a marketplace, or a
cache path. A pre-publish source has none of those.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cpv_agent_closure import (  # noqa: E402
    DEFAULT_MAX_DEPTH,
    AgentClosure,
    closure_files,
    find_plugin_root,
    resolve_agent_closure,
    unreachable_closure_files,
)
from cpv_skillaudit_native import (  # noqa: E402
    filter_scannable_files,
    run_skillaudit_scan_files,
)
from cpv_skillaudit_native import report_findings as skillaudit_report_findings  # noqa: E402
from cpv_validation_common import (  # noqa: E402
    EXIT_OK,
    ValidationReport,
    print_report_summary,
    print_results_aggregated,
    save_report_and_print_summary,
)

# The scan-step table is the mechanism that makes an unrun scanner VISIBLE, and
# `validate_security` already owns it. Importing its recorder (underscore-named
# but module-level, like `_resolve_report_root` below) keeps ONE step-log and ONE
# table renderer — a second copy would let the two surfaces disagree about what
# "SKIPPED" looks like, which is precisely how a coverage gap goes unnoticed.
from validate_security import (  # noqa: E402
    _record_step,
    _reset_scan_step_log,
    format_scan_step_table,
    get_scan_step_log,
)

#: Severity tiers that would gate a publish. Anything in here, when it comes
#: from an UNREACHABLE skill, is demoted to WARNING — see the module docstring.
_BLOCKING_LEVELS: tuple[str, ...] = ("CRITICAL", "MAJOR", "MINOR", "NIT")

#: Exit code for "the findings are clean but a scanner class did not run", used
#: ONLY under ``--require-full-coverage``. Deliberately outside the 0-4 severity
#: range so a coverage gap can never be mistaken for a finding tier.
EXIT_INCOMPLETE_COVERAGE = 5

#: Step-table statuses that mean "this scanner did NOT produce coverage". The
#: verdict can never read VALID while one of these is present: an unrun scanner
#: silently absent from a report is an effective suppression, and "cannot check"
#: is never "clean".
_NO_COVERAGE_STATUSES: frozenset[str] = frozenset({"SKIPPED", "FAILED"})

#: Plugin-STRUCTURE scanners that are out of scope for a single-agent target,
#: with the reason each is out of scope. They audit a plugin's manifest, hook
#: config, and marketplace wiring — none of which is part of an agent's skill
#: closure — so pointing them at a closure file set would describe the file set,
#: not the agent. Recorded EXPLICITLY in the step table with this reason rather
#: than omitted, and the report tells the operator which command does cover them.
OUT_OF_SCOPE_SCANNERS: tuple[tuple[int, str, str], ...] = (
    (
        24,
        "External: cc-audit (plugin structure)",
        "out of scope for a single agent — audits plugin manifest/hook structure; "
        "run `remote_validation.py security <plugin>` for it",
    ),
    (
        25,
        "External: tirith (plugin policy)",
        "out of scope for a single agent — audits plugin-level policy; "
        "run `remote_validation.py security <plugin>` for it",
    ),
)

#: Marker on every demoted unreachable finding. Load-bearing for the reader AND
#: for tests: it is how a non-gating unreachable finding is told apart from a
#: genuine WARNING produced by a reachable file.
UNREACHABLE_PREFIX = "[unreachable — the Skill tool gate is shut, so this cannot execute]"


# ---------------------------------------------------------------------------
# Root grouping — point 3 of the module docstring
# ---------------------------------------------------------------------------


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return path


def _is_under(root: Path, path: Path) -> bool:
    try:
        return _safe_resolve(path).is_relative_to(_safe_resolve(root))
    except (OSError, RuntimeError, ValueError):
        return False


def owning_root(path: Path, skill_roots: Sequence[Path]) -> Path:
    """The root ``path`` should be scanned and reported RELATIVE TO.

    Resolution order, most-specific first:

    1. the nearest ancestor carrying ``.claude-plugin/plugin.json`` — the plugin
       that ships the file. This is also the root whose ``.gitignore`` / git
       index decides shipped-ness, which is the whole reason the root matters;
    2. else the DEEPEST skill search root that contains the file, lifted out of
       its ``skills`` segment (``~/.claude/skills`` → ``~/.claude``) so the
       reported path reads ``skills/<name>/SKILL.md`` instead of a bare
       ``SKILL.md`` that could belong to any skill;
    3. else, for an agent file in an ``agents/`` directory, that directory's
       parent — the manifest-less pre-publish source shape;
    4. else the file's own directory.

    Never raises: a path that resolves nowhere sensible still gets a usable
    root, because a crash here would take down the whole scan over one odd path.
    """
    plugin_root = find_plugin_root(path)
    if plugin_root is not None:
        return plugin_root

    deepest: Path | None = None
    for root in skill_roots:
        if not _is_under(root, path):
            continue
        resolved = _safe_resolve(root)
        if deepest is None or len(resolved.parts) > len(deepest.parts):
            deepest = resolved
    if deepest is not None:
        if deepest.name.lower() == "skills" and deepest.parent != deepest:
            return deepest.parent
        return deepest

    parent = _safe_resolve(path).parent
    if parent.name.lower() == "agents" and parent.parent != parent:
        return parent.parent
    return parent


def group_by_root(files: Iterable[Path], skill_roots: Sequence[Path]) -> list[tuple[Path, list[Path]]]:
    """Partition ``files`` by :func:`owning_root`, deterministically ordered.

    One group per root, so each group can be handed to
    ``run_skillaudit_scan_files`` with the root its paths are actually relative
    to. Groups are sorted by root and files sorted within a group, so the scan
    order — and therefore the report order — is stable across runs.
    """
    groups: dict[Path, list[Path]] = {}
    for path in files:
        groups.setdefault(owning_root(path, skill_roots), []).append(path)
    return [(root, sorted(groups[root])) for root in sorted(groups)]


# ---------------------------------------------------------------------------
# The result model
# ---------------------------------------------------------------------------


@dataclass
class AgentSecurityResult:
    """Everything one ``agent-security`` run produced."""

    agent_path: str
    closure: AgentClosure
    report: ValidationReport
    scanned_files: tuple[str, ...] = ()
    """The GATING scan set that was actually scanned — the agent file plus every
    reachable closure file that SURVIVED the suppression chain."""
    suppressed_files: tuple[str, ...] = ()
    """Closure candidates the suppression chain removed (vendored, oversize,
    non-scannable, or gitignored-AND-untracked). Listed rather than dropped so
    "why was my file not scanned?" has an answer that is not guesswork."""
    unreachable_files: tuple[str, ...] = ()
    """Scanned files of resolved-but-unreachable skills. Reported, never gating."""
    files_scanned: int = 0
    unreachable_findings: list[dict[str, object]] = field(default_factory=list)
    catalog_ok: bool = True
    """False when the skillaudit rule catalog is missing (a CPV packaging
    defect). ``report_findings`` has already emitted a CRITICAL in that case, so
    the run FAILS rather than reporting a vacuous clean."""
    steps: list[dict[str, object]] = field(default_factory=list)
    """The scan-step table: one row per scanner, RAN / SKIPPED / FAILED / N/A."""

    @property
    def coverage_gaps(self) -> list[dict[str, str]]:
        """Every scanner that produced NO coverage, with its stated reason.

        A step recorded ``N/A`` is NOT a gap: it is a scanner declared out of
        scope for a single-agent target, with the reason in the table and the
        covering command named in the report. Only SKIPPED / FAILED — a scanner
        that WAS in scope and did not deliver — counts.
        """
        return [
            {"step": str(s.get("name", "")), "status": str(s.get("status", "")), "reason": str(s.get("details", ""))}
            for s in self.steps
            if str(s.get("status", "")) in _NO_COVERAGE_STATUSES
        ]

    @property
    def coverage_complete(self) -> bool:
        return not self.coverage_gaps

    def verdict(self, *, strict: bool = False) -> str:
        """``INVALID`` / ``INCOMPLETE`` / ``VALID`` — a THREE-state verdict.

        The third state is the point. A scanner class that could not run is not
        evidence of cleanliness, so a run with a coverage gap must never print
        VALID: that is how an operator concludes "the agent is fine" about
        content the plugin gate calls INVALID. INVALID still wins over
        INCOMPLETE — a real finding is a stronger fact than a missing scanner.
        """
        code = self.report.exit_code_strict() if strict else self.report.exit_code
        if code != EXIT_OK:
            return "INVALID"
        if not self.coverage_complete:
            return "INCOMPLETE"
        return "VALID"

    def to_dict(self) -> dict[str, object]:
        """The ``--json`` payload: the standard report dict plus the closure facts.

        ``report.to_dict()`` is reused verbatim, so ``counts`` / ``results`` /
        ``score`` / ``exit_code`` mean exactly what they mean for every other
        CPV validator. Everything added is closure evidence a consumer cannot
        derive from the findings alone.
        """
        payload = self.report.to_dict()
        payload["agent_path"] = self.agent_path
        payload["exit_code_strict"] = self.report.exit_code_strict()
        payload["skill_roots"] = list(self.closure.skill_roots)
        payload["can_load_skills_at_runtime"] = self.closure.can_load_at_runtime
        payload["tools_declared"] = (
            None if self.closure.tools_declared is None else list(self.closure.tools_declared)
        )
        payload["files_scanned"] = self.files_scanned
        payload["scanned_files"] = list(self.scanned_files)
        payload["suppressed_files"] = list(self.suppressed_files)
        payload["catalog_ok"] = self.catalog_ok
        payload["closure"] = [
            {
                "name": ref.name,
                "namespace": ref.namespace,
                "origin": ref.origin,
                "source_file": ref.source_file,
                "line": ref.line,
                "resolved_path": ref.resolved_path,
                "reachable": ref.reachable,
            }
            for ref in self.closure.refs
        ]
        payload["unreachable"] = {
            "files": list(self.unreachable_files),
            "findings": self.unreachable_findings,
            "gating": False,
        }
        payload["scan_steps"] = self.steps
        payload["coverage"] = {
            "complete": self.coverage_complete,
            "gaps": self.coverage_gaps,
        }
        payload["verdict"] = self.verdict()
        payload["verdict_strict"] = self.verdict(strict=True)
        return payload


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _resolve_should_skip() -> Callable[[str, int | None], bool]:
    """The suppression predicate, imported from its ONE definition.

    Deferred import: ``validate_security`` is a large module and this keeps
    ``cpv_agent_security`` cheap to import (the closure resolver and the tests
    both import it without needing the whole security validator). The function
    itself is NOT reimplemented here — that chain (self-scan, vendored,
    dev-scratch, test, fp-corpus, per-line pattern-source) has exactly one
    definition, so the plugin scan and the agent scan can never disagree about
    whether a given file:line is a finding.
    """
    from validate_security import skillaudit_should_skip  # noqa: PLC0415

    return skillaudit_should_skip


@contextmanager
def _armed_self_scan(plugin_root: Path) -> "Iterator[None]":
    """ARM the SHA-verified CPV self-scan exemption for the duration of a scan.

    Without this, ``cpv_self_scan_skip`` returns False unconditionally and the
    chain `_resolve_should_skip` returns is a NO-OP for the self-scan leg — so
    scanning one of CPV's OWN agents surfaces CPV's own hash-verified files as
    findings, and this entry point calls a valid agent INVALID where the plugin
    gate calls it clean. Measured before this was armed: `agent-security` on
    `cpv-plugin-validator-agent.md` reported a CRITICAL
    `INTENT_SECURITY_DISABLE_INTENT` on CPV's own plugin-management SKILL.md
    (the prose "Enable / Disable … Security Audit"), while `plugin --strict`
    reported zero of them on the identical file.

    This SUPPRESSES NOTHING that the plugin gate does not already suppress: the
    exemption requires a SHA match against the trusted manifest, so a tampered
    or unlisted file is still scanned, and a third-party plugin never matches at
    all. It is the same arm/disarm sequence `validate_plugin` performs for its
    own skillaudit call, for the same reason.

    The finally-DISARM is mandatory, not tidiness: the flag is module-global in
    `validate_security`, and this scanner runs in-process (test suite, batch
    orchestrator, several agents in sequence). A flag left armed by a CPV-self
    target would let the NEXT plugin's scan read that stale state and wrongly
    suppress its findings.
    """
    try:
        from validate_security import _set_cpv_self_scan, is_cpv_self_scan  # noqa: PLC0415
    except ImportError:  # pragma: no cover - validate_security ships with CPV
        yield
        return
    try:
        _set_cpv_self_scan(is_cpv_self_scan(plugin_root), plugin_root=plugin_root, notice_report=None)
        yield
    finally:
        _set_cpv_self_scan(False)


# ---------------------------------------------------------------------------
# The external-scanner pass — a closure MIRROR the plugin-scoped wrappers accept
# ---------------------------------------------------------------------------


def build_closure_mirror(
    groups: list[tuple[Path, list[Path]]],
) -> tuple[Path, dict[str, Path]]:
    """Mirror a grouped closure file set into ONE ephemeral scannable tree.

    Returns ``(mirror_root, {mirror_relative_posix: real_absolute_path})``. The
    caller OWNS the returned directory and must remove it.

    WHY a mirror exists at all — this is the whole reason the external scanners
    can run over a closure:

    * every external wrapper is DIRECTORY-shaped (``run_cisco_scan(dir)``,
      ``check_trufflehog(dir, report)``, ``run_snyk_agent_scan(dir)``), so a bare
      file list cannot be handed to any of them;
    * the Cisco scanner WRITES its JSON dump into the directory it scans, so
      pointing it at the user's real skill directory would mutate the tree under
      audit — unacceptable;
    * each file is placed at its OWNING-ROOT-RELATIVE path, so the mirror
      reproduces the ``agents/…`` + ``skills/<name>/…`` layout every wrapper
      already knows how to discover: ``native_skill_targets`` finds
      ``<mirror>/skills``, ``build_staged_tree`` stages ``<mirror>/agents/*.md``,
      and Cisco's recursive walk sees the real SKILL.md content. Nothing is
      reimplemented — the wrappers run unchanged.

    A collision (two roots contributing the same relative path) is resolved with
    a numeric suffix rather than silently overwriting: a dropped file is a file
    nobody scanned, and reporting that as clean is the exact trap this whole
    change exists to close.
    """
    mirror_root = Path(tempfile.mkdtemp(prefix="cpv-agent-closure-")).resolve()
    mapping: dict[str, Path] = {}
    used: set[str] = set()
    for root, files in groups:
        for real in files:
            try:
                rel = real.relative_to(root).as_posix()
            except ValueError:
                rel = real.name
            candidate = rel
            n = 1
            while candidate in used:
                n += 1
                stem = Path(rel)
                candidate = (stem.parent / f"{stem.stem}__{n}{stem.suffix}").as_posix()
            used.add(candidate)
            dest = mirror_root / candidate
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(real, dest)
            mapping[candidate] = real
    return mirror_root, mapping


def _mirror_relative(file_ref: str, mirror_root: Path) -> str | None:
    """The mirror-relative posix path a finding refers to, or None.

    External scanners emit either an ABSOLUTE path under the scanned tree
    (Cisco) or a path already relativised against it (trufflehog / semgrep /
    Snyk's remapped component path), so both shapes are normalised here. None
    means "not a mirror path" — a global/sentinel finding such as Snyk's
    ``<staged instruction surfaces>``, which must be left exactly as-is.
    """
    if not file_ref:
        return None
    text = file_ref.replace("\\", "/")
    mirror = mirror_root.as_posix()
    if text.startswith(mirror + "/"):
        return text[len(mirror) + 1 :]
    if text == mirror:
        return None
    if text.startswith("/"):
        return None
    return text


def _absorb_external_findings(
    report: ValidationReport,
    start_index: int,
    mirror_root: Path,
    *,
    demote_all: bool,
) -> list[dict[str, object]]:
    """Rewrite an external scanner's mirror paths, and demote when this pass is
    the UNREACHABLE one.

    **Provenance comes from the PASS, not from the finding's path — and that is
    load-bearing.** Cisco's ``PIPELINE_TAINT_FLOW`` carries NO file at all
    (verified: the plugin gate reports it against ``<unknown>`` too), so a
    file-based reachable/unreachable classification cannot place it. Classifying
    by which mirror it was found in is exact for every scanner, including the
    ones that report a global finding — which is why the caller runs the gating
    set and the unreachable set as two separate passes over two separate mirrors
    instead of one combined pass.

    ``demote_all`` therefore re-levels EVERY blocking finding of an unreachable
    pass to WARNING — the only tier that never blocks, even under ``--strict`` —
    with the original severity preserved in the message. Without it an external
    CRITICAL on code the agent cannot reach would gate a publish.

    The path rewrite makes a finding's ``file`` the owning-root-relative path
    (which IS the mirror-relative path by construction), so no ephemeral temp
    path ever reaches the report. A global finding with no mirror path is left
    exactly as the scanner reported it rather than guessed at.
    """
    demoted: list[dict[str, object]] = []
    for item in report.results[start_index:]:
        rel = _mirror_relative(item.file or "", mirror_root)
        if rel is not None:
            item.file = rel
        if not demote_all:
            continue
        if item.level in _BLOCKING_LEVELS:
            original = item.level
            item.level = "WARNING"
            item.message = f"{UNREACHABLE_PREFIX} ({original}) {item.message}"
            demoted.append(
                {"original_level": original, "message": item.message, "file": item.file, "line": item.line}
            )
        elif item.level == "INFO":
            item.message = f"{UNREACHABLE_PREFIX} {item.message}"
    return demoted


def run_external_scanners(
    report: ValidationReport,
    groups: list[tuple[Path, list[Path]]],
    *,
    demote_all: bool = False,
    record_steps: bool = True,
) -> list[dict[str, object]]:
    """Run every closure-applicable EXTERNAL scanner over the mirrored file set.

    An external scanner class that is silently absent from a report is an
    effective SUPPRESSION: the plugin gate reports a payload as INVALID with
    MAJORs from Cisco and Snyk, so a single-agent scan that omits them reports
    the SAME content as clean. Each scanner therefore runs here through its
    EXISTING plugin-scoped entry point (no rule, pattern, or severity mapping is
    copied), and each records its own step-table row — RAN / SKIPPED / FAILED
    with a reason — so an absent scanner is visible instead of invisible.

    Ordering matches ``validate_security``'s step numbering so the two tables
    read alike. Every safety invariant of the wrapped scanners is preserved by
    construction, because the wrappers themselves are unchanged: Snyk still
    never passes ``--ci`` or ``--dangerously-run-mcp-servers`` and never starts
    an MCP server; Cisco still runs programmatic-only with no API-key engines.

    ``demote_all`` marks this as the UNREACHABLE pass (see
    :func:`_absorb_external_findings` for why provenance is per-pass);
    ``record_steps`` is False for it because the coverage table describes the
    GATING pass — the same binaries with the same availability serve both, so a
    second full set of rows would double-count the same coverage facts.

    Returns the unreachable-demotion records for the ``--json`` payload.
    """
    from validate_security import (  # noqa: PLC0415
        check_cisco_scanner,
        check_semgrep,
        check_trufflehog,
    )

    mirror_root, _mapping = build_closure_mirror(groups)
    demoted: list[dict[str, object]] = []
    try:
        # Cisco AI Defense — the scanner whose PIPELINE_TAINT_FLOW MAJOR the
        # plugin gate reports and a closure scan previously missed entirely.
        before = len(report.results)
        if record_steps:
            check_cisco_scanner(mirror_root, report, step_num=26)
        else:
            _run_without_recording(lambda: check_cisco_scanner(mirror_root, report, step_num=26))
        demoted += _absorb_external_findings(report, before, mirror_root, demote_all=demote_all)

        # trufflehog / semgrep — path-based, so the mirror is a faithful target.
        # Status is derived MECHANICALLY from `shutil.which`, never by sniffing
        # the emitted WARNING prose (a reword must not silently relabel a broken
        # scan as a benign skip). Mirrors `validate_security._task_specialist`.
        for step_num, name, binary, check in (
            (18, "External: trufflehog (secrets)", "trufflehog", check_trufflehog),
            (19, "External: semgrep (SAST)", "semgrep", check_semgrep),
        ):
            if not shutil.which(binary):
                if record_steps:
                    _record_step(
                        step_num,
                        name,
                        "SKIPPED",
                        details=f"`{binary}` not on PATH (install via brew/pipx/etc.) — NOT scanned, not clean",
                    )
                continue
            before = len(report.results)
            count = check(mirror_root, report)
            demoted += _absorb_external_findings(report, before, mirror_root, demote_all=demote_all)
            if record_steps:
                _record_step(step_num, name, "RAN", findings=count, files=f"{binary} over the closure mirror")

        # Snyk Agent Scan — opt-in and token-gated; its own wrapper stages the
        # agent .md as a synthetic skill and scans `<mirror>/skills` natively, so
        # the mirror covers BOTH halves of the closure with no second stager.
        before = len(report.results)
        if record_steps:
            check_snyk_agent_scan_over(mirror_root, report)
        else:
            _run_without_recording(lambda: check_snyk_agent_scan_over(mirror_root, report))
        demoted += _absorb_external_findings(report, before, mirror_root, demote_all=demote_all)

        # Plugin-STRUCTURE scanners: explicitly out of scope, explicitly stated.
        if record_steps:
            for step_num, name, reason in OUT_OF_SCOPE_SCANNERS:
                _record_step(step_num, name, "N/A", details=reason)
    finally:
        # Ephemeral scratch this function created under the system temp dir —
        # trivially regeneratable, so a plain rmtree is the right tool.
        shutil.rmtree(mirror_root, ignore_errors=True)
    return demoted


def _run_without_recording(fn: Callable[[], object]) -> None:
    """Call ``fn`` and discard any step rows it recorded.

    Cisco and Snyk record their OWN step row inside their shared entry point
    (which is what makes them visible on the plugin path), so the unreachable
    pass would append a duplicate row describing the same binary's availability.
    Trimming the log back to its pre-call length is the least invasive way to run
    the same SSOT entry point twice without inventing a "quiet" parameter on it.
    """
    before = len(get_scan_step_log())
    fn()
    after = get_scan_step_log()
    if len(after) > before:
        _reset_scan_step_log()
        for row in after[:before]:
            _record_step(
                int(row["num"]),
                str(row["name"]),
                str(row["status"]),
                findings=int(row.get("findings", 0) or 0),
                files=str(row.get("files", "") or ""),
                details=str(row.get("details", "") or ""),
            )


def check_snyk_agent_scan_over(mirror_root: Path, report: ValidationReport) -> int:
    """Thin indirection so the Snyk entry point is imported lazily, like Cisco's.

    Kept separate purely to keep ``run_external_scanners`` readable; it adds no
    behaviour of its own and holds no copy of the Snyk wiring.
    """
    from validate_security import check_snyk_agent_scan  # noqa: PLC0415

    return check_snyk_agent_scan(mirror_root, report, step_num=28)


def _filter_groups(
    groups: list[tuple[Path, list[Path]]],
) -> list[tuple[Path, list[Path], list[Path]]]:
    """Split each ``(root, candidates)`` group into ``(root, kept, dropped)``.

    ``filter_scannable_files`` is the SSOT for "would the tree scan scan this
    file?", and it is root-relative — which is exactly why the groups exist. A
    root with nothing left after filtering is dropped from the result, so the
    scan never spawns work for an empty group.
    """
    out: list[tuple[Path, list[Path], list[Path]]] = []
    for root, candidates in groups:
        kept = filter_scannable_files(root, candidates)
        kept_keys = {_safe_resolve(p) for p in kept}
        dropped = [p for p in candidates if _safe_resolve(p) not in kept_keys]
        if kept or dropped:
            out.append((root, kept, dropped))
    return out


def scan_agent(
    agent_path: Path,
    *,
    roots: Sequence[Path] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    externals: bool = True,
) -> AgentSecurityResult:
    """Scan one agent file plus its reachable skill closure.

    ``roots=None`` means "resolve them" (plugin → project → user scope, via
    ``skill_search_roots``). Passing explicit roots makes the run HERMETIC and
    machine-independent, which is what ``--skills-root`` is for.

    ``externals=False`` is a TEST-ISOLATION knob (the same precedent as
    ``validate_security(enable_tirith=False)``), and it is NOT a way to
    manufacture a green result: the skipped scanners are recorded as SKIPPED with
    that reason, so ``coverage_complete`` is False and the verdict can only ever
    be INCOMPLETE or INVALID — never VALID.

    Thin wrapper: it ARMS the SHA-verified self-scan exemption (see
    ``_armed_self_scan`` for why, and for why the disarm is mandatory) around the
    real work, so this entry point and the plugin gate agree on whether a given
    file:line is a finding.
    """
    agent_path = _safe_resolve(agent_path)
    # The plugin root of `agents/<name>.md`. Used ONLY to ask "is this target
    # CPV itself?" — the exemption still requires a per-file SHA match, so a
    # wrong guess here can never suppress a third-party plugin's findings.
    plugin_root = agent_path.parent.parent
    with _armed_self_scan(plugin_root):
        return _scan_agent_inner(agent_path, roots=roots, max_depth=max_depth, externals=externals)


def _scan_agent_inner(
    agent_path: Path,
    *,
    roots: Sequence[Path] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    externals: bool = True,
) -> AgentSecurityResult:
    """``scan_agent``'s body, with the self-scan exemption already armed."""
    agent_path = _safe_resolve(agent_path)
    closure = resolve_agent_closure(agent_path, roots=roots, max_depth=max_depth)
    skill_roots = [Path(r) for r in closure.skill_roots]
    should_skip = _resolve_should_skip()

    report = ValidationReport()

    # Every path is RESOLVED here, once. `owning_root` resolves the root it
    # returns, and the suppression chain's shipped-surface tier is computed as
    # `file.relative_to(root)` — so an UNRESOLVED file under a resolved root
    # (`/tmp/x` vs `/private/tmp/x` on macOS, or any symlinked checkout) raises
    # ValueError there, silently disables that tier, and the agent scan would
    # then report a file the plugin scan correctly skips.
    #
    # The agent file itself is ALWAYS in the gating set: it is the target, and its
    # own body is instruction content. De-duplicated against the closure in case
    # an agent .md happens to live inside a skill directory.
    gating: list[Path] = [agent_path]
    for path in closure_files(closure):
        resolved = _safe_resolve(path)
        if resolved != agent_path:
            gating.append(resolved)

    gating_keys = set(gating)
    # Subtract the gating set: ONE skill can be both preloaded (reachable) and
    # invoked at runtime (unreachable when the gate is shut). Reporting its files
    # in both sections would double-count them AND gate from one section while
    # the other claims they cannot execute.
    unreachable = [
        resolved for p in unreachable_closure_files(closure) if (resolved := _safe_resolve(p)) not in gating_keys
    ]

    result = AgentSecurityResult(agent_path=str(agent_path), closure=closure, report=report)

    # Resolve the suppression chain BEFORE scanning, per group, so the report can
    # state what was actually scanned and what the chain removed. `scan_files`
    # applies the same (idempotent) filter internally — it must, because it is
    # the public entry point and a caller must not be able to bypass suppression
    # by pre-filtering. Doing it here as well is what makes the reported set
    # honest instead of a candidate list dressed up as a scanned list.
    gating_groups = _filter_groups(group_by_root(gating, skill_roots))
    unreachable_groups = _filter_groups(group_by_root(unreachable, skill_roots))

    # `scanned_files` is the GATING set only — the unreachable set is scanned too
    # but reported in its own section, and folding the two together would let a
    # reader believe a non-gating file was part of the verdict.
    gating_kept = sorted({p for _root, files, _dropped in gating_groups for p in files})
    unreachable_kept = sorted({p for _root, files, _dropped in unreachable_groups for p in files})
    dropped = sorted({p for _root, _files, drop in gating_groups + unreachable_groups for p in drop})
    result.scanned_files = tuple(str(p) for p in gating_kept)
    result.unreachable_files = tuple(str(p) for p in unreachable_kept)
    result.suppressed_files = tuple(str(p) for p in dropped)

    # The step log is per-run and module-global (owned by `validate_security`),
    # so reset it FIRST — a stale row from an earlier scan in the same process
    # would misreport this run's coverage.
    _reset_scan_step_log()

    # --- the gating pass ------------------------------------------------------
    for root, files, _dropped in gating_groups:
        scan = run_skillaudit_scan_files(root, files)
        if not scan.invoked:
            # A missing rule catalog is a CPV PACKAGING defect, not an opt-out.
            # `report_findings` turns it into a CRITICAL, so let it — but only
            # ONCE: every remaining group would produce the identical finding,
            # and N copies of "reinstall CPV" is noise, not information.
            skillaudit_report_findings(scan, root, report, should_skip=should_skip)
            result.catalog_ok = False
            break
        result.files_scanned += scan.files_scanned
        skillaudit_report_findings(scan, root, report, should_skip=should_skip)

    if not result.catalog_ok:
        _record_step(
            27,
            "SkillAudit native rules (in-process, MANDATORY)",
            "FAILED",
            details="skillaudit rule catalog missing — reinstall CPV (packaging integrity)",
        )
        result.steps = get_scan_step_log()
        return result

    _record_step(
        27,
        "SkillAudit native rules (in-process, MANDATORY)",
        "RAN",
        findings=sum(1 for r in report.results if r.level in _BLOCKING_LEVELS),
        files=f"{result.files_scanned} closure file(s)",
    )

    # --- the unreachable pass (reported, never gating) ------------------------
    for root, files, _dropped in unreachable_groups:
        scan = run_skillaudit_scan_files(root, files)
        if not scan.invoked:  # pragma: no cover — the gating pass proved it loads
            continue
        # Stage into a THROWAWAY report so the finding text is formatted by the
        # one adapter (`report_findings`) and then demote wholesale. Formatting
        # them here by hand would be a second copy of the message grammar.
        staging = ValidationReport()
        skillaudit_report_findings(scan, root, staging, should_skip=should_skip)
        for item in staging.results:
            if item.level in _BLOCKING_LEVELS:
                report.warning(f"{UNREACHABLE_PREFIX} ({item.level}) {item.message}", item.file, item.line)
                result.unreachable_findings.append(
                    {
                        "original_level": item.level,
                        "message": item.message,
                        "file": item.file,
                        "line": item.line,
                    }
                )
            else:
                report.info(f"{UNREACHABLE_PREFIX} {item.message}", item.file, item.line)

    # --- the EXTERNAL pass ----------------------------------------------------
    # The in-process engine alone under-reports: the plugin gate calls the same
    # payload INVALID on Cisco + Snyk MAJORs the in-process rules do not carry.
    # An omitted scanner class is an effective suppression, so this pass is not
    # optional — and when it cannot run, each gap is recorded, not hidden.
    #
    # TWO passes over TWO mirrors, not one combined pass: an external finding's
    # provenance has to come from WHICH mirror it was found in, because some
    # scanners report a finding with NO file at all (Cisco's PIPELINE_TAINT_FLOW
    # — the plugin gate shows it against `<unknown>` too). A combined pass would
    # be unable to place such a finding, and would have to either gate on
    # unreachable dead code or drop it.
    gating_scannable = [(root, files) for root, files, _dropped in gating_groups if files]
    unreachable_scannable = [(root, files) for root, files, _dropped in unreachable_groups if files]
    if not externals:
        _record_step(
            0,
            "External scanners (cisco / trufflehog / semgrep / snyk)",
            "SKIPPED",
            details="externals=False (test isolation knob) — NOT scanned, so the verdict cannot read VALID",
        )
    elif not gating_scannable:  # pragma: no cover — the agent file is always present
        _record_step(
            0,
            "External scanners (cisco / trufflehog / semgrep / snyk)",
            "SKIPPED",
            details="no scannable closure file survived the suppression chain",
        )
    else:
        run_external_scanners(report, gating_scannable)
        if unreachable_scannable:
            result.unreachable_findings += run_external_scanners(
                report,
                unreachable_scannable,
                demote_all=True,
                record_steps=False,
            )

    result.steps = get_scan_step_log()
    _add_closure_context(result)
    return result


def _add_closure_context(result: AgentSecurityResult) -> None:
    """Add the INFO/PASSED lines that make the verdict interpretable.

    A reader must be able to tell "clean" from "scanned almost nothing": a
    closure that resolved no skills produces the same empty finding list as a
    genuinely clean one. Stating the scan set, the gate state and the
    unreachable count is what keeps a green verdict honest.
    """
    closure = result.closure
    report = result.report
    reachable = sum(1 for ref in closure.refs if ref.reachable and ref.resolved_path)
    unresolved = sum(1 for ref in closure.refs if ref.resolved_path is None)

    report.info(
        f"Agent closure: {len(closure.refs)} skill reference(s), {reachable} reachable and resolved, "
        f"{unresolved} unresolved; Skill tool gate is "
        f"{'OPEN' if closure.can_load_at_runtime else 'SHUT'}; "
        f"{len(result.scanned_files)} file(s) scanned, "
        f"{len(result.suppressed_files)} suppressed by the standard chain",
        result.agent_path,
    )
    if not closure.skill_roots:
        report.warning(
            "No skill search root resolved, so the closure could not be scanned beyond the agent file "
            "itself. Pass --skills-root PATH to scan the skills this agent actually loads — an "
            "unresolved closure is NOT a clean closure.",
            result.agent_path,
        )
    if result.unreachable_files:
        report.info(
            f"{len(result.unreachable_files)} file(s) belong to skills this agent CANNOT reach "
            f"(the Skill tool gate is shut). Their findings are reported as WARNING and do not gate: "
            f"unreachable code cannot execute. They become live the moment the gate opens.",
            result.agent_path,
        )
    # A scanner that could not run is NEVER folded into the pass count: a PASSED
    # line beside a coverage gap is the exact false reassurance this whole pass
    # exists to prevent. The gap becomes a WARNING naming each scanner + reason,
    # and the verdict reads INCOMPLETE instead of VALID.
    if not result.coverage_complete:
        for gap in result.coverage_gaps:
            report.warning(
                f"COVERAGE GAP — {gap['step']} did not run ({gap['status']}): {gap['reason'] or 'no reason given'}. "
                f"This is NOT evidence of cleanliness; the scan is INCOMPLETE.",
                result.agent_path,
            )
        report.info(
            f"Scan coverage INCOMPLETE — {len(result.coverage_gaps)} scanner(s) produced no coverage. "
            f"The verdict is UNKNOWN for the rules those scanners carry.",
            result.agent_path,
        )
    elif report.exit_code_strict() == EXIT_OK:
        report.passed(
            f"No blocking security findings in the agent or its {reachable} reachable skill(s) "
            f"across every applicable scanner",
            result.agent_path,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _default_report_path(agent_path: Path) -> Path:
    """``<main-repo-root>/reports/agent-security/<ts±tz>-<slug>.md``.

    Reuses ``validate_security._resolve_report_root`` so the anchor logic (main
    checkout root → ``CLAUDE_PROJECT_DIR`` → ``$TMPDIR``) has one definition.
    """
    from validate_security import _resolve_report_root  # noqa: PLC0415

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d_%H%M%S%z")
    slug = agent_path.stem or "agent"
    return _resolve_report_root() / "reports" / "agent-security" / f"{ts}-{slug}.md"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Same contract as ``validate_security``: severities, exit
    codes (0 ok / 1 critical / 2 major / 3 minor / 4 nit-under-strict),
    ``--json``, ``--strict``."""
    from cpv_validation_common import launcher_epilog  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        description="Security-scan ONE agent together with its reachable skill closure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The scan set is the agent .md plus every REACHABLE skill's SKILL.md,\n"
            "references/** and scripts/** — because a reachable skill's body enters\n"
            "the agent's context as instructions.\n\n"
            "A skill the agent CANNOT reach (the Skill tool gate is shut) is still\n"
            "reported, in the unreachable section, as a non-gating WARNING.\n\n"
            "Scanners: the in-process skillaudit engine PLUS the external\n"
            "cisco / trufflehog / semgrep / snyk scanners, each over the closure.\n"
            "A scanner that cannot run is reported SKIPPED with its reason and the\n"
            'verdict reads INCOMPLETE — "cannot check" is never "clean".\n\n'
            "Exit Codes:\n"
            "  0 - No blocking findings\n"
            "  1 - CRITICAL issues found\n"
            "  2 - MAJOR issues found\n"
            "  3 - MINOR issues found\n"
            "  4 - NIT issues found (--strict only)\n"
            "  5 - Coverage INCOMPLETE (only with --require-full-coverage)\n\n"
            + launcher_epilog("agent-security")
        ),
    )
    parser.add_argument("agent_path", help="Path to the agent .md file to scan")
    parser.add_argument(
        "--skills-root",
        action="append",
        default=None,
        dest="skills_roots",
        metavar="PATH",
        help=(
            "Skill directory to resolve the agent's closure against (repeatable). "
            "Replaces auto-resolution from the plugin / project / user scope, which is what makes "
            "the scan hermetic and machine-independent."
        ),
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help=f"How deep to follow skill-to-skill references (default {DEFAULT_MAX_DEPTH})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Include INFO and PASSED in the report body")
    parser.add_argument("--json", action="store_true", help="Emit the raw JSON payload (stdout stays pure JSON)")
    parser.add_argument("--strict", action="store_true", help="Strict mode — NIT issues also block")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Write the detailed report to this path instead of the default reports/ location",
    )
    parser.add_argument(
        "--no-external-scanners",
        action="store_true",
        help=(
            "Do NOT run cisco/trufflehog/semgrep/snyk (test-isolation knob). This cannot produce a "
            "green result: the skipped scanners are recorded as SKIPPED, so the verdict reads "
            "INCOMPLETE, never VALID."
        ),
    )
    parser.add_argument(
        "--require-full-coverage",
        action="store_true",
        help="Exit 5 when any in-scope scanner did not run (for CI that must not accept a partial scan)",
    )
    args = parser.parse_args(argv)

    skills_roots: list[Path] | None = None
    if args.skills_roots is not None:
        skills_roots = []
        for raw in args.skills_roots:
            root = Path(raw).expanduser()
            if not root.is_dir():
                # Fail loudly. Silently dropping a bad root would leave every
                # name unresolved and turn the whole closure scan vacuous —
                # green because it scanned nothing.
                print(f"Error: --skills-root {root} is not a directory", file=sys.stderr)
                return 1
            skills_roots.append(root.resolve())

    agent_path = Path(args.agent_path).expanduser()
    if not agent_path.is_file():
        print(f"Error: {agent_path} is not a file", file=sys.stderr)
        return 1
    if agent_path.suffix.lower() != ".md":
        print(f"Error: {agent_path} is not a Markdown (.md) agent file", file=sys.stderr)
        return 1
    if args.max_depth < 1:
        print("Error: --max-depth must be >= 1", file=sys.stderr)
        return 1

    result = scan_agent(
        agent_path,
        roots=skills_roots,
        max_depth=args.max_depth,
        externals=not args.no_external_scanners,
    )
    report = result.report

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        report_path = Path(args.report).expanduser() if args.report else _default_report_path(agent_path)
        step_table = format_scan_step_table(result.steps)

        def _print_full(rep: ValidationReport, verbose: bool = False) -> None:
            # The coverage table goes FIRST in the report body: the reader must
            # know which scanners actually ran BEFORE reading the findings, or a
            # short finding list reads as "clean" instead of "barely scanned".
            if step_table:
                print("## Scan Coverage — per-scanner status\n")
                print(step_table)
                print()
            print_report_summary(rep, "Agent Security Report")
            print(f"\n## Agent\n\n{result.agent_path}\n")
            print("## Gating scan set — scanned, and these findings BLOCK\n")
            for path in result.scanned_files:
                print(f"- {path}")
            print()
            if result.suppressed_files:
                print("## Suppressed by the standard chain — NOT scanned\n")
                print(
                    "Removed by the same suppression chain the plugin scan applies (vendored, "
                    "oversize, non-scannable, or gitignored-AND-untracked). Listed so a missing "
                    "file is explainable rather than a mystery.\n"
                )
                for path in result.suppressed_files:
                    print(f"- {path}")
                print()
            if result.unreachable_files:
                print("## Unreachable — scanned and reported, but NOT gating\n")
                print(
                    "These files belong to skills the agent cannot reach (the Skill tool gate is "
                    "shut), so they cannot execute and their findings do not block. They are listed "
                    'because "cannot reach" is not "clean" — they ship, and they go live the moment '
                    "the gate opens.\n"
                )
                for path in result.unreachable_files:
                    print(f"- {path}")
                print()
            print_results_aggregated(rep, verbose=verbose)

        save_report_and_print_summary(
            report,
            report_path,
            "Agent Security",
            _print_full,
            args.verbose,
            plugin_path=result.agent_path,
            security_gates=True,
            # A clean finding set with a coverage gap must NOT print VALID.
            verdict_override=None if result.coverage_complete else result.verdict(strict=args.strict),
        )

        # Surface the coverage table on stdout too, so an operator sees which
        # scanners ran without opening the report file.
        if step_table:
            print("\nScan coverage — per-scanner status:")
            print(step_table)

    code = report.exit_code_strict() if args.strict else report.exit_code
    if code == EXIT_OK and args.require_full_coverage and not result.coverage_complete:
        # Opt-in CI gate. It is NOT the default: a missing optional binary must
        # not false-block a developer, but a pipeline that requires complete
        # coverage has to be able to say so.
        return EXIT_INCOMPLETE_COVERAGE
    return code


if __name__ == "__main__":
    sys.exit(main())
