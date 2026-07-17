#!/usr/bin/env python3
"""Snyk Agent Scan wrapper — instruction-surface scan, opt-in, never runs an MCP server.

Invokes https://github.com/snyk/agent-scan (Apache-2.0, PyPI `snyk-agent-scan`)
and adapts its findings into CPV's ValidationReport. It adds an LLM-based second
opinion CPV's own regex engines do not have: prompt injection, malware payloads,
untrusted-content and credential-handling risks in agent instruction surfaces.

Coverage note (measured, not assumed — see the tests): the upstream tool natively
discovers ONLY skills (a `<name>/SKILL.md` directory) and MCP servers. Pointed at
a plugin's `agents/`, `commands/`, `hooks/`, or `rules/` it finds NOTHING. To cover
those surfaces — which is where an injection payload actually lives — CPV STAGES
each `agents/*.md`, `commands/**/*.md`, `rules/**/*.md`, and hook SCRIPT into an
ephemeral synthetic `<slug>/SKILL.md` folder, scans that, and REMAPS every finding
back to the real component path (via `reference[0] -> servers[idx].server.path ->
staging manifest`) so the report names the true source, never a temp path.

THE FOUR INVARIANTS BELOW ARE LOAD-BEARING SECURITY PROPERTIES.
Read them before changing anything in this file.

1. WE NEVER HAND THE SCANNER A CONFIG FILE — ONLY DIRECTORIES.
   Upstream's own README: "Scanning MCP configurations will execute the
   commands defined in them ... it starts the stdio MCP servers by executing
   the commands and arguments specified in the config." That is REPRODUCED
   against v0.5.15: aiming the scanner at an `.mcp.json` with
   `--dangerously-run-mcp-servers` executed the `command` in it (a canary
   `touch` fired). CPV's whole job is scanning UNTRUSTED, NOT-YET-INSTALLED
   plugins (see `cpv-pre-install-scan`), so letting this tool reach a scanned
   plugin's `.mcp.json` would execute that plugin's attacker-chosen
   `{"command": ..., "args": [...]}` DURING CPV's own security scan — turning
   the validator into the exploit.

   The bright line is the TARGET KIND: a skill/staging DIRECTORY, never a
   config file. Verified safe with that same live canary — with `.mcp.json`
   planted BOTH inside `skills/` and at the plugin root, `scan --json --skills
   <dir>` executed NEITHER, and the result held only `type: "skill"` entities.
   The STAGING tree is built by CPV from a fixed allowlist of `.md` files and
   script files (never an `.mcp.json`), so it cannot smuggle a config in either.
   `build_scan_command()` REFUSES any target that is not an existing directory.

2. `--ci` IS BANNED BECAUSE IT REQUIRES `--dangerously-run-mcp-servers`.
   Upstream `scan --help`: "--ci  Exit with a non-zero code when there are
   analysis findings or runtime failures. Requires --dangerously-run-mcp-servers."
   The ONE flag that would give a usable exit code is welded to the flag that
   starts every stdio server in scope. We take neither and read findings out of
   `--json`. `FORBIDDEN_FLAGS` is regression-locked so nobody reintroduces them.

3. "CANNOT CHECK" IS NOT "CLEAN".
   With no `SNYK_TOKEN`, the tool exits 1 and — under `--json` — prints NOTHING
   on either stream (verified empirically against v0.5.15). A naive parser reads
   that as an empty finding list and reports GREEN, i.e. a scan that never ran
   looks identical to a scan that passed. `run_snyk_agent_scan()` therefore
   returns `invoked=False` for EVERY path that did not yield parseable JSON, and
   the caller surfaces that as a visible WARNING. `invoked=False` is never
   collapsed into "no findings".

4. STAGING IS EPHEMERAL AND NAME-ONLY.
   The staging tree lives under the system temp dir, is populated only by CPV
   from the allowlisted surfaces, and is torn down in a `finally` before the
   result is returned (findings are parsed and paths remapped first — the
   manifest is just strings, it outlives the tree). No plugin file is mutated.
   The synthetic `SKILL.md` wraps the ORIGINAL component text verbatim as its
   body so the analysis sees exactly what ships.

OPT-IN, AND WHY: this scanner hard-requires a `SNYK_TOKEN` (a free Snyk
account) and is cloud-backed — it sends scanned content to Snyk's analysis
server. CPV must stay usable offline, and a validator must not ship a user's
private plugin source to a third party by default. So the scan runs ONLY when
the operator has exported a token; absent one, it is skipped with a WARNING
naming the variable and the page to get it. A WARNING never blocks `--strict`,
which is correct for an opt-in external scanner — but it is never silent.

UPSTREAM CONTRACT IS EXPERIMENTAL. Snyk states: "The raw output of this CLI —
including issue codes, field names, severity labels, and response structure —
is experimental and may change without notice." Every parser here is therefore
defensive: an unrecognised shape degrades to `invoked=False` (visible skip),
never to a silent zero-finding pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Public constants ─────────────────────────────────────────────────

SNYK_TOKEN_ENV = "SNYK_TOKEN"
SNYK_TOKEN_HELP_URL = "https://app.snyk.io/account"
SNYK_SIGNUP_URL = "https://snyk.io"

#: The skill manifest. Used to RECOGNISE a single-skill plugin (whose skill
#: folder is the plugin root) and as the synthetic filename staging writes;
#: never passed as a target itself — targets are always directories.
SKILL_MANIFEST_NAME = "SKILL.md"

#: Non-skill instruction surfaces CPV stages into synthetic skills so the
#: scanner (which natively sees only skills) covers them too. Each maps a
#: plugin subdir to a component kind label used in the reported finding.
_STAGED_MARKDOWN_SURFACES: dict[str, str] = {
    "agents": "agent",
    "commands": "command",
    "rules": "rule",
}

#: Hook SCRIPT extensions staged as bundled files inside a synthetic skill so
#: the scanner reads them (bundled non-.md files are analysed — verified).
_HOOK_SCRIPT_SUFFIXES: frozenset[str] = frozenset({".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts"})

#: Flags that would make the scanner execute code out of the tree it is
#: scanning (or reach outside it). Never emitted; asserted by tests.
FORBIDDEN_FLAGS: frozenset[str] = frozenset(
    {
        "--dangerously-run-mcp-servers",  # starts every stdio MCP server in scope
        "--ci",  # upstream: "Requires --dangerously-run-mcp-servers"
        "--scan-all-users",  # reaches outside the scanned plugin entirely
    }
)

# Snyk severity vocabulary → CPV ValidationReport levels. Identical to the
# Cisco mapping in cpv_skill_scanner.py; both external scanners speak the same
# critical/high/medium/low/info scale.
_SNYK_TO_CPV_SEVERITY: dict[str, str] = {
    "critical": "critical",
    "high": "major",
    "medium": "minor",
    "low": "nit",
    "info": "info",
}

_VALID_SNYK_SEVERITIES: frozenset[str] = frozenset(_SNYK_TO_CPV_SEVERITY)


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on a blank/garbage value.

    This is evaluated at IMPORT for DEFAULT_TIMEOUT_SECONDS, so a bare
    ``int(os.environ[...])`` here would let a malformed ``CPV_SNYK_SCAN_TIMEOUT_S``
    raise ValueError and crash the whole validate_security import — a typo in an
    env var must never take CPV's security scan down with it.
    """
    try:
        return int((os.environ.get(name, "") or "").strip() or default)
    except ValueError:
        return default


#: Bounded execution. The cold uvx resolve dominates the first run; the analysis
#: is a network round-trip per staged component. Override with
#: CPV_SNYK_SCAN_TIMEOUT_S=<seconds> for very large plugins.
DEFAULT_TIMEOUT_SECONDS = _env_int("CPV_SNYK_SCAN_TIMEOUT_S", 600)

#: Pinned to the package name; `@latest` mirrors upstream's documented
#: invocation. The output contract is experimental (see module docstring), so
#: every parser below tolerates drift rather than trusting this version.
SNYK_PACKAGE_SPEC = "snyk-agent-scan@latest"

TOKEN_MISSING_REASON = (
    f"{SNYK_TOKEN_ENV} is not set. Snyk Agent Scan requires a Snyk API token and will not run "
    f"without one. Register free at {SNYK_SIGNUP_URL} and copy your token from "
    f"{SNYK_TOKEN_HELP_URL} (API Token -> KEY -> click to show), then export {SNYK_TOKEN_ENV} "
    f"in your environment and re-run. Note this scanner is cloud-backed: scanned content is "
    f"sent to Snyk for analysis."
)


# ── Result types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SnykFinding:
    """One normalised issue from a `snyk-agent-scan --json` run.

    ``entity_path`` is the ABSOLUTE path of the scanned entity the issue
    references (a real skill folder, or an ephemeral staged folder). It is the
    key the caller remaps back to a real plugin-relative component path via the
    scan result's ``staging_manifest``. Empty for a global (non-entity) issue.
    """

    severity: str  # CPV-canonical: critical/major/minor/nit/info
    code: str  # Snyk issue code (e.g. "E004", "W008")
    message: str  # Human-readable finding text
    entity_path: str  # abs path of the referenced entity, "" if global
    raw: dict[str, Any]  # Original issue object (for debugging)


@dataclass(frozen=True)
class SnykScanResult:
    """Aggregate result of one `snyk-agent-scan scan` invocation.

    ``invoked`` is the load-bearing field: True ONLY when the scanner ran to
    completion AND emitted parseable JSON. Every other outcome — missing token,
    missing launcher, no scannable surface, timeout, empty output, unparseable
    output — sets it False so the caller reports "skipped", never "clean".

    ``staging_manifest`` maps an ABSOLUTE staged-folder path to
    ``(real_plugin_relative_path, component_kind)`` so ``report_findings`` can
    name the true source of a finding rather than the temp path scanned.
    """

    invoked: bool
    findings: tuple[SnykFinding, ...]
    skipped_reason: str  # Empty when invoked; explains why otherwise
    scan_errors: tuple[str, ...]  # Per-path errors the scanner itself reported
    staging_manifest: dict[str, tuple[str, str]] = field(default_factory=dict)
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int = -1  # subprocess exit code; negative when not invoked


# ── Availability probes ──────────────────────────────────────────────


def is_snyk_token_present() -> bool:
    """True iff a non-empty ``SNYK_TOKEN`` is exported.

    A whitespace-only value counts as absent: the scanner would reject it and
    exit 1 with no output, which is the failure mode invariant 3 exists to
    stop us from reading as a clean scan.
    """
    return bool(os.environ.get(SNYK_TOKEN_ENV, "").strip())


def is_launcher_available() -> bool:
    """True iff we can launch the scanner at all.

    Accepts the persistent ``snyk-agent-scan`` console script (from
    ``uv tool install snyk-agent-scan``) or the ephemeral ``uvx`` fallback,
    mirroring how cpv_skill_scanner.py handles the Cisco scanner.
    """
    return shutil.which("snyk-agent-scan") is not None or shutil.which("uvx") is not None


# ── Target selection & staging (invariants 1 and 4) ──────────────────


def native_skill_targets(plugin_path: Path) -> tuple[Path, ...]:
    """The skill DIRECTORIES the scanner discovers natively.

    A skill is its whole folder — the scanner must see the bundled ``scripts/``
    and ``references/`` next to the manifest, because that is where a real
    payload lives (measured: a directory target flags a planted
    ``scripts/helper.sh``; a manifest-only target cannot see it).

    The scanner resolves a directory that either CONTAINS skill folders or IS
    one; it does NOT recurse to find a nested `skills/` (measured: the plugin
    root as target discovers nothing). So:
      * ``<plugin>/skills`` when present — the canonical layout and upstream's
        own documented form (``snyk-agent-scan ~/.claude/skills``);
      * the plugin root when the plugin IS a single skill (a root SKILL.md).
    """
    skills_dir = plugin_path / "skills"
    if skills_dir.is_dir():
        return (skills_dir,)
    if (plugin_path / SKILL_MANIFEST_NAME).is_file():
        return (plugin_path,)
    return ()


def _slugify(text: str) -> str:
    """Filesystem-safe, collision-resistant slug for a staged folder name."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in text) or "x"


def _write_synthetic_skill_manifest(dest: Path, name: str, kind: str, body: str) -> None:
    """Write a synthetic SKILL.md wrapping ``body`` verbatim.

    The scanner requires a YAML frontmatter with ``name`` and ``description``
    (it raises otherwise), so we supply a minimal synthetic block and place the
    ORIGINAL component text as the body — including any frontmatter the original
    had, which the scanner then treats as ordinary body text. The analysis
    therefore sees exactly what ships (invariant 4).
    """
    dest.write_text(
        f"---\nname: {name}\ndescription: CPV-staged {kind} component for security analysis\n---\n\n{body}",
        encoding="utf-8",
    )


def build_staged_tree(plugin_path: Path) -> tuple[Path | None, dict[str, tuple[str, str]]]:
    """Stage non-skill instruction surfaces into a synthetic-skill tree.

    Returns ``(staging_root, manifest)`` where the manifest maps each staged
    folder's ABSOLUTE path to ``(real_plugin_relative_path, kind)``. The caller
    OWNS the returned directory and must remove it (run_snyk_agent_scan does).
    Returns ``(None, {})`` when there is nothing to stage — the caller then
    skips adding a staging target rather than scanning an empty dir.

    Staged, from a FIXED allowlist (never an `.mcp.json`, upholding invariant 1):
      * every ``agents/**/*.md``, ``commands/**/*.md``, ``rules/**/*.md`` →
        ``<slug>/SKILL.md`` wrapping the file's text;
      * every hook SCRIPT (``hooks/**/*.{sh,py,js,...}``) → ``<slug>/SKILL.md``
        (a minimal synthetic manifest) plus the script copied in beside it, so
        the scanner reads the script as a bundled file.
    """
    manifest: dict[str, tuple[str, str]] = {}
    # .resolve() the temp root: on macOS mkdtemp returns /var/folders/... but
    # the scanner reports its entities under the realpath'd /private/var/...
    # (the /var -> /private/var symlink). Keying the manifest on the resolved
    # path makes it match what `servers[].server.path` reports, so a staged
    # finding remaps to its real component instead of leaking the temp path.
    staging_root = Path(tempfile.mkdtemp(prefix="cpv-snyk-stage-")).resolve()
    used_slugs: set[str] = set()

    def _unique_folder(rel: str) -> Path:
        base = _slugify(rel)
        slug = base
        n = 1
        while slug in used_slugs:
            n += 1
            slug = f"{base}__{n}"
        used_slugs.add(slug)
        folder = staging_root / slug
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    # Everything below can touch the filesystem (mkdir / write / copy / read).
    # Any failure must (a) never leak the temp dir this function created — the
    # caller has not entered its own try/finally yet — and (b) never silently
    # DROP a component, because a component that was not staged is a component
    # Snyk never scanned, and reporting that as clean is the exact "cannot check
    # != clean" trap invariant 3 forbids. So we do NOT swallow a per-file error:
    # it propagates, the partial tree is torn down here, and the caller turns it
    # into a VISIBLE skip (a whole-pass skip with a named cause beats a silent
    # single-file coverage hole).
    try:
        # Markdown instruction surfaces (agents / commands / rules).
        for subdir, kind in _STAGED_MARKDOWN_SURFACES.items():
            surface_dir = plugin_path / subdir
            if not surface_dir.is_dir():
                continue
            for md in sorted(p for p in surface_dir.rglob("*.md") if p.is_file()):
                rel = md.relative_to(plugin_path).as_posix()
                body = md.read_text(encoding="utf-8", errors="ignore")
                folder = _unique_folder(rel)
                _write_synthetic_skill_manifest(folder / SKILL_MANIFEST_NAME, folder.name, kind, body)
                manifest[str(folder)] = (rel, kind)

        # Hook scripts (staged as bundled files beside a minimal synthetic manifest).
        hooks_dir = plugin_path / "hooks"
        if hooks_dir.is_dir():
            for script in sorted(
                p for p in hooks_dir.rglob("*") if p.is_file() and p.suffix.lower() in _HOOK_SCRIPT_SUFFIXES
            ):
                rel = script.relative_to(plugin_path).as_posix()
                folder = _unique_folder(rel)
                _write_synthetic_skill_manifest(
                    folder / SKILL_MANIFEST_NAME,
                    folder.name,
                    "hook",
                    f"Staged hook script `{script.name}` — analysed as the bundled file in this folder.",
                )
                shutil.copyfile(script, folder / script.name)
                manifest[str(folder)] = (rel, "hook")
    except OSError:
        # Tear down the partial tree we created, then re-raise so the caller
        # surfaces a visible skip instead of leaking the dir or scanning half.
        shutil.rmtree(staging_root, ignore_errors=True)
        raise

    if not manifest:
        # Nothing staged — remove the empty temp dir and signal "no staging".
        shutil.rmtree(staging_root, ignore_errors=True)
        return None, {}

    return staging_root, manifest


def build_scan_command(
    targets: "tuple[Path, ...] | list[Path]",
    *,
    package_spec: str = SNYK_PACKAGE_SPEC,
) -> list[str]:
    """Build the argv for an instruction-surface, non-executing scan.

    Raises ValueError rather than emitting an unsafe command:
      * no targets — an argument-less invocation makes the scanner scan every
        well-known config location on the machine (including MCP configs),
        which is exactly what invariant 1 forbids;
      * any target that is not an existing DIRECTORY — the bright line that
        keeps every config file out by construction (a config is a file).

    ``--skills`` is passed explicitly even though it is upstream's current
    default, so a future default flip cannot silently turn this into an
    MCP-only scan. ``--json`` is required because `--ci` is banned (invariant
    2), leaving the JSON payload as our only finding channel.
    """
    if not targets:
        raise ValueError(
            "refusing to build a snyk-agent-scan command with no explicit targets: "
            "an argument-less scan auto-discovers and executes MCP configs machine-wide"
        )
    for target in targets:
        if not Path(target).is_dir():
            raise ValueError(
                f"refusing to scan {str(target)!r}: CPV only ever hands snyk-agent-scan a "
                f"DIRECTORY. A non-directory target would miss a skill's bundled scripts, and a "
                f"config file target would make the scanner execute that config's server commands."
            )

    if shutil.which("snyk-agent-scan"):
        prefix: list[str] = ["snyk-agent-scan"]
    else:
        prefix = ["uvx", package_spec]

    cmd: list[str] = [*prefix, "scan", "--json", "--skills"]
    cmd.extend(str(t) for t in targets)
    return cmd


# ── Parsing (defensive: the upstream contract is experimental) ────────


def derive_severity(issue: dict[str, Any]) -> str:
    """Return the Snyk severity for one issue object.

    Severity is NOT a top-level field — upstream derives it, and we mirror
    `agent_scan.printer.get_severity` exactly:
        code starts with "X"          -> info
        extra_data["severity"] if set -> that value
        code starts with "W"          -> medium
        code starts with "E"          -> high
        otherwise                     -> info

    ONE DELIBERATE DIVERGENCE: upstream raises ValueError on a severity value
    outside its vocabulary. Raising here would abort a whole CPV scan because
    one external tool drifted its own enum, so we fall back to upstream's own
    code-prefix rule instead — keeping an `E...` issue at `high` (-> MAJOR)
    rather than silently demoting an unrecognised severity to info.
    """
    code = str(issue.get("code") or "")
    if code.startswith("X"):
        return "info"

    extra = issue.get("extra_data")
    raw = extra.get("severity") if isinstance(extra, dict) else None
    if isinstance(raw, str) and raw in _VALID_SNYK_SEVERITIES:
        return raw

    if code.startswith("W"):
        return "medium"
    if code.startswith("E"):
        return "high"
    return "info"


def _coerce_payload(json_blob: "str | bytes | dict[str, Any]") -> dict[str, Any] | None:
    """Return the top-level dict, or None when the payload is not one."""
    if isinstance(json_blob, (str, bytes)):
        try:
            data = json.loads(json_blob)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    else:
        data = json_blob
    return data if isinstance(data, dict) else None


def _entity_path_for(issue: dict[str, Any], servers: list[Any]) -> str:
    """Resolve an issue's referenced entity path via ``reference[0]``.

    The Issue's ``reference`` is ``(server_index, entity_index)`` (or None for a
    global issue). We map ``server_index`` into this entry's ``servers`` list
    and take ``servers[i].server.path`` — the scanned skill/staged folder. That
    path is the key the caller remaps to a real component. Returns "" when the
    issue is global or the reference cannot be resolved.
    """
    reference = issue.get("reference")
    if not (isinstance(reference, (list, tuple)) and reference):
        return ""
    idx = reference[0]
    if not isinstance(idx, int) or not (0 <= idx < len(servers)):
        return ""
    server = servers[idx]
    if not isinstance(server, dict):
        return ""
    inner = server.get("server")
    if not isinstance(inner, dict):
        return ""
    return str(inner.get("path") or "")


def parse_findings(json_blob: "str | bytes | dict[str, Any]") -> tuple[SnykFinding, ...]:
    """Convert `snyk-agent-scan --json` output into SnykFinding tuples.

    Payload shape (verified against v0.5.15 `cli.print_scan_inspect`):
        {"<scanned_path>": {"path", "servers": [ {"server": {"path", "type"}} ],
                            "issues": [ {"code","message","reference","extra_data"} ]}}
    Each issue references a server by index; we resolve that to the entity path.
    """
    data = _coerce_payload(json_blob)
    if data is None:
        return ()

    findings: list[SnykFinding] = []
    for path_key, result in data.items():
        if not isinstance(result, dict):
            continue
        issues = result.get("issues")
        if not isinstance(issues, list):
            continue
        servers = result.get("servers")
        servers = servers if isinstance(servers, list) else []
        scanned_path = str(result.get("path") or path_key)
        for raw in issues:
            if not isinstance(raw, dict):
                continue
            snyk_severity = derive_severity(raw)
            findings.append(
                SnykFinding(
                    severity=_SNYK_TO_CPV_SEVERITY.get(snyk_severity, "minor"),
                    code=str(raw.get("code") or "snyk.unknown"),
                    message=str(raw.get("message") or ""),
                    entity_path=_entity_path_for(raw, servers) or scanned_path,
                    raw=raw,
                )
            )
    return tuple(findings)


def parse_scan_errors(json_blob: "str | bytes | dict[str, Any]") -> tuple[str, ...]:
    """Extract per-path scan errors the scanner reported about itself.

    A path whose `error.is_failure` is true was NOT successfully analysed. If
    we dropped these, a plugin whose every skill errored out would report zero
    findings and read as clean — invariant 3 at per-path granularity.
    """
    data = _coerce_payload(json_blob)
    if data is None:
        return ()

    errors: list[str] = []
    for path_key, result in data.items():
        if not isinstance(result, dict):
            continue
        err = result.get("error")
        if not isinstance(err, dict):
            continue
        if not err.get("is_failure", True):
            continue
        detail = err.get("message") or err.get("exception") or "unknown error"
        errors.append(f"{result.get('path') or path_key}: {detail}")
    return tuple(errors)


# ── Invocation ───────────────────────────────────────────────────────


def run_snyk_agent_scan(
    plugin_path: Path,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> SnykScanResult:
    """Run the instruction-surface scan and return parsed, remap-ready findings.

    Stages the non-skill surfaces (invariant 4), scans native skills plus the
    staging tree in one invocation, parses findings, then tears the staging
    tree down before returning. Every non-success path returns
    ``invoked=False`` with a stated reason (invariant 3).
    """
    if not is_snyk_token_present():
        return _skipped(TOKEN_MISSING_REASON, exit_code=-1)

    if not is_launcher_available():
        return _skipped(
            "neither `snyk-agent-scan` nor `uvx` on PATH — run `cpv-doctor --install-scanners` "
            "or `pip install uv && uv tool install snyk-agent-scan`",
            exit_code=-1,
        )

    try:
        staging_root, manifest = build_staged_tree(plugin_path)
    except OSError as exc:
        # build_staged_tree already tore down its partial tree; a staging
        # failure is a VISIBLE skip (invariant 3), never a silent gap or crash.
        return _skipped(
            f"Snyk Agent Scan staging failed ({exc}) — treating as NOT SCANNED, not as clean",
            exit_code=-4,
        )
    try:
        targets: list[Path] = [*native_skill_targets(plugin_path)]
        if staging_root is not None:
            targets.append(staging_root)

        if not targets:
            return _skipped(
                f"no skills, agents, commands, rules, or hook scripts found under {plugin_path} — "
                f"nothing for Snyk Agent Scan to do",
                exit_code=-1,
            )

        cmd = build_scan_command(targets)

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return _skipped(
                f"Snyk Agent Scan timed out after {timeout_seconds}s (override CPV_SNYK_SCAN_TIMEOUT_S)",
                exit_code=-2,
                raw_stdout=_decode(exc.stdout),
                raw_stderr=_decode(exc.stderr),
            )
        except (FileNotFoundError, OSError) as exc:
            return _skipped(f"snyk-agent-scan invocation failed: {exc}", exit_code=-3, raw_stderr=str(exc))

        stdout = completed.stdout or ""

        # THE INVARIANT-3 GATE. With no token, `--json` exits 1 having printed
        # nothing on either stream (verified). Parsing that as "[] findings" is
        # precisely how a scan that never ran becomes a green check, so an empty
        # or unparseable payload is a SKIP, not a pass.
        if not stdout.strip():
            return _skipped(
                f"Snyk Agent Scan produced no output (exit {completed.returncode}) — treating as NOT SCANNED, "
                f"not as clean; re-run with --print-errors to see why",
                exit_code=completed.returncode,
                raw_stderr=completed.stderr or "",
            )

        payload = _coerce_payload(stdout)
        if payload is None:
            return _skipped(
                f"Snyk Agent Scan emitted unparseable output (exit {completed.returncode}) — treating as "
                f"NOT SCANNED, not as clean; Snyk documents the CLI output contract as experimental",
                exit_code=completed.returncode,
                raw_stdout=stdout,
                raw_stderr=completed.stderr or "",
            )

        return SnykScanResult(
            invoked=True,
            findings=parse_findings(payload),
            skipped_reason="",
            scan_errors=parse_scan_errors(payload),
            staging_manifest=manifest,
            raw_stdout=stdout,
            raw_stderr=completed.stderr or "",
            exit_code=completed.returncode,
        )
    finally:
        if staging_root is not None:
            # Ephemeral scratch we created this run under the system temp dir —
            # trivially regeneratable, so a plain rmtree is the right tool.
            shutil.rmtree(staging_root, ignore_errors=True)


def _skipped(
    reason: str,
    *,
    exit_code: int,
    raw_stdout: str = "",
    raw_stderr: str = "",
) -> SnykScanResult:
    """Build a not-invoked result. Centralised so no path can forget a reason."""
    return SnykScanResult(
        invoked=False,
        findings=(),
        skipped_reason=reason,
        scan_errors=(),
        staging_manifest={},
        raw_stdout=raw_stdout,
        raw_stderr=raw_stderr,
        exit_code=exit_code,
    )


def _decode(stream: "str | bytes | None") -> str:
    """Normalise a TimeoutExpired stream (bytes or str or None) to str."""
    if stream is None:
        return ""
    return stream.decode(errors="ignore") if isinstance(stream, bytes) else stream


# ── Reporting ────────────────────────────────────────────────────────


def resolve_component(
    finding: SnykFinding,
    plugin_path: Path,
    manifest: dict[str, tuple[str, str]],
) -> tuple[str, str]:
    """Map a finding to ``(real_plugin_relative_path, component_kind)``.

    A staged finding's ``entity_path`` is an ephemeral temp folder — remapping
    it through the manifest is what makes the report name the REAL agent /
    command / rule / hook file instead of a path that no longer exists. A native
    skill finding is relativised against the plugin root.
    """
    ep = finding.entity_path
    if ep in manifest:
        return manifest[ep]
    # Defensive: match on the realpath too, in case the scanner reported a path
    # that differs from the manifest key only by a symlinked prefix. resolve()
    # is strict=False so a since-deleted staged leaf still normalises via its
    # surviving ancestors.
    try:
        resolved = str(Path(ep).resolve())
    except OSError:
        resolved = ep
    if resolved in manifest:
        return manifest[resolved]
    # A GLOBAL (reference=None) issue on the staged-tree scan resolves to the
    # staging ROOT — the parent of every manifest key, but not itself a key. Do
    # not leak that ephemeral temp path (it is deleted by the time we report);
    # label it generically so the report stays meaningful.
    if manifest:
        staging_roots = {str(Path(k).parent) for k in manifest}
        if ep in staging_roots or resolved in staging_roots:
            return "<staged instruction surfaces>", "staged"
    return _relativise(ep, plugin_path), "skill"


def report_findings(
    result: SnykScanResult,
    plugin_path: Path,
    report: Any,
    should_skip: "Callable[[str, int | None], bool] | None" = None,
) -> int:
    """Adapt a SnykScanResult into ValidationReport calls.

    Returns the count of findings appended (0 when skipped or fully filtered).

    A skip is a WARNING, not INFO: the operator asked for this scanner's
    coverage and did not get it, so it must be visible. WARNING never blocks
    `--strict`, which is the right severity for an opt-in cloud scanner.

    Findings are remapped to the REAL component path before both the skip
    filter and the report call, so the self-scan filters act on real paths and
    the user reads the true source. The component kind is shown for a staged
    surface (agent/command/rule/hook) so a finding rendered against a staged
    file is not mistaken for a skill finding. Snyk issues carry no line.
    """
    if not result.invoked:
        report.warning(f"Snyk Agent Scan skipped — {result.skipped_reason}", "<external-scanner>")
        return 0

    # Per-path failures first: a surface the scanner could not analyse did not pass.
    for scan_error in result.scan_errors:
        report.warning(f"[snyk] path not analysed — {scan_error}", "<external-scanner>")

    appended = 0
    for finding in result.findings:
        rel_file, kind = resolve_component(finding, plugin_path, result.staging_manifest)
        if should_skip is not None and should_skip(rel_file, None):
            continue
        tag = f"snyk {finding.code}" if kind == "skill" else f"snyk {finding.code} · {kind}"
        message = f"[{tag}] {finding.message}".strip()
        if finding.severity == "info":
            report.info(message, rel_file)
        else:
            method = getattr(report, finding.severity, None) or report.minor
            method(message, rel_file, None)
        appended += 1
    return appended


def _relativise(file_path: str, plugin_root: Path) -> str:
    """Return a path relative to plugin_root if possible, else the original."""
    if not file_path:
        return "<unknown>"
    try:
        return str(Path(file_path).relative_to(plugin_root))
    except ValueError:
        return file_path
