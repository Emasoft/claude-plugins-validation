r"""Regression tests for issue #134 — ``PROTOTYPE_POLLUTION`` FP on NON-JS source.

Prototype pollution is a JavaScript/TypeScript RUNTIME attack class: it needs a
mutable ``Object.prototype`` chain and dynamic ``__proto__`` /
``constructor.prototype`` assignment. Compiled / interpreted NON-JS languages
(Python, Ruby, Go, Rust, Java, PHP, …) have no prototype chain and no ``Object``
global, so the rule is CATEGORICALLY inapplicable there.

The catalog ``PROTOTYPE_POLLUTION`` pattern #6 — the merge-family gadget
``(?:merge|extend|assign|…)\s*\(.*(?:…|input|payload|params|userData|…)`` —
over-fires on the ubiquitous benign Python shape::

    argv.extend(["--payload-json", "x"])

``extend`` is a merge-family verb and ``payload`` / ``input`` / ``params`` /
``userData`` are everyday non-JS identifiers and CLI flags. A ``list.extend`` is
concatenation; it cannot pollute a prototype.

FIX (``scripts/cpv_skillaudit_native.py`` — ``_context_classifier_dispatch``):
a dispatcher-level language carve-out suppresses ``PROTOTYPE_POLLUTION`` ONLY on
an EXPLICIT ALLOWLIST of non-JS SOURCE extensions
(``_PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS``). This is a CLASSIFIER/PREDICATE
change — the catalog regex is byte-identical, so it stays re2-safe and needs no
re2-audit regeneration.

Every assertion is TWO-SIDED — the benign non-JS shape CLEARS *and* a real
JS sink still FIRES at its blocking severity. NO rule suppression, NO ``--strict``
relax, NO ``exclude_paths`` / whole-file skip; JS itself is NOT in the allowlist
so detection is FN-safe by construction.

FN-safety boundary cases proven below:
* JS-family extensions (``.js`` / ``.ts`` / ``.jsx`` / ``.tsx`` / ``.mjs`` /
  ``.cjs``) keep firing — they are absent from the allowlist.
* ``.md`` / ``.json`` / ``.yaml`` / ``.html`` keep firing — they can EMBED JS,
  so the rule stays live (those files route to their own classifiers, never the
  language carve-out).
* An extension-less ``#!/usr/bin/env python3`` hook (shebang-recovered to a
  synthetic ``.py``) is COVERED; a ``#!/usr/bin/env node`` hook (→ ``.ts``,
  JS-family) is NOT cleared.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_skillaudit_native import (  # noqa: E402
    _PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS,
    scan_content,
)


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # The skillaudit result cache is keyed on (content, catalog, __version__, ext),
    # NOT on classifier code, so it must be bypassed when testing a same-version
    # classifier change.
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _proto_fires(content: str, file_path: str) -> bool:
    """True iff PROTOTYPE_POLLUTION fires UNSUPPRESSED on content."""
    return any(
        f["ruleId"] == "PROTOTYPE_POLLUTION" and not f.get("suppressed")
        for f in scan_content(content, file_path)
    )


# The exact issue-#134 minimal repro plus the two sibling shapes the report
# lists as "MUST CLEAR".
_PY_ARGV_EXTEND = 'argv.extend(["--payload-json", "x"])\n'
_PY_DICT_UPDATE_INPUT = 'd.update({"input": x})\n'
_PY_PARAMS_APPEND_USERDATA = "params.append(userData)\n"


# ─────────────────── FP — benign non-JS source must NOT fire ────────────────────


class TestIssue134PythonFpCleared:
    """The issue-#134 Python shapes must NOT fire PROTOTYPE_POLLUTION."""

    def test_argv_extend_payload_flag_cleared(self) -> None:
        # The literal minimal repro from the issue.
        assert not _proto_fires(_PY_ARGV_EXTEND, "scripts/build.py")

    def test_dict_update_input_cleared(self) -> None:
        assert not _proto_fires(_PY_DICT_UPDATE_INPUT, "scripts/build.py")

    def test_params_append_userdata_cleared(self) -> None:
        assert not _proto_fires(_PY_PARAMS_APPEND_USERDATA, "scripts/build.py")

    def test_all_three_in_one_module_cleared(self) -> None:
        src = (
            "def run(argv):\n"
            '    argv.extend(["--payload-json", "x"])\n'
            "    d = {}\n"
            '    d.update({"input": x})\n'
            "    params.append(userData)\n"
            "    return argv\n"
        )
        assert not _proto_fires(src, "scripts/build.py")

    def test_extension_less_python_hook_cleared(self) -> None:
        # A `#!/usr/bin/env python3` hook with no `.py` suffix is shebang-recovered
        # to a synthetic `.py` and must be covered too (the carve-out runs AFTER
        # the shebang-recovery block).
        src = (
            "#!/usr/bin/env python3\n"
            "def run(argv):\n"
            '    argv.extend(["--payload-json", "x"])\n'
            "    return argv\n"
        )
        assert not _proto_fires(src, "hooks/prebuild")


class TestIssue134OtherNonJsLanguagesCleared:
    """Every non-JS SOURCE language in the allowlist clears the merge-gadget FP."""

    def test_rust_vec_extend_cleared(self) -> None:
        assert not _proto_fires('args.extend(["--payload", input]);\n', "src/main.rs")

    def test_go_append_params_cleared(self) -> None:
        assert not _proto_fires('argv = append(argv, "--payload", params)\n', "main.go")

    def test_ruby_merge_payload_cleared(self) -> None:
        assert not _proto_fires('opts.merge!({"payload" => input})\n', "lib/cli.rb")

    def test_php_merge_userdata_cleared(self) -> None:
        assert not _proto_fires("$opts = array_merge($opts, $userData);\n", "src/Cli.php")

    def test_java_putall_params_cleared(self) -> None:
        assert not _proto_fires("opts.putAll(params); merge(dest, payload);\n", "Cli.java")

    def test_csharp_addrange_input_cleared(self) -> None:
        assert not _proto_fires('args.AddRange(new[]{"--payload", input});\n', "Cli.cs")


# ───────────── FN-safety — a REAL JS prototype-pollution sink still fires ─────────


class TestIssue134JsSinksStillFire:
    """JS-family files are NOT in the allowlist — real sinks keep firing."""

    def test_object_assign_req_body_js_fires(self) -> None:
        assert _proto_fires("Object.assign(target, req.body);\n", "scripts/sink.js")

    def test_lodash_merge_req_body_ts_fires(self) -> None:
        assert _proto_fires("_.merge(dest, req.body);\n", "src/sink.ts")

    def test_defaultsdeep_req_query_jsx_fires(self) -> None:
        assert _proto_fires("_.defaultsDeep(o, req.query);\n", "src/sink.jsx")

    def test_assign_user_input_tsx_fires(self) -> None:
        assert _proto_fires("Object.assign(t, userInput);\n", "src/sink.tsx")

    def test_merge_payload_mjs_fires(self) -> None:
        assert _proto_fires("_.merge(dest, payload);\n", "scripts/sink.mjs")

    def test_extend_user_data_cjs_fires(self) -> None:
        assert _proto_fires("$.extend(dest, user_data);\n", "scripts/sink.cjs")

    def test_proto_assignment_js_fires(self) -> None:
        # A non-merge-gadget pattern (pattern 0) on a .js file is unaffected.
        assert _proto_fires('obj["__proto__"]["polluted"] = 1;\n', "scripts/sink.js")

    def test_extension_less_node_hook_still_fires(self) -> None:
        # `#!/usr/bin/env node` recovers to `.ts` (JS-family) → still fires.
        src = "#!/usr/bin/env node\nObject.assign(target, req.body);\n"
        assert _proto_fires(src, "hooks/posthook")


class TestIssue134JsEmbeddingSurfacesStillFire:
    """Files that can EMBED JS are absent from the allowlist → rule stays live."""

    def test_markdown_js_fence_sink_fires(self) -> None:
        md = (
            "# How prototype pollution works\n\n"
            "```js\n"
            "Object.assign(target, req.body);\n"
            "```\n"
        )
        assert _proto_fires(md, "skills/x/SKILL.md")

    def test_html_inline_script_sink_fires(self) -> None:
        html = "<html><body><script>Object.assign(t, req.body);</script></body></html>\n"
        assert _proto_fires(html, "assets/page.html")


# ───────────────────────── the allowlist constant itself ────────────────────────


class TestIssue134AllowlistInvariants:
    """The allowlist must contain non-JS source and EXCLUDE every JS-family ext."""

    def test_python_in_allowlist(self) -> None:
        assert ".py" in _PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS

    def test_rust_in_allowlist(self) -> None:
        # Generalises the per-file Rust clear (issue #129/#71) to the dispatcher.
        assert ".rs" in _PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS

    @pytest.mark.parametrize("ext", [".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"])
    def test_js_family_never_in_allowlist(self, ext: str) -> None:
        assert ext not in _PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS

    @pytest.mark.parametrize("ext", [".md", ".markdown", ".json", ".yaml", ".yml", ".html", ".htm"])
    def test_js_embedding_surfaces_never_in_allowlist(self, ext: str) -> None:
        # These can embed JS; suppressing here would hide a real sink.
        assert ext not in _PROTOTYPE_POLLUTION_INERT_SOURCE_EXTS
