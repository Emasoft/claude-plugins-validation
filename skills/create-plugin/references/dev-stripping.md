# Dev-stripping (TRDD-793ac32a)

## Table of Contents

- Why this exists
- When NOT to use

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
