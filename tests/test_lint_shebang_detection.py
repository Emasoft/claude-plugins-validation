"""Extensionless scripts declare their language with a shebang, and the lint engine must read it.

`detect_languages()` bucketed files purely by filename glob, so a script whose language is
declared by `#!/usr/bin/env python3` instead of a `.py` suffix landed in NO bucket and was
never linted. That is not a cosmetic gap: CPV's own `git-hooks/pre-push` — the script that
gates every push — shipped with a `NameError` and passed ruff, mypy, 11k tests, `--strict`
self-validation and a full publish, because `ruff check git-hooks/` printed "No Python files
found" and then "All checks passed". A checker that inspects ZERO files emits the same green
as a clean one.

CPV also installs that same hook into every plugin it scaffolds, so the blind spot was
shipped downstream. The `extend-include` added to CPV's own pyproject.toml fixes one repo;
this fixes the engine, which is what runs against third-party plugins.

The load-bearing tests are the NEGATIVE ones: a dirty hook must actually be DETECTED (a
detector that finds nothing is indistinguishable from one that is switched off), and files
that merely look script-ish must NOT be swept in.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.cpv_lint_engine import _shebang_language, detect_languages  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# _shebang_language — the interpreter parser
# --------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_env_python3_maps_to_python(tmp_path: Path) -> None:
    """The exact form CPV's own git hooks use."""
    assert _shebang_language(_write(tmp_path, "pre-push", "#!/usr/bin/env python3\nx = 1\n")) == "python"


def test_versioned_interpreter_is_normalized(tmp_path: Path) -> None:
    """`python3.12` is still python — version digits must not defeat the mapping."""
    assert _shebang_language(_write(tmp_path, "hook", "#!/usr/bin/env python3.12\n")) == "python"


def test_absolute_interpreter_path_maps(tmp_path: Path) -> None:
    """`#!/usr/bin/python3` has no `env` indirection to skip."""
    assert _shebang_language(_write(tmp_path, "hook", "#!/usr/bin/python3\n")) == "python"


def test_bash_maps_to_shell(tmp_path: Path) -> None:
    assert _shebang_language(_write(tmp_path, "deploy", "#!/bin/bash\necho hi\n")) == "shell"


def test_sh_maps_to_shell(tmp_path: Path) -> None:
    assert _shebang_language(_write(tmp_path, "deploy", "#!/bin/sh\n")) == "shell"


def test_node_maps_to_javascript(tmp_path: Path) -> None:
    assert _shebang_language(_write(tmp_path, "cli", "#!/usr/bin/env node\n")) == "javascript"


def test_env_flag_is_skipped(tmp_path: Path) -> None:
    """`env -S python3 -u` is a real-world form; the flag must not be read as the interpreter."""
    assert _shebang_language(_write(tmp_path, "hook", "#!/usr/bin/env -S python3 -u\n")) == "python"


def test_env_var_assignment_is_skipped(tmp_path: Path) -> None:
    """`env FOO=bar python3` — the assignment is not the interpreter."""
    assert _shebang_language(_write(tmp_path, "hook", "#!/usr/bin/env FOO=bar python3\n")) == "python"


def test_unknown_interpreter_is_not_bucketed(tmp_path: Path) -> None:
    """LOAD-BEARING: perl is a real shebang we do NOT lint — mis-bucketing it would run
    ruff over a Perl file and emit a wall of nonsense findings."""
    assert _shebang_language(_write(tmp_path, "script", "#!/usr/bin/perl\n")) is None


def test_no_shebang_is_not_bucketed(tmp_path: Path) -> None:
    """LICENSE / README / CHANGELOG are extensionless and must stay untouched."""
    assert _shebang_language(_write(tmp_path, "LICENSE", "MIT License\n\nCopyright (c)\n")) is None


def test_empty_file_is_not_bucketed(tmp_path: Path) -> None:
    assert _shebang_language(_write(tmp_path, "empty", "")) is None


def test_shebang_not_at_start_is_not_bucketed(tmp_path: Path) -> None:
    """A `#!` on line 2 is not a shebang — the kernel only honours byte 0."""
    assert _shebang_language(_write(tmp_path, "notascript", "# a comment\n#!/usr/bin/env python3\n")) is None


def test_binary_file_does_not_crash(tmp_path: Path) -> None:
    """A suffix-less binary (compiled hook, .DS_Store-alike) must return None, not raise."""
    p = tmp_path / "binaryblob"
    p.write_bytes(b"\x7fELF\x02\x01\x01\x00\x00\xff\xfe\xfd" + bytes(range(256)))
    assert _shebang_language(p) is None


def test_binary_file_with_shebang_prefix_does_not_crash(tmp_path: Path) -> None:
    """Adversarial: starts with `#!` then undecodable bytes. Must not raise."""
    p = tmp_path / "weird"
    p.write_bytes(b"#!\xff\xfe\x00\x81\x8f binary garbage\n")
    assert _shebang_language(p) is None  # no recognizable interpreter


def test_missing_file_returns_none(tmp_path: Path) -> None:
    """An unreadable/vanished path must degrade, never explode mid-scan."""
    assert _shebang_language(tmp_path / "does-not-exist") is None


# --------------------------------------------------------------------------
# detect_languages — the bucketing pass
# --------------------------------------------------------------------------


def test_extensionless_python_hook_is_collected(tmp_path: Path) -> None:
    """THE REGRESSION TEST: the git hook that shipped broken must now be discovered."""
    hooks = tmp_path / "git-hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text("#!/usr/bin/env python3\nimport sys\nprint(sys.argv)\n", encoding="utf-8")

    langs = detect_languages(tmp_path)
    assert "python" in langs, f"extensionless python hook not discovered: {langs}"
    assert hook in langs["python"], f"pre-push missing from the python bucket: {langs['python']}"


def test_suffixed_files_still_collected_alongside(tmp_path: Path) -> None:
    """The new pass must ADD to the glob buckets, never replace them."""
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    hooks = tmp_path / "git-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    py = detect_languages(tmp_path)["python"]
    names = {p.name for p in py}
    assert names == {"mod.py", "pre-commit"}, f"expected both, got {names}"


def test_license_is_not_collected(tmp_path: Path) -> None:
    """LOAD-BEARING negative: sweeping LICENSE into the python bucket would make ruff
    emit syntax errors on every plugin in existence."""
    (tmp_path / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (tmp_path / "README").write_text("hello\n", encoding="utf-8")
    langs = detect_languages(tmp_path)
    assert "python" not in langs, f"non-script extensionless files were bucketed: {langs}"


def test_dockerfile_is_not_double_bucketed(tmp_path: Path) -> None:
    """Dockerfile is suffix-less and already owned by the dockerfile bucket."""
    (tmp_path / "Dockerfile").write_text("FROM alpine\nRUN echo hi\n", encoding="utf-8")
    langs = detect_languages(tmp_path)
    assert "dockerfile" in langs
    for name, files in langs.items():
        if name == "dockerfile":
            continue
        assert not any(f.name == "Dockerfile" for f in files), f"Dockerfile double-bucketed into {name}"


def test_shell_and_node_hooks_bucket_separately(tmp_path: Path) -> None:
    (tmp_path / "deploy").write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    (tmp_path / "cli").write_text("#!/usr/bin/env node\nconsole.log(1)\n", encoding="utf-8")
    langs = detect_languages(tmp_path)
    assert {p.name for p in langs.get("shell", [])} == {"deploy"}
    assert {p.name for p in langs.get("javascript", [])} == {"cli"}


def test_cpv_own_git_hooks_are_discovered() -> None:
    """Dogfood: CPV's own two hooks are the files that motivated this. If this test ever
    stops finding them, the engine has regressed into the exact vacuous-green state."""
    langs = detect_languages(REPO_ROOT)
    py_names = {p.name for p in langs.get("python", [])}
    assert {"pre-push", "pre-commit"} <= py_names, (
        f"CPV's own extensionless hooks are invisible to the lint engine again: "
        f"{sorted(n for n in py_names if not n.endswith('.py'))}"
    )


def test_scaffolder_emits_extend_include_for_hooks() -> None:
    """Scaffolder parity: a generated plugin's OWN `ruff check` must see its hooks too.

    The engine fix covers plugins CPV validates; this covers the plugin's own CI, which
    runs ruff directly and never goes through CPV's engine.
    """
    src = (REPO_ROOT / "scripts" / "generate_plugin_repo.py").read_text(encoding="utf-8")
    assert "extend-include" in src, (
        "generate_plugin_repo.py emits no ruff extend-include — every scaffolded plugin's "
        "git hooks are a vacuous green in its own CI"
    )
