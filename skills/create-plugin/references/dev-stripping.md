# Dev-stripping (TRDD-793ac32a)

## Table of Contents

- Why this exists
- When NOT to use
- Testing approach (no checked-in fixture)

After scaffolding a new plugin, optionally emit the `cpv.strip` block in
plugin.json so it's ready for `cpv strip-dev-parts` later. Default ON;
ask the user via Unicode table (not AskUserQuestion). See the matching
section in `agents/plugin-creator.md`.

The CLI flag is `generate_plugin_repo.py --strip-dev` (default) /
`--no-strip-dev` (legacy). When ON, the generator writes (PSS pattern —
ONE submodule per plugin):

```json
{
  "cpv": {
    "strip": {
      "extract": [
        {"src": "tests/", "submodule": "<owner>/<plugin>-tests",
         "submodule_path": "tests/"}
      ],
      "require_url_allowlist": true
    }
  }
}
```

into the generated plugin.json. No submodules are created until the user
runs `cpv strip-dev-parts --auto` separately.

## Why this exists

Claude Code's plugin installer does NOT pass `--recurse-submodules`, so
the submodule content never reaches the user — only the .gitmodules
pointer (~86 bytes) does. Verified empirically against PSS
(`perfect-skill-suggester`): the rust source that lives in PSS's
`rust/` submodule never ships to end users (binaries in `bin/` ship
instead).

This pattern is most useful when `tests/` is large (fixtures, sample
data, snapshots). For a typical CPV-style plugin it saves only ~3 MB,
but for plugins with heavy fixtures / sample corpora it can save much
more.

## When NOT to use

Skip dev-stripping (`--no-strip-dev`) when:

- The plugin has no `tests/` directory at all (rare for CPV-style plugins)
- Tests are essential at runtime (very rare)
- The plugin author prefers operational simplicity over install-size
  savings (one extra GitHub repo to manage per plugin)

## Testing approach

The TRDD-793ac32a plan referenced a `tests/fixtures/sample-plugin-with-tests/`
fixture tree, but the implementation deliberately uses **`tmp_path` builders**
instead — `_make_plugin()` helpers in
`tests/test_cpv_strip_dev_unit.py`, `tests/test_cpv_validate_gitmodules.py`,
and `_make_scaffold_with_strip()` in `tests/test_cpv_strip_dev_e2e.py`.
There is no checked-in `sample-plugin-with-tests/` fixture and none is
needed.

Why builders, not a static fixture: the strip-dev surface tests 73
distinct plugin shapes — clean tree, dirty tree, stashed tree, detached
HEAD, non-git, symlinked dir, corrupt state JSON, missing plugin.json,
hostile `.gitmodules` (file://, userinfo, http://, backslash, path
traversal, alien owner), allowlist on/off, with/without `cpv.strip`
block, with `--strip-dev`/`--no-strip-dev` flag, etc. Each test mutates
the plugin shape per-case (drops in a stash, makes a symlink, writes a
particular plugin.json variant). A single checked-in fixture would
either need 73 sub-fixtures (high noise, easy to drift) or each test
would still have to mutate the fixture in place — which is what
`tmp_path` already does, but without the cross-test pollution risk of
shared on-disk state. The builder approach also keeps the fixture
intent inline with the test (you can read the test and see exactly what
plugin shape it asserts against), and forces test isolation by
construction (each `tmp_path` is unique to the test).

If a future test case ever does need a real on-disk fixture (e.g. to
exercise `cpv strip-dev-parts --auto` end-to-end against a snapshot of
a real plugin), add it under `tests/fixtures/<specific-name>/` at that
point — but the current 73 tests cover the engine, the validator, and
the generator-to-builder round-trip without one.
