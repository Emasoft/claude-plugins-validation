#!/usr/bin/env python3
"""Local CI-parity preflight — run the gates ``validate_plugin --strict`` omits.

The #137-143 family all share one root cause: a fixer/upgrade agent declares
DONE on ``validate_plugin --strict``, which does NOT run the jscpd / actionlint
/ mypy / ``uv sync --extra dev`` gates the adopting plugin's GitHub-CI
``ci.yml`` Lint job runs. A canonical upgrade that is locally clean therefore
still red-CIs. This preflight is the missing LOCAL mirror of those gates, so an
agent can prove CI-parity BEFORE declaring DONE (or before a real publish).

It runs, IN ORDER, the five parity gates ``validate_plugin`` omits:

* **(a) jscpd copy-paste** — reusing the ``publish.py`` Gate-2b probe-then-run
  pattern (``shutil.which`` jscpd / npx, ``--version`` probe, BLOCK on
  over-threshold, **degrade to WARNING when jscpd/npx is absent**).
* **(b) actionlint** on ``.github/workflows/*.yml`` (degrade-WARNING if absent).
* **(c) mypy** on the plugin's ``scripts/`` (degrade-WARNING if absent).
* **(d) ``uv sync --extra dev``** resolve smoke (degrade-WARNING if no uv; FAIL
  only on an actual "extra dev is not defined"-class resolve error).
* **(e) the five static CI-parity checks** from ``cpv_ci_parity_checks``.

THE DEGRADE-GRACEFULLY CONTRACT (the #129 pattern applied to the whole
preflight): a TOOL being ABSENT ALWAYS degrades to a non-blocking WARNING — it
NEVER false-blocks a fixer (a dev box without npx / actionlint must not stop the
agent). The ONLY non-zero exit comes from a REAL over-threshold duplication, a
static defect, an actionlint error, a mypy type error, or a dev-extra resolve
failure — i.e. something CI would also fail on.

Exit / return contract:
    run_ci_preflight(plugin_path, *, strict=False) -> PreflightResult
    PreflightResult.exit_code: 0 when parity-clean (only WARNING/PASS), non-zero
    when a real CI gate would fail.

Run via the launcher:
    cpv-remote-validate ci-preflight <plugin-path>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class PreflightFinding:
    """One preflight gate outcome.

    Attributes:
        gate: The gate label (``"jscpd"`` / ``"actionlint"`` / ``"mypy"`` /
            ``"uv-sync-dev"`` / a ``"CIP-N"`` id).
        severity: ``"FAIL"`` (a real CI gate failure → non-zero exit),
            ``"WARNING"`` (tool-absent / advisory → never blocks), or
            ``"PASS"`` (the gate ran clean).
        message: A human-readable description.
        file: An optional plugin-relative path the finding is about.
    """

    gate: str
    severity: str
    message: str
    file: str = ""


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

    def add(self, gate: str, severity: str, message: str, file: str = "") -> None:
        self.findings.append(PreflightFinding(gate, severity, message, file))

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
    import re

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
# Gate (e) — the five static CI-parity checks
# ─────────────────────────────────────────────────────────────────────────


def _gate_static_checks(result: PreflightResult) -> None:
    cip_findings: list[ParityFinding] = check_ci_parity(result.plugin_path)
    if not cip_findings:
        result.add("ci-parity", _SEV_PASS, "All five static CI-parity checks (CIP-1..5) passed.")
        return
    for f in cip_findings:
        severity = _SEV_FAIL if f.severity in _CIP_SEVERITY_IS_BLOCKING else _SEV_WARNING
        result.add(f.check_id, severity, f.message, f.file)


# ─────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────


def run_ci_preflight(plugin_path: Path | str, *, strict: bool = False) -> PreflightResult:
    """Run the full CI-parity preflight against ``plugin_path``.

    Runs the five gates IN ORDER (jscpd → actionlint → mypy → uv-sync-dev →
    static CIP checks). Tool-absence ALWAYS degrades to a WARNING (never a FAIL),
    so a dev box missing npx / actionlint / mypy / uv is never false-blocked.
    The returned ``PreflightResult.exit_code`` is non-zero ONLY when a real CI
    gate would fail (over-threshold duplication, actionlint/mypy error,
    dev-extra resolve failure, or a CIP MAJOR/MINOR static defect).

    ``strict`` is accepted for interface symmetry with the validators; it does
    not currently change gate behaviour (every gate already runs at CI parity),
    but it is threaded into the result for callers/reporting.
    """
    result = PreflightResult(plugin_path=Path(plugin_path), strict=strict)
    _gate_jscpd(result)
    _gate_actionlint(result)
    _gate_mypy(result)
    _gate_uv_sync_dev(result)
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
            "`uv sync --extra dev` / static-CIP gates that `validate_plugin --strict` "
            "omits but the adopting plugin's GitHub-CI ci.yml Lint job runs. "
            "Tool-absent degrades to WARNING (never blocks); a real CI gate failure exits "
            "non-zero."
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
