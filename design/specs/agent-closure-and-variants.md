# SPEC — agent skill-closure, single-agent security scan, variant conversion, variant evaluation

**Status:** normative. Every workstream below builds against the signatures pinned
here VERBATIM. Do not rename a field, reorder a positional parameter, or change a
return type — three other workstreams import this surface concurrently.

## 0.0 Provenance — where the normative facts come from

Every mechanism claim in this spec is taken from the official docs, fetched as RAW
markdown and diffed against CPV's own constants. A WebFetch *summary* of these
pages has false-negatived twice on this project, so summaries are not acceptable
evidence here.

| doc | what it settles |
|---|---|
| `code.claude.com/docs/en/skills.md` | the skill frontmatter table — `context`, `agent`, `background`, `disable-model-invocation`; "Run skills in a subagent" |
| `code.claude.com/docs/en/sub-agents.md` | "Preload skills into subagents" — the `skills:` field's exact semantics, the `Skill`/`disallowedTools` gate, the no-preload rule, the silent skip |
| `agentskills.io/skill-creation/evaluating-skills.md` | the eval schema T4 adopts: `evals/evals.json`, `timing.json`, `benchmark.json`, the delta framing |
| `code.claude.com/docs/en/debug-your-config.md` | the confirmation surfaces (`/skills`, `/context`, `--safe-mode`) the AC findings must name |
| `code.claude.com/docs/en/plugins-reference.md` | plugin skills layout (`skills/<name>/SKILL.md`) |

Two claims an earlier draft got WRONG, both corrected above and recorded here so
they are not reintroduced: `agent:` alone does NOT fork a subagent (`context: fork`
does), and the gate must consult `disallowedTools`, not only `tools`.

## 0. Why this exists (the verified gap)

Probed first-hand against v3.24.0 on a purpose-built fixture:

| Probe | CPV today |
|---|---|
| agent with `skills: [real-skill, totally-nonexistent-skill-xyz]` | **0 findings**, score 100/100, PASSED |
| same fixture through `validate_xref` on the whole plugin | **0 findings** |
| body `Skill({skill: "nonexistent"})` with `tools: Read, Grep` (no `Skill`) | one **WARNING** (never blocks); the skill NAME is never resolved |

Cause: `validate_agent.py` is a SINGLE-FILE validator with no plugin root, so it
structurally cannot resolve a skill name. `validate_xref.py` only matches
`skills/<name>` PATH shapes and blanks frontmatter out of its body scan, so an
agent's `skills:` list is existence-checked by nothing.

Consequence: a preload that silently does nothing, and a runtime invocation that
silently fails, both ship at 100/100. Same false-negative family as v3.18.0's
D1–D6.

## 1. Reachability model (the load-bearing correctness point)

`skills:` frontmatter is a **pre-load hint, not an ACL** (TRDD-9dd64dbf /
TRDD-14cc93a6, and v3.18.0's doc-verified finding that frontmatter injects a
skill's FULL content into every invocation). So "the skills accessible to an
agent" is NOT simply the `skills:` list. It is:

| origin | definition | reachable when |
|---|---|---|
| `preload` | a name in `skills:` frontmatter | ALWAYS (injected at startup) |
| `runtime` | a `Skill(...)` / `/name` invocation in the agent body | ONLY if the agent can use the `Skill` tool |
| `transitive` | a skill invoked from a reachable skill's body | its parent is reachable AND the `Skill` gate is open |

**The `Skill`-tool gate** (`agent_can_load_skills_at_runtime`), doc-backed by
`sub-agents.md` → "Preload skills into subagents": *"To prevent a subagent from
invoking skills entirely, omit `Skill` from the `tools` list **or add it to
`disallowedTools`**."*

- `Skill` in `disallowedTools` → **False**, regardless of `tools` (deny is applied
  first, so it wins)
- else no `tools:` field → **True** (the agent inherits every session tool)
- else `Skill` present in `tools` → **True** (a specifier-carrying `Skill(...)`
  still grants the tool — test the NAME part, not an exact bare token)
- else → **False** — every runtime invocation in that body is DEAD, and a
  `skills:` preload is the agent's only skill access

The same doc confirms the whole model: *"This field controls which skills are
preloaded, **not which skills the subagent can access**: without it, the subagent
can still discover and invoke project, user, and plugin skills through the Skill
tool during execution."* So `skills:` is a preload hint and the gate is the ACL —
which is exactly §1.

`ambient` is the full set of skill names present in the search roots. When the
gate is open they are all *potentially* loadable, so the set is REPORTED but its
members are NOT validated by default — validating an entire palette per agent
would be pure noise. Opt in with `--closure-ambient`.

## 1.1 The three agent architectures — CANONICAL TERMINOLOGY

These names are the project's vocabulary. Use them verbatim in code, findings,
skills, menus and docs. All three pair their skill strategy with the **`Skill`
tool**, so all three require the gate in §1 to be OPEN.

### THE INLINING PROHIBITION — read this before anything else

**A skill's content is NEVER copied into an agent.** Not concatenated into a body,
not duplicated into a variant, not embedded anywhere. An agent REFERENCES skills by
name in its `skills:` frontmatter and nowhere else.

The reason is single-source-of-truth, and it outranks any token argument: a skill
has to stay INDEPENDENT so it can be shared by many agents and edited, fixed, or
updated ONCE. An inlined copy is a second source that silently rots the moment the
original changes, and with N agents inlining it there are N stale copies and no
signal that any of them drifted.

This makes the three architectures far more alike than different:

| architecture | `skills:` frontmatter lists | body carries | skills execute in |
|---|---|---|---|
| **ALL-IN-ONE AGENT** | EVERY skill it needs | how to use each skill, at the right time and in the right choice branch | **the same agent** |
| **ONE-FOR-ALL AGENT** | EVERY skill it needs | the same routing / choice tree / skeleton | **a separate subagent per skill** (a **micro-agent** = **one-skill-agent**, minimal context) |
| **PLUGIN-OMNI-AGENT** | exactly ONE skill — the plugin's `the-skills-menu` (every skill's name + description + when to use it) | routing through that menu | resolved at runtime from the menu |

**ALL-IN-ONE and ONE-FOR-ALL are otherwise essentially the same construction.**
The ONLY difference is WHERE a skill runs: in-agent, or dispatched to a separate
subagent. Nothing else about the agent's shape changes. Any implementation that
makes them differ in more than that has misread this section.

All three pair their list with the **`Skill` tool** — that is how the skills (or
the micro-agents) get launched — so all three need the §1 gate OPEN.

Four facts that constrain any implementation, each verified rather than assumed:

1. **`skills:` frontmatter is the preload mechanism.** It injects each named
   skill's FULL content into every invocation's cached prefix, so listing the
   skills IS the preload. This is why inlining buys nothing: the content is
   already there, and the copy only adds a maintenance liability.
2. **`context: fork` is the subagent-execution mechanism — NOT `agent:`.** The
   docs are explicit: `context` — *"Set to `fork` to run in a forked subagent
   context"*; `agent` — *"Which subagent type to use **when `context: fork` is
   set**"* (default `general-purpose`; built-ins `Explore` / `Plan` also skip
   CLAUDE.md, which is how "minimal context" is actually achieved). **A skill
   carrying `agent:` alone does nothing.** An earlier draft of this spec said
   otherwise; it was wrong. ONE-FOR-ALL therefore adds `context: fork` (plus
   optionally `agent:`) IN PLACE to the existing shared skill, never a copy.
3. **`background` defaults to `true`, so a forked skill returns NOTHING inline** —
   its result arrives as a notification. A pipeline that threads one node's output
   into the next step needs **`background: false`**, which requires Claude Code
   **v2.1.218+**. Getting this wrong yields a graph whose steps appear to run and
   silently deliver nothing to the next step.
4. **`skills:` is NOT a valid skill frontmatter field** — it is agent-only
   (verified against `cpv_validation_common.SKILL_FRONTMATTER_FIELDS`). So a
   micro-agent node CANNOT declare its own skill list, and the choice tree
   therefore has to live in the ONE-FOR-ALL agent's body. Any design nesting
   `skills:` inside a skill is invalid.
5. **A skill with `disable-model-invocation: true` CANNOT BE PRELOADED AT ALL** —
   *"You can't preload skills that set `disable-model-invocation: true`, since
   preloading draws from the same set of skills Claude can invoke"*, and that
   *"includes the bundled `/verify` and `/code-review` skills"*. Listing such a
   skill in `skills:` is a preload that silently does nothing → finding **AC5**.
6. **Adding `context: fork` to a SHARED skill changes how it executes for EVERY
   agent that lists it.** That follows from fact 1 plus the no-copy rule, and it
   is the one genuine cost of the ONE-FOR-ALL conversion. The generator must
   report which shared skills it is about to convert and how many other agents
   list them, rather than mutating them silently.
7. **A missing or disabled preload fails SILENTLY** — *"If a listed skill is
   missing or disabled, Claude Code skips it and logs a warning to the debug
   log."* Nothing surfaces at dispatch time, which is precisely why AC1 has to
   exist: the debug log is not somewhere anyone looks.

The docs also frame the two architectures as two directions of ONE system, which
is why they are so alike: *"With `skills` in a subagent, the subagent controls the
system prompt and loads skill content. With `context: fork` in a skill, the skill
content is injected into the agent you specify. Both use the same underlying
system."*

### 1.2 MANDATORY companion skill on every generated variant

Every generated ALL-IN-ONE, ONE-FOR-ALL, and PLUGIN-OMNI agent MUST carry
**`verification-before-completion`** in its `skills:` list. Its Iron Law — *no
completion claim without fresh verification evidence* — is exactly the failure
mode a multi-skill or multi-node agent is most prone to, because a node reporting
"success" is not evidence that the step happened.

This creates a hard interaction with AC1 (§3): a preload naming a skill that does
not resolve is a MAJOR, so a generator that adds this name without ensuring the
skill exists would emit agents that fail CPV's own validator. Therefore the
generator MUST ensure `skills/verification-before-completion/SKILL.md` exists in
the target plugin — writing it from the bundled template when absent, and NEVER
overwriting an existing one (the user may have adapted it). The template is
`design/specs/verification-before-completion.template.md`.

For a PLUGIN-OMNI agent the `skills:` list is "one skill, the menu" — the
companion skill is the ONE permitted addition, so that list has exactly two
entries, and the menu itself must list the companion skill too.

## 2. `scripts/cpv_agent_closure.py` — the SSOT (workstream T1)

Reuse, never reimplement: `cpv_tool_permission_match` for tool-token
normalization and the fence tracker; `validate_skill_comprehensive`'s existing
`Skill({skill: "<name>"})` name patterns (promote them into this module and have
that validator import them back, so one grammar exists).

```python
@dataclass(frozen=True)
class SkillRef:
    name: str                    # kebab-case name AS WRITTEN (namespace stripped)
    namespace: str | None        # "<plugin>" from "<plugin>:<skill>", else None
    origin: str                  # "preload" | "runtime" | "transitive"
    source_file: str             # absolute path of the file holding the reference
    line: int                    # 1-based; 0 for a frontmatter reference
    resolved_path: str | None    # absolute path to its SKILL.md, else None
    reachable: bool              # False iff origin != "preload" and the gate is shut

@dataclass(frozen=True)
class AgentClosure:
    agent_path: str
    skill_roots: tuple[str, ...]
    can_load_at_runtime: bool
    tools_declared: tuple[str, ...] | None   # None == no `tools:` field
    refs: tuple[SkillRef, ...]
    ambient: tuple[str, ...]
    max_depth_reached: int

def skill_search_roots(
    agent_path: Path, *, plugin_root: Path | None = None,
    project_root: Path | None = None, home: Path | None = None,
) -> list[Path]: ...

def available_skills(roots: Sequence[Path]) -> dict[str, Path]: ...   # name -> SKILL.md

def agent_can_load_skills_at_runtime(frontmatter: dict[str, Any]) -> bool: ...

def extract_preloaded_skill_names(frontmatter: dict[str, Any]) -> list[str]: ...

def extract_runtime_skill_refs(body: str) -> list[tuple[str, int]]: ...  # (name, line)

def resolve_agent_closure(
    agent_path: Path, *, roots: Sequence[Path] | None = None, max_depth: int = 3,
) -> AgentClosure: ...

def closure_files(closure: AgentClosure) -> list[Path]: ...
```

Hard requirements:

- **Cycle-safe + depth-bounded.** A→B→A must terminate. `max_depth=3`.
- **Namespace aware.** `Skill(cpv:cpv-fix-validation)` and
  `Skill({skill: "plugin:name"})` resolve on the bare name; a foreign namespace
  that does not resolve locally is `resolved_path=None` but must NOT produce a
  finding (it may legitimately live in another installed plugin) — record it and
  let the caller decide.
- **Fence-aware.** A `Skill(...)` inside a fenced code block is an ILLUSTRATION,
  not an invocation. Reuse the existing fence tracker; do not write a second one.
- **`roots=None` means "resolve them"** via `skill_search_roots`. A caller that
  passes `roots=[]` gets an empty `available_skills`, and every ref unresolved —
  that state is what the non-vacuity guard in §3 exists to handle.
- **Fail safe on I/O.** Any unreadable file yields no ref, never an exception.

## 3. Closure-aware findings in `validate_agent.py` (workstream T1)

Severity discipline (CPV's north star — never call a valid agent invalid):
**WARNING is the ONLY non-blocking tier under `--strict`.** Anything advisory is
therefore WARNING, never MINOR/NIT.

**The non-vacuity guard, and why every escalation depends on it.** If NO skill
root resolved, or zero of the agent's named skills resolved, then the roots are
probably wrong (single-file validation, a moved plugin, an uninstalled source)
and "this skill does not exist" would be a fabricated finding. So:

> A MAJOR requires that at least one OTHER named skill of the same agent DID
> resolve. Absent that proof, emit WARNING.

| id | condition | severity |
|---|---|---|
| **AC1** | a `skills:` preload name does not resolve in any root | MAJOR w/ guard, else WARNING |
| **AC2** | a body `Skill()` invocation names a skill that does not resolve | MAJOR w/ guard, else WARNING |
| **AC3** | body invokes `Skill()` AND `tools:` denies `Skill` AND the named skill RESOLVES | MAJOR (resolution proves a real invocation, not prose) |
| **AC4** | a `skills:` preload the body never MENTIONS, while the gate is open | WARNING (a preload injects FULL content every invocation) |
| **AC5** | a `skills:` preload names a RESOLVED skill carrying `disable-model-invocation: true`, or the bundled `verify` / `code-review` skill | **MAJOR — no guard needed** |

**AC5 is the one MAJOR that needs no non-vacuity guard**, because resolution
itself is the proof: we read that skill's own frontmatter and saw the field, so
there is no "maybe the roots are wrong" case to fail safe against. Per §1.1 fact 5
such a skill can never be preloaded, so the entry is inert — and per fact 7 the
failure is silent, appearing only in the debug log.

**Every AC finding must tell the author how to CONFIRM it in a live session**, so
the finding is actionable rather than a claim they have to take on trust. The
documented surfaces (`debug-your-config.md`) are: **`/skills`** — lists skills from
project, user and plugin sources, and shows a **"user-only" badge** for exactly the
`disable-model-invocation: true` case AC5 reports; and **`/context`** — shows what
actually occupies the window, including which skills loaded. Name the relevant one
in the finding text. `debug-your-config.md` independently corroborates AC5's
failure mode: *"Skill appears in `/skills` but Claude never invokes it → Skill has
`disable-model-invocation: true` in its frontmatter."*

**AC4 counts a bare NAME MENTION as usage, not just a `Skill()` call.** An
ALL-IN-ONE agent (§1.1) preloads every skill and routes to them from a prose
table or a choice-branch list — `| cpv-fix-validation | when a finding is
mechanical |` is genuine usage. Requiring a `Skill()` call would make CPV warn on
its own canonical output, so the test is: does the skill's name appear anywhere in
the body outside a fenced block? If not, the preload is dead weight and the
WARNING is correct. This keeps AC4 an honest token-economy advisory instead of an
architecture preference.

AC3 supersedes nothing: the existing prose WARNING stays for the unresolved case.
A skill that does not resolve is prose as far as we can prove, so it never
escalates on the tool gate alone.

CLI: `--skills-root PATH` (repeatable), `--closure` (validate each reachable
skill via `validate_skill_comprehensive`, rolled into the agent's report),
`--closure-ambient` (also validate the ambient palette). Default behaviour with
no flags is UNCHANGED except that AC1–AC4 now fire when roots auto-resolve.

## 4. `scripts/cpv_agent_security.py` (workstream T2)

`remote_validation.py` gains `agent-security` → this module.

Target is ONE agent file. Scan set = the agent `.md` PLUS `closure_files()` —
each reachable `SKILL.md`, its `references/**`, its `scripts/**`.

- Reuse `validate_security`'s scanning machinery as the SSOT. Do NOT copy a rule,
  a pattern, or a severity mapping. If a needed entry point is plugin-scoped,
  extract the file-set-scoped core and have the plugin path call it — one
  definition, two callers.
- Honour every existing suppression chain (self-scan, vendored, dev-scratch,
  test, gitignored-and-untracked).
- Report contract identical to `validate_security`: same severities, same exit
  codes, `--json`, `--strict`.
- A skill that is in the closure but UNREACHABLE (gate shut) is reported in a
  separate `unreachable` section and its findings do NOT gate — it cannot execute.
  Never silently drop it; "cannot reach" is not "clean".

## 5. `scripts/convert_agent.py` (workstream T3)

Converts ONE SOURCE AGENT (not a whole plugin — that is what the two existing
generators already do) into any of the three §1.1 architectures, using its
closure as the skill set.

```
convert_agent.py <agent.md> --to all-in-one   [--out DIR] [--name NAME] [--force]
convert_agent.py <agent.md> --to one-for-all  [--out DIR] [--name NAME] [--force]
convert_agent.py <agent.md> --to plugin-omni  [--out DIR] [--name NAME] [--force]
```

Common to all three: `Skill` is added to `tools` (all three depend on the §1 gate
being open); `verification-before-completion` is appended to `skills:` and ensured
on disk per §1.2; no `model:` pin (CA-04); an existing output is never overwritten
without `--force`; every emitted agent must pass `validate_agent` with zero
blocking findings — including the new AC1–AC4, which is the real acceptance test,
since a generator emitting an unresolvable preload has produced a broken agent.

**THE INLINING PROHIBITION APPLIES TO ALL THREE.** No mode may copy, concatenate,
or embed a skill's content anywhere. Every mode emits an agent that REFERENCES
skills by name. A generator that produces a self-contained blob has violated the
single-source-of-truth rule §1.1 exists to protect.

- **`--to all-in-one`** → `<name>-all-in-one.md`. `skills:` = every REACHABLE
  skill of the closure (+ the companion), BY NAME. The BODY is the routing layer:
  for each skill, when to reach for it and which branch of the procedure it
  belongs to. Derive the branches from the source agent's own structure; where the
  source gives no ordering, emit a flat "choose by intent" table rather than
  inventing a sequence. Skills execute IN THIS AGENT.
- **`--to one-for-all`** → `<name>-one-for-all.md`. **Identical to `all-in-one`
  in every respect except one:** each listed skill executes in a SEPARATE
  SUBAGENT with minimal context. Mechanically that is an IN-PLACE frontmatter
  addition to the existing shared skill — never a copy, never a rewritten body:

  ```yaml
  context: fork          # REQUIRED — this is what forks a subagent (§1.1 fact 2)
  agent: Explore         # optional: which type; Explore/Plan also skip CLAUDE.md
  background: false      # REQUIRED for a pipeline — see below
  ```

  `agent:` ALONE DOES NOTHING (§1.1 fact 2) — `context: fork` is the mechanism.
  And `background` defaults to `true`, so without `background: false` each node
  returns NOTHING inline and its result arrives as a notification (§1.1 fact 3):
  a graph whose steps appear to run and silently deliver nothing downstream. Emit
  `background: false` for any node whose output the next step consumes, and record
  that this needs Claude Code **v2.1.218+**.
  The agent BODY is the same routing / choice tree as `all-in-one`; per §1.1 fact
  4 a node cannot carry its own skill list, so the graph belongs to the agent.
  Because of §1.1 fact 6, this mode MUST first report every shared skill it would
  convert and how many other agents list it, and MUST NOT mutate a shared skill
  without `--force` — converting a skill changes its execution for every agent
  that lists it, and that consequence has to be visible before it happens.
- **`--to plugin-omni`** → `<name>-plugin-omni.md`. `skills:` = exactly the
  plugin's `the-skills-menu` skill + the companion. The body routes through the
  menu. If the target plugin has no `the-skills-menu`, generate it from the real
  `skills/` inventory — never an empty catalog, which would make the agent inert
  while looking correct.

All three read the closure through §2; none may re-derive the skill set.

**Supersession — `create_mono_agent.py` must change, not merely gain a flag.**
It currently CONCATENATES every non-meta skill body into one agent, which is
exactly the construction the inlining prohibition forbids. Convert it to the
frontmatter model. This is a deliberate breaking change to published behaviour
(so, a MAJOR bump), and there is to be ONE version of the mechanism — no
inlining path kept behind a flag for compatibility.

Then extend the existing skills to cover the agent-scoped path, delegating here:
`cpv-create-mono-agent` → ALL-IN-ONE, `cpv-create-micro-agents-workflow` →
ONE-FOR-ALL, plus a PLUGIN-OMNI path. Their plugin-wide behaviour is UNCHANGED.
Both must adopt the §1.1 vocabulary; "mono" and "micro" survive only as the
historical skill NAMES (renaming a skill is a separate change), never as a
description of what the architecture is.

## 6. `scripts/cpv_agent_eval.py` (workstream T4)

Selectively compare the ORIGINAL agent against any subset of its §1.1 variants.

```
cpv_agent_eval.py --original A.md [--all-in-one X.md] [--one-for-all Y.md] \
                  [--plugin-omni Z.md] \
                  --variants original,all-in-one,one-for-all,plugin-omni \
                  [--tasks tasks.jsonl] [--live]
```

`--variants` accepts any subset, so "the original vs the 2 new versions" is
`--variants original,all-in-one,one-for-all`. A named variant whose file is not
supplied is reported as NOT-EVALUATED, never silently dropped from the table.

**Tier 1 — static cost model. Always runs. Zero LLM calls. Real measurements
over real files.** Per variant: cached-prefix token estimate (system prompt =
the agent body + injected preload content), per-invocation injected tokens,
tool-schema surface count, closure size (files + bytes), turn-1 readiness, and
the projected cost of N turns under the prefix-cache read rate. This tier is
what the test suite asserts, because it is deterministic.

**Tier 2 — live A/B/C. OPT-IN via `--live`, and never implied.** Dispatches each
selected variant on a real task set and records REAL tokens, turns, wall time and
outcome. **No mocks, no simulated numbers, no fabricated comparison** — if the
harness cannot run it reports UNKNOWN and exits non-zero. A missing task file is
an error, never an empty pass.

**ADOPT THE ECOSYSTEM EVAL SCHEMA — do not invent one.** `evaluating-skills.md`
already defines this exact comparison, so Tier 2 reuses its shapes verbatim and
only swaps the configuration names (`with_skill`/`without_skill` → `original` /
`all-in-one` / `one-for-all` / `plugin-omni`):

- **Input** — `evals/evals.json`: `{skill_name, evals: [{id, prompt,
  expected_output, files}]}`. `--tasks` defaults to this path. Test-case design
  guidance (vary phrasing, cover one boundary condition, use realistic context)
  belongs in the skill, not the script.
- **Per run** — `timing.json`: `{total_tokens, duration_ms}`. In Claude Code these
  two values come from the task-completion notification and, per the doc, **"are
  not persisted anywhere else"** — so capture them the moment a run finishes or
  they are gone. A run whose timing was lost is UNKNOWN, never zero.
- **Aggregate** — `benchmark.json`: `{run_summary: {<config>: {pass_rate,
  time_seconds, tokens}}, delta: {...}}`, each metric `{mean, stddev}`. `stddev`
  is only meaningful with multiple runs per eval, so with single runs report the
  raw counts and the delta and OMIT stddev rather than emitting a fake 0.
- **Isolation** — each run starts from a CLEAN context (a fresh subagent per run),
  so nothing leaks between variants. A shared context would make the comparison
  meaningless.
- **The delta is the deliverable.** Per the doc: the delta says what a variant
  COSTS (time, tokens) and what it BUYS (pass rate). "Higher pass rate for more
  tokens" is a trade-off to report, not a winner to declare — so the tool ranks
  nothing and never prints a verdict; it prints the delta and lets the human
  decide.

Output: a findings-style table plus `--json`, written under
`reports/cpv-agent-eval/`. Every number carries the tier that produced it; a
static estimate must never be presented as a measured result.

## 7. Universal constraints (all workstreams)

- CPV is UNIVERSAL. No ai-maestro assumption, no dependence on an install slug,
  marketplace, or cache path — a pre-publish source has none of those.
- Never suppress a security rule, never relax `--strict`.
- Every new detector needs TWO-SIDED tests: the finding fires on the defect AND
  stays silent on the legitimate sibling. A suppression test without a positive
  control passes vacuously.
- `ruff check` + `mypy` clean. re2-safe regexes (no lookbehind/lookahead) if any
  pattern enters the skillaudit catalog.
- Reports go under `reports/`; both `reports/` and `reports_dev/` are gitignored.
