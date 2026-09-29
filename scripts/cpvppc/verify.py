#!/usr/bin/env python3
"""CPVPPC verify — check a repo against the publishing-pipeline canon.

Verdicts (label, exit code):
    COMPLIANT      0  every applicable assertion passed
    NON-COMPLIANT  1  at least one applicable assertion failed
    UNKNOWN        5  a check could not run — never exit 0
    NOT-DECLARED   6  the repo declares no canon version

Static only in 0.1.0: works on uninstalled checkouts. NEVER executes repo
code: MKT-003 runs the CPV-TEMPLATE renderer (found relative to this file),
and the repo's own renderer is run only when MKT-002 proved it byte-identical
to that template — then it IS the template.

The PyYAML `on:` quirk: YAML 1.1 parses a bare `on` as the boolean True, so a
workflow's trigger key arrives as `True` in safe_load's dict. Workflow reads
here handle both spellings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Bare-CLI entry (`uv run python scripts/cpvppc/verify.py <dir>`) is NOT run as
# a package member, so the sibling import below fails without the same
# bootstrap init.py/pin.py carry (central verification caught the
# ModuleNotFoundError; the suite passed because pytest resolves the package).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpvppc.config_validate import read_config, validate_config  # noqa: E402

EXIT_COMPLIANT = 0
EXIT_NON_COMPLIANT = 1
EXIT_UNKNOWN = 5
EXIT_NOT_DECLARED = 6

_CPV_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_RENDERER = _CPV_ROOT / "templates" / "scripts" / "render_readme_table.py"

# Support window (plan section 5): current + previous minor of the same major,
# plus the last minor of the previous major for 6 months after that major
# shipped. Window history is canon-owned; the current release extends it.
_SUPPORT_WINDOW: dict[str, dict[str, Any]] = {
    "0.1.0": {"current": "0.1.0", "majors": {}},
}
_WINDOW_MONTHS_PREV_MAJOR = 6


def load_canon() -> dict[str, Any]:
    """Load the bundled canon manifest."""
    path = Path(__file__).resolve().parent / "canon.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@dataclass
class Assertion:
    """One canon assertion, parsed from canon.json."""

    id: str
    since: str
    scope: str
    applies_when: str
    fact_type: str
    args: dict[str, Any]
    fix: str


@dataclass
class AssertionResult:
    id: str
    passed: bool
    detail: str


@dataclass
class Verdict:
    label: str
    version: str | None
    failures: list = field(default_factory=list)
    exit_code: int = EXIT_UNKNOWN


def _parse_assertions(canon: dict) -> list[Assertion]:
    registry = set(canon.get("fact_types", []))
    out = []
    for raw in canon.get("assertions", []):
        fact = raw.get("fact", {})
        ftype = fact.get("type", "")
        if ftype not in registry:
            raise ValueError(f"{raw.get('id', '?')}: fact type {ftype!r} is not in the canon registry")
        out.append(
            Assertion(
                id=raw["id"],
                since=raw.get("since", "0"),
                scope=raw.get("scope", ""),
                applies_when=raw.get("applies_when", "always"),
                fact_type=ftype,
                args=dict(fact.get("args", {})),
                fix=raw.get("fix", ""),
            )
        )
    return out


def _read_text(path: Path) -> str | None:
    """Read text handling CRLF; None when the file is missing."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _yaml_load(text: str) -> dict | None:
    """Parse workflow YAML; None when it cannot be parsed (caller decides)."""
    try:
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError:
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _steps_of(doc: dict) -> list[dict]:
    """Collect every step dict from a parsed workflow document."""
    jobs = doc.get("jobs", {})
    if not isinstance(jobs, dict):
        return []
    steps: list[dict] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []) or []:
            if isinstance(step, dict):
                steps.append(step)
    return steps


_HEREDOC_OPEN = re.compile(r"<<-?\s*(?:(['\"])([^'\"]+)\1|([\w-]+))")


def _exec_lines_of(shell: str) -> list[str]:
    """Lines (or compound segments) of a run: block that could execute code.

    Not a full shell parser — bounded heuristics tuned to the two shapes this
    matcher must NOT misjudge (P1 adversarial review):
    - A compound `echo "msg" && <real command>` line is split on `&&`/`;`/`|`
      so the real invocation segment survives (dropping the line whole made a
      compliant repo read NON-COMPLIANT).
    - Heredoc body lines are excluded: a `cat <<EOF` documentation block that
      merely NAMES the renderer must not satisfy the invocation assertion
      (only `#`/`echo`/`printf` prefixes were excluded before, so heredoc
      bodies and `env:`-style values inside run: could pass a mention off as
      an execution). Tags match unquoted, single/double-quoted, and
      space-containing quoted forms; the tag is consumed by the opener line,
      so a body is excluded until its closing tag appears. An unterminated
      heredoc swallows the rest of the block — fail safe (advisory
      NON-COMPLIANT).
    The fail direction of any residual miss is fail-safe: the gate reads
    NON-COMPLIANT (an advisory WARNING by canon, never blocking a publish).
    """
    exec_lines: list[str] = []
    heredoc_tag: str | None = None
    for raw in shell.splitlines():
        line = raw.strip()
        if heredoc_tag is not None:
            if line == heredoc_tag:
                heredoc_tag = None
            continue
        if not line or line.startswith("#"):
            continue
        open_m = _HEREDOC_OPEN.search(line)
        if open_m:
            heredoc_tag = open_m.group(2) or open_m.group(3)
            continue
        head = line
        for segment in re.split(r"&&|\|\||[;|]", head):
            segment = segment.strip()
            if not segment:
                continue
            if segment.startswith(("echo ", "printf ", "cat <<", "cat <<-", "cat >&")):
                continue
            exec_lines.append(segment)
    return exec_lines


def _step_shell_text(step: dict) -> str:
    """The step's script text (run:), or '' — used for --check / write-mode matching."""
    run = step.get("run", "")
    return run if isinstance(run, str) else ""


# --- fact-type implementations ------------------------------------------------


def _fact_file_present(repo: Path, args: dict) -> tuple[bool, str]:
    rel = args.get("path", "")
    text = _read_text(repo / rel)
    if text is None:
        return False, f"missing {rel}"
    needles = args.get("contains", [])
    for needle in needles:
        if needle not in text:
            return False, f"{rel} does not contain {needle!r}"
    start, end = needles[0], needles[1] if len(needles) > 1 else None
    if start and end:
        s_idx = text.find(start)
        e_idx = text.find(end)
        if s_idx > e_idx:
            return False, f"{rel} has {end!r} before {start!r} — markers out of order"
    return True, f"{rel} present with required content"


def _fact_file_matches_render(repo: Path, args: dict) -> tuple[bool, str]:
    rel = args.get("path", "")
    repo_file = repo / rel
    text = _read_text(repo_file)
    if text is None:
        return False, f"missing {rel}"
    template_text = _read_text(_TEMPLATE_RENDERER)
    if template_text is None:
        return False, f"CPV template renderer missing at {_TEMPLATE_RENDERER} — check cannot run"
    if text.replace("\r\n", "\n") != template_text.replace("\r\n", "\n"):
        return False, f"{rel} is not byte-identical to the CPV template renderer"
    return True, f"{rel} is byte-identical to the CPV template renderer"


def _rendered_table(root: Path, renderer: Path, cwd: Path) -> str | None:
    """Run a renderer COPY in check-compare mode and return the would-be README.

    The renderer rewrites README.md in place when run without --check, so this
    works on a THROWAWAY COPY of the repo's manifest+readme inside `cwd`, never
    on the repo itself. Returns None on any failure (caller -> UNKNOWN/FAIL).
    """
    (cwd / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest_src = root / ".claude-plugin" / "marketplace.json"
    readme_src = root / "README.md"
    try:
        (cwd / ".claude-plugin" / "marketplace.json").write_bytes(manifest_src.read_bytes())
        (cwd / "README.md").write_bytes(readme_src.read_bytes())
    except OSError:
        return None
    # --root is required: the renderer defaults it to ITS OWN parent dir
    # (templates/), not the cwd.
    proc = subprocess.run(  # noqa: S603 — fixed argv, no repo influence over the interpreter
        [sys.executable, str(renderer), "--root", str(cwd)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0:
        return None
    out = cwd / "README.md"
    try:
        return out.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _fact_readme_section_current(repo: Path, args: dict, renderer_ok: bool) -> tuple[bool, str]:
    manifest_rel = args.get("manifest", ".claude-plugin/marketplace.json")
    readme_rel = args.get("readme", "README.md")
    if manifest_rel != ".claude-plugin/marketplace.json" or readme_rel != "README.md":
        return False, "unrecognised paths for readme_section_current"
    # Contract: only run when the repo renderer is byte-identical to the
    # template (MKT-002 passed) — then it IS the template. We execute the
    # TEMPLATE, never the repo file, either way.
    if not renderer_ok:
        return False, "repo renderer differs from the CPV template — table currency unverifiable (fix MKT-002 first)"
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="cpvppc-mkt003-") as tmp:
        rendered = _rendered_table(repo, _TEMPLATE_RENDERER, Path(tmp))
    if rendered is None:
        return False, "cannot run: template renderer failed on the repo's manifest/readme"
    repo_readme = _read_text(repo / "README.md")
    if repo_readme is None:
        return False, "README.md missing"
    if repo_readme.replace("\r\n", "\n") != rendered.replace("\r\n", "\n"):
        return False, "README table is stale — run scripts/render_readme_table.py to regenerate"
    return True, "README table matches the renderer output for the current manifest"


def _workflow_step_facts(repo: Path, args: dict) -> tuple[bool, str]:
    wf_dir = repo / ".github" / "workflows"
    if not wf_dir.is_dir():
        return False, "no .github/workflows directory"
    needle = args.get("step_matches", "")
    gate = bool(args.get("gate", False))
    not_matches = args.get("not_matches")
    parse_failed: list[str] = []
    matched: list[str] = []
    for wf in sorted(wf_dir.iterdir()):
        if wf.suffix not in (".yml", ".yaml") or not wf.is_file():
            continue
        text = _read_text(wf)
        if text is None:
            continue
        doc = _yaml_load(text)
        if doc is None:
            parse_failed.append(wf.name)
            continue
        steps = _steps_of(doc)
        for step in steps:
            shell = _step_shell_text(step)
            # A mention inside the shell text is not an invocation: an `echo`
            # or `printf` argument quoting the needle (the update-catalog
            # guard's warning message names the renderer), a `#` comment, or
            # the `env:`-mapping text carry no execution. The command lines
            # decide. The `client_payload.plugin` needle lives in `env:`, so
            # the full-step JSON below still covers mapping-only needles.
            _exec_lines = _exec_lines_of(shell)
            flat_exec = " ".join(" ".join(_exec_lines).split())
            # The step JSON must NOT include `run` again: the dict's run value
            # is the unfiltered shell text, so dumping it whole would
            # re-introduce the echo/comment mentions flat_exec just excluded.
            # env:/name/with: still covered — where `client_payload.plugin`
            # lives.
            step_no_run = {k: v for k, v in step.items() if k != "run"}
            full = flat_exec + " " + json.dumps(step_no_run, sort_keys=True, default=str)
            if needle in full:
                if not_matches and not_matches in flat_exec:
                    continue
                matched.append(wf.name)
    if parse_failed and not matched:
        return False, "cannot run: workflow YAML could not be parsed: " + ", ".join(parse_failed)
    if gate:
        if matched:
            return True, f"--check gate present in: {', '.join(matched)}"
        return False, "no workflow runs the renderer with --check"
    if matched:
        return True, f"matching step present in: {', '.join(matched)}"
    return False, f"no workflow step matches {needle!r}"


def _fact_json_path_equals(repo: Path, args: dict) -> tuple[bool, str]:
    rel = args.get("path", "")
    json_path = args.get("json_path", "")
    text = _read_text(repo / rel)
    if text is None:
        return False, f"missing {rel}"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        return False, f"{rel} is not valid JSON: {err}"
    cur: Any = data
    for part in json_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, f"{rel} has no {json_path!r}"
    if cur is None:
        return False, f"{rel} {json_path!r} is null"
    return True, f"{rel} has top-level {json_path!r}"


def _fact_config_valid(repo: Path, args: dict) -> tuple[bool, str]:
    """cpvppc.yaml parses as YAML and validates against config_schema.json."""
    if args.get("config", "cpvppc.yaml") != "cpvppc.yaml":
        return False, "cannot run: unrecognised config path"
    doc, reason = read_config(repo)
    if doc is None:
        return False, reason
    errs = validate_config(doc)
    if errs:
        return False, "cpvppc.yaml is invalid: " + "; ".join(errs)
    return True, "cpvppc.yaml parses and validates against config_schema.json"


def _fact_lock_consistent(repo: Path, args: dict) -> tuple[bool, str]:
    """.cpvppc-lock.json matches the current cpvppc.yaml bytes + canon version."""
    lock_rel = args.get("lock", ".cpvppc-lock.json")
    text = _read_text(repo / lock_rel)
    if text is None:
        return False, f"missing {lock_rel}"
    try:
        lock = json.loads(text)
    except json.JSONDecodeError as err:
        return False, f"{lock_rel} is not valid JSON: {err}"
    if not isinstance(lock, dict):
        return False, f"{lock_rel} is not a JSON object"
    raw = (repo / args.get("config", "cpvppc.yaml")).read_bytes()
    cfg_sha = hashlib.sha256(raw).hexdigest()
    if lock.get("config_sha256") != cfg_sha:
        return False, (
            f"{lock_rel} recorded config_sha256 does not match the current cpvppc.yaml bytes — regenerate the lock"
        )
    canon = load_canon()
    if lock.get("canon_version") != canon["canon_version"]:
        return False, (
            f"{lock_rel} canon_version {lock.get('canon_version')!r} != current canon {canon['canon_version']!r}"
        )
    doc, reason = read_config(repo)
    if doc is None:
        return False, f"cannot run: {reason}"
    # Principle 7: everything inferred is written explicitly into the lock.
    if lock.get("config") != doc:
        return False, f"{lock_rel} does not record every cpvppc.yaml field explicitly (principle 7)"
    return True, "lock matches the current config bytes, canon version, and records the config in full"


def support_window_versions(release: str) -> set[str]:
    """Canon versions the window admits for a declared `release` version.

    Rule (plan 5): the current and previous minor of the release's major,
    plus the last minor of the previous major while it is within 6 months of
    that major's ship date. Data comes from _SUPPORT_WINDOW (canon-owned).
    """
    try:
        cur_maj, cur_min, _ = (int(x) for x in release.split("."))
    except ValueError:
        return set()
    out = {f"{cur_maj}.{m}.0" for m in (cur_min, cur_min - 1) if m >= 0}
    entry = _SUPPORT_WINDOW.get(release)
    if entry is None:
        return out
    prev = entry.get("majors", {}).get(f"{cur_maj - 1}")
    if prev is None:
        return out
    ship = _parse_iso(prev["shipped"])
    if ship is None:
        return out
    # +6 months, stdlib-only: cap the day at 28 so February never overflows.
    m = ship.month - 1 + _WINDOW_MONTHS_PREV_MAJOR
    horizon = date(ship.year + m // 12, m % 12 + 1, min(ship.day, 28))
    if date.today() <= horizon:
        out.add(prev["last_minor"])
    return out


def _parse_iso(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def run_assertions(repo_root: Path, canon_version: str) -> list[AssertionResult]:
    """Run every canon assertion applicable to `repo_root` under `canon_version`.

    Static facts only. UNKNOWN-when-unrunnable is expressed as passed=False
    with a detail beginning "cannot run:" so the verdict layer can lift it to
    UNKNOWN rather than a plain failure. Assertions are filtered by scope
    against the repo's declared kind (plan section 5): a plugin repo is not
    judged by marketplace-repo assertions and vice versa. With no config the
    repo falls back to the marketplace scope (the P1 surface — the only
    assertions 0.1.0 had before the config existed).
    """
    canon = load_canon()
    results: list[AssertionResult] = []

    doc, _ = read_config(repo_root)
    kind = doc.get("repo", {}).get("kind") if doc else None
    if kind == "plugin":
        scopes = {"plugin-repo"}
    elif kind == "marketplace":
        scopes = {"marketplace-repo"}
    elif kind == "plugin+marketplace":
        scopes = {"plugin-repo", "marketplace-repo"}
    elif (repo_root / "cpvppc.yaml").is_file():
        # The config exists but could not be parsed — the repo is still a
        # CPVPPC repo, and CFG-001 is exactly the assertion that must fire
        # ("not valid YAML" is a failure, never a scope miss).
        scopes = {"plugin-repo", "marketplace-repo"}
    else:
        scopes = {"marketplace-repo"}

    # Pre-compute the byte-identity gate for MKT-003 (read file only, never run).
    repo_renderer = repo_root / "scripts" / "render_readme_table.py"
    template_text = _read_text(_TEMPLATE_RENDERER)
    repo_renderer_text = _read_text(repo_renderer)
    renderer_ok = bool(
        template_text is not None
        and repo_renderer_text is not None
        and repo_renderer_text.replace("\r\n", "\n") == template_text.replace("\r\n", "\n")
    )

    for a in _parse_assertions(canon):
        if a.since != canon_version:
            continue
        if a.scope and a.scope not in scopes:
            continue
        args = a.args
        if a.fact_type == "file_present":
            ok, detail = _fact_file_present(repo_root, args)
        elif a.fact_type == "file_matches_render":
            ok, detail = _fact_file_matches_render(repo_root, args)
        elif a.fact_type == "readme_section_current":
            ok, detail = _fact_readme_section_current(repo_root, args, renderer_ok)
        elif a.fact_type == "workflow_step":
            ok, detail = _workflow_step_facts(repo_root, args)
        elif a.fact_type == "json_path_equals":
            ok, detail = _fact_json_path_equals(repo_root, args)
        elif a.fact_type == "config_valid":
            ok, detail = _fact_config_valid(repo_root, args)
        elif a.fact_type == "lock_consistent":
            ok, detail = _fact_lock_consistent(repo_root, args)
        else:  # pragma: no cover — registry-gated above
            ok, detail = False, f"cannot run: fact type {a.fact_type!r} has no implementation"
        results.append(AssertionResult(id=a.id, passed=ok, detail=detail))
    return results


def _declaration_version(repo_root: Path) -> str | None:
    """The repo's declared canon version, or None.

    cpvppc.yaml is parsed as YAML and `canon.version` read properly (the
    P1 line-scan could not see nested keys). The generated publish.py
    CANON_VERSION convention stays as the fallback for repos not yet
    migrated to the config file.
    """
    doc, _ = read_config(repo_root)
    if doc is not None:
        v = doc.get("canon", {}).get("version") if isinstance(doc.get("canon"), dict) else None
        if isinstance(v, str) and v:
            return v
    pub = repo_root / "scripts" / "publish.py"
    ptext = _read_text(pub)
    if ptext:
        for line in ptext.splitlines():
            s = line.strip()
            if s.startswith("CANON_VERSION"):
                val = s.split("=", 1)[1].strip().strip("\"'")
                if val:
                    return val
    return None


def verdict(results: list[AssertionResult], declared_version: str | None) -> Verdict:
    """Fold assertion results + declaration into the final Verdict."""
    if declared_version is None:
        return Verdict(label="NOT-DECLARED", version=None, failures=[], exit_code=EXIT_NOT_DECLARED)
    failures = [r for r in results if not r.passed]
    unknown = [r for r in failures if r.detail.startswith("cannot run:")]
    if unknown:
        return Verdict(label="UNKNOWN", version=declared_version, failures=unknown, exit_code=EXIT_UNKNOWN)
    if failures:
        return Verdict(label="NON-COMPLIANT", version=declared_version, failures=failures, exit_code=EXIT_NON_COMPLIANT)
    return Verdict(label="COMPLIANT", version=declared_version, failures=[], exit_code=EXIT_COMPLIANT)


def _best_passing_canon(repo_root: Path, versions: list[str]) -> str | None:
    """The highest canon version whose static assertions all pass — for NOT-DECLARED output."""
    best: str | None = None

    def vkey(v: str) -> tuple[int, int, int]:
        try:
            return tuple(int(x) for x in v.split("."))  # type: ignore[return-value]
        except ValueError:
            return (0, 0, 0)

    for v in sorted(versions, key=vkey):
        results = run_assertions(repo_root, v)
        if all(r.passed for r in results):
            best = v
    return best


def render_text(v: Verdict) -> str:
    """Human-readable verdict block."""
    lines: list[str] = []
    if v.label == "NOT-DECLARED":
        lines.append("CPVPPC verdict: NOT-DECLARED (no canon version declared in this repo)")
    else:
        lines.append(f"CPVPPC verdict: {v.label} (canon {v.version})")
    for f in v.failures:
        lines.append(f"  FAIL {f.id}: {f.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cpvppc-verify", description=__doc__)
    ap.add_argument("repo", type=Path, help="path to the repo to verify")
    ap.add_argument("--canon", metavar="X.Y.Z", help="verify against this canon version instead of the declared one")
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return EXIT_UNKNOWN

    canon = load_canon()
    versions = sorted({a["since"] for a in canon.get("assertions", [])})

    target_version = args.canon or _declaration_version(repo)
    if target_version is None:
        best = _best_passing_canon(repo, versions)
        if best is not None:
            print(f"NOT-DECLARED: no canon version declared. Highest canon version whose static assertions pass: {best}")
        else:
            print("NOT-DECLARED: no canon version declared, and no canon version's assertions all pass.")
        return EXIT_NOT_DECLARED
    if target_version not in versions:
        print(f"error: canon {target_version} is not a known canon version (known: {', '.join(versions)})", file=sys.stderr)
        return EXIT_UNKNOWN
    if target_version not in support_window_versions(target_version):
        window = ", ".join(sorted(support_window_versions(target_version)))
        print(f"NON-COMPLIANT (canon {target_version}): version outside the support window (inside: {window})")
        return EXIT_NON_COMPLIANT

    results = run_assertions(repo, target_version)
    v = verdict(results, target_version)
    print(render_text(v))
    return v.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
