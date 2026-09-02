"""TRDD-EZHM759T — the canon-template audit fixes, one test per fixed row.

Source: reports/publish-audit/20260902_092801+0200-canon-template-audit.md (31
rows). Rows 13 and 22 live in ``cpv_network_resilience.py`` and are out of scope
here; every other row that is testable on the RENDERED template gets a test
below, each docstring naming its row.

Two shapes of assertion are used deliberately:

* text assertions on the rendered template, where the fix IS a spelling the
  emitted file must carry (a flag, a branch list, an exemption entry);
* ``ast.parse`` + ``exec`` of the pure helpers, where the fix is BEHAVIOUR and a
  text pin would pass against a stub.

Plus a positive control: the rendered file still parses, and ``--print-gates``
prints exactly as many numbered stages as the ``[N/M]`` labels claim — the
guard that stops row 31 from silently regressing.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_plugin_repo import (  # noqa: E402
    PluginParams,
    gen_notify_marketplace_yml,
    gen_pre_push_hook,
    gen_publish_py,
    gen_pyproject_toml,
    generate_all_files,
)


def _params(**overrides: object) -> PluginParams:
    """A PluginParams with sensible defaults, accepting field overrides."""
    defaults: dict[str, object] = {
        "name": "my-test-plugin",
        "description": "A test plugin",
        "author": "Test Author",
        "author_email": "test@example.com",
        "license": "MIT",
        "python_version": "3.12",
        "github_owner": "test-owner",
        "marketplace": "test-marketplace",
        "version": "0.1.0",
    }
    defaults.update(overrides)
    return PluginParams(**defaults)  # type: ignore[arg-type]


def _src() -> str:
    """The rendered standard-profile publish.py."""
    return gen_publish_py(_params())


def _code_only(text: str) -> str:
    """`text` with whole-line `#` comments dropped.

    A comment that NAMES an anchor is indistinguishable from the anchor, and
    every fix here ships a comment explaining itself — so a bare `x not in src`
    would be satisfied by the rationale that says the defect is gone.
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _exec_helpers(names: tuple[str, ...], extra_names: tuple[str, ...] = ()) -> dict:
    """Exec the named top-level defs (and module constants) out of the rendered
    template, in isolation.

    Only pure helpers are lifted this way; anything needing the module's colour
    constants or `cprint` is asserted on text instead.
    """
    import os
    import subprocess

    tree = ast.parse(_src())
    wanted: list = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in extra_names for t in node.targets
        ):
            wanted.append(node)
    found = {n.name for n in wanted if isinstance(n, ast.FunctionDef)}
    assert found == set(names), f"missing helper(s): {set(names) - found}"
    module = ast.Module(body=wanted, type_ignores=[])
    ns: dict = {"os": os, "subprocess": subprocess, "Path": Path, "sys": sys}
    exec(compile(module, "<canon>", "exec"), ns)  # noqa: S102 - the code under test
    return ns


# ── positive control ────────────────────────────────────────────────────────


def test_rendered_template_still_parses() -> None:
    """Positive control: every fix below is applied to a file that still compiles."""
    compile(_src(), "publish.py", "exec")


def test_print_gates_output_matches_the_stage_label_count() -> None:
    """Control for row 31: --print-gates, the [N/M] labels and the docstring agree.

    The labels are literals (several canon tests exec ONE stage function out of
    the template, where a module-level constant is not in scope), so this is the
    guard that keeps them honest against `_PIPELINE_STAGES`.
    """
    src = _src()
    labels = {int(n) for n, m in re.findall(r"\[(\d+)/(\d+)\]", src)}
    totals = {int(m) for _n, m in re.findall(r"\[(\d+)/(\d+)\]", src)}
    assert totals, "no [N/M] stage labels found — the scan is vacuous"
    assert len(totals) == 1, f"stage labels disagree on the total: {sorted(totals)}"
    total = totals.pop()
    assert labels == set(range(1, total + 1)), (
        f"labels {sorted(labels)} are not a complete 1..{total} run"
    )
    assert f"Full release pipeline ({total} stages" in src, "docstring total disagrees"

    # A COUNT check is not a WIRING check: deleting a stage CALL from main()
    # would leave the labels and --print-gates saying 15 while the pipeline ran
    # 14. Assert every numbered stage is actually invoked.
    main_body = src[src.index("def main() -> int:"):src.index('if __name__ == "__main__":')]
    # `stage_bypass_guard()` takes no arguments — anchor on the call, not on `root`.
    called = set(re.findall(r"^    (stage_\w+)\(", main_body, re.MULTILINE))
    defined = set(re.findall(r"^def (stage_\w+)\(", src, re.MULTILINE))
    assert defined - called == set(), f"stage(s) defined but never called by main(): {defined - called}"
    # 15 numbered + the 2 unnumbered post-release stages.
    assert len(called) == total + 2, (
        f"main() calls {len(called)} stages; expected {total} numbered + 2 post-release"
    )
    # A NAME check is not a CALL check either: `stage_x` could appear in main()
    # as a bare reference. Every stage but the argument-less bypass guard must be
    # invoked as `stage_x(root...`.
    for name in sorted(defined - {"stage_bypass_guard"}):
        assert f"{name}(root" in main_body, f"{name} is never called with `root` in main()"
    assert "stage_bypass_guard()" in main_body


def test_print_gates_runs_with_no_side_effects(tmp_path: Path) -> None:
    """Row 31 / CHECK-24: `--print-gates` prints the stage list and exits 0.

    Run from a NON-git temp dir on purpose: an information command must answer
    without a repo, a clean tree, or a network.
    """
    script = tmp_path / "publish.py"
    script.write_text(_src(), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script), "--print-gates"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    total = int(re.search(r"\[(\d+)/(\d+)\]", _src()).group(2))  # type: ignore[union-attr]
    assert f"Publish pipeline stages ({total}):" in proc.stdout
    for n in range(1, total + 1):
        assert f"{n:>2}/{total}." in proc.stdout, f"stage {n} missing from --print-gates"
    assert "Post-release stages" in proc.stdout
    # No side effects: nothing written beside the script we placed.
    assert [p.name for p in tmp_path.iterdir()] == ["publish.py"]


# ── one test per fixed row ──────────────────────────────────────────────────


def test_row1_g3_blocks_on_a_validator_that_failed_to_run() -> None:
    """Row 1: G3 fail-CLOSED — an exit >= 5 is FAILED-TO-RUN, never 'passed'."""
    src = _src()
    assert "ve != 0 and ve < 5" not in _code_only(src), "the fail-open comparison is still there"
    assert "the validator FAILED TO RUN (exit {ve})" in src
    # The pass branch must be reachable ONLY on exit 0.
    assert "if ve == 0:" in src


def test_row2_posix_only_guard_is_loud_in_both_gate_and_hook() -> None:
    """Row 2: Windows is refused explicitly, not blocked silently by a dead `ps`."""
    msg = "publish.py canon is POSIX-only (macOS/Linux); Windows is unsupported"
    assert msg in _src()
    assert 'os.name != "posix"' in _src()
    assert msg in gen_pre_push_hook(_params())


def test_row3_ci_preflight_carries_a_timeout() -> None:
    """Row 3: stage_ci_preflight bounds its uvx call and BLOCKS on a timeout."""
    src = _src()
    stage = src[src.index("def stage_ci_preflight("):src.index("# ── Marketplace-registration helpers")]
    assert "timeout=_CPV_TIMEOUT_SEC" in stage
    assert "CI-parity preflight timed out" in stage


def test_row4_one_shared_validator_budget_for_both_call_sites() -> None:
    """Row 4: stage_validate and gate G3 pass the SAME named constant."""
    src = _src()
    stage = src[src.index("def stage_validate("):src.index("def stage_secret_scan(")]
    assert "cwd=root, timeout=_CPV_TIMEOUT_SEC)" in stage, "stage_validate still inherits run()'s default"
    gate = src[src.index("[G3] Validating plugin"):src.index("[G3s] Secret scan")]
    assert "timeout=_CPV_TIMEOUT_SEC" in gate
    assert "_CPV_TIMEOUT_SEC = 600.0" in src


def test_row5_install_smoke_flag_is_exempt_from_the_bypass_guard() -> None:
    """Row 5: PLUGIN_SKIP_INSTALL_SMOKE skips the smoke test instead of aborting."""
    src = _src()
    guard = src[src.index("def stage_bypass_guard("):src.index("def stage_check_clean(")]
    assert '"PLUGIN_SKIP_INSTALL_SMOKE",' in guard, "the documented flag still aborts at stage 1"
    # It is still READ where it is documented.
    assert 'os.environ.get("PLUGIN_SKIP_INSTALL_SMOKE") == "1"' in src


def test_row6_secret_scan_is_a_pipeline_stage_not_only_a_gate() -> None:
    """Row 6: main() runs stage_secret_scan; both paths share one implementation."""
    src = _src()
    assert "def stage_secret_scan(" in src
    assert "    stage_secret_scan(root)\n" in src
    # ONE implementation, called from both.
    assert src.count("def _secret_scan(") == 1
    assert "if _secret_scan(root) != 0:" in src
    # ...and it runs BEFORE anything is written or pushed.
    assert src.index("    stage_secret_scan(root)\n") < src.index("    stage_bump(root, new_ver")


def test_row7_a_stale_tag_is_moved_to_head_or_refused() -> None:
    """Row 7: never push a previous attempt's tag; fail closed when unsure."""
    src = _src()
    assert "def _ensure_tag_at_head(" in src
    assert "already exists locally — skipping tag step" not in src
    assert src.count("_ensure_tag_at_head(root,") == 2, "release + dependency tag must both route through it"
    # Fail-closed on both "cannot read the remote" and "already published".
    assert "REFUSING to move" in src
    assert "ALREADY ON ORIGIN" in src


def test_row8_notify_workflow_triggers_on_master_and_main() -> None:
    """Row 8: a `master`-default plugin must still notify its marketplace."""
    wf = gen_notify_marketplace_yml(_params())
    assert "branches: [master, main]" in wf
    assert "branches: [main]" not in wf


def test_row9_binary_release_profile_emits_release_binaries_yml() -> None:
    """Row 9: the binary-release profile gets its own release workflow — and only it."""
    def _paths(profile: str) -> set[str]:
        return {rel for rel, _content, _x in generate_all_files(_params(), profile)}

    binary = _paths("binary-release")
    assert ".github/workflows/release-binaries.yml" in binary
    assert ".github/workflows/release.yml" not in binary, "two release workflows would double-cut a release"
    for other in ("standard", "submodule-build", "remote-validation"):
        paths = _paths(other)
        assert ".github/workflows/release.yml" in paths, other
        assert ".github/workflows/release-binaries.yml" not in paths, other


def test_row10_submodule_helpers_are_defined_before_the_entry_point() -> None:
    """Row 10: the submodule-build helpers are REACHED, not stranded.

    They used to be appended AFTER the standard body's `sys.exit(main())`, so the
    process exited before their defs ran and nothing called them. They are now
    spliced in ahead of the guard and driven by `stage_submodule_release`.
    """
    variant = gen_publish_py(_params(), "submodule-build")
    assert "sys.exit(main())" in variant
    # rindex, not index: the spliced section's own COMMENT quotes the guard it
    # was moved ahead of, and `.index` finds that comment rather than the real
    # entry point — a comment naming an anchor is indistinguishable from the
    # anchor. The EXECUTED guard is the last one in the file.
    guard = variant.rindex('if __name__ == "__main__":')
    assert variant.index("def submodule_source_changed(") < guard, (
        "the helpers are defined after the entry point — main() can never reach them"
    )
    assert "stage_submodule_release(root, new_ver, args.dry_run)" in variant
    for helper in (
        "submodule_source_changed",
        "submodule_clean_tree_ok",
        "submodule_commit_before_gitlink",
        "ensure_submodule_pushed",
    ):
        assert f"{helper}(" in variant.split("def stage_submodule_release(")[1]


def test_row11_ci_verify_subprocesses_are_all_bounded() -> None:
    """Row 11: no unbounded gh/git call inside the CI-verify deadline loop."""
    src = _src()
    block = src[src.index("def stage_verify_ci_green("):src.index("# -- Main ---")]
    calls = re.findall(r"subprocess\.run\(\s*\[(.*?)\]", block, re.DOTALL)
    assert calls, "no subprocess calls found — the slice is wrong"
    for chunk in re.split(r"subprocess\.run\(", block)[1:]:
        head = chunk[: chunk.index(")\n")] if ")\n" in chunk else chunk
        assert "timeout=" in head, f"unbounded subprocess call: {head[:80]!r}"


def test_row12_install_branch_rules_bounds_its_uvx_call() -> None:
    """Row 12: a cold uvx build cannot hang `--install-branch-rules` forever."""
    src = _src()
    block = src[src.index("def install_branch_rules("):src.index("# -- Gate mode")]
    assert "timeout=_CPV_TIMEOUT_SEC" in block
    assert "cpv-setup-branch-rules timed out" in block


def test_row14_fork_parity_is_a_pipeline_stage_not_only_a_gate() -> None:
    """Row 14: main() runs stage_fork_parity; both paths share one implementation."""
    src = _src()
    assert "def stage_fork_parity(" in src
    assert "    stage_fork_parity(root)\n" in src
    assert src.count("def _fork_parity_probe(") == 1
    assert src.count("_fork_parity_probe(root, ") == 2, "gate and stage must both call it"


def test_row15_install_hook_reports_a_failed_hookspath_write() -> None:
    """Row 15: a failed `git config core.hooksPath` no longer prints success."""
    src = _src()
    block = src[src.index("def install_hook("):src.index("def _get_origin_slug(")]
    assert "cfg = subprocess.run(" in block
    assert "if cfg.returncode != 0:" in block
    assert "FAILED to set git config core.hooksPath" in block


def test_row16_trufflehog_installer_timeout_is_caught() -> None:
    """Row 16: a stalled brew/go install lands on the styled BLOCKED path."""
    src = _src()
    block = src[src.index("def _secret_scan("):src.index("def _fork_parity_probe(")]
    # The install attempt sits between the first probe and the block-if-still-missing.
    first = block.index('if not shutil.which("trufflehog"):')
    second = block.index('if not shutil.which("trufflehog"):', first + 10)
    install = block[first:second]
    assert "except subprocess.TimeoutExpired:" in install
    assert "The trufflehog installer timed out" in install


def test_row17_gate_lint_budget_absorbs_a_cold_uv_sync() -> None:
    """Row 17: G2's ruff run gets 300s, not 120 — `uv run` may create the venv first."""
    src = _src()
    block = src[src.index("[G2] Linting"):src.index("[G2b] Copy-paste check")]
    assert 'cwd=str(root), timeout=300)' in block
    assert "timeout=120)" not in block


def test_row18_origin_slug_read_is_bounded() -> None:
    """Row 18: `_get_origin_slug` passes a timeout like every sibling git helper."""
    src = _src()
    block = src[src.index("def _get_origin_slug("):src.index("def install_branch_rules(")]
    assert "timeout=10" in block


def test_row19_receiver_workflow_probe_has_an_aggregate_bound() -> None:
    """Row 19: the per-file `gh api` loop is bounded in files AND wall clock."""
    src = _src()
    block = src[src.index("def _remote_has_receiver_workflow("):src.index("def _plugin_in_remote_marketplace(")]
    assert "_RECEIVER_PROBE_MAX_FILES" in block
    assert "_RECEIVER_PROBE_DEADLINE_S" in block
    assert "time.monotonic() >= deadline" in block


def test_row20_only_pytests_own_process_group_is_killed() -> None:
    """Row 20: browser cleanup requires process-group ownership, not a time window.

    BEHAVIOURAL and two-sided, deliberately. A substring pin plus the
    ``owner_pgid=None`` guard would pass just as happily if ``_ps_table``'s new
    ``pid,pgid,command`` parse were broken — the function would then return 0
    forever, which is indistinguishable from "there were no orphans". So this
    spawns a real process wearing a browser signature and asserts BOTH ends: a
    foreign group spares it, our own group kills it.
    """
    import os
    import subprocess as sp
    import time

    ns = _exec_helpers(
        ("_ps_table", "_snapshot_browser_pids", "_cleanup_browser_orphans"),
        extra_names=("_BROWSER_ORPHAN_SIGNATURES",),
    )
    # No owner pgid => nothing is killed, rather than falling back to a global diff.
    assert ns["_cleanup_browser_orphans"](set(), None) == 0

    baseline = ns["_snapshot_browser_pids"]()
    # `exec -a` renames argv[0], so `ps` reports a browser signature for a plain
    # sleep — no browser needed, and nothing real can be hit by mistake.
    kid = sp.Popen(["/bin/bash", "-c", "exec -a headless_shell sleep 20"])
    try:
        time.sleep(1)
        seen = ns["_snapshot_browser_pids"]() - baseline
        assert kid.pid in seen, "probe is broken: the fake browser was never seen by _ps_table"

        # Foreign process group: spared.
        assert ns["_cleanup_browser_orphans"](baseline, os.getpgrp() + 99999) == 0
        assert kid.poll() is None, "a browser outside pytest's process group was killed"

        # Our own group (the one stage_tests runs pytest in): killed.
        assert ns["_cleanup_browser_orphans"](baseline, os.getpgrp()) == 1
        time.sleep(1)
        assert kid.poll() is not None, "an owned orphan survived the cleanup"
    finally:
        if kid.poll() is None:
            kid.kill()
            kid.wait()

    assert "owner_pgid = os.getpgrp()" in _src(), "stage_tests does not pass an owner group"


def test_row21_registry_path_honours_claude_config_dir(monkeypatch) -> None:
    """Row 21: `$CLAUDE_CONFIG_DIR` is respected instead of a hardcoded ~/.claude."""
    ns = _exec_helpers(("_claude_config_dir",))
    ns["os"] = __import__("os")
    ns["Path"] = Path
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/some-config-dir")
    assert ns["_claude_config_dir"]() == Path("/tmp/some-config-dir")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert ns["_claude_config_dir"]() == Path.home() / ".claude"


def test_row23_pool_scan_survives_a_directory_named_dot_py() -> None:
    """Row 23: a `*.py` DIRECTORY (or an unreadable file) no longer aborts G4b."""
    src = _src()
    block = src[src.index("def _fork_parity_probe("):src.index("\ndef run_gate(")]
    assert "if not p.is_file():" in block
    assert "except OSError:" in block


def test_row24_pyproject_names_no_phantom_pre_commit_hook() -> None:
    """Row 24: extend-include lists only a hook the scaffold actually emits."""
    toml = gen_pyproject_toml(_params())
    assert 'extend-include = ["git-hooks/pre-push"]' in toml
    emitted = {rel for rel, _c, _x in generate_all_files(_params())}
    assert "git-hooks/pre-commit" not in emitted, "the phantom entry would now be real"


def test_row25_no_self_hashes_file_is_staged_without_a_generator() -> None:
    """Row 25: `.plugin-self-hashes.json` is not staged — nothing generates it."""
    src = _src()
    block = _code_only(src[src.index("def stage_commit_and_push("):src.index("def stage_gh_release(")])
    assert ".plugin-self-hashes.json" not in block


def test_row26_a_lone_uv_lock_does_not_abort_the_publish() -> None:
    """Row 26: the issue-#149 carve-out — uv.lock ALONE is auto-committed."""
    src = _src()
    block = src[src.index("def stage_check_clean("):src.index("def stage_lint(")]
    assert 'run(["git", "add", "--", "uv.lock"], cwd=root)' in block
    # ...and it is narrow: any OTHER dirty path still aborts.
    assert "Working tree is dirty" in block

    # The carve-out keys on the STATUS CODE, not the path alone. Exercise the
    # predicate itself against real `git status --porcelain` lines: keying on
    # `ln[3:]` would auto-commit an UNTRACKED or CONFLICTED lockfile, and the
    # first of those contradicts the #186 never-sweep-untracked rule this same
    # file enforces at the commit stage.
    predicate = re.search(
        r"if \(len\(dirty\) == 1 and (dirty\[0\]\[:2\] in \([^)]*\))\s*\n\s*and (dirty\[0\]\[3:\]\.strip\(\) == \"uv\.lock\")\)",
        block,
    )
    assert predicate, "the status-code-keyed carve-out predicate was not found"
    expr = f"len(dirty) == 1 and {predicate.group(1)} and {predicate.group(2)}"

    def _carves(line: str) -> bool:
        # eval() is deliberate and safe here: `expr` is not input of any kind —
        # it is the canon's OWN predicate, lifted verbatim from the rendered
        # template by the anchored regex above, which is why this test measures
        # the shipped condition instead of a retyped copy that could drift.
        # ast.literal_eval cannot evaluate a comparison, and a hand-written
        # reimplementation is exactly the proxy this test exists to avoid.
        return bool(eval(expr, {"dirty": [line]}))  # noqa: S307

    assert _carves(" M uv.lock"), "a modified lockfile is the whole point"
    assert _carves("M  uv.lock")
    assert not _carves("?? uv.lock"), "an UNTRACKED lockfile must not be auto-committed"
    assert not _carves("UU uv.lock"), "a CONFLICTED lockfile must not be auto-committed"
    assert not _carves(" M src/app.py"), "only uv.lock is carved out"
    assert not _carves('R  old.lock -> uv.lock'), "a rename must fail closed"


def test_row27_no_escaped_backslash_n_reaches_the_emitted_file() -> None:
    """Row 27: three cprint sites printed a literal `\\n` instead of a blank line."""
    src = _src()
    offenders = [ln for ln in src.splitlines() if 'cprint(f"\\\\n' in ln]
    assert offenders == [], f"escaped newline(s) still emitted: {offenders}"


def test_row28_no_false_local_tempfile_reimport() -> None:
    """Row 28: `tempfile` is imported once, at module top."""
    src = _src()
    assert "import tempfile" in src.split("def ", 1)[0], "the top-level import is gone"
    assert "stdlib, imported only on this path" not in src


def test_row29_gate_help_lists_every_gate_it_runs() -> None:
    """Row 29: the --gate help/docstring names the gates that actually run."""
    src = _src()
    doc = src[: src.index('"""', src.index('"""') + 3)]
    for gate in ("G2c", "G2d", "G2e", "G2f", "G3s", "G4b"):
        assert gate in doc, f"{gate} missing from the --gate help"


def test_row30_process_ancestry_is_walked_once_per_gate() -> None:
    """Row 30: one ancestry walk feeds both G0 predicates."""
    src = _src()
    block = src[src.index("def run_gate("):src.index("[G1] Checking version bump")]
    assert "_ancestry = _get_process_ancestry()" in block
    assert "_push_in_flight(_ancestry)" in block
    assert "_called_by_publish_orchestrator(root, _ancestry)" in block
    assert block.count("_get_process_ancestry()") == 1
