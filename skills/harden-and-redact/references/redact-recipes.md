# Redact Recipes — Part B (full per-file-kind recipes)

## Table of Contents

- [B1 env-read](#b1--literal-api-key--token-in-source--runtime-read-from-env)
- [B2 GitHub vars](#b2--secret-needed-in-a-github-action--repo-secret--variable)
- [B3 .env hygiene](#b3--secret-in-a-committed-env--config-file--remove--gitignore--example)
- [B4 OS keychain](#b4--secret-stored-in-the-os-keychain--runtime-keychain-read)
- [B5 private-path](#b5--leaked-private-path--username--relativize)
- [B6 rotate + purge](#b6--verified-live-committed-secret--rotate--purge-history-reverse-case)
- [Per-file-kind table](#per-file-kind-redaction-table)
- [Cross-cutting rules](#cross-cutting-rules-restated)

Each entry gives **BEFORE** (the leaked shape, described), **AFTER** (the
redacted / runtime-read rewrite), **WHY-SAFE** (no secret-shaped token
remains), **VERIFY** (re-scan outcome), and **NO-FUNCTIONALITY-LOSS** (the
runtime read fails fast, the behavior is preserved).

> Self-documentation note: every BEFORE block below is written in its
> **already-inert form** — secrets are placeholders (`<YOUR_API_TOKEN>`,
> `sk-XXXX…REDACTED`, `${API_TOKEN}`), hosts are fake
> (`api.example.invalid`), and the leaked literal is *described* in prose
> or a `#` comment, never reproduced as a real credential. The AFTER
> blocks are the real target shapes. This keeps the catalog itself
> provably free of secrets.

The B contract restated: **redact the literal; if the value is genuinely
needed at runtime, read it at runtime from the environment.** A leaked
value that is also live in git history needs **rotate + purge** (B6) —
redaction alone is insufficient.

---

## B1 — Literal API key / token in source → runtime-read from env

Fires HARDCODED_SECRET / a `SECRET_*` rule / a `CREDENTIAL_REFERENCE` /
an external secret scanner when a credential literal is committed.

**BEFORE** (described): a source line that assigns a real-looking
credential value directly to a variable — e.g. an assignment of a token
literal to `api_key`, or a client constructed with an inline key. The
literal IS the leak.

**AFTER** (Python — read at runtime, fail-fast when unset):

```python
import os

api_key = os.environ.get("API_TOKEN")
if not api_key:
    raise RuntimeError("API_TOKEN is not set")   # fail-fast, never silent
```

**AFTER** (JavaScript / TypeScript):

```javascript
const apiKey = process.env.API_TOKEN;
if (!apiKey) {
  throw new Error("API_TOKEN is not set");       // fail-fast
}
```

**AFTER** (shell):

```bash
: "${API_TOKEN:?API_TOKEN is not set}"           # fail-fast if unset/empty
```

**WHY-SAFE:** no credential-shaped token remains in the source. The
detector keys on entropy + known prefix signatures; an `os.environ` /
`process.env` / `${VAR}` reference carries no secret value, so
HARDCODED_SECRET / the `SECRET_*` rules / the external scanner do not
fire.

**VERIFY:** re-scan → the HARDCODED_SECRET / `SECRET_*` / external-scanner
hit on that line is gone (no longer present, not demoted, not
suppressed-by-config).

**NO-FUNCTIONALITY-LOSS:** the value is still available at runtime; it is
read from the environment instead of a literal. The fail-fast guard means
a missing var raises immediately rather than silently passing an empty
credential — strictly safer than the original, with identical behavior
when the var is set.

---

## B2 — Secret needed in a GitHub Action → repo secret / variable

Fires a `SECRET_*` rule / HARDCODED_SECRET when a workflow inlines a
credential value.

**BEFORE** (described): a `.github/workflows/*.yml` step that sets an
environment value to an inline credential literal, or passes a literal
token to a `with:` input. The literal in the workflow file is the leak.

**AFTER** (reference a repo secret for a true secret, a repo+Actions
**variable** for a non-secret config value — never inline):

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    env:
      NPM_TOKEN: ${{ secrets.NPM_TOKEN }}     # secret -> Settings > Secrets
      AWS_REGION: ${{ vars.AWS_REGION }}      # non-secret config -> Variables
```

Set the secret once in the repo settings (Secrets), the variable in
(Variables); the workflow references them by name.

**WHY-SAFE:** `${{ secrets.NAME }}` / `${{ vars.NAME }}` are references
resolved by the Actions runner at run time; the workflow file carries no
credential value, so no secret-detector signature matches.

**VERIFY:** re-scan → the `SECRET_*` / HARDCODED_SECRET hit in the
workflow file is gone.

**NO-FUNCTIONALITY-LOSS:** the job receives the same value at run time
from the repo secret / variable store. Secrets are masked in logs; a true
secret belongs in Secrets (encrypted), a non-secret in Variables.

---

## B3 — Secret in a committed `.env` / config file → remove + gitignore + example

Fires a `SECRET_*` rule / HARDCODED_SECRET when a credential is committed
inside a `.env` / config file.

**BEFORE** (described): a committed `.env` (or a `config.json` / settings
file) whose lines assign real credential values. The committed file is
the leak — and it is in git history the moment it ships.

**AFTER** (three steps):

1. Remove the real values from the committed file (read them at runtime
   per B1, or keep them only in an uncommitted local `.env`).
2. Add the file to `.gitignore`:

   ```gitignore
   .env
   .env.*
   !.env.example
   ```

3. Document the required variables in a committed `.env.example` with
   placeholders only:

   ```bash
   # .env.example — copy to .env and fill in real values locally
   API_TOKEN=<your-token-here>
   DATABASE_URL=<your-database-url>
   ```

**WHY-SAFE:** the committed file (and the example) carry only placeholders
that match no entropy / prefix signature. The real values live in an
uncommitted local `.env` or the environment.

**VERIFY:** re-scan → no `SECRET_*` / HARDCODED_SECRET hit in the
committed config; confirm the real `.env` is gitignored and untracked.

**NO-FUNCTIONALITY-LOSS:** the application reads the same variables at
runtime; `.env.example` documents exactly which variables are required so
a new contributor can reproduce the local config. (If the value was
already committed and is live, this is necessary but NOT sufficient — also
apply B6: rotate + purge history.)

---

## B4 — Secret stored in the OS keychain → runtime keychain read

For a secret that should live in the platform credential store rather than
the environment.

**BEFORE** (described): a credential literal in source for a value that
ought to be held in the OS keychain / secret service. The literal is the
leak.

**AFTER** (macOS — read the value at runtime from the login keychain):

```python
import subprocess

def read_keychain_secret(service: str, account: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"keychain item not found: {service}/{account}")
    return result.stdout.strip()
```

**AFTER** (Linux — Secret Service via `secret-tool`):

```python
import subprocess

def read_secret_tool(attr_key: str, attr_val: str) -> str:
    result = subprocess.run(
        ["secret-tool", "lookup", attr_key, attr_val],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("secret-service item not found")
    return result.stdout.strip()
```

Store the item once out-of-band (the macOS Keychain Access app /
`secret-tool store`); the code reads it at runtime.

**WHY-SAFE:** no credential value is in the source — only the service /
account *names* and a keychain lookup. The subprocess uses an argv list
with `shell=False`, so the names cannot reach a shell.

**VERIFY:** re-scan → no credential literal remains; confirm the keychain
read succeeds for the stored item.

**NO-FUNCTIONALITY-LOSS:** the same value is retrieved at runtime from the
platform store; a missing item raises immediately rather than passing an
empty credential.

---

## B5 — Leaked private path / username → relativize

Fires the in-process private-info CRITICAL (`Private info leaked: …`) or
RC-135 (`Hardcoded user-home path`) when an absolute path embeds a
username or a developer-machine home directory.

**BEFORE** (described): a hardcoded absolute path that contains a real
username or a developer-machine home directory (an absolute path under a
user home, or a network share with an account name). The embedded
username / home is the leak.

**AFTER** (relativize to a plugin-root or home reference):

```python
import os
from pathlib import Path

# Plugin-bundled resource: anchor on the plugin root.
data_dir = os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "data")

# User-scoped config: anchor on the user's home, not a hardcoded one.
config_dir = Path.home() / ".config" / "myplugin"
```

For shell, use `"$CLAUDE_PLUGIN_ROOT"` / `"$HOME"` instead of a literal
path.

**WHY-SAFE:** the path no longer embeds a specific username or
developer-machine home; it resolves per-machine at runtime from the
plugin root or the current user's home, so the private-info / RC-135 rule
finds no hardcoded home path.

**VERIFY:** re-scan → the private-info CRITICAL / RC-135 on that line is
gone.

**NO-FUNCTIONALITY-LOSS:** the path resolves to the correct location on
every machine — for a bundled resource via the plugin root, for
user-scoped state via the current home — instead of a path that only
existed on one developer's machine.

---

## B6 — Verified-live committed secret → ROTATE + purge history (reverse-case)

The rare case where redaction is NOT enough: an external scanner verifies
a committed value as a live secret.

**BEFORE** (described): a committed credential that an external scanner
reports as *verified live* (it successfully authenticated). The value is
already in git history; every clone has it.

**AFTER** — REFUSE the redact-only path. The agent does NOT silently edit
the file to hide the leak. It FLAGS the finding with the two-part
remediation the user must perform:

1. **Rotate the credential** at the provider: issue a new value and revoke
   the exposed one, so the leaked value is dead even where it persists in
   clones and forks. Provider pointers:
   - Cloud API key → the provider's console / IAM, revoke + reissue.
   - GitHub / npm / PyPI token → the account's token settings, revoke +
     reissue.
   - OAuth token → revoke the grant and re-authorize.
   - Database / service password → change it at the service and rotate any
     dependent connection strings.
2. **Purge it from git history** with a history-rewriting tool, then
   force-push, and have collaborators re-clone. (Redacting only the
   working tree leaves the value in every prior commit.)

**WHY-SAFE:** rotation makes the exposed value useless even though it
remains in history elsewhere; purging removes it from the repository's
history going forward. Editing only the working-tree file would *hide* a
live leak — strictly worse than flagging it.

**VERIFY:** after the user rotates + purges, re-scan → the external
scanner no longer reports the value (it is gone from history) and the old
value no longer authenticates (it was rotated).

**NO-FUNCTIONALITY-LOSS:** the application uses the new, rotated value
(read at runtime per B1–B4). The flag is explicit because this is a
user-owned security action, not a shape rewrite — the agent surfaces it
precisely instead of performing an irreversible history rewrite or a push
on the user's behalf.

---

## Per-file-kind redaction table

The same leak takes a slightly different redacted form per file kind. In
every row, the real value moves to a runtime read (B1–B4) and the file
keeps only a placeholder or a reference.

| File kind | Redacted / runtime-read form |
|-----------|------------------------------|
| `.py` | `os.environ.get("NAME")` with a fail-fast when unset (B1) |
| `.js` / `.ts` | `process.env.NAME` with a thrown error when unset (B1) |
| `.sh` | `: "${NAME:?NAME is not set}"` (B1) |
| `.json` | no secret value in the file; read at runtime, or document a placeholder key in an example file (B3) |
| `.yaml` / `.yml` | `${{ secrets.NAME }}` / `${{ vars.NAME }}` in a workflow (B2); a placeholder in a committed config (B3) |
| `.toml` | no secret value; read at runtime; placeholder in an example config (B3) |
| `.md` | an obvious placeholder — `<YOUR_API_TOKEN>` / `${API_TOKEN}` — never a real-looking token (docs never need a live value) |
| `.github/workflows/*.yml` | `${{ secrets.NAME }}` for secrets, `${{ vars.NAME }}` for non-secret config (B2); never inline |
| `.plist` | no secret value in the bundled plist; read at runtime from the keychain (B4) or the environment (B1) |

---

## Cross-cutting rules (restated)

1. **Check the `rule_id` and file kind first.** A placeholder already in a
   doc, or a value already read from the environment, does not fire — read
   the live report and redact only what actually fires.
2. **Redact to the least-invasive form.** A value that is never used →
   placeholder; a value used at runtime → runtime read; a value already
   committed and live → rotate + purge (B6), not a quiet edit.
3. **One finding, one minimal edit, one re-scan.** Fix the specific
   flagged span, re-scan, confirm the leak is gone AND no new finding
   appeared.
4. **A committed-and-live secret is always B6.** Redaction of the working
   tree is necessary but never sufficient; the value is in history and
   must be rotated and purged.
