#!/usr/bin/env python3
"""Issue #128-A regression — the upgrade path must be PROFILE-AWARE.

Bug: ``standardize --fix --force-templates`` (the `/cpv-upgrade-plugin`
engine, ``fix_missing_files``) regenerated ``scripts/publish.py`` by calling
``gen_publish_py(params)`` with NO profile, so a submodule-build plugin (PSS
shape, #128) had its submodule-aware publish.py CLOBBERED with the standard
one — breaking its releases. That is exactly the breakage #128 part (A)
reported.

Fix (TRDD-e9f13df1, Piece D): ``fix_missing_files`` resolves the plugin's
pipeline profile via ``resolve_pipeline_profile(plugin_path)`` and, for a
profile-parameterized ``gen_*`` (currently ``gen_publish_py``), passes the
resolved profile. SELECTOR not suppressor: a submodule-build plugin gets the
submodule-aware variant; a standard plugin resolves to ``standard`` and gets
the byte-identical standard output.

Two-sided: the by-design submodule-build variant is preserved AND a standard
plugin is unaffected (no submodule markers leak into a standard publish.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from generate_plugin_repo import PluginParams, gen_publish_py  # noqa: E402
from standardize_plugin import fix_missing_files  # noqa: E402

# The marker the submodule-build variant carries (the #128 source-change fix
# runs `git -C <submodule> diff`). It must appear in a submodule-build
# publish.py and be ABSENT from a standard one.
SUBMODULE_MARKER = "git -C"


def _write_plugin_json(plugin_path: Path, name: str = "test-plugin") -> None:
    cp = plugin_path / ".claude-plugin"
    cp.mkdir(parents=True, exist_ok=True)
    (cp / "plugin.json").write_text(
        '{"name": "%s", "version": "0.1.0", "description": "t",\n'
        ' "author": {"name": "X", "email": "x@y"}}\n' % name,
        encoding="utf-8",
    )


def _make_submodule_build(plugin_path: Path) -> None:
    """Give the tree the submodule-build SHAPE: a build-source submodule + bin/."""
    _write_plugin_json(plugin_path)
    (plugin_path / ".gitmodules").write_text(
        '[submodule "rust"]\n\tpath = rust\n\turl = https://example.invalid/rust.git\n',
        encoding="utf-8",
    )
    bin_dir = plugin_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "sampletool").write_text("#!/bin/sh\necho prebuilt\n", encoding="utf-8")


def test_force_templates_preserves_submodule_aware_publish(tmp_path):
    """A submodule-build plugin's regenerated publish.py is the submodule variant."""
    _make_submodule_build(tmp_path)
    fix_missing_files(tmp_path, results=[], force_templates=True)
    pub = (tmp_path / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert SUBMODULE_MARKER in pub, (
        "upgrade clobbered the submodule-build plugin with the STANDARD publish.py "
        "(the #128 breakage) — the submodule-aware variant must be regenerated"
    )


def test_force_templates_standard_plugin_gets_standard_publish(tmp_path):
    """A standard plugin's regenerated publish.py carries no submodule markers."""
    _write_plugin_json(tmp_path)  # no .gitmodules, no bin/ => standard profile
    fix_missing_files(tmp_path, results=[], force_templates=True)
    pub = (tmp_path / "scripts" / "publish.py").read_text(encoding="utf-8")
    assert SUBMODULE_MARKER not in pub, (
        "a standard plugin must get the standard publish.py — no submodule-build "
        "section may leak into it (selector, not blanket application)"
    )


def test_standard_regen_is_byte_identical_to_standard_variant(tmp_path):
    """The upgrade path's standard output == gen_publish_py(params, 'standard')."""
    _write_plugin_json(tmp_path)
    fix_missing_files(tmp_path, results=[], force_templates=True)
    regenerated = (tmp_path / "scripts" / "publish.py").read_text(encoding="utf-8")
    # Build the same params the upgrade path derives from the manifest and
    # confirm the regenerated standard publish.py is the canonical standard body.
    p = PluginParams(  # type: ignore[call-arg]
        name="test-plugin",
        description="t",
        author="X",
        author_email="x@y",
    )
    assert regenerated == gen_publish_py(p, profile="standard"), (
        "the upgrade path's standard publish.py drifted from the standard variant"
    )
