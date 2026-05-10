# TRDD-82e836dc — Agent Model-Tier Policy Refactor

**TRDD ID:** `82e836dc-5880-42e6-a533-2e92747eeb77`
**Filename:** `design/tasks/TRDD-82e836dc-5880-42e6-a533-2e92747eeb77-agent-model-tier-policy.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (2026-05-10) — Phases A+B shipped
**Author:** Emanuele (orchestrator captured 2026-05-10)
**Created:** 2026-05-10
**Priority:** Medium (token-efficiency optimisation; user-visible cost reduction; no functional regression risk if done correctly)

---

## 1. User's request (verbatim)

> are the commands using sonnet or haiku to display the menus? and only call the agent (with opus) when it is necessary to do the menu choice?

> - launching scripts (validate, security scan, etc) will only require haiku..
> - basic commands that do not require analysis but only retrieving info, sonnett is enough..
> - diagnosis or analysis of problems, planning upgrades/migrations, reading reports or applying fixes: opus is necessary

(Followed by an instruction that the TRDD MUST capture this in detail because sessions can end abruptly.)

---

## 2. Policy (canonical statement)

The CPV plugin assigns one of three tiers to every agent:

| Tier | When to use | Examples in CPV |
|---|---|---|
| **haiku** | Launching scripts (validate, security scan, etc.); rendering menus and parsing integer/letter choices; routing one-off invocations to specialised work agents. NO analysis. | `cpv-main-menu-agent`, future `*-menu` dispatchers, validators that just shell out |
| **sonnet** | Basic commands that do NOT require analysis but only retrieve info, format output, run mechanical install/uninstall/list/show. | `plugin-manager`, `plugin-creator` (template-based wizard) |
| **opus / opus[1m]** | Diagnosis or analysis of problems, planning upgrades/migrations, reading reports, applying fixes, deep semantic checks. | `plugin-fixer-work`, `marketplace-fixer-work`, `cache-optimizer-work`, `cpv-doctor-work`, `plugin-diagnoser`, `semantic-validator` |

**Why this matters:** every menu-rendering turn on opus burns 5-15k tokens for what is functionally string-format and integer-parse. Across the user base, that compounds into measurable monthly spend with zero quality benefit. The fix is mechanical but architectural: split each menu-bearing agent into `*-menu` (haiku) + `*-work` (opus).

---

## 3. Current state vs target (audit)

Captured 2026-05-10 against agents/*.md frontmatter `model:` fields:

| Agent file | Current model | Target tier | Action |
|---|---|---|---|
| `cpv-main-menu-agent.md` | haiku | haiku | none |
| `plugin-validator.md` | sonnet | **haiku** | downgrade frontmatter only |
| `skill-validation-agent.md` | sonnet | **haiku** | downgrade frontmatter only |
| `plugin-manager.md` | sonnet | sonnet | none |
| `plugin-creator.md` | sonnet | sonnet | none (escalate to opus only on complex multi-component creates — leaf escalation, not whole-agent retier) |
| `plugin-fixer.md` | opus | **opus + new haiku menu** | split into `plugin-fixer-menu.md` (haiku) + `plugin-fixer.md` stripped of menu (opus) |
| `marketplace-fixer.md` | opus | **opus + new haiku menu** | split into `marketplace-fixer-menu.md` (haiku) + `marketplace-fixer.md` stripped of menu (opus) |
| `cache-optimizer-agent.md` | opus | **opus + new haiku menu** | split into `cache-optimizer-menu.md` (haiku) + `cache-optimizer-agent.md` stripped of menu (opus) |
| `cpv-doctor-agent.md` | opus | **opus + new haiku menu** | split into `cpv-doctor-menu.md` (haiku) + `cpv-doctor-agent.md` stripped of menu (opus) |
| `plugin-diagnoser.md` | opus | opus | none |
| `semantic-validator.md` | opus[1m] | opus[1m] | none |

---

## 4. Phasing

### Phase A — quick wins (frontmatter-only downgrades)

**Files:**
- `agents/plugin-validator.md` — change `model: sonnet` → `model: haiku`.
- `agents/skill-validation-agent.md` — change `model: sonnet` → `model: haiku`.

**Risk:** essentially zero. Both agents are documented as "Lightweight … runs scripts and returns compact summaries. Does NOT fix issues or perform semantic analysis". Their entire job is `Bash` + `Read` of the script's stdout + 1-2 lines of summary. Haiku handles this with margin.

**Tests:**
- Add `tests/test_agent_model_tiers.py::test_plugin_validator_is_haiku` — load frontmatter, assert `model == "haiku"`.
- Add `tests/test_agent_model_tiers.py::test_skill_validation_agent_is_haiku` — same.

### Phase B — agent splits (menu vs work)

For each of the four menu-bearing opus agents, produce TWO files:

#### B.1 `plugin-fixer` split

- **NEW `agents/plugin-fixer-menu.md`** (haiku) — frontmatter:
  ```yaml
  model: haiku
  description: |
    Lightweight haiku menu for /cpv-fix-validation. Renders the First Contact
    menu (numbered table of fix categories), parses the user's integer/letter
    choice, then dispatches to plugin-fixer (opus) only when a leaf is picked.
    NEVER does fixing itself — pure dispatch.
  ```
  Body: copy the existing First Contact menu rendering + parse logic from `plugin-fixer.md`. End each menu branch with: "dispatch to plugin-fixer with `<context>`".

- **`agents/plugin-fixer.md`** (opus, modified) — strip the First Contact menu section. Keep only the actual fixing workflows. Update description: "Self-sufficient fix agent invoked ONLY by plugin-fixer-menu after a menu choice is made. Receives the chosen fix category as context, applies fixes, re-validates."

- **`commands/cpv-fix-validation.md`** — change `agent: plugin-fixer` → `agent: plugin-fixer-menu`.

#### B.2 `marketplace-fixer` split

- **NEW `agents/marketplace-fixer-menu.md`** (haiku) — same pattern.
- **`agents/marketplace-fixer.md`** (opus, modified) — strip menu section.
- **`commands/cpv-fix-marketplace-validation.md`** — repoint to `marketplace-fixer-menu`.

#### B.3 `cache-optimizer-agent` split

- **NEW `agents/cache-optimizer-menu.md`** (haiku).
- **`agents/cache-optimizer-agent.md`** (opus, modified) — strip menu section.
- **`commands/cpv-cache-optimize.md`** — repoint to `cache-optimizer-menu`.

#### B.4 `cpv-doctor-agent` split

- **NEW `agents/cpv-doctor-menu.md`** (haiku) — render the 14-option doctor menu.
- **`agents/cpv-doctor-agent.md`** (opus, modified) — strip menu section. Keep deep diagnosis logic + the "follow-up menu" the doctor renders AFTER a diagnosis run — that follow-up menu is small (5 options) and is rendered after opus has read scanner output, so it stays on opus. (Note: the *first contact* menu before any scanning is the haiku candidate; the post-scan choose-what-to-fix menu stays on opus because it requires scanner-output context.)
- **`commands/cpv-doctor.md`** — repoint to `cpv-doctor-menu`.

#### Cross-cutting requirements for every split

1. **Menu agent must NOT have access to opus tools.** Its tool surface is restricted to `Bash`, `Read`, and the `Agent` tool (for dispatching). Frontmatter `tools: [Bash, Read, Agent]`.
2. **Work agent must NOT include any menu rendering.** Strip every numbered table, every "What would you like to do?" prompt. The work agent's input is always a structured `<context>` from the menu agent.
3. **Both agents share the same skill set.** Skills declared in the original agent's frontmatter must be replicated on the work agent (since that's where the actual work happens) but NOT on the menu agent (it doesn't load skills — it just dispatches).
4. **Backwards compat:** the slash-command surface stays identical. `/cpv-fix-validation` still works the same from the user's POV; only the orchestration under the hood changes.
5. **Dispatch protocol:** the menu agent calls the work agent via `Agent` tool with `subagent_type: <work-agent-name>` and `model: opus` (explicit override). The user's choice is passed as a structured context block in the prompt.

---

## 5. Test plan

### Unit tests (new file: `tests/test_agent_model_tiers.py`)

Each test reads the YAML frontmatter from the agent file under test and asserts the `model:` field.

```python
import yaml
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PLUGIN_ROOT / "agents"

def _load_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path} missing frontmatter")
    end = text.index("---", 3)
    return yaml.safe_load(text[3:end])

def test_plugin_validator_is_haiku():
    fm = _load_frontmatter(AGENTS_DIR / "plugin-validator.md")
    assert fm["model"] == "haiku"

def test_skill_validation_agent_is_haiku():
    fm = _load_frontmatter(AGENTS_DIR / "skill-validation-agent.md")
    assert fm["model"] == "haiku"

def test_plugin_fixer_menu_is_haiku():
    fm = _load_frontmatter(AGENTS_DIR / "plugin-fixer-menu.md")
    assert fm["model"] == "haiku"

def test_plugin_fixer_work_is_opus():
    fm = _load_frontmatter(AGENTS_DIR / "plugin-fixer.md")
    assert fm["model"] == "opus"

# … same for marketplace, cache-optimizer, cpv-doctor splits
```

### Architectural-discipline tests

```python
def test_no_opus_agent_renders_first_contact_menu():
    """Opus agents must not contain First Contact / numbered-menu blocks.
    They are dispatched by haiku menu agents which already made the choice."""
    OPUS_AGENTS = [
        "plugin-fixer.md",
        "marketplace-fixer.md",
        "cache-optimizer-agent.md",
        # cpv-doctor-agent.md stays opus but keeps post-scan menu (see §4 B.4) —
        # exclude it from this test.
    ]
    for name in OPUS_AGENTS:
        body = (AGENTS_DIR / name).read_text(encoding="utf-8")
        # Heuristic: the table-drawing characters ┏ ┳ ━ that the legacy menus
        # use should not appear in a work-only agent.
        assert "┏━" not in body, f"{name} still contains a menu table"
        assert "First Contact" not in body, f"{name} still contains First Contact section"

def test_menu_agent_tool_surface_is_minimal():
    """Haiku menu agents must declare tools: [Bash, Read, Agent] only."""
    MENU_AGENTS = [
        "plugin-fixer-menu.md",
        "marketplace-fixer-menu.md",
        "cache-optimizer-menu.md",
        "cpv-doctor-menu.md",
    ]
    for name in MENU_AGENTS:
        fm = _load_frontmatter(AGENTS_DIR / name)
        tools = set(fm.get("tools", []))
        # Allow tools to be a list of strings OR a list of {name: ...} dicts.
        # Normalise:
        normalised = {t if isinstance(t, str) else t.get("name") for t in fm.get("tools", [])}
        assert normalised <= {"Bash", "Read", "Agent"}, \
            f"{name} declares forbidden tools: {normalised - {'Bash','Read','Agent'}}"
```

### E2E behavioural tests (slow-marked 🐌)

```python
@pytest.mark.slow
def test_cpv_fix_validation_dispatch_chain():
    """Invoking /cpv-fix-validation routes haiku-menu → opus-work."""
    # Spawn a fresh CC session, run the command, verify the agent invocation
    # log shows haiku first, opus second, exactly one of each.
    pass  # implementation outline only — requires harness
```

(E2E tests deferred to implementation; the unit + architectural tests are sufficient to guard the policy.)

### CHANGELOG entry

A single entry under the next minor version:
```
- agent: model-tier discipline refactor (TRDD-82e836dc).
  • plugin-validator + skill-validation-agent: sonnet → haiku (script-launchers, no analysis)
  • plugin-fixer / marketplace-fixer / cache-optimizer / cpv-doctor: split into haiku menu + opus work.
  • Token cost reduction estimated 40-60% on routine /cpv-fix-validation, /cpv-doctor, /cpv-cache-optimize invocations.
```

---

## 6. Critical files

| Path | Phase | Action |
|---|---|---|
| `agents/plugin-validator.md` | A | edit `model:` |
| `agents/skill-validation-agent.md` | A | edit `model:` |
| `agents/plugin-fixer-menu.md` | B.1 | NEW |
| `agents/plugin-fixer.md` | B.1 | strip menu, keep work |
| `commands/cpv-fix-validation.md` | B.1 | repoint `agent:` |
| `agents/marketplace-fixer-menu.md` | B.2 | NEW |
| `agents/marketplace-fixer.md` | B.2 | strip menu |
| `commands/cpv-fix-marketplace-validation.md` | B.2 | repoint |
| `agents/cache-optimizer-menu.md` | B.3 | NEW |
| `agents/cache-optimizer-agent.md` | B.3 | strip menu |
| `commands/cpv-cache-optimize.md` | B.3 | repoint |
| `agents/cpv-doctor-menu.md` | B.4 | NEW |
| `agents/cpv-doctor-agent.md` | B.4 | strip First-Contact menu (keep post-scan menu) |
| `commands/cpv-doctor.md` | B.4 | repoint |
| `tests/test_agent_model_tiers.py` | A+B | NEW (unit + architectural) |
| `CHANGELOG.md` | A+B | new minor entry |

**Skill cross-reference:**
- `skills/plugin-management.md` — verify it doesn't hard-code `subagent_type: plugin-manager` etc. in a way that would skip the new menu chain. Update to mention the menu split.
- `skills/fix-validation.md` — same; update dispatch examples.
- `skills/cache-fixes.md` — same.
- `references/canonical-pipeline-migration-checklist.md` — search for any text referencing the old single-agent flow; update.

---

## 7. Existing utilities to reuse

- `cpv-main-menu-agent.md` — gold standard for haiku menu pattern. Copy its dispatch idiom (numbered table + integer-or-letter parser + `0 — Cancel` / `9 — Back` conventions) into each new `*-menu` agent.
- `cpv_validation_common.print_compact_summary` — already prints colored severity tables. Re-use for any menu agent that needs to summarise prior validator output before showing the menu.

---

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Menu agent dispatches wrong work agent | Architectural test asserts the dispatch lookup table maps each leaf to exactly one opus agent; reject if mismatch. |
| User on slow connection sees menu render delay grow because of double-hop (menu→work) | Haiku is faster than sonnet/opus for short outputs; net effect should be FASTER first-paint. Verify with a stopwatch test in the e2e suite. |
| Skills declared on both menu and work agent → loaded twice | §4.3: skills go on WORK agent only. Menu agent has empty `skills:` list. |
| User explicitly invokes `Agent({subagent_type: "plugin-fixer", …})` from a script — backwards-incompat | Plugin-fixer name is preserved (it's the work agent post-split); only the menu surface is new. The slash-command repointing handles user-facing invocations. Direct subagent_type calls keep working. |
| One of the existing skills hard-codes `subagent_type: plugin-fixer` and expects to enter via menu | Skill update is in scope (§6). Grep for `subagent_type: plugin-fixer\b` etc. before merging Phase B. |

---

## 9. Out of scope

- **plugin-creator escalation to opus on complex multi-component creates.** That's a leaf-level escalation pattern; if needed, it's a separate TRDD because the trigger condition (when does sonnet need to escalate?) requires its own design.
- **Cost monitoring / token-budget telemetry.** A future TRDD could add per-agent token counters; this TRDD only changes the assignment.
- **A/B test of haiku-menu UX vs status-quo opus-menu.** The user's directive establishes the policy; no A/B needed.

---

## 10. Acceptance criteria

The TRDD is **Done** when ALL of the following hold:

- [ ] `agents/plugin-validator.md` declares `model: haiku`
- [ ] `agents/skill-validation-agent.md` declares `model: haiku`
- [ ] Four NEW `*-menu.md` files exist with `model: haiku` and `tools: [Bash, Read, Agent]`
- [ ] Four corresponding work-agent files exist with `model: opus` and NO First-Contact menu in the body
- [ ] Four corresponding command `*.md` files have `agent:` pointing at the menu variant
- [ ] `tests/test_agent_model_tiers.py` ships and ALL tests in it pass under `uv run pytest tests/test_agent_model_tiers.py -v`
- [ ] Full suite passes under `uv run pytest tests/ -n auto --dist=worksteal --maxfail=1 -q`
- [ ] CHANGELOG entry under the next minor version
- [ ] `validate_plugin.py . --strict` exits 0 against the modified plugin tree
- [ ] Manual smoke: `/cpv-fix-validation` shows the same menu as before, picking a leaf still ends in fix work, behaviour parity confirmed
- [ ] This TRDD's `**Status:**` line set to `Done (YYYY-MM-DD) — Phases A+B shipped`

---

## 11. Implementation note for future agent

This TRDD is mechanical but touches many files. Recommended approach:

1. Phase A first (single small PR) — ship + test in isolation. Catches any base-case regression before attempting splits.
2. Phase B second — one PR per agent split (4 PRs total) so each can be reverted independently if user feedback surfaces a UX regression. If shipping all four together, gate them behind a feature flag in plugin.json so a hotfix can flip them off without a re-publish.
3. Use TDD: write the architectural-discipline test FIRST, watch it fail, then split. The failing test is your ratchet.
4. After all phases ship, use `cpv-cache-optimize` (the cache-optimization audit) to verify the new menu agents do not re-introduce CA-01..CA-06 cache-invalidation patterns.

---

## 12. References

- User policy statement: 2026-05-10 conversation turn (verbatim in §1).
- Memory note (CPV plugin): "Model selection: Never use Haiku unless it is to run scripts or execute predefined tasks. Use Sonnet instead." → menu rendering qualifies as "predefined task" and aligns with this rule.
- `cpv-main-menu-agent.md` description quote: "Runs on Haiku for fast menu rendering (this agent only displays tables and parses integer/letter choices). Heavy lifting is dispatched to specialised Opus agents (plugin-creator, plugin-fixer, plugin-diagnoser, marketplace-fixer, semantic-validator, cache-optimizer-agent) when a leaf is picked." → confirms the pattern is already in use for the global menu and just needs to be propagated to the four direct-entry commands.
