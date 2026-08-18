"""TRDD-1YNY73NT — the release tool emits the G1.1 `Agent:` commit trailer.

The fleet golden rule G1.1 mandates a self-identification on every GitHub
write; the version-bump commit the tool ITSELF creates carried none (measured:
the 12/40 recent commits without the trailer were dominated by the tool's own
`chore: bump version` commits — a tool that cannot obey, not discipline
drift). Reference implementation: ai-maestro-chief-of-staff publish.py:208,
with the slug DERIVED (never hardcoded) via the name the template already
computes for the dependency tag.

Acceptance (from the hub's ledgered finding): the version-bump commit the tool
itself creates carries `Agent: <derived-slug>`, greppable, on a fresh publish
— proven here against a REAL git repo, not a string match alone. And no `@`
can ever be emitted (a handle-shaped name would page a real account).
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

pub = importlib.import_module("scripts.publish")
gpr = importlib.import_module("scripts.generate_plugin_repo")

PUBLISH_SRC = (REPO_ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")


def _emitted_body() -> str:
    from test_canon_143_genrepo import _params

    return gpr.gen_publish_py(_params())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _make_repo(tmp_path: Path, plugin_name: str) -> Path:
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_name, "version": "1.0.0"}), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    return root


class TestAgentTrailerHelper:
    def test_slug_is_derived_from_the_manifest(self, tmp_path):
        root = _make_repo(tmp_path, "my-plugin")
        assert pub._agent_trailer_args(root) == ["-m", "Agent: my-plugin"]

    def test_handle_shaped_name_cannot_page_anyone(self, tmp_path):
        """A name carrying `@` is stripped — no `@` reaches the message."""
        root = _make_repo(tmp_path, "@evil-handle")
        args = pub._agent_trailer_args(root)
        assert "@" not in " ".join(args)
        assert args == ["-m", "Agent: evil-handle"]

    def test_missing_manifest_falls_back_to_dir_name(self, tmp_path):
        root = tmp_path / "dirname-plugin"
        root.mkdir()
        assert pub._agent_trailer_args(root) == ["-m", "Agent: dirname-plugin"]


class TestRealCommitCarriesGreppableTrailer:
    def test_git_recognises_the_trailer(self, tmp_path):
        """The exact commit shape the tool runs, against a REAL git repo:
        `git log --format=%(trailers:key=Agent,valueonly)` must return the slug
        — i.e. it is a genuine trailer, not prose that merely looks like one."""
        root = _make_repo(tmp_path, "my-plugin")
        (root / "f.txt").write_text("x", encoding="utf-8")
        _git(root, "add", "f.txt")
        subprocess.run(
            ["git", "commit", "-q", "-m", "chore(release): v1.0.1", *pub._agent_trailer_args(root)],
            cwd=root,
            check=True,
        )
        value = _git(root, "log", "-1", "--format=%(trailers:key=Agent,valueonly)").strip()
        assert value == "my-plugin"
        # And the subject is untouched (the HEAD-recovery path compares %s).
        assert _git(root, "log", "-1", "--format=%s").strip() == "chore(release): v1.0.1"


class TestEveryOwnCommitSiteCarriesTheTrailer:
    def test_all_publish_py_commit_calls_include_trailer_args(self):
        """AST sweep: every `git commit` argv literal in publish.py either
        carries the `_agent_trailer_args` starred expression or is one this
        test explicitly knows about (none today)."""
        tree = ast.parse(PUBLISH_SRC)
        commit_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.List):
                    continue
                consts = [e.value for e in arg.elts if isinstance(e, ast.Constant)]
                if consts[:2] == ["git", "commit"]:
                    commit_calls += 1
                    starred = [
                        e
                        for e in arg.elts
                        if isinstance(e, ast.Starred)
                        and isinstance(e.value, ast.Call)
                        and getattr(e.value.func, "id", "") == "_agent_trailer_args"
                    ]
                    assert starred, f"git commit call without Agent trailer: {ast.dump(arg)[:200]}"
        assert commit_calls >= 3, f"expected >=3 commit sites, found {commit_calls}"


class TestEmittedCanonCarriesTheTrailer:
    def test_bump_commit_in_emitted_body(self):
        body = _emitted_body()
        assert 'f"Agent: {_agent_slug}"' in body
        # The slug is derived, never hardcoded to any plugin's name.
        assert "_plugin_name(root)" in body
        ast.parse(body)

    def test_emitted_body_strips_at_signs(self):
        body = _emitted_body()
        assert '.replace("@", "")' in body
