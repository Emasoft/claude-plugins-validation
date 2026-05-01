# Security Validator Contract (`validate_security.py`)

## Table of Contents

- [Path-only stdout by default](#path-only-stdout-by-default)
- [Aggregated reporting](#aggregated-reporting)
- [Six external scanners always run](#six-external-scanners-always-run)
- [Self-scan filter parity](#self-scan-filter-parity)
- [Env knobs](#env-knobs)

When invoking `validate_security.py` from this skill or any agent that
loads it, follow this contract — the script's own defaults already
enforce it but the contract is documented here so that agents reading
the skill know what to expect.

## Path-only stdout by default

Without `--json` or `--report`, the script auto-saves the aggregated
report to `$CLAUDE_PROJECT_DIR/reports/security/<timestamp>-<plugin>.md`
(or `$TMPDIR/reports/security/...` on a remote `uvx` invocation) and
prints **only** the compact summary to stdout. The calling agent reads
the report file only when the user asks for details.

## Aggregated reporting

Findings group by `(level, rule_id, message-stem)` so each vulnerability
TYPE shows its full explanation exactly once, followed by a count and
the first 10 file:line occurrences (overflow becomes
`+N more occurrences (same rule, omitted to save tokens)`).
Token-bounded by distinct-rule count.

## Six external scanners always run

No `--no-tirith` / `--no-trufflehog` / `--no-gitleaks` / `--no-semgrep`
opt-out flags exist — passing them now triggers `argparse`
"unrecognized arguments". Each scanner self-skips with an INFO advisory
if its source binary cannot be resolved on PATH or installed from its
source URL.

| Scanner | Source repo |
|---|---|
| cc-audit | <https://github.com/ryo-ebata/cc-audit> (npx remote) |
| tirith | <https://github.com/sheeki03/tirith> (PATH/docker/nix/auto-install) |
| trufflehog | <https://github.com/trufflesecurity/trufflehog> |
| gitleaks | <https://github.com/gitleaks/gitleaks> |
| semgrep | <https://github.com/semgrep/semgrep> |
| Cisco AI Defense skill-scanner | <https://github.com/cisco-ai-defense/skill-scanner> (uvx remote, programmatic-only — no API-key engines) |

## Self-scan filter parity

Every external scanner's findings are routed through CPV's self-scan
filter chain (`cpv_self_scan_skip` → vendored-deps → dev-scratch →
test-files → FP-corpus markdown → per-line catalog/docstring/comment
pattern-source predicate), so scanning a plugin that ships its own
rule catalogs never surfaces the catalog source as a finding.

## Env knobs

- `CPV_NO_TIRITH_INSTALL=1` — disables tirith's auto-install fallback
  for sandboxed CI.
- `CPV_CISCO_SCAN_TIMEOUT_S=<seconds>` — overrides the 600s Cisco-scan
  default.
- `CPV_SKIP_GITHUB_INTEGRITY=1` — bypasses the manifest-anchored
  self-integrity check (development only — never set this in
  production).
