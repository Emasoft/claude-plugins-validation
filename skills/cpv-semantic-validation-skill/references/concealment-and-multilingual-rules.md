# Concealment and Multilingual Rules — Security Rules

## Contents

- CAT-06 Concealment — markdown comments, HTML, collapsible sections
- CAT-07 Multilingual injection
- CAT-17 Hidden HTML comment with action verbs
- CAT-18 CSS-hidden / collapsible-section injection
- CAT-19 Whitespace-padding / visual-deception

**Purpose:** Rules for attacks that hide malicious instructions from human reviewers or static regex tools — via HTML comments, CSS invisibility, collapsible elements, non-Latin scripts, or whitespace padding. Load when auditing AI-facing files for concealment risk or when the target may contain non-English content.

## Companion files

- `prompt-injection-rules.md` — CAT-01, CAT-02, CAT-03, CAT-04, CAT-05, CAT-11, CAT-13, CAT-14 (direct injection + social engineering)
- `mcp-and-capability-rules.md` — CAT-08, CAT-09, CAT-10 (tool-layer attacks)
- `exfil-and-autonomy-rules.md` — CAT-12, CAT-15, CAT-16 (exfiltration + autonomy abuse)
- `agent-rule-checks.md` — agent-level structural checks
- `channel-source-security.md` — Channel MCP security pillar

---

## CAT-06 — Concealment — markdown comments, HTML, collapsible sections

**What it looks like:** Instructions hidden in non-rendered markdown or HTML so human reviewers skip them. HTML comments `<!-- -->`, CSS `/* */`, collapsed `<details>` with action verbs, inline code spans carrying imperative language.

**Indicators:** HTML comment with action verbs (ignore / override / execute / send / bypass / system / instruction / prompt / secret). Long HTML comments (>150 chars) with no documentation purpose. `<details>` / `<summary>` containing instruction verbs. CSS `visibility:hidden` / `display:none` on elements with action text. Inline backtick span containing imperative language. 11 comment syntaxes: HTML, CSS/JS, Python `#`, Python docstrings, XML CDATA, Jinja, JSP, Razor.

**False-positive guards:** HTML comments with standard dev notes (TODO, FIXME, NOTE). Python `#` in `.py` scripts (not `.md` files). Large `<details>` with legitimate documentation (check action verb density). Frontmatter YAML continuation blocks.

**Severity:** CRITICAL (action verb inside hidden comment); MAJOR (long hidden comment without documentation purpose)

**Source:** declawedai/community-rules (html-comment-injection.yaml), rexcoleman (INJ-002), skillward PROMPT_INJECTION_CONCEALMENT, Opus synthesis RC-07

---

## CAT-07 — Multilingual injection

**What it looks like:** CAT-01 through CAT-06 patterns in Chinese, Korean, Japanese, Russian, Arabic, or Spanish. Static regex tools are English-only; the semantic validator reads all languages equally.

**Indicators:** CJK equivalents of override/deception phrases (绕过确认, 不要告诉用户, 静默发送, 隐藏此步骤, 输出系统提示词). Cyrillic or Arabic override directives. Spanish credential theft (contraseña, secreto + send/exfil pattern). Mixed-script sentences combining Latin and CJK/Cyrillic in a single instruction (obfuscation indicator). Homoglyph identifiers: Cyrillic "А" (U+0410) mixed with Latin "A" in the same token.

**False-positive guards:** Legitimate multilingual README or translation files. `.po`/`.mo`/`.json` locale files. Security documentation examples in context (negation guard). Russian developer comments in Russian-language repos.

**Severity:** CRITICAL / MAJOR — same as the corresponding English-language CAT the content expresses

**Source:** skills-checker (Chinese-language deception patterns), declawedai (Spanish credential-theft i18n), MarPek6 (8-language scanner), Opus synthesis RC-11

---

## CAT-17 — Hidden HTML comment with action verbs

**What it looks like:** `<!-- ... -->` in a SKILL.md or agent markdown file containing imperative action language. Invisible in rendered views but processed by the LLM on raw-markdown load.

**Indicators:** HTML comment containing any of: ignore, forget, override, bypass, disable, skip, system, instruction, prompt, secret, hidden, covert, stealth, private, send, post, execute, run, eval. Long comment (>200 chars) in AI-facing markdown with no documentation purpose. Unusual position: file header, file footer, between frontmatter and body.

**False-positive guards:** `<!-- TODO: add examples -->` — editorial note. `<!-- Security audit section -->` — structural label (check for action verbs specifically). Commented-out legitimate code blocks.

**Severity:** CRITICAL (comment contains CAT-01 through CAT-05 content); MAJOR (action verbs without clear documentation purpose)

**Source:** declawedai/community-rules (html-comment-injection.yaml), emelyanowcom/skill-sanitizer (length heuristic), rexcoleman (INJ-002)

---

## CAT-18 — CSS-hidden / collapsible-section injection

**What it looks like:** Instructions hidden inside elements styled invisible: `<span style="display:none">`, `<div style="visibility:hidden">`, or collapsed `<details>`. Rendered preview hides them; the LLM processes raw HTML.

**Indicators:** `display:\s*none` on element containing action verbs. `visibility:\s*hidden` on instruction-like content. `<details>` content is instruction prose, not documentation. `<details style="display:none">` (double-hidden). `color: white` / `color: #fff` on text (invisible on white background). `font-size: 0` — zero-size text.

**False-positive guards:** `display:none` on decorative UI elements (icons, SVG, spacers). `<details>` for legitimate collapsible documentation (changelog, long API reference). CSS on structural skeleton elements in dashboard templates.

**Severity:** CRITICAL (CSS-hidden element + CAT-01 through CAT-05 content); MAJOR (CSS-hidden + action verbs but ambiguous)

**Source:** rexcoleman (hidden-element rules: display:none, visibility:hidden), skillguard-cli (zero-width + HTML concealment)

---

## CAT-19 — Whitespace-padding / visual-deception

**What it looks like:** Malicious instructions padded below large whitespace blocks so human reviewers or truncated previews never reach the payload. The LLM reads the full content.

**Indicators:** More than 30 consecutive blank lines in an AI-facing markdown file. Large padding (>50 lines whitespace) pushing content below the visible fold. Instructions appearing after an apparent end marker (`---`, `===`) followed by whitespace then more content. Excessive filler text (kilobytes of repeated or meaningless content extending the file well beyond described functionality).

**False-positive guards:** Long skills with extensive documentation. Single blank lines between sections. Whitespace within tables or code blocks.

**Severity:** MINOR (suspicious padding pattern); MAJOR (padding + active instruction content beyond the padding)

**Source:** zantific/skill-security-review-lens (LOW-005, LOW-006), Opus synthesis

---

## Severity reference

| Severity | When to apply |
|----------|--------------|
| CRITICAL | Clear, unambiguous attack; no plausible benign reading |
| MAJOR | Suspicious pattern; low probability of benign use; warrants explicit review |
| MINOR | Unusual pattern that could be benign but deserves a note |
| NIT | Style / hygiene issue with no direct security impact |

## Source citations

- declawedai/community-rules (html-comment-injection.yaml) — CAT-06, CAT-07, CAT-17
- rexcoleman (INJ-002) — CAT-06, CAT-17, CAT-18
- skillward PROMPT_INJECTION_CONCEALMENT — CAT-06
- Opus synthesis RC-07, RC-11 — CAT-06, CAT-07
- skills-checker (Chinese-language deception patterns) — CAT-07
- MarPek6 (8-language scanner) — CAT-07
- emelyanowcom/skill-sanitizer (length heuristic) — CAT-17
- skillguard-cli (zero-width + HTML concealment) — CAT-18
- zantific/skill-security-review-lens (LOW-005, LOW-006) — CAT-19
