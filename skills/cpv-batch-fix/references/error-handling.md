# cpv-batch-fix — Error handling matrix

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to batch-fix. ✓" + stop. |
| Plugin tree not writable | Per-plugin status JSON shows `failed` with the permission error. |
| Fix oscillation in one plugin | The per-plugin fixer stops with `partial`; other plugins complete normally. |
| One plugin needs internal sharding (finding count exceeds safe ceiling) | The per-plugin fixer internally invokes `cpv_batch_planner.py` and consumes shards SEQUENTIALLY (Anthropic spec: subagents cannot spawn parallel subagents). |
| Network failure during URL clone | Resolver raises with a remediation message; no partial batch. |

## Examples

```text
User: fix every plugin in this marketplace
Assistant: /cpv-batch-fix Emasoft/emasoft-plugins

User: fix this one plugin (large) — its findings exceed a single fixer's context
Assistant: /cpv-batch-fix /path/to/big-plugin  # → per-shard fan-out

User: only fix CRITICAL findings across these three plugins
Assistant: /cpv-batch-fix /path/a /path/b /path/c --min-severity critical
```
