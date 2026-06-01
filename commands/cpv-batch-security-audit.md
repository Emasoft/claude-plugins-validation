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

The user supplies a target spec (required; first positional — accepts
every shape listed above) plus an optional `--max-parallel N` (default
8, cap 16).

If no target was given, ask plain-text:

```text
Which marketplace, plugin, or list should I security-audit? Provide a path, URL, or @listfile.
```

## Step 1 — Build the batch plan

```bash
# Separate the positional target specs from the --max-parallel flag in a
# single pass. We must NOT collapse to "$1": the input grammar above lists
# a whitespace-separated LIST (`./a ./b ./c`) as a valid shape, and the
# orchestrator's `plan` subcommand declares its inputs as `nargs="+"`, so
# every positional must be forwarded — using only "$1" would silently
# audit the first plugin and drop the rest.
#
# Both --max-parallel N (two tokens) and --max-parallel=N (one token) are
# handled, because argparse accepts the `=` form and a user may type it.
# Default 8; the orchestrator re-caps at 16, so a larger N is safe to pass.
BATCH_SPECS=()
MAX_PARALLEL=8
while [ "$#" -gt 0 ]; do
  case "$1" in
    --max-parallel)
      MAX_PARALLEL="$2"
      shift 2
      ;;
    --max-parallel=*)
      MAX_PARALLEL="${1#--max-parallel=}"
      shift
      ;;
    *)
      BATCH_SPECS+=("$1")
      shift
      ;;
  esac
done

if [ "${#BATCH_SPECS[@]}" -eq 0 ]; then
  echo "ERROR: no target spec given to /cpv-batch-security-audit" >&2
  exit 1
fi

python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" plan \
  "${BATCH_SPECS[@]}" \
  --agent plugin-validator \
  --mode batch_security_audit \
  --max-parallel "$MAX_PARALLEL"
```

Capture the orchestrator's stdout. It prints one `KEY: value` line each
for `PLAN`, `STATUS_TABLE`, `SESSION_DIR`, `PLUGIN_COUNT`, and
`DISPATCH_GROUPS`. Bind the two you need downstream:

```bash
STATUS_TABLE="$(... STATUS_TABLE line from the plan output ...)"
SESSION_DIR="$(...  SESSION_DIR  line from the plan output ...)"
```

If `PLUGIN_COUNT` is `0`, reply plain-text that there is nothing to
audit and stop.

Queue the initial status table for the claude-menu-system Stop hook
(emitted post-turn via ``systemMessage`` — zero token cost, NEVER
printed inline by the orchestrator):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_menu.py" "$STATUS_TABLE"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at turn end.
End the turn after this call. The user's next reply (if any) is routed
purely from the fixed letter→action map in §"Fixed key→action map" below.

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

Queue the live status table via the orchestrator's ``emit-status``
subcommand (one shot — aggregates every per-plugin status JSON and
hands the CMS spec to ``cpv_menu`` for emission via the Stop hook at
turn end):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
  emit-status "$SESSION_DIR/plan.json"
```

NEVER print menu inline; the CMS Stop hook emits via systemMessage at
turn end. End the turn after this call. Statuses are translated from
CPV symbols (✓ ✗ ⚠ ○) to the CMS enum
(``ok``/``missing``/``buggy``/``pending``) by the orchestrator.

## Step 4 — Final summary

After every plugin has reported:

1. Queue the final status table (same call as Step 3):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/cpv_batch_orchestrator.py" \
     emit-status "$SESSION_DIR/plan.json"
   ```

2. Print a one-line summary inline (text, not a menu):

   ```text
   DONE: plugins=N clean=X findings=Y warning-only=Z. Reports under {session_dir}/.
   ```

3. If any plugin has findings, append the fix-prompt inline:

   ```text
   Run `/cpv-batch-fix {target}` to dispatch plugin-fixer agents across
   the plugins with findings.
   ```

End the turn. The CMS Stop hook emits the final table via systemMessage.

## Fixed key→action map

`/cpv-batch-security-audit` is a one-shot fleet security scan; the
status table is informational only. No numbered or lettered action
rows — the user's next move is to run `/cpv-batch-fix` (text summary).
The slug ``batch-plugin-validator-status`` is shared with
`/cpv-batch-validate` (both invoke the same plugin-validator agent
type, so the orchestrator's auto-derived slug collides — intentional;
they emit one row per plugin in identical shape). The fixed
key→action map is empty by design; future post-scan menus extend this
contract with letter→action rows.

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
