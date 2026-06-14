# CLAUDE.md — claude-plugins-validation (CPV)

**Authoritative structure + ops reference for this project. KEEP IT CURRENT:**
whenever you add/remove a command, agent, skill, or script, or change the
menu/publish/validate flow, update the matching line here in the SAME change.
The counts below are load-bearing — README, the menu doc, and
`test_*_preflight` tests assert against reality, so a stale count here means a
stale count everywhere.

## What CPV is

A Claude Code **plugin validator**: it validates plugins / skills / agents /
marketplaces for structural correctness (severity-counted, binary
VALID/INVALID) and security (a skillaudit engine + RC rules + external
scanners). It is itself a plugin, published to the `emasoft-plugins`
marketplace. Repo: `github.com/Emasoft/claude-plugins-validation`.

## Authoritative inventory (verify with the commands shown; update on change)

| Thing | Count | Where / how to list |
|---|---|---|
| **version** | `2.126.16` | `.claude-plugin/plugin.json` → `version` |
| **commands** | **13** | `ls commands/*.md` — 10×`cpv-batch-*`, `cpv-main-menu`, `cpv-pre-install-scan`, `the-skills-menu-create` |
| **agents** | **15** | `ls agents/*.md` |
| **skills** | **46** | `ls -d skills/*/` |
| **scripts** | **114** | `ls scripts/*.py` (25 `validate_*.py` + management/engine/CLI; `_skillaudit_*_context.py` per-language classifiers) |
| **test files** | **321** | `ls tests/test_*.py`; ~9185 tests |

**The 15 agents:** cache-optimizer-agent · cpv-doctor-agent ·
cpv-main-menu-agent · cpv-spark · cpv · marketplace-fixer · plugin-creator ·
plugin-devitalizer · plugin-diagnoser · plugin-fixer · plugin-leaks-preventer
· plugin-manager · plugin-validator · semantic-validator ·
skill-validation-agent.

**Single-visible-command invariant:** only `/cpv-main-menu` is meant to be the
user's entry point; all skills are `user-invocable: false` and load via
`the-skills-menu`. Adding a new top-level visible command breaks this — don't,
unless explicitly asked.

## Menu architecture (the thing that surprised me — now documented)

`/cpv-main-menu` (`commands/cpv-main-menu.md`, `context: fork`,
`agent: cpv-main-menu-agent`) renders an interactive numbered menu of every
CPV capability. It does NOT print menus inline — it routes through the
**external `claude-menu-system` plugin's Stop hook**, which emits the menu
post-turn via `systemMessage` (zero context cost). The menu TREE + per-leaf
execution recipes live in `skills/cpv-main-menu-skill/references/menu-tree.md`
(read this to know what every menu leaf dispatches to). The
`cpv-main-menu-agent` only queues menu specs + parses integer/letter choices;
heavy work is dispatched to the specialised work agents
(plugin-fixer/plugin-creator/plugin-diagnoser/marketplace-fixer/
cache-optimizer-agent/plugin-devitalizer/plugin-leaks-preventer/etc.).

**Dependency:** the menu needs `claude-menu-system` (separate plugin,
`github.com/Emasoft/claude-menu-system`, dev checkout at
`~/Code/claude-menu-system`, has its own `scripts/publish.py`). CPV's menu is
only as bug-free as that hook.

## Key scripts

- **Validators** (`scripts/validate_*.py`, 25): `validate_plugin.py` (the
  user-facing plugin gate), `validate_skill.py` /
  `validate_skill_comprehensive.py`, `validate_marketplace.py`,
  `validate_security.py` (RC security rules), `validate_agent.py`,
  `validate_command.py`, `validate_hook*.py`, `validate_mcp.py`, …
- **Security engine:** `cpv_skillaudit_native.py` (the in-process skillaudit
  scanner + per-language context classifiers `_skillaudit_*_context.py`),
  `scripts/rules/skillaudit_patterns.json` (the rule catalog),
  `cpv_taint_engine.py`, `cpv_binary_scanner.py`.
- **Launcher:** `scripts/remote_validation.py` — the ONLY correct way to run a
  validator (running `validate_*.py` directly errors). See "Canonical
  commands".
- **Publisher:** `scripts/publish.py` — the 12-gate canonical pipeline (clean
  tree → tests → validate → bump → manifest → commit → tag → push → release).
  The ONLY thing allowed to push (a pre-push hook blocks all other pushes).
- **Self-hash manifest:** `scripts/_plugin_compute_hashes.py` regenerates
  `.plugin-self-hashes.json` / `.cpv-self-hashes.json` (the SHA list CPV's
  self-scan skips so it doesn't flag its OWN rule-pattern strings).

## Canonical commands (memorise these)

```bash
# Validate a plugin (NEVER run validate_plugin.py directly):
PLUGIN_SKIP_GITHUB_INTEGRITY=1 CLAUDE_PRIVATE_USERNAMES="$(whoami)" \
  uv run --with pyyaml python scripts/remote_validation.py plugin <path> --strict -o /tmp/out.txt
#   modes: plugin | security | skill | marketplace
#   CPV_SCAN_CACHE=0 → bypass the skillaudit result cache (MANDATORY when
#   testing a classifier change at the same version — the cache is keyed on
#   (content, catalog, __version__, ext), NOT classifier code).

# Self-validate CPV (after editing CPV's own files, regen the manifest FIRST):
uv run python scripts/_plugin_compute_hashes.py
PLUGIN_SKIP_GITHUB_INTEGRITY=1 CLAUDE_PRIVATE_USERNAMES="$(whoami)" \
  uv run --with pyyaml python scripts/remote_validation.py plugin . --strict -o /tmp/selfval.txt

# Test suite — run it SERIALLY before publishing (CI is serial + no-re2;
# xdist+re2 mask serial-pollution & no-re2 ReDoS):
uv run pytest -p no:cacheprovider -o addopts="" -q tests/

# Publish (bumps version, runs every gate, pushes, releases):
uv run python scripts/publish.py --patch   # | --minor | --major
```

## Load-bearing invariants / gotchas (the hard-won ones)

1. **Regen the manifest after editing CPV's own files**, before trusting a
   self-validate — else CPV flags its own changed files (their SHA no longer
   matches the skip list). New/changed files → `_plugin_compute_hashes.py`.
2. **`CPV_SCAN_CACHE=0` for same-version classifier testing** (see above).
3. **Serial suite before publish** — CI is serial + no-re2.
4. **Never suppress a security rule / never relax `--strict`** — the only
   auto-clear is provably-inert data (a pattern string in a rule-table never
   reaching a sink) or a non-instruction-loadable / comment / doc context;
   any possible execution path BLOCKS. Every FP fix is two-sided tested (the
   FP clears AND a real-threat sibling still fires).
5. **GitHub comments self-id:** begin every issue/PR/release comment with
   `This is the Claude responsible for the claude-plugins-validation project.`
6. **GitHub push-protection** rejects AWS-key-shaped test fixtures (`AKIA…`) —
   use a generic uppercase blob.
7. **re2 compatibility:** `skillaudit_patterns.json` regexes must be
   re2-safe (no lookbehind/lookahead) — CI runs without google-re2.
8. **Reports** go under `reports/` and `reports_dev/` (BOTH gitignored).

## Open issues snapshot (update as they close)

**v2.126.16 — two bounded `validate_plugin` workflow/cross-platform FPs**
(validation-LOGIC, not security): closed **#116** (RC-WORKFLOW-PATH-BROKEN
flagged a mid-job build artifact — a `run:` step executing a path that an
EARLIER step in the SAME job builds, e.g. `./dist/...-bin --help` after a
`stage.sh`; now suppressed when the path's leading segment is a build-output dir
[`dist`/`build`/`target`/`out`/`bin`/`.bin`/`output`/`release`/`artifacts`] OR an
earlier same-job step names it / runs a build command; SAME-JOB only, so a
cross-job broken ref still flags, and the dir match is leading-segment so a
source dir like `distributions/` is never caught) and **#117** (the "Rust source
… users will need to compile" advisory DEMOTED to INFO — kept visible — when an
`install*.sh` BOTH downloads a release asset AND verifies its checksum;
build-from-source or download-without-verify installers still WARN). +13 tests;
FULL SERIAL 9185 pass. Both two-sided-verified through the real validator.

**v2.126.15 — two bounded validation-LOGIC FPs** (not security suppressions):
closed **#120** (`validate_plugin` — the `.claude/` gitignore-coverage MINOR was
UNSATISFIABLE for a plugin tracking content under `.claude/`, since git can't
re-include a path under an excluded parent; now satisfied when `git ls-files --
.claude/` is non-empty, still flags an un-ignored un-tracked cache dir) and
**#119** (`cpv_validation_common` link-checker — a package-repo BASE URL on an
apt `deb`/dnf `baseurl=`/`--add-repo` source line legitimately 404s on a direct
GET; skip is scoped to the repo source LINE, not the host, so the same URL as a
real markdown link is still checked; genuine dead links still flagged). +18
tests; FULL SERIAL 9173 pass. Both two-sided-verified; claim-verification (per
the #91 lesson) caught a false-alarm `.claude`-MINOR "regression" that was
actually a wrong fixture (no `.gitignore` → the per-category check never ran).

**v2.126.12 — Theme-A skillaudit doc-fence cluster, batch 1** — closed FP
issues #77/#78/#80/#81/#88 with 7 markdown-classifier per-rule provably-inert
suppressions in
`_skillaudit_markdown_context.py` (delegated to an opus agent; I caught + closed
TWO FN holes in central verification the agent's targeted tests missed): #77
TIME_BOMB prose-with-no-code-construct; #78 INSECURE_TLS in md table/prose (keeps
firing in an executable fence); #80/#83.2 PROTOTYPE_POLLUTION non-JS-fence /
table; #81 SHELL_EXEC safe static-argv subprocess; #83.3 LOG_INJECTION `${env:}`
no-JNDI; #88 CMD_INJECTION bare-command-NAME list. **FN hole #1 (#81):** the
shell-interpreter-argv0 guard missed code interpreters — `["python","-c",…]` /
`["node","-e",…]` / `["perl","-E"]` / `["ruby","-e"]` / `["php","-r"]` (and
`["env","bash","-c",…]`) run arbitrary inline code and were being suppressed;
fixed to decline a SHELL interp anywhere OR a CODE interp + inline-eval-flag
(`["python","x.py"]` named-target still clears). **FN hole #2 (#88):** the
backtick-list helper suppressed ANY single backticked command incl. `cat
/etc/passwd` (the full serial suite caught it — 7 failing tests); narrowed to
≥2 bare-command-NAME spans only. Crux verified: `exit_code_strict()` makes NIT
block `--strict`, so doc findings that demote-to-NIT genuinely block downstream
CI. **v2.126.13** then closed **#79** PRIVILEGE_ESC `sudo rm` — a deliberately
narrow two-gate (yaml/GHA-step fence + a CLOSED literal-toolchain-path allowlist,
token-terminated so `…/dotnet/../etc` does not match) + a hard-disqualifier
(variable/glob/`..`/2nd-sudo/interpreter/system-path). The allowlist cannot widen
into a `sudo rm` bypass — independently adversarial-probed: an allowlisted
toolchain dir whose name contains a `lib` segment (the jvm/android caches) still
clears, while a config-dir system path (sudoers/shadow), a `$VAR` target, a
bare-root target, a `~` target, a toolchain path plus a `..` traversal, a
toolchain sub-path or a `dotnetEVIL` prefix-trick, the toolchain's parent dir, a
chained `curl|sh` or a second `sudo rm`, and the same line in a `.sh` file or a
bash fence all still fire. (Note to self: NEVER put a literal `/usr`-or-`/opt`
absolute path in a tracked doc — CPV's own absolute-path rule flags it MAJOR;
always re-self-validate AFTER editing CLAUDE.md.) **v2.126.14** then closed **#91**
REGEX_DOS — a classifier-level `_new_regexp_is_provably_linear` (TS/JS) suppresses
a `new RegExp(string-literals + non-user-idents)` ONLY when no literal part and
no ASSEMBLED skeleton has a catastrophic shape (group + unbounded quantifier with
a quantified body, or top-level alternation). Independently probed; one FN hole
caught + closed in central verification — a catastrophic shape split across two
ADJACENT literals (`"(a+)" + "+"` → `(a+)+`) was being suppressed because the
skeleton joined every literal pair with a placeholder; fixed to join adjacent
literals directly and place a placeholder only for an identifier gap. (Two of my
probe "holes" were FALSE — a no-`+` lone-var `new RegExp(userInput)` never matched
the catalog at baseline, a SEPARATE pre-existing detection gap, not a #91
regression.) **Still open:** the NEEDS-DESIGN trio (#83.5/#87/#95); regression
tests for the already-fixed (#86 + #83.1/.4/.6); and the #76/#83 umbrellas.
Investigation: `reports/fp-investigation/20260614_012725...-theme-a-doc-fence-cluster.md`.

**v2.126.10 closed 3** (FN-safe two-sided, each reproduced through the real
validator before + after the fix): **#112** — the RC-73 taint walker
(`iter_python_files`) is now gitignore-aware, so a gitignored `INPUT_DEV/`
scratch tree no longer yields publish-blocking MAJORs; a tracked shipped `.py`
source→sink still fires. **#110** — the advisory prose agent-name WARNING now
requires a kebab/digit identifier, so the English words
`explicit`/`specific`/`single` no longer flag; a hyphenated unknown agent and
the `subagent_type:` ghost-dispatch CRITICAL are untouched. **#105** — a new
`.html`/`.htm` skillaudit context classifier (`_skillaudit_html_context.py`)
reuses the reputable-CDN host allowlist, so a pinned jsdelivr ESM `import` in a
self-contained HTML artifact no longer fires SUPPLY_CHAIN; an unknown-host
import, an `eval(fetch())`, and an off-allowlist `curl … | sh` still fire.
**#113** — resolved in **v2.126.11** by a `cpv_lint_engine` MD004 dedup, NOT the
originally-proposed global dash-pin (which would have flagged every `*`-style
file CPV lints — its own + third-party — 304 bullet lines across 17 files,
incl. historical TRDDs + FP-corpus fixtures that must not be edited: a style
imposition). A stray `+ `/`* ` prose-wrap poisons markdownlint's `consistent`
mode and flags every healthy bullet; the finding relay now collapses repeated
same-(file, Expected/Actual) MD004 findings to ONE explanatory NIT. `consistent`
mode is kept (no style imposition); a genuine mixed-marker file still surfaces
once (visible NIT, never suppressed). Verified through real markdownlint: a
4-flag poisoned doc → 1 NIT.

**Earlier backlog (#76–#118)** — FP reports + suggestions filed by 9 ecosystem
plugin Claudes. **v2.126.9 closed 7** (FN-safe two-sided): `#85` (`.git`-FILE
private-path skip), `#96`/`#99` (bare-prose backtick-path scoping), `#98`
(.gitignore `git check-ignore` glob coverage), `#84` (markdownlint isolated-cwd
and crash→WARNING), `#97` (3rd-person role-def→INFO), `#100`-B/C (scanner
self-match). Remaining FP-fixable = the **Theme-A skillaudit doc-fence cluster**
(`#76` umbrella + `#77`/`#78`/`#79`/`#80`/`#81`/`#83`/`#86`/`#87`/`#88`/`#91`/`#95`),
plus newer `#115`/`#116`/`#117`/`#118`. Also open: **`#114`** (canonical-pipeline
cold-install `uvx-from-git` validate timeout — needs a wheel/cache, the real
ecosystem CI breaker). Security: the "bare secrets in YAML" alarm was a
janitor-scanner FP (ai-maestro-janitor #24), NOT a real exposure — a
17-repo/52-workflow audit found ZERO bare secrets.

**v2.126.8** (not a filed issue —
surfaced unblocking the claude-menu-system publish): FN-safe cspell-dictionary
carve-out — `_context_classifier_verdict` suppresses `_BINARY_INAPPLICABLE_RULES`
(incl. `TOOL_SHADOW`) on a recognised cspell word-list (`.cspell-words.txt` /
`project-words.txt` / `.cspell/`), gated on a non-instruction extension so no
SKILL/agent/command/hook can be disguised as one; exec/secret/exfil rules stay
live. The pytest words `monkeypatch*` were tripping `TOOL_SHADOW`'s bare-word
`monkey.?patch` pattern. TRDD `aed77004`; +24 two-sided tests.

Latest filed-issue cycle (v2.126.7) closed `#75`
— 5 security-scanner-plugin FP classes after self-exemption removal, all
FN-safe two-sided: (1) RC-70 inert-string AST carve-out (validate_security);
(2) RC-73 yaml `Loader=<SafeLoader-subclass>` taint carve-out (cpv_taint_engine);
(3) PEP-723 variable-`sys.path.insert` sibling resolution (validate_hook);
(4) ENV_INJECTION build-output/cache-var allowlist (_skillaudit_python_context);
(5) "no build script" ancestor-dir search (validate_plugin) — RC-NONSTD-DIR-001
on `tools/` kept BY-DESIGN (canonical is `rust/`). Reporter's `tests/`-skip +
fixture-annotation asks REJECTED as attacker-forgeable RT-holes.
Tracked follow-up (defense-in-depth, already mitigated by the #75 class-2
RC-73 fire): the #60 skillaudit DESERIALIZATION `_classdef_subclasses_safe_loader`
ignores `add_constructor` re-enablement.
Earlier: `#69`-`#74` closed (v2.126.2-.6).
