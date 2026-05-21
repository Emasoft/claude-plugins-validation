# cpv-batch-validate — Error handling matrix

| Condition | Behaviour |
|---|---|
| Empty / whitespace-only input | The resolver raises `InputResolutionError("input spec is empty")`. The orchestrator command surfaces it and stops. |
| Spec resolves to ZERO plugins (e.g. empty `@listfile`, marketplace with no `plugins[]`) | The slash command prints "The input resolved to zero plugins. Nothing to validate. ✓" and stops without dispatching any agent. |
| URL input but `--no-url` was passed (only used by scope-aware skills) | The resolver raises with a remediation hint. The orchestrator surfaces it verbatim. |
| `git clone` failure (network down, repo not found) | The resolver raises `InputResolutionError(...)` listing the owner/repo. The orchestrator surfaces it and stops; no partial batch. |
| One subagent fails mid-batch | The status JSON for that plugin shows `status_label: failed` with the error in `notes`. Other agents complete normally. The final summary counts the failed plugin. |
| Manifest temp dir cannot be cleaned up | Cleanup callbacks are best-effort — errors are swallowed. The temp dir lives until the next OS-level `${TMPDIR}` purge. |

## Examples

```text
User: validate every plugin in Emasoft/emasoft-plugins
Assistant: /cpv-batch-validate Emasoft/emasoft-plugins

User: validate these three plugins in parallel — /path/a /path/b /path/c
Assistant: /cpv-batch-validate /path/a /path/b /path/c

User: I have a file with 30 plugin paths, validate them all
Assistant: /cpv-batch-validate @/tmp/plugins.txt
```
