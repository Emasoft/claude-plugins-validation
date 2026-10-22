"""Issue #257 item 1 — a home-path username is allowed in docs ONLY if it is a placeholder.

Two-sided: placeholder usernames (defined inside CPV) stop firing, and a
real-looking login in the same shape still fires. The scanned plugin cannot hide
a real private path by picking an allowlisted name such as `john` or `admin`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cpv_validation_common import (  # noqa: E402
    _NON_PERSONAL_LOGIN_NAMES,
    EXAMPLE_USERNAMES,
    ValidationReport,
    _get_private_usernames,
    scan_file_for_absolute_paths,
    scan_file_for_private_info,
)

PLACEHOLDER_PATHS = [
    "/Users/username/project/notes",
    "/home/user/project/notes",
    "/Users/yourname/project/notes",
    "/home/your-username/project/notes",
    "/Users/USERNAME/project/notes",
]

REAL_LOOKING_PATHS = [
    "/home/john/project/notes",
    "/Users/admin/project/notes",
    # /home/runner/ (CI home) is covered in test_issue_237_vendored_scope: the
    # absolute-path rule flags it, the private-info rule does not (task G 4k).
    "/Users/test/project/notes",
    "/Users/dev/project/notes",
    "/home/jane/project/notes",
    "/Users/demo/project/notes",
    "/home/mrossi/project/notes",
]


def _doc(tmp_path: Path, path_text: str) -> Path:
    doc = tmp_path / "README.md"
    doc.write_text(f"Example:\n\n```\n{path_text}\n```\n", encoding="utf-8")
    return doc


@pytest.mark.parametrize("path_text", PLACEHOLDER_PATHS)
def test_placeholder_username_in_doc_does_not_fire(tmp_path: Path, path_text: str) -> None:
    """A placeholder username in a fenced doc sample raises no absolute-path / private-path finding."""
    doc = _doc(tmp_path, path_text)
    assert scan_file_for_absolute_paths(doc, ValidationReport(), "README.md") == 0
    assert scan_file_for_private_info(doc, ValidationReport(), "README.md") == 0


@pytest.mark.parametrize("path_text", REAL_LOOKING_PATHS)
def test_real_looking_username_in_doc_still_fires(tmp_path: Path, path_text: str) -> None:
    """Same shape with a real-looking username is a finding in both rules (no allowlisted-name hiding)."""
    doc = _doc(tmp_path, path_text)
    assert scan_file_for_absolute_paths(doc, ValidationReport(), "README.md") >= 1
    assert scan_file_for_private_info(doc, ValidationReport(), "README.md") >= 1


def test_example_usernames_holds_only_placeholders() -> None:
    """The allowlist keeps no real-looking login (mutation guard on the set itself)."""
    for real in (
        "john",
        "jane",
        "admin",
        "runner",
        "dev",
        "developer",
        "test",
        "demo",
        "foo",
        "bar",
        "sample",
        "example",
        "root",
    ):
        assert real not in EXAMPLE_USERNAMES


# ── Task G item 2: a person login is registered as private ──────────────────


def _patch_login(monkeypatch: pytest.MonkeyPatch, login: str) -> None:
    import getpass

    monkeypatch.setattr(getpass, "getuser", lambda: login)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home") / login))
    for var in ("USER", "USERNAME", "LOGNAME"):
        monkeypatch.setenv(var, login)
    monkeypatch.delenv("CLAUDE_PRIVATE_USERNAMES", raising=False)


@pytest.mark.parametrize("login", ["john", "dev", "admin", "test"])
def test_person_login_is_registered_private(monkeypatch: pytest.MonkeyPatch, login: str) -> None:
    """A real-looking machine login is a private username (its home path is a CRITICAL leak)."""
    _patch_login(monkeypatch, login)
    assert login in _get_private_usernames()


@pytest.mark.parametrize("login", ["root", "runner"])
def test_system_login_is_not_registered_private(monkeypatch: pytest.MonkeyPatch, login: str) -> None:
    """Only `root` and `runner` (plus placeholders) are exempt from registration."""
    _patch_login(monkeypatch, login)
    assert login not in _get_private_usernames()
    assert _NON_PERSONAL_LOGIN_NAMES == EXAMPLE_USERNAMES | {"root", "runner"}


# ── Task G item 3: a dot is not regex syntax ─────────────────────────────────


def _script(tmp_path: Path, line: str) -> Path:
    path = tmp_path / "run.sh"
    path.write_text(line + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "line",
    ["cat /home/john/x/file.txt", "see /Users/john.smith/notes/a"],
)
def test_path_with_dot_is_flagged(tmp_path: Path, line: str) -> None:
    """A real home path containing a `.` fires in both scanners (former false negative)."""
    path = _script(tmp_path, line)
    assert scan_file_for_absolute_paths(path, ValidationReport(), "run.sh") >= 1
    assert scan_file_for_private_info(path, ValidationReport(), "run.sh") >= 1


@pytest.mark.parametrize(
    "line",
    [
        'PAT = r"/home/[^/]+/"',
        'x = "/home/.*/cache"',
        "echo /home/$USER/x/file.txt",
        "echo /home/${USER}/x/f.txt",
    ],
)
def test_regex_and_env_var_paths_not_flagged(tmp_path: Path, line: str) -> None:
    """Real regex syntax or an env-var username is still not a literal path."""
    path = _script(tmp_path, line)
    assert scan_file_for_absolute_paths(path, ValidationReport(), "run.sh") == 0
    assert scan_file_for_private_info(path, ValidationReport(), "run.sh") == 0
