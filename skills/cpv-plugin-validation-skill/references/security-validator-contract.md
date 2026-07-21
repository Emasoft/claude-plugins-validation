# Security Validator Contract (`validate_security.py`)

## Table of Contents

- [Path-only stdout by default](#path-only-stdout-by-default)
- [Aggregated reporting](#aggregated-reporting)
- [Five external scanners always run](#five-external-scanners-always-run)
- [Pre-scan dedup pipeline (v2.48)](#pre-scan-dedup-pipeline-v248)
- [Self-scan filter parity](#self-scan-filter-parity)
- [Env knobs](#env-knobs)

When invoking `validate_security.py` from this skill or any agent that
loads it, follow this contract — the script's own defaults already
enforce it but the contract is documented here so that agents reading
the skill know what to expect.

## Path-only stdout by default

Without `--json` or `--report`, the script auto-saves the aggregated
report to `$MAIN_ROOT/reports/validate_security/<timestamp>-<plugin>.md`
(or `$TMPDIR/reports/security/...` on a remote `uvx` invocation) and
prints **only** the compact summary to stdout. The calling agent reads
the report file only when the user asks for details.

## Aggregated reporting

Findings group by `(level, rule_id, message-stem)` so each vulnerability
TYPE shows its full explanation exactly once, followed by a count and
the first 10 file:line occurrences (overflow becomes
`+N more occurrences (same rule, omitted to save tokens)`).
Token-bounded by distinct-rule count.

## Five external scanners always run

No `--no-tirith` / `--no-trufflehog` / `--no-semgrep` opt-out flags
exist — passing them now triggers `argparse` "unrecognized arguments".
Each scanner self-skips with an INFO advisory if its source binary
cannot be resolved on PATH or installed from its source URL.

| Scanner | Source repo |
|---|---|
| cc-audit | <https://github.com/ryo-ebata/cc-audit> (persistent `cc-audit` → `npx --yes @cc-audit/cc-audit` fallback) |
| tirith | <https://github.com/sheeki03/tirith> (PATH/docker/nix/auto-install) |
| trufflehog | <https://github.com/trufflesecurity/trufflehog> (`--concurrency=cpu_count` parallel) |
| semgrep | <https://github.com/semgrep/semgrep> (rule packs `p/security-audit` + `p/secrets`) |
| Cisco AI Defense skill-scanner | <https://github.com/cisco-ai-defense/skill-scanner> (persistent `skill-scanner` → `uvx --from cisco-ai-skill-scanner` fallback, programmatic-only — no API-key engines) |

> **v2.48 — gitleaks dropped.** trufflehog's ~700 detectors now provide
> superset coverage, and trufflehog supports parallel scanning safely.
> Run `cpv-doctor --install-scanners` to install all five at once
> (silent, idempotent, per-platform cascade).

## Pre-scan dedup pipeline (v2.48)

Every CPV scan now starts with `fclones` (Rust-based duplicate-file
finder) on the staging tree:

1. **Stage** target → `$TMPDIR/cpv-stage-<TS>/` via hardlinks (same-fs)
   or copy/symlink fallback (cross-fs `EXDEV`).
2. **Dedup** via `fclones group <stage> --format json --hidden --no-ignore`
   — non-canonical hardlinks are deleted from the staging tree.
   The cache is untouched (hardlink count > 1 means the original
   inode survives).
3. **Scan** runs only on canonical files. Internal regex catalog,
   trufflehog/semgrep/Cisco walk the deduped tree once.
4. **Bucket** findings on canonicals are propagated to every original
   member path via the dedup_map snapshot taken in step 2.

If `fclones` isn't on PATH, the autoinstall (`ensure_fclones()`) tries
`brew` (macOS), `snap`/`cargo` (Linux), or GitHub release
(Windows). On total failure: WARNING + scan continues without dedup
(graceful degradation). Set `CPV_NO_FCLONES_INSTALL=1` to disable
the autoinstall.

## Self-scan filter parity

Every external scanner's findings are routed through CPV's self-scan
filter chain (`cpv_self_scan_skip` → vendored-deps → dev-scratch →
test-files → FP-corpus markdown → per-line catalog/docstring/comment
pattern-source predicate), so scanning a plugin that ships its own
rule catalogs never surfaces the catalog source as a finding.

## Env knobs

- `CPV_NO_TIRITH_INSTALL=1` — disables tirith's auto-install fallback
  for sandboxed CI.
- `CPV_NO_FCLONES_INSTALL=1` — disables the silent fclones autoinstall.
- `CPV_CISCO_SCAN_TIMEOUT_S=<seconds>` — overrides the 600s Cisco-scan
  default.
- `PLUGIN_SKIP_GITHUB_INTEGRITY=1` — bypasses the manifest-anchored
  self-integrity check (development only — never set this in
  production). Legacy alias `CPV_SKIP_GITHUB_INTEGRITY=1` honored
  for one release with a deprecation note (TRDD-bbff5bc5; alias
  removed in v2.53.0).
