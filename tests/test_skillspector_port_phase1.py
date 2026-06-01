#!/usr/bin/env python3
"""Two-sided tests for the SkillSpector-port Phase 1 skillaudit rules
(TRDD-de582146): INSECURE_TLS + CREDENTIAL_DISCOVERY.

These two rules port the only genuinely-missing, FP-resistant ideas from the
NVIDIA SkillSpector static scanner (TM3 unsafe-TLS-defaults; E3/PE3 credential
discovery + a few infostealer IOCs), reimplemented under CPV's discipline:
re2-safe patterns + the per-language context classifiers + documentation-path
suppression that SkillSpector's raw-regex versions lack.

Every test is TWO-SIDED:
  * the real/malicious shape FIRES the rule at the declared severity ("high"), and
  * a benign sibling (the safe form, a doc-only path, a comment/prose mention)
    STAYS clean (suppressed or demoted-to-info, i.e. not surfaced to the user).

The benign side is the whole point: a rule that suppressed everything would pass
the malicious side alone. The benign assertions prove the discriminators are
precise, not blanket — the exact failure (6/6 FPs on CPV's own skills) that made
SkillSpector's implementation unusable.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cpv_skillaudit_native as sa  # noqa: E402


def _live_rule_ids(content: str, file_path: str) -> set[str]:
    """Rule IDs of findings that survive context classification (not suppressed,
    not demoted to info) — what a real scan surfaces to the user."""
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
# INSECURE_TLS — TLS/cert verification disabled in code (ported from TM3)
# ──────────────────────────────────────────────────────────────────────────


class TestInsecureTls:
    def test_requests_verify_false_in_code_fires(self) -> None:
        """requests.post(..., verify=False) in real Python code fires INSECURE_TLS at high."""
        src = "import requests\ndef push(url, data):\n    requests.post(url, json=data, verify=False)\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "scripts/uploader.py")
        assert _severity_of(src, "scripts/uploader.py", "INSECURE_TLS") == "high"

    def test_ssl_cert_none_in_code_fires(self) -> None:
        """ssl.CERT_NONE assignment in real Python code fires INSECURE_TLS."""
        src = "import ssl\nctx = ssl.create_default_context()\nctx.verify_mode = ssl.CERT_NONE\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "scripts/conn.py")

    def test_ssl_unverified_context_fires(self) -> None:
        """ssl._create_unverified_context() fires INSECURE_TLS."""
        src = "import ssl\nctx = ssl._create_unverified_context()\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "scripts/conn.py")

    def test_node_reject_unauthorized_false_fires(self) -> None:
        """Node rejectUnauthorized: false in real TS code fires INSECURE_TLS."""
        src = "import https from 'https';\nconst agent = new https.Agent({ rejectUnauthorized: false });\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "src/client.ts")

    def test_curl_insecure_flag_fires(self) -> None:
        """curl -k in a shell hook fires INSECURE_TLS."""
        src = "#!/bin/bash\ncurl -k https://internal.example/api -o out.json\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "hooks/fetch.sh")

    def test_wget_no_check_certificate_fires(self) -> None:
        """wget --no-check-certificate fires INSECURE_TLS."""
        src = "#!/bin/bash\nwget --no-check-certificate https://x.example/pkg.tgz\n"
        assert "INSECURE_TLS" in _live_rule_ids(src, "hooks/get.sh")

    # ---- benign side ----

    def test_verify_true_never_fires(self) -> None:
        """The SAFE form (verify=True, the default) must NOT fire."""
        src = "import requests\nrequests.post(url, json=data, verify=True)\n"
        assert "INSECURE_TLS" not in _live_rule_ids(src, "scripts/uploader.py")

    def test_verify_false_in_doc_only_path_suppressed(self) -> None:
        """`verify=False` inside a genuinely doc-only path (docs/) is suppressed entirely."""
        src = (
            "# Security notes\n\n"
            "Never call `requests.post(url, verify=False)` — it disables TLS.\n"
            "```python\nrequests.post(url, verify=False)  # WRONG\n```\n"
        )
        assert "INSECURE_TLS" not in _live_rule_ids(src, "docs/security-notes.md")

    def test_verify_false_in_references_demotes_not_blocking(self) -> None:
        """references/*.md is NOT doc-only by design (a SKILL.md pointer can make
        it agent-reachable — the bypass-fix at cpv_skillaudit_native.py:1023). So a
        `verify=False` mention there stays visible but DEMOTES to NIT (non-blocking
        'low'), never the blocking 'high' that would be a false positive."""
        src = "# Security notes\n\nNever call `requests.post(url, verify=False)` — it disables TLS.\n"
        sev = _severity_of(src, "skills/x/references/security-notes.md", "INSECURE_TLS")
        assert sev != "high", f"references/ doc mention must not block; got {sev!r}"

    def test_verify_false_in_python_comment_not_live(self) -> None:
        """A `verify=False` mention inside a Python comment demotes (not surfaced live)."""
        src = (
            "import requests\n"
            "# Do NOT pass verify=False here — keep TLS verification on.\n"
            "requests.post(url, json=data, verify=True)\n"
        )
        assert "INSECURE_TLS" not in _live_rule_ids(src, "scripts/safe.py")


# ──────────────────────────────────────────────────────────────────────────
# CREDENTIAL_DISCOVERY — filesystem credential search/enumeration (E3/PE3)
# ──────────────────────────────────────────────────────────────────────────


class TestCredentialDiscovery:
    def test_find_suid_binaries_fires(self) -> None:
        """find / -perm -4000 (SUID hunting) fires CREDENTIAL_DISCOVERY at high."""
        src = "#!/bin/bash\nfind / -perm -4000 -type f 2>/dev/null\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "hooks/recon.sh")
        assert _severity_of(src, "hooks/recon.sh", "CREDENTIAL_DISCOVERY") == "high"

    def test_find_pem_keys_fires(self) -> None:
        """find ~ -name '*.pem' fires CREDENTIAL_DISCOVERY."""
        src = "#!/bin/bash\nfind ~ -name '*.pem' -o -name '*.key'\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "hooks/recon.sh")

    def test_oswalk_home_fires(self) -> None:
        """os.walk over the home directory fires CREDENTIAL_DISCOVERY."""
        src = "import os\nfor root, dirs, files in os.walk(os.path.expanduser('~')):\n    pass\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "scripts/scan.py")

    def test_ls_ssh_dir_fires(self) -> None:
        """ls ~/.ssh/ (enumerating a credential dir) fires CREDENTIAL_DISCOVERY."""
        src = "#!/bin/bash\nls ~/.ssh/ && cat ~/.ssh/id_rsa\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "hooks/x.sh")

    def test_wallet_dat_ioc_fires(self) -> None:
        """A reference to wallet.dat (crypto-wallet theft) fires CREDENTIAL_DISCOVERY."""
        src = "import shutil\nshutil.copy('/home/user/.bitcoin/wallet.dat', '/tmp/x')\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "scripts/grab.py")

    def test_ntds_dit_ioc_fires(self) -> None:
        """A reference to ntds.dit (AD database dump) fires CREDENTIAL_DISCOVERY."""
        src = "#!/bin/bash\ncp /windows/ntds/ntds.dit /tmp/loot\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "hooks/x.sh")

    def test_git_credentials_ioc_fires(self) -> None:
        """A reference to ~/.git-credentials fires CREDENTIAL_DISCOVERY."""
        src = "import shutil\nshutil.copy('/root/.git-credentials', '/tmp/g')\n"
        assert "CREDENTIAL_DISCOVERY" in _live_rule_ids(src, "scripts/grab.py")

    # ---- benign side ----

    def test_benign_find_py_files_not_fired(self) -> None:
        """find . -name '*.py' (a normal dev task) must NOT fire CREDENTIAL_DISCOVERY."""
        src = "#!/bin/bash\nfind . -name '*.py' -not -path './.venv/*'\n"
        assert "CREDENTIAL_DISCOVERY" not in _live_rule_ids(src, "hooks/lint.sh")

    def test_benign_oswalk_project_dir_not_fired(self) -> None:
        """os.walk over a project-relative dir (not home) must NOT fire."""
        src = "import os\nfor root, dirs, files in os.walk('./src'):\n    pass\n"
        assert "CREDENTIAL_DISCOVERY" not in _live_rule_ids(src, "scripts/build.py")

    def test_ioc_in_doc_only_path_suppressed(self) -> None:
        """wallet.dat named in a genuinely doc-only threat-doc (docs/) is suppressed."""
        src = "# Threat catalogue\n\nInfostealers target `wallet.dat`, `ntds.dit`, and Firefox `key4.db`.\n"
        assert "CREDENTIAL_DISCOVERY" not in _live_rule_ids(src, "docs/threats.md")

    def test_ioc_in_references_demotes_not_blocking(self) -> None:
        """references/ is NOT doc-only by design (agent-reachable via a SKILL.md
        pointer). A threat-IOC mention there stays visible but demotes to NIT
        (non-blocking), never the blocking 'high'."""
        src = "# Threat catalogue\n\nInfostealers target `wallet.dat`, `ntds.dit`, and Firefox `key4.db`.\n"
        sev = _severity_of(src, "skills/x/references/threats.md", "CREDENTIAL_DISCOVERY")
        assert sev != "high", f"references/ doc mention must not block; got {sev!r}"
