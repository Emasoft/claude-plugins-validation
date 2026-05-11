# Spec-Coverage Matrix — Claude Code rules vs CPV checks

**TRDD:** b4c6cbe7
**Generated:** 2026-05-11

## 1. Summary counts

| Bucket | Count |
|---|---:|
| `covered` | 0 |
| `partial` | 115 |
| `missing` | 0 |
| `unmapped` | 394 |
| **Total** | **509** |

## 2. Rules

| # | Modal | Sentence (truncated) | Source | Coverage | Likely CPV check |
|---:|---|---|---|---|---|
| 1 | `REQUIRED` | rogress, rate limits, task notifications) that provide extra detail but are not required to drive the loop | `agent-loop.md` | `unmapped` | _unmapped_ |
| 2 | `MUST` | Skills must be created as filesystem artifacts (` | `claude-code-features.md` | `partial` | skill frontmatter validation (Phase 12+) |
| 3 | `MUST` | * **Input schema:** the arguments Claude must provide | `custom-tools.md` | `partial` | skill arguments declaration + `$<name>` cross-ref |
| 4 | `MUST` | It receives the validated arguments and must return an object with: | `custom-tools.md` | `partial` | skill arguments declaration + `$<name>` cross-ref |
| 5 | `REQUIRED` | * `content` (required): an array of result blocks, each with a `type` of `"text"`, `"image"`, or `"resource"` | `custom-tools.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 6 | `REQUIRED` | In Python, the dict schema treats every key as required, so leave the parameter out of the schema, mention it in the description string, and read it with `args | `custom-tools.md` | `partial` | description length recommendation |
| 7 | `REQUIRED` | \| `mimeType` \| `string` \| Required | `custom-tools.md` | `unmapped` | _unmapped_ |
| 8 | `REQUIRED` | Python, the dict schema doesn't support enums, so the full JSON Schema dict is required | `custom-tools.md` | `unmapped` | _unmapped_ |
| 9 | `REQUIRED` | # The dict schema has no equivalent, so full JSON Schema is required | `custom-tools.md` | `unmapped` | _unmapped_ |
| 10 | `REQUIRED` | "required": ["unit_type", "from_unit", "to_unit", "value"], | `custom-tools.md` | `unmapped` | _unmapped_ |
| 11 | `REQUIRED` | }, # Required to receive checkpoint UUIDs in the response stream | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 12 | `REQUIRED` | extraArgs: { "replay-user-messages": null } // Required to receive checkpoint UUIDs in the response stream | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 13 | `REQUIRED` | replay-user-messages": None}` \| `extraArgs: { 'replay-user-messages': null }` \| Required to get user message UUIDs in the stream \| | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 14 | `REQUIRED` | # - extra_args: Required to receive user message UUIDs in the stream | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 15 | `REQUIRED` | // - extraArgs: Required to receive user message UUIDs in the stream | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 16 | `REQUIRED` | **[Sessions](/en/agent-sdk/sessions)**: learn how to resume sessions, which is required for rewinding after the stream completes | `file-checkpointing.md` | `unmapped` | _unmapped_ |
| 17 | `MUST` | When using `updatedInput`, you must also include `permissionDecision: 'allow'` | `hooks.md` | `unmapped` | _unmapped_ |
| 18 | `MUST` | * You must also return `permissionDecision: 'allow'` for the input modification to take effect | `hooks.md` | `unmapped` | _unmapped_ |
| 19 | `REQUIRED` | \| `hooks` \| `HookCallback[]` \| - \| Required | `hooks.md` | `partial` | hook event/type validation (Phase 12+) |
| 20 | `MUST` | Best for agents that must collaborate closely together | `hosting.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 21 | `SHOULD` | For security and isolation, the SDK should run inside a sandboxed container environment | `hosting.md` | `partial` | .env / env.example secret scan |
| 22 | `SHOULD` | ### When should I shut down idle containers vs | `hosting.md` | `unmapped` | _unmapped_ |
| 23 | `SHOULD` | ### How often should I update the Claude Code CLI? | `hosting.md` | `unmapped` | _unmapped_ |
| 24 | `REQUIRED` | * **Missing environment variables**: Ensure required tokens and credentials are set | `mcp.md` | `partial` | .env / env.example secret scan |
| 25 | `REQUIRED` | allowedTools: ["mcp__servername__*"] // Required for Claude to use the tools | `mcp.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 26 | `SHOULD NOT` | cations, test environments, and multi-tenant systems where local customizations should not leak in | `migration-guide.md` | `partial` | .env / env.example secret scan |
| 27 | `REQUIRED` | That's it! No other code changes are required | `migration-guide.md` | `unmapped` | _unmapped_ |
| 28 | `MUST` | afety** \| Maintained \| Maintained \| Maintained \| Must be added \| | `modifying-system-prompts.md` | `unmapped` | _unmapped_ |
| 29 | `MUST` | t context** \| Automatic \| Automatic \| Automatic \| Must be provided \| | `modifying-system-prompts.md` | `unmapped` | _unmapped_ |
| 30 | `SHOULD` | * **Team-shared context** - Guidelines everyone should follow | `modifying-system-prompts.md` | `unmapped` | _unmapped_ |
| 31 | `SHOULD` | * **Long-term memory** - Context that should persist across all sessions | `modifying-system-prompts.md` | `unmapped` | _unmapped_ |
| 32 | `SHOULD` | * Team-shared context that should be version controlled | `modifying-system-prompts.md` | `partial` | plugin.json `version` semver check |
| 33 | `SHOULD` | * "All API endpoints should use async/await patterns" | `modifying-system-prompts.md` | `unmapped` | _unmapped_ |
| 34 | `REQUIRED` | # Required for traces, which are in beta | `observability.md` | `unmapped` | _unmapped_ |
| 35 | `REQUIRED` | // Required for traces, which are in beta | `observability.md` | `unmapped` | _unmapped_ |
| 36 | `SHOULD` | Your product should maintain its own branding and not appear to be Claude Code or any Anthropic product | `overview.md` | `unmapped` | _unmapped_ |
| 37 | `MUST` | The `type` field must be `"local"`, the only value the SDK accepts | `plugins.md` | `unmapped` | _unmapped_ |
| 38 | `MUST` | A plugin directory must contain a ` | `plugins.md` | `unmapped` | _unmapped_ |
| 39 | `SHOULD` | The path should point to the plugin's root directory (the directory containing ` | `plugins.md` | `unmapped` | _unmapped_ |
| 40 | `REQUIRED` | json # Required: plugin manifest | `plugins.md` | `unmapped` | _unmapped_ |
| 41 | `MUST` | Must be non-empty after stripping whitespace \| | `python.md` | `unmapped` | _unmapped_ |
| 42 | `MUST` | You must drain them with `receive_response()` before reading the response to a new query | `python.md` | `unmapped` | _unmapped_ |
| 43 | `MUST` | Custom implementations must be updated to match any interface changes | `python.md` | `unmapped` | _unmapped_ |
| 44 | `MUST` | \| `type` \| Yes \| Must be `"json_schema"` for JSON Schema validation \| | `python.md` | `unmapped` | _unmapped_ |
| 45 | `MUST` | \| `type` \| Yes \| Must be `"preset"` to use a preset system prompt | `python.md` | `unmapped` | _unmapped_ |
| 46 | `MUST` | \| `preset` \| Yes \| Must be `"claude_code"` to use Claude Code's system prompt | `python.md` | `unmapped` | _unmapped_ |
| 47 | `MUST` | \| `behavior` \| `Literal["allow"]` \| `"allow"` \| Must be "allow" \| | `python.md` | `unmapped` | _unmapped_ |
| 48 | `MUST` | \| `behavior` \| `Literal["deny"]` \| `"deny"` \| Must be "deny" \| | `python.md` | `unmapped` | _unmapped_ |
| 49 | `MUST` | \| `type` \| `Literal["local"]` \| Must be `"local"` (only local plugins currently supported) \| | `python.md` | `unmapped` | _unmapped_ |
| 50 | `SHOULD NOT` | All fields are optional hints; clients should not rely on them for security decisions | `python.md` | `unmapped` | _unmapped_ |
| 51 | `SHOULD` | query("Should we be concerned about these readings?") | `python.md` | `unmapped` | _unmapped_ |
| 52 | `SHOULD` | Result indicating the tool call should be allowed | `python.md` | `unmapped` | _unmapped_ |
| 53 | `SHOULD` | Result indicating the tool call should be denied | `python.md` | `unmapped` | _unmapped_ |
| 54 | `REQUIRED` | "required": ["text"], | `python.md` | `unmapped` | _unmapped_ |
| 55 | `REQUIRED` | \| `session_id` \| `str` \| required \| The session ID to retrieve messages for \| | `python.md` | `unmapped` | _unmapped_ |
| 56 | `REQUIRED` | \| `session_id` \| `str` \| required \| UUID of the session to look up \| | `python.md` | `unmapped` | _unmapped_ |
| 57 | `REQUIRED` | \| `session_id` \| `str` \| required \| UUID of the session to rename \| | `python.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 58 | `REQUIRED` | \| `title` \| `str` \| required \| New title | `python.md` | `unmapped` | _unmapped_ |
| 59 | `REQUIRED` | \| `session_id` \| `str` \| required \| UUID of the session to tag \| | `python.md` | `unmapped` | _unmapped_ |
| 60 | `REQUIRED` | \| `tag` \| `str \| None` \| required \| Tag string, or `None` to clear | `python.md` | `unmapped` | _unmapped_ |
| 61 | `REQUIRED` | \| Field \| Required \| Description \| | `python.md` | `partial` | description length recommendation |
| 62 | `REQUIRED` | \| Field \| Required \| Description | `python.md` | `partial` | description length recommendation |
| 63 | `REQUIRED` | els/overview), which include 1M context at standard pricing with no beta header required | `python.md` | `unmapped` | _unmapped_ |
| 64 | `REQUIRED` | # Required: dummy hook keeps the stream open for can_use_tool | `python.md` | `unmapped` | _unmapped_ |
| 65 | `REQUIRED` | When needed, you can restrict the agent to only the capabilities required for its specific task: | `secure-deployment.md` | `unmapped` | _unmapped_ |
| 66 | `REQUIRED` | e is simplicity: no Docker configuration, container images, or networking setup required | `secure-deployment.md` | `unmapped` | _unmapped_ |
| 67 | `MUST` | The client must be used as an async context manager | `sessions.md` | `unmapped` | _unmapped_ |
| 68 | `MUST` | The `cwd` must match | `sessions.md` | `unmapped` | _unmapped_ |
| 69 | `SHOULD` | Session management comes into play when you send multiple prompts that should share context | `sessions.md` | `unmapped` | _unmapped_ |
| 70 | `REQUIRED` | Required when you have multiple sessions (for example, one per user in a multi-user app) or want to return to one that isn't the most recent | `sessions.md` | `unmapped` | _unmapped_ |
| 71 | `REQUIRED` | No ID tracking required | `sessions.md` | `unmapped` | _unmapped_ |
| 72 | `MUST` | Unlike subagents (which can be defined programmatically), Skills must be created as filesystem artifacts | `skills.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 73 | `MUST` | cwd="/path/to/project", # Must contain | `skills.md` | `unmapped` | _unmapped_ |
| 74 | `MUST` | cwd: "/path/to/project", // Must contain | `skills.md` | `unmapped` | _unmapped_ |
| 75 | `REQUIRED` | required: ["company_name"] | `structured-outputs.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 76 | `REQUIRED` | "required": ["company_name"], | `structured-outputs.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 77 | `REQUIRED` | l basic types (object, array, string, number, boolean, null), `enum`, `const`, `required`, nested objects, and `$ref` definitions | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 78 | `REQUIRED` | required: ["text", "file", "line"] | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 79 | `REQUIRED` | required: ["todos", "total_count"] | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 80 | `REQUIRED` | "required": ["text", "file", "line"], | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 81 | `REQUIRED` | "required": ["todos", "total_count"], | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 82 | `REQUIRED` | ** Deeply nested schemas with many required fields are harder to satisfy | `structured-outputs.md` | `unmapped` | _unmapped_ |
| 83 | `MUST` | The `Agent` tool must be included in `allowedTools` since Claude invokes subagents through the Agent tool | `subagents.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 84 | `MUST` | You must resume the same session to access the subagent's transcript | `subagents.md` | `unmapped` | _unmapped_ |
| 85 | `MUST` | **Include the Agent tool**: subagents are invoked via the Agent tool, so it must be in `allowedTools` | `subagents.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 86 | `SHOULD` | Write clear descriptions that explain when the subagent should be used, and Claude will automatically delegate appropriate tasks | `subagents.md` | `partial` | description length recommendation |
| 87 | `SHOULD` | **Write a clear description**: explain exactly when the subagent should be used so Claude can match tasks appropriately | `subagents.md` | `partial` | description length recommendation |
| 88 | `REQUIRED` | # Agent tool is required for subagent invocation | `subagents.md` | `unmapped` | _unmapped_ |
| 89 | `REQUIRED` | // Agent tool is required for subagent invocation | `subagents.md` | `unmapped` | _unmapped_ |
| 90 | `REQUIRED` | eld \| Type \| Required \| Description \| | `subagents.md` | `partial` | description length recommendation |
| 91 | `MUST` | Must be non-empty after trimming whitespace \| | `typescript.md` | `unmapped` | _unmapped_ |
| 92 | `MUST` | The agent must be defined in the `agents` option or in settings | `typescript.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 93 | `MUST` | \| `type` \| `'local'` \| Must be `'local'` (only local plugins currently supported) \| | `typescript.md` | `unmapped` | _unmapped_ |
| 94 | `SHOULD NOT` | All fields are optional hints; clients should not rely on them for security decisions | `typescript.md` | `unmapped` | _unmapped_ |
| 95 | `SHOULD` | \| `AbortSignal` \| Signaled if the operation should be aborted | `typescript.md` | `unmapped` | _unmapped_ |
| 96 | `REQUIRED` | \| `sessionId` \| `string` \| required \| Session UUID to read (see `listSessions()`) \| | `typescript.md` | `unmapped` | _unmapped_ |
| 97 | `REQUIRED` | \| `sessionId` \| `string` \| required \| UUID of the session to look up \| | `typescript.md` | `unmapped` | _unmapped_ |
| 98 | `REQUIRED` | \| `sessionId` \| `string` \| required \| UUID of the session to rename \| | `typescript.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 99 | `REQUIRED` | \| `title` \| `string` \| required \| New title | `typescript.md` | `unmapped` | _unmapped_ |
| 100 | `REQUIRED` | \| `sessionId` \| `string` \| required \| UUID of the session to tag \| | `typescript.md` | `unmapped` | _unmapped_ |
| 101 | `REQUIRED` | \| `tag` \| `string \| null` \| required \| Tag string, or `null` to clear \| | `typescript.md` | `unmapped` | _unmapped_ |
| 102 | `REQUIRED` | Required when using `permissionMode: 'bypassPermissions'` | `typescript.md` | `partial` | agent permissionMode enum |
| 103 | `REQUIRED` | \| Field \| Required \| Description | `typescript.md` | `partial` | description length recommendation |
| 104 | `REQUIRED` | Replayed user message with required UUID | `typescript.md` | `unmapped` | _unmapped_ |
| 105 | `REQUIRED` | els/overview), which include 1M context at standard pricing with no beta header required | `typescript.md` | `unmapped` | _unmapped_ |
| 106 | `MUST` | // Must create an async iterable to feed messages | `typescript-v2-preview.md` | `unmapped` | _unmapped_ |
| 107 | `MUST` | // Must coordinate when to yield next message | `typescript-v2-preview.md` | `unmapped` | _unmapped_ |
| 108 | `MUST` | // Return the answers to Claude (must include original questions) | `user-input.md` | `unmapped` | _unmapped_ |
| 109 | `SHOULD` | "question": "How should I format the output?", | `user-input.md` | `unmapped` | _unmapped_ |
| 110 | `SHOULD` | "question": "Which sections should I include?", | `user-input.md` | `unmapped` | _unmapped_ |
| 111 | `SHOULD` | , `"How should I format the output?"`) \| Key \| | `user-input.md` | `unmapped` | _unmapped_ |
| 112 | `SHOULD` | "How should I format the output?": "Summary", | `user-input.md` | `unmapped` | _unmapped_ |
| 113 | `SHOULD` | "Which sections should I include?": ["Introduction", "Conclusion"], | `user-input.md` | `unmapped` | _unmapped_ |
| 114 | `SHOULD` | "Which sections should I include?": "Introduction, Conclusion" | `user-input.md` | `unmapped` | _unmapped_ |
| 115 | `SHOULD` | "Which sections should I include?": ["Introduction", "Conclusion"] | `user-input.md` | `unmapped` | _unmapped_ |
| 116 | `REQUIRED` | # Required workaround: dummy hook keeps the stream open for can_use_tool | `user-input.md` | `unmapped` | _unmapped_ |
| 117 | `REQUIRED` | \| `questions` \| Pass through the original questions array (required for tool processing) \| | `user-input.md` | `unmapped` | _unmapped_ |
| 118 | `SHOULD NOT` | Teammates should not run cleanup because their team context may not resolve correctly, potentially leaving resources in an inconsistent state | `agent-teams.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 119 | `REQUIRED` | Works in any terminal, no extra setup required | `agent-teams.md` | `unmapped` | _unmapped_ |
| 120 | `MUST` | aws` and must directly return credentials | `amazon-bedrock.md` | `unmapped` | _unmapped_ |
| 121 | `MUST` | The command must output JSON in this format: | `amazon-bedrock.md` | `unmapped` | _unmapped_ |
| 122 | `REQUIRED` | First-time users of Anthropic models are required to submit use case details before invoking a model | `amazon-bedrock.md` | `unmapped` | _unmapped_ |
| 123 | `REQUIRED` | * `AWS_REGION` is a required environment variable | `amazon-bedrock.md` | `partial` | .env / env.example secret scan |
| 124 | `REQUIRED` | Create an IAM policy with the required permissions for Claude Code: | `amazon-bedrock.md` | `unmapped` | _unmapped_ |
| 125 | `MUST` | A GitHub admin must install the GitHub app | `analytics.md` | `unmapped` | _unmapped_ |
| 126 | `REQUIRED` | * **"GitHub app required"**: install the GitHub app to view contribution metrics | `analytics.md` | `unmapped` | _unmapped_ |
| 127 | `MUST` | Your admin must have [invited you](#claude-console-authentication) first | `authentication.md` | `unmapped` | _unmapped_ |
| 128 | `REQUIRED` | ](/en/google-vertex-ai), or [Microsoft Foundry](/en/microsoft-foundry), set the required environment variables before running `claude` | `authentication.md` | `partial` | .env / env.example secret scan |
| 129 | `MUST` | For actions that must never run regardless of user intent or classifier configuration, use `permissions | `auto-mode-config.md` | `unmapped` | _unmapped_ |
| 130 | `MUST` | ironment that the defaults miss, or to `hard_deny` for security boundaries that must never be crossed | `auto-mode-config.md` | `unmapped` | _unmapped_ |
| 131 | `SHOULD` | * **Cloud providers and trusted buckets**: bucket names or prefixes that Claude should be able to read from and write to | `auto-mode-config.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 132 | `SHOULD` | nant infrastructure, or compliance requirements that affect what the classifier should treat as risky | `auto-mode-config.md` | `unmapped` | _unmapped_ |
| 133 | `MUST` | , "IMPORTANT" or "YOU MUST") to improve adherence | `best-practices.md` | `unmapped` | _unmapped_ |
| 134 | `MUST` | Use hooks for actions that must happen every time with zero exceptions | `best-practices.md` | `partial` | hook event/type validation (Phase 12+) |
| 135 | `SHOULD` | refresh, and whether we have any existing OAuth utilities I should reuse | `best-practices.md` | `unmapped` | _unmapped_ |
| 136 | `SHOULD` | Sometimes you *should* let context accumulate because you're deep in one complex problem and the history is valuable | `best-practices.md` | `unmapped` | _unmapped_ |
| 137 | `SHOULD` | Sometimes you should skip planning and let Claude figure it out because the task is exploratory | `best-practices.md` | `unmapped` | _unmapped_ |
| 138 | `REQUIRED` | There's no required format for CLAUDE | `best-practices.md` | `unmapped` | _unmapped_ |
| 139 | `REQUIRED` | \| Developer environment quirks (required env vars) \| File-by-file descriptions of the codebase \| | `best-practices.md` | `partial` | description length recommendation |
| 140 | `REQUIRED` | No special prompting required: ask questions directly | `best-practices.md` | `unmapped` | _unmapped_ |
| 141 | `SHOULD NOT` | tion's deployment and data-handling policy is already configured, and champions should not improvise this answer | `champion-kit.md` | `unmapped` | _unmapped_ |
| 142 | `SHOULD NOT` | md` file, then add your conventions, test commands, and any directories that should not be modified | `champion-kit.md` | `partial` | command frontmatter validation (Phase 12+) |
| 143 | `SHOULD` | ### What this should cost you | `champion-kit.md` | `unmapped` | _unmapped_ |
| 144 | `SHOULD` | activities below are intended to fit inside a normal working week, and the role should remain a multiplier on your existing work rather than an additional support responsibility | `champion-kit.md` | `unmapped` | _unmapped_ |
| 145 | `SHOULD` | \| "What should I try it on first?" \| Recommend a real but contained task, ideally a bug or chore the person has been postponing because it is tedious rather | `champion-kit.md` | `unmapped` | _unmapped_ |
| 146 | `SHOULD` | le, then adding the team's conventions, test commands, and any directories that should be avoided | `champion-kit.md` | `partial` | command frontmatter validation (Phase 12+) |
| 147 | `SHOULD` | When a colleague moves past "should I try this" into "how do I become effective with it," point them to the [Quickstart](/en/quickstart) and [Common workflows](/en/common-workflows) pages | `champion-kit.md` | `unmapped` | _unmapped_ |
| 148 | `SHOULD` | Healthy skepticism is expected; engineers should be cautious about tools that touch their code | `champion-kit.md` | `unmapped` | _unmapped_ |
| 149 | `SHOULD` | " \| Agree that no change should land unread | `champion-kit.md` | `unmapped` | _unmapped_ |
| 150 | `REQUIRED` | \| Effort required \| | `champion-kit.md` | `unmapped` | _unmapped_ |
| 151 | `REQUIRED` | hat did Claude help you with this week?" No preparation, slides, or meeting are required; screenshots and short descriptions are sufficient | `champion-kit.md` | `partial` | description length recommendation |
| 152 | `MUST` | orks with console (API key) authentication — console orgs with managed settings must set `channelsEnabled: true` to enable | `changelog.md` | `unmapped` | _unmapped_ |
| 153 | `MUST` | * Fixed a bug with subagents and MCP servers related to "Tool names must be unique" error | `changelog.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 154 | `MUST` | * Fixed a bug where `/context` would sometimes fail with "max\_tokens must be greater than thinking | `changelog.md` | `unmapped` | _unmapped_ |
| 155 | `SHOULD` | n multiple servers refresh concurrently — users with several remote MCP servers should no longer need daily re-authentication | `changelog.md` | `unmapped` | _unmapped_ |
| 156 | `SHOULD` | * Plugin manifests: `themes` and `monitors` should now be declared under `"experimental": { | `changelog.md` | `partial` | monitors field (Phase 16+) |
| 157 | `SHOULD` | json` - administrators should migrate to `C:\Program Files\ClaudeCode\managed-settings | `changelog.md` | `unmapped` | _unmapped_ |
| 158 | `SHOULD` | s: Added model customization support - you can now specify which model an agent should use | `changelog.md` | `unmapped` | _unmapped_ |
| 159 | `SHOULD` | change: Bedrock ARN passed to `ANTHROPIC_MODEL` or `ANTHROPIC_SMALL_FAST_MODEL` should no longer contain an escaped slash (specify `/` instead of `%2F`) | `changelog.md` | `unmapped` | _unmapped_ |
| 160 | `REQUIRED` | * Windows: Git for Windows (Git Bash) is no longer required — when absent, Claude Code uses PowerShell as the shell tool | `changelog.md` | `unmapped` | _unmapped_ |
| 161 | `REQUIRED` | * Fixed agent-type hooks failing with "Messages are required for agent hooks" when configured for events other than `Stop` or `SubagentStop` | `changelog.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 162 | `REQUIRED` | * Fixed compacting a resumed long-context session failing with "Extra usage is required for long context requests" | `changelog.md` | `unmapped` | _unmapped_ |
| 163 | `REQUIRED` | allowWrite` not working with absolute paths (previously required `//` prefix) | `changelog.md` | `unmapped` | _unmapped_ |
| 164 | `REQUIRED` | 6 by default for Max, Team, and Enterprise plans (previously required extra usage) | `changelog.md` | `unmapped` | _unmapped_ |
| 165 | `REQUIRED` | * Fixed "media\_type: Field required" API error when receiving images via Remote Control | `changelog.md` | `unmapped` | _unmapped_ |
| 166 | `REQUIRED` | Previously this required setting `CLAUDE_BASH_NO_LOGIN=true` | `changelog.md` | `unmapped` | _unmapped_ |
| 167 | `REQUIRED` | * Bedrock: Display awsAuthRefresh output when auth is required | `changelog.md` | `unmapped` | _unmapped_ |
| 168 | `REQUIRED` | * Settings file changes take effect immediately - no restart required | `changelog.md` | `unmapped` | _unmapped_ |
| 169 | `MUST` | Team and Enterprise organizations must [explicitly enable them](#enterprise-controls) | `channels.md` | `unmapped` | _unmapped_ |
| 170 | `MUST` | * **Team, Enterprise, or managed Console org**: your admin must [enable channels](#enterprise-controls) in managed settings | `channels.md` | `unmapped` | _unmapped_ |
| 171 | `MUST` | Must be `true` for any channel to deliver messages | `channels.md` | `unmapped` | _unmapped_ |
| 172 | `MUST` | Team and Enterprise organizations must [explicitly enable them](/en/channels#enterprise-controls) | `channels-reference.md` | `unmapped` | _unmapped_ |
| 173 | `MUST` | During the research preview, every channel must be on the [approved allowlist](/en/channels#research-preview) to register | `channels-reference.md` | `unmapped` | _unmapped_ |
| 174 | `MUST` | Keys must be identifiers: letters, digits, and underscores only | `channels-reference.md` | `unmapped` | _unmapped_ |
| 175 | `SHOULD` | Claude what events to expect, whether to reply, and how to route replies if it should | `channels-reference.md` | `unmapped` | _unmapped_ |
| 176 | `REQUIRED` | experimental['claude/channel']` \| `object` \| Required | `channels-reference.md` | `unmapped` | _unmapped_ |
| 177 | `REQUIRED` | required: ['chat_id', 'text'], | `channels-reference.md` | `unmapped` | _unmapped_ |
| 178 | `FORBIDDEN` | has(sender)) return new Response('forbidden', { status: 403 }) | `channels-reference.md` | `unmapped` | _unmapped_ |
| 179 | `MUST` | Bundled repositories must meet these limits: | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 180 | `MUST` | * The directory must be a git repository with at least one commit | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 181 | `MUST` | * The bundled repository must be under 100 MB | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 182 | `MUST` | \| Clean git state \| Your working directory must have no uncommitted changes | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 183 | `MUST` | \| Correct repository \| You must run `--teleport` from a checkout of the same repository, not a fork | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 184 | `MUST` | \| Branch available \| The branch from the cloud session must have been pushed to the remote | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 185 | `MUST` | \| Same account \| You must be authenticated to the same claude | `claude-code-on-the-web.md` | `unmapped` | _unmapped_ |
| 186 | `SHOULD` | Use a [SessionStart hook](/en/hooks#sessionstart) for project setup that should run everywhere, cloud and local, like `npm install` | `claude-code-on-the-web.md` | `partial` | hook event/type validation (Phase 12+) |
| 187 | `REQUIRED` | The GitHub App is required for [Auto-fix](#auto-fix-pull-requests), which uses the App to receive PR webhooks | `claude-code-on-the-web.md` | `partial` | hook event/type validation (Phase 12+) |
| 188 | `MUST` | - All endpoints must validate input with Zod schemas | `claude-directory.md` | `unmapped` | _unmapped_ |
| 189 | `MUST` | Every finding must include a concrete fix | `claude-directory.md` | `unmapped` | _unmapped_ |
| 190 | `SHOULD` | It lists the build and test commands, the framework conventions Claude should follow, and project-specific rules like export style and file layout | `claude-directory.md` | `partial` | command frontmatter validation (Phase 12+) |
| 191 | `SHOULD` | - Use descriptive test names: "should [expected] when [condition]" | `claude-directory.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 192 | `SHOULD` | If a skill and command share a name, the skill takes precedence', 'New commands should usually be skills instead; commands remain supported'], | `claude-directory.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 193 | `MUST` | \| Use a specific session ID for the conversation (must be a valid UUID) | `cli-reference.md` | `unmapped` | _unmapped_ |
| 194 | `SHOULD` | esearch preview) MCP servers whose [channel](/en/channels) notifications Claude should listen for in this session | `cli-reference.md` | `unmapped` | _unmapped_ |
| 195 | `MUST` | * You must have owner, member, or collaborator access to the repository | `code-review.md` | `unmapped` | _unmapped_ |
| 196 | `MUST` | * The PR must be open | `code-review.md` | `unmapped` | _unmapped_ |
| 197 | `MUST` | specific checks**: add rules you want flagged on every PR, like "new API routes must have an integration test | `code-review.md` | `unmapped` | _unmapped_ |
| 198 | `SHOULD` | \| 🔴 \| Important \| A bug that should be fixed before merging \| | `code-review.md` | `unmapped` | _unmapped_ |
| 199 | `SHOULD` | *Skip rules**: list paths, branch patterns, and finding categories where Claude should post no findings | `code-review.md` | `unmapped` | _unmapped_ |
| 200 | `REQUIRED` | In the table below, `<arg>` indicates a required argument and `[arg]` indicates an optional one | `commands.md` | `unmapped` | _unmapped_ |
| 201 | `SHOULD` | How should we modify it for the new feature? | `common-workflows.md` | `unmapped` | _unmapped_ |
| 202 | `SHOULD` | \| Anthropic-managed infrastructure \| Tasks that should run even when your computer is off | `common-workflows.md` | `unmapped` | _unmapped_ |
| 203 | `SHOULD` | \| Tasks tied to repo events like opened PRs, or cron schedules that should live alongside your workflow config | `common-workflows.md` | `unmapped` | _unmapped_ |
| 204 | `SHOULD` | \| "What should I try first?" \| A bug you've been putting off because it's tedious | `communications-kit.md` | `unmapped` | _unmapped_ |
| 205 | `REQUIRED` | Same agent, no terminal required | `communications-kit.md` | `unmapped` | _unmapped_ |
| 206 | `REQUIRED` | They stand alone with no required order | `communications-kit.md` | `unmapped` | _unmapped_ |
| 207 | `MUST` | If a rule must persist across compaction, drop the `paths:` frontmatter or move it to the project-root CLAUDE | `context-window.md` | `unmapped` | _unmapped_ |
| 208 | `MUST` | ZDR is enabled on a per-organization basis; each new organization must have ZDR enabled separately by your account team | `data-usage.md` | `unmapped` | _unmapped_ |
| 209 | `MUST` | <br />`CLAUDE_CODE_USE_VERTEX` must be 1 | `data-usage.md` | `unmapped` | _unmapped_ |
| 210 | `MUST` | <br />`CLAUDE_CODE_USE_BEDROCK` must be 1 | `data-usage.md` | `unmapped` | _unmapped_ |
| 211 | `MUST` | <br />`CLAUDE_CODE_USE_FOUNDRY` must be 1 | `data-usage.md` | `unmapped` | _unmapped_ |
| 212 | `REQUIRED` | Solid lines indicate required connections, while dashed lines represent optional or user-initiated data flows | `data-usage.md` | `unmapped` | _unmapped_ |
| 213 | `MUST` | " Use permissions or hooks for security boundaries and anything that must never happen, where you need a guarantee instead of guidance | `debug-your-config.md` | `partial` | hook event/type validation (Phase 12+) |
| 214 | `MUST` | The platform that displays the link must allow custom URL schemes | `deep-links.md` | `unmapped` | _unmapped_ |
| 215 | `MUST` | The platform that renders the runbook must allow custom URL schemes | `deep-links.md` | `unmapped` | _unmapped_ |
| 216 | `MUST` | The prompt is part of the URL and must be URL-encoded | `deep-links.md` | `unmapped` | _unmapped_ |
| 217 | `MUST` | Auto-merge must be [enabled in your GitHub repository settings](https://docs | `desktop.md` | `unmapped` | _unmapped_ |
| 218 | `MUST` | The Claude Desktop app must be running | `desktop.md` | `unmapped` | _unmapped_ |
| 219 | `MUST` | Use this when your server must use a specific port, such as for OAuth callbacks or CORS allowlists | `desktop.md` | `unmapped` | _unmapped_ |
| 220 | `MUST` | The remote machine must run Linux or macOS | `desktop.md` | `unmapped` | _unmapped_ |
| 221 | `REQUIRED` | On Windows, Git is required for the Code tab to work: [download Git for Windows](https://git-scm | `desktop.md` | `unmapped` | _unmapped_ |
| 222 | `REQUIRED` | On Windows, Git is required for the Code tab to start local sessions | `desktop.md` | `unmapped` | _unmapped_ |
| 223 | `REQUIRED` | If you see "Git is required," install [Git for Windows](https://git-scm | `desktop.md` | `unmapped` | _unmapped_ |
| 224 | `REQUIRED` | If you see "Git LFS is required by this repository but is not installed," install Git LFS from [git-lfs | `desktop.md` | `unmapped` | _unmapped_ |
| 225 | `FORBIDDEN` | If you see `Error 403: Forbidden` or other authentication failures when using the Code tab: | `desktop.md` | `unmapped` | _unmapped_ |
| 226 | `MUST` | com/downloads/win) must be installed for local sessions to work | `desktop-quickstart.md` | `unmapped` | _unmapped_ |
| 227 | `REQUIRED` | No terminal required | `desktop-quickstart.md` | `unmapped` | _unmapped_ |
| 228 | `MUST` | Must be unique across your tasks | `desktop-scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 229 | `SHOULD` | Use **cloud tasks** for work that should run reliably without your machine | `desktop-scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 230 | `SHOULD` | \| Instructions \| What Claude should do when the task runs | `desktop-scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 231 | `SHOULD` | For tasks that need to run even when your computer is off, or that should trigger on an API call or GitHub event, create a remote [routine](/en/routines) instead | `desktop-scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 232 | `REQUIRED` | A folder is required before you can save the task | `desktop-scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 233 | `MUST` | y default, the container's home directory is discarded on rebuild, so engineers must sign in again each time | `devcontainer.md` | `unmapped` | _unmapped_ |
| 234 | `REQUIRED` | The firewall script and these capabilities are not required for Claude Code itself: you can leave them out and rely on your own network controls instead | `devcontainer.md` | `unmapped` | _unmapped_ |
| 235 | `REQUIRED` | None of them are required when you add Claude Code to your own dev container through the feature, but they show one way to combine the pieces | `devcontainer.md` | `unmapped` | _unmapped_ |
| 236 | `REQUIRED` | \| Language \| Plugin \| Binary required \| | `discover-plugins.md` | `unmapped` | _unmapped_ |
| 237 | `REQUIRED` | nd in $PATH` in the `/plugin` Errors tab after installing a plugin, install the required binary from the table above | `discover-plugins.md` | `unmapped` | _unmapped_ |
| 238 | `MUST` | m-deferral) always block startup regardless of this variable, since their tools must be present when the first prompt is built | `env-vars.md` | `unmapped` | _unmapped_ |
| 239 | `SHOULD NOT` | Useful for container or CI sessions that should not load operator-provisioned skills | `env-vars.md` | `partial` | skill frontmatter validation (Phase 12+) |
| 240 | `SHOULD NOT` | Useful for managed deployments where users should not run installation diagnostics | `env-vars.md` | `unmapped` | _unmapped_ |
| 241 | `SHOULD NOT` | Use when distributing Claude Code through your own channels and users should not self-update | `env-vars.md` | `unmapped` | _unmapped_ |
| 242 | `SHOULD` | PER_TTL_MS` \| Interval in milliseconds at which credentials should be refreshed (when using [`apiKeyHelper`](/en/settings#available-settings)) | `env-vars.md` | `unmapped` | _unmapped_ |
| 243 | `SHOULD` | Use this if scrolling in fullscreen mode shows blank regions where messages should appear | `env-vars.md` | `unmapped` | _unmapped_ |
| 244 | `SHOULD` | Opt-in for environments where the proxy should handle hostname resolution | `env-vars.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 245 | `REQUIRED` | Required if `ANTHROPIC_FOUNDRY_BASE_URL` is not set (see [Microsoft Foundry](/en/microsoft-foundry)) | `env-vars.md` | `unmapped` | _unmapped_ |
| 246 | `REQUIRED` | Required before configuring OTel exporters | `env-vars.md` | `unmapped` | _unmapped_ |
| 247 | `REQUIRED` | Required when `CLAUDE_CODE_OAUTH_REFRESH_TOKEN` is set | `env-vars.md` | `unmapped` | _unmapped_ |
| 248 | `MUST` | \| `max_tokens must be greater than thinking | `errors.md` | `unmapped` | _unmapped_ |
| 249 | `MUST` | max_tokens must be greater than thinking | `errors.md` | `unmapped` | _unmapped_ |
| 250 | `REQUIRED` | com`, or a required corporate proxy that is not configured | `errors.md` | `unmapped` | _unmapped_ |
| 251 | `FORBIDDEN` | * For other failures including `403 Forbidden` and OAuth browser issues, see [Login and authentication](/en/troubleshoot-install#login-and-authentication) | `errors.md` | `unmapped` | _unmapped_ |
| 252 | `MUST` | * **Extra usage enabled**: your account must have extra usage enabled, which allows billing beyond your plan's included usage | `fast-mode.md` | `unmapped` | _unmapped_ |
| 253 | `MUST` | For Team and Enterprise, an admin must enable extra usage for the organization | `fast-mode.md` | `unmapped` | _unmapped_ |
| 254 | `MUST` | An admin must explicitly [enable fast mode](#enable-fast-mode-for-your-organization) before users can access it | `fast-mode.md` | `unmapped` | _unmapped_ |
| 255 | `MUST` | Script, HTTP request, prompt, or subagent triggered by events \| Automation that must run on every matching event \| Run ESLint after every file edit \| | `features-overview.md` | `unmapped` | _unmapped_ |
| 256 | `MUST` | **Use a hook** when the action must happen the same way every time and doesn't need Claude to think | `features-overview.md` | `unmapped` | _unmapped_ |
| 257 | `MUST` | If a rule must hold every time, make it a hook rather than a prompt instruction | `features-overview.md` | `unmapped` | _unmapped_ |
| 258 | `SHOULD` | md** if Claude should always know it: coding conventions, build commands, project structure, "never do X" rules | `features-overview.md` | `partial` | command frontmatter validation (Phase 12+) |
| 259 | `SHOULD` | **Use a skill** when Claude should decide how to apply the steps, or when the content is knowledge rather than a script | `features-overview.md` | `unmapped` | _unmapped_ |
| 260 | `REQUIRED` | The same setting is also required for click-to-expand and text selection to work | `fullscreen.md` | `unmapped` | _unmapped_ |
| 261 | `MUST` | * You must be a repository admin to install the GitHub app and add secrets | `github-actions.md` | `unmapped` | _unmapped_ |
| 262 | `MUST` | All beta users must make these changes to their workflow files in order to upgrade: | `github-actions.md` | `unmapped` | _unmapped_ |
| 263 | `SHOULD` | @claude how should I implement user authentication for this endpoint? | `github-actions.md` | `unmapped` | _unmapped_ |
| 264 | `REQUIRED` | This command will guide you through setting up the GitHub app and required secrets | `github-actions.md` | `unmapped` | _unmapped_ |
| 265 | `REQUIRED` | A service account with the required permissions | `github-actions.md` | `unmapped` | _unmapped_ |
| 266 | `REQUIRED` | Set the required permissions: | `github-actions.md` | `unmapped` | _unmapped_ |
| 267 | `REQUIRED` | urity Note**: Use repository-specific configurations and grant only the minimum required permissions | `github-actions.md` | `unmapped` | _unmapped_ |
| 268 | `REQUIRED` | **Required Setup**: | `github-actions.md` | `unmapped` | _unmapped_ |
| 269 | `REQUIRED` | * For cross-region models, request access in all required regions | `github-actions.md` | `unmapped` | _unmapped_ |
| 270 | `REQUIRED` | **Required Values**: | `github-actions.md` | `unmapped` | _unmapped_ |
| 271 | `REQUIRED` | <Step title="Add Required Secrets"> | `github-actions.md` | `unmapped` | _unmapped_ |
| 272 | `REQUIRED` | **Required GitHub secrets:** | `github-actions.md` | `unmapped` | _unmapped_ |
| 273 | `REQUIRED` | \| Description \| Required \| | `github-actions.md` | `partial` | description length recommendation |
| 274 | `REQUIRED` | \*\*Required for direct Claude API, not for Bedrock/Vertex | `github-actions.md` | `unmapped` | _unmapped_ |
| 275 | `REQUIRED` | Create your own GitHub App with required permissions (contents, issues, pull requests) and use the actions/create-github-app-token action to generate tokens in your workflows | `github-actions.md` | `unmapped` | _unmapped_ |
| 276 | `MUST` | Your GHES instance must be reachable from Anthropic infrastructure so Claude can clone repositories and post review comments | `github-enterprise-server.md` | `unmapped` | _unmapped_ |
| 277 | `REQUIRED` | \| Metadata \| Read \| Required by GitHub for all apps \| | `github-enterprise-server.md` | `unmapped` | _unmapped_ |
| 278 | `REQUIRED` | **Required setup:** | `gitlab-ci-cd.md` | `unmapped` | _unmapped_ |
| 279 | `REQUIRED` | **Required values to store in CI/CD variables:** | `gitlab-ci-cd.md` | `unmapped` | _unmapped_ |
| 280 | `REQUIRED` | A dedicated service account with only the required Vertex AI roles | `gitlab-ci-cd.md` | `unmapped` | _unmapped_ |
| 281 | `REQUIRED` | **Required CI/CD variables:** | `gitlab-ci-cd.md` | `unmapped` | _unmapped_ |
| 282 | `REQUIRED` | * `ANTHROPIC_API_KEY`: Required for the Claude API (not used for Bedrock/Vertex) | `gitlab-ci-cd.md` | `unmapped` | _unmapped_ |
| 283 | `MUST` | Agent teams are experimental and must be enabled by setting `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | `glossary.md` | `unmapped` | _unmapped_ |
| 284 | `MUST` | step away, as opposed to chat-based assistants that only respond with text you must apply yourself | `glossary.md` | `unmapped` | _unmapped_ |
| 285 | `REQUIRED` | h) is disabled by default on Vertex AI because the endpoint does not accept the required beta header | `google-vertex-ai.md` | `unmapped` | _unmapped_ |
| 286 | `REQUIRED` | Assign the required IAM permissions: | `google-vertex-ai.md` | `unmapped` | _unmapped_ |
| 287 | `REQUIRED` | user` role includes the required permissions: | `google-vertex-ai.md` | `unmapped` | _unmapped_ |
| 288 | `REQUIRED` | predict` - Required for model invocation and token counting | `google-vertex-ai.md` | `unmapped` | _unmapped_ |
| 289 | `MUST` | Anthropic authentication must come from `ANTHROPIC_API_KEY` or an `apiKeyHelper` in the JSON passed to `--settings` | `headless.md` | `unmapped` | _unmapped_ |
| 290 | `REQUIRED` | "object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' | `headless.md` | `unmapped` | _unmapped_ |
| 291 | `REQUIRED` | "object","properties":{"functions":{"type":"array","items":{"type":"string"}}},"required":["functions"]}' \ | `headless.md` | `unmapped` | _unmapped_ |
| 292 | `MUST` | The server must already be connected; the hook never triggers an OAuth or connection flow \| | `hooks.md` | `unmapped` | _unmapped_ |
| 293 | `MUST` | You must choose one approach per hook, not both: either use exit codes alone for signaling, or exit 0 and print JSON for structured control | `hooks.md` | `unmapped` | _unmapped_ |
| 294 | `MUST` | Your hook's stdout must contain only the JSON object | `hooks.md` | `unmapped` | _unmapped_ |
| 295 | `MUST` | "reason": "Test suite must pass before proceeding" | `hooks.md` | `unmapped` | _unmapped_ |
| 296 | `MUST` | The value must match the tool's output shape \| | `hooks.md` | `unmapped` | _unmapped_ |
| 297 | `MUST` | The replacement value must match the tool's output shape | `hooks.md` | `unmapped` | _unmapped_ |
| 298 | `MUST` | echo "Task subject must start with a ticket number, e | `hooks.md` | `unmapped` | _unmapped_ |
| 299 | `MUST` | "reason": "Must be provided when Claude is blocked from stopping" | `hooks.md` | `unmapped` | _unmapped_ |
| 300 | `MUST` | The hook must return the absolute path to the created worktree directory | `hooks.md` | `unmapped` | _unmapped_ |
| 301 | `MUST` | The hook must return the absolute path to the created worktree directory: | `hooks.md` | `unmapped` | _unmapped_ |
| 302 | `MUST` | \| `type` \| yes \| Must be `"prompt"` | `hooks.md` | `unmapped` | _unmapped_ |
| 303 | `MUST` | The LLM must respond with JSON containing: | `hooks.md` | `unmapped` | _unmapped_ |
| 304 | `MUST` | \| `type` \| yes \| Must be `"agent"` \| | `hooks.md` | `unmapped` | _unmapped_ |
| 305 | `SHOULD` | etup` typically fire before servers finish connecting, so hooks on those events should expect the "not connected" error on first run | `hooks.md` | `partial` | hook event/type validation (Phase 12+) |
| 306 | `SHOULD` | n/plugins-reference#persistent-data-directory), for dependencies and state that should survive plugin updates | `hooks.md` | `partial` | plugin.json `dependencies` schema (Phase 12+) |
| 307 | `SHOULD` | The exit code from your hook command tells Claude Code whether the action should proceed, be blocked, or be ignored | `hooks.md` | `unmapped` | _unmapped_ |
| 308 | `SHOULD` | Use `additionalContext` for information Claude should know about the current state of your environment or the operation that just ran: | `hooks.md` | `partial` | .env / env.example secret scan |
| 309 | `SHOULD` | Tells Claude why it should continue \| | `hooks.md` | `unmapped` | _unmapped_ |
| 310 | `SHOULD` | "prompt": "Evaluate if Claude should stop: $ARGUMENTS | `hooks.md` | `partial` | skill arguments declaration + `$<name>` cross-ref |
| 311 | `SHOULD` | top` hooks use the same format to evaluate whether a [subagent](/en/sub-agents) should stop: | `hooks.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 312 | `SHOULD` | "prompt": "You are evaluating whether Claude should stop working | `hooks.md` | `unmapped` | _unmapped_ |
| 313 | `REQUIRED` | *` is required: a matcher like `mcp__memory` contains only letters and underscores, so it is compared as an exact string and matches no tool | `hooks.md` | `unmapped` | _unmapped_ |
| 314 | `REQUIRED` | \| Field \| Required \| Description | `hooks.md` | `partial` | description length recommendation |
| 315 | `REQUIRED` | Required for any env var interpolation to work \| | `hooks.md` | `partial` | .env / env.example secret scan |
| 316 | `REQUIRED` | \| Field \| Required \| Description \| | `hooks.md` | `partial` | description length recommendation |
| 317 | `REQUIRED` | The JSON format isn't required for simple use cases | `hooks.md` | `unmapped` | _unmapped_ |
| 318 | `REQUIRED` | This example blocks tasks whose subjects don't follow the required format: | `hooks.md` | `unmapped` | _unmapped_ |
| 319 | `REQUIRED` | \| `reason` \| Required when `decision` is `"block"` | `hooks.md` | `unmapped` | _unmapped_ |
| 320 | `REQUIRED` | \| `reason` \| Required when `ok` is `false` | `hooks.md` | `unmapped` | _unmapped_ |
| 321 | `MUST` | Hook scripts must be executable for Claude Code to run them: | `hooks-guide.md` | `unmapped` | _unmapped_ |
| 322 | `MUST` | If your hook must see every file change, such as for compliance scanning or audit logging, add a [`Stop`](/en/hooks#stop) hook that scans the working tree once per turn | `hooks-guide.md` | `partial` | hook event/type validation (Phase 12+) |
| 323 | `SHOULD` | You should receive a desktop notification | `hooks-guide.md` | `unmapped` | _unmapped_ |
| 324 | `SHOULD` | The endpoint should return a JSON response body using the same [output format](/en/hooks#json-output) as command hooks | `hooks-guide.md` | `partial` | hook event/type validation (Phase 12+) |
| 325 | `SHOULD` | e you store project-specific instructions, conventions, and context that Claude should know every session | `how-claude-code-works.md` | `unmapped` | _unmapped_ |
| 326 | `MUST` | (Tmux users must press Ctrl+B twice due to tmux's prefix key | `interactive-mode.md` | `unmapped` | _unmapped_ |
| 327 | `MUST` | When using JetBrains Remote Development, you must install the plugin in the remote host via **Settings → Plugin (Host)** | `jetbrains.md` | `unmapped` | _unmapped_ |
| 328 | `MUST` | The plugin must be installed on the remote host, not on your local client machine | `jetbrains.md` | `unmapped` | _unmapped_ |
| 329 | `MUST` | ZDR is enabled on a per-organization basis, so each organization must have ZDR enabled separately to be covered under the BAA | `legal-and-compliance.md` | `unmapped` | _unmapped_ |
| 330 | `SHOULD` | 's capabilities, including those using the [Agent SDK](/en/agent-sdk/overview), should use API key authentication through [Claude Console](https://platform | `legal-and-compliance.md` | `unmapped` | _unmapped_ |
| 331 | `MUST` | For an LLM gateway to work with Claude Code, it must meet the following requirements: | `llm-gateway.md` | `unmapped` | _unmapped_ |
| 332 | `MUST` | The gateway must expose to clients at least one of the following API formats: | `llm-gateway.md` | `unmapped` | _unmapped_ |
| 333 | `MUST` | * Must forward request headers: `anthropic-beta`, `anthropic-version` | `llm-gateway.md` | `partial` | plugin.json `version` semver check |
| 334 | `MUST` | * Must preserve request body fields: `anthropic_beta`, `anthropic_version` | `llm-gateway.md` | `partial` | plugin.json `version` semver check |
| 335 | `MUST` | All options (`--transport`, `--env`, `--scope`, `--header`) must come **before** the server name | `mcp.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 336 | `MUST` | The URL must use `https://` | `mcp.md` | `unmapped` | _unmapped_ |
| 337 | `MUST` | * The command must write a JSON object of string key-value pairs to stdout | `mcp.md` | `unmapped` | _unmapped_ |
| 338 | `MUST` | * The JSON must conform to the MCP server configuration schema | `mcp.md` | `unmapped` | _unmapped_ |
| 339 | `MUST` | **Configuring the executable path**: The `command` field must reference the Claude Code executable | `mcp.md` | `unmapped` | _unmapped_ |
| 340 | `MUST` | ven when [`MCP_CONNECTION_NONBLOCKING=1`](/en/env-vars) is set, since the tools must be present when the first prompt is built | `mcp.md` | `partial` | .env / env.example secret scan |
| 341 | `MUST` | **Important**: Each entry must have exactly one of `serverName`, `serverCommand`, or `serverUrl` | `mcp.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 342 | `MUST` | * Command arrays must match **exactly** - both the command and all arguments in the correct order | `mcp.md` | `partial` | skill arguments declaration + `$<name>` cross-ref |
| 343 | `MUST` | * When the allowlist contains **any** `serverCommand` entries, stdio servers **must** match one of those commands | `mcp.md` | `partial` | command frontmatter validation (Phase 12+) |
| 344 | `MUST` | * When the allowlist contains **any** `serverUrl` entries, remote servers **must** match one of those URL patterns | `mcp.md` | `unmapped` | _unmapped_ |
| 345 | `MUST` | js"]`: ❌ Blocked (stdio servers must match commands when command entries exist) | `mcp.md` | `partial` | command frontmatter validation (Phase 12+) |
| 346 | `SHOULD` | * When Claude should search for your tools | `mcp.md` | `unmapped` | _unmapped_ |
| 347 | `SHOULD` | If a server's tools should always be visible to Claude without a search step, set `alwaysLoad` to `true` in that server's configuration | `mcp.md` | `unmapped` | _unmapped_ |
| 348 | `REQUIRED` | If a required environment variable is not set and has no default value, Claude Code will fail to parse the config | `mcp.md` | `partial` | .env / env.example secret scan |
| 349 | `REQUIRED` | Complete any required authentication steps in Claude | `mcp.md` | `unmapped` | _unmapped_ |
| 350 | `REQUIRED` | No configuration is required on your side: elicitation dialogs appear automatically when a server requests them | `mcp.md` | `unmapped` | _unmapped_ |
| 351 | `MUST` | - All API endpoints must include input validation | `memory.md` | `unmapped` | _unmapped_ |
| 352 | `MUST` | The value must be an absolute path or start with `~/` | `memory.md` | `unmapped` | _unmapped_ |
| 353 | `MUST` | If the instruction is something that must run at a specific point, such as before every commit or after each file edit, write it as a [hook](/en/hooks-guide) instead | `memory.md` | `partial` | hook event/type validation (Phase 12+) |
| 354 | `MUST` | This must be passed every invocation, so it's better suited to scripts and automation than interactive use | `memory.md` | `unmapped` | _unmapped_ |
| 355 | `SHOULD` | * A code review catches something Claude should have known about this codebase | `memory.md` | `unmapped` | _unmapped_ |
| 356 | `SHOULD` | Keep it to facts Claude should hold in every session: build commands, conventions, project layout, "always do X" rules | `memory.md` | `partial` | command frontmatter validation (Phase 12+) |
| 357 | `SHOULD` | Each file should cover one topic, with a descriptive filename like `testing | `memory.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 358 | `REQUIRED` | The `Azure AI User` and `Cognitive Services User` default roles include all required permissions for invoking Claude models | `microsoft-foundry.md` | `unmapped` | _unmapped_ |
| 359 | `MUST` | You can use the following environment variables, which must be full **model | `model-config.md` | `partial` | .env / env.example secret scan |
| 360 | `MUST` | Keys must be Anthropic model IDs as listed in the [Models overview](https://platform | `model-config.md` | `unmapped` | _unmapped_ |
| 361 | `MUST` | The script must output valid JSON with string key-value pairs representing HTTP headers: | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 362 | `MUST` | * **Format**: Must be comma-separated key=value pairs: `key1=value1,key2=value2` | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 363 | `MUST` | * **Special characters**: Characters outside the allowed range must be percent-encoded | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 364 | `REQUIRED` | Set authentication (if required) | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 365 | `REQUIRED` | UDE_CODE_ENABLE_TELEMETRY` \| Enables telemetry collection (required) | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 366 | `REQUIRED` | \| `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA` \| Enable span tracing (required) | `monitoring-usage.md` | `unmapped` | _unmapped_ |
| 367 | `REQUIRED` | \| URL \| Required for \| | `network-config.md` | `unmapped` | _unmapped_ |
| 368 | `SHOULD` | [Define how the assistant should behave in this style | `output-styles.md` | `unmapped` | _unmapped_ |
| 369 | `REQUIRED` | m_source=claude_code\&utm_medium=docs\&utm_content=overview_desktop_pricing) is required | `overview.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 370 | `MUST` | * **Admin**: on Team and Enterprise, an admin must enable it in [Claude Code admin settings](https://claude | `permission-modes.md` | `unmapped` | _unmapped_ |
| 371 | `MUST` | A rule must match each subcommand independently | `permissions.md` | `unmapped` | _unmapped_ |
| 372 | `MUST` | A rule must match every subcommand for the compound command to be allowed | `permissions.md` | `unmapped` | _unmapped_ |
| 373 | `REQUIRED` | \| Tool type \| Example \| Approval required \| "Yes, don't ask again" behavior \| | `permissions.md` | `unmapped` | _unmapped_ |
| 374 | `SHOULD` | de-on-the-web) \| Long-running tasks that don't need much steering, or work that should continue when you're offline \| Anthropic-managed cloud, continues after you disconnect | `platforms.md` | `unmapped` | _unmapped_ |
| 375 | `MUST` | Code to find a dependency's available versions, the upstream plugin's releases must be tagged using a specific naming convention | `plugin-dependencies.md` | `partial` | plugin.json `version` semver check |
| 376 | `REQUIRED` | Required | `plugin-dependencies.md` | `unmapped` | _unmapped_ |
| 377 | `MUST` | Must start with ` | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 378 | `MUST` | For a marketplace to be allowed, all specified fields must match exactly: | `plugin-marketplaces.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 379 | `REQUIRED` | * For GitHub sources: `repo` is required, and `ref` or `path` must also match if specified in the allowlist | `plugin-marketplaces.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 380 | `MUST` | * For URL sources: the full URL must match exactly | `plugin-marketplaces.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 381 | `MUST` | Each channel must resolve to a different version | `plugin-marketplaces.md` | `partial` | plugin.json `version` semver check |
| 382 | `MUST` | json` must declare a different `version` at each pinned ref | `plugin-marketplaces.md` | `partial` | plugin.json `version` semver check |
| 383 | `SHOULD` | For dependencies or state that should survive plugin updates, use [`${CLAUDE_PLUGIN_DATA}`](/en/plugins-reference#persistent-data-directory) instead | `plugin-marketplaces.md` | `partial` | plugin.json `dependencies` schema (Phase 12+) |
| 384 | `SHOULD` | You can also specify which plugins should be enabled by default: | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 385 | `REQUIRED` | ### Required fields | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 386 | `REQUIRED` | \| Field \| Type \| Required \| Description \| | `plugin-marketplaces.md` | `partial` | description length recommendation |
| 387 | `REQUIRED` | \| `author` \| object \| Plugin author information (`name` required, `email` optional) | `plugin-marketplaces.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 388 | `REQUIRED` | \| `repo` \| string \| Required | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 389 | `REQUIRED` | \| `url` \| string \| Required | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 390 | `REQUIRED` | \| `path` \| string \| Required | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 391 | `REQUIRED` | \| `package` \| string \| Required | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 392 | `REQUIRED` | json` with required fields \| | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 393 | `REQUIRED` | * Check that plugin directories contain required files | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 394 | `REQUIRED` | * Check that the token has the required permissions (read access to the repository) | `plugin-marketplaces.md` | `unmapped` | _unmapped_ |
| 395 | `MUST` | All other directories must be at the plugin root level | `plugins.md` | `unmapped` | _unmapped_ |
| 396 | `MUST` | Users installing your plugin must have the language server binary installed on their machine | `plugins.md` | `unmapped` | _unmapped_ |
| 397 | `MUST` | \| Must manually copy to share \| Install with `/plugin install` \| | `plugins.md` | `unmapped` | _unmapped_ |
| 398 | `MUST` | \| `command` \| The LSP binary to execute (must be in PATH) \| | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 399 | `MUST` | **You must install the language server binary separately | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 400 | `MUST` | Keys must be valid identifiers | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 401 | `REQUIRED` | The `server` field is required and must match a key in the plugin's `mcpServers` | `plugins-reference.md` | `partial` | MCP server schema (Phase 16+) |
| 402 | `MUST` | * All paths must be relative to the plugin root and start with ` | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 403 | `MUST` | ories (commands/, agents/, skills/, output-styles/, themes/, monitors/, hooks/) must be at the plugin root, not inside ` | `plugins-reference.md` | `partial` | command frontmatter validation (Phase 12+) |
| 404 | `MUST` | th errors \| Absolute paths used \| All paths must be relative and start with ` | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 405 | `MUST` | **Correct structure**: Components must be at the plugin root, not inside ` | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 406 | `MUST` | json`, you must bump it every time you want users to receive changes | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 407 | `SHOULD` | description: What this agent specializes in and when Claude should invoke it | `plugins-reference.md` | `partial` | description length recommendation |
| 408 | `SHOULD` | r Python virtual environments, generated code, caches, and any other files that should persist across plugin versions | `plugins-reference.md` | `partial` | plugin.json `version` semver check |
| 409 | `SHOULD` | Verify the shebang line: First line should be `#!/bin/bash` or `#!/usr/bin/env bash` | `plugins-reference.md` | `partial` | .env / env.example secret scan |
| 410 | `REQUIRED` | **Required fields:** | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 411 | `REQUIRED` | ou see `Executable not found in $PATH` in the `/plugin` Errors tab, install the required binary for your language | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 412 | `REQUIRED` | ### Required fields | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 413 | `REQUIRED` | If you include a manifest, `name` is the only required field | `plugins-reference.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 414 | `REQUIRED` | \| Field \| Required \| Description \| | `plugins-reference.md` | `partial` | description length recommendation |
| 415 | `REQUIRED` | \| `required` \| No \| If `true`, validation fails when the field is empty \| | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 416 | `REQUIRED` | Required when stdin is not a TTY \| \| | `plugins-reference.md` | `unmapped` | _unmapped_ |
| 417 | `REQUIRED` | Remove auto-installed plugin dependencies that are no longer required by any installed plugin | `plugins-reference.md` | `partial` | plugin.json `dependencies` schema (Phase 12+) |
| 418 | `REQUIRED` | Validation errors: name: Required`: a required field is missing | `plugins-reference.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 419 | `MUST` | On Team and Enterprise, an admin must first enable the Remote Control toggle in [Claude Code admin settings](https://claude | `remote-control.md` | `unmapped` | _unmapped_ |
| 420 | `MUST` | * **Local process must keep running**: Remote Control runs as a local process | `remote-control.md` | `unmapped` | _unmapped_ |
| 421 | `MUST` | prompt is the most important part: the routine runs autonomously, so the prompt must be self-contained and explicit about what to do and what success looks like | `routines.md` | `unmapped` | _unmapped_ |
| 422 | `MUST` | The Claude GitHub App must be installed on the repository you want to subscribe to | `routines.md` | `unmapped` | _unmapped_ |
| 423 | `MUST` | All filter conditions must match for the routine to trigger | `routines.md` | `unmapped` | _unmapped_ |
| 424 | `SHOULD` | ns, enable **Allow unrestricted branch pushes** for any repository where Claude should be able to push to existing branches instead of only `claude/`-prefixed ones | `routines.md` | `unmapped` | _unmapped_ |
| 425 | `MUST` | he `dangerouslyDisableSandbox` parameter is completely ignored and all commands must run sandboxed or be explicitly listed in `excludedCommands` | `sandboxing.md` | `partial` | command frontmatter validation (Phase 12+) |
| 426 | `SHOULD` | This option considerably weakens security and should only be used in cases where additional isolation is otherwise enforced | `sandboxing.md` | `unmapped` | _unmapped_ |
| 427 | `REQUIRED` | On **Linux and WSL2**, install the required packages first: | `sandboxing.md` | `unmapped` | _unmapped_ |
| 428 | `REQUIRED` | WSL1 does not support sandboxing because it lacks the required Linux namespace primitives | `sandboxing.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 429 | `REQUIRED` | If required dependencies are missing (such as `bubblewrap` or `socat` on Linux), the menu displays installation instructions for your platform | `sandboxing.md` | `partial` | plugin.json `dependencies` schema (Phase 12+) |
| 430 | `SHOULD` | Use **cloud tasks** for work that should run reliably without your machine | `scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 431 | `REQUIRED` | The file is plain Markdown with no required structure | `scheduled-tasks.md` | `unmapped` | _unmapped_ |
| 432 | `MUST` | Users must approve to proceed | `server-managed-settings.md` | `unmapped` | _unmapped_ |
| 433 | `MUST` | * Security policies that must be enforced organization-wide | `settings.md` | `unmapped` | _unmapped_ |
| 434 | `MUST` | Administrators who deployed settings to that location must migrate files to `C:\Program Files\ClaudeCode\managed-settings | `settings.md` | `unmapped` | _unmapped_ |
| 435 | `MUST` | fault unless your organization deploys managed settings, in which case this key must be set to `true` | `settings.md` | `unmapped` | _unmapped_ |
| 436 | `MUST` | For HKCU policy to also apply on WSL, the flag must additionally be set in HKCU itself | `settings.md` | `unmapped` | _unmapped_ |
| 437 | `MUST` | dangerouslyDisableSandbox` escape hatch is completely disabled and all commands must run sandboxed (or be in `excludedCommands`) | `settings.md` | `partial` | command frontmatter validation (Phase 12+) |
| 438 | `MUST` | Plugins listed here must reference external sources such as GitHub or npm | `settings.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 439 | `MUST` | Plugins in URL-based marketplaces must use external sources (GitHub, npm, or git URLs) rather than relative paths | `settings.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 440 | `MUST` | Marketplace sources must match **exactly** for a user's addition to be allowed | `settings.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 441 | `MUST` | * The `repo` or `url` must match exactly | `settings.md` | `unmapped` | _unmapped_ |
| 442 | `MUST` | * The `ref` field must match exactly (or both be undefined) | `settings.md` | `unmapped` | _unmapped_ |
| 443 | `MUST` | * The `path` field must match exactly (or both be undefined) | `settings.md` | `unmapped` | _unmapped_ |
| 444 | `SHOULD` | * Plugins the whole team should have | `settings.md` | `unmapped` | _unmapped_ |
| 445 | `SHOULD` | \| `excludedCommands` \| Commands that should run outside of the sandbox | `settings.md` | `partial` | command frontmatter validation (Phase 12+) |
| 446 | `SHOULD` | prints the error and refuses to start, so a helper that needs outage resilience should serve from its own cache and exit `0` | `settings.md` | `unmapped` | _unmapped_ |
| 447 | `SHOULD` | Defines additional marketplaces that should be made available for the repository | `settings.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 448 | `REQUIRED` | "Reminder: Code reviews required for all PRs", | `settings.md` | `unmapped` | _unmapped_ |
| 449 | `REQUIRED` | Required for Go-based tools like `gh`, `gcloud`, and `terraform` to verify TLS certificates when using `httpProxyPort` with a MITM proxy and custom CA | `settings.md` | `unmapped` | _unmapped_ |
| 450 | `REQUIRED` | pically used in repository-level settings to ensure team members have access to required plugin sources | `settings.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 451 | `REQUIRED` | Fields: `repo` (required), `ref` (optional: branch/tag/SHA), `path` (optional: subdirectory) | `settings.md` | `unmapped` | _unmapped_ |
| 452 | `REQUIRED` | Fields: `url` (required), `ref` (optional: branch/tag/SHA), `path` (optional: subdirectory) | `settings.md` | `unmapped` | _unmapped_ |
| 453 | `REQUIRED` | Fields: `url` (required), `headers` (optional: HTTP headers for authenticated access) | `settings.md` | `unmapped` | _unmapped_ |
| 454 | `REQUIRED` | Fields: `package` (required, supports scoped packages) | `settings.md` | `unmapped` | _unmapped_ |
| 455 | `REQUIRED` | Fields: `path` (required: absolute path to marketplace | `settings.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 456 | `REQUIRED` | Fields: `path` (required: absolute path to directory containing ` | `settings.md` | `unmapped` | _unmapped_ |
| 457 | `REQUIRED` | Fields: `hostPattern` (required: regex pattern to match against the marketplace host) | `settings.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 458 | `REQUIRED` | Fields: `pathPattern` (required: regex pattern matched against the `path` field of `file` and `directory` sources) | `settings.md` | `partial` | marketplace source-type allowlist (Phase 16+) |
| 459 | `MUST` | Your package manager must allow optional dependencies | `setup.md` | `partial` | plugin.json `dependencies` schema (Phase 12+) |
| 460 | `SHOULD` | asc` should report `31DD DE24 DDFA B679 F42D 7BD2 BAA9 29FF 1A7E CACE` | `setup.md` | `unmapped` | _unmapped_ |
| 461 | `SHOULD` | pub`, which should report `395759c1f7449ef4cdef305a42e820f3c766d6090d142634ebdb049f113168b6` | `setup.md` | `unmapped` | _unmapped_ |
| 462 | `REQUIRED` | * **Network**: internet connection required | `setup.md` | `unmapped` | _unmapped_ |
| 463 | `REQUIRED` | This example installs the required packages on Alpine: | `setup.md` | `unmapped` | _unmapped_ |
| 464 | `SHOULD` | Either way, Claude should respond with a short summary of your edit and a list of risks | `skills.md` | `unmapped` | _unmapped_ |
| 465 | `SHOULD` | \| `when_to_use` \| No \| Additional context for when Claude should invoke the skill, such as trigger phrases or example requests | `skills.md` | `unmapped` | _unmapped_ |
| 466 | `SHOULD` | Claude should know this when relevant, but `/legacy-system-context` isn't a meaningful action for users to take | `skills.md` | `unmapped` | _unmapped_ |
| 467 | `SHOULD` | ude Code does not re-read the skill file on later turns, so write guidance that should apply throughout a task as standing instructions rather than one-time steps | `skills.md` | `unmapped` | _unmapped_ |
| 468 | `REQUIRED` | md # Main instructions (required) | `skills.md` | `unmapped` | _unmapped_ |
| 469 | `REQUIRED` | md` contains the main instructions and is required | `skills.md` | `unmapped` | _unmapped_ |
| 470 | `REQUIRED` | \| Field \| Required \| Description | `skills.md` | `partial` | description length recommendation |
| 471 | `REQUIRED` | md (required - overview and navigation) | `skills.md` | `unmapped` | _unmapped_ |
| 472 | `MUST` | ode on the web \| Access to [Claude Code on the web](/en/claude-code-on-the-web) must be enabled \| | `slack.md` | `unmapped` | _unmapped_ |
| 473 | `MUST` | A workspace administrator must install the Claude app from the Slack App Marketplace | `slack.md` | `partial` | marketplace.json schema (Phase 16+ Layout C) |
| 474 | `MUST` | Users must explicitly invite Claude to channels where they want to use it: | `slack.md` | `unmapped` | _unmapped_ |
| 475 | `REQUIRED` | * **Web access required**: Users must have Claude Code on the web access; those without it will only get standard Claude chat responses | `slack.md` | `unmapped` | _unmapped_ |
| 476 | `SHOULD` | Claude may follow directions from other messages in the context, so users should make sure to only use Claude in trusted Slack conversations | `slack.md` | `unmapped` | _unmapped_ |
| 477 | `SHOULD` | * **Define success**: Explain what "done" looks like—should Claude write tests? Update documentation? Create a PR? | `slack.md` | `unmapped` | _unmapped_ |
| 478 | `REQUIRED` | * **Invite required**: Type `/invite @Claude` in any channel to add Claude to that channel | `slack.md` | `unmapped` | _unmapped_ |
| 479 | `MUST` | org/wiki/ANSI_escape_code#Colors) like `\033[32m` for green (terminal must support them) | `statusline.md` | `unmapped` | _unmapped_ |
| 480 | `REQUIRED` | **Workspace trust required** | `statusline.md` | `unmapped` | _unmapped_ |
| 481 | `MUST` | - Critical issues (must fix) | `sub-agents.md` | `unmapped` | _unmapped_ |
| 482 | `MUST` | The path must match the `command` field in your hook configuration: | `sub-agents.md` | `unmapped` | _unmapped_ |
| 483 | `SHOULD NOT` | mory-local/<name-of-agent>/` \| the subagent's knowledge is project-specific but should not be checked into version control \| | `sub-agents.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 484 | `SHOULD NOT` | knowledge is broadly applicable across projects, or `local` when the knowledge should not be checked into version control | `sub-agents.md` | `partial` | plugin.json `version` semver check |
| 485 | `SHOULD` | It should explain | `sub-agents.md` | `unmapped` | _unmapped_ |
| 486 | `SHOULD` | \| `description` \| Yes \| When Claude should delegate to this subagent | `sub-agents.md` | `partial` | description length recommendation |
| 487 | `SHOULD` | Choose a scope based on how broadly the memory should apply: | `sub-agents.md` | `unmapped` | _unmapped_ |
| 488 | `SHOULD` | claude/agent-memory/<name-of-agent>/` \| the subagent should remember learnings across all projects \| | `sub-agents.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 489 | `SHOULD` | * **Design focused subagents:** each subagent should excel at one specific task | `sub-agents.md` | `partial` | agent frontmatter validation (Phase 12+) |
| 490 | `SHOULD` | - Warnings (should fix) | `sub-agents.md` | `unmapped` | _unmapped_ |
| 491 | `REQUIRED` | Only `name` and `description` are required | `sub-agents.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 492 | `REQUIRED` | \| Field \| Required \| Description | `sub-agents.md` | `partial` | description length recommendation |
| 493 | `REQUIRED` | eb with a single subscription, centralized billing, and no infrastructure setup required | `third-party-integrations.md` | `unmapped` | _unmapped_ |
| 494 | `REQUIRED` | \| Permission Required \| | `tools-reference.md` | `unmapped` | _unmapped_ |
| 495 | `REQUIRED` | **If you're actually on musl**, such as Alpine Linux, install the required packages: | `troubleshoot-install.md` | `unmapped` | _unmapped_ |
| 496 | `FORBIDDEN` | \| `OAuth error` or `403 Forbidden` \| [Fix authentication](#login-and-authentication) | `troubleshoot-install.md` | `unmapped` | _unmapped_ |
| 497 | `FORBIDDEN` | ### 403 Forbidden after login | `troubleshoot-install.md` | `unmapped` | _unmapped_ |
| 498 | `FORBIDDEN` | If you see `API Error: 403 {"error":{"type":"forbidden","message":"Request not allowed"}}` after logging in: | `troubleshoot-install.md` | `unmapped` | _unmapped_ |
| 499 | `FORBIDDEN` | \| Login loops, OAuth errors, `403 Forbidden`, "organization disabled", Bedrock/Vertex/Foundry credentials \| [Troubleshoot installation and login](/en/troubleshoot-install#login-and-authentication) \| | `troubleshooting.md` | `unmapped` | _unmapped_ |
| 500 | `MUST` | always bills as extra usage outside the free runs, your account or organization must have extra usage enabled before you can launch a paid review | `ultrareview.md` | `unmapped` | _unmapped_ |
| 501 | `MUST` | Must be URL-encoded | `vs-code.md` | `unmapped` | _unmapped_ |
| 502 | `MUST` | The session must belong to the workspace currently open in VS Code | `vs-code.md` | `unmapped` | _unmapped_ |
| 503 | `MUST` | Each extension activation generates a fresh random auth token that the CLI must present to connect | `vs-code.md` | `unmapped` | _unmapped_ |
| 504 | `MUST` | The URL must allow cross-origin requests | `web-quickstart.md` | `unmapped` | _unmapped_ |
| 505 | `REQUIRED` | 7, and the <code>--enable-auto-mode</code> flag is no longer required</div> | `2026-w16.md` | `unmapped` | _unmapped_ |
| 506 | `REQUIRED` | <p className="digest-feature-lede">Git for Windows is no longer required | `2026-w18.md` | `partial` | plugin.json `name` checks (Phase 7+ regex) |
| 507 | `SHOULD` | unconditionally in auto mode, regardless of allow exceptions, for actions that should never run automatically even when broader allow rules apply</div> | `2026-w19.md` | `unmapped` | _unmapped_ |
| 508 | `REQUIRED` | **Windows without Git Bash**: Git for Windows is no longer required, and Claude Code uses PowerShell as the shell tool when Bash is absent | `index.md` | `unmapped` | _unmapped_ |
| 509 | `REQUIRED` | Even with ZDR enabled, Anthropic may retain data where required by law or to address Usage Policy violations | `zero-data-retention.md` | `unmapped` | _unmapped_ |
