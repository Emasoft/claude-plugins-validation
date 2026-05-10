---
name: cpv-doctor
description: Menu-driven plugin doctor — diagnose a single plugin, current folder, GitHub repo, scope, individual component, or run cache-cleanup / scanner-install / quick-health-check (NEVER auto-scans every cached plugin)
allowed-tools: Bash(uv:*)
agent: cpv-doctor-menu
user-invocable: true
---

# /cpv-doctor

`/cpv-doctor` opens a 20-option menu (single plugin, current folder, GitHub plugin/marketplace, project/user scope, individual skill/agent/hook/MCP/monitor/output-style/LSP, cache cleanup, scanner install, quick health check, free-form). The doctor NEVER scans every cached plugin by default — the wholesale "scan all installed plugins" path is option `3`, gated behind an explicit confirmation, because on a typical install (15-30 plugins) it takes 3-8 minutes.

## How

The slash command dispatches the **cpv-doctor-menu** agent (haiku —
TRDD-82e836dc), which prints the 22-row first-contact menu verbatim,
reads the user's plain-text reply, and dispatches the **cpv-doctor-agent**
(opus) work agent via the Agent tool with the chosen mode + target_path
inside a structured `<context>` block. The work agent runs the matching
diagnostic recipe, then renders the post-scan follow-up menu (rows 1-9
with severity-threshold actions) when findings exist — that follow-up
menu stays on opus because it requires scanner-output context.

Power-user CLI examples (the same flags the work agent uses internally):

```bash
# Choice 1: dispatch plugin-diagnoser on a path
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    plugin /path/to/plugin --strict

# Choice 17: cache cleanup — DRY-RUN first, then real
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-dry-run
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --prune-old-versions

# Choice 18: install external scanners (one-shot bootstrap, bypasses launcher)
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners

# Choice 19: auto-fix orphaned settings entries
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    doctor --fix

# Choice 3 (only on explicit confirmation): scan EVERY installed plugin
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" \
    doctor --verbose
```

The 22-row first-contact menu and the per-choice → mode mapping live in
`agents/cpv-doctor-menu.md`. The per-mode recipes (the actual diagnostic
work) and the post-scan follow-up menu live in `agents/cpv-doctor-agent.md`.

## Why menu-driven

The historical default `/cpv-doctor` invocation ran the per-plugin validator on EVERY cached plugin under `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. On installs with 20+ plugins this dominated runtime (3-8 minutes), AND in 90 % of cases the user only wanted to diagnose ONE thing — a specific plugin, a single skill, a marketplace, the current folder, etc.

The new menu makes the wholesale scan opt-in (option `3`) while exposing every existing CPV diagnostic surface as a one-keystroke choice. The legacy CLI flags (`--verbose`, `--fix`, `--install-scanners`, `--prune-old-versions`, `--prune-dry-run`, `--prune-keep`) are deduplicated into menu choices `3`, `19`, `18`, `17` (plus the `prune-keep N` follow-up question) so power users get the same operations without remembering flag names.

## Cache cleanup details (choice 17)

`claude plugin update` downloads new versions but never deletes older ones. Over time `~/.claude/plugins/cache/` grows to many GB. Choice 17 always runs `--prune-dry-run` first so the user sees what WOULD be deleted, then asks for confirmation before invoking `--prune-old-versions`. Optionally pass `--prune-keep N` to keep more than the active version per plugin.

The active version (whichever Claude Code references in `enabledPlugins`) is **always** kept, even if older than another cached version. Re-install will re-populate the cache from the marketplace if you later need a pruned version.

## External scanners install (choice 18)

Installs cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner, and fclones (cross-plugin dedup) via the per-platform package managers. Each tool is checked first via `shutil.which()` and only installed if missing.

- **macOS**: `brew install <tool>` + `npm i -g <pkg>` + `pipx install <pkg>` + `uv tool install cisco-ai-skill-scanner`
- **Linux**: `snap install fclones` → `cargo install fclones` fallback; `pipx`/`brew`/`npm` for the rest
- **Windows**: GitHub-release download for fclones (`x86_64-pc-windows-msvc.zip`) + `pipx`/`npm`

Opt-out env vars: `CPV_NO_FCLONES_INSTALL=1`, `CPV_NO_TIRITH_INSTALL=1`. Failed installs emit a one-line WARNING and continue (CPV degrades gracefully — fclones missing just disables dedup, scans still run).

See the **plugin-management** skill for full details.
