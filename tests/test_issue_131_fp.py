"""Regression tests for issue #131 — two scanner false-positives (ai-maestro-webdesign).

Both fixes are CORE-mandate detection/FP fixes. Every assertion is TWO-SIDED —
the benign shape CLEARS *and* the SAME rule's malicious sibling still FIRES at
its real (blocking) severity. NO rule suppression, NO ``--strict`` relax, NO
``exclude_paths``/whole-file skip.

FP-A — skillaudit ``PROTOTYPE_POLLUTION`` (high → MAJOR) fires on the canonical
shadcn ``cn()`` class-name helper::

    export function cn(...inputs: ClassValue[]) {
      return twMerge(clsx(inputs))
    }

There is no proto-pollution sink here. The over-match was catalog pattern 6 (the
``merge``-family gadget shape): the merge-family verb lacked a leading word
boundary (so a camelCase ``twMerge``/``clsx`` matched mid-word) and the generic
``input`` token lacked word boundaries (so the ``inputs`` rest param matched the
``input`` substring). FIX (``scripts/rules/skillaudit_patterns.json`` pattern 6):
word-bound the verb (``\b``) and word-bound the generic user-input identifiers
(``\binput\b``/``\bpayload\b``/``\bparams\b``) while adding the standard
camelCase/snake_case user-controlled identifiers (``userInput``/``user_input``/…).
The real sinks (``__proto__[x]=``, ``a["constructor"]["prototype"]=``,
``Object.assign(target, JSON.parse(userInput))``, ``Reflect.set(obj,"__proto__")``,
``_.merge(target, req.body)``) are still caught (patterns 1-5,7,8 + the
tightened 6).

FP-B — the broken-backtick-path check (``cpv_validation_common.validate_md_file_paths``)
flagged ``docs/product/prd.md`` (a USER-project INPUT the skill READS) as a broken
plugin-internal ref. FIX: ``docs/`` and ``docs_dev/`` are NOT plugin-internal
COMPONENT dirs — they (and ``src/``) name documented INPUT/example paths, so an
unresolved ``docs/...``/``src/...`` backtick path is no longer classified internal
and falls through to the ambiguous-prose branch (skipped unless it carries explicit
``./``/``../`` relative-link intent). A REAL broken internal ref under a component
dir (``references/...``, ``scripts/...``, …) still fires MINOR.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import scan_content  # noqa: E402
from cpv_validation_common import ValidationReport, validate_md_file_paths  # noqa: E402


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # The skillaudit result cache is keyed on (content, catalog, __version__, ext),
    # NOT on classifier/pattern code, so it must be bypassed when testing a
    # same-version catalog change.
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _proto_fires(content: str, file_path: str) -> bool:
    """True iff PROTOTYPE_POLLUTION fires UNSUPPRESSED on content."""
    return any(
        f["ruleId"] == "PROTOTYPE_POLLUTION" and not f.get("suppressed")
        for f in scan_content(content, file_path)
    )


# ───────────────────────── FP-A — PROTOTYPE_POLLUTION ──────────────────────────

# The verbatim shadcn cn() helper (ui.shadcn.com). Several wrapper shapes that
# all wire clsx + tailwind-merge over a `...inputs` / `...args` rest param.
_CN_HELPER_MDX = """# Manual installation

Add the `cn` helper.

```ts
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```
"""

_CN_HELPER_TS = (
    'import { clsx, type ClassValue } from "clsx"\n'
    'import { twMerge } from "tailwind-merge"\n'
    "export function cn(...inputs: ClassValue[]) {\n"
    "  return twMerge(clsx(inputs))\n"
    "}\n"
)


class TestFPAShadcnCnHelperCleared:
    """The shadcn cn() helper must NOT fire PROTOTYPE_POLLUTION (the FP)."""

    def test_cn_helper_in_mdx_does_not_fire(self) -> None:
        assert not _proto_fires(_CN_HELPER_MDX, "skills/ui/docs/installation/manual.mdx")

    def test_cn_helper_in_md_does_not_fire(self) -> None:
        assert not _proto_fires(_CN_HELPER_MDX, "skills/ui/docs/manual.md")

    def test_cn_helper_as_raw_ts_source_does_not_fire(self) -> None:
        # The matcher itself must not fire regardless of host extension.
        assert not _proto_fires(_CN_HELPER_TS, "src/lib/utils.ts")

    def test_cn_helper_with_classname_arg_does_not_fire(self) -> None:
        src = 'export const cn = (...args) => twMerge(clsx(args))\n'
        assert not _proto_fires(src, "src/lib/cn.ts")

    def test_bare_clsx_call_does_not_fire(self) -> None:
        assert not _proto_fires("const c = clsx(inputs)\n", "src/lib/x.ts")


class TestFPAMaliciousSiblingStillFires:
    """A REAL prototype-pollution sink MUST still fire at high (blocking)."""

    def test_lodash_merge_req_body_fires(self) -> None:
        src = "```ts\nfunction h(req) { _.merge(target, req.body) }\n```\n"
        assert _proto_fires(src, "skills/x/SKILL.md")

    def test_bracket_proto_assignment_fires(self) -> None:
        src = 'obj["__proto__"]["polluted"] = 1\n'
        assert _proto_fires(src, "src/handler.ts")

    def test_object_assign_json_parse_user_input_fires(self) -> None:
        src = "Object.assign(dst, JSON.parse(userInput))\n"
        assert _proto_fires(src, "src/handler.ts")

    def test_constructor_prototype_chain_fires(self) -> None:
        src = 'a["constructor"]["prototype"].x = 1\n'
        assert _proto_fires(src, "src/handler.ts")

    def test_reflect_set_proto_fires(self) -> None:
        src = 'Reflect.set(obj, "__proto__", evil)\n'
        assert _proto_fires(src, "src/handler.ts")

    def test_dot_proto_assignment_fires(self) -> None:
        src = "target.__proto__.polluted = 1\n"
        assert _proto_fires(src, "src/handler.ts")

    def test_merge_family_with_word_bounded_input_var_fires(self) -> None:
        # `input` as a standalone identifier (not the `inputs` substring).
        src = "```ts\n_.merge(dst, input)\n```\n"
        assert _proto_fires(src, "skills/x/SKILL.md")

    def test_deep_merge_user_data_fires(self) -> None:
        src = "```ts\ndeepMerge(state, userData)\n```\n"
        assert _proto_fires(src, "skills/x/SKILL.md")

    def test_merge_snake_case_user_input_fires(self) -> None:
        src = "```ts\n_.merge(cfg, user_input)\n```\n"
        assert _proto_fires(src, "skills/x/SKILL.md")

    def test_recursive_merge_camelcase_suffix_fires(self) -> None:
        # #131 central-verify hole: a camelCase-SUFFIX merge verb (`...Merge(`)
        # MUST keep firing. The first #131 draft added a leading `\b` to the verb,
        # which — because the scanner matches case-insensitively — silently missed
        # `recursiveMerge`/`customMerge`/`safeMerge`. The argument discriminator
        # (not a verb boundary) is what clears the benign shadcn idiom.
        assert _proto_fires("```ts\nrecursiveMerge(target, req.body)\n```\n", "src/handler.ts")

    def test_custom_merge_camelcase_suffix_fires(self) -> None:
        assert _proto_fires("```ts\ncustomMerge(dst, userInput)\n```\n", "src/handler.ts")

    def test_safe_merge_camelcase_suffix_fires(self) -> None:
        assert _proto_fires("```ts\nsafeMerge(cfg, user_input)\n```\n", "src/handler.ts")

    def test_lodash_merge_with_fires(self) -> None:
        # `mergeWith(` was a PRE-EXISTING gap — the verb-then-`(` anchor never
        # caught a `merge<Suffix>(`. #131 enumerated the deep-merge family
        # (mergeWith/mergeDeep/defaultsDeep) to close it.
        assert _proto_fires("```ts\n_.mergeWith(dst, req.query, fn)\n```\n", "src/handler.ts")

    def test_immutable_merge_deep_fires(self) -> None:
        assert _proto_fires("```ts\nstate.mergeDeep(userInput)\n```\n", "src/handler.ts")

    def test_lodash_defaults_deep_fires(self) -> None:
        assert _proto_fires("```ts\n_.defaultsDeep(cfg, req.body)\n```\n", "src/handler.ts")


class TestFPAVerbFamilyDoesNotOverFire:
    """The #131 deep-merge family addition must not FP on benign shapes — a verb
    NAME alone is never enough; the user-input ARGUMENT is required."""

    def test_merge_sort_benign_algo_does_not_fire(self) -> None:
        # `mergeSort` is deliberately NOT in the verb list; a sorting algo over a
        # variable named `input` must not be read as a proto-pollution sink.
        assert not _proto_fires("```ts\nconst out = mergeSort(input)\n```\n", "src/sort.ts")

    def test_merge_without_user_input_arg_does_not_fire(self) -> None:
        assert not _proto_fires("```ts\nconst o = merge(defaultsObj, overridesObj)\n```\n", "src/cfg.ts")


# ─────────────────────── FP-B — broken-backtick path guard ─────────────────────


def _run_backtick_check(skill_body: str) -> ValidationReport:
    """Write skill_body to a temp plugin's skills/x/SKILL.md and run the check."""
    d = Path(tempfile.mkdtemp())
    (d / "skills" / "x").mkdir(parents=True)
    md = d / "skills" / "x" / "SKILL.md"
    md.write_text(skill_body, encoding="utf-8")
    report = ValidationReport()
    validate_md_file_paths(md_file=md, plugin_root=d, report=report, is_reference_doc=False)
    return report


def _count(rep: ValidationReport, needle: str) -> int:
    return sum(1 for r in rep.results if needle in r.message)


def _count_level(rep: ValidationReport, needle: str, level: str) -> int:
    return sum(1 for r in rep.results if needle in r.message and r.level == level)


class TestFPBUserInputPathNotFlagged:
    """A documented user-project INPUT path under docs/ or src/ is NOT flagged."""

    def test_docs_product_prd_input_path_not_flagged(self) -> None:
        body = (
            "# X\n\n"
            "If `docs/product/prd.md` exists, read it for context.\n\n"
            "## Examples\n\n"
            "- reads `docs/product/prd.md` and produces a flow map.\n"
        )
        rep = _run_backtick_check(body)
        assert _count(rep, "docs/product/prd.md") == 0

    def test_src_input_path_not_flagged(self) -> None:
        body = "# X\n\nThe component lives at `src/components/Button.tsx`.\n"
        rep = _run_backtick_check(body)
        assert _count(rep, "src/components/Button.tsx") == 0

    def test_docs_dev_path_not_flagged(self) -> None:
        body = "# X\n\nScratch notes in `docs_dev/notes.md`.\n"
        rep = _run_backtick_check(body)
        assert _count(rep, "docs_dev/notes.md") == 0


class TestFPBGenuineInternalRefStillFires:
    """A REAL broken ref under a plugin COMPONENT dir MUST still fire MINOR."""

    def test_missing_references_doc_fires(self) -> None:
        body = "# X\n\nLoad `references/contract-validator.md` for the contract.\n"
        rep = _run_backtick_check(body)
        assert _count_level(rep, "references/contract-validator.md", "MINOR") == 1

    def test_missing_scripts_path_fires(self) -> None:
        body = "# X\n\nRun `scripts/missing.py`.\n"
        rep = _run_backtick_check(body)
        assert _count_level(rep, "scripts/missing.py", "MINOR") == 1

    def test_missing_agents_path_fires(self) -> None:
        body = "# X\n\nDispatch `agents/gone.md`.\n"
        rep = _run_backtick_check(body)
        assert _count_level(rep, "agents/gone.md", "MINOR") == 1

    def test_missing_hooks_path_fires(self) -> None:
        body = "# X\n\nWired in `hooks/none.json`.\n"
        rep = _run_backtick_check(body)
        assert _count_level(rep, "hooks/none.json", "MINOR") == 1


class TestFPBRelativeIntentPreserved:
    """A docs/ path with EXPLICIT ./ or ../ relative-link intent still WARNs
    (the relative-link signal is not lost by the input-path carve-out)."""

    def test_explicit_relative_docs_link_warns(self) -> None:
        body = "# X\n\nSee `./docs/local.md` for details.\n"
        rep = _run_backtick_check(body)
        assert _count_level(rep, "docs/local.md", "WARNING") == 1

    def test_mixed_input_and_genuine_ref_only_genuine_fires(self) -> None:
        body = (
            "# X\n\n"
            "If `docs/product/prd.md` exists, read it.\n\n"
            "Load `references/contract-validator.md` for the contract.\n"
        )
        rep = _run_backtick_check(body)
        assert _count(rep, "docs/product/prd.md") == 0
        assert _count_level(rep, "references/contract-validator.md", "MINOR") == 1
