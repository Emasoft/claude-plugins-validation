---
name: cpv-create-local-plugin
description: Create a new Claude Code plugin locally with all standard files (local only — no GitHub repo creation)
user-invocable: true
---

Scaffold a complete plugin repository **locally** using the generator script. This command creates the directory structure and files on disk only — it does NOT create a GitHub repository or push anything.

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" <target-dir> \
  --name <plugin-name> \
  --description "<description>" \
  --author "<author-name>" \
  --author-email "<email>" \
  --github-owner <github-username> \
  --marketplace <marketplace-name> \
  [--license MIT] \
  [--python-version 3.12] \
  [--dry-run]
```

Parse the user's request to determine the plugin name, description, and other parameters. Ask for any missing required parameters (name, description, author, github-owner).

After generation:
1. Validate: `uv run --with pyyaml python "${CLAUDE_PLUGIN_ROOT}/scripts/validate_plugin.py" <target-dir> --strict`
2. **FIX ALL issues** (CRITICAL, MAJOR, MINOR, NIT) — only WARNINGs may remain. The pre-push hook will block publishing otherwise. See common fixes in the canonical-pipeline skill.
3. Re-validate until only WARNINGs remain
4. Suggest `git init && git add -A && git commit -m "Initial scaffold"`
5. To publish on GitHub, use `/cpv-publish-a-plugin-as-github-repo <target-dir>`
6. To register in a marketplace, use `/cpv-publish-a-plugin-to-a-github-marketplace`

**IMPORTANT**: The generated pre-push hook runs 4 gates (version bump, lint, validate --strict, tests) and blocks pushes with ANY non-WARNING issue. Fix everything BEFORE the first push.
