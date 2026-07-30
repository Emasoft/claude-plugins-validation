#!/usr/bin/env python3
"""Local CI-parity preflight — run the gates ``validate_plugin --strict`` omits.

The #137-143 family all share one root cause: a fixer/upgrade agent declares
DONE on ``validate_plugin --strict``, which does NOT run the jscpd / actionlint
/ mypy / ``uv sync --extra dev`` gates the adopting plugin's GitHub-CI
``ci.yml`` Lint job runs. A canonical upgrade that is locally clean therefore
still red-CIs. This preflight is the missing LOCAL mirror of those gates, so an
agent can prove CI-parity BEFORE declaring DONE (or before a real publish).

It runs, IN ORDER, the parity gates ``validate_plugin`` omits:

* **(a) jscpd copy-paste** — reusing the ``publish.py`` Gate-2b probe-then-run
  pattern (``shutil.which`` jscpd / npx, ``--version`` probe, BLOCK on
  over-threshold, **degrade to WARNING when jscpd/npx is absent**).
* **(b) actionlint** on ``.github/workflows/*.yml`` (degrade-WARNING if absent).
* **(c) mypy** on the plugin's ``scripts/`` (degrade-WARNING if absent).
* **(d) ``uv sync --extra dev``** resolve smoke (degrade-WARNING if no uv; FAIL
  only on an actual "extra dev is not defined"-class resolve error).
* **(e) Mega-Linter sub-linter parity probes** — cspell / checkov / trivy /
  bandit / shellcheck / shfmt. CI's Mega-Linter container enforces these
  (``.mega-linter.yml`` ``ENABLE_LINTERS``) but ``validate_plugin`` and the four
  gates above NEVER reproduce them locally, so an agent declares DONE on a clean
  preflight, publishes, and CI fails on checkov/trivy/cspell/bandit/shellcheck
  (MODE 2 of the #137-143 recurrence). EACH probe runs ONLY when the plugin's
  ``.mega-linter.yml`` actually ENABLES that linter (absent file / linter not
  listed → clean PASS "linter not enabled, skipped"), and only adds LOCAL
  visibility — it NEVER changes the default-enabled Mega-Linter set and NEVER
  weakens any gate. Tool on PATH → run → FAIL on a real error; tool absent →
  WARNING. The cspell probe additionally FAILs when SPELL_CSPELL is enabled but
  the plugin ships NO cspell dictionary (RC-3): CI's cspell then hard-errors on
  the plugin's own proper nouns while the local probe could not reproduce it —
  the local-GREEN / CI-RED parity hole. `standardize --fix` provisions the
  `.cspell.json` that BOTH sides read; see ``_gate_cspell``.
* **(f) the static CI-parity checks** from ``cpv_ci_parity_checks``. The COUNT and
  the id range are deliberately NOT written here: both are DERIVED at runtime from
  that module (see ``_cip_check_ids``). A hardcoded range went stale once already —
  it announced a smaller range for a release after two further checks had shipped,
  telling the user fewer checks ran than actually did.

THE DEGRADE-GRACEFULLY CONTRACT (the #129 pattern applied to the whole
preflight): a TOOL being ABSENT ALWAYS degrades to a non-blocking WARNING — it
NEVER false-blocks a fixer (a dev box without npx / actionlint / checkov must
not stop the agent). The ONLY non-zero exit comes from a REAL over-threshold
duplication, a static defect, an actionlint error, a mypy type error, a
dev-extra resolve failure, or a Mega-Linter sub-linter that ran and found a real
error — i.e. something CI would also fail on.

Exit / return contract:
    run_ci_preflight(plugin_path, *, strict=False) -> PreflightResult
    PreflightResult.exit_code: 0 when parity-clean (only WARNING/PASS), non-zero
    when a real CI gate would fail.

Run via the launcher:
    cpv-remote-validate ci-preflight <plugin-path>
"""

from __future__ import annotations

import argparse
import inspect
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cpv_ci_parity_checks
from cpv_ci_parity_checks import ParityFinding, check_ci_parity

__all__ = ["PreflightFinding", "PreflightResult", "run_ci_preflight", "main"]

# Severities, ordered worst→best for exit-code resolution.
_SEV_FAIL = "FAIL"  # a real CI gate would fail → non-zero exit
_SEV_WARNING = "WARNING"  # tool-absent / advisory → never blocks
_SEV_PASS = "PASS"  # the gate ran and was clean
# CIP static findings carry their own severities; MAJOR/MINOR map to FAIL,
# WARNING maps to WARNING.
_CIP_SEVERITY_IS_BLOCKING = {"MAJOR", "MINOR"}

# Subprocess timeouts (seconds) — mirror publish.py's Gate-2b/lint budgets.
_PROBE_TIMEOUT = 180
_JSCPD_TIMEOUT = 300
_ACTIONLINT_TIMEOUT = 120
_MYPY_TIMEOUT = 300
_UV_SYNC_TIMEOUT = 300
# Mega-Linter sub-linter probes — checkov/trivy can be slow on a large tree.
_MEGALINTER_TIMEOUT = 300

# Issue #183 — how many lines of a failing tool's OWN output the report echoes.
# A blocking gate that reports only a COUNT ("Found 2 errors in 1 file") gives the
# reader nothing to act on, and this gate is one whose failure they often cannot
# reproduce locally: `uv run mypy` resolves against the project venv while the
# preflight (like CI) does not, so the obvious command says "Success" while the
# gate says "CI would fail". The cap keeps a 5000-error run from burying the
# verdict; the elided count is always stated, never silently dropped.
_FAIL_DETAIL_MAX_LINES = 40


def _fail_detail(text: str) -> str:
    """Normalise a failing tool's captured output for the FAIL report.

    Drops blank lines (the cap is scarce, and a tool's padding carries nothing),
    caps at :data:`_FAIL_DETAIL_MAX_LINES` and, when truncated, appends an
    explicit "… and N more line(s)" so the reader always knows output was elided.
    Returns "" for empty input, which the renderer treats as "nothing to echo"
    rather than printing an empty block.

    Each surviving line keeps its OWN leading indentation — several of these
    tools (checkov, trivy, jscpd) indent findings under a header, and stripping
    the block as a whole would flatten the first line out of that structure.
    """
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) <= _FAIL_DETAIL_MAX_LINES:
        return "\n".join(lines)
    elided = len(lines) - _FAIL_DETAIL_MAX_LINES
    kept = lines[:_FAIL_DETAIL_MAX_LINES]
    kept.append(f"… and {elided} more line{'s' if elided != 1 else ''}")
    return "\n".join(kept)


@dataclass
class PreflightFinding:
    """One preflight gate outcome.

    Attributes:
        gate: The gate label (``"jscpd"`` / ``"actionlint"`` / ``"mypy"`` /
            ``"uv-sync-dev"`` / a Mega-Linter probe label (``"cspell"`` /
            ``"checkov"`` / ``"trivy"`` / ``"bandit"`` / ``"shellcheck"`` /
            ``"shfmt"``) / a ``"CIP-N"`` id).
        severity: ``"FAIL"`` (a real CI gate failure → non-zero exit),
            ``"WARNING"`` (tool-absent / advisory → never blocks), or
            ``"PASS"`` (the gate ran clean).
        message: A human-readable description.
        file: An optional plugin-relative path the finding is about.
        detail: The failing tool's OWN output, already capped by
            :func:`_fail_detail` (issue #183). Set on FAIL findings whose gate
            shells out to a tool; empty for PASS/WARNING and for the static
            CIP-N detectors, which have no external output to echo.
    """

    gate: str
    severity: str
    message: str
    file: str = ""
    detail: str = ""


@dataclass
class PreflightResult:
    """Aggregate result of a CI-parity preflight run.

    ``exit_code`` is 0 when every gate is PASS or WARNING (parity-clean), and
    non-zero when ANY gate FAILed (a real CI gate would also fail). The
    non-zero code is 1 (a single "CI would fail" verdict — the preflight is a
    binary parity gate, not a severity-graded validator).
    """

    plugin_path: Path
    strict: bool = False
    findings: list[PreflightFinding] = field(default_factory=list)

    def add(
        self, gate: str, severity: str, message: str, file: str = "", detail: str = ""
    ) -> None:
        self.findings.append(PreflightFinding(gate, severity, message, file, detail))

    @property
    def fails(self) -> list[PreflightFinding]:
        return [f for f in self.findings if f.severity == _SEV_FAIL]

    @property
    def warnings(self) -> list[PreflightFinding]:
        return [f for f in self.findings if f.severity == _SEV_WARNING]

    @property
    def passes(self) -> list[PreflightFinding]:
        return [f for f in self.findings if f.severity == _SEV_PASS]

    @property
    def exit_code(self) -> int:
        # A REAL CI gate failure → non-zero. Tool-absent WARNINGs never block.
        return 1 if self.fails else 0


# ─────────────────────────────────────────────────────────────────────────
# Gate (a) — jscpd copy-paste (publish.py Gate-2b probe-then-run, reused)
# ─────────────────────────────────────────────────────────────────────────


def _resolve_jscpd_cmd() -> list[str] | None:
    """Return the base argv to invoke jscpd, or None if unobtainable.

    Prefers a PATH ``jscpd``; falls back to ``npx --yes jscpd``. Returns None
    when neither jscpd nor npx is on PATH — the caller degrades to WARNING.
    Mirrors publish.py Gate-2b exactly.
    """
    jscpd_bin = shutil.which("jscpd")
    if jscpd_bin:
        return [jscpd_bin]
    npx_bin = shutil.which("npx")
    if npx_bin:
        return [npx_bin, "--yes", "jscpd"]
    return None


def _gate_jscpd(result: PreflightResult) -> None:
    root = result.plugin_path
    base_cmd = _resolve_jscpd_cmd()
    if base_cmd is None:
        result.add(
            "jscpd",
            _SEV_WARNING,
            "jscpd/npx not found — copy-paste check SKIPPED locally. CI's Mega-Linter WILL "
            "enforce it (.jscpd.json threshold); install Node/npx for full local parity. A "
            "clean preflight does NOT guarantee green CI for the copy-paste dimension (#143).",
        )
        return
    # Probe distinguishes 'jscpd unavailable/uninstallable' (WARN) from
    # 'jscpd ran, found dupes' (FAIL) — the publish.py Gate-2b discipline.
    try:
        probe = subprocess.run(
            base_cmd + ["--version"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            "jscpd",
            _SEV_WARNING,
            f"jscpd could not be probed ({type(exc).__name__}) — SKIPPED locally. CI's "
            f"Mega-Linter WILL enforce it (#143).",
        )
        return
    if probe.returncode != 0:
        result.add(
            "jscpd",
            _SEV_WARNING,
            "jscpd could not run (npx fetch/install failed) — SKIPPED locally. CI's "
            "Mega-Linter WILL enforce it; clean preflight != green CI for copy-paste (#143).",
        )
        return
    try:
        # Capture jscpd's (verbose) table output instead of streaming it — the
        # preflight report must stay readable. We only need the exit code: jscpd
        # exits non-zero ONLY when duplication exceeds the .jscpd.json threshold
        # (no config / under threshold → exit 0, advisory), matching publish.py
        # Gate-2b semantics exactly.
        cp = subprocess.run(
            base_cmd + ["."],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_JSCPD_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            "jscpd",
            _SEV_WARNING,
            f"jscpd run could not complete ({type(exc).__name__}) — SKIPPED locally. CI "
            f"still enforces it (#143).",
        )
        return
    if cp.returncode != 0:
        result.add(
            "jscpd",
            _SEV_FAIL,
            "jscpd found copy-paste duplication over the .jscpd.json threshold (parity with "
            "CI Mega-Linter). Reduce duplication or raise the threshold in .jscpd.json.",
            detail=_fail_detail((cp.stdout or "") + "\n" + (cp.stderr or "")),
        )
    else:
        result.add("jscpd", _SEV_PASS, "Copy-paste check passed (no over-threshold duplication).")


# ─────────────────────────────────────────────────────────────────────────
# Gate (b) — actionlint on .github/workflows/*.yml
# ─────────────────────────────────────────────────────────────────────────


def _workflow_yml_paths(root: Path) -> list[Path]:
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(
        p for p in wf_dir.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def _gate_actionlint(result: PreflightResult) -> None:
    root = result.plugin_path
    workflows = _workflow_yml_paths(root)
    if not workflows:
        # No workflows → nothing to lint. Not a defect, not a warning-worthy
        # tool-absence — just a clean no-op.
        result.add("actionlint", _SEV_PASS, "No .github/workflows/*.yml — actionlint not needed.")
        return
    actionlint_bin = shutil.which("actionlint")
    if actionlint_bin is None:
        result.add(
            "actionlint",
            _SEV_WARNING,
            "actionlint not found on PATH — workflow-syntax check SKIPPED locally. CI's Lint "
            "job runs actionlint; install it (https://github.com/rhysd/actionlint) for full "
            "local parity.",
        )
        return
    try:
        proc = subprocess.run(
            [actionlint_bin, *[str(p) for p in workflows]],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_ACTIONLINT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            "actionlint",
            _SEV_WARNING,
            f"actionlint could not run ({type(exc).__name__}) — SKIPPED locally. CI still "
            f"enforces it.",
        )
        return
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip()
        first = detail.splitlines()[0] if detail else "actionlint reported workflow errors"
        result.add(
            "actionlint",
            _SEV_FAIL,
            f"actionlint found workflow errors (parity with CI Lint job): {first}",
            detail=_fail_detail(detail),
        )
    else:
        result.add("actionlint", _SEV_PASS, "Workflow YAML passed actionlint.")


# ─────────────────────────────────────────────────────────────────────────
# Gate (c) — mypy on the plugin's scripts/
# ─────────────────────────────────────────────────────────────────────────


def _gate_mypy(result: PreflightResult) -> None:
    root = result.plugin_path
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        result.add("mypy", _SEV_PASS, "No scripts/ directory — mypy not needed.")
        return
    # An empty scripts/ (no *.py files) is not a type error — it is "nothing to
    # check". mypy exits non-zero with "There are no .py[i] files in directory"
    # in that case, so short-circuit to PASS rather than misreport a FAIL.
    if not any(scripts_dir.rglob("*.py")):
        result.add("mypy", _SEV_PASS, "scripts/ has no .py files — mypy not needed.")
        return
    mypy_bin = shutil.which("mypy")
    if mypy_bin is None:
        result.add(
            "mypy",
            _SEV_WARNING,
            "mypy not found on PATH — type check SKIPPED locally. CI's Lint job runs mypy; "
            "install it for full local parity.",
        )
        return
    # Mirror CPV's own real mypy gate: `mypy scripts/ --ignore-missing-imports`
    # (NOT --strict — the plugin's own CI decides its strictness; this is a
    # parity smoke that catches the type errors CI's Lint job would catch).
    try:
        proc = subprocess.run(
            [mypy_bin, "scripts/", "--ignore-missing-imports"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_MYPY_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            "mypy",
            _SEV_WARNING,
            f"mypy could not run ({type(exc).__name__}) — SKIPPED locally. CI still "
            f"enforces it.",
        )
        return
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip()
        # "There are no .py[i] files in directory" is mypy reporting nothing to
        # check (not a type error) — never a CI gate FAIL.
        if "no .py" in detail.lower() and "files in directory" in detail.lower():
            result.add("mypy", _SEV_PASS, "scripts/ has no checkable .py files — mypy not needed.")
            return
        # mypy prints a trailing "Found N errors …" summary; surface it.
        summary = ""
        for ln in reversed(detail.splitlines()):
            if ln.lower().startswith("found ") and "error" in ln.lower():
                summary = ln.strip()
                break
        if not summary:
            summary = detail.splitlines()[0] if detail else "mypy reported type errors"
        result.add(
            "mypy",
            _SEV_FAIL,
            f"mypy found type errors in scripts/ (parity with CI Lint job): {summary}",
            detail=_fail_detail(detail),
        )
    else:
        result.add("mypy", _SEV_PASS, "Type check passed (mypy scripts/ --ignore-missing-imports).")


# ─────────────────────────────────────────────────────────────────────────
# Gate (d) — `uv sync --extra dev` resolve smoke (#142 Defect-2)
# ─────────────────────────────────────────────────────────────────────────

# Phrases uv prints when the `dev` extra a workflow asks for is not declared.
# We FAIL only on this resolve-error class — any other non-zero exit (network,
# lockfile, build) degrades to WARNING (it is not a CI-parity DEFECT in the
# plugin's metadata, and a dev box may simply be offline).
_DEV_EXTRA_UNDEFINED_MARKERS = (
    "extra `dev` is not defined",
    "extra 'dev' is not defined",
    "extra \"dev\" is not defined",
    "is not defined in",  # uv: "Extra `dev` is not defined in the project's `optional-dependencies` table"
)


def _workflow_requests_dev_extra(root: Path) -> bool:
    """True when any workflow runs ``uv sync --extra dev``."""
    pat = re.compile(r"uv\s+sync\b[^\n]*--extra\s+dev\b")
    for wf in _workflow_yml_paths(root):
        try:
            text = wf.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if pat.search(text):
            return True
    return False


def _gate_uv_sync_dev(result: PreflightResult) -> None:
    root = result.plugin_path
    # Only meaningful when the plugin both has a pyproject AND a workflow that
    # runs `uv sync --extra dev`. A plugin that never asks for the dev extra
    # has nothing to smoke-test here.
    if not (root / "pyproject.toml").is_file():
        result.add("uv-sync-dev", _SEV_PASS, "No pyproject.toml — dev-extra smoke not needed.")
        return
    if not _workflow_requests_dev_extra(root):
        result.add(
            "uv-sync-dev",
            _SEV_PASS,
            "No workflow runs `uv sync --extra dev` — dev-extra smoke not needed.",
        )
        return
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        result.add(
            "uv-sync-dev",
            _SEV_WARNING,
            "uv not found on PATH — `uv sync --extra dev` resolve smoke SKIPPED locally. CI "
            "runs it; install uv for full local parity.",
        )
        return
    # `--frozen --dry-run` resolves the extra WITHOUT mutating the lockfile or
    # downloading packages — it surfaces the "extra dev is not defined" class
    # (a metadata DEFECT) without needing the network.
    try:
        proc = subprocess.run(
            [uv_bin, "sync", "--extra", "dev", "--frozen", "--dry-run"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_UV_SYNC_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            "uv-sync-dev",
            _SEV_WARNING,
            f"`uv sync --extra dev` could not run ({type(exc).__name__}) — SKIPPED locally.",
        )
        return
    if proc.returncode == 0:
        result.add(
            "uv-sync-dev", _SEV_PASS, "`uv sync --extra dev` resolves (dev extra is defined)."
        )
        return
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if any(marker in combined for marker in _DEV_EXTRA_UNDEFINED_MARKERS):
        result.add(
            "uv-sync-dev",
            _SEV_FAIL,
            "`uv sync --extra dev` fails: the `dev` extra is not defined in pyproject.toml "
            "(#142 Defect-2). Add a `[project.optional-dependencies].dev` table (or run "
            "`standardize --fix`).",
            detail=_fail_detail((proc.stdout or "") + "\n" + (proc.stderr or "")),
        )
    else:
        # Some OTHER resolve failure (network, lockfile drift, build) — not a
        # CI-parity DEFECT in the plugin's metadata. Degrade to WARNING.
        first = ""
        for ln in (proc.stderr or proc.stdout or "").splitlines():
            if ln.strip():
                first = ln.strip()
                break
        result.add(
            "uv-sync-dev",
            _SEV_WARNING,
            f"`uv sync --extra dev` did not resolve cleanly, but not due to a missing dev "
            f"extra (likely lockfile/network): {first or 'see uv output'}. SKIPPED as a "
            f"parity DEFECT.",
        )


# ─────────────────────────────────────────────────────────────────────────
# Gate (e) — Mega-Linter sub-linter parity probes (MODE 2 of #137-143)
#
# CI's Mega-Linter container enforces a set of sub-linters declared in
# `.mega-linter.yml`'s `ENABLE_LINTERS` (checkov/trivy/cspell/bandit/shellcheck/
# shfmt), but `validate_plugin` and gates (a)-(d) NEVER reproduce them locally —
# so a clean preflight + publish can still red-CI on one of them. These probes
# add LOCAL visibility ONLY: each runs IFF the plugin's `.mega-linter.yml`
# actually ENABLES that linter (absent file / linter not listed → clean PASS
# "skipped"), tool-on-PATH → run → FAIL on a real error, tool-absent → WARNING.
# They NEVER change the default-enabled Mega-Linter set and NEVER weaken a gate.
# ─────────────────────────────────────────────────────────────────────────

# `.mega-linter.yml` linter id → the `gate` label this preflight reports it as.
# These ids are the exact `ENABLE_LINTERS` entries CPV's `gen_mega_linter_yml`
# emits (generate_plugin_repo.py). A linter NOT in the plugin's enabled list is
# skipped (a clean PASS), so adding an entry here never forces a probe on a
# plugin that didn't opt into that Mega-Linter linter.
_BANDIT_LINTER_ID = "PYTHON_BANDIT"
_SHELLCHECK_LINTER_ID = "BASH_SHELLCHECK"
_SHFMT_LINTER_ID = "BASH_SHFMT"
_CSPELL_LINTER_ID = "SPELL_CSPELL"
_CHECKOV_LINTER_ID = "REPOSITORY_CHECKOV"
_TRIVY_LINTER_ID = "REPOSITORY_TRIVY"

# Match a YAML block-sequence item line `  - <LINTER_ID>` under an
# `ENABLE_LINTERS:` / `ENABLE:` key. re2-safe (no lookaround / backreference):
# anchor the key at the start of a line, the item by its `- ` bullet. We parse
# textually (the rest of the preflight + cpv_ci_parity_checks deliberately avoid
# a pyyaml dependency); an inline-flow list (`ENABLE_LINTERS: [A, B]`) is also
# recognized by _parse_enabled_linters's flow branch.
_ENABLE_KEY_RE = re.compile(r"^(ENABLE_LINTERS|ENABLE)\s*:(.*)$")
_BLOCK_ITEM_RE = re.compile(r"^\s*-\s*([A-Za-z0-9_]+)\s*$")


def _parse_enabled_linters(text: str) -> set[str]:
    """Extract the set of enabled linter ids from `.mega-linter.yml` text.

    Recognizes BOTH the YAML block-sequence form CPV emits::

        ENABLE_LINTERS:
          - PYTHON_BANDIT
          - REPOSITORY_CHECKOV

    AND the inline-flow form ``ENABLE_LINTERS: [PYTHON_BANDIT, REPOSITORY_CHECKOV]``,
    under either the ``ENABLE_LINTERS`` or the ``ENABLE`` key. Pure textual
    parse (no pyyaml) — lines are walked, and the block list ends at the first
    new top-level key or a non-item line. Comment lines (``# …``) and blanks
    inside the block are skipped, so a commented-out linter is NOT counted as
    enabled. Returns an empty set when no enable key is present.
    """
    enabled: set[str] = set()
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        m = _ENABLE_KEY_RE.match(lines[i])
        if not m:
            i += 1
            continue
        inline = m.group(2).strip()
        if inline.startswith("[") and inline.endswith("]"):
            # Inline-flow list: ENABLE_LINTERS: [A, B, C]
            for tok in inline[1:-1].split(","):
                tok = tok.strip().strip("'\"")
                if tok:
                    enabled.add(tok)
            i += 1
            continue
        if inline and not inline.startswith("#"):
            # `ENABLE_LINTERS: SOMETHING` on one line (single scalar) — rare,
            # but treat the bare token as one enabled linter.
            enabled.add(inline.strip("'\""))
            i += 1
            continue
        # Block-sequence form — consume the indented `- ITEM` lines that follow.
        i += 1
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                i += 1
                continue
            item = _BLOCK_ITEM_RE.match(line)
            if item:
                enabled.add(item.group(1))
                i += 1
                continue
            # A non-item, non-comment line. If it is a new top-level key the
            # block ended; otherwise stop scanning this block conservatively.
            break
    return enabled


def _megalinter_enabled_linters(root: Path) -> set[str] | None:
    """Return the enabled-linter id set from ``<root>/.mega-linter.yml``.

    Returns ``None`` when the file is absent (the caller treats that as "no
    Mega-Linter config → every probe is a clean skip"). A present-but-unreadable
    or enable-key-less file yields an empty set (no linter enabled → every probe
    skips), which is the conservative non-blocking direction.
    """
    cfg = root / ".mega-linter.yml"
    if not cfg.is_file():
        return None
    try:
        text = cfg.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()
    return _parse_enabled_linters(text)


def _gate_megalinter_tool(
    result: PreflightResult,
    *,
    gate: str,
    linter_id: str,
    tool_name: str,
    install_hint: str,
    build_argv: Callable[[str, Path], list[str]],
    enabled: set[str] | None,
) -> None:
    """One Mega-Linter sub-linter parity probe, following the gate contract.

    The exact ``_gate_actionlint`` / ``_gate_mypy`` shape, plus the enable gate:

    * ``.mega-linter.yml`` does NOT enable ``linter_id`` (or has no config at
      all) → clean PASS "linter not enabled, skipped". This is what keeps the
      probe from ever forcing a tool on a plugin that did not opt into it, and
      from changing the default-enabled Mega-Linter set.
    * linter enabled, ``tool_name`` ABSENT on PATH → non-blocking WARNING.
    * linter enabled, tool present, ran clean → PASS.
    * linter enabled, tool present, found a real error → FAIL (first error line).

    ``build_argv(tool_bin, root)`` returns the full argv to run the linter on
    the plugin's relevant files. A FAIL surfaces the first non-empty output line
    so the agent sees WHAT failed without the preflight report drowning in the
    linter's full output.
    """
    if enabled is None or linter_id not in enabled:
        result.add(
            gate,
            _SEV_PASS,
            f"{tool_name} ({linter_id}) not enabled in .mega-linter.yml — skipped.",
        )
        return
    tool_bin = shutil.which(tool_name)
    if tool_bin is None:
        result.add(
            gate,
            _SEV_WARNING,
            f"{tool_name} not found on PATH — Mega-Linter {linter_id} check SKIPPED "
            f"locally. CI's Mega-Linter WILL enforce it; install {tool_name} "
            f"({install_hint}) for full local parity.",
        )
        return
    try:
        proc = subprocess.run(
            build_argv(tool_bin, result.plugin_path),
            cwd=str(result.plugin_path),
            capture_output=True,
            text=True,
            timeout=_MEGALINTER_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.add(
            gate,
            _SEV_WARNING,
            f"{tool_name} could not run ({type(exc).__name__}) — SKIPPED locally. "
            f"CI's Mega-Linter still enforces {linter_id}.",
        )
        return
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip()
        first = ""
        for ln in detail.splitlines():
            if ln.strip():
                first = ln.strip()
                break
        if not first:
            first = f"{tool_name} reported errors"
        result.add(
            gate,
            _SEV_FAIL,
            f"{tool_name} found errors (parity with CI Mega-Linter {linter_id}): {first}",
            detail=_fail_detail(detail),
        )
    else:
        result.add(gate, _SEV_PASS, f"{tool_name} passed (Mega-Linter {linter_id} parity).")


def _argv_cspell(tool_bin: str, root: Path) -> list[str]:
    # Mirror Mega-Linter SPELL_CSPELL: spell-check the tree. `--no-progress`
    # keeps output terse; `--no-summary` avoids a non-error trailer; cspell exits
    # non-zero when it finds an unknown word. Invoked only when the plugin ships
    # a cspell config (see _gate_cspell) — cspell auto-discovers it, and CI's
    # Mega-Linter cspell reads that SAME file, so the two runs agree by
    # construction. Without one there is no dictionary to reproduce CI with, and
    # a bare cspell on its default dictionary would false-block on ordinary tech
    # terms; that case is a DEFECT reported by _gate_cspell, not a probe run.
    _ = root
    return [tool_bin, "lint", "--no-progress", "--no-summary", "."]


# cspell config files a local `cspell lint` auto-discovers. Their presence is the
# gate for the cspell probe (see _gate_cspell), and — because CI's Mega-Linter
# cspell auto-discovers the SAME file — their presence is ALSO what makes local
# and CI agree on which words are known.
#
# MUST STAY IN SYNC with `standardize_plugin._CSPELL_CONFIG_NAMES`, the tuple
# standardize provisions against; a drift means standardize writes a second,
# ambiguous config next to one this probe already recognized.
# `tests/test_cspell_parity.py` pins the two tuples equal.
_CSPELL_CONFIG_NAMES = (
    ".cspell.json",
    "cspell.json",
    ".cspell.jsonc",
    "cspell.jsonc",
    ".cspell.config.json",
    "cspell.config.json",
    "cspell.config.yaml",
    "cspell.config.yml",
    "cspell.config.js",
    "cspell.config.cjs",
    "cspell.config.mjs",
    ".cspell.yaml",
    ".cspell.yml",
    "cspell.yaml",
    "cspell.yml",
    ".cspell-words.txt",
    "project-words.txt",
)


def _plugin_has_cspell_config(root: Path) -> bool:
    """True when the plugin ships a cspell config/dictionary a local `cspell lint`
    auto-discovers (so a local run reproduces the plugin's intended spell-check)."""
    if any((root / name).is_file() for name in _CSPELL_CONFIG_NAMES):
        return True
    return (root / ".cspell").is_dir()


def _gate_cspell(result: PreflightResult, enabled: set[str] | None) -> None:
    """Mega-Linter SPELL_CSPELL parity probe.

    RC-3 — this probe used to SKIP (a non-blocking WARNING) whenever the plugin
    shipped no cspell config, and CPV's canonical `.mega-linter.yml` enables
    SPELL_CSPELL while emitting no dictionary. That combination WAS the parity
    hole: the author's local preflight said GREEN and GitHub CI then said RED on
    every plugin-specific proper noun, with no way to see it beforehand.

    The three cases, and why each severity is what it is:

    * SPELL_CSPELL not enabled (or no `.mega-linter.yml`) → clean PASS "skipped".
      CI does not run cspell, so there is nothing to reproduce.
    * enabled, but the plugin ships NO cspell config → **FAIL**. This is a real,
      static, offline-detectable CI-parity DEFECT of exactly the CIP-3 kind
      ("the canonical CI enables a gate whose config the plugin never shipped"):
      CI's cspell has no dictionary for the plugin's own name / agents / skills /
      commands, so it hard-errors on them. It is NOT a probe run — the tool is
      deliberately never invoked here, because a bare local cspell on its default
      dictionary would false-block on ordinary tech terms. The remediation is one
      mechanical command (`standardize --fix`), which provisions `.cspell.json`.
    * enabled + a config present → RUN cspell for real. cspell auto-discovers the
      config and CI's Mega-Linter cspell reads that SAME file, so the local run
      now faithfully reproduces CI: a word the dictionary accepts passes on both
      sides, a word it does not fails on both. A genuine misspelling FAILs here,
      before the push.

    A missing cspell BINARY still degrades to a non-blocking WARNING inside
    `_gate_megalinter_tool` — an agent box without cspell must never be blocked.
    """
    if enabled is None or _CSPELL_LINTER_ID not in enabled:
        result.add(
            "cspell",
            _SEV_PASS,
            f"cspell ({_CSPELL_LINTER_ID}) not enabled in .mega-linter.yml — skipped.",
        )
        return
    if not _plugin_has_cspell_config(result.plugin_path):
        result.add(
            "cspell",
            _SEV_FAIL,
            ".mega-linter.yml ENABLES cspell (SPELL_CSPELL) but the plugin ships no "
            "cspell dictionary (.cspell.json / project-words.txt / .cspell/). CI's "
            "cspell will hard-error on this plugin's own proper nouns (its name, its "
            "agent/skill/command names, its project vocabulary) — it has no dictionary "
            "to read. This is the local-GREEN / CI-RED parity hole: run "
            "`standardize --fix` to provision the canonical .cspell.json, which BOTH "
            "this probe and CI's Mega-Linter cspell then read.",
        )
        return
    _gate_megalinter_tool(
        result,
        gate="cspell",
        linter_id=_CSPELL_LINTER_ID,
        tool_name="cspell",
        install_hint="npm i -g cspell",
        build_argv=_argv_cspell,
        enabled=enabled,
    )


def _argv_checkov(tool_bin: str, root: Path) -> list[str]:
    # Mirror Mega-Linter REPOSITORY_CHECKOV: scan the directory. `--compact`
    # keeps the table small; checkov exits non-zero on a failed policy check.
    _ = root
    return [tool_bin, "--directory", ".", "--compact", "--quiet"]


def _argv_trivy(tool_bin: str, root: Path) -> list[str]:
    # Mirror Mega-Linter REPOSITORY_TRIVY: filesystem scan. `--exit-code 1` makes
    # trivy exit non-zero when a finding at/above the severity threshold exists
    # (default behaviour for misconfig/secret findings is exit 0, so the explicit
    # flag is what surfaces a real failure the CI gate would also surface).
    _ = root
    return [tool_bin, "fs", "--exit-code", "1", "--no-progress", "."]


def _argv_bandit(tool_bin: str, root: Path) -> list[str]:
    # Mirror Mega-Linter PYTHON_BANDIT on the plugin's scripts/. `-r` recurses;
    # `-q` quiet; `-ll` filters to MEDIUM+ severity. The `-ll` filter is
    # load-bearing for the degrade-gracefully "never false-block" contract: the
    # canonical generated `publish.py` (which IS CI-green) produces ~50
    # LOW-severity B404/B603 "subprocess call" findings that Mega-Linter's
    # PYTHON_BANDIT does NOT block on (no adopter CI failure was ever traced to
    # bandit — only checkov/trivy/cspell). A bare `bandit -r` exits non-zero on
    # those LOW findings → it would FALSE-BLOCK a provably-CI-green plugin. `-ll`
    # restricts the FAIL to MEDIUM+ severity (a real issue — e.g. a `shell=True`
    # injection, B602), which is what CI actually fails on.
    _ = root
    return [tool_bin, "-r", "scripts/", "-q", "-ll"]


def _shell_script_paths(root: Path) -> list[str]:
    """Return the plugin-relative `*.sh`/`*.bash` paths shellcheck/shfmt lint.

    Skips genuinely-unshipped paths — gitignored AND untracked (issue #176). A
    CI checkout of the published artifact never contains those files (e.g. a
    `.gitignore`d `downloads_dev/*.sh`), so linting them locally is a parity bug:
    CI would never see them, yet the local preflight FAILs on them. Uses
    `gitignored_unshipped_paths` (git-accurate) so a TRACKED+gitignored file —
    which still ships in the git archive — is NOT skipped and stays linted; and
    when git is unavailable nothing is skipped on gitignore grounds (the present
    tree IS the artifact).
    """
    try:
        from cpv_validation_common import (  # noqa: PLC0415
            gitignored_unshipped_paths,
            path_is_unshipped,
        )

        unshipped: set[str] | None = gitignored_unshipped_paths(root)
    except ImportError:
        unshipped = None
        path_is_unshipped = None  # type: ignore[assignment]
    paths: list[str] = []
    for pat in ("*.sh", "*.bash"):
        for p in sorted(root.rglob(pat)):
            if not p.is_file():
                continue
            rel_posix = p.relative_to(root).as_posix()
            if unshipped is not None and path_is_unshipped is not None and path_is_unshipped(rel_posix, unshipped):
                continue
            paths.append(str(p.relative_to(root)))
    return paths


def _gate_shellcheck(result: PreflightResult, enabled: set[str] | None) -> None:
    """Mega-Linter BASH_SHELLCHECK parity probe.

    Like the generic probe, but shellcheck needs an explicit file list (it has
    no recursive directory mode). When the linter IS enabled but the plugin
    ships no shell scripts, that is a clean PASS (nothing to lint), not a tool
    invocation.
    """
    if enabled is None or _SHELLCHECK_LINTER_ID not in enabled:
        result.add(
            "shellcheck",
            _SEV_PASS,
            f"shellcheck ({_SHELLCHECK_LINTER_ID}) not enabled in .mega-linter.yml — skipped.",
        )
        return
    scripts = _shell_script_paths(result.plugin_path)
    if not scripts:
        result.add(
            "shellcheck",
            _SEV_PASS,
            "No *.sh/*.bash files — shellcheck not needed (Mega-Linter parity).",
        )
        return
    _gate_megalinter_tool(
        result,
        gate="shellcheck",
        linter_id=_SHELLCHECK_LINTER_ID,
        tool_name="shellcheck",
        install_hint="https://github.com/koalaman/shellcheck",
        build_argv=lambda tool_bin, _root: [tool_bin, *scripts],
        enabled=enabled,
    )


def _gate_shfmt(result: PreflightResult, enabled: set[str] | None) -> None:
    """Mega-Linter BASH_SHFMT parity probe (`shfmt -d` = report diffs)."""
    if enabled is None or _SHFMT_LINTER_ID not in enabled:
        result.add(
            "shfmt",
            _SEV_PASS,
            f"shfmt ({_SHFMT_LINTER_ID}) not enabled in .mega-linter.yml — skipped.",
        )
        return
    scripts = _shell_script_paths(result.plugin_path)
    if not scripts:
        result.add(
            "shfmt",
            _SEV_PASS,
            "No *.sh/*.bash files — shfmt not needed (Mega-Linter parity).",
        )
        return
    _gate_megalinter_tool(
        result,
        gate="shfmt",
        linter_id=_SHFMT_LINTER_ID,
        tool_name="shfmt",
        install_hint="https://github.com/mvdan/sh",
        # `-d` prints a diff and exits non-zero when a file is not formatted.
        build_argv=lambda tool_bin, _root: [tool_bin, "-d", *scripts],
        enabled=enabled,
    )


def _gate_megalinter(result: PreflightResult) -> None:
    """Run every Mega-Linter sub-linter parity probe, gated on `.mega-linter.yml`.

    Reads the enabled-linter set ONCE, then runs each probe — each of which is a
    clean PASS "skipped" when its linter is not enabled (so a plugin without a
    `.mega-linter.yml`, or one that disabled checkov/trivy, never draws a probe).
    """
    enabled = _megalinter_enabled_linters(result.plugin_path)
    _gate_cspell(result, enabled)
    _gate_megalinter_tool(
        result,
        gate="checkov",
        linter_id=_CHECKOV_LINTER_ID,
        tool_name="checkov",
        install_hint="pip install checkov",
        build_argv=_argv_checkov,
        enabled=enabled,
    )
    _gate_megalinter_tool(
        result,
        gate="trivy",
        linter_id=_TRIVY_LINTER_ID,
        tool_name="trivy",
        install_hint="https://aquasecurity.github.io/trivy",
        build_argv=_argv_trivy,
        enabled=enabled,
    )
    _gate_megalinter_tool(
        result,
        gate="bandit",
        linter_id=_BANDIT_LINTER_ID,
        tool_name="bandit",
        install_hint="pip install bandit",
        build_argv=_argv_bandit,
        enabled=enabled,
    )
    _gate_shellcheck(result, enabled)
    _gate_shfmt(result, enabled)


# ─────────────────────────────────────────────────────────────────────────
# Gate (f) — the static CI-parity checks (CIP-1..N, N derived — never hardcoded)
# ─────────────────────────────────────────────────────────────────────────

# Every `"CIP-<n>"` finding-code literal in cpv_ci_parity_checks. The ids are
# DERIVED from that module's source rather than hardcoded here, because a
# hardcoded range is exactly what went stale: this PASS message kept announcing
# the pre-CIP-7 range after CIP-7 (RC-1) and CIP-8 (RC-9) had shipped, so a user
# was told SIX checks ran when EIGHT did. Adding a CIP-9 now updates the message
# automatically — and a test asserts no literal range survives in this file.
_CIP_CODE_LITERAL_RE = re.compile(r'"CIP-(\d+)"')


def _cip_check_ids() -> list[int]:
    """The CIP check numbers ``cpv_ci_parity_checks`` actually implements.

    Derived by reading that module's source for its ``"CIP-<n>"`` finding-code
    literals. Returns [] when the source is unavailable (a zipped/compiled
    install) — the caller then degrades to a count-free message rather than
    printing a number it cannot stand behind.
    """
    try:
        source = inspect.getsource(cpv_ci_parity_checks)
    except (OSError, TypeError):
        return []
    return sorted({int(n) for n in _CIP_CODE_LITERAL_RE.findall(source)})


def _cip_all_passed_message() -> str:
    """The PASS message for a parity-clean tree, with the DERIVED check range."""
    ids = _cip_check_ids()
    if not ids:
        return "All static CI-parity checks passed."
    return f"All {len(ids)} static CI-parity checks (CIP-{ids[0]}..{ids[-1]}) passed."


def _gate_static_checks(result: PreflightResult) -> None:
    cip_findings: list[ParityFinding] = check_ci_parity(result.plugin_path)
    if not cip_findings:
        result.add("ci-parity", _SEV_PASS, _cip_all_passed_message())
        return
    for f in cip_findings:
        severity = _SEV_FAIL if f.severity in _CIP_SEVERITY_IS_BLOCKING else _SEV_WARNING
        result.add(f.check_id, severity, f.message, f.file)


# ─────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────


def run_ci_preflight(plugin_path: Path | str, *, strict: bool = False) -> PreflightResult:
    """Run the full CI-parity preflight against ``plugin_path``.

    Runs the gates IN ORDER (jscpd → actionlint → mypy → uv-sync-dev →
    Mega-Linter sub-linter probes → static CIP checks). Tool-absence ALWAYS
    degrades to a WARNING (never a FAIL), so a dev box missing npx / actionlint
    / mypy / uv / checkov / trivy / cspell / bandit / shellcheck / shfmt is
    never false-blocked. Each Mega-Linter probe also self-skips (clean PASS)
    when the plugin's ``.mega-linter.yml`` does not enable that linter, so the
    probes never change the default-enabled Mega-Linter set. The returned
    ``PreflightResult.exit_code`` is non-zero ONLY when a real CI gate would
    fail (over-threshold duplication, actionlint/mypy error, dev-extra resolve
    failure, an enabled Mega-Linter sub-linter that ran and found a real error,
    or a CIP MAJOR/MINOR static defect).

    ``strict`` is accepted for interface symmetry with the validators; it does
    not currently change gate behaviour (every gate already runs at CI parity),
    but it is threaded into the result for callers/reporting.
    """
    result = PreflightResult(plugin_path=Path(plugin_path), strict=strict)
    _gate_jscpd(result)
    _gate_actionlint(result)
    _gate_mypy(result)
    _gate_uv_sync_dev(result)
    _gate_megalinter(result)
    _gate_static_checks(result)
    return result


# ─────────────────────────────────────────────────────────────────────────
# Reporting + CLI entry point (wired into remote_validation.py as ci-preflight)
# ─────────────────────────────────────────────────────────────────────────


def _print_report(result: PreflightResult) -> None:
    """Print a severity-grouped preflight report to stdout."""
    print("=" * 72)
    print(f"CI-PARITY PREFLIGHT — {result.plugin_path}")
    print("=" * 72)
    n_fail = len(result.fails)
    n_warn = len(result.warnings)
    n_pass = len(result.passes)

    if result.fails:
        print(f"\nFAIL ({n_fail}) — these gates would fail GitHub CI:")
        for f in result.fails:
            loc = f" [{f.file}]" if f.file else ""
            print(f"  ✗ {f.gate}{loc}: {f.message}")
            # Issue #183 — echo the failing tool's OWN output. Only the FAIL set
            # gets it: those are the findings the reader must act on, and this is
            # the only place the diagnostics exist (the gate captured them, and
            # for mypy the obvious local re-run resolves a different environment
            # and reports success). PASS/WARNING stay one-line summaries.
            if f.detail:
                for ln in f.detail.splitlines():
                    print(f"      {ln}")
    if result.warnings:
        print(f"\nWARNING ({n_warn}) — tool absent / advisory (does NOT block):")
        for f in result.warnings:
            loc = f" [{f.file}]" if f.file else ""
            print(f"  ! {f.gate}{loc}: {f.message}")
    if result.passes:
        print(f"\nPASS ({n_pass}):")
        for f in result.passes:
            print(f"  ✓ {f.gate}: {f.message}")

    print("\n" + "-" * 72)
    verdict = "PARITY-CLEAN" if result.exit_code == 0 else "CI WOULD FAIL"
    print(f"VERDICT: {verdict}  (FAIL={n_fail}  WARNING={n_warn}  PASS={n_pass})")
    if result.exit_code == 0 and result.warnings:
        print(
            "Note: WARNINGs mean a local tool was absent — CI still enforces those gates. "
            "Install the tools for full local parity."
        )
    print("-" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cpv-remote-validate ci-preflight",
        description=(
            "Local CI-parity preflight — run the jscpd / actionlint / mypy / "
            "`uv sync --extra dev` / Mega-Linter sub-linter (cspell / checkov / trivy / "
            "bandit / shellcheck / shfmt) / static-CIP gates that `validate_plugin --strict` "
            "omits but the adopting plugin's GitHub-CI ci.yml Lint + Mega-Linter jobs run. "
            "Each Mega-Linter probe runs only when `.mega-linter.yml` enables that linter "
            "(never changes the enabled set). Tool-absent degrades to WARNING (never blocks); "
            "a real CI gate failure exits non-zero."
        ),
    )
    parser.add_argument("target", nargs="?", help="Path to the plugin directory")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Accepted for symmetry with the validators (gates already run at CI parity).",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Save the full report to FILE (the summary still prints to stdout).",
    )
    args = parser.parse_args()

    if not args.target:
        parser.error("a target plugin path is required")
    target = Path(args.target).resolve()
    if not target.exists():
        print(f"Error: {target} does not exist", file=sys.stderr)
        return 1
    if not target.is_dir():
        print(f"Error: {target} is not a directory", file=sys.stderr)
        return 1

    result = run_ci_preflight(target, strict=args.strict)
    _print_report(result)

    if args.report:
        try:
            import io

            buf = io.StringIO()
            _stdout = sys.stdout
            sys.stdout = buf
            try:
                _print_report(result)
            finally:
                sys.stdout = _stdout
            Path(args.report).write_text(buf.getvalue(), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: could not write report to {args.report}: {exc}", file=sys.stderr)

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
