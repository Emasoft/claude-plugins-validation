---
name: cpv-batch-security-audit
description: Fan out security-only validation across every plugin in a marketplace, a list of plugins, or a single plugin. Accepts local paths and GitHub URLs. One plugin-validator agent per plugin (mode batch_security_audit) running only the security checker — five external scanners (cc-audit, tirith, trufflehog, semgrep, Cisco AI Defense skill-scanner) plus the in-process AI/security rule pack. Parallel main-session dispatch (default 8 at a time, cap 16).
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-security-audit — Security audit every plugin in a marketplace

For users who maintain a marketplace and need a fleet-wide security
snapshot, this command dispatches one `plugin-validator` per plugin
in `batch_security_audit` mode. The agent runs **only** the
`validate_security` checker (faster than full plugin validation,
and covers the most important signal: external CC-Audit / Tirith /
TruffleHog / Semgrep / Cisco AI Defense / in-process rule findings).

Same input grammar and dispatch shape as `/cpv-batch-validate` —
single plugin / single plugin URL / marketplace local / marketplace
URL / list / `@listfile` / comma-separated.

## You are the orchestrator

You — the model running THIS turn — orchestrate the batch from the
main session. You do NOT scan anything yourself.

## Step 0 — Resolve arguments

If no target was given, ask plain-text:

```text
Which marketplace, plugin, or list should I security-audit? Provide a path, URL, or @listfile.
```

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="$1"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent plugin-validator \
  --mode batch_security_audit \
  --max-parallel "$MAX_PARALLEL"
```

Print the initial status table:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table "$(cat "$STATUS_TABLE")"
```

## Step 2 — Dispatch security-audit agents in parallel

For each `dispatch_groups[i]`, emit one Agent call per plugin in a
single main-session message:

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "plugin-validator",
      description: "Batch-security-audit {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-security-audit
        mode: batch_security_audit
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Run ONLY `validate_security` on the plugin (not the full
        validate_plugin pipeline). Write per-plugin status JSON to
        `status_path` with these keys:

          {
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "findings" | "warning-only",
            "counts": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "report_path": "<abs-path-to-validate_security-report>",
            "notes": "<short summary>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: <C>/<M>/<m>/<n>/<w> (status: {status_path})

        Do NOT render menus. Do NOT recommend follow-ups.
      run_in_background: false
    )
```

## Step 3 — Mid-batch status refresh

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" status \
  "$SESSION_DIR/plan.json" \
| python3 "${CLAUDE_PLUGIN_ROOT}/scripts/format_menu.py" status_table /dev/stdin
```

## Step 4 — Final summary

After every plugin has reported, print the final table + one-line
summary:

```text
DONE: plugins=N clean=X findings=Y warning-only=Z. Reports under {session_dir}/.
```

If any plugin has findings, append the fix prompt:

```text
Run `/cpv-batch-fix {target}` to dispatch plugin-fixer agents across
the plugins with findings.
```

## Why a dedicated security command?

`/cpv-batch-validate` runs every validator (xref, docs, scoring,
lint, …) — useful for "does this plugin work?" but overkill when
you only care about supply-chain risk. `/cpv-batch-security-audit`
runs `validate_security` only — the most expensive checker stand-alone
because it shells out to 5 external scanners, but cheaper than the
full pipeline. For a 17-plugin marketplace it's typically 2-3× faster.

## See also

- TRDD-3dcbb37c §1-5 — full design
- `scripts/validate_security.py` — security validator (5 external scanners)
- `agents/plugin-validator.md` — `batch_security_audit` mode contract
- `commands/cpv-batch-validate.md`, `commands/cpv-batch-caching-audit.md`, `commands/cpv-batch-caching-optimize.md` — sibling batch skills
