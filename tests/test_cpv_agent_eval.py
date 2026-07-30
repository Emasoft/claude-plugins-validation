#!/usr/bin/env python3
"""Tests for cpv_agent_eval.py — ORIGINAL vs ALL-IN-ONE / ONE-FOR-ALL / PLUGIN-OMNI.

The invariant the whole tool lives or dies by: **a static estimate must never be
presented as a measured result.** So the tests assert the two tiers stay apart —
Tier 1 (static, deterministic, zero LLM calls) always runs and is asserted
numerically; Tier 2 (live) is unreachable without ``--live``, errors on a missing
task file, and reports UNKNOWN with a non-zero exit rather than inventing a number.

Every check is two-sided: the behaviour fires on the case that needs it AND the
legitimate sibling is unaffected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

import pytest  # noqa: E402
from cpv_agent_eval import (  # noqa: E402
    CACHE_READ_RATE,
    CACHE_WRITE_RATE,
    EXIT_ERROR,
    EXIT_LIVE_UNKNOWN,
    EXIT_OK,
    TIER1,
    TIER2,
    VARIANT_NAMES,
    EvalInputError,
    benchmark_payload,
    default_report_path,
    load_run_timings,
    load_task_suite,
    main,
    parse_variants,
    profile_variant,
    project_turn_cost,
    render_report,
    run_live_tier,
    run_static_tier,
    static_delta,
    token_count,
)

# ---------------------------------------------------------------------------
# Fixtures — a real plugin tree on disk. Tier 1 measures REAL files, so the
# fixtures must be real files, not stubs.
# ---------------------------------------------------------------------------

SKILL_ALPHA = """---
name: alpha
description: Alpha skill body used to size the cached prefix. Use when sizing.
---

# alpha

Alpha does the alpha thing. """ + ("alpha padding word " * 60)

SKILL_BETA = """---
name: beta
description: Beta skill body, deliberately longer than alpha so token sizes differ.
---

# beta

Beta does the beta thing. """ + ("beta padding word " * 300)

SKILL_MENU = """---
name: the-skills-menu
description: The plugin's skills menu. Use when routing by intent.
---

# the-skills-menu

| skill | when |
|---|---|
| alpha | alpha work |
| beta | beta work |
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    """A minimal but REAL plugin: manifest, three skills, four agents."""
    root = tmp_path / "demo-plugin"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": "demo", "version": "0.1.0"}))
    _write(root / "skills" / "alpha" / "SKILL.md", SKILL_ALPHA)
    _write(root / "skills" / "alpha" / "references" / "notes.md", "# notes\n\nalpha reference notes.\n")
    _write(root / "skills" / "alpha" / "scripts" / "helper.py", "print('helper')\n")
    _write(root / "skills" / "beta" / "SKILL.md", SKILL_BETA)
    _write(root / "skills" / "the-skills-menu" / "SKILL.md", SKILL_MENU)

    # ORIGINAL: preloads alpha only.
    _write(
        root / "agents" / "orig.md",
        "---\n"
        "name: orig\n"
        "description: The original agent under evaluation.\n"
        "tools: Read, Grep, Skill\n"
        "skills:\n"
        "  - alpha\n"
        "---\n\n"
        "# orig\n\nRoute to alpha when the request is alpha work.\n",
    )
    # ALL-IN-ONE: preloads alpha AND beta — a bigger prefix, ready at turn 1.
    _write(
        root / "agents" / "aio.md",
        "---\n"
        "name: aio\n"
        "description: The ALL-IN-ONE variant.\n"
        "tools: Read, Grep, Skill\n"
        "skills:\n"
        "  - alpha\n"
        "  - beta\n"
        "---\n\n"
        "# aio\n\nRoute to alpha for alpha work and beta for beta work.\n",
    )
    # PLUGIN-OMNI: preloads only the menu; alpha arrives at RUNTIME.
    _write(
        root / "agents" / "omni.md",
        "---\n"
        "name: omni\n"
        "description: The PLUGIN-OMNI variant.\n"
        "tools: Read, Grep, Skill\n"
        "skills:\n"
        "  - the-skills-menu\n"
        "---\n\n"
        "# omni\n\nRoute through the menu, then Skill({skill: \"alpha\"}) for alpha work.\n",
    )
    # A variant with NO tools: field — it inherits every session tool.
    _write(
        root / "agents" / "inherit.md",
        "---\n"
        "name: inherit\n"
        "description: A variant with no tools field at all.\n"
        "skills:\n"
        "  - alpha\n"
        "---\n\n"
        "# inherit\n\nRoute to alpha.\n",
    )
    return root


@pytest.fixture
def roots(plugin: Path) -> list[Path]:
    return [plugin / "skills"]


def _tasks_file(path: Path, ids: tuple[str, ...] = ("t1", "t2")) -> Path:
    payload = {
        "skill_name": "alpha",
        "evals": [
            {
                "id": eid,
                "prompt": f"do the {eid} thing",
                "expected_output": f"{eid} done",
                "files": [],
            }
            for eid in ids
        ],
    }
    return _write(path, json.dumps(payload))


def _run(runs_dir: Path, config: str, eval_id: str, *, tokens: int, ms: int, passed: bool | None = True,
         run_id: str | None = None) -> Path:
    body: dict[str, object] = {"total_tokens": tokens, "duration_ms": ms}
    if passed is not None:
        body["passed"] = passed
    leaf = runs_dir / config / eval_id
    if run_id is not None:
        leaf = leaf / run_id
    return _write(leaf / "timing.json", json.dumps(body))


# ---------------------------------------------------------------------------
# --variants parsing (the "selectively" half of the requirement)
# ---------------------------------------------------------------------------


class TestParseVariants:
    def test_accepts_any_subset(self):
        """A subset of the four canonical names parses to exactly that subset."""
        assert parse_variants("original,all-in-one,one-for-all") == ["original", "all-in-one", "one-for-all"]

    def test_accepts_the_full_set(self):
        """All four names parse, so a whole-field comparison is expressible."""
        assert parse_variants(",".join(VARIANT_NAMES)) == list(VARIANT_NAMES)

    def test_rejects_an_unknown_name(self):
        """An unknown variant name is an error, never silently dropped."""
        with pytest.raises(EvalInputError) as exc:
            parse_variants("original,mono")
        assert "mono" in str(exc.value)

    def test_deduplicates_but_keeps_order(self):
        """A repeated name collapses without reordering the selection."""
        assert parse_variants("all-in-one,original,all-in-one") == ["all-in-one", "original"]

    def test_rejects_an_empty_selection(self):
        """An empty --variants value is an error, not an empty pass."""
        with pytest.raises(EvalInputError):
            parse_variants("  ,  ")


# ---------------------------------------------------------------------------
# Tier 1 — static cost model
# ---------------------------------------------------------------------------


class TestStaticProfile:
    def test_preloaded_skill_content_is_in_the_cached_prefix(self, plugin: Path, roots: list[Path]):
        """A preloaded skill's FULL SKILL.md content counts toward the cached prefix."""
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        alpha_tokens = token_count((plugin / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        assert prof.evaluated is True
        assert prof.preload_tokens == alpha_tokens
        assert prof.cached_prefix_tokens == prof.agent_body_tokens + prof.preload_tokens

    def test_a_runtime_only_skill_is_excluded_from_the_prefix(self, plugin: Path, roots: list[Path]):
        """The two-sided sibling: a skill reached at RUNTIME costs no prefix tokens."""
        omni = profile_variant("plugin-omni", plugin / "agents" / "omni.md", roots=roots)
        alpha_tokens = token_count((plugin / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8"))
        menu_tokens = token_count((plugin / "skills" / "the-skills-menu" / "SKILL.md").read_text(encoding="utf-8"))
        assert omni.preload_tokens == menu_tokens
        assert "alpha" in omni.runtime_skills
        assert omni.preload_tokens < menu_tokens + alpha_tokens

    def test_more_preloads_mean_a_bigger_prefix(self, plugin: Path, roots: list[Path]):
        """ALL-IN-ONE (two preloads) has a strictly larger prefix than the original (one)."""
        orig = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        aio = profile_variant("all-in-one", plugin / "agents" / "aio.md", roots=roots)
        assert aio.cached_prefix_tokens > orig.cached_prefix_tokens

    def test_per_invocation_injected_tokens_is_the_preload_portion(self, plugin: Path, roots: list[Path]):
        """The per-invocation injected figure is the preload content, not the whole prefix."""
        prof = profile_variant("all-in-one", plugin / "agents" / "aio.md", roots=roots)
        assert prof.per_invocation_injected_tokens == prof.preload_tokens
        assert prof.per_invocation_injected_tokens < prof.cached_prefix_tokens

    def test_turn1_ready_when_every_reachable_skill_is_preloaded(self, plugin: Path, roots: list[Path]):
        """An all-preload agent is ready at turn 1."""
        assert profile_variant("all-in-one", plugin / "agents" / "aio.md", roots=roots).turn1_ready is True

    def test_not_turn1_ready_when_a_skill_arrives_at_runtime(self, plugin: Path, roots: list[Path]):
        """The sibling: a runtime-loaded skill means the agent is NOT ready at turn 1."""
        assert profile_variant("plugin-omni", plugin / "agents" / "omni.md", roots=roots).turn1_ready is False

    def test_tool_schema_surface_counts_declared_tools(self, plugin: Path, roots: list[Path]):
        """A declared tools: list yields its count and is not treated as inherited."""
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        assert prof.tool_schema_surface == 3
        assert prof.tools_inherited is False

    def test_absent_tools_field_is_reported_as_inherited_not_zero(self, plugin: Path, roots: list[Path]):
        """No tools: field means every session tool is inherited — never a surface of 0."""
        prof = profile_variant("all-in-one", plugin / "agents" / "inherit.md", roots=roots)
        assert prof.tool_schema_surface is None
        assert prof.tools_inherited is True

    def test_closure_size_counts_references_and_scripts(self, plugin: Path, roots: list[Path]):
        """Closure size spans a reachable skill's SKILL.md plus references/** and scripts/**."""
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        assert prof.closure_file_count == 3
        assert prof.closure_bytes > 0

    def test_roots_are_auto_resolved_when_not_supplied(self, plugin: Path):
        """roots=None resolves the search roots from the agent's own plugin."""
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=None)
        assert prof.preload_tokens > 0
        assert prof.preloaded_skills == ("alpha",)

    def test_an_unresolved_preload_is_noted_not_silently_zero(self, plugin: Path, roots: list[Path]):
        """A preload naming a skill that does not resolve is recorded in the notes."""
        agent = _write(
            plugin / "agents" / "broken.md",
            "---\nname: broken\ndescription: d\ntools: Skill\nskills:\n  - alpha\n  - nope-xyz\n---\n\n# broken\n",
        )
        prof = profile_variant("original", agent, roots=roots)
        assert any("nope-xyz" in note for note in prof.notes)

    def test_every_number_carries_tier1(self, plugin: Path, roots: list[Path]):
        """Every static row is labelled with the tier that produced it."""
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        assert prof.tier == TIER1
        assert prof.to_dict()["tier"] == TIER1


class TestTurnProjection:
    def test_one_turn_pays_only_the_cache_write(self):
        """Turn 1 creates the prefix cache, so it is charged at the write rate."""
        assert project_turn_cost(1000, 1) == pytest.approx(1000 * CACHE_WRITE_RATE)

    def test_later_turns_are_charged_at_the_read_rate(self):
        """Every later turn re-reads the same prefix at the cache-read rate."""
        expected = 1000 * CACHE_WRITE_RATE + 1000 * CACHE_READ_RATE * 9
        assert project_turn_cost(1000, 10) == pytest.approx(expected)

    def test_projection_grows_with_the_prefix(self):
        """A bigger prefix projects a bigger N-turn cost — the trade-off the tool exists to show."""
        assert project_turn_cost(2000, 10) > project_turn_cost(1000, 10)

    def test_zero_or_negative_turns_costs_nothing(self):
        """A non-positive turn count is 0, never a negative projection."""
        assert project_turn_cost(1000, 0) == 0.0
        assert project_turn_cost(1000, -5) == 0.0


class TestStaticTier:
    def test_a_named_variant_without_a_file_is_not_evaluated(self, plugin: Path, roots: list[Path]):
        """A selected variant whose file is absent is NOT-EVALUATED, never dropped."""
        result = run_static_tier(
            {"original": plugin / "agents" / "orig.md", "one-for-all": None},
            ["original", "one-for-all"],
            roots=roots,
        )
        by_name = {p.variant: p for p in result.profiles}
        assert set(by_name) == {"original", "one-for-all"}
        assert by_name["one-for-all"].evaluated is False
        assert by_name["one-for-all"].reason

    def test_delta_is_measured_against_the_original(self, plugin: Path, roots: list[Path]):
        """The delta says what a variant COSTS relative to the original baseline."""
        result = run_static_tier(
            {"original": plugin / "agents" / "orig.md", "all-in-one": plugin / "agents" / "aio.md"},
            ["original", "all-in-one"],
            roots=roots,
        )
        delta = static_delta(result.profiles)
        assert delta["all-in-one"]["cached_prefix_tokens"] > 0
        assert "original" not in delta

    def test_delta_omits_a_variant_that_was_not_evaluated(self, plugin: Path, roots: list[Path]):
        """A NOT-EVALUATED variant contributes no delta — a delta would be fabricated."""
        result = run_static_tier(
            {"original": plugin / "agents" / "orig.md", "plugin-omni": None},
            ["original", "plugin-omni"],
            roots=roots,
        )
        assert static_delta(result.profiles) == {}

    def test_tier1_is_deterministic(self, plugin: Path, roots: list[Path]):
        """Two runs over the same files produce byte-identical numbers (assertable, so asserted)."""
        args = ({"original": plugin / "agents" / "orig.md"}, ["original"])
        first = run_static_tier(*args, roots=roots).to_dict()
        second = run_static_tier(*args, roots=roots).to_dict()
        assert first == second

    def test_a_priced_closure_reports_zero_unpriced_preloads(self, plugin: Path, roots: list[Path]):
        """The negative half: when every preload resolves, nothing is understated."""
        prof = profile_variant("all-in-one", plugin / "agents" / "aio.md", roots=roots)
        assert prof.unpriced_preloads == 0

    def test_an_unresolvable_preload_is_counted_as_unpriced(self, plugin: Path):
        """A preload that resolves nowhere was counted as 0 tokens, so it is UNDERSTATED.

        Measured on a real agent: with its preloads unresolvable an ALL-IN-ONE variant read
        as 9,264 tokens CHEAPER than its original, and 13,545 tokens more EXPENSIVE once the
        same preloads resolved — the delta carried the WRONG SIGN. The count is what lets the
        report say the comparison is not like-for-like instead of publishing that number bare.
        """
        prof = profile_variant("all-in-one", plugin / "agents" / "aio.md", roots=[])
        assert prof.unpriced_preloads > 0
        assert prof.to_dict()["unpriced_preloads"] == prof.unpriced_preloads

    def test_report_flags_a_delta_that_is_not_like_for_like(self, plugin: Path, roots: list[Path]):
        """Two-sided: the flag reads `no` with an unpriced preload and `yes` without one."""
        selection = {"original": plugin / "agents" / "orig.md", "all-in-one": plugin / "agents" / "aio.md"}
        priced = render_report(run_static_tier(selection, ["original", "all-in-one"], roots=roots), None)
        unpriced = render_report(run_static_tier(selection, ["original", "all-in-one"], roots=[]), None)
        assert "like-for-like" in priced
        assert "unpriced preloads" not in priced
        assert "unpriced preloads" in unpriced

    def test_tier1_never_consults_the_token_api(self, plugin: Path, roots: list[Path], monkeypatch):
        """Zero LLM calls: the exact-count API stays unused even when its env is armed."""
        monkeypatch.setenv("CPV_TOKEN_EXACT", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-a-real-key")
        import cpv_token_estimate

        def _boom(text: str) -> int | None:  # pragma: no cover - must never run
            raise AssertionError("the token API was consulted from Tier 1")

        monkeypatch.setattr(cpv_token_estimate, "_estimate_api", _boom)
        prof = profile_variant("original", plugin / "agents" / "orig.md", roots=roots)
        assert prof.cached_prefix_tokens > 0


# ---------------------------------------------------------------------------
# Tier 2 — the ecosystem eval schema
# ---------------------------------------------------------------------------


class TestTaskSuite:
    def test_parses_the_ecosystem_schema(self, tmp_path: Path):
        """evals/evals.json is read verbatim: {skill_name, evals:[{id,prompt,expected_output,files}]}."""
        suite = load_task_suite(_tasks_file(tmp_path / "evals" / "evals.json"))
        assert suite.skill_name == "alpha"
        assert [t.id for t in suite.tasks] == ["t1", "t2"]
        assert suite.tasks[0].expected_output == "t1 done"

    def test_a_missing_file_is_an_error(self, tmp_path: Path):
        """A missing task file is an error, never an empty pass."""
        with pytest.raises(EvalInputError):
            load_task_suite(tmp_path / "nope" / "evals.json")

    def test_an_empty_eval_list_is_an_error(self, tmp_path: Path):
        """Zero eval cases cannot be a passing comparison."""
        _write(tmp_path / "e.json", json.dumps({"skill_name": "a", "evals": []}))
        with pytest.raises(EvalInputError):
            load_task_suite(tmp_path / "e.json")

    def test_an_eval_without_an_id_is_an_error(self, tmp_path: Path):
        """A case with no id cannot be matched to its run, so it is rejected."""
        _write(tmp_path / "e.json", json.dumps({"skill_name": "a", "evals": [{"prompt": "p"}]}))
        with pytest.raises(EvalInputError):
            load_task_suite(tmp_path / "e.json")

    def test_an_eval_without_a_prompt_is_an_error(self, tmp_path: Path):
        """A case with no prompt cannot be dispatched."""
        _write(tmp_path / "e.json", json.dumps({"skill_name": "a", "evals": [{"id": "x"}]}))
        with pytest.raises(EvalInputError):
            load_task_suite(tmp_path / "e.json")

    def test_malformed_json_is_an_error(self, tmp_path: Path):
        """Unparseable input is an error rather than a silently empty suite."""
        _write(tmp_path / "e.json", "{not json")
        with pytest.raises(EvalInputError):
            load_task_suite(tmp_path / "e.json")


class TestRunTimings:
    def test_reads_timing_json_per_run(self, tmp_path: Path):
        """timing.json is {total_tokens, duration_ms}, captured per run."""
        _run(tmp_path, "original", "t1", tokens=1200, ms=3400)
        timings = load_run_timings(tmp_path, ["original"])
        assert len(timings) == 1
        assert timings[0].total_tokens == 1200
        assert timings[0].duration_ms == 3400
        assert timings[0].config == "original"
        assert timings[0].eval_id == "t1"

    def test_a_lost_timing_is_unknown_never_zero(self, tmp_path: Path):
        """A run whose timing was lost is UNKNOWN — reporting 0 would be a fabricated number."""
        _write(tmp_path / "original" / "t1" / "timing.json", json.dumps({"duration_ms": 10}))
        timings = load_run_timings(tmp_path, ["original"])
        assert timings[0].total_tokens is None
        assert timings[0].duration_ms == 10

    def test_a_missing_pass_flag_is_unknown_never_a_failure(self, tmp_path: Path):
        """Absent outcome data is UNKNOWN, not a silent fail that would skew pass_rate."""
        _run(tmp_path, "original", "t1", tokens=1, ms=1, passed=None)
        assert load_run_timings(tmp_path, ["original"])[0].passed is None

    def test_repeated_runs_are_each_counted(self, tmp_path: Path):
        """Repeats live in numbered subdirs and each one is a run."""
        _run(tmp_path, "original", "t1", tokens=100, ms=10, run_id="1")
        _run(tmp_path, "original", "t1", tokens=120, ms=12, run_id="2")
        timings = load_run_timings(tmp_path, ["original"])
        assert len(timings) == 2
        assert {t.eval_id for t in timings} == {"t1"}

    def test_only_the_selected_configs_are_read(self, tmp_path: Path):
        """A run dir for an unselected config is ignored, not mixed into the comparison."""
        _run(tmp_path, "original", "t1", tokens=1, ms=1)
        _run(tmp_path, "all-in-one", "t1", tokens=2, ms=2)
        assert {t.config for t in load_run_timings(tmp_path, ["original"])} == {"original"}


class TestLiveTier:
    def _suite(self, tmp_path: Path):
        return load_task_suite(_tasks_file(tmp_path / "evals.json", ("t1",)))

    def test_complete_runs_aggregate_to_a_run_summary(self, tmp_path: Path):
        """With every selected config timed, the live tier reports pass_rate/time/tokens."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000)
        _run(runs, "all-in-one", "t1", tokens=1500, ms=1000)
        live = run_live_tier(self._suite(tmp_path), runs, ["original", "all-in-one"])
        assert live.status == "OK"
        assert live.run_summary["original"]["tokens"]["mean"] == 1000
        assert live.run_summary["original"]["time_seconds"]["mean"] == pytest.approx(2.0)
        assert live.run_summary["original"]["pass_rate"]["mean"] == pytest.approx(1.0)

    def test_a_config_with_no_runs_is_unknown(self, tmp_path: Path):
        """A selected variant that was never run is UNKNOWN — never an empty pass."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000)
        live = run_live_tier(self._suite(tmp_path), runs, ["original", "one-for-all"])
        assert live.status == "UNKNOWN"
        assert "one-for-all" in live.unknown

    def test_a_missing_eval_run_is_unknown(self, tmp_path: Path):
        """Every eval of a selected config must have a run, or the config is UNKNOWN."""
        suite = load_task_suite(_tasks_file(tmp_path / "evals.json", ("t1", "t2")))
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=10, ms=10)
        live = run_live_tier(suite, runs, ["original"])
        assert live.status == "UNKNOWN"
        assert "original" in live.unknown

    def test_stddev_is_omitted_on_single_runs(self, tmp_path: Path):
        """stddev is meaningless with one run per eval, so it is omitted, not faked as 0."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000)
        live = run_live_tier(self._suite(tmp_path), runs, ["original"])
        assert "stddev" not in live.run_summary["original"]["tokens"]

    def test_stddev_is_reported_when_an_eval_is_repeated(self, tmp_path: Path):
        """With repeats the spread is meaningful, so stddev is reported."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000, run_id="1")
        _run(runs, "original", "t1", tokens=1400, ms=2400, run_id="2")
        live = run_live_tier(self._suite(tmp_path), runs, ["original"])
        assert live.run_summary["original"]["tokens"]["stddev"] > 0

    def test_delta_states_cost_and_benefit_against_the_original(self, tmp_path: Path):
        """The delta is the deliverable: what a variant costs and what it buys."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000, passed=False)
        _run(runs, "all-in-one", "t1", tokens=1500, ms=1000, passed=True)
        live = run_live_tier(self._suite(tmp_path), runs, ["original", "all-in-one"])
        assert live.delta["all-in-one"]["tokens"] == pytest.approx(500)
        assert live.delta["all-in-one"]["time_seconds"] == pytest.approx(-1.0)
        assert live.delta["all-in-one"]["pass_rate"] == pytest.approx(1.0)

    def test_benchmark_payload_matches_the_ecosystem_shape(self, tmp_path: Path):
        """benchmark.json is {run_summary: {...}, delta: {...}} — the adopted schema."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000)
        live = run_live_tier(self._suite(tmp_path), runs, ["original"])
        payload = benchmark_payload(live)
        assert set(payload) >= {"run_summary", "delta"}
        assert set(payload["run_summary"]["original"]) == {"pass_rate", "time_seconds", "tokens"}

    def test_live_numbers_carry_tier2(self, tmp_path: Path):
        """A measured number is labelled tier2-live, keeping it apart from an estimate."""
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=1000, ms=2000)
        live = run_live_tier(self._suite(tmp_path), runs, ["original"])
        assert live.tier == TIER2


# ---------------------------------------------------------------------------
# Report + CLI
# ---------------------------------------------------------------------------


class TestReport:
    def test_report_labels_both_tiers(self, plugin: Path, roots: list[Path]):
        """The rendered report names the tier behind every table."""
        static = run_static_tier({"original": plugin / "agents" / "orig.md"}, ["original"], roots=roots)
        text = render_report(static, None)
        assert TIER1 in text
        assert "NOT RUN" in text or "not run" in text

    def test_report_ranks_nothing(self, plugin: Path, roots: list[Path]):
        """No winner, no ranking: the tool prints the delta and lets the human decide."""
        static = run_static_tier(
            {"original": plugin / "agents" / "orig.md", "all-in-one": plugin / "agents" / "aio.md"},
            ["original", "all-in-one"],
            roots=roots,
        )
        text = render_report(static, None).lower()
        for banned in ("winner", "best variant", "we recommend", "recommended variant", "ranked"):
            assert banned not in text

    def test_report_rows_are_numbered(self, plugin: Path, roots: list[Path]):
        """Tables carry a leading # column so a row can be referenced by number."""
        static = run_static_tier({"original": plugin / "agents" / "orig.md"}, ["original"], roots=roots)
        assert "| # |" in render_report(static, None)

    def test_not_evaluated_row_is_visible_in_the_table(self, plugin: Path, roots: list[Path]):
        """A NOT-EVALUATED variant appears in the table with its reason."""
        static = run_static_tier(
            {"original": plugin / "agents" / "orig.md", "one-for-all": None},
            ["original", "one-for-all"],
            roots=roots,
        )
        text = render_report(static, None)
        assert "one-for-all" in text
        assert "NOT-EVALUATED" in text

    def test_default_report_path_is_under_reports_cpv_agent_eval(self, plugin: Path):
        """Reports land under reports/cpv-agent-eval/ with a timestamped filename."""
        path = default_report_path(plugin / "agents" / "orig.md")
        assert path.parent.name == "cpv-agent-eval"
        assert path.parent.parent.name == "reports"
        assert path.suffix == ".md"


class TestCli:
    def test_static_only_run_exits_ok_and_omits_tier2(self, plugin: Path, tmp_path: Path, capsys):
        """Without --live the tool produces Tier 1 only and exits 0."""
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--all-in-one",
                str(plugin / "agents" / "aio.md"),
                "--variants",
                "original,all-in-one",
                "--skills-root",
                str(plugin / "skills"),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        assert payload["tier1"]["profiles"]
        assert payload["tier2"] is None

    def test_variants_defaults_to_the_supplied_files(self, plugin: Path, capsys):
        """Omitting --variants evaluates the original plus every variant file supplied."""
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--plugin-omni",
                str(plugin / "agents" / "omni.md"),
                "--skills-root",
                str(plugin / "skills"),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        assert {p["variant"] for p in payload["tier1"]["profiles"]} == {"original", "plugin-omni"}

    def test_live_without_a_tasks_file_is_an_error(self, plugin: Path, tmp_path: Path, capsys):
        """--live with no task file errors — a missing task file is never an empty pass."""
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--skills-root",
                str(plugin / "skills"),
                "--live",
                "--tasks",
                str(tmp_path / "absent" / "evals.json"),
                "--json",
            ]
        )
        capsys.readouterr()
        assert code == EXIT_ERROR

    def test_live_without_runs_reports_unknown_and_exits_nonzero(self, plugin: Path, tmp_path: Path, capsys):
        """--live with no captured runs reports UNKNOWN and exits non-zero."""
        tasks = _tasks_file(tmp_path / "evals.json", ("t1",))
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--skills-root",
                str(plugin / "skills"),
                "--live",
                "--tasks",
                str(tasks),
                "--runs-dir",
                str(tmp_path / "runs"),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_LIVE_UNKNOWN
        assert payload["tier2"]["status"] == "UNKNOWN"

    def test_live_with_real_runs_aggregates_and_exits_ok(self, plugin: Path, tmp_path: Path, capsys):
        """With real captured timings the live tier aggregates and exits 0."""
        tasks = _tasks_file(tmp_path / "evals.json", ("t1",))
        runs = tmp_path / "runs"
        _run(runs, "original", "t1", tokens=900, ms=1500)
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--skills-root",
                str(plugin / "skills"),
                "--live",
                "--tasks",
                str(tasks),
                "--runs-dir",
                str(runs),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == EXIT_OK
        assert payload["tier2"]["status"] == "OK"
        assert payload["tier2"]["run_summary"]["original"]["tokens"]["mean"] == 900

    def test_a_missing_original_file_is_an_error(self, tmp_path: Path, capsys):
        """A --original path that is not a file is an error, not an empty table."""
        code = main(["--original", str(tmp_path / "nope.md"), "--json"])
        capsys.readouterr()
        assert code == EXIT_ERROR

    def test_an_unknown_variant_name_is_an_error(self, plugin: Path, capsys):
        """A bogus --variants entry errors instead of silently evaluating nothing."""
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--variants",
                "original,mono",
                "--json",
            ]
        )
        capsys.readouterr()
        assert code == EXIT_ERROR

    def test_report_file_is_written_when_not_json(self, plugin: Path, tmp_path: Path, capsys):
        """The human path writes the findings-style report to disk."""
        out = tmp_path / "eval-report.md"
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--skills-root",
                str(plugin / "skills"),
                "--report",
                str(out),
            ]
        )
        capsys.readouterr()
        assert code == EXIT_OK
        assert out.is_file()
        assert TIER1 in out.read_text(encoding="utf-8")

    def test_a_bad_skills_root_is_an_error(self, plugin: Path, tmp_path: Path, capsys):
        """A non-directory --skills-root fails loudly: silently dropping it makes Tier 1 vacuous."""
        code = main(
            [
                "--original",
                str(plugin / "agents" / "orig.md"),
                "--skills-root",
                str(tmp_path / "not-a-dir"),
                "--json",
            ]
        )
        capsys.readouterr()
        assert code == EXIT_ERROR


class TestDrivingSkill:
    """Contract tests for the skill that drives the script.

    The skill is the surface an agent actually reads, so its promises have to be pinned:
    a skill that forgot the clean-context rule or started naming a winner would break the
    comparison while the script stayed correct.
    """

    SKILL = Path(__file__).parent.parent / "skills" / "cpv-evaluate-agent-variants" / "SKILL.md"

    def _body(self) -> str:
        return self.SKILL.read_text(encoding="utf-8")

    def test_the_skill_exists(self):
        """The script ships with a skill that drives it."""
        assert self.SKILL.is_file()

    def test_frontmatter_is_agent_facing(self):
        """The skill is discovered through the catalog, not offered as a slash command."""
        body = self._body()
        assert "name: cpv-evaluate-agent-variants" in body
        assert "user-invocable: false" in body
        assert "Used dynamically via cpv-the-skills-menu" in body

    def test_it_pins_the_two_tier_separation(self):
        """Both tier labels appear, so a reader cannot mistake an estimate for a measurement."""
        body = self._body()
        assert TIER1 in body
        assert TIER2 in body
        assert "--live" in body

    def test_it_pins_the_clean_context_rule(self):
        """A fresh subagent per run is stated: a shared context invalidates the comparison."""
        body = self._body().lower()
        assert "fresh subagent" in body
        assert "clean context" in body

    def test_it_pins_the_capture_now_rule(self):
        """The skill records that the timing values are not persisted anywhere else."""
        assert "not persisted anywhere else" in self._body()

    def test_it_declares_no_winner(self):
        """The skill states the no-ranking rule outright and carries no recommendation phrasing."""
        body = self._body().lower()
        assert "never names a winner" in body
        for banned in ("we recommend", "best variant", "ranked first"):
            assert banned not in body

    def test_it_is_registered_in_the_skills_catalog(self):
        """An unlisted skill is an orphan no agent can discover."""
        catalog = (
            Path(__file__).parent.parent / "skills" / "cpv-the-skills-menu" / "references" / "skills-catalog.md"
        ).read_text(encoding="utf-8")
        assert "cpv-evaluate-agent-variants" in catalog
        assert 'Skill({skill: "claude-plugins-validation:cpv-evaluate-agent-variants"})' in catalog


class TestNoSimulation:
    """Source-level guards: the module must have no path that invents a number."""

    def _source(self) -> str:
        return (scripts_dir / "cpv_agent_eval.py").read_text(encoding="utf-8")

    def test_no_random_or_simulation_machinery(self):
        """No randomness and no simulate/mock helper — a fabricated comparison is worse than none."""
        src = self._source()
        assert "import random" not in src
        assert "def simulate" not in src
        assert "def _simulate" not in src

    def test_no_subprocess_dispatch_of_a_model(self):
        """The script never pretends to dispatch a model: the driver captures real runs."""
        src = self._source()
        assert "anthropic" not in src.lower().replace("anthropic_api_key", "")
