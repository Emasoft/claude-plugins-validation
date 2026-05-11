# TRDD-c0ee9543 — Marketplace ↔ Upstream Plugin.json Cross-Validation + Schema-Strict Fields + Doctor Menu Integration

**TRDD ID:** `c0ee9543-6104-48f4-bb00-420af6d57c4b`
**Filename:** `design/tasks/TRDD-c0ee9543-6104-48f4-bb00-420af6d57c4b-marketplace-upstream-cross-validation.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (2026-05-11) — Phases A+B+D+E+F shipped; Phase C deferred to follow-up wave
**Author:** Emanuele (orchestrator captured 2026-05-11)
**Created:** 2026-05-11
**Priority:** HIGH — caught at user-report stage; same bug class is shipping daily because validator misses it
**Triggering incident:** `Emasoft/ai-maestro-visual-communicator-plugin` v1.2.2 ship failed install via canonical name; `claude plugin install` reported "not found" because marketplace.json had divergent name + stale version + unknown `scope` field on 9 sibling entries. CPV's `validate_marketplace.py --strict` returned VALID (no findings) — false-negative across the entire bug class.

---

## 1. User's request (verbatim)

> the cpv plugin creator/migrate/upgrade/fix/validator agent failed again. this time when updating/migrating/creating some plugins it caused the following errors (discovered by the doctor agent, missing from the main menu btw!) and already under fixing by other claude:
>
> 1. Name mismatch — marketplace entry uses `ai-maestro-visual-communicator` while the plugin's own `plugin.json` declares `ai-maestro-visual-communicator-plugin`. `claude plugin install <plugin.json-name>@<marketplace>` fails with a confusing "not found" error.
> 2. Stale version — marketplace entry pins `"version": "1.0.0"` while the plugin is now at v1.2.2.
> 3. Unrecognized `scope` key on 9 plugin entries — `claude plugin validate <marketplace>` rejects entries that use a top-level `"scope": "local"` key (no such field in the Claude Code marketplace spec).
>
> [...]
>
> write the trdd and fix everything. add even more checks and fixes recipes.

---

## 2. Root cause (single sentence)

CPV's `validate_marketplace.py` validates the **shape** of marketplace.json in isolation but never **cross-references** the listed plugin entries against their upstream `plugin.json`, and never **strict-allowlists** plugin-entry fields against the Claude Code marketplace spec.

The comment at `scripts/validate_marketplace.py:1079` actually documents the bug as a feature: *"CPV cannot fetch the plugin.json at validate-time"* — but CPV CAN (and CPV's own integrity-manifest fetcher does, see `cpv_integrity.py`). The cache mechanism, network resilience, and bypass env vars already exist; only the validator-side hookup is missing.

---

## 3. Authoritative spec references

- Claude Code marketplace.json spec — `https://code.claude.com/docs/en/marketplace.md` (the `plugins[].name` field MUST equal `<repo>/.claude-plugin/plugin.json` `name` per the install resolver's lookup contract; the `scope` field does NOT exist at the entry level)
- Claude Code plugin.json spec — `https://code.claude.com/docs/en/plugin-manifest.md`
- Issue thread (CPV side): this TRDD
- Issue thread (downstream): `ai-maestro-visual-communicator-plugin` marketplace-side fix (in flight by another Claude session, 2026-05-11)

---

## 4. Audit — current gap matrix

Captured 2026-05-11 against `scripts/validate_marketplace.py` HEAD `ea8dacc`:

| # | Check | Spec source | Current CPV behavior | Severity-when-fixed |
|---|---|---|---|---|
| GAP-1 | marketplace entry `name` MUST equal upstream `plugin.json.name` | install resolver | NEVER cross-validates; commented as out-of-scope | **MAJOR** |
| GAP-2 | marketplace entry `version` MUST equal or be absent vs upstream `plugin.json.version` | display contract | NEVER cross-validates | **MINOR** (or drop the field altogether for `source: url`) |
| GAP-3 | marketplace entry MUST NOT carry unknown top-level fields (e.g. `scope`) | schema spec | silently accepts any field | **MAJOR** + fix-recipe |
| GAP-4 | `source.url` MUST be reachable (HTTP HEAD or `git ls-remote`) | install resolver | never probes the source | **WARNING** (network-conditional) |
| GAP-5 | `source.repo` (github source) MUST exist on GitHub | install resolver | never probes | **MAJOR** if 404 |
| GAP-6 | `source.git-subdir` path MUST exist in the repo | install resolver | never probes | **MAJOR** if missing |
| GAP-7 | marketplace entry `description` should match upstream `plugin.json.description` (consistency) | UX | never cross-validates | **NIT** |
| GAP-8 | marketplace entry `author` should match upstream `plugin.json.author` | UX | never cross-validates | **NIT** |
| GAP-9 | marketplace entry `keywords` should match upstream `plugin.json.keywords` | UX | never cross-validates | **NIT** |
| GAP-10 | marketplace entry `homepage` / `repository` URLs should match the source URL | UX | never cross-validates | **NIT** |
| GAP-11 | marketplace entry source-object fields MUST be in the source-type allowlist (e.g. `git-subdir` requires `subdir`, `github` requires `repo`, etc.) | spec | partial coverage; recent v2.32.x added some types but not strict-rejecting unknown sub-fields | **MAJOR** for unknown source-sub-fields |
| GAP-12 | marketplace-wide `name` must match the marketplace `.claude-plugin/marketplace.json`'s on-disk filename context | install resolver | not currently checked | **MAJOR** |
| GAP-13 | Layout C (marketplace-in-plugin) — when marketplace.json contains a single self-entry, that entry's `name` MUST equal the sibling plugin.json's `name` AND its `version` MUST equal the sibling plugin.json's `version` | spec (Layout C) | partial since v2.32.0 (`validate_layout_c_consistency`) — but doesn't reuse the new GAP-1/GAP-2 helpers | **MAJOR**, deduplicate with new code |
| GAP-14 | `/cpv-doctor`'s 14-option deep-diagnostic menu is invisible from `/cpv-main-menu` | UX | row 6 "Diagnose & Upgrade" only dispatches plugin-fixer/upgrade flow | new first-class menu row |
| GAP-15 | `validate_marketplace.py --strict` error message on `claude plugin install <name>@<mkpl>` "not found" failure should suggest running `cpv-doctor <marketplace>` | UX | no such guidance | doc + error-text update |
| GAP-16 | When marketplace entries diverge from upstream, the fix-validation skill should emit a **single auto-applicable patch** that re-aligns them (instead of one finding per drifted field) | UX | not implemented | new recipe `fix-marketplace-upstream-drift.md` |
| GAP-17 | Plugin-creator + plugin-fixer + plugin-upgrade agents MUST refuse to ship a marketplace.json that fails the new cross-validation (currently they happily produce drifted entries) | agent contract | no preflight | wire new check into agent pre-flight |

---

## 5. Phasing

### Phase A — schema-strict allowlist (closes GAP-3, GAP-11) — lowest risk

Mechanical edit to `scripts/validate_marketplace.py`:

```python
_KNOWN_MARKETPLACE_ENTRY_FIELDS: frozenset[str] = frozenset({
    "name",
    "description",
    "version",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "source",
    "category",
    "tags",
    "claude_versions",
    "platforms",
    # CPV-extension fields documented in references/marketplace-error-index.md:
    "alwaysLoad",
    "headersHelper",
})

_KNOWN_SOURCE_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    "github": frozenset({"source", "repo", "ref"}),
    "url":    frozenset({"source", "url"}),
    "git":    frozenset({"source", "url", "ref"}),
    "git-subdir": frozenset({"source", "url", "subdir", "ref"}),
    "npm":    frozenset({"source", "package"}),
    "relative-path": frozenset({"source", "path"}),
}


def _validate_known_fields(entry: dict, report: ValidationReport, entry_label: str) -> None:
    extra = set(entry.keys()) - _KNOWN_MARKETPLACE_ENTRY_FIELDS
    for field in sorted(extra):
        report.major(
            f"[RC-MKPL-UNKNOWN-FIELD] entry '{entry_label}' has unknown top-level "
            f"field '{field}'. Claude Code's marketplace spec does not define this "
            f"field — it will be ignored at install time and `claude plugin "
            f"validate` rejects the entry. If the intent was to express install "
            f"scope, move it to documentation; there is no marketplace-side "
            f"scope override in the current spec.",
            entry_label,
        )
    # Source sub-field check
    src = entry.get("source")
    if isinstance(src, dict):
        src_type = src.get("source")
        allowed = _KNOWN_SOURCE_FIELDS_BY_TYPE.get(src_type)
        if allowed is not None:
            src_extra = set(src.keys()) - allowed
            for field in sorted(src_extra):
                report.major(
                    f"[RC-MKPL-UNKNOWN-SOURCE-FIELD] entry '{entry_label}' source "
                    f"(type={src_type!r}) has unknown sub-field '{field}'.",
                    entry_label,
                )
```

**Tests** (new file `tests/test_validate_marketplace_strict_fields.py`):
- `test_unknown_scope_field_emits_major_with_fix_diff`
- `test_unknown_source_subfield_emits_major`
- `test_all_known_fields_accepted`
- `test_layout_b_nested_entries_also_strict`

### Phase B — upstream plugin.json cross-validation (closes GAP-1, GAP-2, GAP-7..10, GAP-12, GAP-13)

NEW module `scripts/cpv_upstream_plugin_json.py`:

```python
def fetch_upstream_plugin_json(
    entry: dict,
    *,
    cache_dir: Path = Path.home() / ".cache/cpv/plugin-json",
    ttl_seconds: int = 3600,
) -> dict | None:
    """Fetch the .claude-plugin/plugin.json from the marketplace entry's source.

    Supports source types: github, url, git, git-subdir, relative-path.
    Returns None if the source is unreachable (caller emits WARNING, not MAJOR).
    Cache key = sha256(source_dict).
    """
    ...

def diff_marketplace_vs_upstream(entry: dict, upstream: dict) -> list[FieldDrift]:
    """Compare ('name', 'version', 'description', 'author', 'keywords') between
    the marketplace entry and the upstream plugin.json. Returns a list of
    FieldDrift records ordered by severity:
      - name  → MAJOR (install breaks)
      - version → MINOR (display drift; suggest drop-the-field for source:url)
      - description / author / keywords → NIT
    """
    ...
```

Wire into `validate_marketplace.py`:

```python
if cross_validate_upstream:  # gated by CPV_SKIP_UPSTREAM_CROSS_CHECK=0
    upstream = fetch_upstream_plugin_json(entry, cache_dir=cache_dir)
    if upstream is None:
        report.warning(
            f"[RC-MKPL-UPSTREAM-UNREACHABLE] could not fetch upstream "
            f"plugin.json for entry '{entry['name']}' (source type "
            f"{entry['source']['source']!r}); cross-validation skipped.",
            entry_label,
        )
    else:
        for drift in diff_marketplace_vs_upstream(entry, upstream):
            severity_map[drift.severity](drift.message, entry_label)
```

**Cache strategy**:
- Use existing `cpv_network_resilience.run_with_retry` for the fetch
- Cache at `~/.cache/cpv/plugin-json/<sha256(source_dict)>.json` + `.meta` sidecar
- 1-hour TTL by default; override via `CPV_PLUGIN_JSON_TTL_SECONDS=<N>`
- Skip entire cross-check via `CPV_SKIP_UPSTREAM_CROSS_CHECK=1` (air-gapped CI)
- Skip per-entry via setting `"_cpv_skip_upstream_check": true` on the entry

**Tests** (new file `tests/test_marketplace_upstream_cross_validation.py`):
- `test_name_mismatch_emits_major_with_diff` — repros the `ai-maestro-visual-communicator` incident
- `test_version_drift_emits_minor_with_drop_suggestion`
- `test_description_drift_emits_nit`
- `test_unreachable_source_emits_warning_not_major` — air-gapped/network-flaky CI
- `test_layout_c_self_entry_uses_same_helper` — deduplicate with existing v2.32.0 code path
- `test_cache_hit_does_not_refetch` — 1-hr TTL behavior
- `test_skip_env_var_bypasses_check`
- `test_per_entry_opt_out_via_underscore_field`

### Phase C — source reachability probe (closes GAP-4, GAP-5, GAP-6) — opt-in

Behind `--probe-sources` flag (off by default; turned on by `cpv-doctor`):

```python
def probe_source_reachable(source: dict) -> ProbeResult:
    """Best-effort reachability check.
    - github → `gh api repos/<repo>` (HEAD-equivalent)
    - url → `git ls-remote --heads` (HEAD-equivalent for git over https)
    - git-subdir → `git ls-remote` + `git archive --remote=… HEAD <subdir>/.claude-plugin/plugin.json`
    - relative-path → file existence
    """
    ...
```

**Tests**:
- `test_github_404_emits_major`
- `test_url_unreachable_emits_warning`
- `test_git_subdir_missing_path_emits_major`

### Phase D — fix-validation recipes (closes GAP-16)

NEW reference `skills/fix-validation/references/marketplace-upstream-drift.md` with sections:

1. **Name mismatch** — `RC-MKPL-NAME-MISMATCH` → patch shape
2. **Version drift** — `RC-MKPL-VERSION-DRIFT` → either bump-or-drop choice tree
3. **Unknown entry field** — `RC-MKPL-UNKNOWN-FIELD` → drop-field patch + UX-intent migration guidance (e.g. `scope` → install-time `--scope` doc)
4. **Unknown source sub-field** — `RC-MKPL-UNKNOWN-SOURCE-FIELD` → drop or rename
5. **Source unreachable** — `RC-MKPL-UPSTREAM-UNREACHABLE` → diagnostic-only
6. **Description / author / keywords drift** — `RC-MKPL-METADATA-DRIFT` → auto-align patch
7. **Per-batch bulk align** — when N entries in the same marketplace.json have drift, emit ONE consolidated patch rather than N findings

Add `plugin-fixer` skill loader pointer in `agents/plugin-fixer.md`:
```yaml
skills:
  - fix-validation
  - cache-fixes
  - telemetry-hazard-fixes
  - marketplace-upstream-drift   # NEW (Phase D)
```

### Phase E — main-menu doctor integration (closes GAP-14, GAP-15)

`agents/cpv-main-menu-agent.md` — insert a new row 7 in the top-level table:

```
│ 6  │ Diagnose & Upgrade          │ Deep audit + upgrade plugin to current pipeline standards  │
│ 7  │ Doctor (deep diagnostic)    │ 14-option doctor menu: marketplace cross-check,           │
│    │                             │ install-resolver dry-run, scope mismatches, etc.          │
│ 8  │ GitHub setup                │ ... (was 7)                                                │
│ 9  │ Deep semantic analysis      │ ... (was 8)                                                │
│ 0  │ Cancel / Exit               │                                                            │
│ A  │ Ask the agent               │                                                            │
```

`skills/cpv-main-menu-skill/references/menu-tree.md` §3.7 (new) — replicate the 14-option doctor menu the user encountered.

`scripts/validate_marketplace.py` error text for install-resolution failures — add a one-line hint:
> `Run \`/cpv-doctor <marketplace-path>\` to surface name/version/field-name drift before this becomes user-facing.`

### Phase F — agent pre-flight enforcement (closes GAP-17)

`agents/plugin-creator.md`, `agents/plugin-fixer.md`, `commands/cpv-upgrade-plugin.md` — add a pre-completion gate:

```
BEFORE declaring the plugin migrated/created/fixed:
1. Run validate_marketplace.py --strict --cross-validate-upstream <marketplace-path>
2. Exit code MUST be 0 (no CRITICAL/MAJOR/MINOR)
3. If the agent is operating on a Layout A plugin AND has touched a sibling
   marketplace repo, cross-check both AND emit a unified summary.
```

Add a new architectural test `tests/test_agent_marketplace_preflight.py`:
- `test_plugin_creator_runs_marketplace_cross_check_before_declaring_done`
- `test_plugin_fixer_blocks_completion_on_unresolved_marketplace_drift`

---

## 6. Critical files

| Path | Phase | Action |
|---|---|---|
| `scripts/validate_marketplace.py` | A, B | add field allowlist + cross-validation hookup |
| `scripts/cpv_upstream_plugin_json.py` | B | NEW: fetcher + cache + diff |
| `scripts/cpv_network_resilience.py` | B | reuse (no change unless retry params need tweak) |
| `scripts/cpv_integrity.py` | B | reuse cache-key sha256 pattern |
| `skills/fix-validation/references/marketplace-upstream-drift.md` | D | NEW |
| `skills/fix-validation/SKILL.md` | D | TOC update (add entry — keep 100% TOC parity per the v2.80.0 SKILL.md rule) |
| `skills/fix-validation/references/plugin-error-index.md` | D | add `## 20. validate_marketplace cross-validation rules` heading + update SKILL.md TOC |
| `skills/fix-validation/references/marketplace-error-index.md` | D | append RC-MKPL-* row to the marketplace-error-index |
| `agents/cpv-main-menu-agent.md` | E | insert row 7 |
| `skills/cpv-main-menu-skill/references/menu-tree.md` | E | new §3.7 doctor sub-menu |
| `agents/plugin-creator.md` | F | pre-completion gate |
| `agents/plugin-fixer.md` | D, F | add skill pointer + pre-completion gate |
| `commands/cpv-upgrade-plugin.md` | F | pre-completion gate |
| `commands/cpv-doctor.md` | E | error-text hint |
| `tests/test_validate_marketplace_strict_fields.py` | A | NEW |
| `tests/test_marketplace_upstream_cross_validation.py` | B | NEW |
| `tests/test_marketplace_source_reachability.py` | C | NEW (slow-marked 🐌 for network) |
| `tests/test_agent_marketplace_preflight.py` | F | NEW |
| `CHANGELOG.md` | all | one entry per phase |

---

## 7. Cross-cutting requirements

1. **Network resilience** — every fetch uses `run_with_retry` from `cpv_network_resilience.py`. The same Go-net error patterns (`i/o timeout`, `context deadline exceeded`, `dial tcp ... timeout`, `no such host`) apply.
2. **Air-gapped CI escape hatch** — `CPV_SKIP_UPSTREAM_CROSS_CHECK=1` skips Phase B/C entirely. Document in pyproject.toml comment block + add to `references/empirical-loading-bugs.md`.
3. **Per-entry opt-out** — `"_cpv_skip_upstream_check": true` (leading underscore signals private/non-spec) on a marketplace entry skips cross-check for that entry only. Tests must cover.
4. **Per-marketplace opt-out** — file `.claude-plugin/.cpv-no-upstream-check` (zero-byte sentinel) skips cross-check for an entire marketplace. Useful for the dogfood case where CPV's own marketplace lists CPV.
5. **Cache invariants** — same atomicity rules as the integrity cache (`tmp+rename`, sha256 keying, sidecar `.meta` with timestamp + scanner version). Cache key includes both the source dict AND a `CPV_PLUGIN_JSON_FETCHER_VERSION` constant so a fetcher bump invalidates all old entries.
6. **Performance** — Phase B's fetch is bounded by N (number of marketplace entries) × per-fetch latency. Bulk-marketplace cases (Layout B with 30+ plugins) must NOT serialize → reuse the `ThreadPoolExecutor(max_workers=8)` pattern shipped in v2.76.0 Phase B parallelization.
7. **Output ordering** — findings appear in canonical (alphabetical) entry order regardless of completion order of parallel fetches.
8. **Bypass-var rejection** — `CPV_SKIP_UPSTREAM_CROSS_CHECK` is acceptable for opt-in air-gapped CI but Gate 0 of `publish.py` must REJECT it (a release must not ship without the cross-check).
9. **TRDD self-application** — `claude-plugins-validation`'s own marketplace listing (in the `emasoft-plugins` marketplace) MUST pass the new cross-check. Verify before commit.

---

## 8. Acceptance criteria

The TRDD is **Done** when ALL of the following hold:

- [ ] `scripts/cpv_upstream_plugin_json.py` ships with fetcher + cache + diff helpers
- [ ] `scripts/validate_marketplace.py` has the allowlist enforcement (Phase A) AND the cross-validation hookup (Phase B)
- [ ] All 8 new test files ship (A: 4, B: 8, C: 3, F: 2) and pass
- [ ] Full suite passes `uv run pytest tests/ -n auto --dist=worksteal --maxfail=1 -q`
- [ ] `skills/fix-validation/references/marketplace-upstream-drift.md` ships with 7 recipes
- [ ] `SKILL.md` TOC parity (per the v2.80.0 SKILL-TOC rule) — every new heading replicated verbatim
- [ ] `agents/cpv-main-menu-agent.md` has row 7 "Doctor (deep diagnostic)" + sub-menu §3.7 in menu-tree.md
- [ ] `agents/plugin-creator.md` + `agents/plugin-fixer.md` + `commands/cpv-upgrade-plugin.md` have the pre-completion gate
- [ ] `validate_plugin.py . --strict` against the CPV plugin itself exits 0 with the new checks active
- [ ] `validate_marketplace.py --strict --cross-validate-upstream` against `emasoft-plugins` marketplace exits 0
- [ ] Manual repro of the original `ai-maestro-visual-communicator-plugin` incident produces THREE findings (name MAJOR, version MINOR, scope MAJOR) BEFORE publish, not after
- [ ] CHANGELOG.md updated under v2.81.0 (or next minor)
- [ ] This TRDD's `**Status:**` line set to `Done (YYYY-MM-DD)` with phase tags

---

## 9. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Cross-validation fetches add 2-5s to every marketplace validate run | Cache with 1-hr TTL; ThreadPoolExecutor for parallel fetch; skip for layout-A/B sources of type `relative-path` (already local) |
| GitHub rate-limiting during bulk validates | Use authenticated `gh api` calls (already standard in CPV); fall back to anonymous with WARNING |
| Source-probe flakes on transient network → false-positive MAJORs | Probe is **opt-in** (Phase C `--probe-sources` flag); cross-validate is **opt-out** but downgrades to WARNING on unreachable |
| Air-gapped CI users get spurious WARNINGs | `CPV_SKIP_UPSTREAM_CROSS_CHECK=1` + `.cpv-no-upstream-check` sentinel; documented |
| Existing v2.32.0 Layout C `validate_layout_c_consistency` overlaps with new GAP-1/2 helpers | Phase B refactor — Layout C calls the same `diff_marketplace_vs_upstream` helper; deduplicate at merge |
| Plugin-shipped marketplace.json with intentional name-prefix drift (e.g. brand-name vs canonical-name) | Per-entry `"_cpv_skip_upstream_check": true` opt-out + document the policy |
| Migration agent (cpv-upgrade-plugin) shipping a Layout-C plugin where the marketplace.json *already* has divergent name+version that the user wants to keep | Pre-completion gate (Phase F) MUST distinguish "user-blessed drift" (per-entry opt-out present) vs "agent-introduced drift" (no opt-out) — refuse the latter, allow the former |

---

## 10. Out of scope

- Rewriting plugin install resolver inside Claude Code itself (that's Anthropic's responsibility)
- Auto-fixing the upstream plugin.json on behalf of the user (only the marketplace.json is auto-fixable; upstream changes need a PR to the plugin repo)
- Semantic marketplace categorization checks (out of scope — that's the semantic-validator's domain)
- A registry of "blessed" name-prefix divergences (would require a registry; not justified yet)

---

## 11. Implementation note for future agent

1. **Phase A first** (smallest blast radius — pure schema-strict check). Ship + bake one release before adding network-touching Phase B.
2. **Phase B uses Phase D recipes** — recipes describe the patches the validator points at. Write the recipes BEFORE the validator emits them, or the agent will say "see fix-validation:foo" and the file won't exist.
3. **Phase C is opt-in** — do NOT enable source-probe by default. It's a foot-gun on flaky networks.
4. **Phase D recipes must follow the SKILL.md TOC parity contract** (every new heading in `plugin-error-index.md` and `marketplace-error-index.md` MUST be mirrored verbatim into `SKILL.md`'s TOC envelope — the v2.80.0 fix for this exact issue is the precedent).
5. **Phase E + F can ship in the same PR** — they're independent of Phase B but together close the agent-side prevention loop.
6. **Self-apply the new checks** — CPV's own marketplace listing in `emasoft-plugins` MUST pass before merging. Acceptance criterion #11 enforces this.
7. **Spawn one opus agent per Phase** in worktrees forked from current master (`ea8dacc` or later) so they merge cleanly. Avoid touching the same files between Phase B and Phase D (Phase B touches `validate_marketplace.py`; Phase D touches skills only).
8. **After all phases ship**: run a full re-validation against the `ai-maestro-visual-communicator-plugin` incident scenario as a regression test. Confirm three findings appear.

---

## 12. References

- User report: 2026-05-11 conversation turn (§1 verbatim)
- v2.32.0 Layout C self-cross-check — `validate_layout_c_consistency` in `validate_marketplace.py` (precedent for Phase B helper extraction)
- v2.76.0 ThreadPoolExecutor pattern — reused in Phase B for bulk fetches
- v2.78.0 ScannerCache content-hash pattern — reused in Phase B for plugin-json cache
- v2.80.0 SKILL.md TOC parity fix — precedent for Phase D recipe placement
- v2.80.1 RC-WORKFLOW-PATH-BROKEN narrow-exclusion fix — precedent for "narrow startsWith check missing mid-token vars" class of bug; cross-reference in case future audits surface similar shapes in marketplace cross-validation
