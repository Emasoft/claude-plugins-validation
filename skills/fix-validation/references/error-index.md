# Error-to-Fix Index (split)

## Table of Contents

- [Plugin index](#plugin-index)
- [Marketplace index](#marketplace-index)

---

The monolithic error-to-fix index has been split into two indexes by validator scope. The `plugin-fixer` agent loads whichever is relevant based on the report category. This redirect stub exists only so external links pointing at the old filename still resolve.

## Plugin index

- [plugin-error-index.md](plugin-error-index.md) — 16 plugin-level validators (`validate_plugin.py`, `validate_skill.py`, `validate_skill_comprehensive.py`, `validate_hook.py`, `validate_agent.py`, `validate_command.py`, `validate_mcp.py`, `validate_lsp.py`, `validate_security.py`, `validate_rules.py`, `validate_xref.py`, `validate_settings_marketplace.py`, `validate_documentation.py`, `validate_encoding.py`, `validate_enterprise.py`, `validate_scoring.py`).

## Marketplace index

- [marketplace-error-index.md](marketplace-error-index.md) — 2 marketplace-level validators (`validate_marketplace.py`, `validate_marketplace_pipeline.py`) plus the 7-signal architecture/layout restructure warning.

For a listing of every severity-emitting call site across all 18 validators, see `docs_dev/validator_error_inventory_20260412.md`.
