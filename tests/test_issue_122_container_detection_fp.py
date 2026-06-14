"""Regression tests for issue #122 — CONTAINER_ESCAPE on container DETECTION.

The CONTAINER_ESCAPE catalog rule matches three init-process /proc paths in
one alternation: ``/proc/(?:1|self)/(?:root|ns|cgroup)``. Two are genuine
breakout primitives — ``root`` traverses the host filesystem through PID 1's
mount namespace, ``ns`` are the namespace fds used with ``setns`` — but
``cgroup`` is READ-ONLY and is the canonical way runtimes /
``systemd-detect-virt`` / ``is-container`` IDENTIFY the runtime ("does PID 1's
cgroup name ``docker`` / ``kubepods``?"). Flagging a bare
``/proc/<1|self>/cgroup`` read CRITICAL is a false positive on diagnostic /
environment-report tooling (issue #122).

The fix is a language-agnostic discriminator
(``_is_benign_cgroup_detection_read``) that suppresses CONTAINER_ESCAPE ONLY
when (a) the match is the ``cgroup`` member and (b) NO corroborating escape
primitive appears anywhere in the file.

Every assertion is TWO-SIDED — the FP clears AND the real-threat sibling of the
SAME rule still fires:

* ``/proc/<1|self>/cgroup`` detection read (``.py`` / ``.sh``) → SUPPRESSED.
* ``/proc/<1|self>/root`` / ``/proc/<1|self>/ns`` → a different member → FIRES.
* a ``cgroup`` read in a file that also mounts a cgroup / writes a
  ``release_agent`` / uses ``nsenter`` / ``unshare`` / the docker socket →
  corroborated → FIRES.
* every other CONTAINER_ESCAPE primitive (docker socket, ``/dev/mem``,
  ``modprobe`` …) is untouched → FIRES.
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
    _is_benign_cgroup_detection_read,
    scan_content,
)


@pytest.fixture(autouse=True)
def _no_scan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the skillaudit content cache so every scan runs fresh.

    The v2.104.0 cache keys on (content_hash, catalog_hash, version, ext) —
    NOT the classifier code — so without this a same-version classifier change
    would be masked by a cache hit.
    """
    monkeypatch.setenv("CPV_SCAN_CACHE", "0")


def _escape_hits(content: str, file_path: str) -> list[dict]:
    """ACTIONABLE CONTAINER_ESCAPE findings (suppressed dropped).

    Mirrors the filter ``run_skillaudit_scan`` applies before findings reach
    the publish gate: a suppressed finding is gone; a live finding remains.
    """
    return [
        f
        for f in scan_content(content, file_path)
        if f.get("ruleId") == "CONTAINER_ESCAPE" and not f.get("suppressed")
    ]


# ── The issue #122 repro: a /janitor-identify-environment-style probe ──────────
_DETECT_PY = (
    "def detect_sandboxing():\n"
    "    signals = []\n"
    '    if Path("/.dockerenv").exists():\n'
    '        signals.append("docker")\n'
    "    # container DETECTION (not escape): the init cgroup names the runtime\n"
    '    cg = Path("/proc/1/cgroup").read_text(encoding="utf-8")\n'
    '    if "docker" in cg: signals.append("docker (cgroup)")\n'
    '    elif "kubepods" in cg: signals.append("kubernetes (cgroup)")\n'
    "    return signals\n"
)

_DETECT_SELF_PY = (
    'cg = open("/proc/self/cgroup").read()\n'
    'if "kubepods" in cg:\n'
    '    label = "k8s"\n'
)

_DETECT_SH = (
    "#!/bin/bash\n"
    "# report whether we run inside a container\n"
    "if grep -q docker /proc/1/cgroup; then echo container; fi\n"
)


# ── FN side: genuine breakout primitives that MUST keep firing ────────────────
_ROOT_TRAVERSAL_PY = 'host = open("/proc/1/root/etc/shadow").read()\n'
_NS_SETNS_PY = 'fd = open("/proc/self/ns/mnt")\n'

# The cgroup release_agent escape family (CVE-2022-0492): read the cgroup to
# locate the host path, mount a cgroup, write release_agent.
_RELEASE_AGENT_ESCAPE_PY = (
    'cg = open("/proc/self/cgroup").read()\n'
    'os.system("mount -t cgroup -o rdma cgroup /tmp/c")\n'
    'open("/tmp/c/release_agent", "w").write("/cmd")\n'
)
_CGROUP_PLUS_NSENTER_PY = (
    'cg = open("/proc/1/cgroup").read()\n'
    'os.system("nsenter --target 1 --mount --uts --ipc --net --pid sh")\n'
)
_CGROUP_PLUS_UNSHARE_PY = (
    'cg = open("/proc/self/cgroup").read()\n'
    'os.system("unshare -Urm /bin/sh")\n'
)
_DOCKER_SOCK_PY = (
    'requests.get("http://localhost/info", unix_socket="/var/run/docker.sock")\n'
)


# ───────────────────────────── integration (real scanner) ────────────────────


def test_detect_cgroup_read_py_is_suppressed() -> None:
    """The issue #122 repro: a /proc/1/cgroup detection read → 0 CONTAINER_ESCAPE."""
    assert _escape_hits(_DETECT_PY, "scripts/identify_environment.py") == []


def test_detect_self_cgroup_read_py_is_suppressed() -> None:
    """A /proc/self/cgroup detection read → suppressed."""
    assert _escape_hits(_DETECT_SELF_PY, "scripts/env.py") == []


def test_detect_cgroup_read_shell_is_suppressed() -> None:
    """`grep -q docker /proc/1/cgroup` in a shell probe → suppressed."""
    assert _escape_hits(_DETECT_SH, "scripts/detect.sh") == []


def test_proc_root_traversal_still_fires() -> None:
    """/proc/1/root host-FS traversal is a DIFFERENT member → still CRITICAL."""
    assert len(_escape_hits(_ROOT_TRAVERSAL_PY, "scripts/x.py")) >= 1


def test_proc_ns_setns_still_fires() -> None:
    """/proc/self/ns namespace fds (setns) → still CRITICAL."""
    assert len(_escape_hits(_NS_SETNS_PY, "scripts/x.py")) >= 1


def test_release_agent_escape_still_fires() -> None:
    """A cgroup read alongside a cgroup mount + release_agent write → corroborated → fires."""
    assert len(_escape_hits(_RELEASE_AGENT_ESCAPE_PY, "scripts/x.py")) >= 1


def test_cgroup_plus_nsenter_still_fires() -> None:
    """A cgroup read alongside nsenter → corroborated → fires."""
    assert len(_escape_hits(_CGROUP_PLUS_NSENTER_PY, "scripts/x.py")) >= 1


def test_cgroup_plus_unshare_still_fires() -> None:
    """A cgroup read alongside unshare → corroborated → fires."""
    assert len(_escape_hits(_CGROUP_PLUS_UNSHARE_PY, "scripts/x.py")) >= 1


def test_docker_socket_still_fires() -> None:
    """An unrelated CONTAINER_ESCAPE primitive (docker socket) is untouched → fires."""
    assert len(_escape_hits(_DOCKER_SOCK_PY, "scripts/x.py")) >= 1


# ───────────────────────────── unit (the discriminator) ──────────────────────


def test_helper_bare_cgroup_is_benign() -> None:
    """A bare /proc/<1|self>/cgroup match with a clean file → benign (suppress)."""
    assert _is_benign_cgroup_detection_read("/proc/1/cgroup", _DETECT_PY) is True
    assert _is_benign_cgroup_detection_read("/proc/self/cgroup", _DETECT_SELF_PY) is True


def test_helper_root_member_is_not_benign() -> None:
    """A /proc/<1|self>/root or /proc/<1|self>/ns match is never benign."""
    assert _is_benign_cgroup_detection_read("/proc/1/root", _ROOT_TRAVERSAL_PY) is False
    assert _is_benign_cgroup_detection_read("/proc/self/ns", _NS_SETNS_PY) is False


def test_helper_corroborated_cgroup_is_not_benign() -> None:
    """A cgroup match in a file carrying any escape primitive is not benign."""
    assert _is_benign_cgroup_detection_read("/proc/self/cgroup", _RELEASE_AGENT_ESCAPE_PY) is False
    assert _is_benign_cgroup_detection_read("/proc/1/cgroup", _CGROUP_PLUS_NSENTER_PY) is False
    assert _is_benign_cgroup_detection_read("/proc/self/cgroup", _CGROUP_PLUS_UNSHARE_PY) is False


def test_helper_non_cgroup_match_is_not_benign() -> None:
    """A match that is not a /proc/<1|self>/cgroup path is never benign."""
    assert _is_benign_cgroup_detection_read("/var/run/docker.sock", _DOCKER_SOCK_PY) is False
    assert _is_benign_cgroup_detection_read("nsenter", "nsenter --target 1 sh\n") is False
