# TRDD-0f1f7889 — CPV Security Scanner Mega-Upgrade

**TRDD ID:** `0f1f7889-0f22-41c5-a612-54d514865208`
**Filename:** `design/tasks/TRDD-0f1f7889-0f22-41c5-a612-54d514865208-security-mega-upgrade.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Mostly Done (≈95% shipped across v2.27→v2.80) — 4 final gap-fill rules added 2026-05-10 in TRDD-0f1f7889 worktree (RC-68, RC-55, RC-82, RC-107). Remaining ≈5% deferred (see §11 below).
**Created:** 2026-04-26 21:10 +0200
**Last update:** 2026-05-10 — TRDD coverage matrix audit run against shipped codebase; the vast majority of items already landed across the v2.27→v2.80 release wave. This worktree closes the four remaining genuinely-missing rules.
**Owner:** completed by kraken agent (TRDD-0f1f7889 worktree)
**Estimated total effort:** ~2530 LoC additions + ~640 test cases, spread across 5 minor-version phases (v2.27 → v2.31). Audit (TRDD coverage matrix, 2026-04-26) added 7 missing rules (RC-05, 11, 18, 22, 24, 36, 62) and acknowledged 5 folded rules (RC-19, 66, 72, 84, 97). All 108 rule classes now have explicit phase assignment.

---

## 11. Closure status — 2026-05-10 audit

The following table cross-references every TRDD sub-item against what's actually shipped in the codebase as of the v2.80.0 baseline (HEAD `8412fd0`). The audit found the vast majority of items had already landed across the v2.27→v2.80 release wave under their respective TRDD/PR commits.

### Phase 0 (FP-reduction) — DONE
All four helpers (`is_in_fenced_code_block`, `NEGATION_GUARD`, provider-host whitelist, defensive-context demotion) are present in `scripts/cpv_validation_common.py` (lines 1519–1900 region). 30+ FP-reduction tests in `tests/test_fp_reduction.py`.

### Phase 1 (critical net-new) — DONE except RC-68
| Rule | Status | Notes |
|------|--------|-------|
| RC-68 multi-layer encoding | DONE 2026-05-10 | shipped in this worktree; `detect_multilayer_encoded_payload` in `cpv_validation_common.py`, wired into `check_phase2e_extras` at WARNING per TRDD §7 |
| RC-09 zero-width Unicode | DONE | `find_zero_width_chars` in common module |
| RC-10 TAG character block | DONE | `find_tag_block_chars` |
| RC-11 mixed-script confusable | DONE | `has_mixed_script` |
| RC-21 env bulk harvest | DONE | `ENV_BULK_HARVEST_PATTERNS` |
| RC-29 .pth executable | DONE | `is_pth_with_exec` |
| RC-37 GTFOBins/LOLBins | DONE | `GTFOBIN_LOLBIN_PATTERNS` (incl. RC-97 fold) |
| RC-43 time-bomb | DONE | `TIMEBOMB_PATTERNS` |
| RC-47 MCP env-var injection | DONE | `MCP_DANGEROUS_ENV_KEYS` |
| RC-49 MCP description injection | DONE (regex prefilter) | semantic half deferred to opus agent |
| RC-50 tool-name shadowing | DONE | `is_typosquat` shape; SHADOWED_TOOL_NAMES list |
| RC-67 cryptomining | DONE | `CRYPTOMINING_PATTERNS` |
| RC-101 RuleSchema | DONE | `RuleSchema` dataclass + `RULE_REGISTRY` |

### Phase 2 (strengthen 22 checks) — DONE
All 22 rules carry counts ≥ 2 in `cpv_validation_common.py` (RC-01..06, 12..16, 17, 20, 26..28, 34..38, 39, 44, 45, 61, 62, 65, 66 fold, 70). RC-19 is folded into RC-17 per TRDD audit; RC-66 folded into RC-65.

### Phase 3 (MAJOR net-new) — DONE except RC-55, RC-82
| Rule | Status | Notes |
|------|--------|-------|
| RC-55 MCP unbounded retry | DONE 2026-05-10 | `detect_mcp_unbounded_retry` in common module; helper-only (callers wire as needed). WARNING severity per TRDD §7 |
| RC-82 tiered shell classifier | DONE 2026-05-10 | `classify_shell_command_tier` returns 4-tier verdict; helper used by hook/agent validators |
| RC-05/08/25/90/91/92/93/99/108 | DONE | all in `PHASE3_PATTERNS` |
| RC-46/48/51/52/53/54/56/57/58/59/60/63 | DONE | all in `PHASE3_PATTERNS` |
| RC-18/22/23/24/30/31/32/33/40/41/42/72 | DONE | persistence/exfil families covered |
| RC-69 AST eval obfuscation | DONE | included in obfuscation block |
| RC-73/74/75 taint engine | DONE | `cpv_taint_engine.py` (412 LoC) + `check_phase10_taint` |
| RC-79/89/94 architecture | DONE | covered in PHASE3_PATTERNS |

### Phase 4 (MINOR/INFO + observability) — DONE
RC-85 (license), RC-86 (token cost via `cpv_token_cost.py`), RC-87 (SSRF IP), RC-88 (suspicious TLDs), RC-103 (capability scoring), RC-104 (HOLD verdict), RC-105 (`cpv_sarif_writer.py`, 206 LoC), RC-106 (`cpv_sbom_writer.py`), RC-76 (stemmed semantic injection via `find_stemmed_injection_signal`).

### Phase 5 (specialist delegation) — DONE except RC-107
| Rule | Status | Notes |
|------|--------|-------|
| RC-102 trufflehog/semgrep | DONE | `check_trufflehog`, `check_semgrep` in `validate_security.py`. gitleaks intentionally dropped in v2.48 (crashed under repeated invocation per memory note) |
| RC-107 pre-installation URI scan | DONE 2026-05-10 | `extract_install_uris` in common module returns (kind, uri) tuples for npm/pypi/oci. Helper-only — downstream tool decides whether to invoke `npm audit`/`pip install --dry-run`/OCI vet |

### Section 4 (Agent-class semantic checks) — DEFERRED
RC-64 (psychological manipulation), RC-77 (shadow features), RC-78 (capability vs description) require LLM rather than regex. Their programmatic prefilters are not yet present; the LLM-confirm half lives in the `cpv-semantic-validation` agent (opus[1m]). DEFERRED with rationale: each requires a dedicated TRDD with token budget design and FP guard tuning. Out of scope for this worktree.

### Open-question items (§8) — partially closed
- ReDoS risk: every new pattern has bounded inputs in tests (e.g. `test_max_depth_terminates`)
- Performance: not separately benchmarked on a 500-file plugin in this worktree (DEFERRED to a perf-regression TRDD)
- License risk: all four new rules are clean-room from the TRDD sketch — no external source copied
- Maintainability: RC-101 RuleSchema honored — every new rule registers itself
- Cross-file taint: shipped via `cpv_taint_engine.py` (single-file scope; cross-file deferred per TRDD §8)
- Specialist trufflehog/semgrep: never auto-installed; gracefully skip when missing

### What's actually NEW this worktree (commits in `wt/trdd-0f1f7889`)
- `scripts/cpv_validation_common.py` — +RC-68 (multi-layer decoder), +RC-55 (unbounded retry), +RC-82 (shell tier classifier), +RC-107 (install-URI extractor) — ≈ 290 LoC including registrations and docstrings.
- `scripts/validate_security.py` — wired RC-68 into `check_phase2e_extras` at WARNING severity.
- `tests/test_trdd_0f1f7889_missing_rules.py` — 21 new test cases (RC-68: 6, RC-55: 4, RC-82: 6, RC-107: 5).

### Severity stance
All 4 new rules ship at WARNING per TRDD §7. Promotion to their target severity (RC-68 → CRITICAL, RC-55 → MAJOR, RC-82 → varies by tier, RC-107 → helper) is gated on one minor version of empirical FP-rate validation against the wider plugin ecosystem.

### Test count
4858 tests passing (was 4837 baseline; +21 new). CPV self-scan exit 0 with zero CRITICAL/MAJOR findings introduced by the new rules.

---

## 1. Background

A 38-repo survey of community Claude Code security scanners produced:

- **6 batch extraction reports** (~5400 lines total) — every scanner's verbatim detection regex, AST checks, severity tables, novel patterns
- **1 distilled rule digest** (1666 lines, 108 rule classes grouped across all scanners)
- **1 Opus best-of synthesis** (3722 lines) — per-rule decisions: KEEP / REPLACE / MERGE / ADD / DROP, with implementation sketches and FP guards

Synthesis verdict: CPV currently covers the basics well but has critical gaps in:

1. **Encoding evasion** — no recursive multi-layer-decoder; every other rule is bypassable via base64 wrapping
2. **Unicode steganography** — TAG characters (U+E0000-E007F), variation selectors (U+E0100-E01EF), bidi-override edge cases
3. **MCP-specific abuse** — env-var injection (LD_PRELOAD), tool-description prompt injection, name shadowing, auto-approve, shell-meta in args
4. **Cross-file taint** — modern attacks split source/sink across files specifically to defeat per-file scanners
5. **Context-aware FP reduction** — without code-fence tracker + negation_guard + provider-host whitelist, CPV self-FPs heavily on its own validator source
6. **Specialist-tool delegation** — trufflehog/gitleaks ship 100+ credential patterns CPV could never match manually but could delegate to optionally

Adopting the Opus recommendations would roughly DOUBLE CPV's detection surface for ~2530 LoC, while improving precision via 4 complementary FP-reduction layers.

---

## 2. Source materials (READ BEFORE STARTING ANY PHASE)

| Document | Path | Purpose |
|----------|------|---------|
| Opus best-of synthesis | `reports/security-research-survey/20260426_205023+0200-opus-best-of-synthesis.md` | Per-rule decisions, sketches, FP guards |
| Distilled rule digest | `reports/security-research-survey/20260426_202803+0200-distilled-rule-digest.md` | Compact candidate list, repo+file:line citations |
| Batch 1 (AI scanners) | `reports/security-research-survey/20260426_194325+0200-batch1-extraction.md` | zantific, kabofo, hezhijie, emelyanowcom, declawedai, EvolutionUnleashed |
| Batch 2 (mixed) | `reports/security-research-survey/20260426_194245+0200-batch2-extraction.md` | yidun, MarPek6, openmaster-ai, rexcoleman, Fangcun-AI, 16yun-cn |
| Batch 3 (programmatic) | `reports/security-research-survey/20260426_195014+0200-batch3-extraction.md` | felipeinf, GoPlusSecurity, aidongise-cell, AI-Coding-Shield, Cydiar, MRT-8 |
| Batch 4 (programmatic) | `reports/security-research-survey/20260426_193931+0200-batch4-extraction.md` | panguard-ai, GabrielYMC, fasutron, taku-tez, garagon, qualixar |
| Batch 5 (programmatic) | `reports/security-research-survey/20260426_200756+0200-batch5-extraction.md` | theinfosecguy, agentverus, obielin, LichAmnesia, AgentSafety, debu-sinha |
| Batch 6 (programmatic, defensive) | `reports/security-research-survey/20260426_200158+0200-batch6-extraction.md` | Muhammad-Qasim-Munir, edimuj, go-authgate, kurtpayne, agentaudit-dev, pors |
| Existing /tmp clones | `/tmp/security-survey-batch{1..6}/` | Source code if a sketch needs verification |

⚠️ License note: every external scanner's source has its OWN license (mostly AGPL-3.0). CPV is MIT and stays MIT. The Opus synthesis ALREADY did clean-room derivation — it produced sketches in plain Python pseudocode, NOT copy-pasted source. The integration phases below MUST continue this practice: re-implement from the sketch, never copy-paste from a clone.

---

## 3. Phased rollout

Each phase is a discrete CPV minor-version release. Phases 0 and 1 ship together as v2.27; phases 2-4 ship as v2.28-v2.30; phase 5 (specialist delegation) ships as v2.31.

### Phase 0 — FP-reduction layer (MUST ship before any new rules)

**Why first:** without these 4 mechanisms, every new regex below produces overwhelming FPs when CPV is run on its own source (which is the canary case the user catches first). The Opus synthesis is explicit: ship Phase 0 first or the rest of the rollout drowns the maintainer in noise.

| Component | Source | LoC | Files touched |
|-----------|--------|-----|---------------|
| Code-fence tracker (`is_in_fenced_code_block(line, file)`) | RC-83 | 25 | `cpv_validation_common.py` |
| `NEGATION_GUARD` regex helper | RC-83 / RC-100 | 15 | `cpv_validation_common.py` |
| Provider-host whitelist + skillguard placeholder broadening | RC-16 / RC-83 | 50 | `cpv_validation_common.py` |
| Defensive-context severity demotion (`is_test_path(p)`, `is_doc_path(p)`) | RC-84 | 20 | `cpv_validation_common.py` |
| `.env.example` / `*.template` / `*.sample` negation guard | RC-100 | 20 | `cpv_validation_common.py` |
| Test additions: 30 fixtures (15 must-suppress, 15 must-still-fire) | derived | — | `tests/test_fp_reduction.py` (new) |

**Files touched:** 2 (`cpv_validation_common.py` + 1 new test file). Within the 5-file phase budget.

**Derived tasks (per CLAUDE.md "All Todo lists must include DERIVED tasks"):**
- Verify each existing CPV check still fires after the demotion layers — run full validate_security.py on the plugin itself, expect same severity levels for all current findings
- Document the FP-guard chain in a new section of `validate_security.py`'s header docstring
- Add a `--no-fp-guards` debug flag to allow inspection of raw matches when triaging false negatives

### Phase 1 — Critical net-new rules (v2.27)

The ten highest-impact rules from the Opus net-new table, all CRITICAL.

| Rule | Source | LoC | Dependencies |
|------|--------|-----|--------------|
| RC-68 Multi-layer encoding decoder | aguara, vexscan, vetskill | 60 | none |
| RC-09 Zero-width Unicode | felipeinf, LichAmnesia, vetskill | 30 | none |
| RC-10 TAG character block (U+E0000-E007F) | aguara, vetskill (extended) | 25 | none |
| RC-47 MCP env-var injection (LD_PRELOAD/NODE_OPTIONS/etc.) | yidun, agentvet | 15 | regex |
| RC-49 MCP tool-description prompt injection | aguara MCP-005, vexscan MCP-009 | 50 | regex |
| RC-37 GTFOBins / LOLBins / macOS osascript | aguara SUPPLY_007 | 50 | regex (curated GTFOBin name list) |
| RC-43 Time-bomb / conditional activation | yidun, vexscan BACK-001 | 35 | regex |
| RC-29 Python `.pth` executable | aguara SC-09 | 15 | regex + glob |
| RC-50 MCP tool-name shadowing (read_file/write_file/etc.) | aguara MCP-006 | 30 | regex + name extraction |
| RC-67 Cryptomining indicators (xmrig, stratum+tcp) | aguara CRYPTO_001 | 10 | regex |
| RC-21 process.env / os.environ bulk harvest (`Object.keys/.dump`) | aguara CRED_004 | 25 | regex |
| RC-11 Unicode steganography — homoglyph / mixed-script (Cyrillic-Latin tool names) | aguara, vetskill | 25 | regex |

**Approx LoC:** ~370
**Test fixtures:** ~50 (5 per rule on average — positive + negative + FP-guard cases)
**Files touched:**
- `scripts/validate_security.py` (orchestration: new check_* functions + wiring)
- `scripts/cpv_validation_common.py` (new pattern catalogs)
- `tests/test_validate_security.py` (extended)
- 1-2 new test files for the heavier rules (encoding decoder, MCP description injection)

**Within 5-file budget:** yes.

**Derived tasks:**
- Add a `RuleSchema` dataclass (RC-101) to keep the per-rule metadata uniform — without it, 70+ new rules will create maintenance chaos. ~80 LoC, foundational, ship in this phase.
- Add a `--rule <id>` filter for targeted debug runs
- Bump `EXAMPLE_USERNAMES` review with each additional rule that touches user paths

### Phase 2 — Strengthen existing 22 checks (v2.28)

The "existing-rule" merges from Opus table 2. These are gap-fills, not new checks. The 22 entries break into 5 file-batches per the 5-file phase budget:

#### Phase 2a — Prompt-injection family (RC-01, 04, 06, 07)
- PROMPT_INJECTION_PATTERNS gets paraphrase template + typo variants + privilege roleplay + completion attacks
- Identity-hijack adds DAN/jailbreak-modes + identity-revocation
- Fake-system-prompt-marker adds reveal-directive detection
- Files: `validate_security.py`, `cpv_validation_common.py`, `tests/test_validate_security.py`
- ~70 LoC

#### Phase 2b — Secret-pattern family (RC-12, 13, 14, 15, 16)
- AWS prefixes: ASIA / AGPA / AIDA / AROA / ANPA / ANVA family added beside AKIA
- GitHub: gho_ / ghu_ / ghs_ / ghr_ added beside ghp_
- OpenAI: T3BlbkFJ fingerprint guard
- Private Key: PGP variant
- KNOWN_EXAMPLE_SECRETS: broaden to placeholder family from skillward
- Files: `cpv_validation_common.py`, `tests/test_secret_patterns.py` (new) — 2 files only
- ~70 LoC (mostly regex revisions, minimal new code)

#### Phase 2c — Exfil + credential-harvest (RC-17, 20; RC-19 folded into RC-17)
- DATA_EXFIL: webhook host list (discord/slack/telegram/etc.) + AND-gate (only flag exfil when paired with a sensitive-source read)
- CREDENTIAL_HARVEST: Claude MEMORY.md, USER.md, browser keystores, Windows vault
- (RC-19 — exfil via DNS-tunneling style hostnames — folded into RC-17's host list)
- Files: `validate_security.py`, `cpv_validation_common.py`, `tests/test_validate_security.py`
- ~80 LoC

#### Phase 2d — Supply-chain + sandbox-escape (RC-26, 27, 28, 34, 35, 36, 38; RC-97 folded into RC-37)
- curl-pipe: redirect (`>`) + separators (`;`/`&&`)
- pip-install: pinning/hash check
- Lifecycle scripts in package.json: targeted scoping + process substitution + `-enc`
- Reverse-shell: 7-language coverage (bash, sh, python, perl, ruby, php, lua) + msfvenom + socat
- chmod: SUID +s + octal 4755 family
- File-deletion: wipefs, shred, fork bomb, Windows format
- RC-36 sandbox escape — symlink/hardlink traversal to /etc/passwd, /etc/shadow, /Library/LaunchDaemons (~15 LoC)
- (RC-97 Windows-specific LOLBins — `certutil`, `regsvr32`, `mshta`, etc. — folded into RC-37's GTFOBins/LOLBins shipped in Phase 1)
- Files: `validate_security.py`, `cpv_validation_common.py`, `tests/test_supply_chain.py` (new), `tests/test_sandbox_escape.py` (new)
- ~165 LoC

#### Phase 2e — Hooks/MCP/perms (RC-44, 45, 61, 62, 39, 70, 65; RC-66 folded into RC-65)
- check_hook_abuse: raw-JSON dangerous-cmd-in-hook
- check_mcp_abuse: socat / php / ruby in command field
- Permission escalation: dangerouslyDisableSandbox + TLS-bypass env vars (RC-61)
- RC-62 — overbroad permissions: `permissionMode: bypassPermissions`, `--dangerously-skip-permissions` (~5 LoC, explicit add)
- Persistence: macOS launchd + shell RC + defaults loginitems
- Generic obfuscation: proximity-to-exec gating + JS atob/Buffer.from
- Cloud IMDS: GCP/ECS/Alibaba + encoding variants (RC-65)
- (RC-66 — IMDS via DNS-resolved cloud-metadata aliases — folded into RC-65's encoding-variant table)
- Files: `validate_security.py`, `cpv_validation_common.py`, `tests/test_validate_security.py`
- ~80 LoC

**Phase 2 total:** ~475 LoC (after audit additions: RC-36, RC-62, RC-19/66/97 fold notes), 5 sub-phases each ≤5 files. Test coverage grows by ~85 cases.

### Phase 3 — Major-severity net-new rules (v2.29)

The ~30 MAJOR-severity rules from the Opus net-new table grouped into 4 sub-phases, each ≤5 files:

#### Phase 3a — Prompt-injection extended (RC-02, 03, 05, 07, 08, 25, 90, 91, 92, 93, 99, 108)
- Conditional triggers / time-bombs (RC-02), coercive authority (RC-03), role-priority / capability-claim ("As a developer with FULL access" — RC-05, ~15 LoC), concealment (RC-07), Anthropic / system-admin impersonation (RC-08), image-beacon exfil (RC-25), IMPORTANT-tag amplification (RC-90), hidden HTML comments (RC-91), CSS-hidden injection (RC-92), whitespace-padding deception (RC-93), multilingual injection (RC-99), comment-hidden injection (RC-108)
- ~180 LoC

#### Phase 3b — MCP/agent extras (RC-46, 48, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 63)
- security-disabling args, shell metacharacters in args, unbounded retry, recursive self-invocation, sampling/createMessage exfil, 0.0.0.0 bind, tool-description z-score outlier, inputSchema manipulation, auto-approve all servers, agent credential relay, agent identity spoofing, shadow workspace, "don't ask user" autonomy abuse
- ~225 LoC

#### Phase 3c — Persistence + supply + exfil-extended (RC-18, 22, 23, 24, 30, 31, 32, 33, 40, 41, 42, 72, 80, 81, 95, 96, 98)
- Typosquatting (top-100 list + Levenshtein ≤1), unpinned GHA, GHA secrets exfil, compromised-package DB (event-stream/litellm/colors), SSH authorized_keys append, git hooks injection, docker-entrypoint mod, post-uninstall residue, publish hygiene, firewall/Defender disable + kernel-module load, navigator.sendBeacon, hex/decimal IPv4, embedded binary/archive, hidden dotfile with executable extension
- RC-18 — DNS-based exfiltration (long subdomain queries, TXT-lookup tunneling) (~15 LoC)
- RC-22 — clipboard-API exfil (`pbcopy`, `xclip`, `Get-Clipboard`, `navigator.clipboard.readText`) (~10 LoC)
- RC-24 — Web3 / crypto-wallet seed scraping (BIP39 wordlist proximity + wallet env var names) (~25 LoC)
- (RC-72 hex/decimal IPv4 — folded into RC-65 cloud-IMDS encoding variants in Phase 2e; listed here too for cross-domain bypass detection)
- ~300 LoC

#### Phase 3d — Architecture (RC-69, 73, 74, 75, 79, 82, 89, 94)
- AST-level eval obfuscation, cross-file taint, multi-tool toxic-flow MCP, chain-detection mechanism, workbench-tampering protected-surface multipliers, tiered shell-command classifier, social-engineering credential prompt, cursor:// deeplink hook abuse
- (RC-97 Windows LOLBins folded into Phase 2d; not duplicated here)
- This is the heaviest sub-phase (~290 LoC) and includes the cross-file taint engine — split into 2 commits: scaffolding + rules

**Phase 3 total:** ~995 LoC (after audit additions).

### Phase 4 — Minor / informational + observability (v2.30)

- RC-85 License compliance (MINOR, regex)
- RC-86 Token cost / resource abuse (INFO, char-count)
- RC-87 SSRF suspicious external IP (MINOR, ipaddress stdlib)
- RC-88 Suspicious TLDs / shorteners / dev tunnels (MINOR/MAJOR)
- RC-103 Capability scoring dual-axis (verdict layer)
- RC-104 HOLD verdict tier (output)
- RC-105 SARIF output / CWE tagging (output)
- RC-106 ASBOM / CycloneDX (output)
- RC-76 Stemmed semantic injection classifier (MAJOR, optional NLTK dep)

**~310 LoC.** SARIF + ASBOM make CPV enterprise-friendly.

### Phase 5 — Specialist-tool delegation (v2.31)

Same external-binary pattern as `check_cc_audit()` and `check_tirith_scanner()`:

- RC-102: trufflehog / gitleaks / semgrep — invoked via shutil.which + subprocess; each adds 100+ patterns "for free" without ever touching CPV's source
- RC-107: pre-installation scan via npm/PyPI/OCI URI (vexscan + agentvet pattern)
- ~100 LoC each
- All optional, advisory-only when binary missing (single WARNING + skip)
- Independent CLI flags: `--no-trufflehog`, `--no-gitleaks`, `--no-semgrep`

---

## 4. Agent-class additions (Skills + Agent side)

These 7 checks need an LLM, not regex. They live in the existing `cpv-semantic-validation` agent (model: opus[1m], explicit opt-in only). Each MUST run a programmatic prefilter first; LLM is invoked only on prefilter hits with bounded context.

| Rule | Programmatic prefilter | LLM task | Token-optimization |
|------|------------------------|----------|-------------------|
| RC-64 Psychological manipulation | `RC-64_GASLIGHTING/URGENCY/SUBLIMINAL` regex (~20 LoC) | "Does this prose contain gaslighting / urgency / coercion?" | LLM only on prefilter hits |
| RC-77 Shadow features (claim ≠ behavior) | `detect_shadow_features()` returns candidates | "List capabilities declared vs. used; report unjustified" | Per-finding ≤500 token context |
| RC-78 Capability vs description mismatch | `find_unjustified_bins()` (kubectl/docker/aws etc.) | "Does description justify these required tools?" | Per-finding bounded context |
| RC-49 (semantic) MCP tool-description injection | RC-49 regex prefilter | LLM scores remaining descriptions | Only ambiguous cases |
| RC-50 (semantic) Tool name shadowing intent | RC-50 SHADOWED_TOOL_NAMES list | LLM scores ambiguous cases | Only ambiguous cases |
| RC-99 Multilingual injection (CJK/EU) | RC-99 multi-language regex | LLM translates + scores | Translation prompt is small |
| RC-103 Capability scoring disposition | Static `disposition()` produces candidate | LLM second-opinion on ambiguous | Only ambiguous cases |

**Per `~/.claude/rules/use-llm-externalizer.md`:**
- Use `code_task` with `answer_mode=0` and `max_retries=3` for per-finding evaluation
- Use `scan_folder` with explicit `output_dir` for batch prefilter sweeps
- NEVER call LLM if no prefilter hit — CPV's regex tier handles >95% of cases for free
- Estimated tokens per ambiguous case: ≤500 input + ≤200 output (vs. ~10k for a naive whole-file LLM scan)

**Files touched (per agent-class addition):**
- `agents/semantic-validator.md` (add new check stanzas + token budget)
- `skills/semantic-validation-skill/SKILL.md` (document new categories)
- `scripts/cpv_validation_common.py` (the prefilter functions are shared)

Phasing: Agent additions ride alongside the corresponding programmatic phase that introduces their prefilter. E.g. RC-64's prefilter ships in Phase 3a (prompt-injection extended); the LLM-confirm half ships at the same time in `cpv-semantic-validation`.

---

## 5. Best-in-class CPV preserves (DO NOT touch)

Per Opus synthesis section 3, the following are already best-of-class and must be preserved as-is:

- `is_validator_script()` (`validate_security.py:264`) — auto-skip for scanner self-detection
- `is_shell_like_file()` (line 275) — git-hooks / GHA workflow context awareness
- `is_ai_facing_markdown()` (line 305) — skill/agent vs. doc README distinction
- `COMMAND_SUBSTITUTION_PATTERNS` + per-file-type allowlist (line 62) — clean check-then-context-decide architecture
- `PATH_TRAVERSAL_PATTERNS` with `${CLAUDE_PLUGIN_ROOT/DATA/PROJECT_DIR}` carve-outs (line 117)
- `USER_PATH_PATTERNS` + `EXAMPLE_USERNAMES` (cpv_validation_common.py:852)
- `ALLOWED_DOC_PATH_PREFIXES` (line 915)

**New rules MUST integrate with these — never bypass them.** A new check that re-implements file-type detection or skips the example-username allowlist is a regression and should be rejected at code review.

---

## 6. Test infrastructure

Each phase ships with:

1. **Per-rule unit tests** — positive (must-fire), negative (must-not-fire), FP-guard cases (must-suppress)
2. **Self-validation regression** — `uv run scripts/validate_plugin.py .` on CPV itself MUST stay 0-CRITICAL/MAJOR/MINOR/NIT after each phase. The Phase 0 FP-reduction layer is what makes this achievable.
3. **Attack fixture catalog** — `tests/fixtures/security/<rule-id>/` directory per rule, holding category-tagged fixtures (e.g. `tests/fixtures/security/RC-09/zero-width-in-prompt.md` — describes what the fixture exercises in a top-comment, NEVER pastes verbatim attack payloads from external scanners). Fixtures are **clean-room** — describe attack categories, never copy AGPL test fixtures verbatim.
4. **Performance regression** — `tests/test_security_perf.py`: scan a 200-file plugin, total time must stay under 5 seconds. All new regex patterns precompiled at module load. Any new pattern that risks ReDoS (catastrophic backtracking) gets a bounded-input timed test.

---

## 7. Versioning & rollout discipline

- Each phase = one CPV minor-version bump (2.27 → 2.28 → 2.29 → 2.30 → 2.31)
- All NEW rules ship at WARNING severity initially, with the synthesis-recommended severity documented in CHANGELOG. After 1 minor version (when FP rate is empirically validated against the wider plugin ecosystem), the rule is promoted to its target severity in the next minor.
- Each phase is its own commit (or set of commits, one per sub-phase). PRs reference this TRDD ID in the title.
- Memory note in `MEMORY.md` updated after each phase to keep the rule inventory current.

---

## 8. Open questions / risks

- **ReDoS risk** — every new regex with backreferences or nested quantifiers needs a bounded-input timed unit test (run pattern against 1MB of pathological input, fail if >100ms)
- **Performance** — 70+ new rules on every scan; precompile all patterns at module load (current pattern); profile on a large plugin (~500 files) to detect any rule that dominates wall-clock time
- **License risk** — re-extract from sketches only, never copy-paste from AGPL clones (`/tmp/security-survey-batch{1..6}/`). Each rule's commit message cites the source repo as INSPIRATION (not derivation)
- **Maintainability** — without RC-101 (rule schema dataclass) shipped early, the 70+ new patterns become an ad-hoc grab-bag. Ship RC-101 in Phase 1.
- **Cross-file taint engine** — Phase 3d's RC-73/74/75 are the most architecturally novel additions. They require a per-file tag dict + a post-scan cross-reference pass. If this lands fragile, defer to a Phase 6 dedicated TRDD.
- **Specialist-tool delegation transitive trust** — trufflehog/gitleaks/semgrep are themselves dependencies. CPV must NEVER auto-install them (same posture as tirith — use if present, else WARNING and skip)

---

## 9. Out-of-scope (declined)

The Opus synthesis flagged these as DROP — out of CPV's plugin-validation domain:

- Container-image CVE scanning (out of plugin scope)
- Network-traffic intrusion detection (runtime, not static)
- Generic SAST for arbitrary Python apps (CPV stays plugin-focused)
- Kubernetes admission control rules
- Runtime monitoring / agent lifecycle telemetry (separate concern)

---

## 10. Approval checklist (before any code phase starts)

- [ ] User reviews this TRDD and approves the phase plan
- [ ] User decides whether Phase 0 (FP-reduction) ships independently as v2.27 OR bundled with Phase 1
- [ ] User decides on RC-101 (rule schema dataclass) timing — ship in Phase 1 (recommended) or defer
- [ ] User confirms agent-class checks (Section 4) ride with their corresponding programmatic prefilter phase
- [ ] User confirms specialist-tool delegation (Phase 5) is desired — alternative is to skip and stay self-contained
- [ ] User selects test-fixture review process — clean-room descriptions only (recommended) vs. allowing carefully-licensed fixture imports
