---
trdd-id: 02e1672b-973b-44c9-a356-ea0ae6313f36
title: Remove all plugin-declared finding-suppression — FPs must be recognized by CPV's own logic, never read from the plugin under test
status: in-progress
created: 2026-05-28T23:59:32+0200
updated: 2026-05-29T00:02:28+0200
---

<!-- markdownlint-disable-next-line MD025 -->
# TRDD-02e1672b — No plugin-declared finding-suppression

**Filename:** `design/tasks/TRDD-20260528_235932+0200-02e1672b-no-plugin-declared-finding-suppression.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

## User directive (verbatim)

> "My directive is to remove any option to skip or filter as false positives
> elements of the plugin using any configuration file like plugin.json, etc.
> I would prefer the false positives to be recognized as such directly by CPV,
> not by reading them in a plugin.json. Otherwise any malicious actor could
> craft a plugin filled with malicious code and just add that code to the
> plugin.json as false positive to skip, so CPV will validate a very
> dangerous plugin!"

## Threat model

CPV is a **judge**. The artifact being judged (a plugin / marketplace) must
NOT be able to instruct the judge to ignore findings about itself. Any
mechanism where CPV reads a "skip this / treat as FP / allow this" directive
from the plugin-under-test's OWN config (plugin.json, marketplace.json,
settings) is a trust-boundary violation: a malicious author adds the
exempting key alongside the malicious code and CPV passes it.

**Invariant to enforce:** False-positive recognition lives ENTIRELY in CPV's
own classifier/heuristic logic. CPV reads ZERO suppression/skip/allow
directives from the plugin under test.

## Audit — every plugin-declared finding-suppressor (as of v2.108.0)

### Category A — MUST be removed (plugin-under-test self-exemption)

| # | Mechanism | Source file (plugin) | CPV reader | What it suppresses | Security-class? |
|---|-----------|----------------------|-----------|--------------------|-----------------|
| A1 | `cpv.allow_orchestrator_traversal` | plugin.json | validate_skill_comprehensive.py:2219-2255 | parent-`..`-traversal findings in skills | **YES** (path traversal) |
| A2 | `cpv.allow_root_dirs` | plugin.json | validate_plugin.py:1928-2003 | "unexpected top-level dir" finding | structural |
| A3 | `cpv.allow_unversioned_dependencies` | plugin.json | validate_plugin.py:340-350 | unversioned-dependency finding | supply-chain |
| A4 | `cpv.allow_pipeline_drift` | plugin.json | validate_plugin.py:4697-4814 | per-file canonical-pipeline-drift WARNING | structural |
| A5 | `_cpv_skip_upstream_check` + blanket `_`-prefixed silent-accept | marketplace.json entry | validate_marketplace.py:236-310, 1927 | upstream cross-validation check; ALSO any `_`-prefixed field passes with no warning | integrity |

### Category B — NOT finding-suppression (KEEP; out of scope)

- `cpv.strip` (plugin.json) + gitmodules variant → configures the separate
  `strip-dev-parts` TOOL (what to extract to dev submodules). Does not
  suppress any validation finding. Reader: cpv_strip_dev.py, load_strip_config.
- `allowCrossMarketplaceDependenciesOn` / `allowedDependencyMarketplaces`
  (HOSTING marketplace.json) → cross-marketplace dependency POLICY set by the
  marketplace operator, not a plugin self-exempting from a security finding.
  Borderline; recommend KEEP this round, revisit if the threat model extends
  to marketplace-operator trust.

### Category C — identity-detection, related but distinct

- `is_cpv_self_scan` (validate_security.py:1050) flips self-scan suppression
  via Signal 1 (plugin.json `name == "claude-plugins-validation"`) or Signal 2
  (ships `scripts/cpv_validation_common.py` + `scripts/validate_plugin.py`).
  A malicious plugin could satisfy either signal. MITIGATED today: the actual
  skips are **hash-gated** against CPV's committed manifest, and the
  unconditional dev-scratch skip is gated on `_CPV_IS_RUNNING_CPV`
  (target == running CPV). Residual concern: the NAME signal alone flips
  `_CPV_SELF_SCAN_ACTIVE`. Recommend a hardening follow-up (require BOTH a
  signature-file hash match AND name, or drop the name-only signal). Tracked
  here but may be split to its own TRDD.

## THE BLOCKER: CPV dogfoods two of these

CPV's own `.claude-plugin/plugin.json` currently declares:

```json
"cpv": {
  "allow_root_dirs": ["reviews", "design", "templates", "git-hooks"],
  "allow_orchestrator_traversal": ["skills/cpv-batch-validate",
                                    "skills/cpv-batch-scope-diagnose",
                                    "skills/canonical-pipeline"]
}
```

So A1 and A2 cannot be naively deleted — doing so makes CPV's OWN self-scan
fire findings on `reviews/`, `design/`, `templates/`, `git-hooks/` and on the
three named skills' parent-traversal, breaking the mandatory 0/0/0/0
self-validation and the publish Gate 3. This is the proof that the directive
requires **replacing each allowlist with CPV's own built-in recognition**, not
just removing the read.

## Per-mechanism replacement design (FP recognition moves INTO CPV)

- **A2 `allow_root_dirs`** → CPV ships a built-in set of recognized top-level
  dirs (add `reviews`, `design`, `templates`, `git-hooks`, `docs`, etc. to the
  standard allowlist in CPV's code), AND/OR demote "unexpected root dir" to a
  non-blocking INFO/NIT (structural nicety, not a security finding). No plugin
  input. CPV's own dirs are recognized by the built-in set.
- **A1 `allow_orchestrator_traversal`** → CPV's classifier decides whether a
  `..` in a skill is a genuine traversal exploit vs. a benign doc/relative
  reference, using its OWN heuristics (is it inside an exec/read sink? a
  fenced code block? a markdown link? an orchestrator skill referencing a
  sibling skill?). The three CPV skills must be recognized as benign by this
  logic, NOT by name-listing them.
- **A3 `allow_unversioned_dependencies`** → either always-flag (no escape) at
  the current severity, or CPV recognizes the legitimate unversioned form
  itself (e.g. local path-deps). No plugin opt-out.
- **A4 `allow_pipeline_drift`** → the drift WARNING always fires (it is
  advisory/non-blocking already); remove the opt-out. CPV's drift detector
  already knows canonical content, so legitimate intentional drift is the
  author's call to live with the WARNING, not to silence it.
- **A5 `_cpv_skip_upstream_check` + `_`-prefix accept** → remove the blanket
  `_`-prefixed silent-accept; the upstream cross-validation runs
  unconditionally. Any genuinely-FP upstream mismatch is recognized by CPV's
  comparison logic, not waved through by a marketplace-declared flag.

## Work breakdown

1. Replace A2/A1 FP-recognition in CPV code FIRST (so self-scan stays clean).
2. Remove the five plugin.json/marketplace.json reads + their suppression branches.
3. Strip the now-unused `cpv.allow_root_dirs` + `cpv.allow_orchestrator_traversal`
   from CPV's own plugin.json.
4. Rewrite the ~6 impacted test files (they currently assert the opt-out WORKS;
   flip them to assert the opt-out is IGNORED / the finding always fires AND
   that CPV's built-in recognition keeps legit cases clean):
   test_marketplace_upstream_cross_validation.py, test_issues_27_28_29.py,
   test_issue_16_orchestrator_fps.py, test_agent_marketplace_preflight.py,
   test_validate_marketplace_strict_fields.py (+ fixtures).
5. Purge doc/reference mentions of the removed opt-outs (fix-validation refs,
   marketplace-error-index, READMEs, agent prompts).
6. Regen self-hash manifest; full suite green; self-scan 0/0/0/0; publish.

## Acceptance criteria

1. `grep -rn "allow_pipeline_drift|allow_root_dirs|allow_orchestrator_traversal|allow_unversioned_dependencies|_cpv_skip_upstream_check"`
   over `scripts/` returns ZERO reads-as-suppressor (only, if anything,
   rejection logic that warns the key is no longer honored).
2. CPV reads NO skip/allow/exempt directive from any plugin-under-test config.
3. CPV self-scan stays 0/0/0/0 via built-in recognition, with its own
   plugin.json `cpv.allow_*` keys deleted.
4. Tests flipped to two-sided: malicious self-exemption attempt is IGNORED
   (finding still fires) AND legit cases stay clean via CPV's own logic.
5. Full suite green; ships next minor.

## Resolved decisions (user, 2026-05-29)

- **A3/A4 → always-flag, no escape.** The finding always fires at its current
  severity; no plugin can silence it. (A4's drift finding is already a
  non-blocking WARNING; A3 keeps its current severity.)
- **Category C → separate follow-up TRDD.** The name/signature self-scan
  signal is identity-detection, not a config-declared skip; hash-gating
  already mitigates the real risk. File a dedicated hardening TRDD; keep this
  change focused on the five config-declared suppressors.
- **Transition → one-release deprecation WARNING.** When CPV sees a
  now-removed key (e.g. `cpv.allow_root_dirs`), emit a non-blocking WARNING:
  "this key is no longer honored — CPV determines false-positives itself."
  Removed entirely next release.

### A1/A2 replacement, refined (CPV's own recognition)

- **A2 `allow_root_dirs`** → add the legit dirs CPV's own tree uses
  (`reviews`, `design`, `templates`, `git-hooks`, plus common `docs`,
  `examples`) to the BUILT-IN `known_dirs` set in validate_plugin.py. CPV
  recognizes them itself; no plugin input.
- **A1 `allow_orchestrator_traversal`** → drop the `in_allow_list` PASS
  branch. CPV's built-in `is_orchestrator_skill` heuristic (target referenced
  by ≥3 sibling skills) becomes the sole recogniser; for a recognised
  orchestrator target the outcome is PASS (not MINOR) so legit reuse is clean
  WITHOUT a plugin allowlist. FEASIBILITY: must confirm CPV's three listed
  skills are caught by `is_orchestrator_skill`; if not, recognise
  within-plugin sibling traversal (`../<sibling-skill>/` that stays inside the
  plugin root) as benign, reserving MAJOR for traversal that ESCAPES the
  plugin root.
