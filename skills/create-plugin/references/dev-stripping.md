# Dev-stripping (TRDD-793ac32a)

## Table of Contents

- Why this exists
- When NOT to use

After scaffolding a new plugin, optionally emit the `cpv.strip` block in
plugin.json so it's ready for `cpv strip-dev-parts` later. Default ON;
ask the user via Unicode table (not AskUserQuestion). See the matching
section in `agents/plugin-creator.md`.

The CLI flag is `generate_plugin_repo.py --strip-dev` (default) /
`--no-strip-dev` (legacy). When ON, the generator writes:

```json
{
  "cpv": {
    "strip": {
      "extract": [
        {"src": "tests/",  "submodule": "<owner>/<plugin>-tests"},
        {"src": "design/", "submodule": "<owner>/<plugin>-design"}
      ],
      "require_url_allowlist": true
    }
  }
}
```

into the generated plugin.json. No submodules are created until the user
runs `cpv strip-dev-parts` separately.

## Why this exists

Claude Code's plugin installer does NOT pass `--recurse-submodules`, so
the submodule content never reaches the user — only the .gitmodules
pointer (~86 bytes) does. Verified empirically against PSS
(`perfect-skill-suggester`): the gigabytes of Rust source that lives in
PSS's `rust/` submodule never ship to end users.

This pattern saves ~12 MB per cache install for a typical CPV-style
plugin with `tests/` + `design/` directories.

## When NOT to use

Skip dev-stripping (`--no-strip-dev`) when:

- The plugin has no `tests/` or `design/` directories at all
- Tests or design docs are essential at runtime (rare)
- The plugin author prefers to keep everything in MAIN repo for
  simplicity and is willing to pay the install-size cost
