"""Tests for the CPV → claude-menu-system bridge (TRDD-4de479a0).

`resolve_cms_root` is exercised against REAL temp cache directories (real
files, no mocking). `write_menu` is exercised against a REAL stub
`menu_write.py` written to a temp cache dir — a genuine executable that
implements the menu_write contract (reads the spec, echoes it back for
assertions, writes a `.menu.md`, prints the queue path). We deliberately do
NOT drive the real installed menu_write.py here: it resolves the live session
and would queue a spurious menu that the Stop hook emits to the user at this
turn's end. One skip-guarded check confirms the really-installed CMS is
resolvable.

Routing model (TRDD-4de479a0): CPV menus use fixed keys (`renumber: false`),
so the orchestrator routes the user's reply from its own skill-documented map.
The bridge therefore persists no action map; these tests assert the
`renumber: false` default is injected instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cpv_menu import (  # noqa: E402
    MenuSystemUnavailable,
    resolve_cms_root,
    write_menu,
)

# A real, minimal menu_write.py that honours the contract: read spec arg, echo
# it to queue/last-spec.json (so tests can assert what cpv_menu sent), write
# <ts>-<plugin>-<slug>.menu.md, print the queue path. Real I/O, no mocking.
_STUB_MENU_WRITE = '''\
import json, sys, time
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
qdir = Path(__file__).resolve().parent.parent / "queue"
qdir.mkdir(parents=True, exist_ok=True)
(qdir / "last-spec.json").write_text(json.dumps(spec), encoding="utf-8")
menu = qdir / f"{time.time_ns():020d}-{spec['plugin']}-{spec['slug']}.menu.md"
menu.write_text("RENDERED MENU\\n", encoding="utf-8")
print(menu)
'''

# A stub that always fails, to prove cpv_menu surfaces non-zero exits.
_STUB_FAILING = 'import sys\nprint("boom", file=sys.stderr)\nsys.exit(3)\n'


def _make_cms(cache_base: Path, version: str, body: str = _STUB_MENU_WRITE) -> Path:
    """Create a fake CMS version dir with a real stub menu_write.py."""
    vdir = cache_base / version
    (vdir / "scripts").mkdir(parents=True, exist_ok=True)
    (vdir / "scripts" / "menu_write.py").write_text(body, encoding="utf-8")
    return vdir


def _menu_spec(slug: str = "main", **extra: object) -> dict:
    spec = {
        "spec_version": 1,
        "mode": "menu",
        "plugin": "cpv",
        "slug": slug,
        "header": "Pick one",
        "rows": [
            {"key": "1", "action_id": "scan", "label": "Scan"},
            {"key": "2", "action_id": "validate", "label": "Validate"},
            {"key": "0", "action_id": "cancel", "label": "Cancel"},
        ],
        "footer": "Type a key:",
    }
    spec.update(extra)
    return spec


def _received_spec(queue_path: Path) -> dict:
    return json.loads((queue_path.parent / "last-spec.json").read_text(encoding="utf-8"))


# -----------------------------------------------------------------------------
# resolve_cms_root
# -----------------------------------------------------------------------------


class TestResolveCmsRoot:
    def test_picks_highest_numeric_version(self, tmp_path: Path) -> None:
        """0.1.10 must beat 0.1.5 and 0.1.9 (numeric, not lexicographic)."""
        for v in ("0.1.5", "0.1.9", "0.1.10", "0.1.3"):
            _make_cms(tmp_path, v)
        assert resolve_cms_root(cache_base=tmp_path).name == "0.1.10"

    def test_fails_fast_when_base_missing(self, tmp_path: Path) -> None:
        with pytest.raises(MenuSystemUnavailable) as exc:
            resolve_cms_root(cache_base=tmp_path / "nope")
        assert "not installed" in str(exc.value)
        assert "claude plugin install" in str(exc.value)

    def test_fails_fast_when_no_usable_version(self, tmp_path: Path) -> None:
        """A base dir with version folders that lack scripts/menu_write.py."""
        (tmp_path / "0.1.5").mkdir()
        with pytest.raises(MenuSystemUnavailable) as exc:
            resolve_cms_root(cache_base=tmp_path)
        assert "no version ships" in str(exc.value)

    def test_skips_incomplete_versions(self, tmp_path: Path) -> None:
        """An incomplete 0.2.0 (no menu_write.py) is ignored; 0.1.5 wins."""
        _make_cms(tmp_path, "0.1.5")
        (tmp_path / "0.2.0").mkdir()  # no scripts/menu_write.py
        assert resolve_cms_root(cache_base=tmp_path).name == "0.1.5"

    def test_non_numeric_version_does_not_crash(self, tmp_path: Path) -> None:
        _make_cms(tmp_path, "0.1.5")
        _make_cms(tmp_path, "main")  # non-numeric — lexicographic fallback bucket
        assert resolve_cms_root(cache_base=tmp_path).name in ("0.1.5", "main")


# -----------------------------------------------------------------------------
# write_menu (against a real stub menu_write.py)
# -----------------------------------------------------------------------------


class TestWriteMenu:
    def test_queues_menu_and_returns_path(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        queue_path = write_menu(_menu_spec(), cache_base=cache)
        assert queue_path.is_file()
        assert queue_path.name.endswith(".menu.md")

    def test_injects_renumber_false_by_default(self, tmp_path: Path) -> None:
        """Fixed-key convention: the bridge sends renumber: false unless overridden."""
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        queue_path = write_menu(_menu_spec(), cache_base=cache)
        sent = _received_spec(queue_path)
        assert sent["renumber"] is False

    def test_preserves_explicit_renumber(self, tmp_path: Path) -> None:
        """A caller that explicitly sets renumber: true is respected."""
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        queue_path = write_menu(_menu_spec(renumber=True), cache_base=cache)
        sent = _received_spec(queue_path)
        assert sent["renumber"] is True

    def test_does_not_mutate_caller_spec(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        spec = _menu_spec()
        assert "renumber" not in spec
        write_menu(spec, cache_base=cache)
        assert "renumber" not in spec, "write_menu must not mutate the caller's dict"

    def test_propagates_menu_write_failure(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5", body=_STUB_FAILING)
        with pytest.raises(RuntimeError) as exc:
            write_menu(_menu_spec(), cache_base=cache)
        assert "exit 3" in str(exc.value)

    def test_fails_fast_when_cms_absent(self, tmp_path: Path) -> None:
        with pytest.raises(MenuSystemUnavailable):
            write_menu(_menu_spec(), cache_base=tmp_path / "absent")


# -----------------------------------------------------------------------------
# Light real-CMS check (skip if not installed)
# -----------------------------------------------------------------------------


def test_real_cms_is_resolvable_or_skip() -> None:
    """If claude-menu-system is installed, resolve_cms_root finds a usable version."""
    root = None
    try:
        root = resolve_cms_root()
    except MenuSystemUnavailable:
        pytest.skip("claude-menu-system not installed in this environment")
    assert root is not None
    assert (root / "scripts" / "menu_write.py").is_file()


# -----------------------------------------------------------------------------
# CLI surface — stdin mode (v2.104.x) eliminates the Write-tool diff noise
# -----------------------------------------------------------------------------


class TestCliStdinMode:
    """Stdin mode (``cpv_menu.py -``) lets orchestrators emit menus via a
    single Bash heredoc — no Write tool, no Edit tool, no intermediate
    tempfile, hence no visible ``Write(/tmp/...)`` diff panel before the
    menu appears.

    This was added in v2.104.x after the user reported that the cat-heredoc
    + file-path two-step caused agents to reach for the Write tool instead
    of cat, polluting the transcript.
    """

    def test_stdin_mode_queues_menu(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`python cpv_menu.py -` reads spec from stdin and queues it."""
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")

        # Point the bridge at our fake CMS by patching the default base.
        import cpv_menu

        monkeypatch.setattr(cpv_menu, "_default_cache_base", lambda: cache)

        spec_json = json.dumps(_menu_spec())
        monkeypatch.setattr("sys.stdin", _StringStdin(spec_json))

        import io
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)

        rc = cpv_menu._cli(["cpv_menu.py", "-"])

        assert rc == 0
        queue_path = Path(out.getvalue().strip())
        assert queue_path.is_file()
        assert queue_path.name.endswith(".menu.md")

        # The spec the stub received matches what we sent on stdin
        # (with renumber: false injected by the bridge).
        sent = _received_spec(queue_path)
        assert sent["slug"] == "main"
        assert sent["renumber"] is False

    def test_stdin_mode_reports_json_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Malformed JSON on stdin exits 2 with a clear ``<stdin>`` label
        (not a tempfile path, since none was used)."""
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        import cpv_menu

        monkeypatch.setattr(cpv_menu, "_default_cache_base", lambda: cache)
        monkeypatch.setattr("sys.stdin", _StringStdin("not-json"))

        rc = cpv_menu._cli(["cpv_menu.py", "-"])
        err = capsys.readouterr().err

        assert rc == 2
        assert "<stdin>" in err

    def test_file_mode_still_works(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The file-path CLI mode remains supported (backwards-compat with
        callers that already write spec files for other reasons)."""
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")

        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(_menu_spec()), encoding="utf-8")

        import cpv_menu

        monkeypatch.setattr(cpv_menu, "_default_cache_base", lambda: cache)

        import io
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)

        rc = cpv_menu._cli(["cpv_menu.py", str(spec_path)])

        assert rc == 0
        assert Path(out.getvalue().strip()).is_file()


class _StringStdin:
    """Minimal stdin replacement for stdin-mode tests — only ``.read()`` is
    needed (matches what ``_cli`` calls)."""

    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload
