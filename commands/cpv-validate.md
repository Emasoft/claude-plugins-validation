---
name: cpv-validate
description: Interactive validation — the validator agent asks what to validate and runs the right script
agent: plugin-validator
argument-hint: "[path_or_name]"
user-invocable: true
---

Validate a Claude Code plugin, skill, marketplace, or specific component.

If a path or name is provided as $ARGUMENTS, validate that target.
Otherwise, ask the user what they want to validate.
