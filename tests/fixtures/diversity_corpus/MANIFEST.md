# Diversity Corpus

Manifest of non-emasoft Claude Code plugins used as a regression
sentinel for the v2.47 generalization sweep. Each row records:

- **Plugin** — the plugin name
- **Author** — third-party authorship (NOT Emasoft)
- **Repo / local path** — where to obtain the plugin
- **Why representative** — what FP class it surfaced

These plugins are NOT vendored in the CPV repo; the manifest records
them by reference so reviewers can re-run the scan against the same
versions. The diversity corpus is the regression invariant: any new
FP that appears in any of these plugins must be addressed via a
GENERAL predicate, NEVER a per-plugin allowlist.

| Plugin | Author | Source | Surfaced FP class |
|--------|--------|--------|-------------------|
| ccpm | CCPM Plugin Development Team | `/Users/emanuelesabetta/Code/CCProjectManager/ccpm` | RC-110/RC-112 variable-anchored shell paths (`${SCRIPT_DIR}/..`); RC-63 docstring usage examples |
| cc-token-saver | taekim34 | `/Users/emanuelesabetta/Code/cc-token-saver` (https://github.com/taekim34/cc-token-saver) | RC-11 i18n compound terms (`API-вызов`); RC-92 empty placeholder elements; RC-76 i18n locale bundles |
| defuddle-skill | (unattributed third-party) | `/Users/emanuelesabetta/Code/defuddle-skill` | Sanity check — should pass clean |
| claude-code-safety-net | kenryu42 | https://github.com/kenryu42/claude-code-safety-net | JS template-literal `$(...)` build patterns; test-fixture skip for security tools |
| arscontexta | agenticnotetaking | https://github.com/agenticnotetaking/arscontexta | Bash boolean-function chain (`if $has_x && $has_y; then`); shell heredoc + markdown bullet lists |
| modularity | vladikk | https://github.com/vladikk/modularity | Sanity check — should pass clean |

## Expected severity counts (v2.47.0 syntactic floor)

| Plugin | CRITICAL | MAJOR | MINOR | NIT | WARNING | Score |
|--------|----------|-------|-------|-----|---------|-------|
| ccpm | 0 | 1 | 4 | 0 | 0 | 78/100 |
| cc-token-saver | 0 | 0 | 1 | 0 | 0 | 97/100 |
| defuddle-skill | 0 | 0 | 0 | 0 | 0 | 100/100 |
| claude-code-safety-net | 1 | 0 | 3 | 0 | 0 | 66/100 |
| arscontexta | 1 | 4 | 6 | 0 | 0 | 17/100 |
| modularity | 0 | 0 | 0 | 0 | 0 | 100/100 |

Remaining findings are mostly TPs (real `--no-verify` use, real
Windows-path documentation, real RC-02 prose-conditional in skill
docs) — not FPs that the generalizations should suppress.

## Re-running the scan

```bash
for p in /Users/emanuelesabetta/Code/CCProjectManager/ccpm \
         /Users/emanuelesabetta/Code/cc-token-saver \
         /Users/emanuelesabetta/Code/defuddle-skill \
         /tmp/diversity_corpus/claude-code-safety-net \
         /tmp/diversity_corpus/arscontexta \
         /tmp/diversity_corpus/modularity; do
  CPV_SKIP_GITHUB_INTEGRITY=1 \
    python3 scripts/validate_security.py "$p"
done
```

The CPV self-scan-skip mechanism is bypassed here because the
diversity-corpus plugins are ALSO not part of CPV's hash manifest.
The integrity check fires when scanning CPV-recognized files; for
unknown files (i.e. these third-party plugins), it does nothing.

## Adding new plugins

When a new diversity-corpus plugin surfaces an FP:

1. **NEVER add a per-plugin allowlist.** The generalization principle
   (this directory's reason for existing) prohibits hardcoded literals
   that uniquely identify any one plugin.

2. Identify the underlying invariant the FP exemplifies. What is the
   GENERAL property of the line/file that distinguishes documentation
   from active attack content?

3. Encode the invariant as a predicate in `validate_security.py` or
   `cpv_validation_common.py` with a name like
   `_is_<invariant>(line, …)`.

4. Add ≥10 distinct positive cases for the predicate in the
   appropriate test file, plus ≥3 negative cases (real attacks must
   still fire).

5. Re-scan the entire diversity corpus AND the v2.46 verification
   corpus (`tests/fixtures/fp_corpus/`) to confirm no regression.
