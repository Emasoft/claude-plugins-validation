# v2.1.80+ Demo Plugin (CPV fixture)

Test fixture exercising every Claude Code v2.1.80+ feature CPV validates.

## What this fixture demonstrates

| Feature | Where it lives |
|---|---|
| `Monitor` tool (v2.1.98) | `agents/log-watcher.md` -> `tools: [Monitor, Read, Bash, Grep]` |
| `userConfig` (5-type whitelist) | `.claude-plugin/plugin.json` -> `userConfig` (string, number, boolean, directory, file) |
| `CLAUDE_PLUGIN_OPTION_<KEY>` env vars | `mcpServers.notifications.args[1]` and the demo skill body (under `skills/v2-1-80-demo-skill/`) |
| `channels` (plugin.json) | `.claude-plugin/plugin.json` -> `channels[0].server` matches `mcpServers.notifications` |
| Plugin skill `name` field (v2.1.98) | `skills/v2-1-80-demo-skill/SKILL.md` frontmatter -> explicit `name: v2-1-80-demo-skill` |

## Why every feature is here

- **Documentation cross-check.** Every feature documented in
  the v2-1-80-features reference inside the cpv-create-plugin /
  cpv-canonical-pipeline / cpv-setup-plugin-repo skills (in the top-level
  CPV repo) has a matching example in this fixture.
- **CI guardrail.** A regression that breaks `Monitor` tool
  acceptance (or the `userConfig` 5-type whitelist, or any other
  v2.1.80+ feature) will surface here as a CRITICAL or MAJOR finding
  before it ships.

## Out of scope

- This fixture is intentionally NOT a full publish-ready plugin. It
  has no `LICENSE`, `.gitignore`, or `pyproject.toml` because those
  would belong in a real publish pipeline, not in a feature
  demonstration.
- It does NOT exercise the inline-marketplace (`source: "settings"`)
  or `managed-settings.d/` features, because those live in
  `settings.json` rather than in a plugin tree. They are tested
  separately by the settings-marketplace validator test module.

## Usage

```bash
PLUGIN_SKIP_GITHUB_INTEGRITY=1 \
  uv run python scripts/validate_plugin.py tests/fixtures/v2_1_80_plugin
```

Expected CPV behaviour: zero CRITICAL findings; the only MAJOR/MINOR
findings are the standard "no LICENSE / no .gitignore / no
publish.py / no CI workflow" advisories that apply to every minimal
fixture.
