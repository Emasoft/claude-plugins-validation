# Official Documentation URLs

For the most up-to-date specifications, fetch and read these official documentation URLs. Specifications change frequently, so always consult the latest versions before validating plugins.

## Table of Contents

1. [Claude Code Fundamentals](#claude-code-fundamentals)
2. [Settings & CLI](#settings--cli)
3. [Marketplaces](#marketplaces)
4. [Plugins](#plugins)
5. [Custom Agents](#custom-agents)
6. [Hooks](#hooks)
7. [Skills](#skills)
8. [Other Extensions](#other-extensions)
9. [MCP (Model Context Protocol)](#mcp-model-context-protocol)
10. [MCP Detailed Specifications](#mcp-detailed-specifications)
11. [Troubleshooting](#troubleshooting)
12. [How to Use Official Documentation](#how-to-use-official-documentation)

---

## Claude Code Fundamentals

| Topic | Official URL |
|-------|--------------|
| Latest Changes | https://code.claude.com/docs/en/changelog.md |
| How Claude Code Works | https://code.claude.com/docs/en/how-claude-code-works.md |
| Extend Claude Code | https://code.claude.com/docs/en/features-overview.md |

## Settings & CLI

| Topic | Official URL |
|-------|--------------|
| Claude Code Settings | https://code.claude.com/docs/en/settings.md |
| CLI Reference | https://code.claude.com/docs/en/cli-reference.md |

## Marketplaces

| Topic | Official URL |
|-------|--------------|
| Discover Plugins | https://code.claude.com/docs/en/discover-plugins.md |
| Create Marketplaces | https://code.claude.com/docs/en/plugin-marketplaces.md |

## Plugins

| Topic | Official URL |
|-------|--------------|
| Create Plugins | https://code.claude.com/docs/en/plugins.md |
| Plugins Reference | https://code.claude.com/docs/en/plugins-reference.md |

## Custom Agents

| Topic | Official URL |
|-------|--------------|
| Create Subagents | https://code.claude.com/docs/en/sub-agents.md |

## Hooks

| Topic | Official URL |
|-------|--------------|
| Hooks Guide | https://code.claude.com/docs/en/hooks-guide.md |
| Hooks Reference | https://code.claude.com/docs/en/hooks.md |

## Skills

| Topic | Official URL |
|-------|--------------|
| Extend with Skills | https://code.claude.com/docs/en/skills.md |

## Other Extensions

| Topic | Official URL |
|-------|--------------|
| Output Styles | https://code.claude.com/docs/en/output-styles.md |
| Status Line Config | https://code.claude.com/docs/en/statusline.md |
| GitHub Actions | https://code.claude.com/docs/en/github-actions.md |

## MCP (Model Context Protocol)

| Topic | Official URL |
|-------|--------------|
| MCP Spec Changelog | https://modelcontextprotocol.io/specification/2025-11-25/changelog.md |
| Connect via MCP | https://code.claude.com/docs/en/mcp.md |
| MCP Server Overview | https://modelcontextprotocol.io/specification/2025-11-25/server/index.md |
| MCP Specification | https://modelcontextprotocol.io/specification/2025-11-25/index.md |
| MCP Schema Reference | https://modelcontextprotocol.io/specification/2025-11-25/schema.md |
| Build MCP Client | https://modelcontextprotocol.io/docs/develop/build-client.md |
| Build MCP Server | https://modelcontextprotocol.io/docs/develop/build-server.md |
| MCP Architecture | https://modelcontextprotocol.io/docs/learn/architecture.md |
| MCP Client Concepts | https://modelcontextprotocol.io/docs/learn/client-concepts.md |
| MCP Server Concepts | https://modelcontextprotocol.io/docs/learn/server-concepts.md |
| MCP SDKs | https://modelcontextprotocol.io/docs/sdk.md |
| MCP Inspector | https://modelcontextprotocol.io/docs/tools/inspector.md |

## MCP Detailed Specifications

| Topic | Official URL |
|-------|--------------|
| Architecture | https://modelcontextprotocol.io/specification/2025-11-25/architecture/index.md |
| Authorization | https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization.md |
| Basic Overview | https://modelcontextprotocol.io/specification/2025-11-25/basic/index.md |
| Security Best Practices | https://modelcontextprotocol.io/specification/2025-11-25/basic/security_best_practices.md |
| Transports | https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md |
| Tasks | https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks.md |
| Roots | https://modelcontextprotocol.io/specification/2025-11-25/client/roots.md |
| Prompts | https://modelcontextprotocol.io/specification/2025-11-25/server/prompts.md |
| Resources | https://modelcontextprotocol.io/specification/2025-11-25/server/resources.md |
| Tools | https://modelcontextprotocol.io/specification/2025-11-25/server/tools.md |
| Sampling | https://modelcontextprotocol.io/specification/2025-11-25/client/sampling.md |
| Completion | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion.md |
| Logging | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging.md |
| Pagination | https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination.md |
| Versioning | https://modelcontextprotocol.io/specification/versioning.md |

## Troubleshooting

| Topic | Official URL |
|-------|--------------|
| Troubleshooting | https://code.claude.com/docs/en/troubleshooting.md |

---

## How to Use Official Documentation

When validating plugins, always verify against the latest specifications:

1. **Before validation**: Fetch the relevant spec URL to confirm current requirements
2. **When errors occur**: Check the official docs for updated field names or structures
3. **For MCP servers**: The MCP spec version (currently 2025-11-25) defines transport types and fields
4. **For hooks**: Check hooks.md for the current list of valid event types

### Example: Fetching Latest Hook Events

```bash
# Fetch the hooks reference to verify valid event types
curl -s https://code.claude.com/docs/en/hooks.md | grep -A 20 "## Hook Events"
```

### Example: Checking MCP Transport Types

```bash
# Fetch MCP transports spec for current transport types
curl -s https://modelcontextprotocol.io/specification/2025-11-25/basic/transports.md
```
