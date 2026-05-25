"""Tests for scripts/migrate_marketplace.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate_marketplace as m  # noqa: E402

# ── parse_github_url ─────────────────────────────────────────────────────────


def test_parse_github_url_https():
    assert m.parse_github_url("https://github.com/Emasoft/foo") == ("Emasoft", "foo")


def test_parse_github_url_https_with_git():
    assert m.parse_github_url("https://github.com/Emasoft/foo.git") == ("Emasoft", "foo")


def test_parse_github_url_https_trailing_slash():
    assert m.parse_github_url("https://github.com/Emasoft/foo/") == ("Emasoft", "foo")


def test_parse_github_url_ssh():
    assert m.parse_github_url("git@github.com:Emasoft/foo.git") == ("Emasoft", "foo")


def test_parse_github_url_non_github():
    assert m.parse_github_url("https://gitlab.com/x/y") is None


def test_parse_github_url_empty():
    assert m.parse_github_url("") is None


# ── normalize_source ─────────────────────────────────────────────────────────


def test_normalize_source_url_to_repo():
    src = {"url": "https://github.com/Emasoft/my-plugin"}
    new, desc = m.normalize_source(src)
    assert new == {"type": "github", "repo": "Emasoft/my-plugin"}
    assert desc is not None and "url" in desc


def test_normalize_source_already_canonical():
    src = {"type": "github", "repo": "Emasoft/foo"}
    new, desc = m.normalize_source(src)
    assert desc is None
    assert new == src


def test_normalize_source_string_form():
    src = "https://github.com/Emasoft/foo"
    new, desc = m.normalize_source(src)
    assert new == {"type": "github", "repo": "Emasoft/foo"}
    assert desc is not None


def test_normalize_source_preserves_extra_fields():
    src = {"url": "https://github.com/Emasoft/foo", "ref": "v1.0"}
    new, _ = m.normalize_source(src)
    assert new == {"type": "github", "repo": "Emasoft/foo", "ref": "v1.0"}


def test_normalize_source_relative_path_untouched():
    src = {"source": "relative-path", "path": "./plugins/x"}
    new, desc = m.normalize_source(src)
    assert desc is None
    assert new == src


# ── migrate_marketplace integration (no probe) ───────────────────────────────


def _make_marketplace(tmp_path: Path, plugins: list[dict]) -> Path:
    root = tmp_path / "mkt"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "mkt", "plugins": plugins}, indent=2),
        encoding="utf-8",
    )
    return root


def test_migrate_no_changes(tmp_path):
    root = _make_marketplace(
        tmp_path,
        [
            {"name": "p1", "source": {"type": "github", "repo": "Emasoft/p1"}},
        ],
    )
    rc = m.migrate_marketplace(root, check_only=False, probe=False)
    assert rc == 0


def test_migrate_applies_url_to_repo(tmp_path):
    root = _make_marketplace(
        tmp_path,
        [
            {"name": "p1", "source": {"url": "https://github.com/Emasoft/p1"}},
        ],
    )
    rc = m.migrate_marketplace(root, check_only=False, probe=False)
    assert rc == 0
    data = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    assert data["plugins"][0]["source"] == {"type": "github", "repo": "Emasoft/p1"}


def test_migrate_check_mode_returns_1_on_drift(tmp_path):
    root = _make_marketplace(
        tmp_path,
        [
            {"name": "p1", "source": {"url": "https://github.com/Emasoft/p1"}},
        ],
    )
    rc = m.migrate_marketplace(root, check_only=True, probe=False)
    assert rc == 1
    # File should NOT have been modified in check mode.
    text = (root / ".claude-plugin" / "marketplace.json").read_text()
    assert "url" in text  # still original


def test_migrate_check_mode_returns_0_when_clean(tmp_path):
    root = _make_marketplace(
        tmp_path,
        [
            {"name": "p1", "source": {"type": "github", "repo": "Emasoft/p1"}},
        ],
    )
    rc = m.migrate_marketplace(root, check_only=True, probe=False)
    assert rc == 0


def test_migrate_handles_missing_marketplace_json(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    rc = m.migrate_marketplace(root, check_only=False, probe=False)
    assert rc == 1


def test_migrate_handles_invalid_json(tmp_path):
    root = tmp_path / "broken"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "marketplace.json").write_text(
        "{ not json",
        encoding="utf-8",
    )
    rc = m.migrate_marketplace(root, check_only=False, probe=False)
    assert rc == 1


def test_migrate_atomic_write(tmp_path):
    """The .tmp intermediate file should not exist after a successful write."""
    root = _make_marketplace(
        tmp_path,
        [
            {"name": "p1", "source": {"url": "https://github.com/Emasoft/p1"}},
        ],
    )
    rc = m.migrate_marketplace(root, check_only=False, probe=False)
    assert rc == 0
    # No .tmp staging file should remain (per-process-unique name — assert on
    # the suffix, not a fixed name).
    cp = root / ".claude-plugin"
    assert not any(p.name.endswith(".tmp") for p in cp.iterdir())
