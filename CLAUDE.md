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
| **version** | `2.126.8` | `.claude-plugin/plugin.json` → `version` |
| **commands** | **13** | `ls commands/*.md` — 10×`cpv-batch-*`, `cpv-main-menu`, `cpv-pre-install-scan`, `the-skills-menu-create` |
| **agents** | **15** | `ls agents/*.md` |
| **skills** | **46** | `ls -d skills/*/` |
| **scripts** | **113** | `ls scripts/*.py` (25 `validate_*.py` + management/engine/CLI) |
| **test files** | **308** | `ls tests/test_*.py`; ~8996 tests |

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

ALL filed issues CLOSED through `#75`. **v2.126.8** (not a filed issue —
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
