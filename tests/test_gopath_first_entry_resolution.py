"""w9-followups #5 — the generated `_secret_scan`'s ``go install`` GOBIN
fallback resolved the WHOLE ``GOPATH`` string as a single directory:

    _gobin = os.environ.get("GOBIN") or str(
        Path(os.environ.get("GOPATH") or (Path.home() / "go")) / "bin")

``GOPATH`` may be an ``os.pathsep``-separated LIST (``first:second`` on POSIX,
``first;second`` on Windows) — Go's own docs state ``go install`` writes to the
FIRST entry's ``bin``. So ``GOPATH="/a:/b"`` resolved a fabricated
``Path("/a:/b") / "bin"`` that never held the freshly installed binary, and the
subsequent ``shutil.which("trufflehog", path=_gobin)`` would silently miss it.

Fixed to split on ``os.pathsep`` and use the first non-empty entry, falling
back to ``~/go`` exactly as before when ``GOPATH`` is unset or empty.

This block lives inside the giant ``publish.py`` TEMPLATE string in
``generate_plugin_repo.py`` (not live code in that module), and is lifted
VERBATIM by ``standardize_plugin.migrate_publish_py_trufflehog_path`` (it
re-renders canon and slices between fixed anchors), so fixing it here reaches
both the freshly-scaffolded and the migrated shape with one edit — verified by
re-running the existing #231 migrator suite (unaffected: this block sits
strictly between the anchors it already slices) and by extracting the emitted
lines from the rendered template.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_plugin_repo as gen  # noqa: E402

_PARAMS = gen.PluginParams(
    name="w9-gopath-sample", description="x", author="A", author_email="a@a.a",
    github_owner="Emasoft",
)

_START = '                _gopath_raw = os.environ.get("GOPATH")'
_END = '                _trufflehog = shutil.which("trufflehog", path=_gobin)'


def _resolution_block() -> str:
    """The exact GOPATH-resolution lines, sliced out of the rendered canon."""
    canon = gen.gen_publish_py(_PARAMS)
    start = canon.index(_START)
    end = canon.index(_END, start)
    return canon[start:end]


def _resolve_gobin(monkeypatch: pytest.MonkeyPatch, *, gopath: str | None, gobin: str | None) -> str:
    """Execute the extracted block in isolation and return the resulting _gobin."""
    if gopath is None:
        monkeypatch.delenv("GOPATH", raising=False)
    else:
        monkeypatch.setenv("GOPATH", gopath)
    if gobin is None:
        monkeypatch.delenv("GOBIN", raising=False)
    else:
        monkeypatch.setenv("GOBIN", gobin)
    ns: dict[str, object] = {"os": os, "Path": Path}
    exec(textwrap.dedent(_resolution_block()), ns)  # noqa: S102 - our own generated template text
    return ns["_gobin"]  # type: ignore[return-value]


def test_block_present_and_uses_pathsep_split(tmp_path: Path) -> None:
    block = _resolution_block()
    assert "os.pathsep" in block
    assert 'os.environ.get("GOPATH")' in block


def test_gopath_with_two_entries_uses_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = "/tmp/w9-first", "/tmp/w9-second"
    gopath = first + os.pathsep + second
    resolved = _resolve_gobin(monkeypatch, gopath=gopath, gobin=None)
    assert resolved == str(Path(first) / "bin")
    assert second not in resolved


def test_gopath_single_entry_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _resolve_gobin(monkeypatch, gopath="/tmp/w9-only", gobin=None)
    assert resolved == str(Path("/tmp/w9-only") / "bin")


def test_gopath_unset_falls_back_to_home_go(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _resolve_gobin(monkeypatch, gopath=None, gobin=None)
    assert resolved == str(Path.home() / "go" / "bin")


def test_gopath_leading_pathsep_skips_the_empty_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GOPATH string starting with the separator (`:/a`) must not resolve to
    an empty-string directory — the first NON-EMPTY entry wins."""
    resolved = _resolve_gobin(monkeypatch, gopath=os.pathsep + "/tmp/w9-real", gobin=None)
    assert resolved == str(Path("/tmp/w9-real") / "bin")


def test_gobin_still_overrides_gopath_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    resolved = _resolve_gobin(
        monkeypatch, gopath="/tmp/w9-first" + os.pathsep + "/tmp/w9-second", gobin="/tmp/w9-explicit-gobin"
    )
    assert resolved == "/tmp/w9-explicit-gobin"


def test_migrator_lifts_the_fixed_block_verbatim(tmp_path: Path) -> None:
    """The standardize migrator (issue #231) re-renders canon and slices
    between fixed anchors — the GOPATH-first-entry fix sits strictly inside
    that span, so a migrated publish.py gains it automatically."""
    import standardize_plugin as sp

    canon = sp._canonical_publish_py()
    assert canon is not None
    block = sp._slice_between(canon, sp._TRUFFLEHOG_NEW_START, sp._TRUFFLEHOG_BLOCK_END)
    assert block is not None
    assert "os.pathsep" in block
    assert "_gopath_first" in block
