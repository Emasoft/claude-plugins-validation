# Error-to-Fix Index (split)

## Table of Contents

- [Plugin index](#plugin-index)
- [Marketplace index](#marketplace-index)

## Checklist

- [ ] Determine whether the report came from a plugin-scope or marketplace-scope validator
- [ ] Open the matching index file (plugin-error-index.md or marketplace-error-index.md)
- [ ] Continue from that index

---

The monolithic error-to-fix index has been split into two indexes by validator scope. The `cpv-plugin-fixer-agent` agent loads `plugin-error-index.md` for plugin-scope reports; the `cpv-marketplace-fixer-agent` agent loads `marketplace-error-index.md` for marketplace-scope reports. This redirect stub exists only so external links pointing at the old filename still resolve.

## Plugin index

- [plugin-error-index.md](plugin-error-index.md) — 16 plugin-level validators (`validate_plugin.py`, `validate_skill.py`, `validate_skill_comprehensive.py`, `validate_hook.py`, `validate_agent.py`, `validate_command.py`, `validate_mcp.py`, `validate_lsp.py`, `validate_security.py`, `validate_rules.py`, `validate_xref.py`, `validate_settings_marketplace.py`, `validate_documentation.py`, `validate_encoding.py`, `validate_enterprise.py`, `validate_scoring.py`).

## Marketplace index

- [marketplace-error-index.md](marketplace-error-index.md) — 2 marketplace-level validators (`validate_marketplace.py`, `validate_marketplace_pipeline.py`) plus the 7-signal architecture/layout restructure warning.

For a listing of every severity-emitting call site across all 18 validators, see `docs_dev/validator_error_inventory_20260412.md`.
