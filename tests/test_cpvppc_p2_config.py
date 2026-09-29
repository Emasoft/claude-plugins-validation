#!/usr/bin/env python3
"""CPVPPC P2 tests — config schema, lock, init, pin (canon 0.1.0).

Covers, each positive + negative: the anti-bypass property-name scan of the
schema itself (threat T1), closed enums, numeric floors, unknown keys at
every level, out-of-scope features (containers / npm / PyPI / Homebrew),
the support-window computation, init on four fixture shapes (plain Python /
PSS / janitor memgrep / llm-ext) with plan-3.3 structural equivalence, pin
against a REAL local bare remote (no mocks — repo testing rules), lock
consistency, and verify.py end-to-end on a compliant fixture.

Network-free except the pin tests, which use a local `git init --bare`
remote (loopback, no external network).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
for p in (SCRIPTS,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cpvppc import init as init_mod  # type: ignore[import-not-found]  # noqa: E402
from cpvppc import pin as pin_mod  # type: ignore[import-not-found]  # noqa: E402
from cpvppc.config_validate import (  # type: ignore[import-not-found]  # noqa: E402
    SCHEMA_VERSION,
    load_schema,
    validate_against_schema,
    validate_config,
)
from cpvppc.verify import (  # type: ignore[import-not-found]  # noqa: E402
    EXIT_COMPLIANT,
    EXIT_NON_COMPLIANT,
    load_canon,
    run_assertions,
    support_window_versions,
)
from cpvppc.verify import (
    main as verify_main,
)

SCHEMA = load_schema()


def _validate(doc):
    return validate_against_schema(doc, SCHEMA)


def _plain_python_cfg(marketplaces=None):
    """Plan 3.3 plain-Python example, schema_version added."""
    return {
        "schema_version": SCHEMA_VERSION,
        "canon": {"version": "0.1.0"},
        "repo": {"kind": "plugin"},
        "units": [{"name": "scripts", "path": "scripts", "adapter": "python-uv", "toolchain": {"version": "3.12"}}],
        "targets": {"marketplaces": marketplaces if marketplaces is not None else []},
    }


# --- canon manifest: P2 assertions registered -----------------------------------


class TestCanonP2Registry:
    def test_p2_fact_types_in_registry(self):
        canon = load_canon()
        for ft in ("config_valid", "lock_consistent"):
            assert ft in canon["fact_types"]

    def test_p2_assertions_present_after_p1(self):
        canon = load_canon()
        ids = [a["id"] for a in canon["assertions"]]
        assert ids[-2:] == ["CPVPPC-CFG-001", "CPVPPC-CFG-002"]
        # P1's marketplace assertions are untouched and in order.
        assert ids[:7] == [f"CPVPPC-MKT-{i:03d}" for i in range(1, 8)]

    def test_schema_version_const_matches_validator(self):
        assert SCHEMA["properties"]["schema_version"]["const"] == SCHEMA_VERSION


# --- threat T1: no bypass-shaped property name anywhere in the schema ------------


class TestAntiBypassPropertyNames:
    BANNED = re.compile(r"skip|allow|ignore|disable|bypass|except|divergen|optional", re.I)

    def _names_in_schema(self, schema, path="$"):
        """Every object key the schema can accept, enumerated."""
        out = []
        if not isinstance(schema, dict):
            return out
        for key in schema.get("properties", {}):
            out.append(f"{path}.{key}")
            out += self._names_in_schema(schema["properties"][key], f"{path}.{key}")
        items = schema.get("items")
        if isinstance(items, dict):
            out += self._names_in_schema(items, f"{path}[]")
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            out += self._names_in_schema(ap, f"{path}.*")
        for comb in ("oneOf", "anyOf", "allOf"):
            for i, branch in enumerate(schema.get(comb, [])):
                out += self._names_in_schema(branch, f"{path}/{comb}{i}")
        for branch in ([schema["then"]] if "then" in schema else []) + ([schema["else"]] if "else" in schema else []):
            out += self._names_in_schema(branch, f"{path}/ifthen")
        if "propertyNames" in schema:
            # keys accepted by propertyNames pattern — the os/arch hashes dict
            out.append(f"{path}(keys)")
        return out

    def test_no_bypass_word_in_any_accepted_key(self):
        bad = [n for n in self._names_in_schema(SCHEMA) if self.BANNED.search(n.rsplit(".", 1)[-1].strip("()[]*"))]
        assert bad == [], f"schema accepts bypass-shaped keys (threat T1): {bad}"

    def test_bogus_bypass_key_config_fails(self):
        doc = _plain_python_cfg()
        doc["skip_gates"] = True
        errs = _validate(doc)
        assert any("skip_gates" in e for e in errs)

    def test_scan_source_of_truth_is_the_schema_file(self):
        # The scan target IS the committed schema file, not a loaded copy that
        # could drift from it.
        text = (Path(init_mod.__file__).parent / "config_schema.json").read_text(encoding="utf-8")
        assert json.loads(text) == SCHEMA


# --- schema conformance: enums, floors, unknown keys, out-of-scope ---------------


class TestSchemaEnums:
    def test_repo_kind_closed(self):
        for bad in ("monorepo", "skill", ""):
            doc = _plain_python_cfg()
            doc["repo"]["kind"] = bad
            assert any("kind" in e for e in _validate(doc))

    def test_adapter_catalog_closed(self):
        doc = _plain_python_cfg()
        doc["units"][0]["adapter"] = "perl-stdio"
        assert any("adapter" in e for e in _validate(doc))

    def test_build_outputs_kind_closed(self):
        doc = _plain_python_cfg()
        doc["units"][0]["build"] = {"kind": "compiled", "outputs": [{"name": "x", "kind": "elf"}], "targets": []}
        assert any(e for e in _validate(doc) if "outputs" in e and "kind" in e)

    def test_target_enum_and_compiler_closed(self):
        base = {"os": "darwin", "arch": "arm64", "runner": "macos-14", "method": "native"}
        doc = _plain_python_cfg()
        doc["units"][0]["build"] = {"kind": "compiled", "outputs": [], "targets": [{**base, "method": "vagrant"}]}
        assert any("method" in e for e in _validate(doc))
        doc["units"][0]["build"]["targets"] = [{**base, "c_compiler": "tcc"}]
        assert any("c_compiler" in e for e in _validate(doc))
        doc["units"][0]["build"]["targets"] = [{**base, "os": "solaris"}]
        assert any(".os:" in e for e in _validate(doc))

    def test_deps_strategy_closed(self):
        doc = _plain_python_cfg()
        doc["units"][0]["deps"] = {"lockfile": "uv.lock", "strategy": "vendor-and-pray"}
        assert any("strategy" in e for e in _validate(doc))

    def test_archive_per_os_closed(self):
        doc = _plain_python_cfg()
        doc["artifacts"] = {"archive": {"linux": "rar"}}
        assert any("linux" in e for e in _validate(doc))

    def test_provenance_none_or_signer(self):
        doc = _plain_python_cfg()
        art = {"name": "m", "source": "https://x", "version": "1", "sha256": {"any": "a" * 64}}
        doc["external_artifacts"] = [{**art, "provenance": "trust-me"}]
        errs = _validate(doc)
        assert any("provenance" in e for e in errs)
        doc["external_artifacts"] = [{**art, "provenance": "none"}]
        assert not [e for e in _validate(doc) if "provenance" in e]


class TestSchemaFloors:
    def test_quality_floors_have_minimum_zero(self):
        q = SCHEMA["properties"]["quality"]["properties"]
        assert q["coverage_min"]["minimum"] == 0
        assert q["max_test_minutes"]["minimum"] == 0

    def test_negative_floor_rejected(self):
        doc = _plain_python_cfg()
        doc["quality"] = {"max_test_minutes": -5}
        assert any("max_test_minutes" in e for e in _validate(doc))


class TestUnknownKeys:
    def test_unknown_top_level_key_rejected(self):
        doc = _plain_python_cfg()
        doc["containers"] = {"enabled": True}
        assert any("containers" in e and "unknown key" in e for e in _validate(doc))

    def test_unknown_nested_key_rejected(self):
        doc = _plain_python_cfg()
        doc["repo"]["skin"] = "dark"
        assert any("skin" in e and "unknown key" in e for e in _validate(doc))
        doc = _plain_python_cfg()
        doc["units"][0]["toolchain"]["pin_strength"] = "max"
        assert any("pin_strength" in e for e in _validate(doc))

    def test_missing_required_top_level_key_rejected(self):
        doc = _plain_python_cfg()
        del doc["canon"]
        assert any("canon" in e and "required" in e for e in _validate(doc))

    def test_toolchain_version_required_string(self):
        doc = _plain_python_cfg()
        del doc["units"][0]["toolchain"]["version"]
        assert any("version" in e and "required" in e for e in _validate(doc))
        doc["units"][0]["toolchain"]["version"] = 3
        assert any("version" in e and "type" in e for e in _validate(doc))


class TestOutOfScopeFeatures:
    @pytest.mark.parametrize("section", ["containers", "distribution", "npm", "pypi", "homebrew", "docker"])
    def test_out_of_scope_top_level_section_rejected(self, section):
        doc = _plain_python_cfg()
        doc[section] = {"enabled": True}
        errs = _validate(doc)
        assert any(section in e for e in errs)


# --- support window --------------------------------------------------------------


class TestSupportWindow:
    def test_current_version_inside(self):
        assert "0.1.0" in support_window_versions("0.1.0")

    def test_previous_minor_of_same_major_inside(self):
        # 0.2.0 declared: the window includes 0.1.0 (previous minor).
        assert "0.1.0" in support_window_versions("0.2.0")
        assert "0.2.0" in support_window_versions("0.2.0")

    def test_unknown_version_gets_no_window_entry(self):
        # A version absent from the table only gets its own same-major minors;
        # it cannot conjure an arbitrary previous-major version.
        assert "0.0.9" not in support_window_versions("0.2.0")

    def test_prev_major_last_minor_gated_by_ship_date(self):
        from datetime import date, timedelta  # noqa: PLC0415

        from cpvppc.verify import _SUPPORT_WINDOW, _WINDOW_MONTHS_PREV_MAJOR  # noqa: PLC0415

        _SUPPORT_WINDOW["1.0.0"] = {
            "current": "1.0.0",
            "majors": {"0": {"shipped": date.today().isoformat(), "last_minor": "0.9.0"}},
        }
        _SUPPORT_WINDOW["1.0.0"]["majors"]["0"]["shipped"] = (date.today() - timedelta(days=1)).isoformat()
        try:
            assert "0.9.0" in support_window_versions("1.0.0")
            _SUPPORT_WINDOW["1.0.0"]["majors"]["0"]["shipped"] = (
                date.today() - timedelta(days=30 * (_WINDOW_MONTHS_PREV_MAJOR + 1))
            ).isoformat()
            assert "0.9.0" not in support_window_versions("1.0.0")
        finally:
            del _SUPPORT_WINDOW["1.0.0"]

    def test_invalid_version_string_is_empty_window(self):
        assert support_window_versions("not-a-version") == set()


# --- init: shape detection + plan 3.3 structural equivalence ----------------------


def _mk_plain_python_repo(tmp_path: Path) -> Path:
    root = tmp_path / "plain"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "hook.py").write_text("print('x')\n", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "my-plugin"}), encoding="utf-8")
    (root / ".python-version").write_text("3.12\n", encoding="utf-8")
    (root / "uv.lock").write_text("", encoding="utf-8")
    return root


def _mk_pss_repo(tmp_path: Path) -> Path:
    root = tmp_path / "pss"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run.py").write_text("pass\n", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "perfect-skill-suggester"}), encoding="utf-8")
    # local (gitignored) engine clone — detected as a rust unit but never shipped
    (root / "rust").mkdir()
    (root / "rust" / "Cargo.toml").write_text(
        '[package]\nname="pss-engine"\n\n[[bin]]\nname="pss"\n\n[[bin]]\nname="pss-nlp"\n', encoding="utf-8"
    )
    (root / "rust" / "Cargo.lock").write_text("", encoding="utf-8")
    (root / "rust" / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.82.0"\n', encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "pss").write_text("", encoding="utf-8")
    return root


def _mk_janitor_repo(tmp_path: Path) -> Path:
    root = tmp_path / "janitor"
    (root / "scripts" / "memgrep").mkdir(parents=True)
    (root / "scripts" / "memgrep" / "main.py").write_text("pass\n", encoding="utf-8")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "ai-maestro-janitor"}), encoding="utf-8")
    (root / "hooks").mkdir()
    (root / "hooks" / "dispatch.ts").write_text("export {}\n", encoding="utf-8")
    (root / ".nvmrc").write_text("22\n", encoding="utf-8")
    (root / "hooks" / "package-lock.json").write_text("{}", encoding="utf-8")
    return root


def _mk_llmext_repo(tmp_path: Path) -> Path:
    root = tmp_path / "llmext"
    (root / "scripts" / "llm-ext").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "llm-externalizer"}), encoding="utf-8")
    pkg = {
        "name": "llm-ext",
        "devDependencies": {"esbuild": "*", "vitest": "*", "typescript": "*"},
        "scripts": {"build": "esbuild src/cli/main.ts --bundle --outfile=dist/llm-ext.js"},
    }
    (root / "scripts" / "llm-ext" / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    (root / "scripts" / "llm-ext" / "src" / "cli").mkdir(parents=True)
    (root / "scripts" / "llm-ext" / "src" / "cli" / "main.ts").write_text("export {}\n", encoding="utf-8")
    (root / "scripts" / "llm-ext" / "package-lock.json").write_text("{}", encoding="utf-8")
    (root / ".nvmrc").write_text("20.18\n", encoding="utf-8")
    return root


class TestInitDetection:
    def test_plain_python_reproduces_3_3(self, tmp_path):
        """Plan 3.3's plain-Python example: the six content lines, exactly."""
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load(text)
        assert doc["canon"] == {"version": "0.1.0"}
        assert doc["repo"] == {"kind": "plugin"}
        assert doc["units"] == [
            {"name": "scripts", "path": "scripts", "adapter": "python-uv", "toolchain": {"version": "3.12"}}
        ]
        assert doc["targets"]["marketplaces"] == []
        content_lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert len(content_lines) <= 8, f"plain plugin config must stay ~6 lines, got {content_lines}"

    def test_pss_shape_produces_rust_unit_and_artifacts(self, tmp_path):
        root = _mk_pss_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        assert validate_config(doc) == [], f"generated config invalid: {validate_config(doc)}"
        by_name = {u["name"]: u for u in doc["units"]}
        assert by_name["scripts"]["adapter"] == "python-uv"
        rust = by_name["engine"]
        assert rust["adapter"] == "rust-cargo"
        outs = {o["name"]: o["kind"] for o in rust["build"]["outputs"]}
        assert outs == {"pss": "bin", "pss-nlp": "bin"}
        assert rust["build"]["kind"] == "compiled"
        assert rust["deps"]["lockfile"] == "rust/Cargo.lock" and rust["deps"]["strategy"] == "bundled"
        assert doc["artifacts"]["delivery"] == "committed-bin"
        assert {ln["name"] for ln in doc["runtime"]["launchers"]} == {"pss"}

    def test_janitor_shape_emits_node_unit_for_ts_hooks(self, tmp_path):
        root = _mk_janitor_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        assert validate_config(doc) == [], f"generated config invalid: {validate_config(doc)}"
        by_name = {u["name"]: u for u in doc["units"]}
        node = by_name["hooks"]
        assert node["adapter"] == "node-ts"
        assert node["toolchain"]["version"] == "22"  # from .nvmrc, not the default
        assert node["deps"]["lockfile"] == "hooks/package-lock.json"

    def test_llmext_shape_produces_bundled_node_unit(self, tmp_path):
        root = _mk_llmext_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        assert validate_config(doc) == [], f"generated config invalid: {validate_config(doc)}"
        u = doc["units"][0]
        assert u["adapter"] == "node-ts"
        assert u["adapter_options"]["bundler"] == "esbuild"
        assert u["adapter_options"]["test"] == "vitest"
        assert u["toolchain"]["version"] == "20.18"
        assert u["deps"]["lockfile"] == "scripts/llm-ext/package-lock.json"

    def test_marketplace_target_from_notify_workflow(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        wfdir = root / ".github" / "workflows"
        wfdir.mkdir(parents=True)
        text = (REPO_ROOT / ".github" / "workflows" / "notify-marketplace.yml").read_text(encoding="utf-8")
        text = text.replace("MARKETPLACE_OWNER: 'Emasoft'", "MARKETPLACE_OWNER: 'acme'")
        (wfdir / "notify-marketplace.yml").write_text(text, encoding="utf-8")
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        assert doc["targets"]["marketplaces"] == [
            {"owner": "acme", "repo": "emasoft-plugins", "entry_name": "my-plugin"}
        ]

    def test_refuses_overwrite_without_force(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        first = (root / "cpvppc.yaml").read_bytes()
        assert init_mod.main([str(root)]) == init_mod.EXIT_ERROR
        assert (root / "cpvppc.yaml").read_bytes() == first
        assert init_mod.main([str(root), "--force"]) == init_mod.EXIT_OK

    def test_kind_detection_plugin_marketplace_and_marketplace(self, tmp_path):
        root = tmp_path / "both"
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        (root / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
        assert init_mod.detect_config(root)["repo"]["kind"] == "plugin+marketplace"
        root2 = tmp_path / "mkt"
        (root2 / ".claude-plugin").mkdir(parents=True)
        (root2 / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
        assert init_mod.detect_config(root2)["repo"]["kind"] == "marketplace"

    def test_lock_records_every_config_field_explicitly(self, tmp_path):
        root = _mk_pss_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        lock = json.loads((root / ".cpvppc-lock.json").read_text(encoding="utf-8"))
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        assert lock["config"] == doc, "principle 7: the lock must record every field explicitly"
        assert lock["canon_version"] == "0.1.0"
        import hashlib  # noqa: PLC0415

        assert lock["config_sha256"] == hashlib.sha256((root / "cpvppc.yaml").read_bytes()).hexdigest()
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", lock["created"])


# --- pin --------------------------------------------------------------------------


class _BareRemote:
    """A REAL local git remote — repo testing rules prefer this over mocks."""

    def __init__(self, tmp_path: Path):
        self.url = str(tmp_path / "remote.git")
        seed = tmp_path / "seed"
        seed.mkdir()
        def git(*args: str, cwd: Path = seed) -> None:
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # noqa: S603,S607
        git("init", "-q")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        (seed / "f.txt").write_text("one\n", encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", "one")
        git("init", "-q", "--bare", self.url)
        git("remote", "add", "origin", self.url)
        git("push", "-q", "origin", "HEAD")
        self.sha1 = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=seed, check=True, capture_output=True, text=True  # noqa: S603
        ).stdout.strip()
        (seed / "f.txt").write_text("two\n", encoding="utf-8")
        git("add", "f.txt")
        git("commit", "-q", "-m", "two")
        git("push", "-q", "origin", "HEAD")
        self.sha2 = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=seed, check=True, capture_output=True, text=True  # noqa: S603
        ).stdout.strip()


@pytest.fixture()
def bare_remote(tmp_path):
    return _BareRemote(tmp_path)


def _mk_external_repo(tmp_path: Path, remote_url: str, ref: str) -> Path:
    root = tmp_path / "pinrepo"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "x.py").write_text("pass\n", encoding="utf-8")
    cfg = _plain_python_cfg()
    cfg["units"].append(
        {
            "name": "engine",
            "path": "rust",
            "adapter": "rust-cargo",
            "toolchain": {"version": "1.82.0"},
            "source": {"external-repo": {"url": remote_url, "ref": ref}},
        }
    )
    (root / "cpvppc.yaml").write_text(init_mod.render_config(cfg), encoding="utf-8")
    (root / ".cpvppc-lock.json").write_text(
        json.dumps(init_mod.render_lock(cfg, (root / "cpvppc.yaml").read_bytes())), encoding="utf-8"
    )
    return root


class TestPin:
    def test_pins_to_remote_head_and_rewrites_lock(self, tmp_path, bare_remote):
        root = _mk_external_repo(tmp_path, bare_remote.url, bare_remote.sha1)
        rc = pin_mod.main([str(root), "engine"])
        assert rc == pin_mod.EXIT_OK
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        ext = next(u for u in doc["units"] if u["name"] == "engine")["source"]["external-repo"]
        assert ext["ref"] == bare_remote.sha2
        lock = json.loads((root / ".cpvppc-lock.json").read_text(encoding="utf-8"))
        import hashlib  # noqa: PLC0415

        assert lock["config_sha256"] == hashlib.sha256((root / "cpvppc.yaml").read_bytes()).hexdigest()

    def test_refuses_sha_not_reachable_on_remote(self, tmp_path, bare_remote):
        """Plan: 'refusing a SHA not reachable on the engine remote' — an
        explicit --sha is verified against ls-remote output, never trusted."""
        root = _mk_external_repo(tmp_path, bare_remote.url, bare_remote.sha1)
        bogus = bare_remote.sha2[:32] + "0" * 8  # well-formed, not the remote's HEAD
        rc = pin_mod.main([str(root), "engine", "--sha", bogus])
        assert rc == pin_mod.EXIT_SHA_UNREACHABLE
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        doc = yaml.safe_load((root / "cpvppc.yaml").read_text(encoding="utf-8"))
        ext = next(u for u in doc["units"] if u["name"] == "engine")["source"]["external-repo"]
        assert ext["ref"] == bare_remote.sha1, "a refused pin must not change the config"

    def test_accepts_explicit_sha_only_when_remote_reports_it(self, tmp_path, bare_remote):
        root = _mk_external_repo(tmp_path, bare_remote.url, bare_remote.sha1)
        rc = pin_mod.main([str(root), "engine", "--sha", bare_remote.sha2])
        assert rc == pin_mod.EXIT_OK
        assert bare_remote.sha2 in (root / "cpvppc.yaml").read_text(encoding="utf-8")

    def test_in_tree_unit_is_not_pinnable_exit_2(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        rc = pin_mod.main([str(root), "scripts"])
        assert rc == pin_mod.EXIT_NOT_PINNABLE

    def test_unknown_unit_exit_2(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        assert pin_mod.main([str(root), "nope"]) == pin_mod.EXIT_NOT_PINNABLE

    def test_network_unavailable_exit_5_never_0(self, tmp_path, monkeypatch):
        root = _mk_external_repo(tmp_path, "https://invalid.invalid/nope.git", "a" * 40)

        def _fail(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git ls-remote", timeout=1)

        monkeypatch.setattr(pin_mod.subprocess, "run", _fail)
        rc = pin_mod.main([str(root), "engine"])
        assert rc == pin_mod.EXIT_NETWORK
        assert rc != 0

    def test_unreachable_host_exit_5(self, tmp_path):
        # No monkeypatch: a real unroutable URL — ls-remote fails -> UNKNOWN.
        root = _mk_external_repo(tmp_path, "https://cpvppc-test-invalid.invalid/x.git", "a" * 40)
        assert pin_mod.main([str(root), "engine"]) == pin_mod.EXIT_NETWORK


# --- lock consistency + config_valid fact types -----------------------------------


class TestFactTypesConfigAndLock:
    def test_config_valid_on_init_output(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        results = run_assertions(root, "0.1.0")
        r = next(x for x in results if x.id == "CPVPPC-CFG-001")
        assert r.passed, r.detail

    def test_config_valid_fails_on_unknown_key(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        (root / "cpvppc.yaml").write_text(text + "skip_gates: true\n", encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-001")
        assert not r.passed and "skip_gates" in r.detail

    def test_config_valid_fails_on_unparseable_yaml(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        (root / "cpvppc.yaml").write_text("canon: [unclosed\n", encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-001")
        assert not r.passed

    def test_lock_consistent_passes_fresh_and_fails_after_edit(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-002")
        assert r.passed, r.detail
        # Edit the config after the lock -> mismatch.
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        (root / "cpvppc.yaml").write_text(text.replace('"3.12"', '"3.13"'), encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-002")
        assert not r.passed and "config_sha256" in r.detail

    def test_regenerated_lock_is_compliant_again(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        (root / "cpvppc.yaml").write_text(text.replace('"3.12"', '"3.13"'), encoding="utf-8")
        assert init_mod.main([str(root), "--force"]) == init_mod.EXIT_OK
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-002")
        assert r.passed, r.detail

    def test_lock_stale_canon_version_fails(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        lock = json.loads((root / ".cpvppc-lock.json").read_text(encoding="utf-8"))
        lock["canon_version"] = "0.0.1"
        (root / ".cpvppc-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-002")
        assert not r.passed and "canon_version" in r.detail

    def test_lock_missing_config_field_fails_principle_7(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        lock = json.loads((root / ".cpvppc-lock.json").read_text(encoding="utf-8"))
        del lock["config"]["units"]
        (root / ".cpvppc-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-002")
        assert not r.passed and "principle 7" in r.detail

    def test_each_3_3_example_validates(self):
        for doc in (_plain_python_cfg(), _mk_pss_style_cfg(), _mk_llmext_style_cfg()):
            assert _validate(doc) == [], f"plan-3.3-style config rejected: {_validate(doc)}"

    def test_threat_t1_bogus_key_config_fails_via_fact(self, tmp_path):
        doc = _plain_python_cfg()
        doc["optional_bypass"] = {"skip": True}
        root = tmp_path / "t1"
        root.mkdir()
        import yaml  # type: ignore[import-not-found]  # noqa: PLC0415

        (root / "cpvppc.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
        r = next(x for x in run_assertions(root, "0.1.0") if x.id == "CPVPPC-CFG-001")
        assert not r.passed


def _mk_pss_style_cfg():
    """Plan 3.3 PSS example, condensed to the schema (external-repo source)."""
    doc = _plain_python_cfg()
    doc["units"].insert(
        0,
        {
            "name": "engine",
            "path": "rust",
            "adapter": "rust-cargo",
            "toolchain": {"version": "1.82.0"},
            "source": {"external-repo": {"url": "https://github.com/Emasoft/pss-rust-engine", "ref": "a" * 40}},
            "build": {
                "kind": "compiled",
                "outputs": [{"name": "pss", "kind": "bin"}, {"name": "pss-nlp", "kind": "bin"}],
                "targets": [
                    {"os": "darwin", "arch": "arm64", "runner": "macos-14", "method": "native", "c_compiler": "clang"},
                    {
                        "os": "linux",
                        "arch": "x86_64",
                        "triple": "x86_64-unknown-linux-musl",
                        "runner": "ubuntu-24.04",
                        "method": "zigbuild",
                        "c_compiler": "zig",
                    },
                ],
            },
            "deps": {"lockfile": "rust/Cargo.lock", "strategy": "bundled"},
        },
    )
    doc["artifacts"] = {"naming": "{name}-{os}-{arch}{ext}", "delivery": "release-asset-fetch"}
    doc["runtime"] = {
        "launchers": [{"name": "pss", "output": "pss"}],
        "arch_aliases": ["windows-arm64-to-x86_64"],
    }
    doc["external_artifacts"] = [
        {"name": "nlprule-en-model", "source": "https://example.invalid/m", "version": "0.6.4",
         "sha256": {"any": "b" * 64}, "provenance": "none"}
    ]
    return doc


def _mk_llmext_style_cfg():
    doc = _plain_python_cfg()
    doc["units"][0] = {
        "name": "cli",
        "path": "scripts/llm-ext",
        "adapter": "node-ts",
        "adapter_options": {
            "bundler": "esbuild",
            "entries": {"src/cli/main.ts": "dist/llm-ext.js"},
            "externals": ["better-sqlite3"],
            "test": "vitest",
        },
        "toolchain": {"version": "20.18"},
        "build": {
            "kind": "bundle",
            "outputs": [{"name": "llm-ext", "kind": "bundle"}, {"name": "better-sqlite3", "kind": "node-addon"}],
            "targets": [
                {"os": "darwin", "arch": "arm64", "runner": "macos-14", "method": "native", "node_abi": [20, 22]},
                {"os": "linux", "arch": "x86_64", "runner": "ubuntu-24.04", "method": "native", "node_abi": [20, 22]},
            ],
        },
        "deps": {"lockfile": "scripts/llm-ext/package-lock.json", "strategy": "bundled"},
    }
    doc["artifacts"] = {"naming": "{name}-{os}-{arch}{ext}", "delivery": "release-asset-fetch"}
    doc["runtime"] = {"launchers": [{"name": "llm-ext", "output": "llm-ext"}], "path_shim": {"dir": "~/.local/bin"}}
    doc["writes_outside_data"] = ["~/.local/bin/llm-ext"]
    return doc


# --- verify.py end-to-end ----------------------------------------------------------


class TestVerifyEndToEnd:
    def test_compliant_fixture_exit_0(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        rc = verify_main([str(root)])
        assert rc == EXIT_COMPLIANT

    def test_mutated_config_exit_1(self, tmp_path):
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        (root / "cpvppc.yaml").write_text(text + "bogus_key: 1\n", encoding="utf-8")
        rc = verify_main([str(root)])
        assert rc == EXIT_NON_COMPLIANT

    def test_declaration_version_read_from_yaml_not_line_scan(self, tmp_path):
        """The P1 line-scan matched the first `version:` line anywhere; the
        config reads canon.version properly — a nested `version:` must not
        hijack the declaration."""
        root = _mk_plain_python_repo(tmp_path)
        assert init_mod.main([str(root)]) == init_mod.EXIT_OK
        text = (root / "cpvppc.yaml").read_text(encoding="utf-8")
        assert 'canon: {version: "0.1.0"}' in text, "anchor line missing from the rendered config"
        (root / "cpvppc.yaml").write_text(text.replace('canon: {version: "0.1.0"}', 'canon: {version: "9.9.9"}'), encoding="utf-8")
        from cpvppc.verify import _declaration_version  # noqa: PLC0415

        assert _declaration_version(root) == "9.9.9"

    def test_canon_fallback_still_works_without_config(self, tmp_path):
        root = tmp_path / "legacy"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "publish.py").write_text("CANON_VERSION = '0.1.0'\n", encoding="utf-8")
        from cpvppc.verify import _declaration_version  # noqa: PLC0415

        assert _declaration_version(root) == "0.1.0"
