# Create-plugin examples

## Table of Contents

- Create plugin
- Create marketplace

## Create plugin

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_plugin_repo.py" /tmp/my-plugin \
  --name my-plugin --description "My awesome plugin" \
  --author "Me" --author-email "me@example.com" --github-owner MyGitHub
```

## Create marketplace

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/generate_marketplace_repo.py" /tmp/my-mkt \
  --name my-marketplace --owner-name "My Org" --github-owner MyGitHub
```
