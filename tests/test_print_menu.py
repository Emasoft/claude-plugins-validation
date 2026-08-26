"""Tests for the fixed/dynamic menu emitter (TRDD-ef3fc7d8).

`assemble_dynamic_spec` and `load_fixed_spec` are pure and exercised directly.
The CLI is exercised end-to-end against a REAL stub `menu_write.py` in a temp
CMS cache (no mocking of the bridge). The stub echoes the received spec to
`queue/last-spec.json` so we can assert exactly what print_menu queued.

Routing model (TRDD-4de479a0): numbers = dynamic positional list; letters =
fixed actions/nav. These tests pin: alphabetical numbering, the auto-appended
P/A/B/M/0 footer, extra_options placement, fixed-menu integer-prefix matching,
and `renumber: false` on every queued spec.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import print_menu  # noqa: E402
from print_menu import (  # noqa: E402
    MenuSystemUnavailable,
    assemble_dynamic_spec,
    load_fixed_spec,
)

# Real stub menu_write.py — reads the spec arg, echoes it to queue/last-spec.json,
# writes <ts>-<plugin>-<slug>.menu.md, prints the queue path. Real I/O, no mocking.
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


def _make_cms(cache_base: Path, version: str, body: str = _STUB_MENU_WRITE) -> Path:
    vdir = cache_base / version
    (vdir / "scripts").mkdir(parents=True, exist_ok=True)
    (vdir / "scripts" / "menu_write.py").write_text(body, encoding="utf-8")
    return vdir


def _received_spec(queue_path: Path) -> dict:
    return json.loads((queue_path.parent / "last-spec.json").read_text(encoding="utf-8"))


def _menu_spec(slug: str = "main", **extra: object) -> dict:
    spec = {
        "spec_version": 1,
        "mode": "menu",
        "plugin": "cpv",
        "slug": slug,
        "header": "Pick one",
        "rows": [{"key": "1", "action_id": "scan", "label": "Scan"}],
        "footer": "Type a key:",
    }
    spec.update(extra)
    return spec


def _write_menus(d: Path, files: dict[str, dict]) -> None:
    for name, spec in files.items():
        (d / name).write_text(json.dumps(spec), encoding="utf-8")


class _StringStdin:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload


# ─────────────────────────────────────────────────────────────────────────────
# assemble_dynamic_spec
# ─────────────────────────────────────────────────────────────────────────────


class TestAssembleDynamic:
    def test_sorts_numbers_then_appends_fixed_footer(self) -> None:
        """Entries sorted + numbered 1..N, then P/A/B/M/0."""
        spec = assemble_dynamic_spec(["~/b", "~/a", "~/c"])
        rows = spec["rows"]
        assert rows[0] == {"key": "1", "action_id": "~/a", "label": "~/a"}
        assert rows[1]["key"] == "2" and rows[1]["label"] == "~/b"
        assert rows[2]["label"] == "~/c"
        assert [r["key"] for r in rows[3:]] == ["P", "A", "B", "M", "0"]

    def test_sort_is_case_insensitive(self) -> None:
        spec = assemble_dynamic_spec(["Banana", "apple", "Cherry"])
        # entry rows only — exclude the fixed footer keys (note "0"/Exit is also a digit)
        labels = [r["label"] for r in spec["rows"] if r["key"] not in {"P", "A", "B", "M", "0"}]
        assert labels == ["apple", "Banana", "Cherry"]

    def test_object_entries_carry_action_id(self) -> None:
        spec = assemble_dynamic_spec([{"label": "Plugin X", "action_id": "plug-x"}])
        assert spec["rows"][0] == {"key": "1", "action_id": "plug-x", "label": "Plugin X"}

    def test_object_entry_action_id_defaults_to_label(self) -> None:
        spec = assemble_dynamic_spec([{"label": "Only Label"}])
        assert spec["rows"][0]["action_id"] == "Only Label"

    def test_empty_list_yields_only_fixed_rows(self) -> None:
        """No detected items → just type-a-path + nav (the 'nothing found' case)."""
        spec = assemble_dynamic_spec([])
        assert [r["key"] for r in spec["rows"]] == ["P", "A", "B", "M", "0"]

    def test_extra_options_sit_between_path_and_nav(self) -> None:
        spec = assemble_dynamic_spec(
            ["x"], extra_options=[{"key": "R", "action_id": "rescan", "label": "Rescan"}]
        )
        assert [r["key"] for r in spec["rows"]] == ["1", "P", "R", "A", "B", "M", "0"]

    def test_extra_option_key_is_uppercased(self) -> None:
        spec = assemble_dynamic_spec(["x"], extra_options=[{"key": "r", "label": "Rescan"}])
        assert any(r["key"] == "R" for r in spec["rows"])

    def test_extra_option_reserved_key_fails(self) -> None:
        with pytest.raises(ValueError, match="reserved"):
            assemble_dynamic_spec(["x"], extra_options=[{"key": "A", "label": "nope"}])

    def test_duplicate_extra_option_keys_fail(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            assemble_dynamic_spec(
                ["x"], extra_options=[{"key": "R", "label": "one"}, {"key": "R", "label": "two"}]
            )

    def test_header_footer_slug_override(self) -> None:
        spec = assemble_dynamic_spec(["x"], header="H", footer="F", slug="picks")
        assert spec["header"] == "H" and spec["footer"] == "F" and spec["slug"] == "picks"

    def test_default_plugin_and_mode(self) -> None:
        spec = assemble_dynamic_spec(["x"])
        assert spec["plugin"] == "claude-plugins-validation"
        assert spec["mode"] == "menu" and spec["spec_version"] == 1

    def test_does_not_set_renumber(self) -> None:
        """write_menu injects renumber:false; assemble must not pre-set it."""
        assert "renumber" not in assemble_dynamic_spec(["x"])

    def test_bad_entry_type_fails(self) -> None:
        with pytest.raises(ValueError):
            assemble_dynamic_spec([123])

    def test_empty_string_entry_fails(self) -> None:
        with pytest.raises(ValueError):
            assemble_dynamic_spec([""])


# ─────────────────────────────────────────────────────────────────────────────
# load_fixed_spec
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadFixed:
    def test_match_by_integer_prefix(self, tmp_path: Path) -> None:
        _write_menus(tmp_path, {"01-main.json": {"slug": "main"}, "06-validate.json": {"slug": "validate"}})
        assert load_fixed_spec(6, dir_override=str(tmp_path))["slug"] == "validate"

    def test_zero_pad_tolerant(self, tmp_path: Path) -> None:
        _write_menus(tmp_path, {"6-validate.json": {"slug": "validate"}})
        assert load_fixed_spec(6, dir_override=str(tmp_path))["slug"] == "validate"

    def test_missing_index_fails_fast(self, tmp_path: Path) -> None:
        _write_menus(tmp_path, {"01-main.json": {"slug": "main"}})
        with pytest.raises(ValueError, match="no fixed menu with index 9"):
            load_fixed_spec(9, dir_override=str(tmp_path))

    def test_ambiguous_index_fails_fast(self, tmp_path: Path) -> None:
        _write_menus(tmp_path, {"06-a.json": {"slug": "a"}, "6-b.json": {"slug": "b"}})
        with pytest.raises(ValueError, match="ambiguous"):
            load_fixed_spec(6, dir_override=str(tmp_path))

    def test_missing_dir_fails_fast(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            load_fixed_spec(1, dir_override=str(tmp_path / "nope"))

    def test_env_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_menus(tmp_path, {"03-x.json": {"slug": "x"}})
        monkeypatch.setenv("CPV_SKILL_MENUS_DIR", str(tmp_path))
        assert load_fixed_spec(3)["slug"] == "x"

    def test_no_env_no_dir_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CPV_SKILL_MENUS_DIR", raising=False)
        with pytest.raises(ValueError, match="no skill-menus dir"):
            load_fixed_spec(1)

    def test_ignores_non_numbered_files(self, tmp_path: Path) -> None:
        _write_menus(tmp_path, {"readme.json": {"slug": "doc"}, "02-real.json": {"slug": "real"}})
        assert load_fixed_spec(2, dir_override=str(tmp_path))["slug"] == "real"

    def test_dir_override_beats_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        envdir = tmp_path / "env"
        ovrdir = tmp_path / "ovr"
        envdir.mkdir()
        ovrdir.mkdir()
        _write_menus(envdir, {"01-a.json": {"slug": "from-env"}})
        _write_menus(ovrdir, {"01-a.json": {"slug": "from-override"}})
        monkeypatch.setenv("CPV_SKILL_MENUS_DIR", str(envdir))
        assert load_fixed_spec(1, dir_override=str(ovrdir))["slug"] == "from-override"


# ─────────────────────────────────────────────────────────────────────────────
# CLI (end-to-end against a real stub menu_write.py)
# ─────────────────────────────────────────────────────────────────────────────


class TestCli:
    def _cms(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        cache = tmp_path / "cache"
        _make_cms(cache, "0.1.5")
        monkeypatch.setattr(print_menu, "_default_cache_base", lambda: cache)
        return cache

    def test_fixed_queues_named_menu(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        menus = tmp_path / "menus"
        menus.mkdir()
        _write_menus(menus, {"06-validate.json": _menu_spec(slug="validate")})
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(["print_menu.py", "fixed", "6", "--dir", str(menus)])
        assert rc == 0
        sent = _received_spec(Path(out.getvalue().strip()))
        assert sent["slug"] == "validate"
        assert sent["renumber"] is False

    def test_dynamic_inline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(["print_menu.py", "dynamic", json.dumps(["~/b", "~/a"])])
        assert rc == 0
        sent = _received_spec(Path(out.getvalue().strip()))
        assert [r["key"] for r in sent["rows"]] == ["1", "2", "P", "A", "B", "M", "0"]
        assert sent["rows"][0]["label"] == "~/a"
        assert sent["renumber"] is False

    def test_dynamic_from_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        f = tmp_path / "dyn.json"
        f.write_text(
            json.dumps({"entries": ["z"], "extra_options": [{"key": "R", "label": "Rescan"}], "header": "H"}),
            encoding="utf-8",
        )
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(["print_menu.py", "dynamic", "--from-file", str(f)])
        assert rc == 0
        sent = _received_spec(Path(out.getvalue().strip()))
        assert sent["header"] == "H"
        assert [r["key"] for r in sent["rows"]] == ["1", "P", "R", "A", "B", "M", "0"]

    def test_slug_flag_beats_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A --slug flag overrides a file's slug (consistent flag-wins precedence)."""
        self._cms(tmp_path, monkeypatch)
        f = tmp_path / "dyn.json"
        f.write_text(json.dumps({"entries": ["x"], "slug": "fromfile"}), encoding="utf-8")
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(
            ["print_menu.py", "dynamic", "--from-file", str(f), "--slug", "fromflag"]
        )
        assert rc == 0
        assert _received_spec(Path(out.getvalue().strip()))["slug"] == "fromflag"

    def test_raw_stdin(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.stdin", _StringStdin(json.dumps(_menu_spec())))
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(["print_menu.py", "-"])
        assert rc == 0
        assert _received_spec(Path(out.getvalue().strip()))["slug"] == "main"

    def test_raw_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        sp = tmp_path / "spec.json"
        sp.write_text(json.dumps(_menu_spec()), encoding="utf-8")
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        rc = print_menu._cli(["print_menu.py", str(sp)])
        assert rc == 0
        assert Path(out.getvalue().strip()).is_file()

    def test_dynamic_bad_json_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._cms(tmp_path, monkeypatch)
        rc = print_menu._cli(["print_menu.py", "dynamic", "not-json"])
        assert rc == 2
        assert "dynamic" in capsys.readouterr().err

    def test_fixed_non_integer_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._cms(tmp_path, monkeypatch)
        rc = print_menu._cli(["print_menu.py", "fixed", "abc", "--dir", str(tmp_path)])
        assert rc == 2
        assert "integer" in capsys.readouterr().err

    def test_no_args_exits_2_with_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = print_menu._cli(["print_menu.py"])
        assert rc == 2
        assert "usage" in capsys.readouterr().err

    def test_cms_absent_exits_5(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(print_menu, "_default_cache_base", lambda: tmp_path / "absent")
        rc = print_menu._cli(["print_menu.py", "dynamic", json.dumps(["x"])])
        assert rc == 5

    def test_dynamic_inline_object_form(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._cms(tmp_path, monkeypatch)
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        payload = json.dumps({"entries": ["b", "a"], "slug": "targets", "footer": "go"})
        rc = print_menu._cli(["print_menu.py", "dynamic", payload])
        assert rc == 0
        sent = _received_spec(Path(out.getvalue().strip()))
        assert sent["slug"] == "targets" and sent["footer"] == "go"
        assert sent["rows"][0]["label"] == "a"

    def test_dynamic_non_list_entries_exits_2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._cms(tmp_path, monkeypatch)
        rc = print_menu._cli(["print_menu.py", "dynamic", json.dumps({"entries": "oops"})])
        assert rc == 2
        assert "entries" in capsys.readouterr().err


def test_real_cms_resolvable_or_skip() -> None:
    """If claude-menu-system is installed, the re-exported resolver finds it."""
    root = None
    try:
        root = print_menu.resolve_cms_root()
    except MenuSystemUnavailable:
        pytest.skip("claude-menu-system not installed in this environment")
    assert root is not None
    assert (root / "scripts" / "menu_write.py").is_file()
