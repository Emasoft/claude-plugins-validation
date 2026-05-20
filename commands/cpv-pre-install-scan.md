---
name: cpv-pre-install-scan
description: Scan any skill / plugin / marketplace for security threats BEFORE installing it. Runs the MANDATORY native skillaudit scanner (50 rules / 489 patterns) + the full CPV security pipeline in a sandboxed tmp dir — never writes to ~/.claude/plugins/cache/. Use it before `claude plugin install`, before adding a remote skill, or any time you're about to run untrusted code.
argument-hint: <target> — local path, GitHub URL, owner/repo slug, or archive
---

# /cpv-pre-install-scan

Pre-install security gate. Scans an untrusted skill, plugin, or
marketplace **before** it lands in `~/.claude/plugins/cache/`.

## What it scans

The scanner runs all of:

* The full CPV security pipeline (27 in-process checks + 5 external
  scanners when available)
* The **MANDATORY** native skillaudit rule catalog (50 rules / 489
  patterns across 21 threat categories — credential theft, data
  exfiltration, prompt injection, MCP schema poisoning, A2A attacks,
  obfuscation, supply-chain, container escape, persistence, crypto
  theft, etc.)
* Invisible-Unicode / bidi-override detection
* Base64 + hex + Unicode-escape + char-code obfuscation decoders
* Hardcoded-secret detection (GitHub/AWS/Slack/Discord/Telegram/Vercel/npm/PyPI/OpenAI/Anthropic/Google/Stripe tokens, PEM keys, JWTs)

## How it works

```
cpv-pre-install-scan <target>
```

The target is fetched into `${TMPDIR}/cpv-preinstall-<uuid>/` — a
sandboxed work directory. The scanner runs read-only static
analysis. **No target code is executed.** When the scan completes
the sandbox is deleted.

Accepted target forms:

| Form | Example |
|------|---------|
| Local path | `cpv-pre-install-scan /path/to/plugin` |
| GitHub URL | `cpv-pre-install-scan https://github.com/owner/repo` |
| owner/repo slug | `cpv-pre-install-scan owner/repo` |
| Release URL | `cpv-pre-install-scan https://github.com/owner/repo/releases/tag/v1.2.3` |
| Single file URL | `cpv-pre-install-scan https://example.com/SKILL.md` |
| Local archive | `cpv-pre-install-scan plugin.tar.gz` |

## Exit codes

* `0` — clean. Zero CRITICAL, zero MAJOR findings. Safe to install.
* `1` — findings. CRITICAL or MAJOR present. **Do NOT install.**
* `2` — usage error (bad target, fetch failure, etc.).

## Iron rule

The skillaudit native scanner runs on EVERY pre-install scan. There
is no `CPV_NO_SKILLAUDIT`, no `--skip-skillaudit`, no opt-out. A
missing rule catalog is reported as CRITICAL.

When uncertain matches are detected (e.g. shell-keyword substring in
documentation), the scanner DEMOTES them to NIT-level "⚠ (demoted,
needs review)" findings instead of silently suppressing them — the
downstream security agents triage these for verification.

## Execution

```bash
uv run python "$CLAUDE_PLUGIN_ROOT/scripts/cpv_pre_install_scan.py" "$ARGUMENTS"
```

On exit:

* Clean → tell the user the plugin is safe to install with the
  standard `claude plugin install` flow.
* Blocked → list the top 5 most severe findings inline and tell the
  user **NOT** to install; the report file path is printed for the
  user to share with the plugin author or the security agent.

The sandbox is cleaned up automatically (use `--keep-sandbox` to
preserve it for debugging).
