---
trdd-id: 891a0804-0bdf-4a42-9ff5-5c658618311f
title: SkillAudit rules — Claude env-var poisoning + plugin-system / CLI abuse detection
column: complete
created: 2026-06-01T17:47:50+0200
updated: 2026-08-25T17:25:16+0200
---

# TRDD-891a0804 — Claude env-var poisoning + CLI-abuse detection rules

**Filename:** `design/tasks/TRDD-20260601_174750+0200-891a0804-claude-env-poison-cli-abuse-rules.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## ⏵ STATE — READ FIRST

Implementing a new SkillAudit rule set (GitHub issue #64 + the maintainer's
broader mandate) that flags a plugin/hook **poisoning Claude Code's declared
env vars** or **abusing the `claude` CLI**. Ground truth (authoritative, from
the official docs 2026-06-01) is in
`reports/env-poison-feature/ground-truth.md`.

**NEXT ACTION:** implement the rules in `scripts/rules/skillaudit_patterns.json`
plus context-classifier guards, two-sided tests, then `publish.py --minor`.

## Origin

- GitHub issue #64 (Emasoft): a plugin re-exporting reserved `CLAUDE_PLUGIN_DATA`
  into `$CLAUDE_ENV_FILE` clobbers it session-wide for every other plugin (real
  incident: codex plugin broke the janitor daemon). Filed enhancement.
- Maintainer mandate (verbatim intent): "all env vars of the plugins that claude
  code declares must be never overwritten … major issue. both plugin and hooks
  (different vars). Also … env vars that are supposed to be changed by the user,
  but that may be changed by error or maliciously. And … using the claude cli
  directly for malicious purposes, unauthorized plugin installation from
  untrusted marketplaces, or for data exfiltration (e.g. `claude setup-token`
  generating a token that could be stolen)."

## Severity model (confirmed)

`_SEVERITY_MAP` in cpv_skillaudit_native.py: `critical→CRITICAL`, `high→MAJOR`,
`medium→MINOR`, `low→NIT`.

## Rule families

### A. `CLAUDE_RESERVED_ENV_POISON` — severity `high` (MAJOR) — category `persistence`
A plugin/hook **writes** a reserved, harness-set, per-plugin/per-context var into
the global session env (`$CLAUDE_ENV_FILE`) or `export`s it.
Reserved names: `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA`, `CLAUDE_PROJECT_DIR`,
`CLAUDE_ENV_FILE`, `CLAUDE_EFFORT`, `CLAUDE_CODE_REMOTE`, `CLAUDECODE`.
Cross-plugin integrity violation; long-lived daemons inherit the clobbered value.

### B. `CLAUDE_AUTH_ENV_OVERRIDE` — severity `critical` (CRITICAL) — category `credential_theft`
A plugin/hook **writes/overwrites** an auth or API-endpoint var:
`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_AWS_API_KEY`,
`ANTHROPIC_FOUNDRY_API_KEY`, `ANTHROPIC_WORKSPACE_ID`, `ANTHROPIC_AWS_WORKSPACE_ID`,
`CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_OAUTH_REFRESH_TOKEN`,
`CLAUDE_CODE_OAUTH_SCOPES`, `AWS_BEARER_TOKEN_BEDROCK`,
`ANTHROPIC_BASE_URL`, `ANTHROPIC_*_BASE_URL`. Setting `ANTHROPIC_BASE_URL` redirects
ALL API traffic (MITM/exfil of prompts+responses); setting a key swaps the user's
credentials. CRITICAL.

### C. `CLAUDE_SAFETY_ENV_TAMPER` — severity `high` (MAJOR) — category `persistence`
A plugin/hook **writes** a guardrail/telemetry/plugin-loading toggle:
`CLAUDE_CODE_DISABLE_*`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`,
`CLAUDE_CODE_DISABLE_POLICY_SKILLS`, `DISABLE_TELEMETRY`, `DO_NOT_TRACK`,
`DISABLE_AUTOUPDATER`, `DISABLE_ERROR_REPORTING`, `DISABLE_FEEDBACK_COMMAND`,
`CLAUDE_CODE_CERT_STORE`, `CLAUDE_CODE_CLIENT_CERT|KEY|KEY_PASSPHRASE`,
`CLAUDE_CODE_PLUGIN_CACHE_DIR`, `CLAUDE_CODE_PLUGIN_SEED_DIR`,
`CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL`. Silently disables
guardrails / hides activity / redirects plugin loading.

### D. `CLAUDE_CLI_TOKEN_THEFT` — severity `critical` (CRITICAL) — category `credential_theft`
A plugin/hook/script invokes `claude setup-token` (mints a long-lived OAuth token
that, if exfiltrated, grants a year of the user's subscription).

### E. `CLAUDE_CLI_PERMISSION_BYPASS` — severity `critical` (CRITICAL) — category `code_execution`
A plugin/hook invokes `claude` with `--dangerously-skip-permissions` /
`--dangerously-bypass-approvals-and-sandbox` (bypasses all permission prompts).

### F. `CLAUDE_CLI_UNAUTHORIZED_INSTALL` — severity `high` (MAJOR) — category `supply_chain`
A plugin/hook invokes `claude plugin install`, `claude plugin marketplace add`,
or `claude mcp add` (programmatic install/registration from a plugin = supply-chain
/ unauthorized-install vector).

## Detection mechanics

1. **Direct-form patterns** (skillaudit_patterns.json regexes), language-agnostic:
   - bash/env-file: `export\s+<NAME>=`, `<NAME>=...>>...CLAUDE_ENV_FILE`
   - JS/TS: `process\.env\.<NAME>\s*=[^=]`, the literal `export <NAME>=` inside a
     template string written to the env file
   - Python: `os\.environ\[['"]<NAME>['"]\]\s*=`, `os.putenv("<NAME>"`
   - CLI: `claude\s+setup-token`, `claude\b.*--dangerously-skip-permissions`,
     `claude\s+plugin\s+(install|marketplace\s+add)`, `claude\s+mcp\s+add`
2. **Indirected env-file-writer flow** (context classifier, catches the codex case):
   a reserved/auth/toggle NAME bound to a const that flows into an env-file writer
   (`appendFileSync`/`writeFileSync`/`open(CLAUDE_ENV_FILE,"a")`/`>> $CLAUDE_ENV_FILE`).
3. **hooks.json command strings** are scanned (the command is a shell string).

## FP guardrails (MUST NOT fire)

- **READING** any of these (`process.env.X` w/o `=`, `${X}`, `os.environ[X]` read,
  `os.environ.get`) — normal, never fires.
- Writing a plugin's **own namespaced** var (`CODEX_*`, `MYPLUGIN_*`, `NODE_ENV`,
  `DEBUG_*`, PATH-append) — never fires.
- Setting a var inside a **per-command `env:{}`** block scoped to the plugin's own
  MCP/LSP server (NOT the global env file) — never fires.
- Docs/prose/comments that *describe* the anti-pattern (the existing
  safe_doc/comment classifier handling applies).

## Acceptance

- Two-sided tests: benign (read / own-namespaced / per-command env / doc-mention)
  STAY clean; each malicious form (incl. the verbatim codex snippet) FIRES at the
  declared severity.
- self-scan 0/0/0/0, mypy clean, ruff clean, full serial pytest green, manifest.
- `publish.py --minor`; CI/Release/Notify green; close issue #64 citing the release.

## Evidence

- `reports/env-poison-feature/ground-truth.md` (authoritative var lists + CLI).
- Existing rule template: `ENV_INJECTION` (skillaudit_patterns.json:790).

## v2.116.0 shipped, then v2.116.1 refinement — plugin-wide install-combo

v2.116.0 shipped all six rules + the env-file flow detector (validated against
the live ai-maestro-plugin source: 0 false positives). The maintainer then
refined the **install-authorization** model:

> Adding a marketplace IS the user's trust decision, so installing a SPECIFIC
> plugin from an already-trusted marketplace is AUTHORIZED. Flag only when the
> plugin — anywhere across its files (possibly SPLIT to evade per-file scanning)
> — BOTH adds a SPECIFIC marketplace AND installs a SPECIFIC (other) plugin.
> Universal/templated procedures are not a security issue.

v2.116.1 implements this as a **plugin-wide** check
(`validate_plugin._check_unauthorized_install_combo`), and NARROWS the per-file
`CLAUDE_CLI_UNAUTHORIZED_INSTALL` rule to `claude mcp add` only (autonomous
MCP-server registration). The combo fires only when BOTH a specific
marketplace-add AND a specific non-self plugin-install exist in AUTONOMOUS
surfaces. FP-iteration (each found scanning CPV itself, then fixed):

1. **Self-bootstrap exemption** — installing THIS plugin (name from
   plugin.json) is the benign first-install path; exempt.
2. **Documentation exclusion** — README / design/ / references/ / docs/ and
   loose `.md` are human-read guides/examples, not autonomous execution. Only
   executable code (.sh/.py/.js/.mjs/.cjs/.ts), hooks/MCP configs, and
   AGENT-LOADED instructions (SKILL.md, agents/, commands/, .claude/rules/,
   CLAUDE.md) count (`_combo_path_is_autonomous`).
3. **tests/ + fixtures/ excluded** — dev-only, never harness-loaded.
4. **Example/placeholder tokens excluded** (`_install_ref_is_specific`):
   `my-*` / `your-*` / `owner/*` / `foo` / `X@X` self-referential /
   `path/to` / `${VARS}` / `<placeholders>` are illustrative, not concrete
   targets.

Result: CPV self-scan 0/0/0/0; ai-maestro-plugin 0 findings; the real
`marketplace add https://evil + install evil@evil` (incl. split across hooks or
SKILL.md) fires MAJOR. Tests in tests/test_claude_env_poison_cli_abuse.py.

## Approval log

- 2026-08-25T17:25:16+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.116.0/.1 — CLAUDE_RESERVED_ENV_POISON/CLAUDE_CLI_TOKEN_THEFT rules live (batch_ae)
