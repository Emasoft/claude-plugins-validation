# Orphan Plugin Onboarding

## Table of Contents

- [Scenario](#scenario)
- [The marketplace requirement — explain it first](#the-marketplace-requirement--explain-it-first)
- [Detect the scenario](#detect-the-scenario)
- [Ask the user which path fits](#ask-the-user-which-path-fits)
- [Path A: plugin came from an existing marketplace](#path-a-plugin-came-from-an-existing-marketplace)
- [Path B: host in a NEW local marketplace](#path-b-host-in-a-new-local-marketplace)
- [Path C: host in a NEW GitHub marketplace (user's own)](#path-c-host-in-a-new-github-marketplace-users-own)
- [Path D: host in an EXISTING GitHub marketplace the user owns](#path-d-host-in-an-existing-github-marketplace-the-user-owns)
- [Full pipeline is mandatory](#full-pipeline-is-mandatory)
- [Final user instructions](#final-user-instructions)

## Scenario

The user has downloaded a plugin from the internet — a standalone folder with `.claude-plugin/plugin.json` but no marketplace around it. They want to install it with `claude plugin install`. They do NOT know that Claude Code plugins can't be installed directly without a marketplace. The agent must educate, decide, build, and hand back install commands.

## The marketplace requirement — explain it first

Before doing anything, say this (adapt the wording, keep the content):

> **Heads-up: Claude Code plugins aren't installed directly.** Every plugin has to be registered in a **marketplace** — a small index file that Claude reads to know where the plugin lives and what version to use. Without a marketplace, `claude plugin install <name>` has nothing to install.
>
> Marketplaces come in two flavors: **local** (a folder on your disk, good for dev and private use) and **online** (a GitHub repo, good for sharing and versioning). The plugin you downloaded needs to sit inside one before you can install it.
>
> Three questions will decide what I do next:
> 1. Did you get this plugin from a marketplace (GitHub repo with a `marketplace.json`)? If yes, you can just add that marketplace and install — no rebuild needed.
> 2. If not: do you want me to set up a local marketplace (fastest, private) or a GitHub marketplace (shareable, CI-backed)?
> 3. Do you already have a marketplace I created for you in a previous session? If so, I can register this plugin there instead of making a new one.

## Detect the scenario

Markers, in order:

1. The path the user gave resolves to a plugin root (after the Path Resolution Protocol runs). This is the plugin.
2. No ancestor folder within 3 levels has `marketplace.json` or `.claude-plugin/marketplace.json`. The plugin is an **orphan**.
3. `claude plugin marketplace list` (ask the user to run it if they can) does NOT show any marketplace that already contains this plugin by `name` + `source`.
4. The plugin folder has no GitHub remote (`git -C <plugin> remote -v` is empty), OR has a remote but that repo is not referenced by any known marketplace.

When all four hold, this is the orphan-plugin scenario. Run the explanation step above, then the chooser.

## Ask the user which path fits

Use `AskUserQuestion`. Never decide for them. Present the four paths below with one-line pros/cons:

- **A. "I got it from a marketplace I already know about"** — you just need to `marketplace add` + `install`. No rebuild. (If the user doesn't know what marketplace, help them find it by checking the plugin's README, repository URL, or by inspecting its `.git/config` for an origin.)
- **B. "Host it in a local marketplace (quick, private)"** — creates a folder on the user's disk with `marketplace.json`, points it at the plugin directory. Good for one-off use or active development. Not shareable.
- **C. "Host it in a new GitHub marketplace (shareable)"** — creates a GitHub repo for the marketplace, pushes the plugin to its own repo, wires CI/CD and auto-notify. Good for teams and public distribution.
- **D. "Use an existing marketplace I own"** — list marketplaces the agent created in prior sessions (search `~/.claude/plugins/known_marketplaces.json` for `source.repo` entries where `<owner>` matches the user's GitHub account) OR ask the user to name one. Then publish the plugin to that marketplace (treat as Path C against a pre-existing marketplace).

Remember: the user may not know which applies. If they say "I don't know", walk through the questions:
- "Is the plugin on GitHub? If yes, does the repo's description or README mention a marketplace?"
- "Do you want others to be able to install this too, or is it just for you?"
- "Are you willing to create a GitHub account/repo, or do you prefer everything local?"

## Path A: plugin came from an existing marketplace

**Work**: zero rebuild. The plugin is already registered somewhere; just tell the user how to point Claude at it.

**Steps**:

1. Help the user identify the marketplace. Ask where they downloaded the plugin from. Common signals:
   - Plugin README mentions `<owner>/<marketplace>` or a `@marketplace-name` install command.
   - Plugin's git remote URL is `github.com/<owner>/<plugin>.git` — often the same `<owner>` hosts a `<owner>/<something>-plugins` marketplace. Check with `gh repo list <owner> --json name --search plugins`.
   - User's browser history or download source ("I got it from the CPV marketplace", "Emasoft's repo").
2. Verify the marketplace exists and lists this plugin: `gh repo view <owner>/<marketplace> --json name` + fetch `marketplace.json` and grep for the plugin's `name`.
3. If it's there with the matching version, emit the instructions:

   ```
   # 1. Add the marketplace
   claude plugin marketplace add <owner>/<marketplace>

   # 2. Refresh
   claude plugin marketplace update <marketplace>

   # 3. Install (pick scope)
   claude plugin install <plugin-name>@<marketplace> --scope user
   ```

4. If the marketplace exists but doesn't list THIS version of the plugin (the user's download is newer / older / a fork), offer Path C or D — "your local copy is out of sync with that marketplace; you can either install the marketplace's version or publish yours to a marketplace you control".

## Path B: host in a NEW local marketplace

**Work**: create a small marketplace folder on disk that points at the plugin. No GitHub needed.

**Steps**:

1. Ask the user where to put the marketplace folder. Default to the plugin's parent. Name it `<parent>/my-local-marketplace/` or whatever the user prefers.
2. Create the layout:
   ```
   my-local-marketplace/
   └── .claude-plugin/
       └── marketplace.json
   ```
   `marketplace.json` body (Layout A relative source — the plugin stays where it is; the marketplace entry points at its absolute path relative to the marketplace):
   ```json
   {
     "name": "my-local-marketplace",
     "description": "Local plugin hub",
     "owner": { "name": "<user>", "email": "<user-email>" },
     "plugins": [
       {
         "name": "<plugin-name>",
         "source": "<relative-path-from-marketplace-to-plugin>",
         "version": "<plugin-version>",
         "description": "<plugin-description>",
         "category": "<ask-user-to-pick>",
         "author": { "name": "<plugin-author>" },
         "license": "<plugin-license>"
       }
     ]
   }
   ```
3. Validate: `uv run --with pyyaml python scripts/validate_marketplace.py <marketplace-folder> --strict`. Route above-WARNING findings to the `marketplace-fixer` agent.
4. Validate the plugin itself is still clean: `validate_plugin.py --strict`. Route to `plugin-fixer` if needed.
5. Emit the final instructions (local marketplace add uses the directory path, not a GitHub slug):

   ```
   # 1. Register the local marketplace with Claude Code
   claude plugin marketplace add <absolute-path-to-my-local-marketplace>

   # 2. Refresh (Claude re-reads marketplace.json from disk)
   claude plugin marketplace update my-local-marketplace

   # 3. Install (pick scope)
   claude plugin install <plugin-name>@my-local-marketplace --scope user
   ```

6. Tell the user this marketplace is on their disk only — to share the plugin they'd need Path C later.

**Note on the publish pipeline**: local marketplaces do NOT need `publish.py`, CI, or auto-notify. Those are for GitHub distribution. But DO add a minimal `.gitignore` (if the user wants to git-track the marketplace folder) and a README so they can find it again.

## Path C: host in a NEW GitHub marketplace (user's own)

**Work**: full deploy. Create two GitHub repos (plugin + marketplace, Layout A) or one Layout B monorepo if the user prefers. Install `publish.py`, CI, auto-notify. This is the longest path but produces a shareable, versioned result.

**Steps**:

1. Pick a layout with `AskUserQuestion` — Layout A (separate plugin + marketplace repos, default) or Layout B (marketplace repo contains the plugin as a subfolder).
2. **Plugin repo** (Layout A): run the full "Publish Plugin as GitHub Repo" workflow from `agents/plugin-creator.md` step-by-step. This gives the plugin its own GitHub repo with `scripts/publish.py`, CI, pre-push hooks, first tag + GitHub release.
3. **Marketplace repo**: invoke the `setup-github-marketplace` skill's setup guide. Create `<owner>/<marketplace>`, install `marketplace.json` + CI/CD templates + `update-submodules.yml` dispatch receiver.
4. **Link the plugin**: invoke the `publish-to-marketplace` skill to wire `notify-marketplace.yml` on the plugin repo + set `MARKETPLACE_PAT` via `scripts/set_marketplace_pat.py` (never pipe to `gh secret set`).
5. **First sync**: push the plugin → notify workflow fires → marketplace receives dispatch → `marketplace.json` updated → commit lands on marketplace default branch.
6. **Verify end-to-end**: poll marketplace Actions tab; fetch `marketplace.json` via `gh api` and confirm the plugin entry with the correct version.
7. **Branch rules**: apply `cpv-setup-branch-rules` to both repos.
8. Emit final install instructions (GitHub marketplace slug form):

   ```
   # 1. Add the marketplace (first time only)
   claude plugin marketplace add <owner>/<marketplace>

   # 2. Refresh
   claude plugin marketplace update <marketplace>

   # 3. Install (pick scope)
   claude plugin install <plugin-name>@<marketplace> --scope user
   ```

Every step of this path is covered by existing skills: `setup-plugin-repo`, `canonical-pipeline`, `setup-github-marketplace`, `publish-to-marketplace`, `setup-marketplace-auto-notification`. Load them on demand — don't improvise.

## Path D: host in an EXISTING GitHub marketplace the user owns

**Work**: same as Path C minus the marketplace creation. Plugin gets its own GitHub repo, then is linked into the existing marketplace.

**Steps**:

1. Identify the target marketplace. Sources to check:
   - `~/.claude/plugins/known_marketplaces.json` (what the user has registered with Claude Code) — filter by `source.repo` matching `<user-owner>/*`.
   - `gh repo list <user-owner> --json name --search plugins` — likely matches include `*-plugins`, `*-marketplace`.
   - Ask the user explicitly if they can name it.
2. Validate the target: `cpv-remote-validate marketplace <owner>/<marketplace> --strict`. If findings above WARNING, route to `marketplace-fixer` before continuing — don't register into a broken marketplace.
3. Full Path C from step 2 onward (plugin repo + linking + first sync + verify + branch rules).
4. Emit the same user instructions as Path C.

## Full pipeline is mandatory

Paths C and D must leave both repos with **full** CPV standards: `scripts/publish.py`, consolidated `ci.yml` (lint + validate + test jobs), `cliff.toml`, `CHANGELOG.md`, pre-push hook, `notify-marketplace.yml` on the plugin, `update-submodules.yml` on the marketplace, `MARKETPLACE_PAT` secret wired, `cpv-setup-branch-rules` applied. Skipping any of these leaves the chain fragile — the next update will fail silently or ship broken.

Path B (local) needs only `marketplace.json` + a README. The heavy pipeline is wasted work locally.

Path A does no rebuild at all.

## Final user instructions

Regardless of path, end with the commands the user must run themselves. Never run `claude plugin install` for them — scope (user/project/local) is their choice. Emit the block verbatim with placeholders substituted, including a "pick one scope" reminder and a `claude plugin list` verification step.

Suggested closing sentence: "That's everything I can do automatically. The install commands above are the only things you need to run — report back if any of them error out and I'll investigate."
