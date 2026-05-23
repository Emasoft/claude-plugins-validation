#!/usr/bin/env python3
"""Regression locks for issue #32 — skillaudit catalog must ship in the wheel.

In v2.99.2 the SkillAudit pattern catalog was kept at the **repo root**
``rules/skillaudit_patterns.json``. The hatchling build manifest in
``pyproject.toml`` declares

    [tool.hatch.build.targets.wheel]
    packages = ["scripts"]

so only the ``scripts/`` tree ships in the wheel. Every fresh
``uvx --from git+...`` install of v2.99.2 was therefore missing the
catalog, and every plugin scan emitted CRITICAL=1
("SkillAudit native scan could not run — skillaudit rule catalog
missing"). This blocked every downstream plugin's publish.py strict
gate.

v2.99.3 moved the catalog into the python package
(``scripts/rules/skillaudit_patterns.json``) so hatchling picks it up
automatically. These tests pin three invariants so the regression
cannot recur:

1. The canonical location is ``scripts/rules/skillaudit_patterns.json``.
2. No bare top-level ``rules/skillaudit_patterns.json`` exists (it
   would re-introduce the v2.99.2 packaging bug).
3. The loader in ``cpv_skillaudit_native._RULES_PATH`` resolves
   relative to its own module dir (``Path(__file__).resolve().parent
   / "rules" / ...``), so the catalog is found in BOTH a source-tree
   checkout AND a site-packages install.

A wheel-build smoke test rounds the suite out: actually build the
wheel via ``uv build``, unzip it, and confirm the catalog file is
present at the expected path inside the artefact.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class TestCatalogLocation:
    """Pin the canonical location of the catalog inside the package."""

    def test_catalog_lives_inside_scripts_package(self) -> None:
        canonical = SCRIPTS_DIR / "rules" / "skillaudit_patterns.json"
        assert canonical.is_file(), (
            f"v2.99.3 canonical catalog location missing: {canonical}. "
            "The catalog MUST live inside scripts/ (the python package) "
            "so it ships in the hatchling wheel via packages=['scripts']."
        )

    def test_no_top_level_rules_dir(self) -> None:
        """The repo-root rules/ folder must not be recreated.

        Putting the catalog at the repo root was the root cause of #32 —
        the wheel did not ship it. If someone adds it back the v2.99.2
        bug returns.
        """
        top_level = REPO / "rules" / "skillaudit_patterns.json"
        assert not top_level.exists(), (
            "rules/skillaudit_patterns.json must NOT exist at the repo "
            "root — that's where v2.99.2 had it and the wheel didn't "
            "ship it. The canonical location is "
            "scripts/rules/skillaudit_patterns.json (issue #32)."
        )

    def test_catalog_is_valid_json_with_expected_shape(self) -> None:
        canonical = SCRIPTS_DIR / "rules" / "skillaudit_patterns.json"
        data = json.loads(canonical.read_text(encoding="utf-8"))
        assert "rules" in data
        rules = data["rules"]
        assert isinstance(rules, list)
        assert len(rules) >= 40, f"catalog suspiciously small: {len(rules)} rules"


class TestLoaderResolution:
    """The loader must resolve the catalog relative to its module dir."""

    def test_rules_path_is_module_relative_not_repo_relative(self) -> None:
        """``_RULES_PATH`` must point to ``<module-dir>/rules/...``.

        v2.99.0–v2.99.2 used ``Path(__file__).resolve().parent.parent``
        which works in a source checkout (where ``parent.parent`` is the
        repo root and ``rules/`` lives there) but fails in a site-packages
        install (where ``parent.parent`` is ``site-packages/`` and there
        is no ``rules/`` sibling).
        """
        loader_src = (SCRIPTS_DIR / "cpv_skillaudit_native.py").read_text(encoding="utf-8")
        # The expected idiom: parent / "rules" / ... (NOT parent.parent)
        assert re.search(
            r'_RULES_PATH\s*=\s*Path\(__file__\)\.resolve\(\)\.parent\s*/\s*["\']rules["\']\s*/\s*["\']skillaudit_patterns\.json["\']',
            loader_src,
        ), (
            "_RULES_PATH must resolve relative to the module's own dir "
            "(Path(__file__).resolve().parent / 'rules' / 'skillaudit_patterns.json') "
            "so a site-packages install finds the catalog. "
            "DO NOT regress to .parent.parent — that broke v2.99.2 (issue #32)."
        )

    def test_rules_path_actually_points_at_existing_file(self) -> None:
        import cpv_skillaudit_native as native

        assert native._RULES_PATH.is_file(), (
            f"loader's _RULES_PATH ({native._RULES_PATH}) does not point at "
            "a real file. After issue #32 the catalog moved into the package "
            "but the loader must follow."
        )

    def test_loaded_rules_match_catalog_on_disk(self) -> None:
        import cpv_skillaudit_native as native

        rules = native._get_rules()
        assert len(rules) >= 40
        canonical = SCRIPTS_DIR / "rules" / "skillaudit_patterns.json"
        on_disk = json.loads(canonical.read_text(encoding="utf-8"))["rules"]
        assert len(rules) == len(on_disk)


class TestWheelShipsCatalog:
    """End-to-end packaging test: build the wheel and inspect its contents.

    The whole point of issue #32 is "what ships in the wheel" — a
    static check on the source tree is not enough. We invoke the
    actual build backend and confirm the catalog is inside the
    artefact at the expected path.

    Marked slow because ``uv build`` typically takes 5-15s; runs in CI
    on every push so the regression cannot ship.
    """

    def test_built_wheel_contains_skillaudit_catalog(self) -> None:
        if shutil.which("uv") is None:
            pytest.skip("uv not available — wheel-build smoke test requires uv")

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            out.mkdir()
            result = subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", str(out)],
                cwd=REPO,
                capture_output=True,
                text=True,
                timeout=180,
            )

            assert result.returncode == 0, f"uv build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

            wheels = list(out.glob("*.whl"))
            assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"

            with zipfile.ZipFile(wheels[0]) as zf:
                names = zf.namelist()

            # The catalog must be present inside the wheel under the
            # python-package path. hatchling renames ``scripts/`` to
            # the package's installed location, so we look for the
            # tail path.
            catalog_entries = [n for n in names if n.endswith("rules/skillaudit_patterns.json")]
            assert catalog_entries, (
                "skillaudit_patterns.json missing from the built wheel. "
                "This is exactly the v2.99.2 regression (issue #32). "
                "Wheel contents (first 30 entries):\n" + "\n".join(names[:30])
            )
