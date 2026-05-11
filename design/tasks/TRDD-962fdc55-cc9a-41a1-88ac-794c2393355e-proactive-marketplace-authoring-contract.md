# TRDD-962fdc55 — Proactive Marketplace Authoring Contract for Plugin-Touching Agents

**TRDD ID:** `962fdc55-cc9a-41a1-88ac-794c2393355e`
**Filename:** `design/tasks/TRDD-962fdc55-cc9a-41a1-88ac-794c2393355e-proactive-marketplace-authoring-contract.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Not started — queued behind TRDD-c0ee9543
**Blocked by:** TRDD-c0ee9543 must land first (this TRDD touches the agent `.md` files and references skills whose recipes are emitted by Phase D of c0ee9543)
**Author:** Emanuele (orchestrator captured 2026-05-11)
**Created:** 2026-05-11
**Priority:** HIGH — the user's escalation: *"every plugin updated/migrated/created by the agents was produced with errors. only the doctor was capable of detecting them, but executing the doctor is costly in terms of tokens. and the user expects the plugin to be correctly upgraded/created by the plugin. it certainly does not expect a plugin that is supposed to repair the errors in plugins ends creating new errors!"*

---

## 1. User's request (verbatim)

> you must improve the agents and their skills. until now every plugin updated/migrated/created by the agents was produced with errors. only the doctor was capable of detecting them, but executing the doctor is costly in terms of tokens. and the user expects the plugin to be correctly upgraded/created by the plugin. it certainly does not expect a plugin that is supposed to repair the errors in plugins ends creating new errors!

---

## 2. Problem (one-paragraph)

CPV ships agents whose explicit job is to *fix*, *migrate*, *create*, or *upgrade* Claude Code plugins — but those agents have been systematically producing **new** errors of the same shape they claim to detect (name-mismatch, stale-version, unknown-fields like `scope`). The errors only surface when the user runs `/cpv-doctor`, a deep-diagnostic opus-tier flow that costs 5–15k tokens per run. The agents should be PROACTIVELY structured to never emit drift in the first place — gating at completion (TRDD-c0ee9543 Phase F) catches the bad output but does not address the broken authoring flow that produces it.

---

## 3. Why Phase F of TRDD-c0ee9543 is necessary but not sufficient

TRDD-c0ee9543 Phase F adds a *reactive* gate: *"agent must run validate_marketplace.py --strict --cross-validate-upstream BEFORE declaring done"*. That prevents bad output from shipping but does not change the authoring flow.

Consequences of relying only on the reactive gate:

1. **Wasted opus turns.** Every "creator" or "fixer" invocation drafts a bad marketplace.json, gets rejected by the gate, retries, drafts another bad one, gets rejected again, … N retries until either lucky or the user intervenes. Each retry burns opus tokens on the SAME wrong reasoning.
2. **User-visible failure modes.** Some failure shapes the gate cannot auto-fix (e.g. "marketplace entry name MUST equal plugin.json name" — the gate emits MAJOR but the agent must still decide which name to keep). Without proactive guidance, the agent may pick wrong.
3. **No teaching signal.** A gated agent learns "stop when validator complains" but never internalises *why* — so the next refactor reintroduces the same shape.

This TRDD adds the **proactive** half: agents internalise the marketplace-authoring contract, draft correct output on the first try, and only fall back to the validator gate as a safety net.

---

## 4. Scope — which agents are affected

Agents that emit, modify, or migrate `marketplace.json` directly OR indirectly via plugin scaffolding:

| Agent | Current model | Files emitting marketplace.json (or fragments) |
|---|---|---|
| `plugin-creator` | sonnet | `commands/cpv-create.md` flow, scaffolds via `scripts/generate_plugin_repo.py` |
| `plugin-fixer` (work agent post-TRDD-82e836dc split) | opus | applies fix-validation recipes against existing marketplace.json |
| `plugin-fixer-menu` | haiku | dispatcher only — out of scope |
| `marketplace-fixer` (work agent) | opus | direct marketplace.json edits |
| `marketplace-fixer-menu` | haiku | dispatcher only — out of scope |
| `plugin-upgrade` / `cpv-upgrade-plugin` | (sonnet flow + plugin-fixer opus work) | bumps plugin version → must coordinate with sibling marketplace entry |
| `migrate-marketplace` (via `commands/cpv-migrate-marketplace.md` + `scripts/migrate_marketplace.py` + `skills/migrate-marketplace-architecture/`) | (orchestrator flow) | Layout A↔B↔C migrations all touch marketplace.json shape |
| `plugin-manager` | sonnet | install/list flows that READ marketplace.json — read-only, out of scope |
| `cpv-doctor-agent` | opus | the SAFETY NET — out of scope, but its findings become the new agents' training signal |

5 in-scope agents.

---

## 5. The contract — what every in-scope agent must internalise

NEW skill: `skills/marketplace-authoring-contract/SKILL.md` (user-invocable: false; loaded by the 5 agents above).

Contract sections (each becomes a reference file under `skills/marketplace-authoring-contract/references/`):

### 5.1 — `name-canonicalisation.md`

**Rule:** The marketplace entry's `name` MUST equal the upstream `plugin.json`'s `name`. No exceptions, no brand-aliasing, no shortening.

**Why:** the install resolver looks up by name. `claude plugin install foo@mkpl` reads `mkpl.plugins[?(@.name=='foo')].source`. If the marketplace `.name` ≠ `plugin.json.name`, the canonical install command fails with cryptic "not found" error.

**How agents must apply:**
- BEFORE drafting any marketplace entry, fetch the upstream plugin.json (via the new Phase B fetcher from TRDD-c0ee9543) and use its `.name` verbatim.
- If the user requests a different name (rare), refuse and explain.
- If the upstream plugin.json is unreachable, emit a DRAFT marketplace.json with `name: "<UPSTREAM_PLUGIN_JSON_NAME_HERE>"` and explicit comment, never invent a name.

### 5.2 — `version-strategy.md`

**Rule:** For `source: url` and `source: github` entries, **OMIT** the `version` field. The install resolver consults upstream tags; the marketplace `.version` is only consulted for display and goes stale within hours of every plugin release. For `source: relative-path` entries (Layout B nested monorepo), the `version` field is REQUIRED and MUST equal `plugin.json.version` (no upstream fetcher applies for local).

**Why:** prevents the entire "stale version" class of bug. Was the dominant pattern in the 2026-05-11 incident (every multi-plugin marketplace had at least one entry with version drift).

**How agents must apply:**
- `plugin-creator` defaulting marketplace scaffolds — emit `source: github` (no version field) unless user explicitly asks for `relative-path`.
- `plugin-fixer` — when fixing version-drift findings, auto-DROP the field rather than auto-bump (unless `relative-path`).
- `migrate-marketplace` — when migrating Layout A → B, ADD version field (now mandatory); B → A, DROP it.

### 5.3 — `known-fields.md`

**Rule:** Marketplace entries' top-level field allowlist is exactly:
`name, description, version, author, homepage, repository, license, keywords, source, category, tags, claude_versions, platforms, alwaysLoad, headersHelper`

No other fields. Specifically forbidden (with rationale + alternative):

| Forbidden field | Why agents try to add it | Correct alternative |
|---|---|---|
| `scope` | conflated with `--scope <local\|user\|project>` install flag | document the recommended scope in plugin README, OR set as default in plugin.json's settings |
| `private` | conflated with GitHub repo visibility | rely on `source: github` returning 404 on private repos for unauthed installs |
| `published` | conflated with `claude_versions` | use `claude_versions: { min: "2.1.x" }` |
| `requires` | conflated with plugin.json's `dependencies` block | use plugin.json's `dependencies` block |
| `archived` | conflated with GitHub archived status | mark the GitHub repo as archived; the marketplace will reflect it |

**How agents must apply:**
- When asked to add an unknown field, REFUSE and explain — point at this reference.

### 5.4 — `source-shape.md`

Per-source-type field allowlist + canonical examples:

```jsonc
// source: github — for plugins hosted on GitHub
{ "source": "github", "repo": "owner/repo", "ref": "main" }   // ref optional

// source: url — for plugins hosted anywhere git can clone (HTTPS)
{ "source": "url", "url": "https://github.com/owner/repo.git" }

// source: git — like url but explicit, with optional ref
{ "source": "git", "url": "https://example.com/repo.git", "ref": "v1.2.3" }

// source: git-subdir — monorepo subdirectory
{ "source": "git-subdir", "url": "...", "subdir": "plugins/foo", "ref": "main" }

// source: npm — published npm package
{ "source": "npm", "package": "@scope/plugin" }

// source: relative-path — Layout B nested monorepo
{ "source": "relative-path", "path": "./plugins/foo" }
```

No other fields per source type. Especially **NO `version` field on `github`/`url`/`git`/`git-subdir`** (consulted from upstream tag).

### 5.5 — `layout-decision-tree.md`

When asked to create a marketplace, the agent walks this tree:

```
Q1: How many plugins will live in this marketplace?
├── 1 plugin only?  → Layout C (self-marketplace-in-plugin), single self-entry, `source: "./"`.
└── 2+ plugins
    ├── Are all plugins in the SAME GitHub repo?  → Layout B (nested monorepo), `source: relative-path`.
    └── Different repos                             → Layout A (hub-and-spoke), `source: github`/`url`.
```

Each layout has a fixed entry-shape — no improvisation.

### 5.6 — `common-pitfalls.md` (lessons from incidents)

Catalog of past failure modes with detection regex + auto-fix patch. Currently:

- **PIT-001** (2026-05-11): name mismatch — entry name uses non-`-plugin` suffix while plugin.json uses `-plugin` suffix.
- **PIT-002** (2026-05-11): stale version on `source: url` entries.
- **PIT-003** (2026-05-11): top-level `scope` field intended as install-scope hint.
- **PIT-004** (v2.32.0): Layout C self-entry omitting `"source": "./"` literal (must be present, not inferred).
- **PIT-005** (v2.22.x): `source: github` with full URL instead of `owner/repo` shorthand.
- **PIT-006** (legacy): `homepage` field pointing at a different repo than `source` (cross-link).
- **PIT-007** (v2.x): `category` field with arbitrary user value instead of canonical taxonomy.

Each pitfall includes a one-liner regex + Edit/diff patch for `plugin-fixer` to apply.

### 5.7 — `preflight-recipe.md`

Mechanical preflight every agent runs BEFORE emitting marketplace.json:

```bash
# 1. If editing existing marketplace.json
CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py <mkpl-path> --strict --cross-validate-upstream
# 2. If creating new marketplace.json (Layout A or B)
for entry in <list>; do
    fetch_upstream_plugin_json <entry-source>
    assert entry.name == upstream.name
    assert entry.version is absent OR entry.version == upstream.version
done
# 3. Emit
write marketplace.json
# 4. Post-emit sanity check (same as step 1)
CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py <mkpl-path> --strict --cross-validate-upstream
```

Step 1 is OPTIONAL for fresh creates; step 4 is MANDATORY for every flow.

---

## 6. Wiring — how each agent loads the contract

Each in-scope agent's `.md` frontmatter gets:

```yaml
skills:
  - marketplace-authoring-contract   # NEW — added by this TRDD
  - <existing skills unchanged>
```

Each agent's BODY gets a new section:

```markdown
## Marketplace Authoring Contract (MANDATORY READ)

BEFORE drafting, modifying, or migrating ANY `marketplace.json`, read:
`skills/marketplace-authoring-contract/SKILL.md` and ALL its references.

Failure to apply the contract produces user-facing install failures —
the doctor agent catches these after the fact but at high opus token
cost. The user expects this agent to produce correct output on the
FIRST try, not after N validator retries.
```

---

## 7. Anti-patterns the contract eliminates

Each of these has been observed in CPV agent output (some pre-TRDD-c0ee9543, some during testing):

1. **Brand-aliasing the name** — agent shortens `ai-maestro-visual-communicator-plugin` to `ai-maestro-visual-communicator` because it "looks cleaner". Forbidden.
2. **Copying the version from a previous entry** — when adding a new entry to an existing marketplace.json, agent copies `"version": "1.0.0"` from the first entry without consulting the new plugin's actual version. Forbidden (drop the field per §5.2).
3. **Inventing "useful" fields** — `scope`, `private`, `published`, `requires`. Forbidden per §5.3.
4. **Mixing source types within one marketplace** without justification — Layout A entries should all be `github` (or all `url`); Layout B all `relative-path`. Mixing creates resolver edge cases.
5. **Not consulting upstream** — emitting a marketplace entry without fetching the upstream plugin.json. The "I'll guess what the user wants" failure mode.
6. **Reusing `description` from sibling entries** — agent copies the description of a previous plugin to the new entry because they're "similar". Forbidden — use upstream plugin.json's description verbatim.

The contract turns each anti-pattern into an explicit refusal.

---

## 8. Test plan

### 8.1 Architectural-discipline tests (`tests/test_marketplace_authoring_contract.py`)

```python
def test_all_in_scope_agents_load_contract_skill():
    """plugin-creator, plugin-fixer, marketplace-fixer, plugin-upgrade,
    migrate-marketplace must declare marketplace-authoring-contract in
    their skills: frontmatter."""
    AGENTS = ["plugin-creator", "plugin-fixer", "marketplace-fixer"]
    for agent in AGENTS:
        fm = _load_frontmatter(AGENTS_DIR / f"{agent}.md")
        skills = fm.get("skills", [])
        assert "marketplace-authoring-contract" in skills, \
            f"{agent} missing marketplace-authoring-contract skill"

def test_contract_skill_has_all_seven_references():
    refs_dir = SKILLS_DIR / "marketplace-authoring-contract" / "references"
    expected = {
        "name-canonicalisation.md",
        "version-strategy.md",
        "known-fields.md",
        "source-shape.md",
        "layout-decision-tree.md",
        "common-pitfalls.md",
        "preflight-recipe.md",
    }
    actual = {p.name for p in refs_dir.glob("*.md")}
    assert expected <= actual

def test_contract_known_fields_match_validator_allowlist():
    """The contract's §5.3 allowlist MUST equal the validator's
    _KNOWN_MARKETPLACE_ENTRY_FIELDS. Drift between contract and validator
    is a self-inconsistency bug."""
    from scripts.validate_marketplace import _KNOWN_MARKETPLACE_ENTRY_FIELDS
    contract_fields = _parse_contract_known_fields()
    assert contract_fields == _KNOWN_MARKETPLACE_ENTRY_FIELDS
```

### 8.2 Behavioural tests via fixture roundtrip

```python
def test_creator_emits_marketplace_passing_strict_validate(tmp_path):
    """End-to-end: invoke plugin-creator's scaffold flow programmatically,
    confirm the emitted marketplace.json passes
    `validate_marketplace.py --strict --cross-validate-upstream`."""
    pass  # implementation via subprocess + golden output
```

(E2E tests slow-marked 🐌 for opus subagent invocation.)

### 8.3 Regression tests for the 7 pitfalls

`tests/test_marketplace_pitfall_regression.py` — one test per PIT-001..PIT-007. Each constructs a fixture marketplace.json with the pitfall, runs `validate_marketplace.py --strict --cross-validate-upstream`, and asserts the expected MAJOR/MINOR finding emits.

---

## 9. Critical files

| Path | Action |
|---|---|
| `skills/marketplace-authoring-contract/SKILL.md` | NEW — root |
| `skills/marketplace-authoring-contract/references/name-canonicalisation.md` | NEW |
| `skills/marketplace-authoring-contract/references/version-strategy.md` | NEW |
| `skills/marketplace-authoring-contract/references/known-fields.md` | NEW |
| `skills/marketplace-authoring-contract/references/source-shape.md` | NEW |
| `skills/marketplace-authoring-contract/references/layout-decision-tree.md` | NEW |
| `skills/marketplace-authoring-contract/references/common-pitfalls.md` | NEW |
| `skills/marketplace-authoring-contract/references/preflight-recipe.md` | NEW |
| `agents/plugin-creator.md` | add skill loader + "Marketplace Authoring Contract" body section |
| `agents/plugin-fixer.md` | same |
| `agents/marketplace-fixer.md` | same |
| `commands/cpv-upgrade-plugin.md` | same |
| `commands/cpv-migrate-marketplace.md` | same |
| `tests/test_marketplace_authoring_contract.py` | NEW (architectural + behavioural) |
| `tests/test_marketplace_pitfall_regression.py` | NEW (7 pitfalls) |
| `CHANGELOG.md` | entry under next minor |

---

## 10. Acceptance criteria

The TRDD is **Done** when ALL of the following hold:

- [ ] `skills/marketplace-authoring-contract/` exists with `SKILL.md` + 7 references
- [ ] 5 in-scope agent/command files load the new skill AND have a "Marketplace Authoring Contract" body section pointing at it
- [ ] `test_marketplace_authoring_contract.py` ships, all tests pass
- [ ] `test_marketplace_pitfall_regression.py` ships with 7 pitfall regressions, all pass
- [ ] `validate_plugin.py . --strict` against CPV exits 0 (every new SKILL.md respects TOC parity contract)
- [ ] CHANGELOG.md updated
- [ ] This TRDD's `**Status:**` line set to `Done (YYYY-MM-DD)`
- [ ] Manual repro: instruct plugin-creator to scaffold a new marketplace, confirm the output passes `validate_marketplace.py --strict --cross-validate-upstream` on the first try (no retries needed)

---

## 11. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Contract drifts away from validator (e.g. validator adds new known-field but contract not updated) | Architectural test §8.1 `test_contract_known_fields_match_validator_allowlist` ratchets the two together |
| Agents ignore the new body section (description-buried) | Place it RIGHT AFTER the agent's `## When to use this agent` section — first thing the agent reads |
| User's existing marketplaces fail the new strict validation post-upgrade | TRDD-c0ee9543 Phase D recipes handle this — agents can auto-fix existing drift with the new recipes |
| Layout-C self-entry forces version field always present (per §5.2 logic for relative-path) — but Layout-C uses `source: "./"`, not `relative-path` | §5.2 clarification: for ALL inline/local sources (`./`, `relative-path`, `directory`, `file`) → version required; for ALL remote sources (`github`, `url`, `git`, `git-subdir`, `npm`) → version forbidden |
| Plugin-creator's wizard might want to ask user for the name before fetching upstream | Wizard restructured: (1) ask source type, (2) ask source location, (3) FETCH upstream plugin.json, (4) display canonical name to user, (5) confirm |

---

## 12. Out of scope

- Rewriting the install resolver (Anthropic's responsibility)
- Auto-publishing fixes upstream (plugin-fixer applies fixes locally; user must push)
- Cross-validation in the `plugin-validator` agent (already covered by `validate_marketplace.py` per TRDD-c0ee9543)
- New marketplace sources (e.g. GitLab) — out of scope until Anthropic adds them

---

## 13. References

- TRDD-c0ee9543 — the validator-side prerequisite this TRDD depends on
- 2026-05-11 user report — §1 verbatim, the triggering incident
- v2.32.0 Layout C migration TRDD — precedent for layout-decision-tree approach
- v2.80.0 SKILL.md TOC parity contract — every new reference file's TOC must be embedded verbatim in `marketplace-authoring-contract/SKILL.md`
