# FP Corpus

Labelled exemplars driving the context-aware classifier work
described in `design/tasks/TRDD-fe006962-f3ec-48e8-bb2b-849ec7109d2c-context-aware-detection.md`.

Each rule that opts into the classifier owns a markdown file under
this directory named `<rule_id>.md`. The file contains paired blocks
of true positive (TP) and false positive (FP) exemplars taken from
the v2.36 → v2.41 sweep. The format is intentionally
mostly-prose-with-fenced-code: human-readable for review, mechanically
parseable by the corpus harness.

## File layout

```markdown
# <RULE_ID> — short rule description

## TP exemplars

### TP-1: <one-line description>

```<lang>
<code that should fire>
```

**File role:** source | test | doc | fixture | sample
**Rationale:** why this match is real

## FP exemplars

### FP-1: <one-line description>

```<lang>
<code that should NOT fire>
```

**File role:** source | test | doc | fixture | sample
**Rationale:** why this match is a false positive
```

## Corpus harness

`scripts/bench_fp_classifier.py` (TBD — Step 3 of the TRDD) iterates
every `<rule_id>.md` file, extracts exemplars, runs the registered
classifier, and prints precision / recall per rule. The corpus is the
regression suite: any classifier change that re-introduces an FP or
suppresses a TP will be caught by the bench.

## Adding new rules

1. Pick a rule from the v2.41 binary-toggle guard list
   (RC-21 / RC-22 / RC-65 / RC-87 / RC-93 are the seed candidates).
2. Sweep the seven emasoft-plugins for occurrences of the rule.
3. Hand-label each occurrence as TP / FP with a one-line rationale.
4. Drop both lists into `<rule_id>.md` using the layout above.
5. Add a classifier function in `scripts/cpv_fp_classifier_rules/`
   (or inline in `cpv_fp_classifier.py` for the first few — keeping
   them centralized while the API stabilizes).
6. Add tests in `tests/test_cpv_fp_<rule>_classifier.py` that
   exercise both lists.

The TRDD success criterion is "≥5 TP and ≥5 FP exemplars per rule".
Below that, the precision / recall numbers are too noisy to be
informative.
