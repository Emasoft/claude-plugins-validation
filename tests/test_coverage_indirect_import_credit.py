#!/usr/bin/env python3
"""RC-TEST-COVERAGE: credit a module the suite reaches through a dispatcher.

The advisory matched a component to a test by filename stem or content mention
only, so a module exercised INDIRECTLY — the suite imports a dispatcher, the
dispatcher imports the backend — was reported as having "no discoverable test".
Concretely: CPV's own `_skillaudit_rust_context` is driven by 78 test files
through `cpv_skillaudit_native.scan_content`, and its name appears in none of
them, so the plugin was told its own tested classifier was untested.

The statement was not FALSE ("no *discoverable* test" is literally what it
said), which is exactly why it was worth fixing rather than arguing about: an
advisory that names a module the author knows is tested spends the credibility
it needs in order to be read at all.

Every test here is two-sided. The credit half alone would also pass against a
change that credited every module unconditionally — which would make the check
vacuous — so the must-still-fire half is the load-bearing one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from validate_plugin import (  # noqa: E402
    _coverage_indirect_python_tokens,
    check_test_coverage,
)


def _plugin(tmp_path: Path, scripts: dict[str, str], tests: dict[str, str]) -> Path:
    """A minimal plugin tree: scripts/<name>.py + tests/<name>.py."""
    root = tmp_path / "plug"
    (root / "scripts").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    for name, body in scripts.items():
        (root / "scripts" / name).write_text(body, encoding="utf-8")
    for name, body in tests.items():
        (root / "tests" / name).write_text(body, encoding="utf-8")
    return root


class _Report:
    """Minimal ValidationReport stand-in capturing only what this check emits."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _coverage_warning(root: Path) -> str:
    """Run the real check; return its warning text ('' when it stayed silent).

    Non-vacuity guard: this helper asserting on a field the check does not emit
    would make every 'is absent' test pass while proving nothing, so the tests
    below always pair an absence with a positive control.
    """
    report = _Report()
    check_test_coverage(root, report)
    return report.warnings[0] if report.warnings else ""


# ---------------------------------------------------------------------------
# The credit half — an indirectly-driven module must NOT be called untested
# ---------------------------------------------------------------------------
class TestIndirectlyTestedModuleIsCredited:
    def test_lazy_import_inside_a_function_is_followed(self, tmp_path: Path) -> None:
        """The real shape: the dispatcher imports its backend INSIDE a function.

        `cpv_skillaudit_native.py:2205` does exactly this, so a scan that only
        looked at top-level imports would miss the very edge this exists for.
        """
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": (
                    "def classify(x):\n"
                    "    if x.endswith('.rs'):\n"
                    "        from backend_rust import classify as rs\n"
                    "        return rs(x)\n"
                    "    return None\n"
                ),
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "from dispatcher import classify\n"},
        )
        assert "backend_rust.py" not in _coverage_warning(root)

    def test_top_level_from_import_is_followed(self, tmp_path: Path) -> None:
        """The ordinary top-level `from X import Y` edge."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "from backend_rust import classify\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        assert "backend_rust.py" not in _coverage_warning(root)

    def test_plain_import_form_is_followed(self, tmp_path: Path) -> None:
        """`import X` counts as well as `from X import Y`."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "import backend_rust\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        assert "backend_rust.py" not in _coverage_warning(root)

    def test_helper_returns_the_indirect_token(self, tmp_path: Path) -> None:
        """Unit-level: the helper itself reports the credited stem."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "from backend_rust import classify\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        tokens = _coverage_indirect_python_tokens(root, "test_dispatcher.py", "import dispatcher")
        assert "backend_rust" in tokens


# ---------------------------------------------------------------------------
# The must-still-fire half — the credit must not become a blanket mute
# ---------------------------------------------------------------------------
class TestGenuinelyUntestedModulesStillFire:
    def test_module_nobody_imports_still_fires(self, tmp_path: Path) -> None:
        """An orphan module is reachable from no tested module — still reported."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "from backend_rust import classify\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
                "orphan.py": "def unused():\n    return 1\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        warning = _coverage_warning(root)
        assert "orphan.py" in warning, "an unreachable module must still be reported"
        # Positive control: the credited sibling is absent from the SAME warning,
        # so this is not passing merely because the check emitted everything.
        assert "backend_rust.py" not in warning

    def test_importer_that_is_itself_untested_lends_nothing(self, tmp_path: Path) -> None:
        """Only a module the suite NAMES may lend its coverage onward.

        Without this gate, an untested module importing another would launder
        coverage it never had — two untested modules would silence each other.
        """
        root = _plugin(
            tmp_path,
            {
                "untested_a.py": "from untested_b import helper\n",
                "untested_b.py": "def helper():\n    return 1\n",
                "tested.py": "def run():\n    return 1\n",
            },
            {"test_tested.py": "import tested\n"},
        )
        warning = _coverage_warning(root)
        assert "untested_a.py" in warning
        assert "untested_b.py" in warning, "coverage cannot be laundered by an untested importer"

    def test_second_hop_is_not_credited(self, tmp_path: Path) -> None:
        """ONE hop only — a module two hops out is still reported.

        Pins the bound deliberately: full transitive closure would credit nearly
        every module in a cohesive package and make the advisory vacuous.
        """
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "from mid.py import x\n".replace(".py", ""),
                "mid.py": "from deep import y\n",
                "deep.py": "y = 1\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        warning = _coverage_warning(root)
        assert "deep.py" in warning, "the one-hop bound must stay pinned"
        assert "mid.py" not in warning, "the first hop must still be credited"

    def test_name_in_a_comment_is_not_an_import(self, tmp_path: Path) -> None:
        """A mention in a comment must not be read as an import edge."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "# we deliberately do not import backend_rust here\nx = 1\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        assert "backend_rust.py" in _coverage_warning(root)

    def test_stdlib_import_credits_nothing(self, tmp_path: Path) -> None:
        """Only a SIBLING plugin module can be credited."""
        root = _plugin(
            tmp_path,
            {
                "dispatcher.py": "import json\nimport os\n",
                "backend_rust.py": "def classify(x):\n    return 'safe'\n",
            },
            {"test_dispatcher.py": "import dispatcher\n"},
        )
        assert "backend_rust.py" in _coverage_warning(root)


# ---------------------------------------------------------------------------
# Dogfood — CPV's own tree, the case that prompted the fix
# ---------------------------------------------------------------------------
def test_cpv_own_rust_classifier_is_credited() -> None:
    """`_skillaudit_rust_context` is driven through `cpv_skillaudit_native`.

    This is the reported instance: the suite names the dispatcher in 78 files and
    never names the classifier, which the advisory then called untested.
    """
    root = Path(__file__).parent.parent
    if not (root / "scripts" / "_skillaudit_rust_context.py").exists():
        pytest.skip("CPV tree layout changed")
    warning = _coverage_warning(root)
    assert "_skillaudit_rust_context.py" not in warning
