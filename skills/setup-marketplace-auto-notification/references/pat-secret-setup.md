# MARKETPLACE_PAT Secret Setup

## Table of Contents
- [Auto Detect From Environment](#auto-detect-from-environment)
- [Why a PAT](#why-a-pat)
- [Classic PAT Scopes](#classic-pat-scopes)
- [Fine Grained PAT](#fine-grained-pat)
- [Creating the PAT](#creating-the-pat)
- [Storing the Secret](#storing-the-secret)
- [Verifying the Secret](#verifying-the-secret)
- [Rotation](#rotation)
- [Renewal When Expired](#renewal-when-expired)

## Checklist

- [ ] Create a PAT with `repo` scope (classic) or fine-grained equivalent
- [ ] Store via `scripts/set_marketplace_pat.py` (never pipe to `gh secret set`)
- [ ] Verify secret is set with `gh secret list` (value never printed)
- [ ] Add rotation reminder to calendar before expiry
- [ ] Document the rotation procedure for renewal

This reference explains how to create and scope the Personal Access Token
that lets a plugin repo fire `repository_dispatch` at its marketplace repo.
Without this PAT, the notify workflow cannot cross repo boundaries.

---

## Auto Detect From Environment

**ALWAYS run this check BEFORE walking the user through manual PAT creation.**
If `MARKETPLACE_PAT` is already set in the shell, skip the whole creation
flow and just push the existing value to the target repo secrets.

### Step 1: Probe the shell for an existing token

```bash
# Read-only probe. NEVER echo the value itself — only the length.
if [ -n "${MARKETPLACE_PAT:-}" ]; then
  echo "MARKETPLACE_PAT found in environment (${#MARKETPLACE_PAT} chars)"
else
  echo "MARKETPLACE_PAT NOT set — will run manual creation walkthrough"
fi
```

`${#VAR}` expands to the length only; the token value never hits stdout,
logs, or shell history. Do NOT write helpers that `echo "$MARKETPLACE_PAT"`
for "debugging" — use `printf '%s\n' "${#MARKETPLACE_PAT}"` instead.

### Step 2: If the token IS set — push it to both repos non-interactively

**Preferred: use the dedicated helper script.** `scripts/set_marketplace_pat.py`
enforces the correct `gh secret set --body "$VALUE"` form, never prints the
token (so it cannot leak into Claude Code transcripts, shell history, or log
files), validates repo arguments, rejects malformed PATs (whitespace /
newlines from copy-paste), and runs verification automatically.

```bash
# One call, both repos, atomic — this is the canonical form
uv run python scripts/set_marketplace_pat.py \
  "<PLUGIN_OWNER>/<PLUGIN_REPO>" \
  "<MARKETPLACE_OWNER>/<MARKETPLACE_REPO>"

# Verification without exposing the value
uv run python scripts/set_marketplace_pat.py --verify-only \
  "<PLUGIN_OWNER>/<PLUGIN_REPO>" \
  "<MARKETPLACE_OWNER>/<MARKETPLACE_REPO>"
```

#### Manual fallback — `gh secret set --body`

If (and only if) the helper script is unavailable (e.g., setting a *different*
secret name, or working outside the CPV repo), the only correct manual form
uses the `--body` / `-b` flag to pass the value directly as an argument:

```bash
# Silence xtrace in case `set -x` is active so the value is not traced
set +x
gh secret set MARKETPLACE_PAT --repo "<PLUGIN_OWNER>/<PLUGIN_REPO>"      --body "$MARKETPLACE_PAT" >/dev/null
gh secret set MARKETPLACE_PAT --repo "<MARKETPLACE_OWNER>/<MARKETPLACE_REPO>" --body "$MARKETPLACE_PAT" >/dev/null
set -x 2>/dev/null || true

# Verify (names only, never the value)
gh secret list --repo "<PLUGIN_OWNER>/<PLUGIN_REPO>"      | grep -q '^MARKETPLACE_PAT' && echo "plugin:      present"
gh secret list --repo "<MARKETPLACE_OWNER>/<MARKETPLACE_REPO>" | grep -q '^MARKETPLACE_PAT' && echo "marketplace: present"
```

`-b` is the short form; `--body` is the long form — they are identical. Both
take the value as an argv parameter, which keeps the token out of stdin and
out of the shell's history expansion.

**Forbidden patterns** — these are WRONG and cause real outages. Reject them
on sight and do not emit them yourself:

- `echo "$MARKETPLACE_PAT" | gh secret set ...` — the pipe adds a trailing
  newline that gets stored inside the secret. The receiving repo then sends
  a malformed Authorization header at push time → `Bad credentials` / 401.
- `gh secret set MARKETPLACE_PAT <<< "$MARKETPLACE_PAT"` — here-string also
  appends a newline on most shells; same failure mode.
- `printf "$MARKETPLACE_PAT" | gh secret set ...` — same category; even if
  `printf` avoids the newline, stdin-driven `gh secret set` is still fragile.
- Any invocation that writes the token value to stdout, stderr, a log file,
  the git fix log, or the Claude Code conversation transcript.

If both verification lines print, the auto-detection path is complete.
Skip sections "Creating the PAT" and "Storing the Secret" below and jump
straight to the receiver and notify workflow setup.

### Step 3: If the token is NOT set — fall through to manual walkthrough

Continue to [Creating the PAT](#creating-the-pat) below. After the user
has generated a fresh token, guide them to export it persistently:

```bash
# Session-only export (one-shot)
read -rs -p "Paste PAT (input hidden): " MARKETPLACE_PAT
export MARKETPLACE_PAT

# Persist in shell profile (choose the right one)
# zsh:   echo 'export MARKETPLACE_PAT="ghp_..."' >> ~/.zshrc
# bash:  echo 'export MARKETPLACE_PAT="ghp_..."' >> ~/.bashrc
#
# direnv-style users: append to ~/.env (which must be gitignored):
#   export MARKETPLACE_PAT="ghp_..."
#
# NOTE: do NOT paste the token in plaintext in any tracked file.
# The examples above are for the user's private, gitignored shell profile.
```

After the export is in place, re-run Step 1 to confirm, then Step 2 to push
the secret. From the user's next shell session, the skill will auto-detect
the token and skip the walkthrough entirely.

### Step 4: Handle expired or wrong-scope tokens

If `gh secret set` fails with `HTTP 401`, `HTTP 403`, or `Bad credentials`,
the env-var token is either expired, revoked, or missing required scopes.
Offer to re-run the manual creation flow:

```bash
unset MARKETPLACE_PAT   # forget the bad value so Step 1 re-triggers creation
# then re-run the skill — it will now take the "NOT set" branch
```

Never try to salvage a failing token by retrying with the same value.
Tell the user plainly: "the stored PAT does not work, we need a new one."

---

## Why a PAT

The default `GITHUB_TOKEN` that GitHub Actions injects into each workflow
run is scoped to the **current** repository only. It cannot dispatch
`repository_dispatch` events to another repo, and it cannot push past
branch protection on a second repo. So every cross-repo notify chain
requires a Personal Access Token stored as a secret on the plugin repo
(and, for the receiver, also on the marketplace repo).

---

## Classic PAT Scopes

For a classic PAT (simpler, broader):

| Marketplace visibility | Scope needed | Notes |
|------------------------|--------------|-------|
| Public                 | `public_repo` | Allows dispatch and push to public repos only |
| Private or mixed       | `repo`        | Full control; required for any private marketplace |
| Org with SSO           | `repo` + "Configure SSO" | Must authorize the token for the org |

The `workflow` scope is NOT needed — the token only dispatches events; the
target workflow runs under the marketplace repo's own `GITHUB_TOKEN`.

---

## Fine Grained PAT

Fine grained PATs are the modern alternative. They are repo scoped and have
a mandatory expiry.

Scope the fine grained PAT to **the marketplace repository only** with these
permissions:

| Permission | Access | Why |
|------------|--------|-----|
| Contents   | Read and write | Receiver workflow commits marketplace.json |
| Actions    | Read and write | Receiver job needs to run; dispatch trigger |
| Metadata   | Read           | Required for every fine grained PAT |

A fine grained PAT scoped this narrowly is strictly safer than a classic
`repo` scope PAT and is the recommended option for production marketplaces.

---

## Creating the PAT

Classic PAT (full guide: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens):
1. Sign in to GitHub, then click your avatar → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic). (Direct URL requires login: `https://github.com/settings/tokens`)
2. Click "Generate new token" then "Generate new token (classic)".
3. Note: `Marketplace auto notify <plugin-name>`.
4. Expiration: 90 days (rotation is required; see below).
5. Select scope: `repo` for any marketplace, `public_repo` if public only.
6. Click Generate.
7. Copy the token immediately — it is shown only once.

Fine grained PAT (full guide: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens):
1. Sign in to GitHub, then click your avatar → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. (Direct URL requires login: `https://github.com/settings/personal-access-tokens/new`)
2. Resource owner: the user or org that owns the marketplace repo.
3. Repository access: "Only select repositories" then pick the marketplace.
4. Repository permissions: Contents=RW, Actions=RW, Metadata=R.
5. Expiration: 90 days.
6. Create, copy.

---

## Storing the Secret

Use `gh secret set` with the `--body` flag to avoid shell history leaks.
The value of `$PAT` must NOT be pasted inline — read it from a file or an
env var sourced from a `.env` file you own.

```bash
# DO NOT paste the token on the command line in any form.
# Put it in an env var first (one of these), then run gh:
export PAT="$(pbpaste)"                 # if you just copied the token
# OR: export PAT="$(cat ~/.secrets/marketplace-pat)"

# Plugin repo (required)
gh secret set MARKETPLACE_PAT \
  --repo <PLUGIN_OWNER>/<PLUGIN_REPO> \
  --body "$PAT"

# Marketplace repo (also required — the receiver uses it to push)
gh secret set MARKETPLACE_PAT \
  --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> \
  --body "$PAT"

unset PAT
```

You must always use `--body` and never `--body-file /dev/stdin` from a
pipe — the `gh` client buffers stdin into shell history in some versions.

For org-wide marketplaces, set the secret at the org level instead:

```bash
gh secret set MARKETPLACE_PAT \
  --org <MARKETPLACE_OWNER> \
  --visibility selected \
  --repos "<PLUGIN_REPO_1>,<PLUGIN_REPO_2>" \
  --body "$PAT"
```

---

## Verifying the Secret

You cannot read a secret back — that is by design. You can only verify it
EXISTS with the correct name:

```bash
# List all secrets on the plugin repo
gh secret list --repo <PLUGIN_OWNER>/<PLUGIN_REPO>
# Expected: MARKETPLACE_PAT    Updated YYYY-MM-DD

# And on the marketplace repo
gh secret list --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO>
```

To verify the secret is usable end to end, push a harmless commit (e.g. a
README typo) to the plugin repo and watch the Actions tab on the
marketplace repo for a `repository_dispatch` run. See
`end-to-end-verification.md` for the full verification script.

---

## Rotation

Recommended rotation schedule: **every 90 days**.

1. Create a new PAT (classic or fine grained) with the same scopes.
2. Update the secret on BOTH repos using `gh secret set` — see above.
3. Wait for at least one notify cycle to confirm the new token works.
4. Delete the old PAT from your GitHub Developer settings → Personal access tokens. (Direct URL requires login: `https://github.com/settings/tokens`. Public docs: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)

Classic PATs support setting the same name twice; fine grained PATs
require a unique note per token, so include a rotation counter
(`marketplace-pat-2026q2`, `marketplace-pat-2026q3`, ...).

---

## Renewal When Expired

Symptoms: notify workflow job log shows
`Bad credentials` or `HTTP 401`, or the marketplace receiver never fires.

Recovery procedure:

```bash
# 1. Generate a fresh PAT with the same scopes (see "Creating the PAT").
export PAT="$(pbpaste)"

# 2. Rewrite the secret on BOTH repos.
gh secret set MARKETPLACE_PAT --repo <PLUGIN_OWNER>/<PLUGIN_REPO>      --body "$PAT"
gh secret set MARKETPLACE_PAT --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> --body "$PAT"
unset PAT

# 3. Re-run the failed job from the plugin repo's Actions tab.
gh run rerun --failed --repo <PLUGIN_OWNER>/<PLUGIN_REPO>

# 4. Verify by watching the marketplace repo Actions tab for the dispatch.
gh run list --repo <MARKETPLACE_OWNER>/<MARKETPLACE_REPO> --workflow "Update plugin version" --limit 5
```

If the failed run cannot be re-run (too old), make any one-character commit
on the default branch of the plugin repo — that is enough to re-trigger the
notify workflow.
