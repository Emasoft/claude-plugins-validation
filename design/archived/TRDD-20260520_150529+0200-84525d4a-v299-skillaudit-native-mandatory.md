---
trdd-id: 84525d4a-c401-455f-96b2-4b131b1301e7
title: v2.99.0 SkillAudit native port — MANDATORY in-process security check
column: complete
created: 2026-05-20T15:05:29+0200
updated: 2026-08-25T17:25:05+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-84525d4a — v2.99.0 SkillAudit native MANDATORY scanner

**Filename:** `design/tasks/TRDD-20260520_150529+0200-84525d4a-v299-skillaudit-native-mandatory.md`

## Source

User request (verbatim): *"add this security scanner to the security pipeline (mandatory not skippable): https://github.com/megamind-0x/skillaudit"*

Follow-up (verbatim): *"the scanner says to detect malicious crypto scam in skills... verify if it is true or only an excuse to embed those libs and be a scam itself"*

Final direction (verbatim): *"clone the repo, remove all suspicious lines of code and dependencies, and any external connection/port/download/upload and clean it up to be self sufficient and safe. then take the code and reimplement it in the form of a scanner integrated in the claude plugin validation. beware of hidden chars, bundled scripts, indirect exfiltrations, etc."*

## Background — why we ported instead of integrating the npm package

The `skillaudit` npm package (megamind-0x/skillaudit, v1.1.0) is a JavaScript security scanner that ships 50 rules / 489 patterns for detecting credential theft, data exfiltration, prompt injection, MCP schema poisoning, A2A attacks, shell exec, obfuscation, supply-chain hazards, container escape, persistence, and crypto wallet theft.

Direct integration via `npx skillaudit <plugin_path> --json` was rejected after a two-pass audit:

1. **Ensemble audit** (Gemini 3 Flash + Qwen 3.6 Plus on shipped JS files) — both models reached the same verdict: the SCANNER CODE is safe (zero network on local-scan path, no eval, no postinstall, no fs writes), but `package.json` declares `ethers`, `@x402/evm`, `@x402/express`, `express`, `express-rate-limit` as runtime dependencies. The shipped CLI/scanner code (`bin/skillaudit.js`, `bin/mcp-server.js`, `src/scanner.js`, `src/capabilities.js`, `src/secrets.js`) NEVER imports any of those. They are leftover from `src/server.js` (the hosted vercel API offering x402-based monetized scans), which is excluded from the npm package via the `files:` allow-list.
2. **Repo audit** — cloned megamind-0x/skillaudit; verified all 5 shipped files are clean of hidden chars (no zero-width, no bidi override, no tag chars); confirmed `src/verify-payment.js` (uses ethers + x402) is server-only; confirmed `src/server.js` runs on vercel only.

Making `npx skillaudit` a MANDATORY dependency would drop ~50 MB of unused crypto deps into every CPV user's `~/.npm/_npx/` cache. Any future malicious update to `ethers` / `@x402/*` / `express` would silently land on every CPV user's machine. For a MANDATORY scanner, this is unacceptable.

The user's directive resolved the dilemma: port the SAFE parts to native Python, abandon the npm package entirely.

## Decision

Port the scanning logic to native Python:
* `scripts/cpv_skillaudit_native.py` — pure-Python port. ZERO third-party imports (only stdlib: `base64`, `binascii`, `hashlib`, `json`, `re`, `unicodedata`, `dataclasses`, `pathlib`, `typing`, `urllib.parse`, `collections.abc`). ZERO `subprocess`. ZERO network. ZERO `npx`.
* `rules/skillaudit_patterns.json` — the 50 rules / 489 patterns from the upstream repo HEAD (MIT-licensed; attribution preserved in module docstring).
* `validate_security.py` Check 27 — wires the native scan into every security validation pass. NOT SKIPPABLE — no `CPV_NO_SKILLAUDIT` / `CPV_SKIP_SKILLAUDIT` / similar env var honored. Missing rule catalog → CRITICAL finding (iron rule: any failure to scan is fail-loud).

## Implementation

### Module layout

```
scripts/cpv_skillaudit_native.py     # native port (~700 LOC)
rules/skillaudit_patterns.json       # 50 rules / 489 patterns (bundled)
tests/test_skillaudit_native.py      # 25 regression tests
```

### Logic ported faithfully

1. **Rule-based pattern matching** with `re.IGNORECASE`. Each rule's patterns are compiled once at module load and cached for the process lifetime.
2. **Suppression heuristics** — placeholder detection (`YOUR_`, `xxx`, `REPLACE`, `placeholder`, `example.com`, etc.); doc-context detection (`example`, `usage`, `step N`, `how to`, `tutorial`, `setup`, etc.); markdown table/heading detection; backtick-wrapped reference detection. Mirrors `src/scanner.js::shouldSuppress`.
3. **Code-block tracker** — `_build_code_block_map` walks `\`\`\`...\`\`\`` fences and emits per-line `in_block` flags + range list with lang. Suppression is sharper inside doc-context code blocks; severity is uplifted (`medium`→`high`, `high`→`critical`) inside `bash`/`sh`/`shell`/`zsh` code blocks.
4. **Structural read→net detector** — flags files that contain BOTH read-pattern hits AND network-pattern hits outside instructional context.
5. **URL reputation analyzer** — extracts URLs, parses hostnames, matches against `SUSPICIOUS_DOMAINS` (webhook.site, requestbin, ngrok, etc.) and raw-IP regex.
6. **Invisible Unicode detector** — 20 zero-width / bidi-override / format codepoints, with BOM-at-line-0 exemption.
7. **Intent analyzer** — 10 natural-language patterns for `send/upload/post/forward/delete/exfiltrate/...`.
8. **Hardcoded-secret detector** — 16 detectors for GitHub/AWS/Slack/Discord/Telegram/Vercel/npm/PyPI/OpenAI/Anthropic/Google/Stripe tokens + PEM private keys + JWTs. Suppressed when the line contains a placeholder.
9. **Base64 payload decoder** — finds base64 strings ≥40 chars, decodes, checks printable ratio, scans decoded content for 12 hidden-threat patterns.
10. **Hex/Unicode/CharCode escape decoder** — decodes `\xNN`, `\uNNNN`, `String.fromCharCode(...)`, array-of-charcodes-then-map; scans decoded content for the same 12 hidden-threat patterns.

### Severity mapping

| skillaudit | CPV |
|---|---|
| critical | critical |
| high | major |
| medium | minor |
| low | nit |
| info | info |

### Iron-rule enforcement

* When the rule catalog can't be loaded (missing file / parse error / empty list), `run_skillaudit_scan` returns `invoked=False` and `report_findings` emits a CRITICAL finding via `report.critical()`. There is NO graceful degrade.
* `report_findings` is the only adapter — its existence + behaviour is pinned by `tests/test_skillaudit_native.py::TestIronRuleEnforcement::test_missing_rule_catalog_emits_critical`.

### Tree walker scope

`scan_path` walks the plugin tree, scanning files matching `_SCAN_EXTENSIONS` (`.md`, `.sh`, `.bash`, `.zsh`, `.fish`, `.py`, `.js`, `.ts`, `.mjs`, `.cjs`, `.json`, `.yaml`, `.yml`, `.toml`). Skips `_SKIP_DIRS` (vendored deps + VCS + build cruft + `*_dev/` + `reports/`). Files >2 MB are skipped to bound memory.

### CPV self-scan exemption

* `cpv_validation_common.py::validate_no_absolute_paths` now skips `rules/*.json` files — the rule catalog contains regex strings matching `/etc/passwd`, `/etc/shadow`, `/etc/cron` etc., which are pattern SOURCES (what we look for in scanned plugins), not actual paths the validator follows.
* `validate_security.py::_is_self_scan_eligible` now recognizes `rules/*.json` as a CPV-internal pattern source so self-scan hash verification can short-circuit it cleanly.

## Test coverage

`tests/test_skillaudit_native.py` — 25 tests covering:

* Module + rule catalog presence
* Rule catalog shape (≥40 rules, all have `id`/`severity`/`category`/`patterns`)
* Iron-rule documentation (`MANDATORY`, `iron rule`, `report.critical(`)
* Pure stdlib import gate (no subprocess, urllib.request, requests, etc. in code)
* Malicious skill detection (DATA_EXFIL / PROMPT_INJECT / URL_SUSPICIOUS)
* Clean doc → 0 actionable findings
* Placeholder suppression
* Invisible Unicode detection
* Base64 obfuscation decode + flag
* Suspicious domain detection
* Tree walker recursion + vendored-dir skip
* Missing rule catalog → CRITICAL via `report.critical(`
* Install scanners EXCLUDES skillaudit (it's not an external scanner anymore)
* The old `cpv_skillaudit_scanner.py` is gone (safe-deleted, not present)
* `validate_security.py` imports native module + records step 27 + documents MANDATORY + documents supply-chain rejection + has zero env-var bypass
* Publish-time bypass guard still rejects `PLUGIN_SKIP_SKILLAUDIT` / `CPV_SKIP_SKILLAUDIT` / `SKIP_SKILLAUDIT`

## Acceptance

* [x] `rules/skillaudit_patterns.json` ships in CPV (489 patterns / 50 rules)
* [x] `scripts/cpv_skillaudit_native.py` exists, pure-stdlib
* [x] `validate_security.py` Check 27 wired, non-skippable
* [x] 25 new tests passing
* [x] Full test suite: 5461 passed, 1 skipped, ~17s wall
* [x] Self-scan: 0/0/0/0 + 0 warnings
* [x] npx-based wrapper safe-deleted (`scripts/cpv_skillaudit_scanner.py` + `tests/test_skillaudit_integration.py`)
* [x] `install_all_scanners()` no longer returns `skillaudit`
* [x] `ensure_skillaudit` removed from `cpv_install_scanners.py`

## Attribution

The pattern catalog (`rules/skillaudit_patterns.json`) and the scanner-algorithm design are from megamind-0x/skillaudit (MIT license). The Python port is a clean-room reimplementation in CPV's existing style.

Reference: https://github.com/megamind-0x/skillaudit

## Audit reports

* `reports_dev/llm_externalizer/20260520_144744+0200-code_task-package.json-f75184.md` — ensemble audit of shipped npm files (Gemini 3 Flash + Qwen 3.6 Plus). Both verdicts: SAFE for local-scan path; the npm deps are inert bloat (never imported).
* Repo HEAD audit (no hidden chars, no zero-width tricks, no bundled binaries; `src/verify-payment.js` is the only ethers-using file and it's server-only).

## Approval log

* 2026-08-25T17:25:05+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.99.0 — cpv_skillaudit_native.py is the live core scanner (batch_ab)
