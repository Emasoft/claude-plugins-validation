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

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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
from cpv_validation_common import ValidationReport, ValidationResult, resolve_tool_command
from gitignore_filter import GitignoreFilter

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


def lint_python(
    repo_root: Path,
    files: list[Path],
    report: ValidationReport,
    *,
    strict_missing_tools: bool = True,
) -> bool:
    """Lint Python files with ruff (errors) and mypy (warnings)."""
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
        result = subprocess.run(
            ruff_cmd
            + [
                "check",
                "--select=E,F,W,I",
                "--ignore=E501,E402",
                "--output-format=concise",
                *targets,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("ruff timed out after 120s — skipping Python lint")
        return True

    if result.returncode == 0:
        report.passed(f"ruff check passed for {len(files)} Python file(s)")
    else:
        errors_by_file: dict[str, int] = {}
        for line in (result.stdout or "").splitlines():
            if line and ":" in line:
                file_part = line.split(":", 1)[0].strip()
                if file_part:
                    errors_by_file[file_part] = errors_by_file.get(file_part, 0) + 1
        for file_path_str, count in sorted(errors_by_file.items()):
            rel = _relpath(repo_root, file_path_str)
            report.major(f"Ruff: {count} error(s) in {rel}", rel)
        if not errors_by_file and (result.stdout or "").strip():
            report.major("Ruff: error(s) across Python files")
        ok = False

    # mypy — type warnings only (non-blocking). Scope limited to files under
    # scripts/ to match the pre-v2.64 validate_scripts behaviour: type-checking
    # the whole repo (especially test files) surfaces mountains of
    # annotation-unchecked notes that have nothing to do with plugin
    # publishability. The lint_repo orchestrator's primary signal is ruff.
    mypy_targets = [str(f) for f in files if "scripts" in f.parts]
    if not mypy_targets:
        return ok
    mypy_cmd = _resolve("mypy")
    if mypy_cmd:
        try:
            mypy_result = subprocess.run(
                mypy_cmd
                + [
                    "--ignore-missing-imports",
                    "--exclude",
                    "scripts_dev|docs_dev|builds_dev|tests_dev",
                    *mypy_targets,
                ],
                capture_output=True,
                text=True,
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
        result = subprocess.run(
            eslint_cmd + ["--format=json", *targets],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("eslint timed out after 120s — skipping JS/TS lint")
        return True

    if result.returncode == 0:
        report.passed(f"eslint passed for {len(files)} JS/TS file(s)")
        return True

    try:
        data = json.loads(result.stdout) if result.stdout else []
    except json.JSONDecodeError:
        report.major("eslint: produced non-JSON output — see logs")
        return False

    ok = True
    for file_result in data:
        rel = _relpath(repo_root, file_result.get("filePath", ""))
        for msg in file_result.get("messages", []):
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
            result = subprocess.run(
                cmd + ["-f", "json", "-x", str(f)],
                capture_output=True,
                text=True,
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
        result = subprocess.run(
            gofmt_cmd + ["-l", *targets],
            cwd=repo_root,
            capture_output=True,
            text=True,
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
        vet_result = subprocess.run(
            go_cmd + ["vet", "./..."],
            cwd=repo_root,
            capture_output=True,
            text=True,
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
        fmt_result = subprocess.run(
            cargo_cmd + ["fmt", "--check"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("cargo fmt --check timed out")
        return True

    if fmt_result.returncode != 0:
        report.major("cargo fmt: formatting issues found (run 'cargo fmt')")
        ok = False

    try:
        clippy_result = subprocess.run(
            cargo_cmd + ["clippy"],
            cwd=repo_root,
            capture_output=True,
            text=True,
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

    try:
        result = subprocess.run(
            invocation + file_paths,
            cwd=repo_root,
            capture_output=True,
            text=True,
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
    for line in output.splitlines():
        if not line.strip():
            continue
        report.nit(f"markdownlint: {line.strip()}")
        surfaced += 1
        if surfaced >= 20:
            break
    # Silent-failure surface (issue #20): if markdownlint exited non-zero
    # but produced no parseable per-line output, the developer used to see
    # only "CPV blocked the push (exit 3)" with no clue what failed. Now
    # we always emit at least one finding carrying the raw stderr/stdout.
    if not surfaced:
        if output:
            report.nit(f"markdownlint: {output[:200]}")
        else:
            report.warning(
                f"markdownlint exited non-zero (rc={result.returncode}) but "
                f"produced no output — possible binary or environment issue"
            )
    return False


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
        result = subprocess.run(
            cmd + ["-d", "relaxed", "--format", "parsable", *file_paths],
            cwd=repo_root,
            capture_output=True,
            text=True,
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
            result = subprocess.run(
                cmd + [str(f)],
                capture_output=True,
                text=True,
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
    """Lint XML files with xmllint --noout."""
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
            result = subprocess.run(
                cmd + ["--noout", str(f)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            report.warning(f"xmllint timed out on {rel}")
            continue
        if result.returncode == 0:
            report.passed(f"xmllint: {rel} OK")
            continue
        for line in (result.stderr or "").splitlines()[:5]:
            stripped = line.strip()
            if stripped:
                report.major(f"xmllint: {stripped}", rel)
                ok = False
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
        result = subprocess.run(
            cmd + file_paths,
            cwd=repo_root,
            capture_output=True,
            text=True,
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
    return False


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
        result = subprocess.run(
            cmd + file_paths,
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        report.warning("htmlhint timed out — skipping HTML lint")
        return True

    if result.returncode == 0:
        report.passed(f"htmlhint passed for {len(files)} HTML file(s)")
        return True

    for line in (result.stdout or "").splitlines()[:20]:
        stripped = line.strip()
        if stripped:
            report.minor(f"htmlhint: {stripped}")
    return False


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
        result = subprocess.run(
            cmd + ["lint", "--dialect", "ansi", *file_paths],
            cwd=repo_root,
            capture_output=True,
            text=True,
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
    return False


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

    ok = True
    for f in files:
        rel = _relpath(repo_root, str(f))
        try:
            result = subprocess.run(
                cmd + ["-Path", str(f), "-Severity", "Error,Warning"],
                capture_output=True,
                text=True,
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
                ok = False
    return ok


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
    # invocation. mypy is auxiliary and runs only on scripts/, but
    # we don't bother modelling it here: a mypy version bump still
    # invalidates via the scanner_version field on a separate
    # cache key, AND mypy findings are MINOR (non-blocking) so a
    # stale cached "no mypy issues" outcome is conservative.
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
        # ValidationResult takes ``Level`` (a Literal alias). The
        # cache may have come from a different CPV release so we
        # defensively coerce by string-equality against the known
        # set inside ValidationReport.add() — invalid levels would
        # raise there, but every level we ever emit is in the
        # standard set, so passing the string through is fine.
        result = ValidationResult(
            level=level,  # type: ignore[arg-type]
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

    Returns:
        True iff no MAJOR/CRITICAL was added by any linter AND no
        missing-tool failure occurred (in strict mode). MINOR/WARNING
        findings do not flip the return value.
    """
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
        if cache_key is not None:
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

    # max_workers caps at 8 to keep the system responsive on machines
    # with many subprocess-heavy linters configured. Linters never
    # share state, so the pool's only contention is the subprocess
    # spawn syscall and disk IO — both of which scale well beyond 8
    # in practice but plateau in benefit past that point.
    max_workers = min(8, len(sorted_langs))
    results: list[tuple[str, ValidationReport, str, bool]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        # `executor.map` preserves input order and is the simplest
        # way to fan out + collect; we re-sort below anyway in case
        # the dispatch order changes in a future refactor.
        for outcome in ex.map(_run_one, sorted_langs):
            results.append(outcome)

    # Replay in canonical (alphabetical) order so logs are stable
    # across runs even if linters finish in different orders.
    results.sort(key=lambda t: t[0])

    all_passed = True
    for _lang, local_report, header_line, passed in results:
        if header_line:
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
