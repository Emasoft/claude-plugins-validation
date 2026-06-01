---
trdd-id: de582146-76f9-47ea-817e-3fb44fb4997b
title: Cherry-pick SkillSpector static-check ideas — reimplement well in CPV
status: in-progress
created: 2026-06-01T21:27:35+0200
updated: 2026-06-01T21:48:19+0200
---

# TRDD-de582146 — Cherry-pick SkillSpector static checks, reimplement well in CPV

**Filename:** `design/tasks/TRDD-20260601_212735+0200-de582146-skillspector-cherry-pick-static-checks.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative)

Goal (user, `/goal`): "catalogue all the static code checks ideas of the nvidia
skillspector scanner. since it looks pretty sloppy, just cherry pick the good
ideas or the checks we are missing, but reimplement them well in our plugin in a
much better way."

**Catalogue is DONE** (two gitignored evidence reports, both verified):
- `reports/skillspector-eval/CATALOG-GAP-ANALYSIS.md` (Opus, per-category COVERED/PARTIAL/ABSENT + tiers)
- `reports/skillspector-eval/FULL-STATIC-CHECK-CATALOGUE.md` (Opus, ~440 sub-checks + literal corpora)

SkillSpector verdict (objective, empirical): tool itself NOT worth integrating —
confirmed dead TP3 path (never fires on real scans), 6/6 false positives on CPV's
own benign skills (matched "write" in "rewrite", flagged `chmod 0700`, README
`<!--BADGES-START-->`), no code-vs-prose discrimination. ~30-40% of the catalog
*design* is a useful idea-source; the rest is redundant-with-CPV, out-of-scope-for-
static, or FP-prone prose heuristics.

**OUTCOME (2026-06-01):** Phase 1 SHIPPED — `INSECURE_TLS` + `CREDENTIAL_DISCOVERY`
added to skillaudit_patterns.json (re2-safe), re2_compatibility.json regenerated,
21 two-sided tests pass. Phases 2-4 (TR1, LP2, AST7, MP2, PRIVILEGE_ESC/REVERSE_SHELL
extensions) MOVED TO PROPOSALS after the actual CPV code overrode the gap-analysis
tiering: **TR1 = FP** (validate_hook.py:733 deliberately exempts `*` matcher),
**LP2 = redundant** (RC-62 already flags bypassPermissions), AST7 = niche taint
work, MP2 = re2-incompat backref. See
`design/proposals/TRDD-...-b0c85371-skillspector-deferred-checks.md`.

**KEY GOTCHA (load-bearing):** `references/` is INTENTIONALLY NOT doc-only
(cpv_skillaudit_native.py:1023 security bypass-fix — a SKILL.md pointer makes
references/*.md agent-reachable). New non-execution-class rules therefore SUPPRESS
in docs/README but DEMOTE-to-NIT (non-blocking) in references/ — never a blocking
FP. Do NOT add references/ to `_DOC_ONLY_DIR_PREFIXES` (would reintroduce the bypass).

**NEXT ACTION:** integration — regen `.plugin-self-hashes.json` LAST, full serial
pytest, self-scan 0/0/0/0, mypy/ruff, `publish.py --minor`, show breakdown table.

## Verified CPV current coverage (claim-checked, do NOT duplicate)

- TLS-disable: CPV RC-61 catches only the ENV-VAR form (`NODE_TLS_REJECT_UNAUTHORIZED=0`,
  `PYTHONHTTPSVERIFY=0`). In-code `verify=False`/`ssl.CERT_NONE`/`rejectUnauthorized:false`/
  `curl -k`/`wget --no-check-certificate` = genuinely ABSENT.
- Cred paths: `.aws/credentials`, `id_rsa`, `.npmrc`, `.netrc`, `.kube/config`, `.ssh/`
  already covered (CRED_ENV_READ + RC at validate_security.py:871). MISSING = credential
  *discovery/enumeration* shapes (`find ~ -name '*.pem'`, `find / -perm -4000`,
  `os.walk(home)`) + a few infostealer IOCs (`wallet.dat`, `ntds.dit`, `mimikatz`, `lazagne`).
- Hook matcher: validate_hook treats `*`/empty matcher as benign match-all → TR1 ABSENT.
- Taint engine (cpv_taint_engine.py): sinks = exec/eval/compile/os.system/subprocess; AST7
  dynamic `getattr` sink ABSENT.
- Covered-with-more-precision (DO NOT port): TT* taint (RC-73/74/75), SC6 typosquat (RC-30),
  TP2 homoglyph (RC-11), TP1 (MCP_SCHEMA_POISON+RC-49), YR3 cryptominer (RC-67), SC3 obfusc.

## Cherry-pick — phased implementation

**Phase 1 — skillaudit rules** (skillaudit_patterns.json + re2_compatibility.json regen + tests):
1. `INSECURE_TLS` (high/MAJOR, category `network`) — in-code/CLI TLS-verify disabled
   (py `verify=False`/`ssl.CERT_NONE`/`_create_unverified_context`; ts `rejectUnauthorized:false`;
   shell `curl -k|--insecure`, `wget --no-check-certificate`). NOT in _EXECUTION_CLASS_RULES
   (→ suppressed in pure-doc README/references, fires in code + agent-loaded SKILL.md/agents).
   Scoped to NOT duplicate RC-61's env-var form.
2. `CREDENTIAL_DISCOVERY` (high/MAJOR, category `credential_access`) — cred-search/enumeration
   shapes + missing infostealer file IOCs. Same classifier default.

**Phase 2 — structural manifest checks:**
3. TR1 → `validate_hook` overly-broad matcher WARNING (`*`/empty on
   PreToolUse/PostToolUse/PermissionRequest/PermissionDecision; event-scoped → low FP).
4. LP2 → wildcard permission (surface TBD: skill `allowed-tools:["*"]` / settings
   `permissions.allow` / .mcp.json — investigate, JSON-structural, low FP).

**Phase 3 — taint engine:**
5. AST7 → `getattr(x, <tainted>)` dynamic sink in cpv_taint_engine (taint-gated only;
   literal-attr `getattr(o,"x",d)` never fires). Maybe AST1 bare-literal `exec("…")`.

**Phase 4 — selective (decide after 1-3):**
6. MP2 repeated-token padding (structural `((\S)…)\1{20,}` — algorithmic, low FP). [re2-INCOMPAT
   backref → would land in incompat list; or reimplement without backref.]
7. REVERSE_SHELL shape extensions (PowerShell `New-Object …TCPClient`, `mkfifo | /bin/sh`,
   PHP `fsockopen(..exec(`, Ruby `TCPSocket.new(..exec(`).
8. TR3 keyword-baiting skill-description triggers (markdown classifier guard).

## Reimplement-WELL discipline (every check)

- re2-COMPATIBLE patterns only (no `(?=)`/`(?!)`/`(?<=)`/`\1`/`\u`/`\R`); regen
  re2_compatibility.json (surgical append: trust existing 504 verdicts, add new,
  update _source_sha256 + _summary + _generated_at).
- Two-sided tests MANDATORY: benign (README fenced / docstring / doc-only path) STAYS clean;
  real code/CLI FIRES at declared severity. A one-sided test passes with a suppress-everything bug.
- Lean on CPV's per-language context classifiers + `_is_documentation_only_path` (the discipline
  SkillSpector lacks) — never raw regex.
- Test locally with `CPV_SCAN_CACHE=0` (cache keys on scanner_version+catalog_hash+content_hash).

## Acceptance

- self-scan 0/0/0/0; mypy clean; ruff clean; full SERIAL pytest green (`-p no:xdist`).
- re2_compatibility.json regenerated; `.plugin-self-hashes.json` regenerated LAST (after ALL
  edits to patterns.json/re2_compat/any threat-pattern-bearing file) else Gate-3 self-flag.
- `publish.py --minor`; CI/Release/Notify green; show severity-breakdown table before push.

## Evidence
- `reports/skillspector-eval/CATALOG-GAP-ANALYSIS.md`, `reports/skillspector-eval/FULL-STATIC-CHECK-CATALOGUE.md`
- SkillSpector source (read-only): `/tmp/SkillSpector` @ 2eb8447 (v2.0.0); issue NVIDIA/SkillSpector#5.
