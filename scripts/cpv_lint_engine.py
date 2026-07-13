#!/usr/bin/env python3
"""Single-source-of-truth lint engine for CPV.

Replaces the standalone `lint_files.py` orchestrator and the inline lint
pieces of `validate_plugin.py:validate_scripts()`. The engine

  - walks the gitignore-filtered tree once via `GitignoreFilter`,
  - resolves every linter through `cpv_validation_common.resolve_tool_command`
    so missing tools auto-route via uvx / bunx / npx / docker without
    polluting the user's machine,
  - emits findings into a `ValidationReport` so downstream consumers
    (validate_plugin / publish gates / pre-push hooks) get a uniform
    severity surface (CRITICAL / MAJOR / MINOR / WARNING / INFO).

Strict-by-default: a missing linter for ANY detected language raises a
MAJOR finding and `lint_repo()` returns False. Pass
`strict_missing_tools=False` to demote those to WARNING for local dev.

Public API:

    detect_languages(plugin_root, *, gi=None) -> dict[str, list[Path]]
    lint_repo(plugin_root, report, *, strict_missing_tools=True,
              languages=None) -> bool
    lint_python(repo_root, files, report, *, strict_missing_tools=True) -> bool
    lint_javascript(...)  lint_shell(...)  lint_go(...)  lint_rust(...)
    lint_markdown(...)    lint_json(...)   lint_yaml(...) lint_dockerfile(...)
    lint_xml(...)         lint_css(...)    lint_html(...) lint_sql(...)
    lint_toml(...)        lint_powershell(...)

Each per-language helper returns True iff no MAJOR/CRITICAL finding was
added for files in that language. Tests can mock `_resolve` to simulate
unavailable tools without touching the host environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

# Local helpers — the scripts/ dir is on sys.path when validate_plugin.py
# imports us; tests insert it explicitly via conftest.
from cpv_scanner_cache import (
    CacheKey,
    ScannerCache,
    get_scanner_version,
    sha256_of_args,
    tree_merkle,
)
from cpv_validation_common import (
    ValidationReport,
    ValidationResult,
    normalize_level,
    resolve_tool_command,
)
from gitignore_filter import GitignoreFilter

# markdownlint-cli2 finding shape: "<path>.md[x]:<line>[:<col>] <severity> MD<NNN>".
# Anything that does not match (uv installer chatter "Resolving dependencies",
# "Resolved, downloaded and extracted N", "Saved lockfile", etc.) is NOT a
# markdownlint finding and must not leak through as a NIT report entry.
#
# The extension alternation MUST cover every suffix `detect_languages`
# buckets into "markdown" — that is `*.md` AND `*.mdx` (see the collect()
# call below). A bare `\.md:` anchor silently DROPPED every finding on a
# `.mdx` file (the char after ".md" is "x", not ":"), so markdownlint
# complaints about `.mdx` sources never reached the report when any `.md`
# finding also surfaced (the raw-output safety net only fires when NOTHING
# matched). `\.mdx?:` matches both `.md:` and `.mdx:`; keep it in sync with
# the markdown collect() patterns if a new markdown suffix is ever added.
_MARKDOWNLINT_FINDING_RE = re.compile(r"\.mdx?:\d+(?::\d+)?\s+(?:error|warning|info)\s+MD\d+")

# htmlhint (and some other CLIs) colorize stdout with ANSI SGR escapes; strip
# them so captured finding lines are readable and prefix-matching (banner /
# summary detection) is not defeated by a leading color code (issue #132).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Content fingerprint of THIS lint-engine module, folded into every lint cache
# key (see _build_cache_key). The lint cache is content + external-tool-version
# keyed, but CPV's own output-PROCESSING logic (banner filtering, finding
# parsing, severity mapping) lives here — a fix to it (e.g. issue #132's
# htmlhint banner strip) would otherwise be MASKED for warm-cache users until
# the file content or the external tool version happened to change. Hashing the
# module invalidates the lint cache exactly when this logic changes (precise —
# unlike folding the plugin version, which would bump every release regardless
# of whether the lint logic actually moved).
try:
    _LINT_ENGINE_CODE_REV = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
except OSError:
    _LINT_ENGINE_CODE_REV = "unknown"

# Issue #113: MD004 (ul-style) in markdownlint's default `consistent` mode is
# poisoned by a stray MINORITY marker. A hard-wrapped prose line that happens to
# begin `+ ` (or `* `) is parsed by CommonMark as a list item, which sets the
# file's expected ul-style — then EVERY healthy bullet of the majority style is
# flagged, producing N near-identical NITs that point at the healthy bullets,
# not the one stray marker. We collapse repeated same-signature MD004 findings
# WITHIN a single file to one explanatory NIT (the inconsistency still surfaces
# ONCE — a visible NIT, never suppressed — so a genuine mixed-marker file is
# still reported). The dedup key is (file path, `[Expected: X; Actual: Y]`
# signature). Pinning MD004 to a single style was rejected: it would flag every
# `*`-style file CPV lints (its own + third-party) — a style imposition, where
# `consistent` mode (the markdownlint default) correctly leaves each file's
# marker choice alone and only flags genuine within-file inconsistency.
_MD004_DEDUP_RE = re.compile(
    r"^(?P<file>.+?\.mdx?):\d+(?::\d+)?\s+(?:error|warning|info)\s+MD004/ul-style"
    r".*?(?P<sig>\[Expected:[^\]]*\])"
)

# Tool/environment CRASH signatures (issue #84). When markdownlint-cli2 is
# launched via `bunx`/`npx` and its ESM imports fail (e.g. `bunx` resolves the
# package up into an unrelated ANCESTOR `package.json` with a broken
# `node_modules`), markdownlint never runs and emits a Node crash stack instead
# of MD### findings. That is an ENVIRONMENT failure, not a lint violation — it
# must surface as a WARNING (never blocks `--strict`), not a NIT (which does).
# This regex is consulted ONLY in the `surfaced == 0` fallback below, so a real
# MD### finding line (which matches `_MARKDOWNLINT_FINDING_RE`, surfaced > 0) can
# never reach — and so can never be suppressed by — this discriminator.
_MARKDOWNLINT_TOOL_CRASH_RE = re.compile(
    r"ERR_MODULE_NOT_FOUND|ERR_REQUIRE_ESM|Cannot find (?:module|package)"
    r"|MODULE_NOT_FOUND|node:internal/|ERR_PACKAGE_PATH_NOT_EXPORTED"
    r"|Error \[ERR_|npm error|command not found|No such file or directory",
    re.IGNORECASE,
)

# Issue #129 (reopened): xmllint now resolves via the docker fallback on a bare
# CI runner (no native `xmllint`, docker present). `docker run` auto-pulls the
# alpine image and its registry/daemon progress lands on the SAME stderr as
# xmllint's diagnostics — so `_lint_xml` must triage stderr into three kinds:
# container/tool INFRASTRUCTURE noise (never a finding), a NON-FATAL xmllint
# WARNING (surface, never block), and a GENUINE xmllint validation ERROR (a real
# MAJOR). These three regexes do that triage; they classify ONLY xmllint stderr
# and are consulted nowhere else.

# (a) Docker / registry / daemon output emitted by `docker run` while pulling
# the image (and a couple of daemon-connectivity failures). NONE of these is a
# statement about the user's XML. The final alternative matches a bare layer-id
# progress line ("<12+hex>: Pulling fs layer", "<hash>: Download complete",
# "<hash>: Already exists", …) — a 12+ hex-char id at the start of the line
# followed by ':' — which is how Docker reports per-layer pull progress.
_XMLLINT_INFRA_NOISE_RE = re.compile(
    r"Unable to find image"
    r"|Pulling from"
    r"|Pulling fs layer"
    r"|Verifying Checksum"
    r"|Download complete"
    r"|Downloading"
    r"|Already exists"
    r"|Pull complete"
    r"|Extracting"
    r"|Waiting"
    r"|Retrying"
    r"|Digest:"
    r"|Status:"
    r"|docker:"
    r"|Cannot connect to the Docker daemon"
    r"|error during connect"
    r"|^[0-9a-f]{12,}:\s",
    re.IGNORECASE,
)

# (b1) A GENUINE xmllint validation diagnostic. xmllint's real `--noout`
# findings reference the file and a line number and carry one of these markers
# (`f.xml:12: parser error : Opening and ending tag mismatch`, `Premature end of
# data`, a bare `: error :`, …). These are the only lines that fire a MAJOR.
_XMLLINT_REAL_ERROR_RE = re.compile(
    r"parser error"
    r"|:\s*error\b"
    r"|\berror:"
    r"|Opening and ending tag mismatch"
    r"|Premature end of data"
    r"|Extra content at the end"
    r"|Start tag expected"
    r"|xmlParseEntityRef"
    r"|not well-formed",
    re.IGNORECASE,
)

# (b2) A NON-FATAL xmllint WARNING — most commonly an external DTD/entity that
# an offline runner could not fetch (`warning: failed to load external entity
# "…/pom.xml"`). The document is still well-formed; this is reported but never
# blocks. Checked AFTER the real-error regex so a line carrying both an error
# and the word "warning" is still treated as the error it is.
_XMLLINT_WARNING_RE = re.compile(
    r"\bwarning:" r"|failed to load external entity",
    re.IGNORECASE,
)

# ruff concise finding shape: "<path>:<line>[:<col>]: <code> <message>".
# Group 1 captures the full path (non-greedy up to the ":<line>[:<col>]:"
# suffix), so a Windows drive letter ("C:\\…") stays attached to its path
# instead of being shorn off at the first colon, and prose lines that happen
# to contain a colon but no ":<line>:" suffix (ruff's own summary text) never
# masquerade as a file. The numeric suffix is the discriminator.
_RUFF_CONCISE_FINDING_RE = re.compile(r"^(.+?):\d+(?::\d+)?:\s")

# Issue #108: a ruff `--output-format=concise` line, fully decomposed so the
# report can show the rule code, line:col, AND the message for EVERY finding
# (the bare "<N> error(s) in <file>" count made every consumer re-run ruff to
# learn what the finding was). Groups: 1=path, 2=line, 3=col (optional),
# 4=rule code (e.g. F401 / I001 / E701), 5=the human message (which may begin
# with ruff's "[*]" auto-fixable marker — kept verbatim). The rule-code group
# is `[A-Z]{1,4}\d+` (ruff codes are an uppercase prefix + digits); a line that
# matches `_RUFF_CONCISE_FINDING_RE` but NOT this (no recognizable code, e.g. a
# future ruff format tweak) still falls back to the count path, so no finding is
# ever silently dropped. The path group is non-greedy up to the ":<line>:"
# suffix for the same Windows-drive-letter reason as the concise regex above.
_RUFF_CONCISE_FINDING_FULL_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:\s+(?P<code>[A-Z]{1,4}\d+)\s+(?P<msg>.*)$"
)

# Display labels for `[REPO LINT][PYTHON]` style section headers when
# the engine is invoked from validate_plugin.py — kept short so the
# output stays compact in CI logs.
_LANG_LABEL: dict[str, str] = {
    "python": "PYTHON",
    "javascript": "JS/TS",
    "shell": "SHELL",
    "go": "GO",
    "rust": "RUST",
    "markdown": "MD",
    "json": "JSON",
    "yaml": "YAML",
    "dockerfile": "DOCKER",
    "xml": "XML",
    "css": "CSS",
    "html": "HTML",
    "sql": "SQL",
    "toml": "TOML",
    "powershell": "PS",
}

# Per-language tool name passed to `resolve_tool_command` and surfaced in
# the missing-tool finding text. Some languages need >1 tool (Python uses
# ruff + mypy) — only the PRIMARY tool is recorded here; the secondary
# is reported via `report.minor(...)` from inside the lint function.
_PRIMARY_TOOL: dict[str, str] = {
    "python": "ruff",
    "javascript": "eslint",
    "shell": "shellcheck",
    "go": "gofmt",
    "rust": "cargo",
    "markdown": "markdownlint-cli2",
    "json": "json",  # stdlib — never missing
    "yaml": "yamllint",
    "dockerfile": "hadolint",
    "xml": "xmllint",
    "css": "stylelint",
    "html": "htmlhint",
    "sql": "sqlfluff",
    "toml": "tomllib",  # stdlib in Python 3.11+
    "powershell": "PSScriptAnalyzer",
}


# ---------------------------------------------------------------------------
# Tool resolution
# ---------------------------------------------------------------------------


def _resolve(tool_name: str) -> list[str] | None:
    """Resolve a tool to its argv prefix.

    Wraps `resolve_tool_command` with two safety nets:

    1. `ValueError` is raised by smart_exec for tools that aren't in TOOL_DB
       (go, gofmt, cargo, markdownlint). Catch it and fall back to
       `shutil.which` so the engine still works when those toolchains are
       installed natively.
    2. `None` is returned when no executor is available — callers either
       fail strict (MAJOR + False) or warn-skip (WARNING + True).
    """
    try:
        cmd = resolve_tool_command(tool_name)
    except ValueError:
        cmd = None
    if cmd:
        return cmd
    # Fallback for tools not in TOOL_DB but installed locally.
    local = shutil.which(tool_name)
    if local:
        return [local]
    return None


def _tool_missing(
    report: ValidationReport,
    *,
    lang: str,
    tool: str,
    file_count: int,
    strict: bool,
) -> None:
    """Emit a uniform missing-tool finding.

    Cross-platform note: tools like ``shellcheck`` aren't natively
    packaged on Windows (no homebrew/apt equivalent without scoop/WSL).
    On Windows we always demote to WARNING regardless of strict mode so
    Windows users aren't blocked from publishing — they get a
    documentation pointer instead of a hard MAJOR finding.
    """
    import sys

    msg = (
        f"Missing linter for {lang}: {tool} (needed for {file_count} file(s)) — "
        "install it locally or rely on uvx / bunx / npx / docker fallback. "
        "Pass strict_missing_tools=False (or --soft-missing-linters in publish.py) "
        "to demote to WARNING."
    )
    # Windows-specific: shellcheck has no native MSI installer. Don't
    # block Windows users on a tool that POSIX systems package by default.
    windows_only_unavailable = {"shellcheck"}
    if sys.platform == "win32" and tool in windows_only_unavailable:
        msg += (
            " (On Windows: install via `scoop install shellcheck` or run "
            "the plugin's CI under WSL/Linux — auto-demoted to WARNING here.)"
        )
        report.warning(msg)
        return
    if strict:
        report.major(msg)
    else:
        report.warning(msg)


# ---------------------------------------------------------------------------
# Hardened linter subprocess runner (issue #74)
# ---------------------------------------------------------------------------
#
# Every linter spawn in this module routes through `_run_linter`. Without it,
# a per-linter spawn on a bare CI runner (no TTY, cold tool cache) can hang
# FOREVER even though a `timeout=` is set, for two reasons:
#
#   1. No `stdin` redirect — when the linter (or the `uvx`/`npx`/`bunx`
#      first-run fetcher that `smart_exec.choose_best` falls back to) prompts,
#      it blocks on a stdin that never delivers EOF in a no-TTY environment.
#   2. A forked GRANDCHILD outlives the timeout — `subprocess.run`'s own
#      timeout kills only the DIRECT child, then `communicate()` keeps reading
#      the captured stdout/stderr pipe; a surviving grandchild (the `uvx`/`npx`
#      download process) that inherited that pipe keeps it open, so the read
#      blocks PAST the deadline. This is the exact "timeout set but it still
#      hangs + orphan `uv`/`python` children on cancel" signature in issue #74.
#
# `_run_linter` closes both holes universally:
#   * `stdin=subprocess.DEVNULL` — instant EOF, so nothing can ever block on
#     stdin (the cheapest, broadest fix).
#   * non-interactive env — `CI=1`, `DEBIAN_FRONTEND=noninteractive`,
#     `NPM_CONFIG_YES=true`, `PIP_NO_INPUT=1`, `UV_NO_PROGRESS=1`,
#     `GIT_TERMINAL_PROMPT=0` — belt-and-braces so a fetcher that consults
#     these instead of the TTY also stays silent and non-blocking.
#   * a NEW PROCESS GROUP / SESSION (`start_new_session=True`) plus, on
#     timeout, killing the WHOLE group — so a forked grandchild that inherited
#     the pipe is terminated and the read unblocks at the deadline instead of
#     hanging forever.
#
# It returns a `subprocess.CompletedProcess`-shaped object so call sites read
# `.returncode` / `.stdout` / `.stderr` exactly as before, and it re-raises
# `subprocess.TimeoutExpired` so each linter's existing
# `except subprocess.TimeoutExpired: report.warning(...)` handler keeps
# working unchanged.

# Environment overrides forced on every linter spawn so a missing tool that
# routes through a first-run fetcher (uvx / npx / bunx / pipx) can never stop
# to ask a question on a runner with no TTY.
_NONINTERACTIVE_ENV: dict[str, str] = {
    "CI": "1",
    "DEBIAN_FRONTEND": "noninteractive",
    "NPM_CONFIG_YES": "true",  # npx/npm: auto-confirm package install
    "PIP_NO_INPUT": "1",  # pip: never prompt
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    "PYTHONUNBUFFERED": "1",
    "UV_NO_PROGRESS": "1",  # uv/uvx: no interactive progress bar
    "GIT_TERMINAL_PROMPT": "0",  # any git fetch: fail instead of prompting
    "HOMEBREW_NO_AUTO_UPDATE": "1",
}


# Hard ceiling applied to EVERY linter spawn (issue #148). The per-linter call
# sites pass a sane default (60/120/180s); `PLUGIN_REPO_LINT_TIMEOUT` lets a CI
# runner cap (or raise) that uniformly without editing each site. A blocked or
# never-finishing linter on a fresh GitHub runner could otherwise sit until the
# whole CI job's wall-clock timeout (~30 min, the v2.137.0 incident) with no
# output. With a per-linter ceiling the worst case is that language being
# SKIPPED as a WARNING and the next language proceeding — never a job-killing
# hang. A non-positive / unparseable value disables the override (falls back to
# the call-site default), so a typo can never make the ceiling shorter than a
# real linter needs and silently skip everything.
_REPO_LINT_TIMEOUT_ENV = "PLUGIN_REPO_LINT_TIMEOUT"


def _effective_timeout(call_site_default: float) -> float:
    """Resolve the timeout for one linter spawn.

    Returns ``PLUGIN_REPO_LINT_TIMEOUT`` (seconds, float) when it is set to a
    positive number; otherwise the caller's ``call_site_default``. An empty,
    zero, negative, or non-numeric value is ignored (the default wins) so a
    misconfiguration degrades to today's hard-coded behaviour rather than to a
    near-zero ceiling that would skip every language.
    """
    raw = os.environ.get(_REPO_LINT_TIMEOUT_ENV, "").strip()
    if not raw:
        return call_site_default
    try:
        override = float(raw)
    except ValueError:
        return call_site_default
    return override if override > 0 else call_site_default


# Aggregate wall-clock ceiling for the WHOLE REPO LINT phase (issue #162). The
# #148 per-linter ceiling bounds each linter spawn, but NOT their SUM: ~17 linters
# each capped at 60-180s is ~34 min, and on a cold CI runner uv/npm serialize the
# concurrent first-run `uvx`/`npx` fetches on a global cache lock — so the parallel
# fan-out degrades toward serial and the phase marches to ~27-34 min, past the CI
# job's own `timeout-minutes`. GitHub then SIGKILLs the job, leaving the orphaned
# `uv`/`python` children seen in #162. This budget caps the SUM so the phase can
# NEVER approach the job wall-clock, no matter how many linters go cold at once.
_REPO_LINT_PHASE_TIMEOUT_ENV = "PLUGIN_REPO_LINT_PHASE_TIMEOUT"
# 600s: ~17× the ~35s warm run and ~4× a plausibly-slow-but-healthy cold run, yet
# well under the typical 25-30 min validate-job ceiling — so it will not false-skip
# a healthy cold run, but always fires before the job timeout on a cold linter storm.
_DEFAULT_PHASE_TIMEOUT = 600.0


def _phase_timeout() -> float:
    """Resolve the aggregate wall-clock budget for the whole REPO LINT phase.

    Returns ``PLUGIN_REPO_LINT_PHASE_TIMEOUT`` (seconds, float) when set to a
    positive number; otherwise ``_DEFAULT_PHASE_TIMEOUT``. An empty, zero,
    negative, or non-numeric value falls back to the default — mirroring
    ``_effective_timeout`` so a typo can never DISABLE the guard or set a
    near-zero ceiling that skips every language.
    """
    raw = os.environ.get(_REPO_LINT_PHASE_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_PHASE_TIMEOUT
    try:
        override = float(raw)
    except ValueError:
        return _DEFAULT_PHASE_TIMEOUT
    return override if override > 0 else _DEFAULT_PHASE_TIMEOUT


def _phase_budget_skip_message(budget: float, skipped: list[str]) -> str:
    """The single WARNING emitted when the phase budget is exhausted (issue #162).

    Shared by the parallel and serial paths so both report identically. The skip
    is a WARNING (never blocking) — the same degrade as a per-linter timeout, and
    consistent with ``PLUGIN_SKIP_REPO_LINT``; the authoritative CI lint is the
    downstream Mega-Linter pass, so a budget-forced skip is visible, not silent.
    """
    return (
        f"REPO LINT phase budget ({budget:g}s) exhausted before linting every "
        f"language — skipped: {', '.join(skipped)}. Each linter is still "
        "individually bounded; this aggregate ceiling "
        "(PLUGIN_REPO_LINT_PHASE_TIMEOUT) stops a cold-runner linter storm from "
        "blowing the CI job's own timeout (issue #162). Raise it, warm the tool "
        "cache, or set PLUGIN_SKIP_REPO_LINT=1 if a downstream linter (e.g. "
        "Mega-Linter) already covers these."
    )


def _repo_lint_disabled() -> bool:
    """True when ``PLUGIN_SKIP_REPO_LINT`` opts out of the whole REPO LINT phase.

    Mirrors the ``PLUGIN_SKIP_GITHUB_INTEGRITY`` opt-out (issue #148): a
    downstream CI that already runs its own linter (e.g. Mega-Linter) sets
    ``PLUGIN_SKIP_REPO_LINT=1`` so CPV's 15-language pass is not a redundant
    second lint that can also hang on a cold runner. Any truthy value
    (``1``/``true``/``yes``/``on``, case-insensitive) disables the phase; unset
    or a falsey value keeps it ON (the default — linting still happens).
    """
    return os.environ.get("PLUGIN_SKIP_REPO_LINT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _run_linter(
    cmd: list[str],
    *,
    timeout: float,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a linter command so it can NEVER hang in a bare CI environment.

    Drop-in replacement for ``subprocess.run(cmd, capture_output=True,
    text=True, timeout=timeout[, cwd=cwd])`` with three anti-hang
    guarantees layered on (see the module section header for why each is
    load-bearing for issue #74):

      1. ``stdin=subprocess.DEVNULL`` — instant EOF; nothing can block on a
         missing TTY.
      2. ``_NONINTERACTIVE_ENV`` merged over ``os.environ`` — first-run
         tool fetchers (uvx/npx/bunx/pipx) stay non-interactive.
      3. a new process group (``start_new_session=True``); on
         ``TimeoutExpired`` the WHOLE group is killed, so a forked
         grandchild holding the captured pipe cannot keep the read alive
         past the deadline.

    Raises ``subprocess.TimeoutExpired`` on deadline (after killing the
    group) so each caller's existing ``except subprocess.TimeoutExpired``
    branch fires exactly as before.

    The effective deadline is ``PLUGIN_REPO_LINT_TIMEOUT`` when set to a
    positive value, else the caller's ``timeout`` (issue #148) — so a single
    env var caps EVERY linter spawn without editing each call site.
    """
    # Resolve the hard ceiling here (issue #148): every call site passes its own
    # default, but a CI runner can shrink/raise them all uniformly via the env
    # var. Applied centrally so a future linter added with a fresh `_run_linter`
    # call automatically inherits the override too.
    timeout = _effective_timeout(timeout)
    env = {**os.environ, **_NONINTERACTIVE_ENV}
    # Windows has no POSIX process groups / killpg; `start_new_session` is a
    # no-op-equivalent there. On Windows, `Popen.kill()` already terminates
    # the child, and `subprocess` cannot guarantee grandchild teardown without
    # a Job Object — acceptable, because the bare-CI hang reported in #74 is a
    # Linux runner and the stdin=DEVNULL + non-interactive env still apply.
    new_session = os.name == "posix"
    with subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        start_new_session=new_session,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the ENTIRE process group so a forked grandchild (uvx/npx
            # fetcher) that inherited the captured pipe dies too — otherwise
            # the post-kill drain below would itself block forever, exactly
            # the bug we are fixing.
            _kill_process_tree(proc)
            # Drain whatever is buffered so the pipes close cleanly; the group
            # is dead now, so this returns promptly. Re-raise so the caller's
            # TimeoutExpired handler runs.
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _kill_process_tree(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Best-effort kill of ``proc`` and its whole process group.

    On POSIX the child was started with ``start_new_session=True`` so it is
    the leader of its own group; ``killpg`` reaps every descendant the linter
    spawned (the uvx/npx download grandchild that causes issue #74's hang).
    Falls back to ``proc.kill()`` everywhere the group signal is unavailable
    or the group is already gone.
    """
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # group already gone / not permitted — fall through to kill()
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def detect_languages(
    plugin_root: Path,
    *,
    gi: GitignoreFilter | None = None,
) -> dict[str, list[Path]]:
    """Walk the gitignore-filtered tree once and bucket files by language.

    Pre-filtering via `GitignoreFilter` is the only reliable way to keep
    nested .git/ trees (e.g. cloned reference repos under INPUT_DEV/) out
    of the scan — the underlying linters (ruff / eslint / gofmt) treat
    each nested .git/ as a separate root and ignore the parent .gitignore.
    """
    if gi is None:
        gi = GitignoreFilter(plugin_root)

    languages: dict[str, list[Path]] = {}

    def collect(name: str, patterns: list[str]) -> None:
        out: list[Path] = []
        for pattern in patterns:
            out.extend(gi.rglob(pattern))
        if out:
            languages[name] = out

    collect("python", ["*.py"])
    collect("javascript", ["*.js", "*.ts", "*.jsx", "*.tsx"])
    collect("shell", ["*.sh", "*.bash"])
    collect("go", ["*.go"])
    collect("rust", ["*.rs"])
    collect("markdown", ["*.md", "*.mdx"])
    collect("json", ["*.json"])
    collect("yaml", ["*.yml", "*.yaml"])
    collect("dockerfile", ["Dockerfile", "Dockerfile.*", "*.dockerfile"])
    collect("xml", ["*.xml", "*.xhtml", "*.xsd", "*.xsl"])
    collect("css", ["*.css", "*.scss", "*.less"])
    collect("html", ["*.html", "*.htm"])
    collect("sql", ["*.sql"])
    collect("toml", ["*.toml"])
    collect("powershell", ["*.ps1", "*.psm1", "*.psd1"])

    return languages


# ---------------------------------------------------------------------------
# 15 per-language linters — uniform signature
# ---------------------------------------------------------------------------


def _files_or_root(repo_root: Path, files: list[Path]) -> list[str]:
    """Return file paths for the linter, falling back to repo_root if empty.

    Tools like ruff / eslint / gofmt accept either a list of files or a
    directory; passing the gitignore-filtered file list is what blocks
    scanning into nested cloned repos. The fallback is only used when
    callers haven't done discovery (rare; the dispatcher always feeds
    a non-empty list).
    """
    if files:
        return [str(f) for f in files]
    return [str(repo_root)]


def _relpath(repo_root: Path, p: str) -> str:
    """Best-effort relative path; fall back to the original on ValueError."""
    try:
        return str(Path(p).resolve().relative_to(repo_root.resolve()))
    except (ValueError, OSError):
        return p


def _canonical_python_typechecker(repo_root: Path) -> str:
    """Decide which type-checker the project canonically uses — from config ONLY.

    Deterministic and content-derived (never a plugin-declared allow-list, per
    CPV's no-self-exemption doctrine): the choice is a structural fact about the
    target's own config files, so a project that opted into pyright is not also
    forced through mypy (which mis-infers types pyright gets right → FPs, #58).

    Precedence (matches the tie-break the issue specifies):
      1. pyrightconfig.json present                 -> "pyright" (strongest; wins
         over every mypy signal — a dedicated file is an unambiguous opt-in).
      2. else any mypy signal (mypy.ini / .mypy.ini / [tool.mypy] / setup.cfg
         [mypy]) -> "mypy" (an explicit mypy config beats a bare [tool.pyright]).
      3. else [tool.pyright] in pyproject.toml      -> "pyright".
      4. else                                       -> "mypy" (status-quo default).

    Unparseable config is treated as "no signal" (fail-safe to the default).
    """
    if (repo_root / "pyrightconfig.json").is_file():
        return "pyright"

    has_tool_pyright = False
    has_tool_mypy = False
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib as _toml
        except ModuleNotFoundError:
            try:
                import tomli as _toml  # type: ignore[no-redef,import-not-found]
            except ModuleNotFoundError:
                _toml = None  # type: ignore[assignment]
        if _toml is not None:
            try:
                with open(pyproject, "rb") as fp:
                    data = _toml.load(fp)
                tool = data.get("tool", {}) if isinstance(data, dict) else {}
                if isinstance(tool, dict):
                    has_tool_pyright = "pyright" in tool
                    has_tool_mypy = "mypy" in tool
            except (OSError, ValueError):
                pass  # unparseable -> no signal

    # A dedicated mypy config (or [tool.mypy]) beats a bare [tool.pyright];
    # only pyrightconfig.json (handled above) overrides an explicit mypy opt-in.
    if (repo_root / "mypy.ini").is_file() or (repo_root / ".mypy.ini").is_file():
        return "mypy"
    if has_tool_mypy:
        return "mypy"
    setup_cfg = repo_root / "setup.cfg"
    if setup_cfg.is_file():
        try:
            if re.search(r"(?m)^\[mypy\]", setup_cfg.read_text(encoding="utf-8", errors="replace")):
                return "mypy"
        except OSError:
            pass

    if has_tool_pyright:
        return "pyright"
    return "mypy"


def lint_python(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Python files with ruff (errors) and the project's canonical
    type-checker — pyright OR mypy, never both (issue #58) — as warnings."""
    if not files:
        return True

    ruff_cmd = _resolve("ruff")
    if not ruff_cmd:
        _tool_missing(
            report,
            lang="python",
            tool="ruff",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    targets = _files_or_root(repo_root, files)
    ok = True

    # ruff check — errors block
    try:
        result = _run_linter(
            ruff_cmd
            + [
                "check",
                "--select=E,F,W,I",
                "--ignore=E501,E402",
                "--output-format=concise",
                *targets,
            ],
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("ruff timed out after 120s — skipping Python lint")
        return True

    if result.returncode == 0:
        report.passed(f"ruff check passed for {len(files)} Python file(s)")
    else:
        # ruff --output-format=concise emits "<path>:<line>[:<col>]: <code> …".
        # Splitting on the FIRST ":" mis-grouped Windows absolute paths
        # ("C:\\…\\foo.py" -> bucket "C") and turned any colon-bearing
        # summary line ("warning: …") into a bogus file bucket. Anchor on
        # the ":<line>[:<col>]:" suffix instead: the path is everything
        # before it, the leading numeric group is what distinguishes a real
        # finding from prose (a Windows drive's ":" is followed by "\\", not
        # a digit), so non-finding lines are skipped outright.
        #
        # Issue #108: keep the per-FILE MAJOR grouping (one MAJOR per file —
        # the count consumers depend on is unchanged) but ENRICH each MAJOR's
        # message to list every finding as "<code> <rel>:<line>[:<col>]
        # <message>", so the report shows the rule code, location, and message
        # the consumer would otherwise have to re-derive by re-running ruff.
        # Findings are kept in ruff's emission order per file.
        errors_by_file: dict[str, list[str]] = {}
        order: list[str] = []
        for line in (result.stdout or "").splitlines():
            m = _RUFF_CONCISE_FINDING_RE.match(line)
            if not m:
                continue
            file_part = m.group(1).strip()
            if not file_part:
                continue
            if file_part not in errors_by_file:
                errors_by_file[file_part] = []
                order.append(file_part)
            rel = _relpath(repo_root, file_part)
            full = _RUFF_CONCISE_FINDING_FULL_RE.match(line)
            if full:
                loc = f"{rel}:{full.group('line')}"
                if full.group("col"):
                    loc += f":{full.group('col')}"
                detail = f"{full.group('code')} {loc} {full.group('msg').strip()}".rstrip()
            else:
                # Recognised as a finding line by the concise regex but the
                # rule-code shape did not parse (defensive — keeps the finding
                # visible verbatim rather than dropping it).
                detail = line.strip()
            errors_by_file[file_part].append(detail)
        for file_part in order:
            details = errors_by_file[file_part]
            rel = _relpath(repo_root, file_part)
            count = len(details)
            header = f"Ruff: {count} error(s) in {rel}"
            body = "\n".join(f"  {d}" for d in details)
            report.major(f"{header}\n{body}", rel)
        if not errors_by_file and (result.stdout or "").strip():
            report.major("Ruff: error(s) across Python files")
        ok = False

    # mypy — type warnings only (non-blocking). Scope limited to files under
    # scripts/ to match the pre-v2.64 validate_scripts behaviour: type-checking
    # the whole repo (especially test files) surfaces mountains of
    # annotation-unchecked notes that have nothing to do with plugin
    # publishability. The lint_repo orchestrator's primary signal is ruff.
    # Restrict to scripts under the repo's OWN scripts/ tree. Test
    # fixtures (e.g. ``tests/fixtures/<plugin>/scripts/...``) have
    # ``scripts`` in their path-parts too, but they're checked-in
    # foreign plugin source — their type errors are the foreign plugin
    # author's responsibility, not CPV's.
    def _is_own_script(f: Path) -> bool:
        parts = f.parts
        if "scripts" not in parts:
            return False
        if "fixtures" in parts:
            return False
        if "tests" in parts and parts.index("tests") < parts.index("scripts"):
            return False
        return True

    mypy_targets = [str(f) for f in files if _is_own_script(f)]
    if not mypy_targets:
        return ok

    # Run the project's CANONICAL type-checker, never both. Imposing mypy on a
    # pyright-canonical project surfaces divergence FPs on code the project's
    # own checker accepts (issue #58).
    if _canonical_python_typechecker(repo_root) == "pyright":
        pyright_cmd = _resolve("pyright")
        if not pyright_cmd:
            # pyright is auxiliary — never fail strict on its absence; only inform.
            report.info("pyright not available locally or via npx/uvx; skipping Python type check")
            return ok
        try:
            pr = _run_linter(
                pyright_cmd + ["--outputjson", *mypy_targets],
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            report.warning("pyright timed out after 180s — skipping type check")
            return ok
        try:
            payload = json.loads(pr.stdout) if (pr.stdout or "").strip() else {}
            diagnostics = payload.get("generalDiagnostics", []) if isinstance(payload, dict) else []
        except (json.JSONDecodeError, ValueError):
            diagnostics = []
        errors = [d for d in diagnostics if isinstance(d, dict) and d.get("severity") == "error"]
        if not errors:
            report.passed(f"pyright passed for {len(mypy_targets)} script file(s)")
        else:
            for d in errors[:20]:
                f_str = d.get("file", "") or ""
                rng = d.get("range")
                start = rng.get("start", {}) if isinstance(rng, dict) else {}
                line_no = (start.get("line", 0) + 1) if isinstance(start, dict) else 0
                msg = (d.get("message") or "").splitlines()[0] if d.get("message") else ""
                rel = _relpath(repo_root, f_str) if f_str else "?"
                report.minor(f"Pyright: {rel}:{line_no} {msg}")
        return ok

    mypy_cmd = _resolve("mypy")
    if mypy_cmd:
        try:
            mypy_result = _run_linter(
                mypy_cmd
                + [
                    "--ignore-missing-imports",
                    "--exclude",
                    "scripts_dev|docs_dev|builds_dev|tests_dev|tests/fixtures",
                    *mypy_targets,
                ],
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            report.warning("mypy timed out after 180s — skipping type check")
            return ok

        if mypy_result.returncode == 0:
            report.passed(f"mypy passed for {len(mypy_targets)} script file(s)")
        else:
            for line in mypy_result.stdout.splitlines()[:20]:
                stripped = line.strip()
                if not stripped or stripped.startswith(("Success", "Found")):
                    continue
                report.minor(f"Mypy: {stripped}")
    else:
        # mypy is auxiliary — never fail strict on its absence; only inform.
        report.info("mypy not available locally or via uvx; skipping Python type check")

    return ok


def lint_javascript(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint JS/TS files with eslint."""
    if not files:
        return True

    eslint_cmd = _resolve("eslint")
    if not eslint_cmd:
        # Local node_modules/.bin/eslint is the project-vendored install
        # path — honour it before declaring eslint missing.
        local = repo_root / "node_modules" / ".bin" / "eslint"
        if local.exists():
            eslint_cmd = [str(local)]
        else:
            _tool_missing(
                report,
                lang="javascript",
                tool="eslint",
                file_count=len(files),
                strict=strict_missing_tools,
            )
            return not strict_missing_tools

    # eslint requires a config file — without one, every run is effectively
    # noise. Skip with INFO (not WARNING) when missing to match the legacy
    # behaviour of lint_files.py.
    config_files = (
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.json",
        ".eslintrc.yml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "eslint.config.ts",
    )
    if not any((repo_root / cfg).exists() for cfg in config_files):
        report.info("No eslint config found — skipping JavaScript lint")
        return True

    targets = _files_or_root(repo_root, files)

    try:
        result = _run_linter(
            eslint_cmd + ["--format=json", *targets],
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("eslint timed out after 120s — skipping JS/TS lint")
        return True

    if result.returncode == 0:
        report.passed(f"eslint passed for {len(files)} JS/TS file(s)")
        return True

    # eslint exited non-zero. With --format=json it normally prints a JSON
    # ARRAY of per-file results on stdout. Two non-finding failure shapes are
    # possible and must NOT be swallowed as "clean":
    #   * empty stdout (e.g. a broken eslint.config / flat-config error that
    #     eslint writes to stderr and exits 2) — previously fell through to
    #     `data = []`, looped over nothing, and returned True, hiding the
    #     failure (same silent-failure class fixed for markdownlint, issue #20);
    #   * non-JSON stdout — already handled below.
    if not (result.stdout or "").strip():
        err = (result.stderr or "").strip()
        report.major(
            "eslint exited non-zero "
            f"(rc={result.returncode}) with no JSON output — "
            f"likely a config/runtime error: {err[:200] or 'see logs'}"
        )
        return False

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        report.major("eslint: produced non-JSON output — see logs")
        return False

    # eslint --format=json always yields a top-level array; guard against a
    # foreign/garbled payload (e.g. a JSON object) so iterating it can't raise
    # AttributeError and crash the whole parallel lint_repo run.
    if not isinstance(data, list):
        report.major("eslint: unexpected JSON shape (expected an array) — see logs")
        return False

    ok = True
    for file_result in data:
        if not isinstance(file_result, dict):
            continue
        rel = _relpath(repo_root, file_result.get("filePath", ""))
        for msg in file_result.get("messages", []):
            if not isinstance(msg, dict):
                continue
            severity = msg.get("severity", 1)
            text = msg.get("message", "Unknown issue")
            line = msg.get("line", 0) or None
            rule = msg.get("ruleId", "") or ""
            label = f"eslint{(' ' + rule) if rule else ''}"
            if severity >= 2:
                report.major(f"{label}: {text}", rel, line)
                ok = False
            else:
                report.minor(f"{label}: {text}", rel, line)
    return ok


def lint_shell(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint shell scripts with shellcheck (per-file, JSON output)."""
    if not files:
        return True

    cmd = _resolve("shellcheck")
    if not cmd:
        _tool_missing(
            report,
            lang="shell",
            tool="shellcheck",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            result = _run_linter(
                cmd + ["-f", "json", "-x", str(f)],
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            report.warning(f"shellcheck timed out on {rel}")
            continue
        if result.returncode == 0:
            report.passed(f"shellcheck: {rel} OK")
            continue
        try:
            issues = json.loads(result.stdout) if result.stdout else []
        except json.JSONDecodeError:
            issues = []
        for issue in issues:
            level = issue.get("level", "warning")
            msg = issue.get("message", "Unknown issue")
            line = issue.get("line", 0) or None
            code = issue.get("code", "")
            label = f"shellcheck SC{code}"
            if level == "error":
                report.major(f"{label}: {msg}", rel, line)
                ok = False
            else:
                report.minor(f"{label}: {msg}", rel, line)
    return ok


def lint_go(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Go files with gofmt -l + go vet (only when go.mod present)."""
    if not files:
        return True

    gofmt_cmd = _resolve("gofmt")
    if not gofmt_cmd:
        _tool_missing(
            report,
            lang="go",
            tool="gofmt",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    targets = [str(f) for f in files]
    ok = True

    try:
        result = _run_linter(
            gofmt_cmd + ["-l", *targets],
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("gofmt timed out — skipping Go lint")
        return True

    if (result.stdout or "").strip():
        # gofmt -l prints the path of each file that needs reformatting.
        for line in result.stdout.splitlines()[:10]:
            rel = _relpath(repo_root, line.strip())
            report.major(f"gofmt: {rel} needs formatting", rel)
        ok = False

    # go vet only runs when repo_root itself is a Go module (has go.mod).
    # Without that guard, `./...` would walk into nested cloned modules
    # under gitignored trees — same root-cause as the gofmt fix.
    if not (repo_root / "go.mod").exists():
        return ok

    go_cmd = _resolve("go")
    if not go_cmd:
        report.info("go binary not available; skipping go vet")
        return ok
    try:
        vet_result = _run_linter(
            go_cmd + ["vet", "./..."],
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("go vet timed out")
        return ok

    if vet_result.returncode != 0:
        for line in (vet_result.stderr or vet_result.stdout).splitlines()[:10]:
            stripped = line.strip()
            if stripped:
                report.minor(f"go vet: {stripped}")
        # vet diagnostics are reported as MINOR (matches the validate_scripts
        # pre-refactor severity); treat any failure as a soft regression but
        # don't block strict mode.
    return ok


def lint_rust(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Rust files with cargo fmt --check + cargo clippy."""
    if not files:
        return True

    # Without Cargo.toml at the repo root we can't run cargo at all;
    # treat that as "no rust project" rather than a missing-tool failure.
    if not (repo_root / "Cargo.toml").exists():
        report.info(f"Found {len(files)} Rust file(s) but no Cargo.toml at repo root — skipping cargo fmt / clippy")
        return True

    cargo_cmd = _resolve("cargo")
    if not cargo_cmd:
        _tool_missing(
            report,
            lang="rust",
            tool="cargo",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    ok = True
    try:
        fmt_result = _run_linter(
            cargo_cmd + ["fmt", "--check"],
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("cargo fmt --check timed out")
        return True

    if fmt_result.returncode != 0:
        report.major("cargo fmt: formatting issues found (run 'cargo fmt')")
        ok = False

    try:
        clippy_result = _run_linter(
            cargo_cmd + ["clippy"],
            cwd=repo_root,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        report.warning("cargo clippy timed out")
        return ok

    if clippy_result.returncode != 0:
        for line in (clippy_result.stderr or "").splitlines()[:10]:
            stripped = line.strip()
            if "error" in stripped.lower() or "warning" in stripped.lower():
                report.minor(f"clippy: {stripped}")
    return ok


def lint_markdown(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Markdown files with markdownlint-cli2."""
    if not files:
        return True

    cmd = _resolve("markdownlint-cli2")
    if not cmd:
        _tool_missing(
            report,
            lang="markdown",
            tool="markdownlint-cli2",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    invocation = list(cmd)
    # If the target doesn't have its own .markdownlint.json, use CPV's
    # relaxed config (disables MD013/MD033/MD040 — see issue #8).
    #
    # Multi-path resolver (issue #20 fix): the canonical rule set MUST be
    # found whether CPV is invoked from the cached install (full repo at
    # `~/.claude/plugins/cache/.../<ver>/`) OR from `uvx --from git+...`
    # (only the wheel-bundled `scripts/` dir present). Try, in order:
    #   1. `<scripts>/.markdownlint.json` — wheel package data (uvx case;
    #      shipped via [tool.hatch.build.targets.wheel.force-include]).
    #   2. `<scripts>/../.markdownlint.json` — repo-root copy (cached case
    #      and dev-checkout case).
    # Whichever exists first wins. Without (1), the uvx-from-HEAD path
    # had `cpv_config.is_file()` return False, no `--config` was passed,
    # and markdownlint-cli2 fell back to ITS defaults (MD013/MD012/MD032
    # all enabled) — producing the cached-vs-remote disagreement in #20.
    target_config = repo_root / ".markdownlint.json"
    if not target_config.exists():
        scripts_dir = Path(__file__).resolve().parent
        for candidate in (scripts_dir / ".markdownlint.json", scripts_dir.parent / ".markdownlint.json"):
            if candidate.is_file():
                invocation.extend(["--config", str(candidate)])
                break

    file_paths = [str(f) for f in files]

    # Run markdownlint from an ISOLATED empty temp cwd, not `cwd=repo_root`
    # (issue #84). `_resolve` may return `['bunx', 'markdownlint-cli2']` /
    # `['npx', ...]`, and `bunx`/`npx` resolve the package by walking UP the
    # cwd's directory tree to the nearest `package.json` / `node_modules`. With
    # `cwd=repo_root`, a broken ancestor Node project (e.g. `$HOME/package.json`
    # with an incomplete `node_modules`) makes markdownlint-cli2's ESM imports
    # crash with `ERR_MODULE_NOT_FOUND`. An empty temp cwd has no ancestor
    # `package.json`, so resolution falls to the global/uvx-installed package.
    # The file paths AND `--config <path>` are already ABSOLUTE (verified), so
    # the cwd governs ONLY module resolution, never WHICH files get linted.
    try:
        with tempfile.TemporaryDirectory(prefix="cpv-mdlint-") as _isolated_cwd:
            result = _run_linter(
                invocation + file_paths,
                cwd=Path(_isolated_cwd),
                timeout=120,
            )
    except subprocess.TimeoutExpired:
        report.warning("markdownlint timed out — skipping markdown lint")
        return True

    if result.returncode == 0:
        report.passed(f"markdownlint passed for {len(files)} markdown file(s)")
        return True

    # markdownlint-cli2 prints one issue per line — surface up to 20 as
    # NIT (issue #20: stylistic markdownlint findings should NOT block a
    # publish via --strict; the canonical pipeline's correctness gates
    # are the JSON/YAML/Python validators, not markdown prose style).
    output = (result.stderr or result.stdout or "").strip()
    surfaced = 0
    # Issue #113: track which (file, MD004-signature) pairs have already been
    # surfaced so a consistent-mode-poisoned file emits one explanatory NIT
    # instead of N near-identical ones on its healthy bullets.
    seen_md004_signatures: set[tuple[str, str]] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        # Skip non-finding lines: subprocess output also carries tool-launcher
        # chatter (uv installer "Resolving dependencies", "Resolved, downloaded
        # and extracted N", "Saved lockfile", etc.) that is NOT a markdownlint
        # finding and would otherwise leak through as a spurious NIT.
        if not _MARKDOWNLINT_FINDING_RE.search(line):
            continue
        # Issue #113: collapse repeated same-signature MD004 (ul-style) findings
        # within one file to a single, clearer NIT. A stray prose-wrap marker
        # poisons markdownlint's consistent-style check and flags every healthy
        # bullet; relaying all N is confusing noise that points at the healthy
        # bullets, not the cause. The inconsistency still surfaces ONCE (visible
        # NIT, never suppressed), with a message naming the likely cause.
        md004 = _MD004_DEDUP_RE.match(line.strip())
        if md004 is not None:
            key = (md004.group("file"), md004.group("sig"))
            if key in seen_md004_signatures:
                continue
            seen_md004_signatures.add(key)
            report.nit(
                f"markdownlint: MD004/ul-style — {md004.group('file')} mixes "
                f"unordered-list markers {md004.group('sig')}; standardize the file "
                "on one marker. A hard-wrapped prose line beginning '+ ' or '* ' can "
                "poison markdownlint's consistent-style check and flag every healthy "
                "bullet (issue #113)."
            )
            surfaced += 1
            if surfaced >= 20:
                break
            continue
        report.nit(f"markdownlint: {line.strip()}")
        surfaced += 1
        if surfaced >= 20:
            break
    # Silent-failure surface (issue #20): if markdownlint exited non-zero
    # but produced no parseable per-line output, the developer used to see
    # only "CPV blocked the push (exit 3)" with no clue what failed. Now
    # we always emit at least one finding carrying the raw stderr/stdout.
    #
    # Crash-vs-style discriminator (issue #84): non-zero with no MD### finding
    # has two distinct causes. If the output is genuine non-parseable
    # markdownlint output (NOT a Node/tool crash), keep the NIT — it is a real,
    # if unstructured, lint signal. But a TOOL CRASH (`ERR_MODULE_NOT_FOUND`
    # etc. — markdownlint could not even RUN) or EMPTY output is an
    # environment/tool failure, not a lint violation, and must be a WARNING
    # (never blocks `--strict`), never a NIT (which does). This branch is
    # reachable ONLY when surfaced == 0, so a real MD### finding (surfaced > 0)
    # can never be down-graded by this discriminator.
    if not surfaced:
        if output and not _MARKDOWNLINT_TOOL_CRASH_RE.search(output):
            report.nit(f"markdownlint: {output[:200]}")
        else:
            report.warning(
                "markdownlint could not run (tool/environment failure, no "
                f"findings produced) — rc={result.returncode}: {output[:200]}"
            )
    # Return True: the only findings this branch adds are NIT (or a lone
    # WARNING). Per the module/`lint_repo` contract — "returns True iff no
    # MAJOR/CRITICAL finding was added … MINOR/WARNING findings do not flip
    # the return value" — and issue #20 ("stylistic markdownlint findings
    # must NOT block a publish"), returning False here would make the
    # standalone `cpv_lint_engine` CLI exit 1 on a NIT-only run, treating a
    # non-blocking style nit as a hard lint failure. (audit MED #15)
    return True


def lint_json(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,  # noqa: ARG001
) -> bool:
    """Validate JSON syntax with stdlib json (always available)."""
    if not files:
        return True

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            with open(f, encoding="utf-8") as fp:
                json.load(fp)
        except json.JSONDecodeError as e:
            report.major(f"JSON syntax error in {rel}: {e}", rel, getattr(e, "lineno", None))
            ok = False
        except UnicodeDecodeError as e:
            report.major(f"JSON encoding error in {rel}: {e}", rel)
            ok = False
        except OSError as e:
            report.warning(f"JSON I/O error reading {rel}: {e}", rel)
    if ok:
        report.passed(f"JSON syntax check passed for {len(files)} file(s)")
    return ok


def lint_yaml(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint YAML files with yamllint."""
    if not files:
        return True

    cmd = _resolve("yamllint")
    if not cmd:
        _tool_missing(
            report,
            lang="yaml",
            tool="yamllint",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    file_paths = [str(f) for f in files]
    try:
        result = _run_linter(
            cmd + ["-d", "relaxed", "--format", "parsable", *file_paths],
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("yamllint timed out — skipping YAML lint")
        return True

    if result.returncode == 0:
        report.passed(f"yamllint passed for {len(files)} YAML file(s)")
        return True

    ok = True
    for line in (result.stdout or "").splitlines()[:30]:
        stripped = line.strip()
        if not stripped:
            continue
        if "[error]" in stripped:
            report.major(f"yamllint: {stripped}")
            ok = False
        else:
            report.minor(f"yamllint: {stripped}")
    return ok


def lint_dockerfile(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Dockerfiles with hadolint."""
    if not files:
        return True

    cmd = _resolve("hadolint")
    if not cmd:
        _tool_missing(
            report,
            lang="dockerfile",
            tool="hadolint",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            result = _run_linter(
                cmd + [str(f)],
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            report.warning(f"hadolint timed out on {rel}")
            continue
        if result.returncode == 0:
            report.passed(f"hadolint: {rel} OK")
            continue
        for line in (result.stdout or result.stderr or "").splitlines()[:5]:
            stripped = line.strip()
            if stripped:
                report.major(f"hadolint: {stripped}", rel)
                ok = False
    return ok


def lint_xml(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint XML files with xmllint --noout.

    Issue #129 (reopened): when xmllint resolves via the docker fallback
    (`smart_exec` runs `docker run … alpine … xmllint --noout`), a non-zero
    returncode no longer means "the XML is malformed". The stderr is now a
    mix of THREE distinct line kinds, only one of which is a real finding:

      1. Docker / registry / daemon INFRASTRUCTURE noise from `docker run`
         auto-pulling the image (`Unable to find image 'alpine:latest'
         locally`, `latest: Pulling from library/alpine`, bare `<hash>:
         Pulling fs layer`, `Download complete`, …). NEVER a finding.
      2. A NON-FATAL xmllint WARNING — e.g. `warning: failed to load
         external entity "…/pom.xml"` when an offline runner cannot fetch
         an external DTD/entity. The document is still well-formed
         (`xmllint --noout` passes when run with the entity available), so
         this must be a WARNING, NEVER a MAJOR.
      3. A GENUINE xmllint validation ERROR (`f.xml:12: parser error :
         Opening and ending tag mismatch`, `Premature end of data`, …).
         This is the only line kind that is a real MAJOR.

    So we (a) drop the infra noise, (b) classify the surviving xmllint
    lines into warnings vs. errors, and (c) when the run failed but no
    genuine error line survives, emit ONE explanatory WARNING and do NOT
    fail the file (and do NOT falsely claim it passed either).
    """
    if not files:
        return True

    cmd = _resolve("xmllint")
    if not cmd:
        _tool_missing(
            report,
            lang="xml",
            tool="xmllint",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            result = _run_linter(
                cmd + ["--noout", str(f)],
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            report.warning(f"xmllint timed out on {rel}")
            continue
        if result.returncode == 0:
            report.passed(f"xmllint: {rel} OK")
            continue

        # returncode != 0 — triage stderr line by line. `saw_error` tracks
        # whether at least one GENUINE xmllint validation error survived
        # the infra-noise filter; if none did, the failure was infra/pull
        # /warning-only and must not flip `ok` to False (issue #129).
        saw_error = False
        for line in (result.stderr or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if _XMLLINT_INFRA_NOISE_RE.search(stripped):
                # Docker/registry/daemon output mixed into stderr — never a
                # finding about the user's XML.
                continue
            if _XMLLINT_REAL_ERROR_RE.search(stripped):
                report.major(f"xmllint: {stripped}", rel)
                saw_error = True
                ok = False
            elif _XMLLINT_WARNING_RE.search(stripped):
                # Non-fatal xmllint warning (e.g. an unfetchable external
                # entity offline) — surface it, but do NOT block the gate.
                report.warning(f"xmllint: {stripped}", rel)
            # else: an unclassified surviving line (rare) is left to the
            # post-loop fallback rather than being upgraded to a MAJOR — a
            # mystery non-error line must never invent a malformed-XML claim.
        if not saw_error:
            # The non-zero exit was infrastructure/warning-only — xmllint
            # could not run cleanly (typically the docker fallback's
            # image-pull failed, or only emitted a non-fatal warning). The
            # XML was NOT validated; flag that as a WARNING and let the file
            # pass without a false MAJOR (and without a false `passed`).
            report.warning(
                f"xmllint could not run cleanly via docker — XML not validated: {rel}"
            )
    return ok


def lint_css(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint CSS/SCSS/Less files with stylelint."""
    if not files:
        return True

    cmd = _resolve("stylelint")
    if not cmd:
        _tool_missing(
            report,
            lang="css",
            tool="stylelint",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    file_paths = [str(f) for f in files]
    try:
        result = _run_linter(
            cmd + file_paths,
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("stylelint timed out — skipping CSS lint")
        return True

    if result.returncode == 0:
        report.passed(f"stylelint passed for {len(files)} CSS/SCSS file(s)")
        return True

    for line in (result.stdout or "").splitlines()[:20]:
        stripped = line.strip()
        if stripped:
            report.minor(f"stylelint: {stripped}")
    # Return True: only MINOR findings were added. Per the module/`lint_repo`
    # contract, MINOR findings do not flip the return value; returning False
    # here would make the standalone CLI exit 1 on a MINOR-only run. The
    # missing-tool path above still returns False in strict mode. (audit MED #15)
    return True


def lint_html(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint HTML files with htmlhint."""
    if not files:
        return True

    cmd = _resolve("htmlhint")
    if not cmd:
        _tool_missing(
            report,
            lang="html",
            tool="htmlhint",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    file_paths = [str(f) for f in files]
    try:
        result = _run_linter(
            cmd + file_paths,
            cwd=repo_root,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("htmlhint timed out — skipping HTML lint")
        return True

    if result.returncode == 0:
        report.passed(f"htmlhint passed for {len(files)} HTML file(s)")
        return True

    # htmlhint prints an INFO banner ("Config loaded: <rc>", once per scanned
    # file) and a "Scanned N files, M errors found" summary line to stdout —
    # neither is a lint error. Filter both BEFORE building findings, then report
    # the first 20 REAL error lines so genuine errors are never crowded out of
    # the slice by banner noise (issue #132: a 6-file run emitted 10 bogus
    # "Config loaded:" MINORs). A real htmlhint error line still becomes a
    # MINOR (FN-safe — only the non-error banner/summary lines are dropped).
    error_lines: list[str] = []
    for raw in (result.stdout or "").splitlines():
        # Strip ANSI color escapes first so the banner/summary prefix checks
        # match a colorized line AND the surfaced finding is readable.
        stripped = _ANSI_RE.sub("", raw).strip()
        if not stripped:
            continue
        if stripped.startswith("Config loaded:"):
            continue
        if stripped.startswith("Scanned ") and " file" in stripped:
            continue
        error_lines.append(stripped)
    for stripped in error_lines[:20]:
        report.minor(f"htmlhint: {stripped}")
    # Return True: only MINOR findings were added. Per the module/`lint_repo`
    # contract, MINOR findings do not flip the return value; returning False
    # here would make the standalone CLI exit 1 on a MINOR-only run. The
    # missing-tool path above still returns False in strict mode. (audit MED #15)
    return True


def lint_sql(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint SQL files with sqlfluff."""
    if not files:
        return True

    cmd = _resolve("sqlfluff")
    if not cmd:
        _tool_missing(
            report,
            lang="sql",
            tool="sqlfluff",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    file_paths = [str(f) for f in files]
    try:
        result = _run_linter(
            cmd + ["lint", "--dialect", "ansi", *file_paths],
            cwd=repo_root,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        report.warning("sqlfluff timed out — skipping SQL lint")
        return True

    if result.returncode == 0:
        report.passed(f"sqlfluff passed for {len(files)} SQL file(s)")
        return True

    for line in (result.stdout or "").splitlines()[:20]:
        stripped = line.strip()
        if stripped:
            report.minor(f"sqlfluff: {stripped}")
    # Return True: only MINOR findings were added. Per the module/`lint_repo`
    # contract, MINOR findings do not flip the return value; returning False
    # here would make the standalone CLI exit 1 on a MINOR-only run. The
    # missing-tool path above still returns False in strict mode. (audit MED #15)
    return True


def lint_toml(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,  # noqa: ARG001
) -> bool:
    """Validate TOML files using stdlib tomllib (Python 3.11+) or tomli."""
    if not files:
        return True

    try:
        import tomllib as _toml
    except ModuleNotFoundError:
        try:
            import tomli as _toml  # type: ignore[no-redef,import-not-found]
        except ModuleNotFoundError:
            report.warning("No TOML parser available (need Python 3.11+ or 'pip install tomli')")
            return True

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            with open(f, "rb") as fp:
                _toml.load(fp)
        except _toml.TOMLDecodeError as e:
            report.major(f"TOML syntax error in {rel}: {e}", rel)
            ok = False
        except OSError as e:
            report.warning(f"TOML I/O error reading {rel}: {e}", rel)
    if ok:
        report.passed(f"TOML syntax check passed for {len(files)} file(s)")
    return ok


def lint_powershell(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint PowerShell scripts with PSScriptAnalyzer."""
    if not files:
        return True

    cmd = _resolve("PSScriptAnalyzer")
    if not cmd:
        _tool_missing(
            report,
            lang="powershell",
            tool="PSScriptAnalyzer",
            file_count=len(files),
            strict=strict_missing_tools,
        )
        return not strict_missing_tools

    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            result = _run_linter(
                cmd + ["-Path", str(f), "-Severity", "Error,Warning"],
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            report.warning(f"PSScriptAnalyzer timed out on {rel}")
            continue
        if result.returncode == 0 and not (result.stdout or "").strip():
            report.passed(f"PSScriptAnalyzer: {rel} OK")
            continue
        for line in (result.stdout or result.stderr or "").splitlines()[:5]:
            stripped = line.strip()
            if stripped:
                report.minor(f"PSScriptAnalyzer: {stripped}", rel)
    # Return True: this linter only ever adds MINOR findings (plus PASSED /
    # WARNING). Per the module/`lint_repo` contract — "returns True iff no
    # MAJOR/CRITICAL finding was added … MINOR/WARNING findings do not flip
    # the return value" — the body used to flip an `ok` flag to False on a
    # MINOR, which made the standalone CLI exit 1 on a MINOR-only run, treating
    # a non-blocking PSScriptAnalyzer nit as a hard lint failure. This matches
    # the lint_css / lint_html / lint_sql fix (audit MED #15); the missing-tool
    # path above still returns False in strict mode via `return not strict…`.
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Language → (lint function, primary tool name) — programming bug if any
# entry from `_LANG_LABEL` is missing.
_DISPATCH: dict[str, Callable[..., bool]] = {
    "python": lint_python,
    "javascript": lint_javascript,
    "shell": lint_shell,
    "go": lint_go,
    "rust": lint_rust,
    "markdown": lint_markdown,
    "json": lint_json,
    "yaml": lint_yaml,
    "dockerfile": lint_dockerfile,
    "xml": lint_xml,
    "css": lint_css,
    "html": lint_html,
    "sql": lint_sql,
    "toml": lint_toml,
    "powershell": lint_powershell,
}

# ---------------------------------------------------------------------------
# Phase D — content-hash scanner cache wiring
# ---------------------------------------------------------------------------
#
# Each per-language linter has a small set of CLI knobs (e.g. ruff's
# ``--select=E,F,W,I``, ``--ignore=E501,E402``) that are baked into
# this module's source. We capture them as a stable list-of-strings
# per language so the cache key tracks "this language was linted with
# these flags against this scanner version against these file
# contents" — change any of those three and the cache misses.
#
# This is INTENTIONALLY coarse-grained: we cache at the per-language
# level (one cache entry per <repo, language>) rather than per-file.
# Most linters (ruff, eslint, mypy, markdownlint) batch all files in
# one subprocess invocation, so per-file caching would double-count
# the spawn cost. shellcheck is the only true per-file linter, but
# its loop is small and a per-language merkle still saves all of it
# on a warm run.
#
# The scanner_name field carries both the language and the primary
# tool so a future addition of e.g. "pylint" alongside "ruff" doesn't
# accidentally hit a stale ruff entry.
_LANG_LINTER_ARGS: dict[str, list[str]] = {
    # Ruff flags from lint_python — keep in sync with the ruff_cmd
    # invocation. The python cache key's scanner_version tracks RUFF
    # ONLY (_PRIMARY_TOOL["python"] == "ruff" → _build_cache_key uses
    # get_scanner_version("ruff")); the auxiliary type-checker
    # (mypy / pyright) version is deliberately NOT modelled in the key.
    # That is safe because type-checker findings are emitted as MINOR /
    # INFO (never MAJOR/CRITICAL), so a stale cached "no type issues"
    # outcome can never flip a VALID verdict to INVALID — it is always
    # conservative. (Editing the type-checker CONFIG still invalidates
    # via _config_fingerprint, which folds pyrightconfig.json / mypy.ini
    # / [tool.mypy] / [tool.pyright] content into the key — issue #58.)
    "python": ["check", "--select=E,F,W,I", "--ignore=E501,E402", "--output-format=concise"],
    "javascript": ["--format=json"],
    "shell": ["-f", "json", "-x"],
    "go": ["-l"],
    "rust": ["fmt", "--check"],
    "markdown": ["--no-globs"],
    "json": [],  # stdlib json — scanner_version="stdlib"
    "yaml": ["-f", "parsable"],
    "dockerfile": ["--format", "json"],
    "xml": ["--noout"],
    "css": ["--formatter", "json"],
    "html": ["--format", "json"],
    "sql": ["lint", "--format", "json"],
    "toml": [],  # stdlib tomllib — scanner_version="stdlib"
    "powershell": ["-Settings", "PSGallery"],
}

# WARNING-message fragments that mark a NON-DETERMINISTIC outcome — one that
# is NOT a function of the cached inputs (file contents + tool version), so it
# must never be written to the scanner cache. If it were cached, a transient
# failure would masquerade as a durable "clean" result for the full cache TTL
# (up to 30 days), and the affected files would silently go UNLINTED until
# their content or the tool's version changed — hiding real lint errors.
#
# Two transient classes, both emitted ONLY by this module:
#   1. "<tool> timed out …" — every linter's `subprocess.TimeoutExpired`
#      handler emits a WARNING and returns True/ok. A timeout is a property of
#      machine load at run time, not of the source bytes; re-running on an idle
#      box typically succeeds. Caching it pins a passing verdict on files that
#      were never actually linted.
#   2. "… possible binary or environment issue" — markdownlint exited non-zero
#      but produced no parseable output (broken binary / bad env), which is
#      likewise unrelated to the file content being scanned.
#
# A "missing tool" WARNING (soft mode / Windows shellcheck) and "No TOML parser
# available" are deliberately NOT in this set: tool absence is already captured
# by scanner_version ("unknown" / "stdlib-pyX.Y"), so the cache key self-heals
# the moment the tool appears — those WARNINGs are deterministic given the key
# and remain cacheable.
_NON_CACHEABLE_WARNING_MARKERS: tuple[str, ...] = (
    "timed out",
    "possible binary or environment issue",
)


def _report_has_non_cacheable_outcome(report: ValidationReport) -> bool:
    """True if ``report`` carries a transient (non-deterministic) finding.

    Such an outcome must not be cached — see ``_NON_CACHEABLE_WARNING_MARKERS``.
    Only WARNING-level findings are inspected because every transient marker
    above is emitted at WARNING level; scoping to WARNING avoids a false match
    on a legitimate MAJOR/MINOR message that happened to contain a fragment.
    """
    for r in report.results:
        if r.level != "WARNING":
            continue
        msg = r.message
        if any(marker in msg for marker in _NON_CACHEABLE_WARNING_MARKERS):
            return True
    return False


def _replay_results_into_report(
    serialised: list[dict],
    report: ValidationReport,
) -> None:
    """Re-inject cached findings into ``report``.

    The cache stores ``[ValidationResult.to_dict(), ...]``; this
    helper rebuilds ``ValidationResult`` instances and appends them
    to the live report so the final summary, score, and exit code
    are byte-identical to the no-cache path.
    """
    for entry in serialised:
        if not isinstance(entry, dict):
            continue
        level = entry.get("level")
        message = entry.get("message")
        if not isinstance(level, str) or not isinstance(message, str):
            continue
        # ``Level`` is a Literal alias erased at runtime, so neither
        # ValidationResult.__init__ nor ValidationReport.add() does ANY
        # runtime validation of the level string. A cross-release or
        # corrupted cache could carry a typo, a stray trailing space
        # ("CRITICAL "), or a foreign value, and the exit-code logic
        # uses exact string equality — an unknown level would be
        # silently mis-bucketed as non-blocking. Route every cached
        # level through normalize_level(): valid levels pass through
        # unchanged ("MAJOR" -> "MAJOR"); anything else collapses to
        # the safe default "INFO".
        result = ValidationResult(
            level=normalize_level(level),
            message=message,
            file=entry.get("file"),
            line=entry.get("line"),
            phase=entry.get("phase"),
            fixable=bool(entry.get("fixable", False)),
            fix_id=entry.get("fix_id"),
            category=str(entry.get("category", "")),
            suggestion=entry.get("suggestion"),
        )
        report.results.append(result)


# Per-language linter config files. Editing one of these changes the lint
# OUTPUT for the same source bytes, so its content MUST be folded into the cache
# key — otherwise an edit to `.markdownlint.json` / ruff config / `.eslintrc`
# returned stale findings for up to the 30-day TTL. (audit MAJOR cache #1)
_LANG_CONFIG_FILENAMES: dict[str, tuple[str, ...]] = {
    "markdown": (
        ".markdownlint.json",
        ".markdownlint.jsonc",
        ".markdownlint.yaml",
        ".markdownlint.yml",
        ".markdownlintrc",
        ".markdownlint-cli2.jsonc",
        ".markdownlint-cli2.yaml",
        ".markdownlint-cli2.cjs",
    ),
    "python": (
        "pyproject.toml",
        "ruff.toml",
        ".ruff.toml",
        "setup.cfg",
        "tox.ini",
        # Type-checker config — folded in so switching/editing the canonical
        # checker (pyright<->mypy) invalidates the lint cache (issue #58).
        "pyrightconfig.json",
        "mypy.ini",
        ".mypy.ini",
    ),
    "javascript": (
        ".eslintrc",
        ".eslintrc.js",
        ".eslintrc.cjs",
        ".eslintrc.json",
        ".eslintrc.yml",
        ".eslintrc.yaml",
        "eslint.config.js",
        "eslint.config.mjs",
        "eslint.config.cjs",
        "package.json",
        "tsconfig.json",
    ),
    "yaml": (".yamllint", ".yamllint.yaml", ".yamllint.yml"),
    "shell": (".shellcheckrc",),
    "rust": ("rustfmt.toml", ".rustfmt.toml", "clippy.toml", ".clippy.toml"),
    "dockerfile": (".hadolint.yaml", ".hadolint.yml"),
    "css": (".stylelintrc", ".stylelintrc.json", ".stylelintrc.yaml", "stylelint.config.js"),
    "sql": (".sqlfluff",),
}


def _config_fingerprint(lang: str, plugin_root: Path) -> str:
    """Hash the resolved linter config files for ``lang`` that exist under
    ``plugin_root`` so editing one invalidates the lint cache. (audit MAJOR cache #1)"""
    h = hashlib.sha256()
    for name in _LANG_CONFIG_FILENAMES.get(lang, ()):
        cfg = plugin_root / name
        try:
            if cfg.is_file():
                h.update(name.encode("utf-8"))
                h.update(b"\0")
                h.update(cfg.read_bytes())
                h.update(b"\0")
        except OSError:
            continue
    return h.hexdigest()


def _build_cache_key(
    lang: str,
    files: list[Path],
    plugin_root: Path,
    *,
    strict_missing_tools: bool,
) -> CacheKey | None:
    """Return a CacheKey for ``lang`` over ``files`` — None if uncacheable.

    Returns None for languages we don't model in ``_LANG_LINTER_ARGS``
    (defensive — every key from ``_DISPATCH`` is mapped today, but a
    new language added without a flag-list entry should miss the
    cache rather than collide with another language's entry).
    """
    flag_list = _LANG_LINTER_ARGS.get(lang)
    if flag_list is None:
        return None
    if not files:
        return None

    # Tree merkle of the language's input files (relative to plugin
    # root, so the merkle is stable across machines).
    merkle = tree_merkle(files, base=plugin_root)

    # The args hash also encodes the strict_missing_tools knob —
    # a strict run vs a soft run can produce different findings
    # for the same file content (a missing tool is MAJOR vs WARNING).
    args = list(flag_list)
    args.append(f"strict_missing_tools={strict_missing_tools}")
    # Fold the resolved linter config content into the key so editing a config
    # file invalidates the cache (audit MAJOR cache #1).
    args.append(f"config_fingerprint={_config_fingerprint(lang, plugin_root)}")
    # Fold this lint engine's own code revision into the key so a CPV fix to
    # output PROCESSING (e.g. issue #132's htmlhint banner strip) invalidates
    # warm cache entries instead of serving stale findings after a CPV upgrade.
    args.append(f"lint_engine_rev={_LINT_ENGINE_CODE_REV}")
    args_hash = sha256_of_args(args)

    primary_tool = _PRIMARY_TOOL.get(lang, lang)
    # stdlib-backed linters (json, toml) don't have a meaningful
    # external version. Tag them with "stdlib" so a stdlib upgrade
    # (Python version bump) invalidates the cache.
    if primary_tool in ("json", "tomllib"):
        scanner_version = f"stdlib-py{sys.version_info.major}.{sys.version_info.minor}"
    else:
        scanner_version = get_scanner_version(primary_tool)

    return CacheKey(
        target_id=f"{plugin_root}::{lang}",
        content_sha256=merkle,
        scanner_name=f"cpv-lint:{lang}",
        scanner_version=scanner_version,
        args_hash=args_hash,
    )


def lint_repo(
    plugin_root: Path,
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
    languages: list[str] | None = None,
    cache: ScannerCache | None = None,
    quiet: bool = False,
) -> bool:
    """Run every applicable linter across the gitignore-filtered tree.

    Args:
        plugin_root: Project root.
        report: Findings sink — MAJOR for hard errors, MINOR for soft
            findings, INFO for tool unavailability in soft mode, WARNING
            for timeouts and missing tools (soft mode).
        strict_missing_tools: When True (default), a missing linter for
            any DETECTED language is recorded as MAJOR and the function
            returns False. When False, the same situation is a WARNING
            and the run continues.
        languages: When supplied, restrict the run to this subset of
            language names. Unknown names are silently skipped.
        cache: Phase D scanner-result cache. When ``None`` (default), a
            ``ScannerCache`` against the user's home cache directory is
            constructed. Tests can pass an isolated cache via
            ``ScannerCache(cache_dir=tmp_path / "cache")``. When the
            cache hits for a language, the cached findings are replayed
            into ``report`` and the linter subprocess is skipped.
        quiet: When True, suppress the two human-readable stdout writes
            this function makes (the ``Detected languages: ...`` line and
            the per-language ``[LABEL] N file(s)`` headers). Findings are
            STILL recorded into ``report`` exactly as before — only the
            decorative terminal output is muted. ``validate_plugin.py``
            passes ``quiet=True`` whenever ``--json`` is active so stdout
            ends up containing ONLY the machine-readable JSON object (the
            ``--json`` contract: stdout = JSON only, human text → stderr).

    Returns:
        True iff no MAJOR/CRITICAL was added by any linter AND no
        missing-tool failure occurred (in strict mode). MINOR/WARNING
        findings do not flip the return value.
    """
    # Issue #148 opt-out: when PLUGIN_SKIP_REPO_LINT is set, skip the whole
    # phase. A downstream CI already running its own linter (Mega-Linter, etc.)
    # uses this to avoid a redundant second lint that — on a cold runner — can
    # also block. Return True (no findings, treated as pass) and record ONE INFO
    # so the report explains the empty result instead of looking like a clean
    # lint that never ran.
    if _repo_lint_disabled():
        if not quiet:
            print("  REPO LINT skipped (PLUGIN_SKIP_REPO_LINT set)")
        report.info("REPO LINT phase skipped via PLUGIN_SKIP_REPO_LINT")
        return True
    if cache is None:
        # Default: a real on-disk cache under the user's home dir.
        # Tests that want isolation pass their own ScannerCache.
        cache = ScannerCache()
    detected = detect_languages(plugin_root)
    if not detected:
        report.info("No source files found to lint")
        return True

    selected = (
        {lang: files for lang, files in detected.items() if lang in set(languages)}
        if languages is not None
        else detected
    )
    if not selected:
        report.info("No files matched the requested language subset: " + ", ".join(sorted(languages or [])))
        return True

    if not quiet:
        print(f"  Detected languages: {', '.join(sorted(selected.keys()))}")

    # Phase B (v2.76.0) — run every applicable linter in parallel.
    # Each lint function is essentially a series of subprocess calls
    # (ruff, eslint, shellcheck, gofmt, …); subprocesses release the
    # GIL while they wait, so a ThreadPoolExecutor gives near-linear
    # speedup without adding any new dependency.
    #
    # Output ordering must remain deterministic (alphabetical by
    # language) regardless of which linter finishes first, so each
    # task writes both its findings and its captured stdout into a
    # per-language buffer, and the main thread replays them in
    # sorted order after the pool drains.
    #
    # IMPORTANT — no `contextlib.redirect_stdout` inside the thread
    # tasks. ``redirect_stdout`` mutates the process-global
    # ``sys.stdout`` reference: with N concurrent threads the last one
    # to exit may restore a stale per-thread buffer instead of the
    # real stdout, swallowing every subsequent write made by the main
    # thread (this exact bug surfaced in early Phase B drafts). All
    # CPV lint helpers route their output through ``report.X(...)``
    # and ``capture_output=True`` subprocesses, so there is no inner
    # ``print()`` to capture. The per-language `[LABEL] N file(s)`
    # header line is the only direct stdout write — we synthesise it
    # explicitly into the per-task buffer here, and replay everything
    # in canonical order after the pool drains.
    sorted_langs = sorted(selected.keys())

    def _run_one(lang: str) -> tuple[str, ValidationReport, str, bool]:
        """Lint one language in isolation.

        Returns ``(lang, per_task_report, header_line, passed)``. The
        per-task report is merged into the caller's ``report`` in
        canonical order; the header line is replayed verbatim so the
        terminal sees exactly the same lines the serial version
        printed (just possibly re-ordered by language).

        Phase D — before invoking the linter, look up a cache entry
        keyed on (plugin_root, lang, file-content merkle, args, scanner
        version). On hit, replay the cached findings into ``local_report``
        and return without spawning any subprocess. On miss, run the
        linter and cache the resulting findings + pass flag.
        """
        local_report = ValidationReport()
        files = selected[lang]
        label = _LANG_LABEL.get(lang, lang.upper())
        # The only stdout write the serial version produced per
        # language — synthesise it here so the main-thread replay
        # below can emit it in alphabetical order.
        header_line = f"  [{label}] {len(files)} file(s)\n"
        lint_fn = _DISPATCH.get(lang)
        if lint_fn is None:
            # Programming error — `detect_languages` returned a key
            # the dispatch table doesn't know about. Fail loud, into
            # this task's local report so the merge step sees it.
            local_report.major(f"No lint function registered for language '{lang}' — CPV dispatch table out of sync")
            return lang, local_report, header_line, False

        # Phase D — cache lookup. Build the key off the file contents
        # and tool versions so the entry is invalidated by ANY drift.
        cache_key = _build_cache_key(lang, files, plugin_root, strict_missing_tools=strict_missing_tools)
        if cache_key is not None:
            cached = cache.get(cache_key)
            if cached is not None and isinstance(cached.get("findings"), list):
                # Cache hit — replay findings into the local report
                # and return WITHOUT invoking any linter subprocess.
                # This is the warm-path win that makes a re-run of
                # `validate_plugin --strict` after a single edit go
                # from ~15s to <2s.
                _replay_results_into_report(cached["findings"], local_report)
                passed = bool(cached.get("passed", True))
                return lang, local_report, header_line, passed

        passed = lint_fn(
            plugin_root,
            files,
            local_report,
            strict_missing_tools=strict_missing_tools,
        )

        # Phase D — write the result back to the cache for future
        # warm runs. Serialise the findings via to_dict() so the
        # cache entry is pure JSON. put() is best-effort: if the
        # write fails, the next run simply re-misses and re-scans.
        #
        # Do NOT cache a NON-DETERMINISTIC outcome (a timeout, or a
        # markdownlint binary/env failure): those depend on run-time
        # conditions, not on the cached inputs, so caching them would
        # pin a transient skip as a durable "clean" result for the full
        # cache TTL and leave the affected files silently unlinted until
        # their content or the tool version changed. Recompute next run.
        if cache_key is not None and not _report_has_non_cacheable_outcome(local_report):
            try:
                serialised = [r.to_dict() for r in local_report.results]
                cache.put(
                    cache_key,
                    {
                        "findings": serialised,
                        "passed": passed,
                        "ts": time.time(),
                    },
                )
            except Exception:
                # Cache writes must NEVER affect lint correctness —
                # swallow any unexpected error and continue.
                pass

        return lang, local_report, header_line, passed

    if not sorted_langs:
        # Defensive — `selected` is non-empty by the early return above,
        # but guard against future refactors that could reach here with
        # an empty dict and accidentally pass `max_workers=0` to the
        # executor (which raises ValueError).
        return True

    # Escape hatch (Agent B2 / task #384): `CPV_LINT_PARALLEL=0` forces
    # the serial fan-out path. Used in three places:
    #   1. Parity tests pinning that serial vs parallel produce
    #      identical findings (same content, same order).
    #   2. Debugging — if a future linter helper turns out NOT to be
    #      thread-safe, a user can fall back without editing source.
    #   3. Single-core CI runners where the ThreadPoolExecutor overhead
    #      is not worth it (rare; default still parallel).
    #
    # Any non-empty value other than "0" / "false" / "no" (case-insensitive)
    # keeps parallel ON — biased towards staying parallel since the
    # speedup is real on the typical workload (15 languages, 8-core box).
    parallel_env = os.environ.get("CPV_LINT_PARALLEL", "1").strip().lower()
    parallel_enabled = parallel_env not in {"0", "false", "no", ""}

    # Issue #162 — the aggregate wall-clock ceiling for the whole phase. Bounds
    # the SUM of the (individually #148-bounded) linters so a cold-runner linter
    # storm can never march REPO LINT past the CI job's own `timeout-minutes`.
    # Shared by both the parallel and serial paths below.
    phase_budget = _phase_timeout()

    results: list[tuple[str, ValidationReport, str, bool]] = []
    if parallel_enabled:
        # max_workers caps at 8 to keep the system responsive on machines
        # with many subprocess-heavy linters configured. Linters never
        # share state, so the pool's only contention is the subprocess
        # spawn syscall and disk IO — both of which scale well beyond 8
        # in practice but plateau in benefit past that point.
        max_workers = min(8, len(sorted_langs))
        # Manual executor lifecycle (NOT a `with` block): on a budget timeout we
        # must `shutdown(wait=False, cancel_futures=True)` to return promptly. A
        # `with` block's __exit__ calls shutdown(wait=True), which would re-block
        # on the very in-flight linters we are trying to escape (issue #162).
        ex = ThreadPoolExecutor(max_workers=max_workers)
        try:
            # `executor.map` preserves input order and yields results as they
            # arrive; `timeout=` is measured from THIS call, making it a true
            # aggregate deadline across every language. On exhaustion __next__
            # raises FuturesTimeoutError, and the languages not yet yielded
            # (sorted_langs[len(results):]) are exactly the ones we skip.
            for outcome in ex.map(_run_one, sorted_langs, timeout=phase_budget):
                results.append(outcome)
        except FuturesTimeoutError:
            skipped = sorted_langs[len(results):]
            report.warning(_phase_budget_skip_message(phase_budget, skipped))
        finally:
            # cancel_futures kills the QUEUED languages immediately; the
            # ≤max_workers in-flight linters are each already per-linter-bounded,
            # so they finish/die on their own ceiling in the background — we do
            # not wait on them (wait=False), so the phase returns promptly.
            ex.shutdown(wait=False, cancel_futures=True)
    else:
        # Serial fallback — preserves byte-identical semantics by calling
        # the same per-language work function (`_run_one`) one at a time,
        # in canonical alphabetical order. Same cache, same dispatch,
        # same report merge order — only the executor is removed.
        phase_start = time.monotonic()
        for idx, lang in enumerate(sorted_langs):
            # Check BEFORE starting each language so the phase stops launching new
            # work once the budget is spent; the language already running is
            # bounded by its own per-linter ceilings, so the overrun is finite.
            # `idx > 0` guarantees at least one language always runs.
            if idx > 0 and time.monotonic() - phase_start >= phase_budget:
                report.warning(_phase_budget_skip_message(phase_budget, sorted_langs[idx:]))
                break
            results.append(_run_one(lang))

    # Replay in canonical (alphabetical) order so logs are stable
    # across runs even if linters finish in different orders. Serial
    # path is already alphabetical (we iterated `sorted_langs`), but
    # the sort is cheap and keeps the contract explicit.
    results.sort(key=lambda t: t[0])

    all_passed = True
    for _lang, local_report, header_line, passed in results:
        if header_line and not quiet:
            sys.stdout.write(header_line)
        report.merge(local_report)
        if not passed:
            all_passed = False

    return all_passed


# ---------------------------------------------------------------------------
# Standalone CLI (legacy compat)
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run lint_repo from the command line.

    Used by `cpv-remote-validate lint` (legacy alias) and any developer
    who wants to invoke linting without the surrounding plugin
    validation pipeline.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="cpv-lint-engine",
        description="Read-only repo lint engine for CPV (15 languages).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Repository root (default: cwd)",
    )
    parser.add_argument(
        "--soft-missing-linters",
        action="store_true",
        help="Treat missing linters as WARNING instead of MAJOR (local dev only).",
    )
    args = parser.parse_args(argv)

    plugin_root = Path(args.path).resolve()
    if not plugin_root.is_dir():
        print(f"Error: {plugin_root} is not a directory", file=sys.stderr)
        return 2

    report = ValidationReport()
    print(f"=== Linting {plugin_root} ===")
    passed = lint_repo(
        plugin_root,
        report,
        strict_missing_tools=not args.soft_missing_linters,
    )

    # Issue #108: surface the actual findings, not just a count. Before the
    # compact summary, print one Detail block per non-PASSED result with its
    # severity, message (which already carries the per-finding rule-code /
    # location / text for ruff), and file:line when known. A clean run has no
    # non-PASSED results, so nothing is printed here — the summary still shows
    # PASSED=N as before.
    detail_levels = ("CRITICAL", "MAJOR", "MINOR", "NIT", "WARNING", "INFO")
    details = [r for r in report.results if r.level in detail_levels]
    if details:
        print()
        print("Details:")
        for r in details:
            loc = ""
            if r.file:
                loc = f" ({r.file}:{r.line})" if r.line else f" ({r.file})"
            print(f"  [{r.level}] {r.message}{loc}")

    # Compact summary
    counts: dict[str, int] = {}
    for r in report.results:
        counts[r.level] = counts.get(r.level, 0) + 1
    print()
    print(
        "Summary: "
        + ", ".join(
            f"{lvl}={counts.get(lvl, 0)}" for lvl in ("CRITICAL", "MAJOR", "MINOR", "WARNING", "INFO", "PASSED")
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
