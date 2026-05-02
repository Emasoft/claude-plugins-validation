# CPV Main-Menu Tree (numbered-table edition)

## Table of Contents

- [Shell prologue](#shell-prologue)
- [Table-rendering rules](#table-rendering-rules)
- [Menu definitions](#menu-definitions)
- [Etiquette and error handling](#etiquette-and-error-handling)

## Shell prologue

Every leaf that produces a report uses this shell prologue:

```bash
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  MAIN_ROOT="$(git worktree list | head -n1 | awk '{print $1}')"
else
  MAIN_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
fi
TS="$(date +%Y%m%d_%H%M%S%z)"
SLUG="$(basename "$TARGET_PATH")"
LAUNCHER="${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py"
mkdir -p "$MAIN_ROOT/reports/<component>"
REPORT_FILE="$MAIN_ROOT/reports/<component>/$TS-$SLUG.md"
```

## Table-rendering rules

Every menu is rendered as a Unicode box-drawing table. The user picks an
option by typing the number in their next message. NEVER use
`AskUserQuestion` for menu navigation.

### Canonical layout

- **Header row** uses heavy box-drawing characters (`┏━┳━┓` / `┡━╇━┩`).
- **Data rows** use light characters (`│ │ │` / `├─┼─┤`).
- **Footer** is a single line below the table: `Type a number to choose:`.
- **Cancel / Exit** is ALWAYS the LAST row, numbered `0`.
- **Back** (sub-menus only) is the second-to-last row, numbered `9` (or `B` if `9` is taken — but `0` for cancel and `9` for back is the canonical pair).
- Column widths fit the longest entry; pad with spaces.
- Three columns standard: `#` (1-2 chars) / `Option` / `What it does`. Add a 4th column for `Pros / Cons / Cost / Risk` only when it adds value (e.g. semantic-validation cost warning).
- Use full-width separators where the table is wider than 80 chars.

### Reference template (paste into the agent's output verbatim, then customize)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Option               ┃ What it does                                           ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ <option name>        │ <one-line description>                                 │
│ 2 │ <option name>        │ <one-line description>                                 │
│ … │                      │                                                        │
│ 9 │ Back                 │ Return to the previous menu                            │
│ 0 │ Cancel / Exit        │ Terminate without action                               │
└───┴──────────────────────┴────────────────────────────────────────────────────────┘
Type a number to choose:
```

For top-level menus (no parent), drop the `Back` row.

## Menu definitions

### 3.0 Top-level menu (8 categories + Cancel)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Category                ┃ What it does                                                          ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Validate                │ Run a CPV validator (plugin/skill/cache/marketplace/scope/component)  │
│ 2 │ Validate from GitHub    │ Clone owner/repo to tmpdir, scan, clean up                            │
│ 3 │ Fix                     │ Apply mechanical fixes from a validation report                       │
│ 4 │ Create                  │ Scaffold a new plugin or marketplace from scratch                     │
│ 5 │ Manage                  │ List, install, doctor, install scanners, bump version                 │
│ 6 │ GitHub setup            │ Branch protection rules, link plugin to marketplace                   │
│ 7 │ Deep semantic analysis  │ Opus A-F grading (expensive — confirms cost first)                    │
│ 8 │ Help / About            │ Category overview, command list, CPV version                          │
│ 0 │ Cancel / Exit           │ Terminate without action                                              │
└───┴─────────────────────────┴───────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

---

### Post-validate flow (applies to every leaf in §3.1 and §3.2)

After a leaf in §3.1 or §3.2 finishes and the report is on disk, the
orchestrator MUST print the §3.10 post-validate fix menu (NEVER the
generic §3.9 "do something else" table). This is non-negotiable: the user
always gets the explicit "fix N or end" choice after a validation, never
just "what's next?".

### 3.1 Validate sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Validator                   ┃ What it does                                                      ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Plugin                      │ Full plugin validation (190+ rules, all 17 sub-validators)        │
│ 2 │ Skill                       │ Single SKILL.md (frontmatter + structure + 190+ rules)            │
│ 3 │ Cache                       │ Prompt-cache invalidation patterns CA-01..CA-06                   │
│ 4 │ Marketplace settings inline │ extraKnownMarketplaces block in settings.json                     │
│ 5 │ Local scope                 │ Non-git-tracked .claude/ (settings.local.json, gitignored elts)   │
│ 6 │ Project scope               │ Git-tracked .claude/ (settings.json, agents/skills/commands)      │
│ 7 │ Component                   │ Specific element (hook/mcp/agent/command/security/encoding/etc.)  │
│ 9 │ Back                        │ Return to top-level menu                                          │
│ 0 │ Cancel / Exit               │ Terminate without action                                          │
└───┴─────────────────────────────┴───────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.1.1 Plugin

- **arg-prompt**: `Path to the plugin to validate? (e.g. ~/Code/my-plugin/ or just the plugin name for auto-discovery)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" plugin "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_plugin/$TS-$SLUG.md"
  ```

#### 3.1.2 Skill

- **arg-prompt**: `Path to the skill directory? (e.g. ./skills/my-skill/)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" skill "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_skill/$TS-$SLUG.md"
  ```

#### 3.1.3 Cache (CA-01..CA-06)

- **arg-prompt**: `Path to plugin OR project root? (cache audit works on any directory with CLAUDE.md / .claude/)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" cache "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_cache/$TS-$SLUG.md"
  ```

#### 3.1.4 Marketplace settings inline

- **arg-prompt**: `Path to settings.json containing the inline marketplaces block?`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" settings-marketplace "$TARGET_PATH" --strict --report "$MAIN_ROOT/reports/validate_settings_marketplace/$TS-$SLUG.md"
  ```

#### 3.1.5 Local scope (.claude/local)

- **arg-prompt**: `Path to the project root? (validates non-git-tracked elements: settings.local.json, gitignored agents/skills/commands)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" local-scope "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_local_scope/$TS-$SLUG.md"
  ```

#### 3.1.6 Project scope (.claude/git-tracked)

- **arg-prompt**: `Path to the project root? (validates git-tracked elements: settings.json, agents/skills/commands)`
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" project-scope "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```

#### 3.1.7 Component

- **arg-prompts** (two questions, in order):
  1. `Which component? (hook / mcp / agent / command / security / encoding / rules / xref / docs / enterprise / scoring / lsp)`
  2. `Path to the plugin?`
- **execution**: same launcher pattern with the chosen alias.

---

### 3.2 Validate from GitHub sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Source                    ┃ What it does                                                      ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Plugin from GitHub        │ Clone owner/repo, validate plugin, clean up                       │
│ 2 │ Marketplace from GitHub   │ Clone owner/repo, validate marketplace, clean up                  │
│ 9 │ Back                      │ Return to top-level menu                                          │
│ 0 │ Cancel / Exit             │ Terminate without action                                          │
└───┴───────────────────────────┴───────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.2.1 Plugin from GitHub

- **arg-prompts** (in order):
  1. `GitHub spec? (owner/repo or https://github.com/owner/repo)`
  2. `Run security --audit too? (yes/no)`
- **execution** (without audit):
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --plugin "$REPO" --report "$MAIN_ROOT/reports/validate_github_plugin/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```
- **execution** (with audit): append `--audit` before `--report`.
- **fallback** (security-only direct URL ingestion, v2.48+):
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" security "https://github.com/$REPO" --report "$REPORT_FILE"
  ```

#### 3.2.2 Marketplace from GitHub

- **arg-prompts**: same as 3.2.1
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --marketplace "$REPO" --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

---

### 3.3 Fix sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                ┃ What it does                                                       ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix plugin findings      │ From a report file OR a plugin path (uses plugin-fixer agent)      │
│ 2 │ Fix marketplace findings │ From a report OR marketplace path (uses marketplace-fixer agent)   │
│ 3 │ Cache optimize           │ Audit + fix loop for CA-01..CA-06 (uses cache-optimizer-agent)     │
│ 9 │ Back                     │ Return to top-level menu                                           │
│ 0 │ Cancel / Exit            │ Terminate without action                                           │
└───┴──────────────────────────┴────────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.3.1 Fix plugin findings

- **arg-prompt**: `Path to a validation report .md file OR a plugin directory?`
- **execution**: dispatch the **plugin-fixer agent** with the path. The agent owns the validate→fix→re-validate loop.

#### 3.3.2 Fix marketplace findings

- **arg-prompt**: `Path to a marketplace validation report OR a marketplace directory?`
- **execution**: dispatch the **marketplace-fixer agent**. Handles mechanical fixes AND architectural migrations (Layout A↔B↔C).

#### 3.3.3 Cache optimize

- **arg-prompts** (in order):
  1. `Path to plugin or project root?`
  2. `Also do broader cache-aware refactoring? (yes/no — --broader invokes Phase 4)`
- **execution**: dispatch the **cache-optimizer-agent** with the path and `--broader` flag if requested.

---

### 3.4 Create sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Scaffold                    ┃ What it does                                                     ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Scaffold a new plugin       │ Generate full plugin repo (uses plugin-creator agent)            │
│ 2 │ Scaffold a new marketplace  │ Generate marketplace hub (uses plugin-creator agent)             │
│ 9 │ Back                        │ Return to top-level menu                                         │
│ 0 │ Cancel / Exit               │ Terminate without action                                         │
└───┴─────────────────────────────┴──────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.4.1 Scaffold a new plugin

- **arg-prompts** (in order):
  1. `Plugin name?`
  2. `Target directory?`
  3. `Layout (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin self-referential)?`
- **execution**: dispatch the **plugin-creator agent** with the answers.

#### 3.4.2 Scaffold a new marketplace

- **arg-prompts** (in order):
  1. `Marketplace name?`
  2. `Target directory?`
  3. `Owner GitHub username?`
- **execution**: dispatch the **plugin-creator agent** in marketplace mode.

---

### 3.5 Manage sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                       ┃ What it does                                                   ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ List installed plugins          │ Show every plugin Claude Code knows about                      │
│ 2 │ Install / update / enable / dis │ Dispatches plugin-manager agent (its First Contact menu picks) │
│ 3 │ Doctor (health check)           │ Probe registry, settings, cache for orphans                    │
│ 4 │ Install all external scanners   │ Batch-install cc-audit/tirith/trufflehog/semgrep/Cisco/fclones │
│ 5 │ Prune old plugin cache versions │ Free disk space — keep active version, delete older            │
│ 6 │ Bump version (current plugin)   │ patch / minor / major (uses bump_version.py)                   │
│ 7 │ Show CPV version                │ Read .claude-plugin/plugin.json                                │
│ 9 │ Back                            │ Return to top-level menu                                       │
│ 0 │ Cancel / Exit                   │ Terminate without action                                       │
└───┴─────────────────────────────────┴────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.5.1 List installed plugins

- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" registry --list
  ```

#### 3.5.2 Install / update / enable / disable

- **execution**: dispatch the **plugin-manager agent**. The agent's First Contact menu (also a Unicode table) asks what operation to do.

#### 3.5.3 Doctor (health check)

- **arg-prompts** (in order):
  1. `Verbose output? (yes/no)`
  2. `Auto-fix orphaned entries? (yes/no — passes --fix)`
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" doctor [--verbose] [--fix]
  ```

#### 3.5.4 Install all external scanners

- **arg-prompt**: `This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, AND fclones via brew/snap/pipx/cargo (silent, idempotent, per-platform). Proceed? (yes/no)`
- **execution**: ALWAYS confirm first, then:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners
  ```
- **note**: This is the ONLY direct invocation of `manage_doctor.py` for this leaf — `--install-scanners` is a one-shot bootstrap that doesn't need the launcher's environment isolation.

#### 3.5.5 Prune old plugin cache versions (v2.48)

- **arg-prompts** (in order):
  1. `First show DRY-RUN preview? (yes/no — recommended yes)`
  2. `Keep how many newest versions per plugin? (default: 1)`
  3. After dry-run preview prints: `Proceed with actual deletion? (yes/no)`
- **execution** (dry-run preview):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-dry-run --prune-keep $KEEP_N
  ```
- **execution** (actual delete):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-old-versions --prune-keep $KEEP_N
  ```
- **note**: The active version (whichever Claude Code's `enabledPlugins` references) is always kept, even if older than another cached version.

#### 3.5.6 Bump version (current plugin)

- **arg-prompt**: `Bump type? (patch / minor / major)`
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --$BUMP_TYPE
  ```

#### 3.5.7 Show CPV version

- **execution**:
  ```bash
  cat "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  ```

---

### 3.6 GitHub setup sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                            ┃ What it does                                              ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Branch protection (current repo)     │ Apply rules to the repo of `git remote get-url origin`    │
│ 2 │ Branch protection (generic owner/rp) │ Apply rules to a different owner/repo                     │
│ 3 │ Link plugin to a marketplace         │ Add the plugin to a marketplace's plugin list             │
│ 9 │ Back                                 │ Return to top-level menu                                  │
│ 0 │ Cancel / Exit                        │ Terminate without action                                  │
└───┴──────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.6.1 Branch protection (current repo)

- **execution**: invokes `/cpv-setup-branch-rules` workflow inline (no extra prompts — uses the current `git remote get-url origin`).

#### 3.6.2 Branch protection (generic owner/repo)

- **arg-prompt**: `Owner/repo slug?`
- **execution**: invokes `/cpv-setup-branch-rules-generic` workflow inline with the slug.

#### 3.6.3 Link plugin to a marketplace

- **arg-prompts** (in order):
  1. `Plugin repo (owner/repo)?`
  2. `Marketplace repo (owner/repo)?`
- **execution**: invokes `/cpv-link-plugin` workflow inline with the answers.

---

### 3.7 Deep semantic analysis (opus, EXPENSIVE)

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Operation                  ┃ What it does                                                     ┃ Cost                    ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Confirm + run on a path    │ Opus A-F semantic grading of a skill / agent / whole plugin      │ 10-50× normal scan cost │
│ 9 │ Back                       │ Return to top-level menu                                         │ —                       │
│ 0 │ Cancel / Exit              │ Terminate without action                                         │ —                       │
└───┴────────────────────────────┴──────────────────────────────────────────────────────────────────┴─────────────────────────┘
Type a number to choose:
```

#### 3.7.1 Confirm + run

- **arg-prompts** (in order):
  1. `Semantic validation uses Opus with 1M context at max effort. Cost: ~10-50× normal. Proceed? (yes/no)`
  2. (only if yes) `Path to skill or agent or whole plugin?`
- **execution**: dispatch the **semantic-validator agent** with the path. The agent itself runs the syntactic baseline first then the semantic pass.

---

### 3.8 Help / About sub-menu

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Help topic                          ┃ What it shows                                             ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Category overview                   │ Re-print the 8-row top-level table                        │
│ 2 │ List every CPV command              │ Walk commands/cpv-*.md and print name + description       │
│ 3 │ Show CPV plugin version             │ Read .claude-plugin/plugin.json                           │
│ 9 │ Back                                │ Return to top-level menu                                  │
│ 0 │ Cancel / Exit                       │ Terminate without action                                  │
└───┴─────────────────────────────────────┴───────────────────────────────────────────────────────────┘
Type a number to choose:
```

#### 3.8.1 Category overview

- **execution**: re-print the 3.0 top-level menu table.

#### 3.8.2 List every CPV command

- **execution**:
  ```bash
  for f in "${CLAUDE_PLUGIN_ROOT}"/commands/cpv-*.md; do
    name=$(basename "$f" .md)
    desc=$(awk '/^description:/{sub(/^description:[[:space:]]*/, ""); print; exit}' "$f")
    printf "%-42s %s\n" "/$name" "$desc"
  done
  ```

#### 3.8.3 Show CPV plugin version

- **execution**: same as 3.5.7.

---

### 3.9 End-of-leaf "do something else?" table (NON-validate flows)

After a Create / Manage / GitHub-setup / Help leaf finishes, print this 2-row
table and wait for the user's number:

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Next                        ┃ What it does                                                   ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Do something else           │ Return to top-level menu                                       │
│ 0 │ Done (exit)                 │ Reply `Done.` and stop                                         │
└───┴─────────────────────────────┴────────────────────────────────────────────────────────────────┘
Type a number to choose:
```

---

### 3.10 Post-validate fix menu (MANDATORY after every Validate / Validate-from-GitHub leaf)

This table replaces §3.9 for ALL validate flows. It MUST be printed
unconditionally after a validate leaf finishes — even when the validation
verdict is PASS / VALID — so the user always has the explicit option to
end OR to fix any residual WARNINGs they care about.

If the validation finished completely clean (CRITICAL=0 MAJOR=0 MINOR=0
NIT=0 WARNING=0), still print the table. Rows 1-5 will simply find
nothing to fix when dispatched, and the fixer will exit clean — but the
user always sees the menu and is never auto-deflected.

```
┏━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ Action                                          ┃ What it does                                                          ┃ Severities the fixer will touch ┃
┡━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │ Fix ALL issues (incl. WARNING)                  │ Dispatch the cpv-fixer agent on every finding in the report           │ CRITICAL+MAJOR+MINOR+NIT+WARNING │
│ 2 │ Fix NIT and higher                              │ Skip WARNING-only findings                                            │ CRITICAL+MAJOR+MINOR+NIT         │
│ 3 │ Fix MINOR and higher                            │ Skip NIT and WARNING                                                  │ CRITICAL+MAJOR+MINOR             │
│ 4 │ Fix MAJOR and higher                            │ Only fix the publish-blockers (and CRITICALs)                         │ CRITICAL+MAJOR                   │
│ 5 │ Fix CRITICAL only                               │ Strictest mode — fix the loaders/security blockers and nothing else   │ CRITICAL                         │
│ 0 │ End                                             │ Done — exit without running the fixer                                 │ —                                │
└───┴─────────────────────────────────────────────────┴───────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
Type a number to choose:
```

#### 3.10.1 Dispatching the fixer with a minimum severity

When the user picks rows 1-5, dispatch the **plugin-fixer agent** (or, for
marketplace reports, the **marketplace-fixer agent**; for cache reports,
the **cache-optimizer-agent**) with the report path and a `min_severity`
parameter. The agent honours the filter by skipping fixes for any finding
whose severity is BELOW the threshold.

| Row | `min_severity` value to pass | Agent prompt template |
|-----|-------------------------------|----------------------|
| 1 | `WARNING` | `Fix every finding in <REPORT_PATH>. min_severity=WARNING (fix everything including WARNINGs).` |
| 2 | `NIT` | `Fix findings in <REPORT_PATH>. min_severity=NIT (skip WARNING-only).` |
| 3 | `MINOR` | `Fix findings in <REPORT_PATH>. min_severity=MINOR (skip NIT and WARNING).` |
| 4 | `MAJOR` | `Fix findings in <REPORT_PATH>. min_severity=MAJOR (publish-blockers only).` |
| 5 | `CRITICAL` | `Fix findings in <REPORT_PATH>. min_severity=CRITICAL (strictest — only loader/security blockers).` |

After the fixer agent returns, print the §3.9 "do something else?" 2-row
table (Return to top-level / Done) and wait.

If the user picks `0` (End) → reply `Done.` and stop.

---

## Etiquette and error handling

### Cancel / Exit semantics

At ANY menu level, picking `0` (Cancel / Exit) → the orchestrator MUST:

1. Stop all further menu prompts.
2. Reply with exactly ONE line: `Cancelled — no actions taken.`
3. Not run any bash, not write any reports, not modify any files.

### Back semantics

In a sub-menu, picking `9` (Back) → re-print the PARENT menu's table
(typically 3.0 top-level). At the top-level menu there is no `9` row.

### Argument-prompt etiquette

- ALWAYS ask required arguments as a single plain-text line — NEVER use AskUserQuestion.
- Example: `Path to the plugin to validate? (e.g. ~/Code/my-plugin/)`
- If the user provides an invalid path → re-ask with a hint, do not abort.
- If the user replies `0` or `cancel` or `exit` at the argument prompt → treat the same as a top-level Cancel.
- For paths, ALWAYS resolve `~` to `$HOME` and expand environment variables before invoking bash.

### Number-parsing rules

- Strip surrounding whitespace from the user's reply.
- Take the FIRST integer found in the reply (so `1` and `1.` and `1)` all match row 1).
- If the user types text not starting with a digit but matching an option name (case-insensitive substring match on the `Option` column), accept it.
- Otherwise: print `Invalid choice. Pick a number from the table.` and re-print the SAME table (do not jump back to top-level).

### Error handling

- If `${CLAUDE_PLUGIN_ROOT}` is unset → abort with:
  > "CPV plugin not installed in this session. Install via
  > `/plugin install claude-plugins-validation@emasoft-plugins`."
- If a launcher invocation exits non-zero → surface stderr verbatim, then re-print the SAME sub-menu table so the user can retry with different arguments.

### Token budget

- Never paste a full report into the response. Always return the report-file path and a 3-line summary (verdict + counts + path).
- Do not load `references/menu-tree.md` repeatedly — the orchestrator reads it once at session start.
- Use the launcher invocation table (above) verbatim — do not generate alternative bash spellings.
