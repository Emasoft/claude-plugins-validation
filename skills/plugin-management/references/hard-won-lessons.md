# Hard-Won Lessons (from real publish runs)

## Table of Contents

- uv run --with pyyaml
- gh secret --body flag
- Update notify-marketplace.yml
- MARKETPLACE_PAT env var
- Strip ANSI codes
- grep -oE not -oP
- Standardize exit code 1
- author.email check
- CI uv sync --extra dev
- Update notify before push
- Local dry-run

## Checklist

- [ ] Running CPV scripts? — use `uv run --with pyyaml python`
- [ ] Setting GitHub secrets? — use `--body` flag or the helper script, NEVER pipe
- [ ] Editing `notify-marketplace.yml`? — update MARKETPLACE_OWNER + MARKETPLACE_REPO before push
- [ ] Need `MARKETPLACE_PAT`? — check env BEFORE asking user
- [ ] Parsing validator output? — strip ANSI codes with `sed 's/\x1b\[[0-9;]*m//g'`
- [ ] Checking author.email? — noreply GitHub format
- [ ] Updating notify workflow? — commit before push, not after
- [ ] First push of a new repo? — run local dry-run first
- Verify CI after push
- Checkov CKV2_ prefix
- pytest exit code 5
- __init__.py no shebang
- Repository field optional
- validate_marketplace paths
- git config user setup
- README sections required

## Lessons

1. **Always `uv run --with pyyaml python`** when running CPV scripts from outside the CPV venv. Without it: `ModuleNotFoundError: No module named 'yaml'`.
2. **Always `--body` flag for `gh secret set`**. Piping does NOT work. Use: `gh secret set NAME --repo owner/repo --body "$VALUE"`
3. **Always update notify-marketplace.yml** after standardize. MARKETPLACE_OWNER/MARKETPLACE_REPO are placeholders.
4. **Check `$MARKETPLACE_PAT` env var** before asking the user: `test -n "$MARKETPLACE_PAT"` first.
5. **Strip ANSI codes** when processing validation output: `| sed 's/\x1b\[[0-9;]*m//g'`
6. **Use `grep -oE` not `grep -oP`** — macOS grep has no Perl regex.
7. **standardize_plugin.py exit code 1 is expected** after `--fix` if warnings remain.
8. **Check `author.email`** in plugin.json — suggest GitHub noreply format if missing.
9. **CI needs `uv sync --extra dev`** not just `uv sync` — without it ruff/pytest/mypy are missing.
10. **Update notify-marketplace.yml BEFORE the first push** — use `--marketplace` flag with standardize.
11. **Run local dry-run BEFORE first push**: `publish.py --gate` and `publish.py --patch --dry-run`.
12. **Verify CI AFTER first push**: `gh run list --repo <owner>/<name> --limit 5`.
13. **Checkov uses `CKV2_` prefix** for GitHub Actions (not `CKV_`).
14. **pytest exit code 5 = no tests collected** — OK for fresh plugins.
15. **`__init__.py` files do NOT need shebangs** — validator excludes them.
16. **`repository` is an OPTIONAL marketplace plugin-entry field** — omitting it emits only a non-blocking WARNING (it does not affect the exit code or the VALID/INVALID verdict). Add it as publishing hygiene so users can find the upstream repo, but a spec-compliant marketplace that omits it is still valid.
17. **`validate_marketplace.py` accepts both paths** — `marketplace.json` at root or `.claude-plugin/`.
18. **Set `git config user.name/email`** before committing in /tmp directories.
19. **Marketplace README needs Uninstall + Troubleshooting sections** — validator blocks on missing.
