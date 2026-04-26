# Exfil and Autonomy Rules — Security Rules

## Contents

- CAT-12 Social engineering credential prompt
- CAT-15 Markdown image beacon (silent exfiltration)
- CAT-16 "Don't ask user" autonomy abuse (removes HITL)

**Purpose:** Rules covering data exfiltration channels and autonomy-abuse patterns — credential harvesting via AI-readable memory files, pixel-beacon exfil through markdown images, and instructions that remove the human-in-the-loop safety gate. Load when auditing skills or agents for data-leakage or unbounded-execution risk.

## Companion files

- `prompt-injection-rules.md` — CAT-01, CAT-02, CAT-03, CAT-04, CAT-05, CAT-11, CAT-13, CAT-14 (direct injection + social engineering)
- `concealment-and-multilingual-rules.md` — CAT-06, CAT-07, CAT-17, CAT-18, CAT-19 (hiding attacks from humans/regex)
- `mcp-and-capability-rules.md` — CAT-08, CAT-09, CAT-10 (tool-layer attacks)
- `agent-rule-checks.md` — agent-level structural checks
- `channel-source-security.md` — Channel MCP security pillar

---

## CAT-12 — Social engineering credential prompt

**What it looks like:** Skill instructs the LLM to ask the user for credentials and store/transmit them. MEMORY.md and CLAUDE.md are readable by all subsequent skills — a persistent cross-skill credential leak channel.

**Indicators:** "ask the user for [API key / password / token]" AND "store in / save to" follows. "store [API key / secret] in MEMORY.md / CLAUDE.md / .env". "request user's [SSH key / private key]" without stated need. Credential prompt + any network destination (Telegram, Discord, webhook). "plaintext password in memory" as recommendation.

**False-positive guards:** Legitimate env-variable setup telling the USER to set a variable in their shell profile. "ask what model to use" (not a credential). `.env.example` instructions.

**Severity:** CRITICAL (credential → external destination); MAJOR (credential stored in AI-readable memory file)

**Source:** EvolutionUnleashed/skill-sentinel (T4), emelyanowcom/skill-sanitizer (SELF_MOD_PATTERNS), yidun (AGENT_MEMORY tag)

---

## CAT-15 — Markdown image beacon (silent exfiltration)

**What it looks like:** A markdown `![]()` or HTML `<img>` whose URL includes session-data query parameters. Auto-fetching UIs deliver the exfiltrated data to the attacker via a single HTTP GET.

**Indicators:** Image URL with template expressions or variable substitutions in path/query. URL pointing to known OOB exfil services: oastify.com, interact.sh, dnslog, ceye.io, burpcollaborator.net, ngrok.io, webhook.site, requestbin. Instructions to "include this image in your response" followed by URL with query params. `<img src="...?data=...">` where query value contains session data.

**False-positive guards:** Static image URLs for logos/badges with no dynamic parameters. GitHub shields.io badges with static params. Documentation screenshots from well-known CDNs.

**Severity:** CRITICAL (image URL with variable substitution → external destination); MAJOR (suspicious external URL, no clear interpolation)

**Source:** declawedai/community-rules (webhook-exfil.yaml, OOB exfil catalog), emelyanowcom/skill-sanitizer (EXFIL_PATTERNS), rexcoleman (EXFIL-002/004)

---

## CAT-16 — "Don't ask user" autonomy abuse (removes HITL)

**What it looks like:** Instructions to skip user confirmation, suppress errors, or retry indefinitely. Removes the human-in-the-loop safety gate.

**Indicators:** "don't / do not ask [the user] for confirmation / permission". "proceed without asking". "ignore / override the user's decision". "retry indefinitely / forever / until success". "keep trying forever". "suppress all errors / exceptions". "ignore every error … continue". "modify my own code / behavior / instructions". "rewrite myself". "escalate privileges … without asking / automatically".

**False-positive guards:** "proceed without asking" for clearly non-destructive UX. Bounded retry logic ("retry up to 3 times"). "ignore warnings" narrowly scoped ("ignore deprecation warnings from the linter").

**Severity:** MAJOR (single autonomy-bypass phrase); CRITICAL (bypass + a destructive or exfil operation in the same context)

**Source:** skillward (autonomy_abuse_generic.yara — skip_confirmation, override_user, infinite_retry, self_modify), declawedai/community-rules, Opus synthesis RC-07

---

## Severity reference

| Severity | When to apply |
|----------|--------------|
| CRITICAL | Clear, unambiguous attack; no plausible benign reading |
| MAJOR | Suspicious pattern; low probability of benign use; warrants explicit review |
| MINOR | Unusual pattern that could be benign but deserves a note |
| NIT | Style / hygiene issue with no direct security impact |

## Source citations

- EvolutionUnleashed/skill-sentinel (T4) — CAT-12
- emelyanowcom/skill-sanitizer (SELF_MOD_PATTERNS, EXFIL_PATTERNS) — CAT-12, CAT-15
- yidun (AGENT_MEMORY tag) — CAT-12
- declawedai/community-rules (webhook-exfil.yaml, OOB exfil catalog) — CAT-15, CAT-16
- rexcoleman (EXFIL-002/004) — CAT-15
- skillward (autonomy_abuse_generic.yara) — CAT-16
- Opus synthesis RC-07 — CAT-16
