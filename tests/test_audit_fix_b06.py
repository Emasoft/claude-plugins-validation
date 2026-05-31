"""Audit-fix regression tests for ``scripts/_skillaudit_python_context.py`` (batch b06).

Pins the five audit findings fixed in this file. Security-relevant fixes are
asserted BOTH ways — the benign idiom must stay suppressed AND a deliberately
malicious shape must STILL be flagged (a one-sided test would pass against a
classifier that simply suppresses everything).

Findings covered:

* HIGH — ``_is_env_read_modify_write`` self-satisfied its READ-half check from
  the WRITE line, so EVERY dynamic-key ``os.environ[var] = …`` (including a
  fully attacker-controlled one with no real read) was hard-suppressed to
  ``safe_literal``. Fixed by excluding the matched write line from the
  read-search window.
* MED #36 — ``_is_ruamel_yaml_safe_load`` searched the WHOLE module tree, so an
  unrelated module/sibling-scope ``yaml = YAML(typ="rt")`` suppressed a
  genuinely-unsafe PyYAML ``yaml.load()`` that merely reused the name. Fixed by
  restricting the constructor search to the load's enclosing-function +
  module-level (LEGB) scope, honoring local shadowing.
* MED #37 — ``_is_pure_literal_data`` SKIPPED ``**spread`` entries (``if k is
  not None``) instead of rejecting them, certifying ``{**other, '/p': 'x'}`` as
  pure-literal. Fixed to reject any dict containing a ``None`` key (a spread).
* LOW #122 — dead ``while``-loop advance in ``_match_in_python_inline_comment``
  with a self-contradicting comment. Behavior unchanged; dead scaffolding
  removed and the comment corrected.
* LOW #123 — ``_find_enclosing_call`` returned the OUTER call (BFS-first) on a
  span tie, contradicting its "deepest" docstring + worked example. Fixed with
  ``<=`` so the inner call wins on a tie.

Classifiers are exercised directly (unit level) for speed and independence
from the SQLite scan cache; ``CPV_SCAN_CACHE=0`` is set for any path that might
consult it.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("CPV_SCAN_CACHE", "0")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _skillaudit_python_context as pyctx  # noqa: E402

# ── HIGH — ENV_INJECTION read-modify-write self-satisfaction ──────────────────


def test_env_dynamic_key_write_without_read_is_not_suppressed() -> None:
    """A dynamic-key env write with NO env read in scope must NOT be treated
    as a benign read-modify-write (the malicious / undecidable shape)."""
    malicious = [
        "k = request.args['name']",
        "v = request.args['value']",
        "os.environ[k] = v",
    ]
    assert pyctx._is_env_read_modify_write(malicious, 2) is False


def test_env_single_line_dynamic_write_is_not_suppressed() -> None:
    """The audit's minimal repro ``os.environ[k] = v`` (one line, no read)
    must not self-satisfy the READ-half check."""
    assert pyctx._is_env_read_modify_write(["os.environ[k] = v"], 0) is False


def test_env_genuine_read_modify_write_is_suppressed() -> None:
    """The canonical benign shape — value derived from the env's own current
    value on a prior line — stays recognized as read-modify-write."""
    benign = [
        "val = os.environ.get('NO_PROXY', '')",
        "parts = [p for p in val.split(',') if p != host]",
        "os.environ[k] = ','.join(parts)",
    ]
    assert pyctx._is_env_read_modify_write(benign, 2) is True


def test_env_hijack_var_literal_keeps_finding_visible() -> None:
    """A runtime-hijack var literal anywhere in the window keeps the finding
    visible even when the read-modify-write shape is present (iron rule)."""
    hijack = [
        "cur = os.environ.get('LD_PRELOAD', '')",
        "os.environ[k] = cur + ':/tmp/evil.so'",
    ]
    assert pyctx._is_env_read_modify_write(hijack, 1) is False


def test_env_classify_malicious_is_not_safe_literal() -> None:
    """End-to-end: malicious dynamic-key ENV_INJECTION write no longer
    classifies as safe_literal (it falls through, staying visible)."""
    src = "k = request.args['name']\nv = request.args['value']\nos.environ[k] = v"
    verdict = pyctx.classify("a.py", src, 2, "os.environ[k] = v", "ENV_INJECTION")
    assert verdict != "safe_literal"


def test_env_classify_benign_is_safe_literal() -> None:
    """End-to-end: the benign read-modify-write still classifies safe_literal."""
    src = (
        "val = os.environ.get('NO_PROXY', '')\n"
        "parts = [p for p in val.split(',') if p != host]\n"
        "os.environ[k] = ','.join(parts)"
    )
    verdict = pyctx.classify("a.py", src, 2, "os.environ[k] = ','.join(parts)", "ENV_INJECTION")
    assert verdict == "safe_literal"


# ── MED #36 — ruamel-yaml scope restriction ──────────────────────────────────


def test_ruamel_cross_scope_unsafe_pyyaml_not_suppressed() -> None:
    """An unrelated module-level ruamel ``yaml = YAML(typ='rt')`` must NOT
    suppress an unsafe PyYAML ``yaml.load()`` inside a function that locally
    ``import yaml`` (the local import shadows the global)."""
    src = (
        "import yaml as _y\n"
        'yaml = ruamel.yaml.YAML(typ="rt")\n'
        "\n"
        "def load_untrusted(blob):\n"
        "    import yaml\n"
        "    return yaml.load(blob)\n"
    )
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 6) is False


def test_ruamel_sibling_scope_instance_not_suppressed() -> None:
    """A ruamel instance built in a SIBLING function must not suppress an
    unsafe PyYAML load in another function."""
    src = (
        "def make():\n"
        '    y = YAML(typ="rt")\n'
        "    return y\n"
        "\n"
        "def attack(blob):\n"
        "    import yaml as y\n"
        "    return y.load(blob)\n"
    )
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 7) is False


def test_ruamel_module_instance_load_in_func_is_suppressed() -> None:
    """A module-level ruamel instance closed over by a function (no local
    shadow) is the legitimate FP shape — still suppressed."""
    src = (
        "from ruamel.yaml import YAML\n"
        'yaml = YAML(typ="rt")\n'
        "yaml.preserve_quotes = True\n"
        "\n"
        "def read_settings(f):\n"
        "    data = yaml.load(f)\n"
        "    return data\n"
    )
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 6) is True


def test_ruamel_same_scope_module_is_suppressed() -> None:
    """The docstring's canonical same-scope example stays suppressed."""
    src = (
        "from ruamel.yaml import YAML\n"
        'yaml = YAML(typ="rt")\n'
        "yaml.indent(mapping=2)\n"
        'with open("s.yaml") as f:\n'
        "    data = yaml.load(f)\n"
    )
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 5) is True


def test_ruamel_func_local_instance_is_suppressed() -> None:
    """A ruamel instance created and used inside the same function stays
    suppressed."""
    src = "def read(f):\n    y = YAML(typ=\"safe\")\n    return y.load(f)\n"
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 3) is True


def test_ruamel_parameter_named_yaml_not_suppressed() -> None:
    """A ``.load`` whose receiver is a function PARAMETER named ``yaml`` is
    attacker-supplied — the module-level ruamel instance is shadowed and must
    NOT suppress it."""
    src = 'yaml = YAML(typ="rt")\n\ndef run(yaml, f):\n    return yaml.load(f)\n'
    assert pyctx._is_ruamel_yaml_safe_load(ast.parse(src), src, 4) is False


def test_function_locally_binds_detects_bindings() -> None:
    """``_function_locally_binds`` recognizes import / assign / param bindings
    and does NOT cross into nested-function scope."""
    src = (
        "def f(a, yaml_param):\n"
        "    import yaml as y\n"
        "    z = 1\n"
        "    def nested():\n"
        "        w = 2\n"
        "        return w\n"
        "    return nested()\n"
    )
    func = ast.parse(src).body[0]
    assert isinstance(func, ast.FunctionDef)
    assert pyctx._function_locally_binds(func, "y") is True  # import alias
    assert pyctx._function_locally_binds(func, "z") is True  # assignment
    assert pyctx._function_locally_binds(func, "yaml_param") is True  # parameter
    assert pyctx._function_locally_binds(func, "w") is False  # nested scope only
    assert pyctx._function_locally_binds(func, "absent") is False


# ── MED #37 — **spread dict not pure-literal ──────────────────────────────────


def _parse_and_find_const(src: str, value: str) -> tuple[ast.AST, ast.AST]:
    """Parse ``src`` ONCE and return ``(tree, target_constant)`` from that same
    tree. The target MUST belong to the tree passed to
    ``_node_is_in_module_level_pure_data_assign`` because it compares node
    identity (``n is t``) — two separate ``ast.parse`` calls produce distinct
    node objects that never match."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == value:
            return tree, node
    raise AssertionError(f"constant {value!r} not found in source")


def test_spread_dict_is_not_module_pure_data() -> None:
    """A module-level dict spreading a non-literal variable
    (``{**other, '/p': 'x'}``) is NOT inert pure-literal data."""
    tree, target = _parse_and_find_const("X = {**other, '/etc/passwd': 'leak'}\n", "/etc/passwd")
    assert pyctx._node_is_in_module_level_pure_data_assign(tree, target) is False


def test_pure_dict_is_module_pure_data() -> None:
    """A module-level dict of only literal keys/values stays recognized as
    inert pure-literal data."""
    tree, target = _parse_and_find_const("Y = {'/a': 'b', '/c': 'd'}\n", "/a")
    assert pyctx._node_is_in_module_level_pure_data_assign(tree, target) is True


def test_nested_pure_dict_is_module_pure_data() -> None:
    """A nested all-literal dict is still pure-literal data."""
    tree, target = _parse_and_find_const("Z = {'k': {'/a': 'b'}}\n", "/a")
    assert pyctx._node_is_in_module_level_pure_data_assign(tree, target) is True


# ── LOW #122 — inline-comment match (behavior preserved) ─────────────────────


def test_inline_comment_match_inside_comment() -> None:
    """A match that appears ONLY in the inline comment is certified inside."""
    assert pyctx._match_in_python_inline_comment("x = 1  # curl http://evil", "curl") is True


def test_inline_comment_match_in_code_keeps_visible() -> None:
    """A match whose first occurrence is in CODE (even if it also appears in a
    later comment) keeps the finding visible — return False."""
    assert pyctx._match_in_python_inline_comment("x = curl_thing()  # curl http://evil", "curl") is False


def test_inline_comment_no_comment_is_false() -> None:
    """A line with no inline comment never certifies."""
    assert pyctx._match_in_python_inline_comment("plain code line", "code") is False


def test_inline_comment_hash_in_string_handled() -> None:
    """A ``#`` inside a string literal is not the comment start; a match in the
    real trailing comment is still certified."""
    assert pyctx._match_in_python_inline_comment("a='#hash' # real comment x", "x") is True


def test_inline_comment_match_absent_is_false() -> None:
    """An absent match returns False even with a comment present."""
    assert pyctx._match_in_python_inline_comment("z = 3  # nothing here", "absent") is False


# ── LOW #123 — _find_enclosing_call returns the deepest call on a tie ─────────


def test_find_enclosing_call_returns_inner_on_tie() -> None:
    """For ``open(subprocess.run(...))`` (both span the same single line), the
    DEEPEST call (subprocess.run) is returned, matching the docstring."""
    src = "open(subprocess.run(['git', 'log'], capture_output=True).stdout)"
    call = pyctx._find_enclosing_call(ast.parse(src), 1)
    assert call is not None
    assert pyctx._node_qualname(call.func) == "subprocess.run"


def test_find_enclosing_call_multiline_inner_smaller_span() -> None:
    """When the inner call has a strictly smaller span, it is still returned."""
    src = "result = wrapper(\n    subprocess.run(['x']),\n    other,\n)"
    call = pyctx._find_enclosing_call(ast.parse(src), 2)
    assert call is not None
    assert pyctx._node_qualname(call.func) == "subprocess.run"


def test_find_enclosing_call_single_call_unchanged() -> None:
    """A lone call is returned unchanged."""
    src = "subprocess.run(['x'])"
    call = pyctx._find_enclosing_call(ast.parse(src), 1)
    assert call is not None
    assert pyctx._node_qualname(call.func) == "subprocess.run"


def test_classify_nested_safe_inner_cmd_injection() -> None:
    """End-to-end: a CMD_INJECTION match on a safe list-form subprocess.run
    wrapped in open(...) classifies via the inner safe call."""
    src = "open(subprocess.run(['git','log'], capture_output=True).stdout)"
    assert pyctx.classify("a.py", src, 0, "subprocess.run", "CMD_INJECTION") == "safe_literal"


def test_classify_unsafe_shell_true_stays_suspect() -> None:
    """Guard against over-suppression: shell=True with an f-string argument
    stays suspect."""
    src = "subprocess.run(f'curl {host}', shell=True)"
    assert pyctx.classify("a.py", src, 0, "curl", "CMD_INJECTION") == "suspect"
