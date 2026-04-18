# Channel MCP Server Source-Code Security

## Contents

- [Why This Pillar Exists](#why-this-pillar-exists)
- [Workflow](#workflow)
- [Rule 1 — Sender-ID allowlist (CRITICAL)](#rule-1--sender-id-allowlist-critical)
- [Rule 2 — Permission-relay gating (CRITICAL)](#rule-2--permission-relay-gating-critical)
- [Rule 3 — Chat-ID-only gating (MAJOR)](#rule-3--chat-id-only-gating-major)
- [Rule 4 — Fully gated (PASSED)](#rule-4--fully-gated-passed)
- [Example vulnerable code](#example-vulnerable-code)
- [Example safe code](#example-safe-code)
- [Opus prompt template](#opus-prompt-template)
- [Rubric contribution](#rubric-contribution)

## Checklist

- [ ] Identify the channel-hosting MCP server source
- [ ] Apply Rule 1 (sender allowlist), Rule 2 (permission-relay gating), or Rule 3 (chat-ID gating)
- [ ] Confirm Rule 4 (fully gated PASSED) holds before shipping
- [ ] Use the Opus prompt template exactly — no paraphrasing
- [ ] Record rubric contribution for scoring

Detailed semantic-validation rules for the **Channel MCP Server Source-Code Security** pillar. This reference is loaded by the `semantic-validator` agent whenever a plugin declares a non-empty `channels` array in `plugin.json`.

## Why This Pillar Exists

`channels-reference.md` (Claude Code v2.1.80+) introduces a "channel" capability that lets an MCP server forward inbound messages (Telegram, Discord, iMessage, SMS gateways, etc.) to Claude. The spec warns explicitly:

> Only declare the capability if your channel authenticates the sender, because anyone who can reply through your channel can approve or deny tool use in your session.

Two attack vectors follow from this:

1. **Ungated inbound messages.** If the channel forwards every inbound payload to Claude without a sender allowlist, the channel becomes an open prompt-injection vector. Any third party who can reach the upstream transport (a Telegram bot, a public Discord room, a shared phone number) can inject arbitrary instructions into the Claude session.
2. **Permission-relay without sender gating.** If the MCP server declares `capabilities.experimental['claude/channel/permission']`, Claude will route tool-permission prompts through the channel. Without sender gating, any external actor can approve destructive tool calls (file writes, shell commands, HTTP requests).

CPV's syntactic validators catch the *declaration* side (that a `channels` array exists, that `mcpServers.<server>` cross-references a real server). They CANNOT read the MCP server's source code to verify that the sender-gating logic is actually implemented. That check requires an LLM — which is exactly what the semantic validator provides.

## Scope

This pillar runs ONLY when BOTH conditions hold:

- `plugin.json` contains a non-empty `channels` array
- The plugin ships MCP server source code referenced by `mcpServers.<server>.command` / `args` (local source — not pip/npm published packages the plugin only invokes)

Skip this pillar entirely when `plugin.json.channels` is missing or empty. Do not burn opus tokens on plugins that do not use channels.

## Source-Language Support

| Language | Status |
|----------|--------|
| TypeScript | REQUIRED |
| JavaScript | REQUIRED |
| Python | REQUIRED |
| Other | Emit INFO note — "Language not yet supported; manual review recommended" |

Start every evaluation by reading the `mcpServers` block in `plugin.json` and resolving the entry-point source file. Typical patterns:

```json
"mcpServers": {
  "my-channel": {
    "command": "node",
    "args": ["${CLAUDE_PLUGIN_ROOT}/servers/my-channel/dist/index.js"]
  }
}
```

The entry-point file is the one named in `args[0]`. If the build output is a bundled `dist/` artifact, look for the matching `src/index.ts` (or `src/server.py`) and read that — minified/bundled code is unreliable for manual gating analysis.

## Rule 1 — Inbound Sender Gating (CRITICAL)

### What to look for

Before any call that forwards an inbound payload to Claude, the handler MUST compare the sender ID against a known allowlist. Acceptable gating surfaces:

| Property path (TypeScript/JS) | Property path (Python) | Notes |
|-------------------------------|------------------------|-------|
| `message.from.id` | `message["from"]["id"]` / `message.from_user.id` | Telegram-style |
| `message.author.id` | `message.author.id` | Discord-style |
| `message.sender` / `message.sender_id` | `message.sender` / `message["sender_id"]` | iMessage/generic |
| `update.message.from.id` | `update.message.from.id` | Telegram Bot API v2 |
| `ctx.message.from.id` | `ctx.message.from.id` | Telegraf / aiogram middleware |

The allowlist value itself must come from a constant or an environment variable — never a hard-coded single value that silently accepts anyone during the comparison.

### Acceptable patterns

TypeScript:
```typescript
const ALLOWED_USER_IDS = new Set(
  (process.env.ALLOWED_USER_IDS ?? "").split(",").map(Number).filter(Boolean)
);

bot.on("message", async (msg) => {
  if (!msg.from || !ALLOWED_USER_IDS.has(msg.from.id)) {
    await bot.sendMessage(msg.chat.id, "Unauthorized sender.");
    return;
  }
  await mcp.notification("notifications/claude/channel", {
    channelId: CHANNEL_ID,
    message: msg.text,
  });
});
```

Python:
```python
ALLOWED_USER_IDS = {
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
}

@dp.message_handler()
async def handle(message: types.Message) -> None:
    if message.from_user is None or message.from_user.id not in ALLOWED_USER_IDS:
        await message.reply("Unauthorized sender.")
        return
    await mcp.send_notification(
        "notifications/claude/channel",
        {"channelId": CHANNEL_ID, "message": message.text},
    )
```

### Vulnerable patterns — emit CRITICAL

TypeScript:
```typescript
bot.on("message", async (msg) => {
  await mcp.notification("notifications/claude/channel", {
    channelId: CHANNEL_ID,
    message: msg.text,
  });
});
```

Python:
```python
@dp.message_handler()
async def handle(message):
    await mcp.send_notification(
        "notifications/claude/channel",
        {"channelId": CHANNEL_ID, "message": message.text},
    )
```

Finding text:
> "Channel MCP server forwards inbound messages to Claude without a sender allowlist (`<file>:<line>`). Any third party that can reach the upstream transport can inject arbitrary prompts into the Claude session. Add a check against `message.from.id` (or the transport's equivalent) against an allowlist sourced from a constant or env var, and early-return on mismatch. See `channels-reference.md` §Authentication."

### Naïve gating — emit MAJOR

Gating that is present in form but effectively a no-op. Examples:

```typescript
if (true) {                              // always-true guard
  await forward(msg);
}

if (msg.from) {                          // truthy check, no allowlist compare
  await forward(msg);
}

const ALLOWED = [];                      // empty allowlist — passes no one OR (more commonly) bypassed elsewhere
if (ALLOWED.includes(msg.from.id)) { ... }
// … but forward(msg) is ALSO called outside the block.

if (msg.from.id) { await forward(msg); } // truthy-only, any numeric id passes
```

Finding text:
> "Channel MCP server has a gating block near `<file>:<line>` but the check is effectively a no-op (`<pattern summary>`). Any sender will reach the forward call. Replace with a real allowlist compare."

## Rule 2 — Permission-Relay Capability Gate (CRITICAL)

### What to look for

When the server's `initialize` response (or equivalent declaration) includes:

```jsonc
{
  "capabilities": {
    "experimental": {
      "claude/channel/permission": { /* any payload */ }
    }
  }
}
```

…the server is telling Claude Code it is safe to route tool-permission prompts through this channel. The spec makes sender gating MANDATORY in this case: any sender who can reply through the channel can approve destructive tool calls.

Match the capability declaration in source with any of:
- `capabilities.experimental["claude/channel/permission"]`
- `capabilities: { experimental: { "claude/channel/permission": ... } }`
- Python dict: `{"capabilities": {"experimental": {"claude/channel/permission": ...}}}`

Then locate the handler that responds to `notifications/claude/channel/permission` (name varies: `onPermissionRequest`, `handle_permission`, a `switch` branch in a message router, etc.). Verify that this handler — or a middleware upstream of it — gates on sender ID before dispatching the `approve`/`deny` response.

### Acceptable pattern

```typescript
server.setNotificationHandler("claude/channel/permission", async (req) => {
  const from = req.params?.from?.id;
  if (!from || !ALLOWED_USER_IDS.has(from)) {
    return { approved: false, reason: "Unauthorized approver." };
  }
  return await relayToUpstream(req);
});
```

### Vulnerable patterns — emit CRITICAL

```typescript
server.setNotificationHandler("claude/channel/permission", async (req) => {
  return await relayToUpstream(req);   // no gating at all
});
```

```python
@server.notification("claude/channel/permission")
async def on_permission(req):
    return await relay(req)            # no gating
```

Finding text:
> "Channel MCP server declares `capabilities.experimental['claude/channel/permission']` (`<file>:<line>`) without a sender allowlist in the permission handler. Any inbound sender can approve destructive tool calls in the Claude session. Either remove the capability or gate the handler on `message.from.id`."

## Rule 3 — Room/Chat-ID-Only Gating (MAJOR)

### What to look for

Some authors gate on the chat/room ID instead of the sender ID — for example, a Telegram bot that only listens in a private chat. This is INSUFFICIENT because:
- A room can have multiple members, any of whom can send messages.
- Chat IDs can be spoofed in less-authenticated transports (SMS, webhooks).
- The spec explicitly warns against this.

Detect patterns where the ONLY gating mechanism is:

```typescript
if (msg.chat.id !== ALLOWED_CHAT_ID) return;  // chat-only gating
await forward(msg);
```

```python
if message.chat.id != ALLOWED_CHAT_ID:
    return
await forward(message)
```

If the code ALSO checks `msg.from.id`, this is fine (room + sender compound gating is strictly safer than sender-only). The MAJOR only fires when chat-ID is the *only* gating mechanism.

Finding text:
> "Channel MCP server gates forwarding on chat/room ID only (`<file>:<line>`). Anyone in the authorized room can inject prompts. Add a sender-ID allowlist check as the primary gate; chat-ID can remain as a secondary scope check but must not be the only one."

## Rule 4 — PASSED

Emit a PASSED finding only when ALL of these hold:

- Rule 1: a sender-ID allowlist check is present before every forward call (not merely in one branch).
- Rule 2: if `claude/channel/permission` is declared, the permission handler has a sender-ID allowlist check too.
- Rule 3: chat-ID gating (if present) is not the only gate.

Finding text:
> "Channel MCP server implements sender gating correctly (`<file>:<line>`). Inbound messages and permission requests are both allowlist-checked on `<property>` before forwarding."

## Opus Prompt Template

When the semantic validator runs this pillar, it should load the MCP server source file with the LLM Externalizer's `code_task` tool (or read it directly when small), and issue an instruction like:

```
You are auditing a Claude Code channel MCP server source file for prompt-injection
safety per channels-reference.md. Evaluate these four rules:

1. CRITICAL — Inbound sender gating. Before any call that forwards an inbound
   payload to Claude (e.g. `mcp.notification('notifications/claude/channel', ...)`,
   `send_notification('notifications/claude/channel', ...)`), the handler MUST
   compare the sender ID (`message.from.id`, `message.author.id`,
   `message.sender`, etc.) against an allowlist sourced from a constant or
   env var. Missing check => CRITICAL. Naïve check (always-true guard, empty
   allowlist, truthy-only) => MAJOR.

2. CRITICAL — Permission-relay capability. If
   `capabilities.experimental['claude/channel/permission']` is declared, the
   handler that answers `notifications/claude/channel/permission` MUST also
   gate on sender ID. Missing check => CRITICAL.

3. MAJOR — Chat-ID-only gating. If the ONLY gate is a chat/room-ID
   comparison with no sender-ID compare, emit MAJOR.

4. PASSED — If rules 1-3 are all satisfied, emit PASSED with the file:line
   of the gating check.

Quote the file:line of each finding. Do not infer from variable names alone;
cite the actual source expression.
```

## Integration With the A-F Rubric

This pillar contributes to the **Channel Source Security** row in the Semantic Validation report. Grading contribution:

| Finding in this pillar | Rubric effect |
|------------------------|---------------|
| No channels declared in plugin.json | N/A (pillar skipped — no grade impact) |
| Rule 4 PASSED | Pass |
| Rule 3 MAJOR only (chat-only gating) | Partial |
| Rule 1 MAJOR only (naïve gating) | Partial |
| Rule 1 CRITICAL (no gating) | Fail |
| Rule 2 CRITICAL (permission cap ungated) | Fail |

Apply the Fail to the overall grade per the existing grading table:
- 1–2 Fail criteria => D
- 3+ Fail criteria => F

## Report Format

Add this section to the semantic-validation report when the pillar runs:

```markdown
### Channel MCP Server Source Security
- **Inbound Sender Gating**: PASS/PARTIAL/FAIL - <file:line> <notes>
- **Permission-Relay Gating**: PASS/PARTIAL/FAIL/N/A - <file:line> <notes>
- **Chat-ID-Only Detection**: PASS/PARTIAL/FAIL/N/A - <file:line> <notes>

<severity>: <finding text quoting the source line>
```

If the plugin does not ship channels, write a single line:

```markdown
### Channel MCP Server Source Security
- **Skipped**: plugin.json declares no channels.
```

## References

- `channels-reference.md` (Anthropic Claude Code docs, v2.1.80+)
- TRDD-26446eed — design doc for this pillar
- `skills/fix-validation/references/plugin-error-index.md` — error catalogue (structural channel errors)
- `skills/fix-validation/references/skill-semantic-validation.md` — parent grading rubric
