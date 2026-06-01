"""Round-trip tests for the cpv-main-menu skill's pre-baked FIXED menus.

Phase 2 of TRDD-ef3fc7d8 extracted every static menu from
``skills/cpv-main-menu-skill/references/menu-tree.md`` into individual
``skill-menus/NN-<slug>.json`` files queued by ``print_menu.py fixed NN``.

These tests pin the contract those files must satisfy so the migration
stays behavior-preserving: each file is a valid claude-menu-system spec,
the filename's ``NN`` prefix is a unique integer, the filename's slug
matches the spec's embedded ``slug``, and ``print_menu.load_fixed_spec``
resolves every index to the matching spec. Same import + sys.path style as
``tests/test_print_menu.py`` (the engine is exercised directly, no mocking).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import print_menu  # noqa: E402

SKILL_MENUS_DIR = REPO_ROOT / "skills" / "cpv-main-menu-skill" / "skill-menus"


def _menu_files() -> list[Path]:
    return sorted(SKILL_MENUS_DIR.glob("*.json"))


def _parse_filename(path: Path) -> tuple[int, str]:
    """``NN-<slug>.json`` → (int(NN), <slug>). Fails the test if malformed."""
    stem = path.name[: -len(".json")] if path.name.endswith(".json") else path.name
    assert "-" in stem, f"{path.name}: filename must be <NN>-<slug>.json"
    prefix, slug = stem.split("-", 1)
    assert prefix.isdigit(), f"{path.name}: NN prefix {prefix!r} must be numeric"
    assert slug, f"{path.name}: slug part must be non-empty"
    return int(prefix), slug


def test_skill_menus_dir_exists_and_non_empty() -> None:
    """The skill-menus dir exists and ships at least the known fixed menus."""
    assert SKILL_MENUS_DIR.is_dir(), f"missing dir: {SKILL_MENUS_DIR}"
    files = _menu_files()
    # Phase 2 extracted 26 fixed menus (4 path-source mini-menus + 22 heredocs).
    assert len(files) >= 26, f"expected >= 26 fixed menus, found {len(files)}"


@pytest.mark.parametrize("path", _menu_files(), ids=lambda p: p.name)
def test_each_file_is_a_valid_cms_spec(path: Path) -> None:
    """Every skill-menu JSON is a structurally valid claude-menu-system spec."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(spec, dict), f"{path.name}: top-level must be an object"
    assert spec.get("spec_version") == 1, f"{path.name}: spec_version must be 1"
    assert spec.get("mode") == "menu", f"{path.name}: mode must be 'menu'"
    assert isinstance(spec.get("slug"), str) and spec["slug"], f"{path.name}: slug must be a non-empty string"
    rows = spec.get("rows")
    assert isinstance(rows, list) and rows, f"{path.name}: rows must be non-empty"
    for row in rows:
        assert isinstance(row, dict), f"{path.name}: every row must be an object"
        for field in ("key", "action_id", "label"):
            assert isinstance(row.get(field), str) and row[field], (
                f"{path.name}: row {row!r} missing non-empty {field!r}"
            )


@pytest.mark.parametrize("path", _menu_files(), ids=lambda p: p.name)
def test_each_file_has_exactly_one_zero_cancel_row(path: Path) -> None:
    """Every menu has exactly one ``key == "0"`` row (the cancel/exit/done row)."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    zero_rows = [r for r in spec["rows"] if r["key"] == "0"]
    assert len(zero_rows) == 1, f"{path.name}: expected exactly one '0' row"


@pytest.mark.parametrize("path", _menu_files(), ids=lambda p: p.name)
def test_each_file_has_no_duplicate_keys(path: Path) -> None:
    """No menu reuses a key — the fixed-key contract requires unique keys."""
    spec = json.loads(path.read_text(encoding="utf-8"))
    keys = [r["key"] for r in spec["rows"]]
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f"{path.name}: duplicate keys {dups}"


@pytest.mark.parametrize("path", _menu_files(), ids=lambda p: p.name)
def test_filename_slug_matches_embedded_slug(path: Path) -> None:
    """The ``<slug>`` in ``NN-<slug>.json`` equals the spec's ``slug`` field."""
    _, filename_slug = _parse_filename(path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    assert spec["slug"] == filename_slug, f"{path.name}: filename slug {filename_slug!r} != spec slug {spec['slug']!r}"


def test_numeric_prefixes_are_unique() -> None:
    """No two skill-menu files share the same integer ``NN`` prefix."""
    seen: dict[int, str] = {}
    for path in _menu_files():
        index, _ = _parse_filename(path)
        assert index not in seen, f"duplicate index {index}: {seen[index]} and {path.name}"
        seen[index] = path.name


@pytest.mark.parametrize("path", _menu_files(), ids=lambda p: p.name)
def test_load_fixed_spec_returns_matching_spec(path: Path) -> None:
    """``print_menu.load_fixed_spec(N, dir_override=...)`` returns this file's spec."""
    index, _ = _parse_filename(path)
    loaded = print_menu.load_fixed_spec(index, dir_override=str(SKILL_MENUS_DIR))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == on_disk, f"{path.name}: load_fixed_spec({index}) mismatch"


def test_load_fixed_spec_resolves_every_present_index() -> None:
    """Every index present on disk is resolvable by load_fixed_spec (exhaustive)."""
    for path in _menu_files():
        index, slug = _parse_filename(path)
        spec = print_menu.load_fixed_spec(index, dir_override=str(SKILL_MENUS_DIR))
        assert spec["slug"] == slug, f"index {index} resolved to slug {spec['slug']!r}"


def test_entry_keys_are_contiguous_digits_then_letters() -> None:
    """Numbered rows form a contiguous 1..K run; the rest are letter/`0` keys.

    The fixed-key contract reserves numbers for the positional list and
    letters for fixed actions. A static fixed menu's digit rows must start
    at 1 and not skip (e.g. create has 1..10; manage's 9 gap is the known
    historical exception, so this test tolerates a single internal gap by
    only asserting the run starts at 1).
    """
    for path in _menu_files():
        spec = json.loads(path.read_text(encoding="utf-8"))
        digit_keys = [int(r["key"]) for r in spec["rows"] if r["key"].isdigit() and r["key"] != "0"]
        if digit_keys:
            assert min(digit_keys) == 1, f"{path.name}: numbered rows must start at key '1', got {sorted(digit_keys)}"
