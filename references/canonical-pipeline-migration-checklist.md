# Canonical-pipeline migration checklist

**Owner**: cpv-plugin-fixer-agent agent (during `/cpv-upgrade-plugin` or `/cpv-fix-validation`).

**Contract**: After the migration agent has finished editing the plugin tree it
MUST run every BLOCKER and MAJOR check in this file. Every BLOCKER must pass.
Every MAJOR must pass unless the agent has captured an explicit, narrowly-scoped
waiver in `cpv.template_overrides[<file>]` inside `plugin.json` (and the waiver
itself is checked by CHECK-43 below). MINOR checks are advisory — they are
reported but never block the agent from declaring success.

**Origin**: Issue [#21](https://github.com/Emasoft/claude-plugins-validation/issues/21)
documents an `ai-maestro-janitor` v0.4.1 migration that left CI broken on the
first push because the migration agent had no post-migration smoke test. Every
row of issue #21's failure table maps to ≥1 check below — the index of issue-21
to CHECK-NN appears at the end of this file.

**Severity grades used here**:
- **BLOCKER**: must pass; agent MUST loop and re-fix until it passes. Failure
  is structurally invisible to `validate_plugin.py --strict` and breaks the
  plugin in the wild (CI, install, hook firing, gh release, etc.).
- **MAJOR**: must pass unless waived in `plugin.json::cpv.template_overrides`
  with a 1-line justification. Failure is recoverable but indicates the
  migration left a known footgun in place.
- **MINOR**: warn-only. Used for nice-to-have hygiene that occasionally
  trips up plugins but doesn't break first-push CI.

**Format conventions**:
- Every check has a unique `CHECK-NN` ID. Numbering is stable — do NOT renumber
  existing IDs when adding new checks; append at the end.
- Every snippet is meant to be runnable by the agent directly via `bash -c`,
  with `cwd` set to the plugin root. No env vars are required beyond `gh`,
  `uv`, and `git` being on `$PATH`.
- Every check exits 0 on PASS and non-zero on FAIL. The exit code may be
  inspected; STDOUT/STDERR is human-readable.

**ID range plan:**

| Range          | Category                                       |
| -------------- | ---------------------------------------------- |
| CHECK-01..07   | `.github/workflows/*.yml` integrity            |
| CHECK-08..16   | Python source quality                          |
| CHECK-17..21   | Hooks shape (`scripts/hooks/*.py`)             |
| CHECK-22..26   | `scripts/publish.py`                           |
| CHECK-27..31   | `plugin.json`                                  |
| CHECK-32..36   | `.gitignore` + dev folders                     |
| CHECK-37..41   | CPV self-validate clean                        |
| CHECK-42..46   | Canonical-template parity                      |
| CHECK-47..51   | Tests                                          |
| CHECK-52..56   | Git state                                      |
| CHECK-57..61   | Smoke-test publish                             |
| CHECK-62..65   | Marketplace                                    |
| CHECK-66..69   | Notification chain                             |
| CHECK-70..74   | `hooks/hooks.json`                             |
| CHECK-75..78   | MCP servers                                    |
| CHECK-79..82   | Docs & changelog                               |
| CHECK-83..87   | CI-parity defects (#137-143)                   |

**Total: 87 checks across 17 categories.**

---

## Category 1 — `.github/workflows/*.yml` integrity (CHECK-01..07)

### CHECK-01 [BLOCKER] Every workflow YAML parses with `yaml.safe_load`
**Why**: A workflow that doesn't parse breaks GitHub Actions immediately on push. Issue #21's literal-glob breakage was visible only because the YAML still parsed; a typo'd YAML would never even start a job.
**Verify**:
```bash
uv run python - <<'PY'
import glob, pathlib, sys
import yaml
ok = True
for f in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    try:
        yaml.safe_load(pathlib.Path(f).read_text())
    except Exception as e:
        ok = False
        print(f'PARSE-FAIL {f}: {e}', file=sys.stderr)
sys.exit(0 if ok else 1)
PY
```
**Pass when**: exit 0; every `*.yml`/`*.yaml` under `.github/workflows/` round-trips through `yaml.safe_load` without raising.
**On fail**: open the failing file, fix the offending line, re-run. See `skills/cpv-fix-validation/references/pipeline-migration.md` §1.

### CHECK-02 [BLOCKER] Every literal path in every `run:` step exists on disk
**Why**: This is the exact bug from issue #21 — `chmod +x scripts/dispatch.sh scripts/detectors/*.sh` referenced files that the migration deleted, CI exited 1 on first push.
**Verify**:
```bash
uv run python - <<'PY'
import glob, re, shlex, sys
from pathlib import Path
import yaml
SCRIPT_HINT = re.compile(r'^(?:scripts|tests|git-hooks|\.githooks|references|templates|assets|docs|hooks)/')
fails=[]
for wf in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    raw = Path(wf).read_text()
    doc = yaml.safe_load(raw) or {}
    for jname, job in (doc.get('jobs') or {}).items():
        for i, step in enumerate(job.get('steps') or []):
            run = step.get('run')
            if not isinstance(run, str): continue
            for line in run.splitlines():
                line = line.strip().rstrip('\\')
                try: tokens = shlex.split(line, posix=True)
                except ValueError: continue
                for tok in tokens:
                    if not SCRIPT_HINT.match(tok): continue
                    if any(c in tok for c in '*?[]{}'): continue  # globs handled in CHECK-03
                    if not Path(tok).exists():
                        fails.append((wf, jname, i, tok))
for f in fails: print('MISSING', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0; no `MISSING` lines printed.
**On fail**: either remove the dead reference from the workflow OR re-create the missing file. Maps to issue #21 row 1 (`ci.yml` globs `scripts/{detectors,hooks,lib}/*.sh`).

### CHECK-03 [BLOCKER] Every glob in every `run:` step expands to ≥1 file
**Why**: Issue #21 (literal-glob expansion) — `scripts/detectors/*.sh: openBinaryFile: does not exist`. Empty globs are never just "noisy" — `chmod`/`shellcheck`/`bash -n` exit non-zero on them under `set -e`.
**Verify**:
```bash
uv run python - <<'PY'
import glob, re, shlex, sys
from pathlib import Path
import yaml
SCRIPT_HINT = re.compile(r'^(?:scripts|tests|git-hooks|\.githooks|references|templates|assets|docs|hooks)/')
fails=[]
for wf in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    doc = yaml.safe_load(Path(wf).read_text()) or {}
    for jname, job in (doc.get('jobs') or {}).items():
        for i, step in enumerate(job.get('steps') or []):
            run = step.get('run')
            if not isinstance(run, str): continue
            for line in run.splitlines():
                line = line.strip().rstrip('\\')
                try: tokens = shlex.split(line, posix=True)
                except ValueError: continue
                for tok in tokens:
                    if not SCRIPT_HINT.match(tok): continue
                    if not any(c in tok for c in '*?[]{}'): continue
                    if not glob.glob(tok):
                        fails.append((wf, jname, i, tok))
for f in fails: print('EMPTY-GLOB', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0; no `EMPTY-GLOB` lines printed.
**On fail**: either delete the glob step or rewrite it for the post-migration tree (`scripts/*.py` instead of `scripts/*.sh`). Maps to issue #21 ask #2.

### CHECK-04 [BLOCKER] `pull_request_target` is NOT used (only `pull_request`)
**Why**: `pull_request_target` runs forked PRs with write access + secrets — the canonical CPV pipeline never needs that. Accidentally enabling it on migration is a privilege escalation.
**Verify**:
```bash
! grep -rE '^[[:space:]]*-?[[:space:]]*pull_request_target' .github/workflows/ 2>/dev/null
```
**Pass when**: exit 0; grep finds no matches.
**On fail**: replace `pull_request_target` with `pull_request`. See `~/.claude/rules/gh-actions.md`.

### CHECK-05 [MAJOR] Top-level `permissions:` is least-privilege OR absent
**Why**: GitHub Actions default token permission is broad on older repos (often `write-all` inherited). Canonical CPV pipeline declares an explicit minimal set (`contents: read` at top level + add only what's needed per job).
**Verify**:
```bash
uv run python - <<'PY'
import glob, sys
from pathlib import Path
import yaml
fails=[]
for wf in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    doc = yaml.safe_load(Path(wf).read_text()) or {}
    perms = doc.get('permissions')
    if perms == 'write-all': fails.append((wf, 'write-all top-level'))
for f in fails: print('PERM', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0; no `PERM` lines.
**On fail**: replace `permissions: write-all` with explicit per-job grants. See `~/.claude/rules/gh-actions.md`.

### CHECK-06 [MAJOR] Every third-party `uses:` action is pinned to a full SHA
**Why**: Canonical pipeline pins all non-`actions/`/`github/` actions by SHA to defeat tag-rewriting attacks.
**Verify**:
```bash
uv run python - <<'PY'
import glob, re, sys
from pathlib import Path
import yaml
SHA_RE = re.compile(r'@[0-9a-f]{40}\b')
TRUSTED = ('actions/', 'github/')
fails=[]
for wf in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    doc = yaml.safe_load(Path(wf).read_text()) or {}
    for jname, job in (doc.get('jobs') or {}).items():
        for i, step in enumerate(job.get('steps') or []):
            uses = step.get('uses')
            if not isinstance(uses, str) or uses.startswith('./'): continue
            owner = uses.split('/', 1)[0] + '/'
            if owner in TRUSTED: continue
            if not SHA_RE.search(uses):
                fails.append((wf, jname, i, uses))
for f in fails: print('UNPINNED', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0; no `UNPINNED` lines.
**On fail**: run `pinact run` (third-party tool) or manually replace the tag with the resolved commit SHA + tag comment.

### CHECK-07 [MAJOR] `setup-{node,python,go,java}` actions have caching enabled
**Why**: Canonical pipeline always enables caching on the setup-X actions to avoid burning Actions minutes on dependency installs every run.
**Verify**:
```bash
uv run python - <<'PY'
import glob, sys
from pathlib import Path
import yaml
SETUPS = {'actions/setup-node', 'actions/setup-python', 'actions/setup-go', 'actions/setup-java'}
fails=[]
for wf in sorted(glob.glob('.github/workflows/*.yml') + glob.glob('.github/workflows/*.yaml')):
    doc = yaml.safe_load(Path(wf).read_text()) or {}
    for jname, job in (doc.get('jobs') or {}).items():
        for i, step in enumerate(job.get('steps') or []):
            uses = step.get('uses') or ''
            base = uses.split('@', 1)[0]
            if base not in SETUPS: continue
            with_ = step.get('with') or {}
            if 'cache' not in with_:
                fails.append((wf, jname, i, base))
for f in fails: print('NO-CACHE', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0; no `NO-CACHE` lines.
**On fail**: add `cache: <pkg-manager>` to the step's `with:` block (e.g. `cache: pip`, `cache: npm`).

---

## Category 2 — Python source quality (CHECK-08..16)

### CHECK-08 [BLOCKER] Every `scripts/**/*.py` parses with `ast.parse`
**Why**: A migration that leaves a syntax error in `scripts/` makes publish.py unimportable, which makes the orchestrator-check Gate-0 fail.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
fails=[]
for p in sorted(pathlib.Path('scripts').rglob('*.py')):
    try: ast.parse(p.read_text())
    except SyntaxError as e: fails.append((str(p), str(e)))
for f in fails: print('SYNTAX', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: open the failing file, fix the syntax. Cannot proceed.

### CHECK-09 [BLOCKER] No module-scope `sys.exit` in `scripts/**/*.py`
**Why**: Issue #21 row 8 (hooks). Module-scope `sys.exit` makes the file unimportable — every `from scripts.X import Y` triggers an exit. Hooks must guard with `if __name__ == "__main__": main()`.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
fails=[]
for p in sorted(pathlib.Path('scripts').rglob('*.py')):
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
               and f.value.id == 'sys' and f.attr == 'exit':
                fails.append((str(p), node.lineno))
for f in fails: print('MOD-SYS-EXIT', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: wrap in `def main(): ...` and add `if __name__ == "__main__": sys.exit(main())`. See `skills/cpv-fix-validation/references/hook-fixes.md`.

### CHECK-10 [MAJOR] Every Python script with third-party imports has a PEP 723 `# /// script` block
**Why**: PEP 723 lets `uv run` resolve dependencies declaratively — without it, `uv run scripts/X.py` falls back to the global env and breaks reproducibly across machines.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
STDLIB = set(sys.stdlib_module_names)
fails=[]
for p in sorted(pathlib.Path('scripts').rglob('*.py')):
    src = p.read_text()
    try: tree = ast.parse(src)
    except SyntaxError: continue
    third_party = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                root = n.name.split('.')[0]
                if root not in STDLIB and not root.startswith('_'):
                    third_party = True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                root = node.module.split('.')[0]
                if root not in STDLIB and not root.startswith('_'):
                    third_party = True
    if third_party and '# /// script' not in src:
        fails.append(str(p))
for f in fails: print('NO-PEP723', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add a PEP 723 inline-metadata block at the top of the file.

### CHECK-11 [MAJOR] Third-party imports inside `main()` (not module-scope) for hook scripts
**Why**: Issue #21 row 8. Hooks are imported by `claude` at session-start; module-scope third-party imports failing cause Claude Code to silently disable the hook.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
STDLIB = set(sys.stdlib_module_names)
hook_dir = pathlib.Path('scripts/hooks')
if not hook_dir.is_dir(): sys.exit(0)
fails=[]
for p in hook_dir.rglob('*.py'):
    if p.name == '__init__.py': continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name.split('.')[0] for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split('.')[0]]
            for n in names:
                if n not in STDLIB and not n.startswith('_'):
                    fails.append((str(p), node.lineno, n))
for f in fails: print('MOD-3RDPARTY', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: move the import into `def main():`. Re-run.

### CHECK-12 [BLOCKER] Every package directory under `scripts/` has `__init__.py`
**Why**: Issue #21 row 9 (`scripts/lib/__init__.py` missing). Without the package marker, `from lib import state` is treated as a third-party import and PEP 723 misclassifies it.
**Verify**:
```bash
uv run python - <<'PY'
import pathlib, sys
fails=[]
for d in pathlib.Path('scripts').rglob('*'):
    if not d.is_dir(): continue
    if any(c.suffix == '.py' for c in d.iterdir() if c.is_file()):
        if not (d / '__init__.py').exists():
            fails.append(str(d))
for f in fails: print('NO-INIT', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: `touch scripts/<dir>/__init__.py` per missing dir.

### CHECK-13 [MAJOR] Sibling-package imports use `from .X import Y` (relative form)
**Why**: Once `__init__.py` is present, sibling imports like `from lib import state` should be `from .lib import state` if they live in the same package — otherwise `pyright` and `mypy` flag them.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
fails=[]
for p in sorted(pathlib.Path('scripts').rglob('*.py')):
    parent_pkg = p.parent
    if not (parent_pkg / '__init__.py').exists(): continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    siblings = {x.stem for x in parent_pkg.iterdir() if x.suffix == '.py' and x.stem != '__init__'}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            root = node.module.split('.')[0]
            if root in siblings:
                fails.append((str(p), node.lineno, node.module))
for f in fails: print('ABS-SIBLING', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: prefix the import with `.` for relative form. Maps to issue #21 row 9.

### CHECK-14 [MAJOR] mypy clean on `scripts/`
**Why**: Issue #21 row 10 (`scripts/safe_delete.py` mypy `[no-redef]`). mypy catches the `try: import state as _state_mod` pattern that breaks under strict typing.
**Verify**:
```bash
uv run mypy --no-incremental --hide-error-context --show-error-codes scripts/ 2>&1 | tee /tmp/cpv-mypy.log; \
  ! grep -q '^scripts/.*: error:' /tmp/cpv-mypy.log
```
**Pass when**: no `error:` lines for files under `scripts/`. Exit 0.
**On fail**: read the mypy output, fix each error. See `skills/cpv-fix-validation/references/code-quality-fixes.md`.

### CHECK-15 [MAJOR] ruff clean on `scripts/`
**Why**: Canonical pipeline lints via `cpv_lint_engine` (ruff under the hood). Migration regressions show up as ruff errors.
**Verify**:
```bash
uv run ruff check --quiet scripts/
```
**Pass when**: exit 0; no output.
**On fail**: `uv run ruff check --fix scripts/` for auto-fixes; manually address the rest.

### CHECK-16 [MAJOR] pyright clean on `scripts/`
**Why**: pyright catches type-narrowing edge cases mypy misses (issue #21 row 10 was a `[no-redef]` ambiguity that pyright also detects).
**Verify**:
```bash
uv run pyright scripts/ 2>&1 | tee /tmp/cpv-pyright.log; \
  awk '/^[0-9]+ error/{print $1; exit ($1!=0)}' /tmp/cpv-pyright.log
```
**Pass when**: pyright reports `0 errors`.
**On fail**: read `/tmp/cpv-pyright.log`, fix each error. Use `cast(...)` or restructure imports.

---

## Category 3 — Hooks shape (`scripts/hooks/*.py`) (CHECK-17..21)

### CHECK-17 [BLOCKER] Every hook script has `if __name__ == "__main__": main()`
**Why**: Issue #21 row 8. Without the guard, importing the module triggers side-effects.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
hook_dir = pathlib.Path('scripts/hooks')
if not hook_dir.is_dir(): sys.exit(0)
fails=[]
for p in sorted(hook_dir.rglob('*.py')):
    if p.name == '__init__.py': continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    has_guard = False
    for node in tree.body:
        if isinstance(node, ast.If):
            t = node.test
            if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == '__name__':
                has_guard = True
    if not has_guard: fails.append(str(p))
for f in fails: print('NO-MAIN-GUARD', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: wrap the script body in `def main(): ...` and add the guard. See `skills/cpv-fix-validation/references/hook-fixes.md`.

### CHECK-18 [BLOCKER] No module-scope `sys.exit(0)` in `scripts/hooks/`
**Why**: Some hooks accidentally `sys.exit(0)` at the module top to early-return. This makes import succeed-by-exit and hides logic bugs in CI.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
hook_dir = pathlib.Path('scripts/hooks')
if not hook_dir.is_dir(): sys.exit(0)
fails=[]
for p in sorted(hook_dir.rglob('*.py')):
    if p.name == '__init__.py': continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
               and f.value.id == 'sys' and f.attr == 'exit':
                fails.append((str(p), node.lineno))
for f in fails: print('MOD-SYS-EXIT', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: remove the `sys.exit` at module scope; rely on `if __name__ == "__main__"` guard.

### CHECK-19 [MAJOR] Hook scripts import sibling modules (e.g. `state`) inside `main()`, not at module scope
**Why**: Issue #21 row 8. Module-scope `import state` runs at session-start and crashes if `lib/__init__.py` is missing or PEP 723 didn't fetch the dependency.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
hook_dir = pathlib.Path('scripts/hooks')
if not hook_dir.is_dir(): sys.exit(0)
SAFE_MOD = set(sys.stdlib_module_names) | {'__future__'}
fails=[]
for p in sorted(hook_dir.rglob('*.py')):
    if p.name == '__init__.py': continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [n.name.split('.')[0] for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split('.')[0]]
            for n in names:
                if n not in SAFE_MOD and not n.startswith('_'):
                    fails.append((str(p), node.lineno, n))
for f in fails: print('MOD-IMPORT', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: move the import into `def main():`.

### CHECK-20 [MAJOR] Every `scripts/hooks/*.py` is referenced by `hooks/hooks.json` OR is documented as standalone
**Why**: Orphaned hook scripts confuse later migrations — the agent thinks they're live but `hooks.json` ignores them.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, sys
hook_dir = pathlib.Path('scripts/hooks')
hj = pathlib.Path('hooks/hooks.json')
if not hook_dir.is_dir(): sys.exit(0)
referenced = set()
if hj.exists():
    try:
        cfg = json.loads(hj.read_text())
        for events in (cfg.get('hooks') or {}).values():
            for entry in events:
                for h in (entry.get('hooks') or []):
                    cmd = h.get('command') or ''
                    referenced.update(p for p in cmd.split() if p.endswith('.py'))
    except Exception: pass
fails = []
for p in sorted(hook_dir.rglob('*.py')):
    if p.name == '__init__.py': continue
    rel = str(p)
    if any(rel in r or p.name in r for r in referenced): continue
    # standalone if first 10 lines contain a # STANDALONE: marker
    head = '\n'.join(p.read_text().splitlines()[:10])
    if 'STANDALONE:' in head: continue
    fails.append(rel)
for f in fails: print('ORPHAN-HOOK', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: either register the hook in `hooks/hooks.json`, add a `# STANDALONE:` comment with rationale, or delete the file.

### CHECK-21 [MINOR] Hook scripts have a docstring describing their purpose
**Why**: Migration regressions are easier to diagnose when each hook's first statement is a clear docstring.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
hook_dir = pathlib.Path('scripts/hooks')
if not hook_dir.is_dir(): sys.exit(0)
fails = []
for p in sorted(hook_dir.rglob('*.py')):
    if p.name == '__init__.py': continue
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    if not (tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant)):
        fails.append(str(p))
for f in fails: print('NO-DOCSTRING', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add a top-of-file `"""..."""` docstring.

---

## Category 4 — `scripts/publish.py` (CHECK-22..26)

### CHECK-22 [BLOCKER] `publish.py` imports cleanly (no ImportError)
**Why**: A broken import here means `publish.py --print-gates` exits non-zero, which means the publish pipeline is dead.
**Verify**:
```bash
uv run python -c "import importlib.util, sys; \
  spec = importlib.util.spec_from_file_location('publish', 'scripts/publish.py'); \
  m = importlib.util.module_from_spec(spec); \
  spec.loader.exec_module(m); \
  print('IMPORT-OK')"
```
**Pass when**: prints `IMPORT-OK`. Exit 0.
**On fail**: read the traceback. Most often: `cpv_network_resilience.py` missing (re-run `--force-templates`).

### CHECK-23 [BLOCKER] `publish.py` calls `cpv-remote-validate plugin --strict` (NOT the retired `lint` subcommand)
**Why**: Issue #21 row 5. Migration left `publish.py` calling `cpv-remote-validate lint`, which CPV retired in v2.71.0; that fails Step 4 with exit 2.
**Verify**:
```bash
! grep -E 'cpv-remote-validate[[:space:]]+lint\b' scripts/publish.py
```
**Pass when**: exit 0 (no matches).
**On fail**: replace `lint` with `plugin --strict`. See `scripts/generate_plugin_repo.py:gen_publish_py` for canonical form.

### CHECK-24 [BLOCKER] `publish.py --print-gates` succeeds (zero side-effects)
**Why**: This is the cheapest end-to-end smoke test of the publish pipeline. If it fails, every subsequent gate is dead.
**Verify**:
```bash
uv run python scripts/publish.py --print-gates
```
**Pass when**: exit 0; output lists all gates.
**On fail**: read the traceback. Typically a missing import or argparse misconfiguration after migration.

### CHECK-25 [BLOCKER] Gate sequence matches canonical `gen_publish_py(p)` template
**Why**: Migration that reorders gates (validate before tests, tests before clean-tree) breaks the contract that `validate_plugin.py --strict` runs against the in-tree state, not stale state.
**Verify**:
```bash
uv run python - <<'PY'
import re, sys, pathlib
canonical_order = ['Gate 1', 'Gate 2', 'Gate 3']
src = pathlib.Path('scripts/publish.py').read_text()
positions = []
for tag in canonical_order:
    m = re.search(rf'\b{re.escape(tag)}\b', src)
    if not m: print(f'MISSING-TAG {tag}'); sys.exit(1)
    positions.append(m.start())
if positions != sorted(positions):
    print('GATE-ORDER-WRONG', positions); sys.exit(1)
print('GATE-ORDER-OK')
PY
```
**Pass when**: prints `GATE-ORDER-OK`.
**On fail**: regenerate `publish.py` via `--force-templates` or hand-merge the canonical order.

### CHECK-26 [MAJOR] No bare `git push` outside `publish.py`
**Why**: Canonical pipeline contract: only `publish.py` may push. A migration that re-introduces a bare `git push` workflow step bypasses every gate.
**Verify**:
```bash
! grep -rnE '^[[:space:]]*-?[[:space:]]*run:[[:space:]]*.*\bgit[[:space:]]+push\b' .github/workflows/ 2>/dev/null
```
**Pass when**: exit 0.
**On fail**: replace direct `git push` with `uv run python scripts/publish.py --patch` (or equivalent).

---

## Category 5 — `plugin.json` (CHECK-27..31)

### CHECK-27 [BLOCKER] `.claude-plugin/plugin.json` parses as valid JSON
**Why**: Without parseable plugin.json, every CPV validator fails fast and the CC loader silently disables the plugin.
**Verify**:
```bash
uv run python -c "import json,pathlib; json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text()); print('OK')"
```
**Pass when**: prints `OK`.
**On fail**: read the JSON syntax error, fix the offending byte.

### CHECK-28 [MAJOR] `plugin.json.name` matches the GitHub repo name
**Why**: Layout C plugins (`marketplace.json` self-entry) and the marketplace notification chain key off `name == repo`. Drift causes the marketplace lookup to silently miss this plugin.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, subprocess, sys
name = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text()).get('name')
remote = subprocess.run(['git','config','--get','remote.origin.url'], capture_output=True, text=True).stdout.strip()
if not remote: sys.exit(0)
repo = remote.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git')
if name != repo:
    print(f'MISMATCH plugin.name={name!r} repo={repo!r}'); sys.exit(1)
print('OK')
PY
```
**Pass when**: prints `OK`.
**On fail**: rename one or the other. Most often `plugin.json.name` should follow the repo.

### CHECK-29 [MAJOR] `cpv.allow_root_dirs` covers every non-standard rooted dir with content
**Why**: Issue #21 row 7. ai-maestro-janitor had `INPUT_DEV/` (1.6 GB) gitignored but populated; without `cpv.allow_root_dirs`, CPV emits MAJOR for unknown root dirs.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, sys
STANDARD = {'.claude','.claude-plugin','.git','.github','.githooks','agents','commands','skills','rules','servers',
            'scripts','tests','templates','assets','references','hooks','docs','design','reports','reports_dev',
            'docs_dev','scripts_dev','samples_dev','examples_dev','tests_dev','downloads_dev','libs_dev','builds_dev',
            'node_modules','.venv','.mypy_cache','.pytest_cache','.ruff_cache','.serena','.trashcan','.janitor','.local',
            '.rechecker','dist','build','target','vendor','reviews','git-hooks'}
allow = set()
try:
    cfg = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text()).get('cpv',{}).get('allow_root_dirs') or []
    allow.update(cfg)
except Exception: pass
fails=[]
for p in sorted(pathlib.Path('.').iterdir()):
    if not p.is_dir(): continue
    name = p.name
    if name in STANDARD or name.startswith('.'): continue
    if name in allow: continue
    if any(p.iterdir()):
        fails.append(name)
for f in fails: print('UNKNOWN-ROOT-DIR', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add the listed dirs to `cpv.allow_root_dirs` in `plugin.json` OR move them under a standard dir. See issue #21 row 7.

### CHECK-30 [MINOR] `plugin.json.version` is bumped if any tracked file changed since the last tag
**Why**: Skipping the version bump pushes content under the previous tag, which the marketplace caches.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, subprocess, sys
v = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text()).get('version')
last = subprocess.run(['git','describe','--tags','--abbrev=0'], capture_output=True, text=True).stdout.strip()
if not last or not v: sys.exit(0)
expected = 'v' + v
if last == expected:
    diff = subprocess.run(['git','diff','--name-only',last,'HEAD'], capture_output=True, text=True).stdout.strip()
    if diff:
        print('STALE-VERSION', last, 'has changes since tag:', diff.split('\n')[:3]); sys.exit(1)
print('OK')
PY
```
**Pass when**: prints `OK`.
**On fail**: bump version via `uv run python scripts/publish.py --patch`.

### CHECK-31 [MINOR] No unknown top-level keys in `plugin.json`
**Why**: Migration that adds an unknown top-level key (typo'd `agentss`) is invisible to CC at load time. CPV's strict validate flags these.
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  bad=[f for f in d.get('findings',[]) if 'plugin.json' in f.get('message','') and 'unknown' in f.get('message','').lower()]; \
  [print(b.get('message','')[:200]) for b in bad]; sys.exit(0 if not bad else 1)"
```
**Pass when**: exit 0.
**On fail**: remove or rename the unknown key. See `skills/cpv-fix-validation/references/plugin-error-index.md`.

---

## Category 6 — `.gitignore` + dev folders (CHECK-32..36)

### CHECK-32 [BLOCKER] `/reports/` AND `/reports_dev/` listed in `.gitignore`
**Why**: Reports contain private data (paths, tokens, internal notes). Per `~/.claude/rules/agent-reports-location.md` both MUST be gitignored.
**Verify**:
```bash
grep -qxF '/reports/' .gitignore && grep -qxF '/reports_dev/' .gitignore
```
**Pass when**: exit 0.
**On fail**: append both lines to `.gitignore`.

### CHECK-33 [MAJOR] `/.trashcan/` listed in `.gitignore`
**Why**: `ai-maestro-janitor:janitor-safe-delete` writes `claude-plugins-validation/.trashcan/`. Per CPV memory: this directory must never be committed.
**Verify**:
```bash
grep -qxF '/.trashcan/' .gitignore
```
**Pass when**: exit 0.
**On fail**: append `/.trashcan/` to `.gitignore`.

### CHECK-34 [MINOR] All `*_dev/` folders gitignored when present
**Why**: Per CLAUDE.md RULE 0 — `docs_dev`, `scripts_dev`, `samples_dev`, etc. exist as the dev-counterpart of standard folders and are never committed.
**Verify**:
```bash
uv run python - <<'PY'
import pathlib, sys
expected = ['docs_dev','scripts_dev','samples_dev','examples_dev','tests_dev','downloads_dev','libs_dev','builds_dev','reports_dev']
gi = pathlib.Path('.gitignore').read_text() if pathlib.Path('.gitignore').exists() else ''
fails=[]
for d in expected:
    if pathlib.Path(d).exists() and f'/{d}/' not in gi and f'{d}/' not in gi:
        fails.append(d)
for f in fails: print('NOT-IGNORED', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add the missing entries to `.gitignore`.

### CHECK-35 [MINOR] No tracked file under any `*_dev/` directory
**Why**: A migration accidentally `git add`-ed a dev artefact in a previous commit. `git ls-files` will reveal it.
**Verify**:
```bash
! git ls-files | grep -E '^(docs_dev|scripts_dev|samples_dev|examples_dev|tests_dev|downloads_dev|libs_dev|builds_dev|reports_dev)/'
```
**Pass when**: exit 0.
**On fail**: `git rm --cached <file>` (do NOT delete from disk per RULE 0); commit; re-run.

### CHECK-36 [MINOR] No tracked `.cpv-*-hashes.json` cache files
**Why**: Both `.cpv-cisco-scan.json` and `.cpv-self-hashes.json` are autogenerated and should be gitignored per cache hygiene.
**Verify**:
```bash
! git ls-files | grep -E '^\.cpv-(cisco-scan|self-hashes|.*-hashes)\.json$'
```
**Pass when**: exit 0.
**On fail**: `git rm --cached <file>`; add to `.gitignore`; commit; re-run.

---

## Category 7 — CPV self-validate clean (CHECK-37..41)

### CHECK-37 [BLOCKER] `validate_plugin.py --strict --json` returns zero CRITICAL findings
**Why**: This is the canonical correctness gate.
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  c=sum(1 for f in d.get('findings',[]) if f.get('severity')=='CRITICAL'); \
  print(f'CRITICAL={c}'); sys.exit(0 if c==0 else 1)"
```
**Pass when**: prints `CRITICAL=0`.
**On fail**: invoke `/cpv-fix-validation` with the report.

### CHECK-38 [BLOCKER] `validate_plugin.py --strict --json` returns zero MAJOR findings
**Why**: MAJOR findings include broken-glob/literal-path detections (post issue-#21 implementation).
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  m=sum(1 for f in d.get('findings',[]) if f.get('severity')=='MAJOR'); \
  print(f'MAJOR={m}'); sys.exit(0 if m==0 else 1)"
```
**Pass when**: prints `MAJOR=0`.
**On fail**: read the report, address each MAJOR. See `skills/cpv-fix-validation/references/plugin-error-index.md`.

### CHECK-39 [MAJOR] `validate_plugin.py --strict --json` returns zero MINOR findings
**Why**: MINORs are advisory but accumulate post-migration; ignoring them creates technical debt that the next migration agent re-flags.
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  m=sum(1 for f in d.get('findings',[]) if f.get('severity')=='MINOR'); \
  print(f'MINOR={m}'); sys.exit(0 if m==0 else 1)"
```
**Pass when**: prints `MINOR=0`.
**On fail**: address each MINOR. May be deferred with explicit user override.

### CHECK-40 [MINOR] `validate_plugin.py --strict --json` returns zero NIT findings
**Why**: NITs are pure cosmetics. Not blocking, but leaving them unaddressed makes future migration audits harder.
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  n=sum(1 for f in d.get('findings',[]) if f.get('severity')=='NIT'); \
  print(f'NIT={n}'); sys.exit(0 if n==0 else 1)"
```
**Pass when**: prints `NIT=0`.
**On fail**: address each NIT or document a waiver.

### CHECK-41 [MAJOR] WARNING findings other than `RC-PIPELINE-DRIFT-001` are zero
**Why**: `RC-PIPELINE-DRIFT-001` is the post-migration "you have customizations" advisory. Other WARNINGs (e.g. `RC-LEGACY-PIPELINE-001`) signal real drift.
**Verify**:
```bash
uv run python scripts/validate_plugin.py . --strict --json 2>/dev/null | \
  uv run python -c "import json,sys; d=json.load(sys.stdin); \
  w=[f for f in d.get('findings',[]) if f.get('severity')=='WARNING' and 'RC-PIPELINE-DRIFT-001' not in f.get('message','')]; \
  print(f'OTHER-WARN={len(w)}'); \
  [print(' -', f.get('message','')[:120]) for f in w]; \
  sys.exit(0 if not w else 1)"
```
**Pass when**: prints `OTHER-WARN=0`.
**On fail**: each non-drift WARNING must be resolved. Maps to issue #21 ask #1 "treat any new MAJOR/MINOR as failure of the agent's contract".

---

## Category 8 — Canonical-template parity (CHECK-42..46)

### CHECK-42 [MAJOR] Every canonical template path in `_CANONICAL_PIPELINE_FILES` exists on disk
**Why**: A missing canonical file (e.g. `cliff.toml`, `.markdownlint.json`) means publish.py cannot run a gate that depends on it.
**Verify**:
```bash
uv run python - <<'PY'
import sys, pathlib
sys.path.insert(0, 'scripts')
from validate_plugin import _CANONICAL_PIPELINE_FILES
fails = []
for rel, _gen in _CANONICAL_PIPELINE_FILES:
    if not pathlib.Path(rel).exists():
        fails.append(rel)
for f in fails: print('MISSING-CANON', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: re-run `uvx cpv-remote-validate standardize . --fix` to scaffold the missing files.

### CHECK-43 [MINOR] Drifted canonical files are listed in `cpv.template_overrides` (explicit waiver)
**Why**: Some plugins legitimately deviate (custom matrix, custom secrets handling). Without the explicit waiver, the drift WARNING re-fires every audit.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, re, subprocess, sys
try:
    cfg = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text())
    overrides = cfg.get('cpv',{}).get('template_overrides') or []
except Exception: overrides = []
res = subprocess.run(['uv','run','python','scripts/validate_plugin.py','.','--strict','--json'],
                    capture_output=True, text=True)
try: d = json.loads(res.stdout)
except Exception: sys.exit(0)
drifted = []
for f in d.get('findings',[]):
    msg = f.get('message','')
    if 'RC-PIPELINE-DRIFT-001' in msg:
        m = re.search(r'standard in: ([^.]+?)\. Run', msg)
        if m: drifted.extend([s.strip() for s in m.group(1).split(',')])
fails = [d for d in drifted if d not in overrides]
for f in fails: print('UNDECLARED-DRIFT', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add the drifted paths to `cpv.template_overrides` in `plugin.json` OR run `--force-templates` to re-sync.

### CHECK-44 [MAJOR] No legacy bash scripts (`*.sh`) shadow ported Python equivalents
**Why**: Issue #21 root cause. Migration ported scripts to Python but left `.sh` shadows that the workflow YAML still globs.
**Verify**:
```bash
uv run python - <<'PY'
import pathlib, sys
LEGACY = {'release.sh','lint.sh','test.sh','dispatch.sh','bump_version.sh'}
fails=[]
for d in ('scripts','git-hooks','.githooks'):
    base = pathlib.Path(d)
    if not base.is_dir(): continue
    for p in base.rglob('*.sh'):
        if p.name in LEGACY:
            stem_py = p.with_suffix('.py')
            if stem_py.exists():
                fails.append(str(p))
for f in fails: print('SHADOWED-SH', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: delete the `.sh` shadow (after committing per RULE 0). See issue #21 root cause.

### CHECK-45 [MINOR] `cpv_network_resilience.py` exists at `scripts/`
**Why**: `publish.py` imports it as a soft dependency. Missing → runtime warning every gate.
**Verify**:
```bash
test -f scripts/cpv_network_resilience.py
```
**Pass when**: exit 0.
**On fail**: re-run `--force-templates` or copy the canonical file from `scripts/generate_plugin_repo.py:gen_cpv_network_resilience_py`.

### CHECK-46 [MINOR] `git-hooks/pre-push` is executable (or absent)
**Why**: A non-executable `pre-push` is silently ignored by git. Migration that touched permissions (or ran on Windows) loses the +x bit.
**Verify**:
```bash
test ! -e git-hooks/pre-push || test -x git-hooks/pre-push
```
**Pass when**: exit 0.
**On fail**: `chmod +x git-hooks/pre-push`. Then `uv run python scripts/publish.py --install-hook` to register it.

---

## Category 9 — Tests (CHECK-47..51)

### CHECK-47 [BLOCKER] `pytest tests/ -x -q` exits 0
**Why**: A migration that breaks a test breaks the canonical gate-1 of publish.py.
**Verify**:
```bash
uv run pytest tests/ -x -q 2>&1 | tail -5
```
**Pass when**: exit 0; last line includes `passed`.
**On fail**: read the failing test output, fix.

### CHECK-48 [MAJOR] No new `pytest.mark.skip` or `pytest.mark.xfail` introduced by the migration commit
**Why**: A common migration shortcut: skip a failing test instead of fixing it. Catches that.
**Verify**:
```bash
uv run python - <<'PY'
import subprocess, sys
diff = subprocess.run(['git','diff','--unified=0','HEAD~1','HEAD','--','tests/'],
                     capture_output=True, text=True).stdout
fails=[l.strip() for l in diff.splitlines()
       if l.startswith('+') and any(p in l for p in ('@pytest.mark.skip','@pytest.mark.xfail','pytestmark = pytest.mark.skip'))]
for f in fails: print('SKIP-ADDED', f[:200])
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: revert the skip; fix the underlying test.

### CHECK-49 [MAJOR] Test count did NOT regress >5% vs the previous tag
**Why**: A migration that silently deletes tests (or merges them into a single passthrough) hides regressions.
**Verify**:
```bash
uv run python - <<'PY'
import re, subprocess, sys
last = subprocess.run(['git','describe','--tags','--abbrev=0'], capture_output=True, text=True).stdout.strip()
if not last: sys.exit(0)
cur = subprocess.run(['uv','run','pytest','tests/','--collect-only','-q'], capture_output=True, text=True).stdout
m = re.search(r'(\d+)\s+test', cur)
n_cur = int(m.group(1)) if m else 0
prev_files = subprocess.run(['git','ls-tree','-r','--name-only',last,'tests/'],
                           capture_output=True, text=True).stdout.splitlines()
n_prev = 0
for f in prev_files:
    if not f.endswith('.py'): continue
    src = subprocess.run(['git','show',f'{last}:{f}'], capture_output=True, text=True).stdout
    n_prev += len(re.findall(r'^\s*def\s+test_', src, re.M))
print(f'cur={n_cur} prev={n_prev}')
if n_prev and n_cur < n_prev * 0.95:
    sys.exit(1)
PY
```
**Pass when**: exit 0; current count ≥ 95% of previous.
**On fail**: investigate the missing tests.

### CHECK-50 [MINOR] No `def test_` function with `time.sleep(>=1)` lacks `@pytest.mark.slow`
**Why**: Per the user's preferences (CLAUDE.md): slow tests deserve the snail emoji marker. CI can split them off.
**Verify**:
```bash
uv run python - <<'PY'
import ast, pathlib, sys
fails=[]
for p in sorted(pathlib.Path('tests').rglob('test_*.py')):
    try: tree = ast.parse(p.read_text())
    except SyntaxError: continue
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
            slow = False
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if sub.func.attr == 'sleep':
                        slow = True
            if slow:
                decs = [ast.unparse(d) for d in node.decorator_list]
                if not any('slow' in d for d in decs):
                    fails.append((str(p), node.name))
for f in fails[:5]: print('UNMARKED-SLOW', *f, sep=' | ')
sys.exit(0)
PY
```
**Pass when**: exit 0 (advisory only — never blocks).
**On fail**: add `@pytest.mark.slow` decorator to long-running tests.

### CHECK-51 [MAJOR] No new `Mock(...)` / `@patch(...)` introduced by the migration commit
**Why**: Per CLAUDE.md: "Do not use mockup tests or mocked behaviours unless absolutely impossible." Migration that introduces mocks instead of fixing real flakiness violates this.
**Verify**:
```bash
uv run python - <<'PY'
import subprocess, sys
diff = subprocess.run(['git','diff','--unified=0','HEAD~1','HEAD','--','tests/'],
                     capture_output=True, text=True).stdout
suspect = [l for l in diff.splitlines()
           if l.startswith('+') and ('Mock(' in l or '@patch(' in l or 'mocker.' in l)]
for s in suspect[:10]: print('NEW-MOCK', s.strip()[:120])
sys.exit(0 if not suspect else 1)
PY
```
**Pass when**: exit 0.
**On fail**: review each new mock; replace with real test if possible.

---

## Category 10 — Git state (CHECK-52..56)

### CHECK-52 [BLOCKER] Working tree is clean before publish
**Why**: `publish.py` Gate 0 refuses to run on a dirty tree. Committing the migration changes is part of the migration contract.
**Verify**:
```bash
test -z "$(git status --porcelain)"
```
**Pass when**: exit 0.
**On fail**: stage and commit the changes (or stash) before running `publish.py`.

### CHECK-53 [MAJOR] No untracked files in `scripts/`
**Why**: Untracked files in `scripts/` could be a half-migrated artefact; CC plugin loader silently treats them as live.
**Verify**:
```bash
test -z "$(git ls-files --others --exclude-standard scripts/)"
```
**Pass when**: exit 0.
**On fail**: either `git add` or move into `scripts_dev/` per RULE 0.

### CHECK-54 [MAJOR] No untracked files in `tests/`
**Why**: Same as CHECK-53.
**Verify**:
```bash
test -z "$(git ls-files --others --exclude-standard tests/)"
```
**Pass when**: exit 0.
**On fail**: `git add` or move to `tests_dev/` per RULE 0.

### CHECK-55 [MAJOR] No untracked files in `.github/`
**Why**: A half-applied workflow YAML in `.github/workflows/` not yet committed runs on next push but doesn't show in `git log`.
**Verify**:
```bash
test -z "$(git ls-files --others --exclude-standard .github/)"
```
**Pass when**: exit 0.
**On fail**: `git add .github/<file>` and commit.

### CHECK-56 [MINOR] No tracked `rck-*-merge-pending.md` files at the repo root
**Why**: These are recheck artefacts. Per CLAUDE.md they belong in `reports_dev/`, not the repo root.
**Verify**:
```bash
! git ls-files --error-unmatch 'rck-*-merge-pending.md' 2>/dev/null
```
**Pass when**: exit 0.
**On fail**: `git mv rck-*.md reports_dev/recheck/` and commit.

---

## Category 11 — Smoke-test publish (CHECK-57..61)

### CHECK-57 [BLOCKER] `publish.py --dry-run` exits 0
**Why**: Issue #21 ask #1 — "the migration agent's exit contract should be CI passes on next push". `--dry-run` exercises every gate without actually pushing.
**Verify**:
```bash
uv run python scripts/publish.py --dry-run 2>&1 | tee /tmp/publish-dryrun.log
grep -qE '(Gate [1-4]: PASS|All gates passed|Dry run complete|✓ All checks passed)' /tmp/publish-dryrun.log
```
**Pass when**: every gate prints PASS.
**On fail**: read `/tmp/publish-dryrun.log`. The gate that failed is the migration's residual issue.

### CHECK-58 [BLOCKER] Tag's CI workflow run goes green (issue #21 ask #1 contract)
**Why**: This is the explicit issue #21 contract. After the migration agent declares DONE, the next push must result in a green CI run. Run a sandboxed `publish.py` to a test branch, watch the Actions run.
**Verify**:
```bash
# Run on a test branch, do NOT push to main
TEST_BRANCH="test/migration-smoke-$(date +%s)"
git checkout -b "$TEST_BRANCH"
uv run python scripts/publish.py --patch --skip-tag 2>&1 | tee /tmp/publish-smoke.log
git push -u origin "$TEST_BRANCH"
RUN_ID="$(gh run list --branch "$TEST_BRANCH" --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID" --exit-status
```
**Pass when**: `gh run watch --exit-status` exits 0.
**On fail**: open the failing job log via `gh run view "$RUN_ID" --log-failed`. Address the failure and re-run the smoke.

### CHECK-59 [MAJOR] `publish.py --gate` exits 0 (the pre-push 4-gate)
**Why**: This is the gate that runs inside the git pre-push hook. If it fails locally, every push fails locally.
**Verify**:
```bash
uv run python scripts/publish.py --gate 2>&1 | tail -10
```
**Pass when**: exit 0; last lines show all gates PASS.
**On fail**: identify the failing gate and address it.

### CHECK-60 [MAJOR] `publish.py --install-hook` succeeds (idempotent)
**Why**: The post-migration plugin must be able to install its own pre-push hook. If the hook generation logic regresses, the user can't push.
**Verify**:
```bash
uv run python scripts/publish.py --install-hook 2>&1 | tail -5
```
**Pass when**: exit 0; output confirms hook installed.
**On fail**: read the traceback. Most often: missing `git-hooks/pre-push`.

### CHECK-61 [MAJOR] `publish.py --print-gates` enumerates ≥4 gates
**Why**: A migration that drops a gate is a silent test-coverage regression.
**Verify**:
```bash
test "$(uv run python scripts/publish.py --print-gates 2>&1 | grep -cE '^Gate [0-9]+:')" -ge 4
```
**Pass when**: prints `4` or more.
**On fail**: re-run `--force-templates` to regenerate publish.py.

---

## Category 12 — Marketplace (CHECK-62..65)

### CHECK-62 [MAJOR] If Layout C, `marketplace.json` self-entry has the SAME version as `plugin.json`
**Why**: Per CPV memory: Layout C requires synced version on triple-bump. Out-of-sync versions cause marketplace cache thrash.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, sys
plug = pathlib.Path('.claude-plugin/plugin.json')
mp = pathlib.Path('.claude-plugin/marketplace.json')
if not mp.exists(): sys.exit(0)
pv = json.loads(plug.read_text()).get('version')
mpd = json.loads(mp.read_text())
top = mpd.get('metadata',{}).get('version') or mpd.get('version')
self_ent = next((p for p in mpd.get('plugins',[]) if (p.get('source') or '').startswith('./')), None)
sv = (self_ent or {}).get('version')
if not (pv == top == sv):
    print(f'VERSION-MISMATCH plugin={pv} marketplace.metadata={top} marketplace.self={sv}')
    sys.exit(1)
print('OK')
PY
```
**Pass when**: prints `OK`.
**On fail**: re-run `publish.py --patch` (it does triple-bump on Layout C).

### CHECK-63 [MAJOR] If Layout C, `validate_marketplace.py --strict` exits 0
**Why**: Marketplace validation is gate 4 of `publish.py` on Layout B/C. Failure here breaks the release pipeline.
**Verify**:
```bash
test ! -f .claude-plugin/marketplace.json || \
  uv run python scripts/validate_marketplace.py . --strict
```
**Pass when**: exit 0.
**On fail**: `/cpv-fix-marketplace-validation` against the report.

### CHECK-64 [MINOR] If Layout A, plugin is registered in the upstream marketplace (manual confirmation)
**Why**: A Layout A plugin that's not in the marketplace can't be installed via `/plugin install`.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, sys
try:
    cfg = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text())
except Exception: sys.exit(0)
upstream = cfg.get('metadata',{}).get('upstream_marketplace')
if not upstream: sys.exit(0)
print(f'CHECK-MANUALLY: visit {upstream} and verify {cfg.get("name")} is listed')
PY
```
**Pass when**: prints advisory line.
**On fail**: open a PR to the upstream marketplace.

### CHECK-65 [MINOR] Marketplace CI workflow on the upstream repo last run was green (Layout A only)
**Why**: A Layout A plugin's marketplace pipeline is hosted upstream — if upstream CI is red, marketplace updates won't propagate.
**Verify**:
```bash
echo "CHECK-MANUALLY: gh run list --repo <upstream> --workflow validate-marketplace.yml --limit 1"
```
**Pass when**: manual confirmation.
**On fail**: investigate upstream marketplace CI.

---

## Category 13 — Notification chain (CHECK-66..69)

### CHECK-66 [MAJOR] `.github/workflows/notify-marketplace.yml` exists (or Layout C waiver applies)
**Why**: Without this workflow, the upstream marketplace never learns about the new tag.
**Verify**:
```bash
test -f .claude-plugin/marketplace.json || test -f .github/workflows/notify-marketplace.yml
```
**Pass when**: exit 0 (Layout C waives this; otherwise file exists).
**On fail**: re-run `--force-templates` to regenerate.

### CHECK-67 [MAJOR] `notify-marketplace.yml` references a notify-token secret
**Why**: Without the secret, the workflow fails silently with an unauthenticated `gh` API call.
**Verify**:
```bash
test ! -f .github/workflows/notify-marketplace.yml || \
  grep -qE 'secrets\.(MARKETPLACE_NOTIFY_TOKEN|MARKETPLACE_PAT|MARKETPLACE_TOKEN)' .github/workflows/notify-marketplace.yml
```
**Pass when**: exit 0.
**On fail**: add the secret reference (and configure it in repo Settings).

### CHECK-68 [MAJOR] Repo's secrets actually include the notify token (Layout A, B)
**Why**: Even if the workflow YAML references the secret, if the secret isn't actually stored, the call fails.
**Verify**:
```bash
test ! -f .github/workflows/notify-marketplace.yml || \
  gh secret list --json name --jq '.[].name' 2>/dev/null | grep -qE '^(MARKETPLACE_NOTIFY_TOKEN|MARKETPLACE_PAT|MARKETPLACE_TOKEN)$'
```
**Pass when**: exit 0.
**On fail**: `gh secret set MARKETPLACE_NOTIFY_TOKEN`.

### CHECK-69 [MINOR] notify-marketplace.yml's last run was successful (where applicable)
**Why**: End-to-end smoke of the notification chain.
**Verify**:
```bash
test ! -f .github/workflows/notify-marketplace.yml || \
  gh run list --workflow notify-marketplace.yml --limit 1 --json conclusion --jq '.[0].conclusion // "none"' | \
  grep -qE '^(success|none)$'
```
**Pass when**: exit 0 (last run was success or not yet run).
**On fail**: read the failed run via `gh run view`.

---

## Category 14 — `hooks/hooks.json` (CHECK-70..74)

### CHECK-70 [BLOCKER] `hooks/hooks.json` parses as JSON (if exists)
**Why**: A malformed hooks.json silently disables every hook for the plugin.
**Verify**:
```bash
test ! -f hooks/hooks.json || \
  uv run python -c "import json,pathlib; json.loads(pathlib.Path('hooks/hooks.json').read_text()); print('OK')"
```
**Pass when**: exit 0.
**On fail**: open the file, fix the JSON syntax error.

### CHECK-71 [MAJOR] Every event in `hooks.json.hooks` is in CC's valid 30-event set
**Why**: A typo'd event ('PreToolUSE') is silently ignored by CC.
**Verify**:
```bash
test ! -f hooks/hooks.json || uv run python - <<'PY'
import json, pathlib, sys
# Keep this set in sync with the single source of truth:
# scripts/cpv_validation_common.py::VALID_HOOK_EVENTS. It is re-hardcoded here
# (not imported) because the snippet runs inside the *target* plugin being
# migrated, where cpv_validation_common is not importable. A stale subset would
# false-FAIL legitimate events (e.g. 'Setup' is legacy-but-accepted, and
# 'MessageDisplay' is a real v2.1.152 event) — both must appear below.
VALID = {'PreToolUse','PostToolUse','PostToolUseFailure','PostToolBatch','PermissionRequest','PermissionDenied',
         'UserPromptSubmit','UserPromptExpansion','Notification','Stop','StopFailure','SubagentStop','SubagentStart',
         'SessionStart','SessionEnd','PreCompact','PostCompact','TeammateIdle','TaskCompleted','TaskCreated',
         'ConfigChange','WorktreeCreate','WorktreeRemove','InstructionsLoaded','Elicitation','ElicitationResult',
         'CwdChanged','FileChanged','Setup','MessageDisplay'}
hj = pathlib.Path('hooks/hooks.json')
if not hj.exists(): sys.exit(0)
cfg = json.loads(hj.read_text())
fails=[ev for ev in (cfg.get('hooks') or {}) if ev not in VALID]
for f in fails: print('UNKNOWN-EVENT', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: rename the event per CPV's `VALID_HOOK_EVENTS` 30-event list.

### CHECK-72 [BLOCKER] Every `command:` referenced by `hooks.json` resolves to an existing file
**Why**: Issue-#21-class breakage. Migration that ports a hook to `.py` but leaves the JSON pointing at the `.sh` shadow.
**Verify**:
```bash
test ! -f hooks/hooks.json || uv run python - <<'PY'
import json, pathlib, re, shlex, sys
hj = pathlib.Path('hooks/hooks.json')
if not hj.exists(): sys.exit(0)
cfg = json.loads(hj.read_text())
fails=[]
for ev, entries in (cfg.get('hooks') or {}).items():
    for entry in entries:
        for h in (entry.get('hooks') or []):
            cmd = h.get('command') or ''
            try: tokens = shlex.split(cmd, posix=True)
            except ValueError: tokens = cmd.split()
            for tok in tokens:
                if re.match(r'^(scripts|\.githooks|git-hooks|hooks)/', tok):
                    if not pathlib.Path(tok).exists():
                        fails.append((ev, tok))
for f in fails: print('NO-CMD-FILE', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: update the `command:` to point at the migrated `.py` file.

### CHECK-73 [MAJOR] No `disableAllHooks: true` regression in `hooks.json`
**Why**: A migration that silently sets `disableAllHooks: true` (e.g. as a debugging shortcut) makes the entire plugin's hooks dead.
**Verify**:
```bash
test ! -f hooks/hooks.json || ! grep -q '"disableAllHooks"[[:space:]]*:[[:space:]]*true' hooks/hooks.json
```
**Pass when**: exit 0.
**On fail**: remove the `disableAllHooks: true` line.

### CHECK-74 [MINOR] Every hook entry has a `description` field
**Why**: Per CC plugin spec, a hook without description is opaque to the user. Migration regressions hide here.
**Verify**:
```bash
test ! -f hooks/hooks.json || uv run python - <<'PY'
import json, pathlib, sys
hj = pathlib.Path('hooks/hooks.json')
if not hj.exists(): sys.exit(0)
cfg = json.loads(hj.read_text())
fails=[]
for ev, entries in (cfg.get('hooks') or {}).items():
    for i, entry in enumerate(entries):
        for j, h in enumerate(entry.get('hooks') or []):
            if not h.get('description'):
                fails.append((ev, i, j))
for f in fails: print('NO-DESC', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add `description` to each hook.

---

## Category 15 — MCP servers (CHECK-75..78)

### CHECK-75 [BLOCKER] `.mcp.json` parses as JSON (if exists)
**Why**: A malformed `.mcp.json` makes the entire MCP integration silently fail at session start.
**Verify**:
```bash
test ! -f .mcp.json || \
  uv run python -c "import json,pathlib; json.loads(pathlib.Path('.mcp.json').read_text()); print('OK')"
```
**Pass when**: exit 0.
**On fail**: fix the JSON.

### CHECK-76 [BLOCKER] Every `command:` in `.mcp.json` resolves to an existing executable
**Why**: A migration that moved an MCP server entry point from `servers/foo.js` to `servers/foo.py` but didn't update `.mcp.json` produces a server that fails at first session.
**Verify**:
```bash
test ! -f .mcp.json || uv run python - <<'PY'
import json, pathlib, shutil, sys
cfg = json.loads(pathlib.Path('.mcp.json').read_text())
fails=[]
for name, srv in (cfg.get('mcpServers') or {}).items():
    cmd = srv.get('command')
    if not cmd: continue
    if cmd.startswith('/') or '/' in cmd:
        if not pathlib.Path(cmd).exists(): fails.append((name, cmd))
    else:
        if not shutil.which(cmd): fails.append((name, cmd))
for f in fails: print('NO-MCP-CMD', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: update the `.mcp.json` `command:` to the post-migration path.

### CHECK-77 [MAJOR] Every required `env:` var in `.mcp.json` is documented in `README.md`
**Why**: Users need to know which env vars to set. Migration that adds a server but forgets to document its env vars confuses every new user.
**Verify**:
```bash
test ! -f .mcp.json || uv run python - <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path('.mcp.json').read_text())
readme = pathlib.Path('README.md').read_text() if pathlib.Path('README.md').exists() else ''
fails=[]
for name, srv in (cfg.get('mcpServers') or {}).items():
    for k in (srv.get('env') or {}):
        if k not in readme:
            fails.append((name, k))
for f in fails: print('UNDOCUMENTED-ENV', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add a section to README.md listing the env vars.

### CHECK-78 [MINOR] Bundled MCP server executables live in `servers/` (per CPV convention)
**Why**: Per CPV memory: `servers/` is the canonical location; flat `.mcp.json` references at the root are deprecated.
**Verify**:
```bash
test ! -f .mcp.json || uv run python - <<'PY'
import json, pathlib, sys
cfg = json.loads(pathlib.Path('.mcp.json').read_text())
fails=[]
for name, srv in (cfg.get('mcpServers') or {}).items():
    cmd = srv.get('command') or ''
    if cmd.startswith('./') and not cmd.startswith('./servers/'):
        fails.append((name, cmd))
for f in fails: print('NON-CANONICAL-LOC', *f, sep=' | ')
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: move the executable into `servers/` and update `.mcp.json`.

---

## Category 16 — Docs & changelog (CHECK-79..82)

### CHECK-79 [MAJOR] `README.md` lists every command in `commands/`
**Why**: Migration that adds a command but forgets to document it leaves users guessing.
**Verify**:
```bash
uv run python - <<'PY'
import pathlib, sys
readme = pathlib.Path('README.md').read_text() if pathlib.Path('README.md').exists() else ''
cmd_dir = pathlib.Path('commands')
fails=[]
if cmd_dir.is_dir():
    for c in sorted(cmd_dir.glob('*.md')):
        name = c.stem
        if name not in readme and f'/{name}' not in readme:
            fails.append(name)
for f in fails: print('UNDOC-CMD', f)
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: add the command to README.md's command list.

### CHECK-80 [MAJOR] `CHANGELOG.md` has an entry for the current `plugin.json.version`
**Why**: Migration that bumps version but forgets the changelog entry produces a silent release.
**Verify**:
```bash
uv run python - <<'PY'
import json, pathlib, re, sys
v = json.loads(pathlib.Path('.claude-plugin/plugin.json').read_text()).get('version','')
ch = pathlib.Path('CHANGELOG.md').read_text() if pathlib.Path('CHANGELOG.md').exists() else ''
if not v: sys.exit(0)
if not re.search(rf'^[#\[\(]?\s*v?{re.escape(v)}\b', ch, re.M):
    print(f'NO-CHANGELOG-ENTRY for v{v}')
    sys.exit(1)
print('OK')
PY
```
**Pass when**: prints `OK`.
**On fail**: add a changelog entry. `git-cliff` (canonical) generates it automatically; if missing, run `git-cliff -o CHANGELOG.md`.

### CHECK-81 [MINOR] README references the current marketplace install URL
**Why**: A post-migration plugin renamed or moved repos must update its install instructions.
**Verify**:
```bash
uv run python - <<'PY'
import pathlib, re, subprocess, sys
readme = pathlib.Path('README.md').read_text() if pathlib.Path('README.md').exists() else ''
url = subprocess.run(['git','config','--get','remote.origin.url'], capture_output=True, text=True).stdout.strip()
m = re.match(r'^(?:https://github.com/|git@github\.com:)([^/]+)/([^/.]+)', url)
if not m: sys.exit(0)
slug = f'{m.group(1)}/{m.group(2)}'
if readme and slug not in readme:
    print(f'STALE-INSTALL-URL: README does not reference {slug}')
    sys.exit(1)
print('OK')
PY
```
**Pass when**: prints `OK`.
**On fail**: update README's install instructions.

### CHECK-82 [MINOR] No `TODO`/`FIXME`/`XXX` comments added by the migration commit
**Why**: Migration shouldn't be a license to leave landmines.
**Verify**:
```bash
uv run python - <<'PY'
import subprocess, sys
diff = subprocess.run(['git','diff','--unified=0','HEAD~1','HEAD'],
                     capture_output=True, text=True).stdout
fails = [l for l in diff.splitlines()
         if l.startswith('+') and any(t in l for t in ('TODO','FIXME','XXX'))
         and not l.startswith('+++')]
for f in fails[:10]: print('NEW-TODO', f.strip()[:120])
sys.exit(0 if not fails else 1)
PY
```
**Pass when**: exit 0.
**On fail**: address each TODO before declaring DONE OR document in `design/tasks/TRDD-*.md`.

---

## Category 17 — CI-parity defects (#137-143) (CHECK-83..87)

These five checks close the dominant migration failure mode: an upgrade that passes `validate_plugin.py --strict` LOCALLY but RED-CIs on GitHub, because `validate_plugin` does NOT run the jscpd / actionlint / mypy / `uv sync --extra dev` gates the generated `ci.yml` Lint job runs, nor the 5 static #137-143 defect detectors. **All five rows are satisfied by ONE command** — `cpv-remote-validate ci-preflight <plugin-root>` runs the CIP-1..6 static checks (CIP-6 — the stale CPV `@main` ref — has no dedicated row; see the note after CHECK-87) AND the live parity gates and exits non-zero on any real (non-WARNING) finding. Run it once; each `CHECK-83..87` row PASSES when the corresponding CIP finding is absent.

**WARNING ≠ FAIL.** `ci-preflight` DEGRADES to a non-blocking WARNING when a tool is absent (no `npx`/jscpd/actionlint/mypy on the box) — that NEVER fails a check (the #129 degrade-gracefully pattern). A real over-threshold / static-defect / resolve-failure is the only non-zero exit. (A WARNING means the gate could not be locally verified — CI still enforces it; a green preflight does not guarantee green CI when a tool was absent.)

### CHECK-83 [BLOCKER] CIP-1 — `CLAUDE_PRIVATE_USERNAMES` is NOT inverted in any workflow (#140)
**Why**: A canon-generator regression set `CLAUDE_PRIVATE_USERNAMES: ${{ github.repository_owner }}` on the CPV-validate step — semantically inverted (that env lists PRIVATE usernames), so CPV flagged every owner GitHub URL + the owner's no-reply email as CRITICAL "private path leaked" → the downstream CI Validate job failed under `--strict`.
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0
```
**Pass when**: `ci-preflight` exits 0 with no `CIP-1` finding (the inverted env is absent from every `.github/workflows/*.yml`).
**On fail**: drop the `CLAUDE_PRIVATE_USERNAMES` line from the affected workflow's validate step (keep `PLUGIN_SKIP_GITHUB_INTEGRITY=1`); the public owner must never be in the private list, and a CI runner has no developer local-username to protect.

### CHECK-84 [MAJOR] CIP-2 — import-fallback shims carry `# type: ignore[no-redef, misc]` not bare `[no-redef]` (#142 Defect-1)
**Why**: The generated `publish.py` network-resilience shims (`gh_with_retry`/`git_with_retry`) used `# type: ignore[no-redef]`, but the downstream `mypy --strict` gate also needs `[misc]` (the conditional-variant non-identical-signature rule) → 12 MINORs blocked the adopting plugin's `--strict`.
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0
```
**Pass when**: `ci-preflight` exits 0 with no `CIP-2` finding (every conditional import-fallback shim carrying `[no-redef]` also carries `misc`).
**On fail**: change the shim's `# type: ignore[no-redef]` to `# type: ignore[no-redef, misc]` (idiomatic import-fallback, not a suppression).

### CHECK-85 [BLOCKER] CIP-3 — `[project.optional-dependencies].dev` exists when CI runs `uv sync --extra dev` (#142 Defect-2)
**Why**: The canon `ci.yml`/`release.yml` run `uv sync --extra dev`, but if `pyproject.toml` lacks a `[project.optional-dependencies].dev` table CI fails "Extra dev is not defined".
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0
```
**Pass when**: `ci-preflight` exits 0 with no `CIP-3` finding (the dev extra is present, or no workflow references it).
**On fail**: run `uvx cpv-remote-validate standardize . --fix` (auto-provisions `dev = pytest/ruff/mypy`), or add the table by hand + refresh the lockfile.

### CHECK-86 [MAJOR] CIP-4 — no superseded `validate.yml` alongside the consolidated `ci.yml` (#142 Defect-4)
**Why**: Adding the consolidated `ci.yml` (whose Validate job replaces the standalone `validate.yml`) but LEAVING `validate.yml` lets its pre-existing shellcheck SC2086 fail `ci.yml`'s actionlint Lint job.
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0
```
**Pass when**: `ci-preflight` exits 0 with no `CIP-4` finding (no CPV-shipped `validate.yml` survives next to `ci.yml`).
**On fail**: run `standardize --fix` (removes a CPV-shipped `validate.yml`, identity-guarded, safe-deleted to `scripts_dev/superseded-workflows/`) and re-point branch protection at the `ci.yml` Validate job.

### CHECK-87 [MAJOR] CIP-5 — `.jscpd.json` exists when `ci.yml` enables `COPYPASTE_JSCPD` (jscpd parity, #143)
**Why**: The generated `ci.yml` Mega-Linter Lint job enforces `COPYPASTE_JSCPD --threshold 5`; without a `.jscpd.json` single-source config, the publish gate ran `ruff` but NOT jscpd, so an adopter tagged + released then failed CI on jscpd.
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0
```
**Pass when**: `ci-preflight` exits 0 with no `CIP-5` finding (a `.jscpd.json` exists, or `ci.yml` does not enable jscpd). Note: `ci-preflight` ALSO runs jscpd itself when present (degrade-WARNING if absent), so an over-threshold duplication surfaces here too.
**On fail**: run `standardize --fix` (provisions `.jscpd.json`, never clobbering an existing one); the config is auto-discovered by both CI's Mega-Linter jscpd and the local `publish.py` Gate 2b.

### Note — CIP-6: stale/non-resolvable CPV `@main` ref (BLOCKER; ALSO enforced at publish Gate 3 since v2.148.0)

CIP-6 has **no dedicated CHECK row** (the matrix stays at 87 / CHECK-83..87), but the single `ci-preflight` command the CHECK-83..87 rows run ALSO executes it — so it is covered here, and it is the fix for the **dominant fleet-blocking failure**.

**Why**: A plugin migrated by an OLD CPV (≤v2.137, pre-#139) pins `git+https://github.com/Emasoft/claude-plugins-validation@main` in its `.github/workflows/*.yml`. CPV's default branch is `master`, so `@main` 404s on the runner (`uvx --from git+…@main` → `Git operation failed / Updating … (main)`) and the workflow RED-CIs forever. `ci-preflight` runs **CIP-6** (`cpv_ci_parity_checks.py::_check_stale_cpv_ref`), which fires MAJOR on any CPV ref that is not `master` / a `v<semver>` tag / a 7-40 hex SHA. Since **v2.148.0** the SAME rule is folded into `validate_plugin.py` (`validate_workflow_cpv_ref`, in the validator dispatch list), so it is enforced at **publish Gate 3** — `publish.py` now REFUSES to ship a `@main`-pinned pipeline even when the migrator never ran `ci-preflight` separately. (The downstream generator has emitted a resolvable `@v<version>` pin since #139, so freshly-generated plugins never trip this; it only catches the pre-#139 legacy migrations.)
**Verify**:
```bash
uv run python scripts/remote_validation.py ci-preflight . ; test $? -eq 0   # CIP-6 fires here
uv run python scripts/validate_plugin.py . --strict ; test $? -eq 0         # publish Gate 3 (v2.148.0) also blocks a stale ref
```
**Pass when**: no `.github/workflows/*.yml` pins a non-resolvable CPV ref (every `git+…/claude-plugins-validation@<ref>` is `@master` / `@v<semver>` / a SHA).
**On fail**: run `uvx --from git+https://github.com/Emasoft/claude-plugins-validation --with pyyaml cpv-remote-validate standardize . --fix` (the `repin_stale_cpv_ref` pass rewrites `@main`→`@v<version>`), then commit the workflow change.

---

## Issue-#21 row → check mapping

Each row from the failure table in [Issue #21](https://github.com/Emasoft/claude-plugins-validation/issues/21) maps to ≥1 check above.

| # | Issue #21 row                                                                                                | Mapped checks                                                                       |
| - | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| 1 | `.github/workflows/ci.yml` globs `scripts/{detectors,hooks,lib}/*.sh` (now empty); chmod/bash -n over empty globs | CHECK-02, CHECK-03, CHECK-44                                                        |
| 2 | `.github/workflows/release.yml` same drift category                                                          | CHECK-02, CHECK-03, CHECK-44                                                        |
| 3 | `.github/workflows/notify-marketplace.yml` same drift category                                               | CHECK-02, CHECK-03, CHECK-66, CHECK-67                                              |
| 4 | `.github/workflows/weekly-audit.yml` same drift category                                                     | CHECK-02, CHECK-03                                                                  |
| 5 | `scripts/publish.py` calls retired `cpv-remote-validate lint`                                                | CHECK-23, CHECK-24                                                                  |
| 6 | `cliff.toml` drift not investigated                                                                          | CHECK-42, CHECK-43                                                                  |
| 7 | `INPUT_DEV/` 1.6 GB user data; missing `cpv.allow_root_dirs`                                                 | CHECK-29                                                                            |
| 8 | Hooks (`scripts/hooks/*.py`): module-scope `sys.exit` + module-scope `import state`; missing `lib/__init__.py`; mypy `[no-redef]` | CHECK-09, CHECK-11, CHECK-12, CHECK-13, CHECK-14, CHECK-17, CHECK-18, CHECK-19      |

---

## Run-all script

Paste the function below into a shell or `source` this file's run-all section
(extracted between the `### run_all_checks` and `### END_RUN_ALL` markers) to
execute every check end-to-end with a Unicode-bordered summary.

```bash
### run_all_checks
run_all_checks() {
  local plugin_root="${1:-$PWD}"
  cd "$plugin_root" || { echo "ERR: cannot cd to $plugin_root"; return 2; }

  local ts; ts="$(date +%Y%m%d_%H%M%S%z)"
  local main_root; main_root="$(git worktree list 2>/dev/null | head -n1 | awk '{print $1}')"
  [ -z "$main_root" ] && main_root="$plugin_root"
  local out_dir="$main_root/reports/canonical-pipeline-migration"
  mkdir -p "$out_dir"
  local log="$out_dir/${ts}-run-all.log"
  local report="$out_dir/${ts}-run-all.md"

  # The full 87-check matrix (82 base + 5 CI-parity CHECK-83..87). Each entry: ID|SEVERITY|TITLE|CHECK_FN
  # CHECK_FN is the name of a shell function the orchestrator must define
  # (one per check); the function executes the snippet from this file and
  # returns 0 on PASS, non-zero on FAIL. Default cpv_check_NN are stubs that
  # mark every check PENDING (treated as FAIL) until wired up.
  local -a CHECKS=(
    "01|BLOCKER|Workflow YAMLs parse|cpv_check_01"
    "02|BLOCKER|Literal paths in run: exist|cpv_check_02"
    "03|BLOCKER|Globs in run: expand to >=1|cpv_check_03"
    "04|BLOCKER|No pull_request_target|cpv_check_04"
    "05|MAJOR|Top-level permissions least-priv|cpv_check_05"
    "06|MAJOR|Third-party uses pinned by SHA|cpv_check_06"
    "07|MAJOR|setup-X actions cache enabled|cpv_check_07"
    "08|BLOCKER|scripts/*.py ast.parse clean|cpv_check_08"
    "09|BLOCKER|No module-scope sys.exit|cpv_check_09"
    "10|MAJOR|PEP 723 block on 3rd-party imports|cpv_check_10"
    "11|MAJOR|3rd-party imports inside main()|cpv_check_11"
    "12|BLOCKER|__init__.py in every package dir|cpv_check_12"
    "13|MAJOR|Sibling imports use relative form|cpv_check_13"
    "14|MAJOR|mypy clean on scripts/|cpv_check_14"
    "15|MAJOR|ruff clean on scripts/|cpv_check_15"
    "16|MAJOR|pyright clean on scripts/|cpv_check_16"
    "17|BLOCKER|Hooks: __main__ guard|cpv_check_17"
    "18|BLOCKER|Hooks: no module-scope sys.exit(0)|cpv_check_18"
    "19|MAJOR|Hooks: state imports in main()|cpv_check_19"
    "20|MAJOR|Hooks referenced by hooks.json|cpv_check_20"
    "21|MINOR|Hooks have docstring|cpv_check_21"
    "22|BLOCKER|publish.py imports clean|cpv_check_22"
    "23|BLOCKER|publish.py uses plugin --strict|cpv_check_23"
    "24|BLOCKER|publish.py --print-gates exits 0|cpv_check_24"
    "25|BLOCKER|Gate sequence canonical|cpv_check_25"
    "26|MAJOR|No bare git push outside publish|cpv_check_26"
    "27|BLOCKER|plugin.json parses|cpv_check_27"
    "28|MAJOR|plugin.name == repo|cpv_check_28"
    "29|MAJOR|cpv.allow_root_dirs covers non-std|cpv_check_29"
    "30|MINOR|Version bumped if changes since tag|cpv_check_30"
    "31|MINOR|No unknown plugin.json keys|cpv_check_31"
    "32|BLOCKER|/reports/ + /reports_dev/ ignored|cpv_check_32"
    "33|MAJOR|/.trashcan/ in .gitignore|cpv_check_33"
    "34|MINOR|*_dev/ folders gitignored|cpv_check_34"
    "35|MINOR|No tracked file under *_dev/|cpv_check_35"
    "36|MINOR|No tracked .cpv-*-hashes.json|cpv_check_36"
    "37|BLOCKER|validate_plugin CRITICAL=0|cpv_check_37"
    "38|BLOCKER|validate_plugin MAJOR=0|cpv_check_38"
    "39|MAJOR|validate_plugin MINOR=0|cpv_check_39"
    "40|MINOR|validate_plugin NIT=0|cpv_check_40"
    "41|MAJOR|Non-DRIFT WARNINGs=0|cpv_check_41"
    "42|MAJOR|Canonical files exist|cpv_check_42"
    "43|MINOR|Drifts in template_overrides|cpv_check_43"
    "44|MAJOR|No legacy *.sh shadows|cpv_check_44"
    "45|MINOR|cpv_network_resilience.py exists|cpv_check_45"
    "46|MINOR|git-hooks/pre-push executable|cpv_check_46"
    "47|BLOCKER|pytest -x -q exits 0|cpv_check_47"
    "48|MAJOR|No new skip/xfail|cpv_check_48"
    "49|MAJOR|Test count >= 95% of prev tag|cpv_check_49"
    "50|MINOR|Slow tests marked|cpv_check_50"
    "51|MAJOR|No new mocks|cpv_check_51"
    "52|BLOCKER|Working tree clean|cpv_check_52"
    "53|MAJOR|No untracked in scripts/|cpv_check_53"
    "54|MAJOR|No untracked in tests/|cpv_check_54"
    "55|MAJOR|No untracked in .github/|cpv_check_55"
    "56|MINOR|No tracked rck-*.md at root|cpv_check_56"
    "57|BLOCKER|publish.py --dry-run green|cpv_check_57"
    "58|BLOCKER|Tag CI run green|cpv_check_58"
    "59|MAJOR|publish.py --gate green|cpv_check_59"
    "60|MAJOR|publish.py --install-hook green|cpv_check_60"
    "61|MAJOR|publish.py --print-gates >=4|cpv_check_61"
    "62|MAJOR|Layout C versions synced|cpv_check_62"
    "63|MAJOR|validate_marketplace --strict|cpv_check_63"
    "64|MINOR|Layout A: registered upstream|cpv_check_64"
    "65|MINOR|Upstream marketplace CI green|cpv_check_65"
    "66|MAJOR|notify-marketplace.yml exists|cpv_check_66"
    "67|MAJOR|notify-token reference present|cpv_check_67"
    "68|MAJOR|notify-token actually stored|cpv_check_68"
    "69|MINOR|notify last run green|cpv_check_69"
    "70|BLOCKER|hooks.json parses|cpv_check_70"
    "71|MAJOR|hooks.json events valid|cpv_check_71"
    "72|BLOCKER|hooks.json command paths exist|cpv_check_72"
    "73|MAJOR|No disableAllHooks regression|cpv_check_73"
    "74|MINOR|Every hook has description|cpv_check_74"
    "75|BLOCKER|.mcp.json parses|cpv_check_75"
    "76|BLOCKER|.mcp.json command paths exist|cpv_check_76"
    "77|MAJOR|.mcp.json env documented|cpv_check_77"
    "78|MINOR|MCP executables in servers/|cpv_check_78"
    "79|MAJOR|README lists every command|cpv_check_79"
    "80|MAJOR|CHANGELOG entry for current ver|cpv_check_80"
    "81|MINOR|README install URL current|cpv_check_81"
    "82|MINOR|No new TODO/FIXME/XXX|cpv_check_82"
    "83|BLOCKER|CIP-1 CLAUDE_PRIVATE_USERNAMES not inverted|cpv_check_ci_preflight"
    "84|MAJOR|CIP-2 shim [no-redef, misc]|cpv_check_ci_preflight"
    "85|BLOCKER|CIP-3 dev extra present for uv sync|cpv_check_ci_preflight"
    "86|MAJOR|CIP-4 no superseded validate.yml|cpv_check_ci_preflight"
    "87|MAJOR|CIP-5 .jscpd.json present for jscpd|cpv_check_ci_preflight"
  )

  local n_pass=0 n_fail=0 n_block_fail=0 n_major_fail=0 n_minor_fail=0
  local -a results

  for entry in "${CHECKS[@]}"; do
    local id="${entry%%|*}"; entry="${entry#*|}"
    local sev="${entry%%|*}"; entry="${entry#*|}"
    local title="${entry%%|*}"; entry="${entry#*|}"
    local fn="${entry%%|*}"

    local rc=0
    if declare -F "$fn" >/dev/null 2>&1; then
      "$fn" >>"$log" 2>&1
      rc=$?
    else
      echo "PENDING $fn (no implementation)" >>"$log"
      rc=1
    fi

    if [ "$rc" -eq 0 ]; then
      n_pass=$((n_pass + 1))
      results+=("CHECK-${id}|${sev}|PASS|${title}")
    else
      n_fail=$((n_fail + 1))
      results+=("CHECK-${id}|${sev}|FAIL|${title}")
      case "$sev" in
        BLOCKER) n_block_fail=$((n_block_fail + 1)) ;;
        MAJOR)   n_major_fail=$((n_major_fail + 1)) ;;
        MINOR)   n_minor_fail=$((n_minor_fail + 1)) ;;
      esac
    fi
  done

  {
    echo "# Canonical-pipeline migration check report"
    echo
    echo "**Plugin:** $plugin_root"
    echo "**Timestamp:** $ts"
    echo
    echo '┏━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓'
    echo '┃ ID         ┃ Severity ┃ Status ┃ Title                                                       ┃'
    echo '┡━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩'
    for row in "${results[@]}"; do
      local id="${row%%|*}"; row="${row#*|}"
      local sev="${row%%|*}"; row="${row#*|}"
      local st="${row%%|*}"; row="${row#*|}"
      local ti="${row%%|*}"
      printf '│ %-10s │ %-8s │ %-6s │ %-59s │\n' "$id" "$sev" "$st" "$ti"
    done
    echo '└────────────┴──────────┴────────┴─────────────────────────────────────────────────────────────┘'
    echo
    echo "Summary: ${n_pass}/${#CHECKS[@]} passed."
    echo
    echo "  BLOCKER fails: ${n_block_fail}"
    echo "  MAJOR fails:   ${n_major_fail}"
    echo "  MINOR fails:   ${n_minor_fail}"
  } | tee "$report"

  if [ "$n_block_fail" -gt 0 ] || [ "$n_major_fail" -gt 0 ]; then
    echo
    echo "FAIL: ${n_block_fail} BLOCKER + ${n_major_fail} MAJOR check(s) failed."
    return 1
  fi

  echo
  echo "PASS: all ${#CHECKS[@]} checks passed (or only MINOR fails)."
  return 0
}
### END_RUN_ALL
```

**Notes on the run-all helper:**

- The function emits a Unicode-bordered Markdown table per the user's standing format preference (heavy `━` for the header row, light `─` for body rows, status column exactly 6 chars wide so PASS/FAIL aligns).
- `cpv_check_NN` functions are placeholders. The migration orchestrator (or a thin shell wrapper that lives at `scripts/run_migration_checks.sh`) defines each one by inlining the **Verify** block of the corresponding `### CHECK-NN` section above.
- `cpv_check_ci_preflight` (shared by CHECK-83..87) inlines ONE `uv run python scripts/remote_validation.py ci-preflight .` invocation — it runs the CIP-1..6 static detectors (CIP-6 = stale CPV ref; no dedicated row) AND the live parity gates and exits non-zero on any real (non-WARNING) finding; a tool-absent WARNING degrades and never fails the check. Run it once; all five rows read the same exit status.
- Exit codes:
  - `0` — all 87 checks pass (or only MINOR fails)
  - `1` — at least one BLOCKER or MAJOR check failed
  - `2` — usage error (cannot cd to plugin root)
- Timestamps follow the canonical local-time + GMT-offset format (`%Y%m%d_%H%M%S%z`), per `~/.claude/rules/agent-reports-location.md`.
- Output report goes to `$MAIN_ROOT/reports/canonical-pipeline-migration/<ts>-run-all.md`.

---

## Plugin-fixer integration contract

The `cpv-plugin-fixer-agent` agent (`agents/cpv-plugin-fixer-agent.md`) MUST run `run_all_checks` as **step 7c** of its algorithm (between the existing step 7b "validate_plugin.py final re-run" and step 8 "capture SUMMARY"). The agent's DONE condition becomes:

> "Step 7 final re-run shows zero CRITICAL/MAJOR/MINOR/NIT **AND** `run_all_checks` returns exit 0."

A BLOCKER/MAJOR fail in `run_all_checks` is equivalent to a CRITICAL/MAJOR finding in `validate_plugin.py` — the agent must address it before declaring DONE.

Because step 7c now includes **CHECK-83..87** (the `ci-preflight` CI-parity gate — CIP-1..6 + the live jscpd/actionlint/mypy/`uv sync --extra dev` gates), a `--force-templates`-clean migration that is locally clean under `validate_plugin --strict` can no longer silently RED-CI on a #137-143 defect shape or a stale `@main` CPV pin — the parity gap is caught BEFORE the real publish, not after the tag is cut.

This closes issue #21 ask #1: "the migration agent's exit contract should be CI passes on next push."

---

## References

- Issue: https://github.com/Emasoft/claude-plugins-validation/issues/21
- TRDD: `design/tasks/TRDD-bbff5bc5-*.md` (publish-auth standard); `design/tasks/TRDD-79638eb6-*.md` (drift autodetect)
- Drift rule: `scripts/validate_plugin.py:3640` (`validate_canonical_pipeline_drift`)
- Migration spec: `skills/cpv-fix-validation/references/pipeline-migration.md`
- Iterative fix loop: `skills/cpv-fix-validation/references/iterative-fix-loop.md`
- Canonical pipeline standard: `skills/cpv-canonical-pipeline/references/detailed-standard.md`
- Pipeline rules: `skills/cpv-canonical-pipeline/references/pipeline-rules.md`
- Hook fixes: `skills/cpv-fix-validation/references/hook-fixes.md`
- Code-quality fixes: `skills/cpv-fix-validation/references/code-quality-fixes.md`
- Plugin-fixer agent: `agents/cpv-plugin-fixer-agent.md`
- Audit reports: `reports/migration-audit/20260509_184143+0200-current-state.md`, `reports/migration-audit/20260509_184057+0200-drift-rule-state.md`
