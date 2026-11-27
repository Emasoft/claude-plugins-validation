"""Issue #269: real helper modules (constants, annotations, classes) need no shebang."""

from __future__ import annotations

import os
import sys
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import ValidationReport  # noqa: E402
from validate_plugin import validate_scripts  # noqa: E402

HELPER_SOURCE = (
    "\"\"\"Helper module.\"\"\"\n"
    "from __future__ import annotations\n"
    "\n"
    "from pathlib import Path\n"
    "from typing import NamedTuple\n"
    "\n"
    "__all__ = [\"LOCK_NAME\", \"Entry\", \"read_lock\"]\n"
    "LOCK_NAME = \"excerpt.lock\"\n"
    "RETRIES: int = 3\n"
    "\n"
    "\n"
    "class Entry(NamedTuple):\n"
    "    name: str\n"
    "    size: int = 0\n"
    "\n"
    "\n"
    "def read_lock(path: Path) -> dict[str, int]:\n"
    "    return {path.name: RETRIES}\n"
)


def _missing_shebang_names(plugin_root: Path) -> list[str]:
    """Run validate_scripts and return the file names in the missing-shebang MINOR finding."""
    report = ValidationReport()
    validate_scripts(plugin_root, report)
    missing = [r.message for r in report.results if r.level == "MINOR" and "Scripts missing shebang" in r.message]
    assert len(missing) <= 1, missing
    if not missing:
        return []
    return missing[0].split(": ", 1)[1].split(". Without", 1)[0].split(", ")


def _plugin(tmp_path: Path, name: str, source: str, mode: int = 0o644) -> Path:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    target = scripts / name
    target.write_text(source)
    os.chmod(target, mode)
    return tmp_path


def test_helper_with_constants_annotations_and_class_not_flagged(tmp_path: Path) -> None:
    """Docstring, imports, constants, annotated function and NamedTuple class: no finding."""
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", HELPER_SOURCE)) == []


def test_helper_with_main_guard_flagged(tmp_path: Path) -> None:
    """The same helper plus a main guard is a script and stays flagged."""
    source = HELPER_SOURCE + "\n\nif __name__ == \"__main__\":\n    read_lock(Path(\".\"))\n"
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == ["helper.py"]


def test_helper_with_top_level_call_flagged(tmp_path: Path) -> None:
    """A bare top-level call makes the file a script."""
    source = HELPER_SOURCE + "\n\nread_lock(Path(\".\"))\n"
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == ["helper.py"]


def test_helper_with_top_level_loop_flagged(tmp_path: Path) -> None:
    """A top-level for loop makes the file a script."""
    source = HELPER_SOURCE + "\n\nfor _ in range(2):\n    pass\n"
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == ["helper.py"]


def test_type_checking_block_allowed(tmp_path: Path) -> None:
    """An `if TYPE_CHECKING:` block is allowed in a helper."""
    source = (
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from pathlib import Path\n"
        "\n"
        "\n"
        "def f(p: Path) -> str:\n"
        "    return str(p)\n"
    )
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == []


def test_reporters_file_shape_not_flagged(tmp_path: Path) -> None:
    """Shape of the reporter excerpt_lock.py: constants built from calls, regex tables, many functions."""
    source = (
        "\"\"\"Excerpt lock helper.\"\"\"\n"
        "from __future__ import annotations\n"
        "\n"
        "import re\n"
        "from pathlib import Path\n"
        "\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "CACHE = Path.home() / \".claude/plugins/cache\"\n"
        "FENCE_CLOSE = re.compile(r\"^`{3,}\\s*$\")\n"
        "COUNT: int = 0\n"
        "COUNT += 1\n"
        "\n"
        "\n"
        "def fence_close(line: str) -> bool:\n"
        "    return FENCE_CLOSE.match(line) is not None\n"
    )
    assert _missing_shebang_names(_plugin(tmp_path, "excerpt_lock.py", source)) == []


def test_executable_helper_without_shebang_still_flagged(tmp_path: Path) -> None:
    """The executable bit keeps a shebang-less helper flagged (unchanged behaviour)."""
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", HELPER_SOURCE, mode=0o755)) == ["helper.py"]

def test_decorator_call_and_async_def_not_flagged(tmp_path: Path) -> None:
    """A decorator with a call (dataclass(frozen=True)) and an async def are normal helper content."""
    source = (
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class Point:\n"
        "    x: int = 0\n"
        "\n"
        "\n"
        "async def fetch(p: Point) -> int:\n"
        "    return p.x\n"
    )
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == []


def test_call_in_default_or_annotation_flagged(tmp_path: Path) -> None:
    """A call in a default or annotation runs at import time, so the file stays flagged."""
    source = "def build():\n    return 1\n\n\ndef f(x=build()):\n    return x\n"
    assert _missing_shebang_names(_plugin(tmp_path, "helper.py", source)) == ["helper.py"]


def test_other_top_level_statements_flagged(tmp_path: Path) -> None:
    """A non-TYPE_CHECKING top-level if, a with block and a try block each make the file a script."""
    for index, tail in enumerate(
        (
            "if LIMIT:\n    import json\n",
            "with open(\"x\") as handle:\n    handle.read()\n",
            "try:\n    import json\nexcept ImportError:\n    json = None\n",
        )
    ):
        root = tmp_path / f"case{index}"
        root.mkdir()
        assert _missing_shebang_names(_plugin(root, "helper.py", "LIMIT = 1\n\n\ndef f():\n    return LIMIT\n\n\n" + tail)) == ["helper.py"], tail
