---
name: cpv-batch-full-scan-and-fix
description: Maximum-coverage same-turn scan + fix. One plugin-fixer per plugin reads each source file ONCE and runs validate + security + caching audit + caching optimize + verify-FPs + fix — all inline. Each per-file scan triggers every applicable checker; confirmed-real findings are fixed; confirmed-FPs are skipped. Cuts per-plugin token cost ~5× vs running the four separate batch skills sequentially. Default 8 parallel agents per main-session message, cap 16.
allowed-tools: Read, Bash(python3:*,git:*,uv:*,cat:*,mkdir:*), Glob, Grep
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
user-invocable: true
---

# /cpv-batch-full-scan-and-fix — Maximum-coverage same-turn sweep

For fleet operators who want the deepest possible parallel sweep
across every plugin in a marketplace, this command bundles
**validate + security + caching audit + caching optimize + FP
verification + fix** into ONE per-plugin agent turn. Each
plugin-fixer subagent reads each source file ONCE and triggers
every applicable in-process checker, classifies findings via the
v2.100.x context classifier, verifies uncertain findings via
`llm-externalizer` with file-range syntax (≤ 200 LOC per call),
applies confirmed-real fixes inline, then runs a clean-room
re-check.

The four separate batch skills run separately would read every
source file 4× (once per scan type) plus the fix pass — this
command does it in one pass.

Per the iron-rule: same-turn = optimisation. Every finding still
walks the full classifier chain; FPs are verified by external LLM
before being silenced. Real findings are still fixed.

Same input grammar as the rest of the batch family.

## You are the orchestrator

You — the model running THIS turn — drive the batch. You do NOT
scan or fix anything yourself.

## Step 0 — Resolve arguments

If no target was given, ask plain-text:

```text
What should I full-scan-and-fix? Provide an absolute path, a GitHub URL, a marketplace, or a list file like @/tmp/plugins.txt.
```

## Step 1 — Build the batch plan

```bash
BATCH_SPEC="$1"
MAX_PARALLEL=8

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "$BATCH_SPEC" \
  --agent plugin-fixer \
  --mode batch_same_turn_full \
  --max-parallel "$MAX_PARALLEL"
```

Print the initial status table.

## Step 2 — Dispatch full-scan-and-fix agents in parallel

```yaml
for plugin_index in group:
    plugin = plan.plugins[plugin_index]
    Agent(
      subagent_type: "plugin-fixer",
      description: "Batch-full-scan-and-fix {plugin.display_name}",
      prompt: |
        <context>
        source: /cpv-batch-full-scan-and-fix
        mode: batch_same_turn_full
        plugin_index: {plugin.plugin_index}
        plugin_path: {plugin.abs_path}
        source_url: {plugin.source_url or "—"}
        display_name: {plugin.display_name}
        session_dir: {plan.session_dir}
        status_path: {plan.session_dir}/plugin-{plugin.plugin_index}.status.json
        </context>

        Apply the FULL same-turn scan + fix loop to the plugin at
        `plugin_path`:

        1. Walk every source file ONCE.
        2. For each file, trigger EVERY applicable checker:
           a. `validate_plugin` schema/structure rules
           b. `validate_security` (5 external scanners + AI/security rules)
           c. `validate_cache` (CA-01..CA-06)
           d. Any other in-scope checker (lint, xref, encoding, …)
        3. Classify each finding via the v2.100.x context
           classifier (Python AST / JSON schema / Markdown fence /
           YAML workflow). For uncertain findings, invoke
           `llm-externalizer` with file-range syntax (≤ 200 LOC
           per call) — minimum-token FP verification.
        4. Apply confirmed-real fixes inline. Skip confirmed FPs.
        5. Run ONE final clean-room re-check via
           `validate_plugin --strict + validate_security + validate_cache`.

        Write per-plugin status JSON to `status_path`:

          {
            "schema_version": 1,
            "plugin_index": <int>,
            "status_symbol": "✓" | "✗" | "⚠",
            "status_label": "clean" | "fixed" | "partial" | "failed",
            "before": {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "after":  {"critical": N, "major": N, "minor": N, "nit": N, "warning": N},
            "by_checker": {
              "validate": {"before": N, "after": N},
              "security": {"before": N, "after": N},
              "cache":    {"before": N, "after": N}
            },
            "fps_verified": <int>,
            "report_path": "<abs-path-to-final-re-check-report>",
            "notes": "<short summary>"
          }

        Return ONE line exactly:

          [plugin-{plugin.plugin_index}] {label}: fixed=X remaining=Y fps=Z (status: {status_path})

        Do NOT render menus.
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

After every plugin has reported, print the final status table +
one-line summary:

```text
DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Total FPs verified: F. Reports under {session_dir}/.
```

## When to use this vs the separate batch skills

| Goal | Best command |
|------|--------------|
| Read-only validate snapshot | `/cpv-batch-validate` |
| Read-only security snapshot | `/cpv-batch-security-audit` |
| Read-only cache snapshot | `/cpv-batch-caching-audit` |
| Apply fixes for the most-recent validation | `/cpv-batch-fix` |
| Apply cache fixes specifically | `/cpv-batch-caching-optimize` |
| One-pass validate + fix (skip security/caching) | `/cpv-batch-validate-and-fix` |
| One-pass EVERY checker + fix | **this command** |

## See also

- TRDD-3dcbb37c §3 — full design
- `/cpv-batch-validate-and-fix` — narrower same-turn variant
- `agents/plugin-fixer.md` — `batch_same_turn_full` mode contract
