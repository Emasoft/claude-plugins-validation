# TRDD-793ac32a — Strip dev-only parts from Claude Code plugin installs

**TRDD ID:** `793ac32a-6ccf-4628-aa6c-952218e06477`
**Filename:** `design/tasks/TRDD-793ac32a-6ccf-4628-aa6c-952218e06477-strip-dev-parts.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Not started — design only, defer implementation
**Author:** Emasoft
**Created:** 2026-05-02

---

## 1. Problem statement

Claude Code installs plugins by shallow-cloning the upstream git repo (or
unpacking a release archive) into
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. **Everything
that is committed to the plugin repo gets copied verbatim into the user's
machine** — including parts that are only useful to the plugin's
*developers* and contribute nothing at runtime.

For CPV today (v2.50.x):

| Folder | Size in install | Purpose |
|---|---|---|
| `tests/` | **12 MB** (80 files, 48,263 lines) | Test suite — never executed at runtime by Claude Code |
| `scripts/` | 6.8 MB | Validators (NEEDED at runtime via remote_validation.py) |
| `docs_dev/` | already-gitignored | Already excluded |
| `reports_dev/` | already-gitignored | Already excluded |
| `reports/` | currently 2.2 MB | Per-run reports — should be gitignored, not committed |
| `design/` | 208 KB | TRDDs — useful for traceability but never executed |
| `skills/` | 1.7 MB | NEEDED at runtime |
| `agents/` | 160 KB | NEEDED at runtime |
| `commands/` | 172 KB | NEEDED at runtime |

**Conservative target:** removing `tests/` alone shrinks every CPV install by
12 MB (~50% of the runtime payload). With cleanup of stray `reports/` and
the small `design/` folder, the install drops from ~25 MB to ~10 MB.

PSS already solves a parallel problem the *opposite* way: PSS's `bin/`
folder is 144 MB of cross-platform binaries that the plugin **cannot
function without**, but `rust/` (the source for those binaries) is moved
out via a git submodule pointing at `pss-rust-engine`. Claude Code's
shallow-clone install does **not** recurse into submodules, so the
multi-gigabyte Rust dependency tree never reaches the user — only the
.gitmodules pointer file does.

This TRDD adopts the same submodule mechanic for **dev-only artefacts** in
every CPV-style plugin and adds a one-shot CPV command that performs the
extraction.

---

## 2. The PSS submodule pattern (verified empirically 2026-05-02)

```
Source repo (Code/PERFECT_SKILL_SUGGESTER/perfect-skill-suggester):
  rust/                  ← submodule (Cargo workspace, gigabytes when built)
  .gitmodules            ← single file: rust → github.com/Emasoft/pss-rust-engine
  bin/                   ← prebuilt binaries committed to MAIN repo
  ...

Cache install (.claude/plugins/cache/.../perfect-skill-suggester/3.2.9):
  rust/                  ← only 1.2 MB — the .git is a 29-byte submodule pointer file
                           Cargo.lock, Cargo.toml, README.md, LICENSE, and a top-level
                           workspace shell ship; the actual src/ trees of skill-suggester
                           and negation-detector ship because the submodule itself
                           wasn't recursed
  bin/                   ← FULL 144 MB (cross-platform Rust binaries — REQUIRED)
  .gitmodules            ← 86 bytes
```

Two confirmed properties:
- `git clone --depth 1 https://github.com/...plugin-repo.git` does NOT clone
  submodules unless `--recurse-submodules` is added. Claude Code's
  installer does NOT pass `--recurse-submodules`.
- The .gitmodules pointer is harmless — at runtime nothing in the plugin
  reads `rust/` (the binaries in `bin/` are what get executed).

**Implication for CPV:** if `tests/` becomes a submodule pointing at
`Emasoft/cpv-tests`, the test files do NOT ship with the install. Devs
who clone the source repo can still `git submodule update --init` to get
them.

---

## 3. Decision: which parts go where

### 3.1 Three-bucket classification

| Bucket | Examples (CPV) | Mechanism |
|---|---|---|
| **Runtime** — needed by Claude Code at execution | `scripts/`, `skills/`, `agents/`, `commands/`, `templates/`, `hooks/`, `.claude-plugin/`, `README.md`, `LICENSE`, `pyproject.toml`, `uv.lock` | Stay in MAIN repo, ship in install |
| **Dev-only** — only useful to plugin developers | `tests/`, `design/tasks/`, `git-hooks/`, `cliff.toml`, `mypy.ini`, `pyrightconfig.json`, `tests/fixtures/`, `examples_dev/`, `samples_dev/` | Move to `dev/` SUBMODULE → `Emasoft/<plugin>-dev` |
| **Already excluded** — `.gitignore`d, never committed | `docs_dev/`, `scripts_dev/`, `reports_dev/`, `reports/`, `.venv/`, caches | No change — already absent from install |

### 3.2 Boundary rules (which side a folder lands on)

| Question | If yes → ship in install | If yes → dev submodule |
|---|---|---|
| Is it executed at install time or by a slash-command at runtime? | ✓ | |
| Is it referenced by `.claude-plugin/plugin.json` (commands/agents/skills/hooks/mcpServers paths)? | ✓ | |
| Does any `*.md` shipped in the runtime bucket link/reference it? | ✓ | |
| Is it a config for **CI / dev tooling** only (mypy, ruff, pytest, git-cliff, githooks)? | | ✓ |
| Is it a TRDD, design doc, or scenario test? | | ✓ |
| Is it a fixture only consumed by `tests/`? | | ✓ |

### 3.3 Two preserved-in-install exceptions worth listing

- `pyproject.toml` and `uv.lock` — the user's `uv run` invocations need
  these to resolve dependencies. They stay.
- `cliff.toml`, `pyrightconfig.json`, `mypy.ini` — even though they're
  CI-only, removing them is risky if any user runs the plugin's own dev
  commands locally. Decision: **move to dev submodule** but symlink them
  back during a `cpv strip --keep-dev-configs` mode for users who want
  them.

---

## 4. The `cpv strip-dev-parts` command (deferred — design only)

### 4.1 Modes

```bash
cpv strip-dev-parts <plugin-path>                    # interactive (default)
cpv strip-dev-parts <plugin-path> --auto             # apply standard rules, no prompts
cpv strip-dev-parts <plugin-path> --dry-run          # preview, no changes
cpv strip-dev-parts <plugin-path> --restore          # pull tests/ submodule back into working tree
cpv strip-dev-parts <plugin-path> --check            # exit 1 if dev-parts still in MAIN repo
```

### 4.2 Standard extraction targets (built-in defaults)

| Target | Submodule URL convention | Submodule path |
|---|---|---|
| `tests/` | `https://github.com/<owner>/<plugin>-tests.git` | `dev/tests/` |
| `design/` | `https://github.com/<owner>/<plugin>-design.git` | `dev/design/` |
| `git-hooks/` | (same as `dev/tests/` repo, under `git-hooks/`) | `dev/git-hooks/` |
| `tests/fixtures/` (large fixtures > 1 MB) | `https://github.com/<owner>/<plugin>-fixtures.git` | `dev/fixtures/` |

The user can override via `cpv.strip` block in `plugin.json` (see §5).

### 4.3 Mechanical steps (per target)

```
1. Verify the target folder exists in the MAIN repo and has no
   uncommitted changes.
2. Create the dev repo on GitHub (gh repo create --private --confirm
   --description "Dev-only artefacts for <plugin>").
3. Clone the dev repo locally to a tmpdir.
4. git mv <target>/ <tmpdir>/<target>/ via filter-repo (preserves history)
   — fall back to plain copy + git rm if filter-repo unavailable.
5. cd <tmpdir>; git add .; git commit -m "Initial: extracted from <plugin>";
   git push origin main.
6. cd <plugin>; git submodule add <dev-repo-url> dev/<target>/
7. Symlink the submodule path back to its original location for backwards
   compat (`ln -s dev/<target> <target>` — only when the user opts in via
   --keep-symlinks).
8. Update .gitignore: add `<target>/` if symlink mode is OFF.
9. Update CI workflows to clone with --recurse-submodules.
10. Commit the .gitmodules + symlinks atomically with message:
    "chore: extract <target>/ to <plugin>-<target> submodule
    (cpv strip-dev-parts)".
```

### 4.4 Rollback semantics

`cpv strip-dev-parts --restore` runs:
```bash
git submodule update --init --recursive dev/<target>/
# Then dev/<target>/ contains the actual source tree, accessible like any folder.
```

Devs who clone the plugin repo can also just run:
```bash
git clone --recurse-submodules https://github.com/owner/plugin.git
```

### 4.5 Plugin-creator integration

The `plugin-creator` agent gains a top-level **dev-stripping default**.
When a user creates a new plugin via the menu:

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Dev-stripping (default = ON)                                     ┃
┣━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┫
┃ N │ Choice                                                       ┃
┣━━━━┼─────────────────────────────────────────────────────────────┫
┃ 1 │ Standard (extract tests/ + design/ to dev submodule)         ┃
┃ 2 │ Aggressive (also extract git-hooks/ and dev configs)         ┃
┃ 3 │ Keep everything in MAIN repo (legacy mode — discouraged)     ┃
┃ 0 │ Cancel                                                       ┃
┗━━━━┴─────────────────────────────────────────────────────────────┘
```

Choosing 1 or 2 makes the generator scaffold the dev repo and submodule
from the start — no post-hoc extraction needed.

### 4.6 Existing-plugin migration command

For a plugin that's already published with everything in the MAIN repo,
run `cpv strip-dev-parts <path>` interactively. The command:

1. Snapshots current state (commits any pending changes, refuses if
   working tree dirty).
2. Runs the per-target steps in §4.3 with progress prints.
3. Bumps version one minor level (12 MB → 0.5 MB install is a user-visible
   change worth a minor bump).
4. Updates CHANGELOG.md with a "BREAKING for devs: tests/ moved to dev
   submodule. To work on the plugin, clone with --recurse-submodules."
   note.
5. Triggers the standard publish.py at the end.

---

## 5. `cpv.strip` config block in `plugin.json`

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "cpv": {
    "strip": {
      "extract": [
        { "src": "tests/", "submodule": "owner/my-plugin-tests" },
        { "src": "design/", "submodule": "owner/my-plugin-design" }
      ],
      "keep_in_main": [
        "tests/fixtures/small-snippets/"
      ],
      "keep_dev_configs": false,
      "symlinks_for_devs": true
    }
  }
}
```

| Key | Default | Effect |
|---|---|---|
| `extract[].src` | `tests/` and `design/` | folders to move out |
| `extract[].submodule` | `<owner>/<plugin>-<src>` | dev repo to create |
| `keep_in_main` | `[]` | per-folder allowlist (e.g. small fixtures) |
| `keep_dev_configs` | `false` | if true, leaves `mypy.ini`, `cliff.toml`, etc. |
| `symlinks_for_devs` | `true` | symlinks back into original paths after extraction |

`cpv strip-dev-parts` reads this block as authoritative; absence falls
back to defaults in §4.2.

---

## 6. CPV self-application

After the strip command lands, **CPV applies it to itself first** as a
test bed:

```
1. Create Emasoft/cpv-tests
2. cpv strip-dev-parts /Code/CLAUDE-PLUGIN-VALIDATION/claude-plugins-validation/ --auto
3. Verify install size dropped from ~25 MB to ~10 MB
4. Verify all CPV scripts still work (tests still runnable from dev/tests/
   for development)
5. Bump CPV to v2.51.0 with the breaking-for-devs note
```

Once this is proven on CPV, the same standard is documented in the
plugin-creator and recommended for every new plugin.

---

## 7. Side question: are 3932 tests actually doing useful work?

The user raised the suspicion that most of CPV's tests are mock-heavy
unit tests that could collapse to a much smaller integration suite.
Decision: **out of scope for THIS TRDD** but tracked here as a follow-up.

Quick triage ideas for a future audit:
- `grep -l "mock\\|Mock\\|patch" tests/*.py | wc -l` — count mock-using files
- `grep -l "subprocess\\|Path(\".*\\.py\")\\|tmp_path" tests/*.py | wc -l` — count integration-shaped files
- Run pytest with `--collect-only -q` to see test names; cluster by validator they target.
- Map tests to validators 1-to-N. If 90% target the same validator with
  trivial regex variants, fold them into parametrized tests.

That audit is its own TRDD when the time comes.

---

## 8. Files this TRDD WILL touch when implemented (estimate)

| File | Change |
|---|---|
| `scripts/cpv_strip_dev.py` | NEW — implements the command |
| `commands/cpv-strip-dev-parts.md` | NEW — slash-command surface |
| `agents/plugin-creator.md` | Add §4.5 dev-stripping prompt |
| `skills/create-plugin/SKILL.md` | Same dev-stripping prompt |
| `scripts/generate_plugin_repo.py` | Add `--strip-dev` flag |
| `scripts/validate_plugin.py` | New rule: warn if `tests/` size > 5 MB and no `cpv.strip` config |
| `tests/test_cpv_strip_dev.py` | NEW |
| `README.md` | Document the strip-dev workflow |
| `CHANGELOG.md` | Major user-visible change for v2.51.0 |
| Self-extraction: this very plugin's `tests/`, `design/` | Moved to `Emasoft/cpv-tests` and `Emasoft/cpv-design` submodules |

---

## 9. Implementation phases (when this TRDD is approved)

1. **Phase 1 (v2.51.0-rc1)** — `cpv_strip_dev.py` + `cpv-strip-dev-parts`
   command + `cpv.strip` config block parser. No CPV self-application
   yet. Tests for the script.
2. **Phase 2 (v2.51.0-rc2)** — plugin-creator + generate_plugin_repo.py
   integration. Default-ON for new plugins.
3. **Phase 3 (v2.51.0-rc3)** — validate_plugin.py rule that warns when a
   plugin ships > 5 MB of `tests/` without a `cpv.strip` block.
4. **Phase 4 (v2.51.0)** — Apply to CPV itself: extract tests/ and
   design/ to submodules, bump to v2.51.0, publish.

Each phase is independently revertable. No phase blocks runtime
functionality of any existing plugin.

---

## 10. Cross-references

- TRDD-9065109a — zero-config publish pipeline (covers submodule-bundle
  detection in publish.py; this TRDD is the user-facing extraction
  command that complements it)
- TRDD-b5e44619 — universal publish-and-auth contract (auth standard;
  related but orthogonal)
- TRDD-91a98c92 — publish.py authentication standard (sibling TRDD;
  see filename `TRDD-bbff5bc5-...-publish-auth-standard.md`)
- PSS source: `/Users/emanuelesabetta/Code/PERFECT_SKILL_SUGGESTER/perfect-skill-suggester/`
- PSS .gitmodules pattern: `[submodule "rust"] path = rust  url = https://github.com/Emasoft/pss-rust-engine.git`
