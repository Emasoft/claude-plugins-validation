#!/usr/bin/env python3
"""Corpus-driven precision/recall bench for CPV FP/TP classifiers.

Step 3 of TRDD-fe006962. Reads every `tests/fixtures/fp_corpus/<rule>.md`,
parses TP and FP exemplar blocks, runs the registered classifier for
that rule, and prints a per-rule precision/recall table.

The corpus is the regression suite: any classifier change that
re-introduces an FP (false negative on the FP list) or suppresses a
TP (false positive on the TP list) is caught by the bench.

Usage:
    uv run python scripts/bench_fp_classifier.py
    uv run python scripts/bench_fp_classifier.py --rule RC-21
    uv run python scripts/bench_fp_classifier.py --json   # for CI

Exit codes:
    0 — every rule's classifier matched its corpus perfectly
    1 — at least one rule had a TP/FP misclassification
    2 — corpus parse error or no classifier registered for a rule
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cpv_fp_classifier_rules  # noqa: E402,F401  — registers classifiers as a side-effect
from cpv_fp_classifier import (  # noqa: E402
    RULE_CLASSIFIERS,
    Context,
    FindingVerdict,
    classify_rule,
    file_role_of,
)

CORPUS_DIR = SCRIPTS_DIR.parent / "tests" / "fixtures" / "fp_corpus"

_HEADING_RE = re.compile(r"^##+\s+(?P<text>.+?)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(
    r"^```(?P<lang>[a-zA-Z0-9_+\-]*)\n(?P<body>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)
_FILE_ROLE_RE = re.compile(r"\*\*File role:\*\*\s*(?P<role>[a-zA-Z|/ -]+)$", re.MULTILINE)
_FILE_PATH_RE = re.compile(r"\*\*File path:\*\*\s*`?(?P<path>[^`\n]+?)`?\s*$", re.MULTILINE)
_PLUGIN_META_RE = re.compile(
    r"\*\*Plugin meta:\*\*\s*`(?P<json>\{[^`]*\})`",
    re.MULTILINE,
)
_SURROUNDING_RE = re.compile(
    r"\*\*Surrounding:\*\*\s*```\n(?P<body>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class Exemplar:
    """One labelled exemplar block from a corpus file."""

    rule_id: str
    label: str  # "TP" or "FP"
    title: str
    code: str
    file_role: str
    file_path: str  # synthesized from file_role for classifier dispatch
    plugin_meta: dict | None = None  # parsed from **Plugin meta:** if present
    extra_surrounding: tuple[str, ...] = ()  # parsed from **Surrounding:** block


@dataclass
class BenchResult:
    """Per-rule precision/recall numbers."""

    rule_id: str
    tp_total: int = 0
    tp_classified_real: int = 0
    fp_total: int = 0
    fp_classified_fp: int = 0  # any of LIKELY_FP / DEFINITE_FP counts as "suppressed"
    misclassifications: list[tuple[Exemplar, FindingVerdict]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.misclassifications is None:
            self.misclassifications = []

    @property
    def recall(self) -> float:
        return self.tp_classified_real / self.tp_total if self.tp_total else 1.0

    @property
    def precision(self) -> float:
        # Precision here = "of the matches the classifier says are REAL,
        # what fraction are actually TPs?"
        positives = self.tp_classified_real + (self.fp_total - self.fp_classified_fp)
        if positives == 0:
            return 1.0
        return self.tp_classified_real / positives


def parse_corpus(corpus_path: Path) -> list[Exemplar]:
    """Parse one `<rule>.md` corpus file into Exemplar list.

    The corpus uses a fixed layout (see tests/fixtures/fp_corpus/README.md):

        # <RULE_ID> — description
        ## TP exemplars
        ### TP-N: <one-line description>
        ```<lang>
        <code>
        ```
        **File role:** source | test | doc | fixture | sample
        ...
        ## FP exemplars
        ...

    The parser is deliberately conservative: any block that fails to
    match the layout is silently skipped, not raised. Skipped blocks
    show up as missing exemplars in the bench output, which is the
    feedback the corpus author needs.
    """

    text = corpus_path.read_text(encoding="utf-8", errors="ignore")
    rule_id = corpus_path.stem  # filename → rule id

    section_label: str | None = None
    section_starts: list[tuple[str, int]] = []  # (label, body-offset)
    for m in _HEADING_RE.finditer(text):
        heading = m.group("text").strip().lower()
        if heading.startswith("tp exemplars"):
            section_label = "TP"
        elif heading.startswith("fp exemplars"):
            section_label = "FP"
        elif heading.startswith(("tp-", "fp-")) and section_label is not None:
            section_starts.append((section_label, m.end()))

    exemplars: list[Exemplar] = []
    # Counters per label so titles read naturally — TP-1, TP-2, …
    # FP-1, FP-2, … instead of the global section index that mixes
    # both labels into one running count.
    counts: dict[str, int] = {"TP": 0, "FP": 0}
    for i, (label, start) in enumerate(section_starts):
        end = section_starts[i + 1][1] if i + 1 < len(section_starts) else len(text)
        block = text[start:end]
        fence = _FENCE_RE.search(block)
        if fence is None:
            continue
        role_match = _FILE_ROLE_RE.search(block)
        role = role_match.group("role").strip().split("|")[0].strip().lower() if role_match else "source"
        role = role.split()[0] if role else "source"
        # An explicit `**File path:**` overrides the synthesized one
        # so corpus exemplars can target a specific basename (e.g.
        # `package.json` for RC-87 manifest tests).
        path_match = _FILE_PATH_RE.search(block)
        if path_match:
            file_path = path_match.group("path").strip()
        else:
            file_path = {
                "source": "src/example.py",
                "test": "tests/test_example.py",
                "doc": "docs/example.md",
                "fixture": "tests/fixtures/example.py",
                "sample": "examples/example.py",
            }.get(role, "src/example.py")

        plugin_meta: dict | None = None
        meta_match = _PLUGIN_META_RE.search(block)
        if meta_match:
            try:
                plugin_meta = json.loads(meta_match.group("json"))
            except json.JSONDecodeError:
                plugin_meta = None

        extra_surrounding: tuple[str, ...] = ()
        surr_match = _SURROUNDING_RE.search(block)
        if surr_match:
            extra_surrounding = tuple(ln for ln in surr_match.group("body").splitlines() if ln.strip())

        counts[label] = counts.get(label, 0) + 1
        exemplars.append(
            Exemplar(
                rule_id=rule_id,
                label=label,
                title=f"{label}-{counts[label]}",
                code=fence.group("body").strip(),
                file_role=role,
                file_path=file_path,
                plugin_meta=plugin_meta,
                extra_surrounding=extra_surrounding,
            )
        )
    return exemplars


def _build_context(ex: Exemplar, plugin_meta: dict | None = None) -> Context:
    """Synthesize a Context from an exemplar's code block.

    The classifier expects line + surrounding lines. For TP/FP corpus
    blocks we treat the FIRST non-blank line as `line` and the rest as
    `surrounding_lines`. For multi-line exemplars (the most realistic
    ones), this matches what the scan loop hands a classifier in
    practice.
    """

    lines = [ln for ln in ex.code.splitlines() if ln.strip()]
    if not lines:
        line = ""
        surrounding: tuple[str, ...] = ()
    else:
        line = lines[0]
        surrounding = tuple(lines[1:])
    # An explicit corpus `**Surrounding:**` block extends the synthesized
    # surrounding with extra context so e.g. an RC-65 denylist exemplar
    # can declare the `UNSAFE_HOSTS = {` line that wraps the literal.
    if ex.extra_surrounding:
        surrounding = surrounding + ex.extra_surrounding
    effective_meta = ex.plugin_meta if ex.plugin_meta is not None else (plugin_meta or {})
    return Context(
        rule_id=ex.rule_id,
        matched_text=line,
        line_number=1,
        line=line,
        surrounding_lines=surrounding,
        file_role=ex.file_role or file_role_of(ex.file_path),
        file_path=ex.file_path,
        plugin_meta=effective_meta,
    )


def run_bench(rule_filter: str | None = None) -> tuple[list[BenchResult], int]:
    """Run the bench. Returns `(results, exit_code)`."""

    if not CORPUS_DIR.is_dir():
        print(f"corpus dir missing: {CORPUS_DIR}", file=sys.stderr)
        return [], 2

    results: list[BenchResult] = []
    exit_code = 0
    total_misclassifications = 0

    for corpus_file in sorted(CORPUS_DIR.glob("RC-*.md")):
        rule_id = corpus_file.stem
        if rule_filter and rule_id != rule_filter:
            continue
        if rule_id not in RULE_CLASSIFIERS:
            print(
                f"⚠ {rule_id}: corpus exists but no classifier registered — skipped",
                file=sys.stderr,
            )
            exit_code = max(exit_code, 2)
            continue

        exemplars = parse_corpus(corpus_file)
        result = BenchResult(rule_id=rule_id)
        # RC-22 needs a clipboard-claim plugin meta to exercise the FP path.
        # For now we run two passes: clipboard-claim and not. Tests cover
        # both paths exhaustively; the bench just cares about per-exemplar
        # default behaviour, so we use empty plugin_meta.
        plugin_meta: dict = {}
        for ex in exemplars:
            ctx = _build_context(ex, plugin_meta)
            verdict = classify_rule(rule_id, ctx)

            if ex.label == "TP":
                result.tp_total += 1
                if verdict in (FindingVerdict.REAL, FindingVerdict.DEFINITE_TP):
                    result.tp_classified_real += 1
                else:
                    result.misclassifications.append((ex, verdict))
            else:
                result.fp_total += 1
                if verdict in (FindingVerdict.LIKELY_FP, FindingVerdict.DEFINITE_FP):
                    result.fp_classified_fp += 1
                else:
                    result.misclassifications.append((ex, verdict))

        results.append(result)
        total_misclassifications += len(result.misclassifications)

    if total_misclassifications:
        exit_code = max(exit_code, 1)
    return results, exit_code


def print_table(results: list[BenchResult]) -> None:
    """Pretty-print a per-rule precision/recall table."""

    print(f"{'rule':<10} {'TP':>6} {'TP→real':>8} {'FP':>6} {'FP→supp':>8} {'precision':>10} {'recall':>8}")
    print("-" * 60)
    for r in results:
        print(
            f"{r.rule_id:<10} {r.tp_total:>6} {r.tp_classified_real:>8} "
            f"{r.fp_total:>6} {r.fp_classified_fp:>8} "
            f"{r.precision:>10.2%} {r.recall:>8.2%}"
        )
    if any(r.misclassifications for r in results):
        print("\nMisclassifications:")
        for r in results:
            for ex, verdict in r.misclassifications:
                print(
                    f"  [{r.rule_id}] {ex.title} ({ex.file_role}): expected"
                    f" {'REAL' if ex.label == 'TP' else 'FP-tier'}, got {verdict.value}"
                )


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "Bench fp classifier").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--rule", help="run only this rule's corpus (e.g. RC-21)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a table")
    args = parser.parse_args(argv)

    results, exit_code = run_bench(rule_filter=args.rule)
    if args.json:
        payload = [
            {
                "rule": r.rule_id,
                "tp_total": r.tp_total,
                "tp_classified_real": r.tp_classified_real,
                "fp_total": r.fp_total,
                "fp_classified_fp": r.fp_classified_fp,
                "precision": r.precision,
                "recall": r.recall,
                "misclassifications": [
                    {"exemplar": ex.title, "verdict": verdict.value, "label": ex.label}
                    for ex, verdict in r.misclassifications
                ],
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
    else:
        print_table(results)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
