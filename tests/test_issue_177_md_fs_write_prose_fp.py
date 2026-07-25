#!/usr/bin/env python3
"""Two-sided regression lock for issue #177 — ``skillaudit:filesystem FS_WRITE``
false positive on a BASH-FENCE COMMENT in skill reference documentation.

The FS_WRITE catalog patterns match a BARE dotfile suffix
(``(?:^|[/~\\s'"`])\\.zshrc\\b`` and its ``.bashrc`` / ``.profile`` siblings), so
ANY line inside a ```` ```bash ```` fence that merely NAMES one fired — including
the reporter's prose comment::

    # Ensure $(go env GOPATH)/bin is on your PATH - add it to your shell profile
    # (e.g. ~/.zshrc or ~/.bashrc) so the gopls binary is found

whose enclosing fence's only real commands are ``go install`` / ``which`` /
``gopls version``. No write occurs.

SEVERITY (why this BLOCKED a publish): the catalog declares FS_WRITE ``medium``
(which would map to CPV MINOR), but ``cpv_skillaudit_native`` applies a
BASH-FENCE UPLIFT — a kept match inside a ``bash``/``sh``/``shell``/``zsh`` fence
is promoted ``medium`` -> ``high``, and ``_SEVERITY_MAP`` maps ``high`` ->
``major``. That uplift is why #177 reports **MAJOR**, not the catalog's MINOR.
These tests therefore assert the true emitted tier ``high``.

THE FIX reuses the predicate that ALREADY existed for ``.sh`` files and is
COMMENT-AGNOSTIC: ``_skillaudit_shell_context._shell_match_lacks_write_intent``.
A real write carries a write-intent token (``>`` ``>>`` ``tee`` ``cp`` ``mv``
``ln`` ``sed -i`` ``open(...,'w')`` …); a line naming a dotfile without one
performs no write.

WHY NOT "the line starts with ``#``" (this is load-bearing — a ``#`` rule would
be a FALSE NEGATIVE): ``_EXECUTABLE_LANGS`` includes ``console`` / ``terminal`` /
``tty``, where a leading ``#`` is a ROOT PROMPT rather than a comment, and
``bat`` / ``cmd`` / ``batch``, where ``#`` is not a comment at all; and an
unquoted heredoc body line ``# $(curl evil|sh)`` matches ``^\\s*#`` yet still
executes.

FN CLOSURE SHIPPED ALONGSIDE: the shared ``_WRITE_INTENT_RE`` was MISSING the
symlink / in-place-editor / truncate / Python-file-object write verbs, so
``ln -sf /evil/rc ~/.zshrc``, ``sed -i '' … ~/.zshrc``, ``truncate -s 0
~/.zshrc`` and ``python -c "open('~/.zshrc','w')"`` were demoted to ``info`` in
``.sh`` scripts — a measured, pre-existing FALSE NEGATIVE. Reusing the predicate
in markdown without closing that gap would have propagated the FN to ``.md``, so
the tokens were added in the ONE source of truth and are locked from BOTH sides
here.

Every case is verified through the REAL scanner
(``cpv_skillaudit_native.scan_content``).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# The reporter's path: a skill reference doc. ``references/`` was DELIBERATELY
# removed from ``_DOC_ONLY_DIR_PREFIXES`` (agents are told to FOLLOW recipes in
# reference docs), so this path does NOT get the doc-only demote — it must be
# cleared by the write-intent predicate or not at all.
REF_DOC = "skills/amaa-planning-patterns/references/lsp-enforcement-checklist.md"
SH_PATH = "scripts/setup.sh"


def _fs_write(content: str, path: str) -> list[dict[str, object]]:
    """Run the REAL scanner and return every FS_WRITE finding, suppressed or not."""
    from cpv_skillaudit_native import scan_content  # type: ignore[import-not-found]

    return [f for f in scan_content(content, path) if f.get("ruleId") == "FS_WRITE"]


def _blocking(content: str, path: str) -> list[dict[str, object]]:
    """Return only the FS_WRITE findings that actually count (not suppressed)."""
    return [f for f in _fs_write(content, path) if not f.get("suppressed")]


def _fence(*body: str) -> str:
    """Wrap shell lines in a markdown ```bash fence inside the reporter's doc shape.

    CAUTION for future edits — the heading is load-bearing for the SEVERITY
    assertions. A ``# Setup`` / installation-style heading trips a SEPARATE,
    pre-existing install-doc heuristic that demotes a kept finding ``high`` ->
    ``low``. That demote is unrelated to #177 (a NIT still blocks ``--strict``,
    so the finding is still visible either way), but it masks the bash-fence
    uplift this file asserts. Keep the reporter's own heading so the fixture
    reproduces the MAJOR tier that #177 actually reported.
    """
    lines = "\n".join(body)
    return (
        "# LSP enforcement checklist\n\nInstall the Go language server:\n\n"
        f"```bash\n{lines}\n```\n"
    )


# The verbatim #177 reproducer: the fence's only real commands are go install /
# which / gopls version; the match is the bare .zshrc/.bashrc in comment prose.
GOPLS_FENCE = _fence(
    "# Install via go install",
    "go install golang.org/x/tools/gopls@latest",
    "",
    "# Ensure $(go env GOPATH)/bin is on your PATH - add it to your shell profile",
    "# (e.g. ~/.zshrc or ~/.bashrc) so the gopls binary is found",
    "",
    "# Verify installation",
    "which gopls",
    "gopls version",
)


class TestGuardTheGuard:
    """The helpers must not be vacuous — a helper that always returns [] would
    make every FP assertion below pass while proving nothing."""

    def test_blocking_helper_detects_a_real_write(self) -> None:
        """The _blocking helper returns a non-empty list for an obvious real write."""
        assert _blocking(_fence("echo x > ~/.zshrc"), REF_DOC), (
            "_blocking() found nothing on a blatant redirect write — the helper "
            "is broken, so every MUST-CLEAR assertion in this file is vacuous."
        )

    def test_fs_write_helper_reads_the_right_rule_id(self) -> None:
        """The _fs_write helper actually matches the FS_WRITE ruleId key."""
        hits = _fs_write(_fence("echo x > ~/.zshrc"), REF_DOC)
        assert hits and all(f.get("ruleId") == "FS_WRITE" for f in hits)


class TestIssue177ProseFalsePositiveClears:
    """The reported FP shapes must stop counting."""

    def test_gopls_comment_fence_emits_no_blocking_fs_write(self) -> None:
        """The verbatim #177 gopls install fence raises no counting FS_WRITE finding."""
        assert _blocking(GOPLS_FENCE, REF_DOC) == []

    def test_gopls_comment_fence_finding_is_suppressed_at_info(self) -> None:
        """Any residual #177 gopls FS_WRITE finding is suppressed and demoted to info."""
        for f in _fs_write(GOPLS_FENCE, REF_DOC):
            assert f.get("suppressed") is True
            assert f.get("severity") == "info"

    def test_dotfile_existence_test_clears(self) -> None:
        """A read-only ``[ -f "$HOME/.zshrc" ]`` test in a bash fence does not count."""
        assert _blocking(_fence('[ -f "$HOME/.zshrc" ] && echo found'), REF_DOC) == []

    def test_dotfile_variable_assignment_clears(self) -> None:
        """Assigning a dotfile path to a shell variable is not a write."""
        assert _blocking(_fence('shell_rc="$HOME/.zshrc"'), REF_DOC) == []

    def test_sourcing_a_dotfile_clears(self) -> None:
        """``source ~/.zshrc`` reads the file and so must not count as a write."""
        assert _blocking(_fence("source ~/.zshrc"), REF_DOC) == []


class TestIssue177RealWritesStillFire:
    """The FN side: a genuine dotfile write inside a bash fence must keep firing."""

    def test_append_redirect_still_fires(self) -> None:
        """``echo 'export PATH=...' >> ~/.zshrc`` in a bash fence still fires FS_WRITE."""
        assert _blocking(_fence("echo 'export PATH=$PATH:/opt/bin' >> ~/.zshrc"), REF_DOC)

    def test_copy_over_dotfile_still_fires(self) -> None:
        """``cp custom.zshrc ~/.zshrc`` in a bash fence still fires FS_WRITE."""
        assert _blocking(_fence("cp custom.zshrc ~/.zshrc"), REF_DOC)

    def test_real_write_is_emitted_at_major_tier(self) -> None:
        """A kept bash-fence FS_WRITE is uplifted to ``high`` (CPV MAJOR), not minor."""
        hits = _blocking(_fence("echo 'x' >> ~/.zshrc"), REF_DOC)
        assert hits and all(f.get("severity") == "high" for f in hits), (
            "catalog severity is 'medium', but the bash-fence uplift promotes a "
            "kept match to 'high', which _SEVERITY_MAP maps to CPV 'major' — this "
            "is why #177 was reported as MAJOR."
        )

    def test_truncating_redirect_still_fires(self) -> None:
        """``echo 'x' > ~/.zshrc`` (clobbering redirect) still fires FS_WRITE."""
        assert _blocking(_fence("echo 'x' > ~/.zshrc"), REF_DOC)

    def test_tee_write_still_fires(self) -> None:
        """``tee -a ~/.zshrc`` still fires FS_WRITE."""
        assert _blocking(_fence("tee -a ~/.zshrc < new-config"), REF_DOC)

    def test_heredoc_write_still_fires(self) -> None:
        """``cat > ~/.zshrc <<'EOF'`` still fires FS_WRITE."""
        assert _blocking(_fence("cat > ~/.zshrc <<'EOF'", "export X=1", "EOF"), REF_DOC)


class TestIssue177AdvisorFlaggedWriteVerbs:
    """Writes that carry NO redirect/copy token — the FN class the advisor flagged.

    Each of these was measured to be cleared by the ORIGINAL ``_WRITE_INTENT_RE``;
    the predicate was extended so reusing it in markdown could not introduce a
    false negative.
    """

    def test_symlink_over_dotfile_still_fires_in_markdown(self) -> None:
        """``ln -sf /evil/rc ~/.zshrc`` replaces the dotfile and still fires in a fence."""
        assert _blocking(_fence("ln -sf /evil/rc ~/.zshrc"), REF_DOC)

    def test_python_open_write_mode_still_fires_in_markdown(self) -> None:
        """``python -c "open('~/.zshrc','w')"`` in a fence still fires FS_WRITE."""
        doc = _fence("python -c \"open('/Users/me/.zshrc','w').write('evil')\"")
        assert _blocking(doc, REF_DOC)

    def test_sed_in_place_still_fires_in_markdown(self) -> None:
        """``sed -i '' 's/a/b/' ~/.zshrc`` edits the dotfile in place and still fires."""
        assert _blocking(_fence("sed -i '' 's/a/b/' ~/.zshrc"), REF_DOC)

    def test_perl_in_place_still_fires_in_markdown(self) -> None:
        """``perl -pi -e 's/a/b/' ~/.zshrc`` edits in place and still fires."""
        assert _blocking(_fence("perl -pi -e 's/a/b/' ~/.zshrc"), REF_DOC)

    def test_truncate_still_fires_in_markdown(self) -> None:
        """``truncate -s 0 ~/.zshrc`` empties the dotfile and still fires."""
        assert _blocking(_fence("truncate -s 0 ~/.zshrc"), REF_DOC)

    def test_python_read_mode_is_not_treated_as_a_write(self) -> None:
        """``open('~/.zshrc','r')`` is a read, so it must not be counted as write intent."""
        from _skillaudit_shell_context import (  # type: ignore[import-not-found]
            _shell_match_lacks_write_intent,
        )

        assert _shell_match_lacks_write_intent("python -c \"open('~/.zshrc','r').read()\"", ".zshrc")


class TestIssue177ShellFalseNegativeClosed:
    """The same write verbs were demoted to ``info`` in real ``.sh`` scripts —
    a pre-existing FN in the shell classifier, closed by the shared predicate."""

    def _sh(self, body: str) -> list[dict[str, object]]:
        return _blocking(f"#!/bin/bash\nset -euo pipefail\n{body}\n", SH_PATH)

    def test_symlink_over_dotfile_fires_in_shell_script(self) -> None:
        """``ln -sf /evil/rc ~/.zshrc`` in a .sh script is no longer demoted to info."""
        assert self._sh("ln -sf /evil/rc ~/.zshrc")

    def test_python_open_write_fires_in_shell_script(self) -> None:
        """``python -c "open('~/.zshrc','w')"`` in a .sh script is no longer info."""
        assert self._sh("python -c \"open('/Users/me/.zshrc','w').write('evil')\"")

    def test_sed_in_place_fires_in_shell_script(self) -> None:
        """``sed -i '' 's/a/b/' ~/.zshrc`` in a .sh script is no longer info."""
        assert self._sh("sed -i '' 's/a/b/' ~/.zshrc")

    def test_truncate_fires_in_shell_script(self) -> None:
        """``truncate -s 0 ~/.zshrc`` in a .sh script is no longer info."""
        assert self._sh("truncate -s 0 ~/.zshrc")

    def test_read_only_test_still_clears_in_shell_script(self) -> None:
        """The original r08 FP (``[ -f "$HOME/.zshrc" ]``) must STILL clear in .sh."""
        assert self._sh('[ -f "$HOME/.zshrc" ] && echo yes') == []

    def test_sed_without_in_place_flag_is_not_write_intent(self) -> None:
        """``sed -n '1,5p' ~/.zshrc`` prints without editing, so it stays cleared."""
        assert self._sh("sed -n '1,5p' ~/.zshrc") == []


class TestIssue177NonCommentFenceLanguagesUnaffected:
    """A ``#`` line is NOT a comment in every ``_EXECUTABLE_LANGS`` member — the
    reason the fix keys on write intent rather than on a leading ``#``."""

    def test_console_root_prompt_write_still_fires(self) -> None:
        """In a ```console fence a leading ``#`` is a ROOT PROMPT, so its write fires."""
        doc = "# LSP enforcement checklist\n\n```console\n# echo 'evil' >> ~/.zshrc\n```\n"
        assert _blocking(doc, REF_DOC)

    def test_console_root_prompt_prose_still_clears(self) -> None:
        """A ```console line naming a dotfile with no write verb still does not count."""
        doc = "# LSP enforcement checklist\n\n```console\n# see ~/.zshrc for details\n```\n"
        assert _blocking(doc, REF_DOC) == []
