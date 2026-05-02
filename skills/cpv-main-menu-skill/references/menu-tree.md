# CPV Main-Menu Tree (per-leaf recipes)

## Table of Contents

- [Shell prologue](#shell-prologue)
- [Menu definitions](#menu-definitions)
- [Etiquette and error handling](#etiquette-and-error-handling)

## Shell prologue

Full menu definition. Each leaf has these three keys (arg-prompts /
execution / fallback). Every menu and sub-menu MUST include a Cancel /
Exit option; every sub-menu MUST also include a Back option that
returns to the parent menu.

Every leaf that produces a report uses this prologue:

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

### Top-level menu (8 categories + Cancel)

`AskUserQuestion` options:
1. **Validate** — Run a CPV validator (plugin/skill/cache/marketplace/scope/component)
2. **Validate from GitHub** — Clone owner/repo to tmpdir, scan, clean up
3. **Fix** — Apply mechanical fixes from a validation report
4. **Create** — Scaffold a new plugin or marketplace from scratch
5. **Manage** — List, install, doctor, install scanners, bump version
6. **GitHub setup** — Branch protection rules, link plugin to marketplace
7. **Deep semantic analysis** — Opus A-F grading (expensive — confirms cost first)
8. **Help / About** — Category overview, command list, CPV version
9. **Cancel / Exit** — Terminate without action

---

## Menu definitions

### 1. Validate

`AskUserQuestion` options:
1. **Plugin** — Full plugin validation (190+ rules, all 17 sub-validators)
2. **Skill** — Single SKILL.md (frontmatter + structure + 190+ rules)
3. **Cache** — Prompt-cache invalidation patterns CA-01..CA-06
4. **Marketplace settings inline** — `extraKnownMarketplaces` block in settings.json
5. **Local scope** — non-git-tracked `.claude/` (settings.local.json, gitignored elements)
6. **Project scope** — git-tracked `.claude/` (settings.json, agents/skills/commands)
7. **Component** — Specific element (hook, MCP, agent, command, security, encoding…)
8. **Back** — Return to top-level menu
9. **Cancel / Exit** — Terminate

### 1.1 Plugin

- **arg-prompts**: "What is the path to the plugin to validate? (e.g. `~/Code/my-plugin/` or just the plugin name for auto-discovery)"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" plugin "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_plugin/$TS-$SLUG.md"
  ```

### 1.2 Skill

- **arg-prompts**: "Path to the skill directory? (e.g. `./skills/my-skill/`)"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" skill "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_skill/$TS-$SLUG.md"
  ```

### 1.3 Cache (CA-01..CA-06)

- **arg-prompts**: "Path to plugin OR project root? (cache audit works on any directory with CLAUDE.md / .claude/)"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" cache "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_cache/$TS-$SLUG.md"
  ```

### 1.4 Marketplace settings inline

- **arg-prompts**: "Path to settings.json containing the inline marketplaces block?"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" settings-marketplace "$TARGET_PATH" --strict --report "$MAIN_ROOT/reports/validate_settings_marketplace/$TS-$SLUG.md"
  ```

### 1.5 Local scope (.claude/local)

- **arg-prompts**: "Path to the project root? (validates non-git-tracked elements: settings.local.json, gitignored agents/skills/commands)"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" local-scope "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_local_scope/$TS-$SLUG.md"
  ```

### 1.6 Project scope (.claude/git-tracked)

- **arg-prompts**: "Path to the project root? (validates git-tracked elements: settings.json, agents/skills/commands)"
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" project-scope "$TARGET_PATH" --report "$MAIN_ROOT/reports/validate_project_scope/$TS-$SLUG.md"
  ```

### 1.7 Component

- **arg-prompts**: "Which component? (hook / mcp / agent / command / security / encoding / rules / xref / docs / enterprise / scoring / lsp)" + "Path to the plugin?"
- **execution**: same launcher pattern with the chosen alias.

---

### 2. Validate from GitHub

1. **Plugin from GitHub** (owner/repo)
2. **Marketplace from GitHub** (owner/repo)
3. **Back**
4. **Cancel / Exit**

### 2.1 Plugin from GitHub

- **arg-prompts**: "GitHub spec? (`owner/repo` or `https://github.com/owner/repo`)" + "Run security `--audit` too? (yes/no)"
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

### 2.2 Marketplace from GitHub

- **arg-prompts**: same as 2.1
- **execution**:
  ```bash
  CLAUDE_PRIVATE_USERNAMES="$(whoami)" uv run --with pyyaml \
    python "$LAUNCHER" github --marketplace "$REPO" --report "$MAIN_ROOT/reports/validate_github_marketplace/$TS-$(echo "$REPO" | tr '/' '_').md"
  ```

---

### 3. Fix

1. **Fix plugin findings** — from a report file OR a plugin path (uses plugin-fixer agent)
2. **Fix marketplace findings** — from a report OR marketplace path (uses marketplace-fixer agent)
3. **Cache optimize** — audit + fix loop for CA-01..CA-06 (uses cache-optimizer-agent)
4. **Back**
5. **Cancel / Exit**

### 3.1 Fix plugin findings

- **arg-prompts**: "Path to a validation report `.md` file OR a plugin directory?"
- **execution**: dispatch the **plugin-fixer agent** with the path. The agent owns the validate→fix→re-validate loop.

### 3.2 Fix marketplace findings

- **arg-prompts**: "Path to a marketplace validation report OR a marketplace directory?"
- **execution**: dispatch the **marketplace-fixer agent**. Handles mechanical fixes AND architectural migrations (Layout A↔B↔C).

### 3.3 Cache optimize

- **arg-prompts**: "Path to plugin or project root?" + "Also do broader cache-aware refactoring? (yes/no — `--broader` invokes Phase 4)"
- **execution**: dispatch the **cache-optimizer-agent** with the path and `--broader` flag if requested.

---

### 4. Create

1. **Scaffold a new plugin** (uses plugin-creator agent)
2. **Scaffold a new marketplace** (uses plugin-creator agent — marketplace branch)
3. **Back**
4. **Cancel / Exit**

### 4.1 Scaffold a new plugin

- **arg-prompts**: "Plugin name?" + "Target directory?" + "Layout (A=hub-and-spoke / B=nested monorepo / C=marketplace-in-plugin self-referential)?"
- **execution**: dispatch the **plugin-creator agent** with the answers.

### 4.2 Scaffold a new marketplace

- **arg-prompts**: "Marketplace name?" + "Target directory?" + "Owner GitHub username?"
- **execution**: dispatch the **plugin-creator agent** in marketplace mode.

---

### 5. Manage

1. **List installed plugins**
2. **Install / update / enable / disable** (dispatches plugin-manager agent)
3. **Doctor (health check)**
4. **Install all external scanners** (one-shot batch install — `cpv-doctor --install-scanners`)
5. **Prune old plugin cache versions** (free disk space — keeps active version, deletes older)
6. **Bump version (current plugin)**
7. **Show CPV version**
8. **Back**
9. **Cancel / Exit**

### 5.1 List installed plugins

- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" registry --list
  ```

### 5.2 Install / update / enable / disable

- **execution**: dispatch the **plugin-manager agent**. The agent's First Contact menu asks what operation to do.

### 5.3 Doctor (health check)

- **arg-prompts**: "Verbose output? (yes/no)" + "Auto-fix orphaned entries? (yes/no — passes `--fix`)"
- **execution**:
  ```bash
  uv run --with pyyaml python "$LAUNCHER" doctor [--verbose] [--fix]
  ```

### 5.4 Install all external scanners

- **arg-prompts**: "This will install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, AND fclones via brew/snap/pipx/cargo (silent, idempotent, per-platform). Proceed? (yes/no)"
- **execution**: ALWAYS confirm first, then:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners
  ```
- **note**: This is the ONLY direct invocation of `manage_doctor.py` — `--install-scanners` is a one-shot bootstrap that doesn't need the launcher's environment isolation (it just calls platform package managers).

### 5.5 Prune old plugin cache versions (v2.48)

- **arg-prompts**: "First show DRY-RUN preview? (yes/no — recommended yes)" then if yes, show output and ask "Proceed with actual deletion? (yes/no)". Also ask "Keep how many newest versions per plugin? (default: 1)"
- **execution** (dry-run preview):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-dry-run --prune-keep $KEEP_N
  ```
- **execution** (actual delete):
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-old-versions --prune-keep $KEEP_N
  ```
- **note**: Direct invocation of `manage_doctor.py` is intentional (one-shot operation, no validator imports). The active version (whichever Claude Code's `enabledPlugins` references) is always kept, even if older than another cached version.

### 5.6 Bump version (current plugin)

- **arg-prompts**: "Bump type? (patch / minor / major)"
- **execution**:
  ```bash
  uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/bump_version.py" --$BUMP_TYPE
  ```

### 5.7 Show CPV version

- **execution**:
  ```bash
  cat "${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])'
  ```

---

### 6. GitHub setup

1. **Branch protection rules (current repo)**
2. **Branch protection rules (generic owner/repo)**
3. **Link plugin to a marketplace**
4. **Back**
5. **Cancel / Exit**

### 6.1 Branch protection rules (current repo)

- **execution**: invokes `/cpv-setup-branch-rules` workflow inline (no extra prompts — uses the current `git remote get-url origin`).

### 6.2 Branch protection rules (generic owner/repo)

- **arg-prompts**: "Owner/repo slug?"
- **execution**: invokes `/cpv-setup-branch-rules-generic` workflow inline with the slug.

### 6.3 Link plugin to a marketplace

- **arg-prompts**: "Plugin repo (owner/repo)?" + "Marketplace repo (owner/repo)?"
- **execution**: invokes `/cpv-link-plugin` workflow inline with the answers.

---

### 7. Deep semantic analysis (opus, expensive)

1. **Confirm + run on a path**
2. **Back**
3. **Cancel / Exit**

### 7.1 Confirm + run

- **arg-prompts**: ALWAYS show the cost warning first:
  > "Semantic validation uses Opus with 1M context at max effort. Cost: ~10-50x normal. Proceed? (yes/no)"
- If yes, second prompt: "Path to skill or agent or whole plugin?"
- **execution**: dispatch the **semantic-validator agent** with the path. The agent itself runs the syntactic baseline first then the semantic pass.

---

### 8. Help / About

1. **Category overview (one-liner per category)**
2. **List every CPV command with description**
3. **Show CPV plugin version**
4. **Back**
5. **Cancel / Exit**

### 8.1 Category overview

- **execution**: print the 8-row table from the top of this file.

### 8.2 List every CPV command

- **execution**:
  ```bash
  for f in "${CLAUDE_PLUGIN_ROOT}"/commands/cpv-*.md; do
    name=$(basename "$f" .md)
    desc=$(awk '/^description:/{sub(/^description:[[:space:]]*/, ""); print; exit}' "$f")
    printf "%-42s %s\n" "/$name" "$desc"
  done
  ```

### 8.3 Show CPV plugin version

- **execution**: same as 5.7.

---

## Etiquette and error handling

### Cancel / Exit semantics

At ANY menu level, picking "Cancel / Exit" → the orchestrator MUST:
1. Stop all further menu prompts.
2. Reply with exactly ONE line: `Cancelled — no actions taken.`
3. Not run any bash, not write any reports, not modify any files.

"Back" semantics: at any sub-menu level, picking "Back" → re-present the
PARENT menu (typically the top-level menu).

### Argument-prompt etiquette

- ALWAYS ask required arguments via `AskUserQuestion` (NEVER infer them).
- If the user provides an invalid path → re-ask with a hint, do not abort.
- If the user picks "Cancel / Exit" at the argument prompt → treat the
  same as a top-level Cancel.
- For paths, ALWAYS resolve `~` to `$HOME` and expand environment
  variables before invoking bash.

### Error handling

- If `${CLAUDE_PLUGIN_ROOT}` is unset → abort with:
  > "CPV plugin not installed in this session. Install via
  > `/plugin install claude-plugins-validation@emasoft-plugins`."
- If a launcher invocation exits non-zero → surface stderr verbatim,
  then return to the SAME sub-menu (not top-level) so the user can
  retry with different arguments.
- If `AskUserQuestion` itself fails → fall back to a plain text prompt
  asking the user to paste their choice (last resort).

### Token budget

- Never paste a full report into the response. Always return the
  report-file path and a 3-line summary (verdict + counts + path).
- Do not load `references/menu-tree.md` repeatedly — the orchestrator
  reads it once at session start.
- Use the launcher invocation table (above) verbatim — do not generate
  alternative bash spellings.
