#!/usr/bin/env python3
"""Two-sided regression lock for issue #102 — JWT_VULN false positives on a
security-review plugin's lens/checklist files.

A code-REVIEW plugin's `*.lens.md` / checklist / scenario-data files necessarily
ENUMERATE JWT anti-pattern vocabulary (`algorithms=None`, `alg:'none'`,
`ignoreExpiration: True`, `verify_exp=False`, `jwt.decode(...)`) as the tokens
the auditor must grep for. In markdown prose / a GFM-table row / a checklist
bullet / a DATA fence, none of that can run — yet `skillaudit`'s JWT_VULN
intent-matcher fired on it (demoted to NIT, which still blocks `--strict`).

`_is_inert_jwt_vuln_doc` certifies the inert CONFIG-anti-pattern shape as
`safe_literal` (full suppress) ONLY in a non-code-fence markdown context, and
KEEPS firing inside an executable code fence (```python / ```js / …) where a
real `jwt.decode(token, algorithms=None)` would run.

CRUCIAL FN-SAFETY (the difference from #78's INSECURE_TLS analog): JWT_VULN
ALSO matches a LEAKED SECRET (`JWT_SECRET='…'` / `jwt_secret=…`) and a JWT TOKEN
LITERAL (`eyJ…eyJ…`). A committed secret / token is a REAL exposure regardless
of the surrounding markdown, so those sub-patterns are NEVER suppressed — only
the inert config anti-patterns are. Every CLEAR below is therefore paired with a
leak / executable-fence case that MUST still surface.

All cases verified through the REAL classifier:
`import _skillaudit_markdown_context as ctx`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ────────────────────────────────────────────────────────────────────────
# CLEARS — an inert JWT config anti-pattern in a non-code-fence md context.
# ────────────────────────────────────────────────────────────────────────


class TestJwtConfigDocClears:
    def test_alg_none_in_prose_is_inert(self) -> None:
        """`alg:'none'` in a checklist bullet (no fence) → inert (suppressed)."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "alg:'none'", "JWT_VULN") is True

    def test_algorithms_none_in_prose_is_inert(self) -> None:
        """`algorithms=None` in prose → inert."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "algorithms=None", "JWT_VULN") is True

    def test_ignore_expiration_in_prose_is_inert(self) -> None:
        """`ignoreExpiration: True` in a checklist bullet → inert."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "ignoreExpiration: True", "JWT_VULN") is True

    def test_verify_exp_false_in_prose_is_inert(self) -> None:
        """`verify_exp=False` in prose → inert."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "verify_exp=False", "JWT_VULN") is True

    def test_config_in_data_fence_is_inert(self) -> None:
        """`alg:'none'` inside a DATA fence (json) → inert (data cannot run)."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc((0, 0, "json"), "alg:'none'", "JWT_VULN") is True

    def test_reporter_line_via_classify_is_safe_literal(self) -> None:
        """The verbatim #102 checklist line → safe_literal through classify()."""
        import _skillaudit_markdown_context as ctx

        src = "- `algorithms=None` (PyJWT), `alg:'none'`, or no algorithm pinning → MUST-FIX"
        assert ctx.classify("jwt.lens.md", src, 0, "alg:'none'", "JWT_VULN") == "safe_literal"


# ────────────────────────────────────────────────────────────────────────
# STILL FIRES — a LEAKED secret/token, or a real call in an exec fence.
# ────────────────────────────────────────────────────────────────────────


class TestJwtRealExposureStillFires:
    def test_leaked_jwt_secret_not_suppressed(self) -> None:
        """`JWT_SECRET='hunter2short'` in markdown → a real leak, NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "JWT_SECRET='hunter2short'", "JWT_VULN") is False

    def test_leaked_jwt_secret_lowercase_underscore_not_suppressed(self) -> None:
        """`jwt_secret = \"abc123\"` (lowercase/underscore form) → NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, 'jwt_secret = "abc123"', "JWT_VULN") is False

    def test_jwt_token_literal_not_suppressed(self) -> None:
        """An `eyJ…eyJ…` JWT token literal in markdown → a real leak, NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        tok = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc"
        assert ctx._is_inert_jwt_vuln_doc(None, tok, "JWT_VULN") is False

    def test_config_in_python_fence_not_suppressed(self) -> None:
        """`algorithms=None` inside a ```python fence → real code, NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc((0, 0, "python"), "algorithms=None", "JWT_VULN") is False

    def test_config_in_js_fence_not_suppressed(self) -> None:
        """`alg:'none'` inside a ```js fence → real code, NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc((0, 0, "js"), "alg:'none'", "JWT_VULN") is False

    def test_config_in_ts_fence_not_suppressed(self) -> None:
        """`ignoreExpiration: true` inside a ```ts fence → real code, NOT suppressed."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc((0, 0, "ts"), "ignoreExpiration: true", "JWT_VULN") is False


# ────────────────────────────────────────────────────────────────────────
# Rule scoping — the discriminator must not generalise to other rules.
# ────────────────────────────────────────────────────────────────────────


class TestJwtDocRuleScoping:
    def test_other_rule_declines(self) -> None:
        """A non-JWT_VULN rule with the same shape is not suppressed by this branch."""
        import _skillaudit_markdown_context as ctx

        assert ctx._is_inert_jwt_vuln_doc(None, "alg:'none'", "INSECURE_TLS") is False
        assert ctx._is_inert_jwt_vuln_doc(None, "alg:'none'", "CMD_INJECTION") is False
