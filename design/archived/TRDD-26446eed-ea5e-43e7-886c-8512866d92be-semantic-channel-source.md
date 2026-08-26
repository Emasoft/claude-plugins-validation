---
trdd-id: 26446eed
title: Semantic validation of channel MCP server source
column: complete
updated: 2026-08-25T17:25:22+0200
---

# TRDD-26446eed — Semantic validation of channel MCP server source

**TRDD ID:** `26446eed-ea5e-43e7-886c-8512866d92be`
**Filename:** `design/tasks/TRDD-26446eed-ea5e-43e7-886c-8512866d92be-semantic-channel-source.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done 2026-05-10 — deterministic prefilter helper (`scripts/cpv_channel_source_predicate.py`) implemented with 29 fixture-based tests (`tests/test_channel_source_predicate.py`). Reference doc, SKILL.md, agent.md, README.md, and plugin-error-index.md cross-references in place. Reference file `channel-source-security.md` was already authored in v2.22.3; this TRDD added the prefilter helper, the wiring, the fixture corpus (7 plugin scaffolds covering ungated TS, gated TS, chat-id-only TS, permission-capability TS, no-channels, gated PY, ungated PY), and the test suite enumerating the 5 TRDD acceptance scenarios.
**Deferred from:** TRDD-479cde0c §v2.22.1 "DEFERRED"
**Parent audit report:** `docs_dev/spec-audit-5-new-features-20260417-163011.md` §V4

## Problem

channels-reference.md introduces a security protocol with two prompt-injection-
adjacent footguns that CANNOT be caught by CPV's syntactic validators — they
require reading TypeScript/JavaScript/Python MCP server source code:

1. **Ungated inbound messages.** Every channel author MUST check
   `message.from.id` against an allowlist. A channel that forwards every
   inbound message to Claude unconditionally turns the channel into a
   prompt-injection vector. The spec says:
   > "only declare the capability if your channel authenticates the sender,
   > because anyone who can reply through your channel can approve or deny
   > tool use in your session."
2. **Permission-relay capability without sender gating.** A channel that
   declares `capabilities.experimental['claude/channel/permission']` and
   forwards permission-approval requests without checking
   `message.from.id` lets any Telegram/Discord/iMessage sender approve
   destructive tool calls.

CPV's existing `channels` structural validator catches the declaration
side (the plugin.json `channels` array, the `mcpServers` cross-reference),
but cannot check the MCP server's source code for sender-gating.

## What CPV needs to do

This is EXPLICITLY a `semantic-validator` skill extension, NOT a core
validator. The semantic-validator uses Opus and is expensive; users opt
in via `/cpv-semantic-validation`. The checks here belong there.

### New semantic rules

Add to `skills/semantic-validation-skill/` (or its references):

1. **Sender-gating detection.** For every plugin that declares a `channels`
   array in plugin.json:
   - Locate the MCP server source (from `mcpServers.<server>.command` /
     `args`).
   - Read the source (TypeScript/JavaScript/Python).
   - Look for a sender allowlist check before ANY call to
     `mcp.notification('notifications/claude/channel', ...)`.
   - Patterns to match:
     - `message.from.id` compared against a constant/env-var
     - `message.sender` / `message.author.id` compared
     - Explicit early-return when not allowlisted
   - If no such check is found → MAJOR semantic finding:
     "Channel MCP server appears to forward inbound messages without
     sender gating — this is a prompt-injection vector. Add an
     allowlist check against `message.from.id` before calling
     `mcp.notification()`."

2. **Permission-relay capability gate check.** For every plugin MCP server
   that declares `capabilities.experimental['claude/channel/permission']`:
   - Verify the server ALSO implements sender gating.
   - Confirm the `notifications/claude/channel/permission` response handler
     reads the sender ID before dispatching.
   - If either is missing → CRITICAL:
     "Channel MCP server declares `claude/channel/permission` capability
     without a corresponding sender allowlist. Any inbound sender can
     approve destructive tool calls. Either remove the capability or
     add sender gating."

3. **Room/chat-ID-only gating detection.** The spec explicitly warns
   about gating on room/chat-ID instead of sender-ID. Detect patterns
   like `message.chat.id === <constant>` as the ONLY gating mechanism
   and flag as MAJOR.

### Source-language support

Start with TypeScript/JavaScript (the reference channel implementations
use these). Python support later. Don't bother with other languages
until the ecosystem grows.

### Integration point

- New reference file: `skills/semantic-validation-skill/references/channel-source-gating.md`
- Opus prompt template loaded by the semantic-validator when it detects
  `plugin.json.channels` is non-empty.
- Output: structured findings merged into the semantic-validation report.

## Why this is deferred, not cancelled

Channels are a research-preview feature as of 2026-04-17. The ecosystem is
small; forcing semantic analysis on every plugin would burn tokens for
most plugins that don't ship channels. The right design is:

1. Core validator detects `channels` array in plugin.json (done in v2.22.0).
2. User opts into `/cpv-semantic-validation <plugin>`.
3. The semantic-validator sees channels + opts into the Opus source-code
   check as a separate phase with progress indication.

This way the cost is bounded to plugins that actually use channels.

## Tests

Once implemented:
- `test_channel_source_with_sender_gating_passes` (real fixture with
  `if message.from.id !== ALLOWED_USER_ID: return`).
- `test_channel_source_without_gating_fires_semantic_major`
- `test_channel_source_with_chat_id_only_gating_fires_semantic_major`
- `test_permission_capability_without_gating_fires_semantic_critical`
- `test_plugin_without_channels_skips_semantic_source_check`

These go in a new test file under `tests/semantic/`.

## Success criteria

- New reference file authored and loaded by the semantic-validator.
- Opus-driven semantic audit of channel MCP server source produces
  real findings on deliberately-ungated fixtures.
- Documentation (README + `plugin-error-index.md`) describes when the
  check runs and how to opt in.
- All fixture-based tests pass.

## Non-goals

- Runtime enforcement. CPV is a pre-install validator; it cannot prevent
  an unpatched channel from delivering prompt injection at runtime.
- Fuzzing the channel protocol. That belongs in the channel authors'
  own test suite.
- Static analysis of every language. Start TypeScript/JavaScript only.

## Approval log

- 2026-08-25T17:25:22+0200 — CLOSED as complete by the CPV session (board drain;
  authority delegated by USER 2026-08-25). Done 2026-05-10; cpv_channel_source_predicate.py
  - tests live (batch_ag).
