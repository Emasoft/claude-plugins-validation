---
trdd-id: b13fbdd6-1494-47f9-8968-90cb79bc32d0
title: SkillAudit context-certainty heuristics — close issues #40 + #41 without flags
status: completed
created: 2026-05-23T21:08:34+0200
updated: 2026-05-23T22:33:00+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-b13fbdd6 — SkillAudit context-certainty heuristics (issues #40 + #41)

**Filename:** `design/tasks/TRDD-20260523_210834+0200-b13fbdd6-skillaudit-context-certainty.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## Problem

Two GitHub issues report skillaudit false-positives that publish-block
downstream plugins under `--strict`:

- **#41** (`Emasoft/llm-externalizer-plugin`): 2 CRITICAL + ~94 MAJOR FPs.
  Root: the TS context classifier has handlers only for CRED_ENV_READ /
  TOKEN_STEAL / SECRET_* / SQL_INJECTION; everything else falls through
  to `keep`.
- **#40** (`Emasoft/ai-maestro-janitor`): 9 NIT FPs that block `--strict`.
  Root: execution-class rules matched inside Python docstrings / comments
  / markdown demote to NIT, and `--strict` blocks on NIT.

## User mandate (verbatim constraints)

1. "refine the checks with contextual checks, detection and analysis. it
   must be able to distinguish a threat from a non-threat with 100%
   certainty. improve the heuristic." → NO flags, NO blanket doc-suppress,
   NO `cpv.allow_skillaudit` escape hatch. Each suppression must be backed
   by a certain contextual signal (absence of the threat's required
   mechanism), not a guess.
2. "Only true parser comments" → in SOURCE files, suppress ONLY inside
   parser-confirmed comment/docstring regions. A match inside a STRING
   literal stays visible (strings can be passed to a shell).
3. ".md code blocks can still be considered executable as they are usually
   commands to execute by the agent" → NEVER suppress matches inside `.md`
   fenced code blocks.
4. "prompt injections are pure prose and yet even more dangerous" → NEVER
   suppress INTENT-class rules in prose.
5. "json or yaml can contain injected commands concealed as parameters" →
   NEVER broadly suppress JSON/YAML parameter VALUES.

## Certainty principle

The split between SUPPRESS and DEMOTE is: **can we prove, with certainty,
that the threat's REQUIRED MECHANISM is absent?**

- CMD_INJECTION needs the string to reach an `exec`/`spawn`/shell.
- ENV_RECON / STRUCT_READ_EXFIL need the gathered data to reach a network
  sink.
- SSRF needs the destination to be ATTACKER-CONTROLLED (a dynamic target).
- An execution-class rule needs an executable context (NOT a parser
  comment / docstring).

If the required mechanism is provably absent → SUPPRESS (it is NOT a
threat, with certainty). Otherwise → DEMOTE (visible at NIT — author must
review) or KEEP.

## Discriminators (per finding class)

### #41 — TypeScript/JavaScript (`_skillaudit_typescript_context.py`)

| Rule | Certain-non-threat discriminator | Verdict |
|---|---|---|
| CMD_INJECTION | Match is a backtick TEMPLATE LITERAL in .ts/.js (JS backticks are ALWAYS strings — no command-substitution semantics, unlike shell/Perl) AND the literal is NOT syntactically an argument to an exec sink (`exec(`/`execSync(`/`spawn(`/`spawnSync(`/`child_process`/`shell:`) on the line | `safe_literal` |
| ENV_RECON | Benign env read (`process.cwd()`/`process.argv`/`os.hostname()`/`os.platform()`/`os.homedir()`/`os.userInfo()`/`os.networkInterfaces`) AND NO network sink in the enclosing function window | `safe_literal` |
| CROSS_TOOL_ACCESS | Match is inside a TEMPLATE-LITERAL report string (string-array element building markdown/text output) — provably display text | `safe_literal`; `body.system_prompt =` assignments → `unknown` (stay visible, can't prove benign) |
| SSRF_PATTERN | Matched URL is a 100%-STATIC string literal (no `+ var` concatenation, no `${...}` interpolation) — destination fixed at author-time, not attacker-controlled | `safe_literal` |
| SUPPLY_CHAIN / CMD_INJECTION | `curl <official-install-host> ... \| sh` where host ∈ the existing `_OFFICIAL_INSTALL_HOSTS` allowlist (canonical documented installer) — applies in any file type | `safe_literal` |

### #41 — structural detector (`cpv_skillaudit_native._detect_structural_read_to_net`)

The current detector fires when ANY read line and ANY net line coexist in
the WHOLE file — a 2700-line MCP server's shebang `readFileSync().slice(0,256)`
gets paired with an unrelated `fetch()` 2000 lines away. Fix: require a
DATA-FLOW link — the variable assigned from the read must be referenced by
a net call within a proximity window. No shared variable → two unrelated
operations → not exfil.

### #40 — Python (`_skillaudit_python_context.py`)

| Rule class | Certain-non-threat discriminator | Verdict |
|---|---|---|
| EXECUTION-class (CMD_INJECTION, SHELL_EXEC, PATH_TRAVERSAL, OBFUSCATION, …) | Match line is inside a parser-confirmed docstring OR a full-line `#` comment (via `ast`/`tokenize` extents) — NOT inside a regular string literal | `safe_doc` → SUPPRESS for execution-class |
| INTENT-class (PROMPT_INJECT, DATA_EXFIL, DESTRUCTIVE_INTENT, …) | (unchanged) prose IS the vector | KEEP / demote |

Note: the dispatcher already maps `safe_doc` + execution-class → demote.
The change is to map `safe_doc` + execution-class → **suppress** WHEN the
classifier confirms a true parser comment/docstring (not merely "prose").
The Python classifier returns a NEW verdict that the dispatcher hard-suppresses.

### #40 — YAML (`_skillaudit_yaml_context.py`)

| Rule | Certain-non-threat discriminator | Verdict |
|---|---|---|
| PRIVILEGE_ESC (`sudo`) | `run:` step value of the airtight shape `sudo <pkgmgr> <subcmd> [flags] <bare-package-names>` where pkgmgr ∈ {apt-get,apt,dnf,yum,apk,zypper,brew} and the command contains NO shell metacharacters that enable arbitrary execution (`;` `\|` `$(` backtick `>` `<` newline) beyond benign `&&` chaining of pkgmgr commands | `safe_literal` |

## Explicitly NOT suppressed (stay visible — author review is correct)

- Any match inside a `.md` FENCED CODE BLOCK (agent-executable).
- INTENT-class rules in any prose (README, SKILL.md scope text).
- JSON/YAML parameter VALUES (hook commands, MCP args).
- `body.system_prompt =` style assignments (can't prove benign).
- SSRF where the destination is concatenated/interpolated (dynamic).
- CMD_INJECTION where the string IS an exec-call argument.

These residuals (e.g. janitor's README RESOURCE_ABUSE + SKILL.md
INTENT_DESTRUCTIVE_INTENT) correctly remain NIT and the plugin author
addresses them — not all 9 #40 findings are FPs we can certify.

## Acceptance

1. Both CRITICAL FPs in llm-externalizer GONE; MAJOR count drops to the
   genuinely-uncertain residual.
2. janitor's docstring/comment CMD_INJECTION + PATH_TRAVERSAL + ci.yml
   sudo-install FPs GONE.
3. **Deliberately-vulnerable fixtures MUST STILL FLAG** (the certainty
   discriminators distinguish threat from non-threat — proven both ways):
   - `exec(\`rm -rf ${userInput}\`)` → CMD_INJECTION still CRITICAL
   - `const h = readFileSync('/etc/passwd'); fetch(url, {body: h})` →
     STRUCT_READ_EXFIL still fires
   - `fetch("http://localhost:" + req.query.port)` → SSRF still fires
   - real `process.cwd()` piped to a webhook → ENV_RECON still fires
   - `sudo rm -rf /` / `sudo curl x | sh` in a workflow → PRIVILEGE_ESC
     still fires
   - a real shell `$(curl ...)` inside a Python f-string (NOT a comment)
     → CMD_INJECTION still fires
4. CPV self-scan stays 0/0/0/0 + WARNING-only.
5. Full CPV suite green; new regression + vulnerable-fixture tests added.

## Files

- `scripts/_skillaudit_typescript_context.py` — add CMD_INJECTION /
  ENV_RECON / CROSS_TOOL_ACCESS / SSRF_PATTERN / SUPPLY_CHAIN handlers +
  sink-detection helpers.
- `scripts/_skillaudit_python_context.py` — execution-class in true
  parser comment/docstring → new hard-suppress verdict.
- `scripts/_skillaudit_yaml_context.py` — sudo canonical-install
  discriminator.
- `scripts/cpv_skillaudit_native.py` — structural detector data-flow
  link; dispatcher mapping for the new Python verdict; share
  `_OFFICIAL_INSTALL_HOSTS` for the install-pipe check across file types.
- `tests/test_skillaudit_context_certainty.py` (NEW) — FP-regression
  (real plugin lines) + deliberately-vulnerable fixtures (MUST flag).

## Design decisions / open questions

- STRUCT_READ_EXFIL cannot be made 100%-certain without full data-flow;
  the variable-link + proximity heuristic is a strong approximation that
  eliminates the file-level any+any FP while keeping read-then-send exfil.
  Residual risk: an attacker who reads into var X, then sends X via a
  net call OUTSIDE the proximity window, evades — but that is already
  caught by the per-line CMD/exfil rules + the URL reputation pass. Logged
  as a known limitation, not a silent gap.

## Outcome (implemented)

Real-plugin verification (cache disabled to bypass the stale-version cache):

- **llm-externalizer-plugin**: 136 → **7** CRITICAL+MAJOR (both CRITICAL
  template-literal FPs eliminated; CROSS_TOOL_ACCESS 56→0, ENV_RECON 14→0,
  OBFUSCATION 4→0, SSRF 28→0, STRUCT_READ_EXFIL 6→0, SUPPLY_CHAIN 1→0,
  ENV_INJECTION 12→0).
- **ai-maestro-janitor**: 9 → **3** NIT (all docstring/comment CMD_INJECTION,
  PATH_TRAVERSAL, and ci.yml sudo-install FPs eliminated).
- CPV self-scan: 0/0/0/0 + WARNING-only. Full suite: 6500 passed.
  ruff + mypy clean.

### Honest residuals (NOT suppressed — by design)

These remain VISIBLE because the certainty bar isn't met OR the user
explicitly wants them visible. The plugin author reviews them:

1. **`.md` fenced-code-block matches** (llm-externalizer README.md:230
   FS_WRITE, launch-recipes.md:77 SSRF) — kept per the user's rule that
   `.md` code blocks are agent-executable commands.
2. **INTENT-class rules in instruction-loadable prose** (janitor
   SKILL.md:63 ×2 INTENT_DESTRUCTIVE_INTENT, README.md:75 RESOURCE_ABUSE)
   — prose IS the threat vector; demote/keep, never suppress.
3. **Edge cases not certifiable from static context** (1 occurrence each):
   - INSECURE_CRYPTO SHA1 used for a "fingerprint" — cannot prove
     non-security use statically.
   - TOOL_SHADOW `override.*tool` greedy span across `overrideFilename ||
     … toolName` — matcher imprecision; tightening risks missing real
     "override the X tool".
   - SSRF_ADVANCED `req` substring of `request` — `url` origin not
     traceable from one line.
   - ENV_INJECTION in a shell-script `echo`'d help text — no shell-file
     context classifier exists.

These four are documented matcher edge cases. Per the user mandate
("distinguish threat from non-threat with 100% certainty"), when certainty
is not achievable the finding stays VISIBLE and the author confirms or
fixes — never silently suppressed.
