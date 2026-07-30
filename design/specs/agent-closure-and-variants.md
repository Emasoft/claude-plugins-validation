# SPEC — agent skill-closure, single-agent security scan, variant conversion, variant evaluation

**Status:** normative. Every workstream below builds against the signatures pinned
here VERBATIM. Do not rename a field, reorder a positional parameter, or change a
return type — three other workstreams import this surface concurrently.

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

**The `Skill`-tool gate** (`agent_can_load_skills_at_runtime`):

- no `tools:` field  → **True** (the agent inherits every session tool)
- `tools:` contains `Skill` (normalized) → **True**
- `tools:` present without `Skill` → **False** — every runtime invocation in that
  body is DEAD, and a `skills:` preload is the agent's only skill access

`ambient` is the full set of skill names present in the search roots. When the
gate is open they are all *potentially* loadable, so the set is REPORTED but its
members are NOT validated by default — validating an entire palette per agent
would be pure noise. Opt in with `--closure-ambient`.

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
| **AC4** | a `skills:` preload the body never uses, while the gate is open | WARNING (a preload injects FULL content every invocation) |

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
generators already do) plus its closure.

```
convert_agent.py <agent.md> --to mono  [--out DIR] [--name NAME] [--force]
convert_agent.py <agent.md> --to micro [--out DIR] [--name NAME] [--force]
```

- `--to mono` → `<name>-mono.md`: the source agent's body with every REACHABLE
  skill inlined under `## Skill: <name>` (frontmatter stripped, headings demoted
  so the agent keeps one H1). Turn-1 ready, no runtime load. Carries the union of
  the source's tool grants; no `model:` pin (CA-04).
- `--to micro` → `<name>-launcher.md` + `workflows/<name>-micro.ts`: a thin
  launcher plus a Workflow-tool graph whose nodes are the closure's skills,
  each run as a near-empty micro-agent, edges threaded output→input, per-step
  verify.
- Never overwrite without `--force`. Both outputs must pass `validate_agent`
  (and the emitted `.ts` must live under a `known_dirs` entry).
- Both modes read the closure through §2. Neither may re-derive the skill set.
- The two existing skills (`cpv-create-mono-agent`,
  `cpv-create-micro-agents-workflow`) gain the agent-scoped path and delegate to
  this script; their plugin-wide path is unchanged.

## 6. `scripts/cpv_agent_eval.py` (workstream T4)

Selectively compare the ORIGINAL agent against its `mono` and `micro` variants.

```
cpv_agent_eval.py --original A.md [--mono M.md] [--micro L.md] \
                  --variants original,mono,micro [--tasks tasks.jsonl] [--live]
```

**Tier 1 — static cost model. Always runs. Zero LLM calls. Real measurements
over real files.** Per variant: cached-prefix token estimate (system prompt =
the agent body + injected preload content), per-invocation injected tokens,
tool-schema surface count, closure size (files + bytes), turn-1 readiness, and
the projected cost of N turns under the prefix-cache read rate. This tier is
what the test suite asserts, because it is deterministic.

**Tier 2 — live A/B/C. OPT-IN via `--live`, and never implied.** Dispatches each
selected variant on a real task set and records REAL tokens, turns, wall time,
and outcome. **No mocks, no simulated numbers, no fabricated comparison** — if
the harness cannot run, it reports UNKNOWN and exits non-zero. A missing
`--tasks` file is an error, never an empty pass.

Output: a findings-style table plus `--json`, written under
`reports/cpv-agent-eval/`. The report must state which tier produced each number;
a static estimate must never be presented as a measured result.

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
