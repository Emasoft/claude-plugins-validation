---
name: cpv-doctor
description: Health-check all installed Claude Code plugins, settings, and marketplaces (--fix to auto-repair)
user-invocable: true
---

Run the plugin health check via the launcher (NEVER call `manage_doctor.py` directly from the cache — environment-isolation guard refuses):

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" doctor
```

For full validation details: `--verbose`. To auto-fix orphaned entries: `--fix`.

```bash
uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/remote_validation.py" doctor --verbose --fix
```

Checks: CLI auth, settings integrity, marketplace registrations, plugin validation, orphaned entries, stale `settings.local.json` entries.

`--fix` auto-removes: orphaned marketplace registrations pointing to missing directories, orphaned `enabledPlugins` entries for missing plugins/marketplaces.

## v2.48 — Install all external scanners with one command

```bash
# Install cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner,
# AND fclones (cross-plugin dedup) — silent, idempotent, per-platform cascade.
# Each tool is checked first via shutil.which() and only installed if missing.
uv run python "${CLAUDE_PLUGIN_ROOT}/scripts/manage_doctor.py" --install-scanners
```

This direct invocation is intentional — `--install-scanners` is a one-shot bootstrap operation that bypasses the launcher (it doesn't need environment isolation; it just calls platform package managers). The doctor itself uses the launcher for normal `--verbose`/`--fix` operations.

**Per-platform install paths** (silent, automatic):
- macOS: `brew install <tool>` (fclones, trufflehog, semgrep) + `npm i -g <pkg>` (cc-audit) + `pipx install <pkg>` (tirith) + `uv tool install cisco-ai-skill-scanner`
- Linux: `snap install fclones` → `cargo install fclones` fallback; `pipx`/`brew`/`npm` for the rest
- Windows: GitHub-release download for fclones (`x86_64-pc-windows-msvc.zip`) + `pipx`/`npm`

**Opt-out env vars**: `CPV_NO_FCLONES_INSTALL=1`, `CPV_NO_TIRITH_INSTALL=1`. Failed installs emit a one-line WARNING and continue (CPV degrades gracefully — fclones missing just disables dedup, scans still run).

See the **plugin-management** skill for full details.
