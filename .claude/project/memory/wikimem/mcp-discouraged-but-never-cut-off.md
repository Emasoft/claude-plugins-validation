---
name: mcp-discouraged-but-never-cut-off
description: "should this plugin use MCP or a CLI; is MCP deprecated in our plugins; someone wants to remove MCP support paths from CPV; agent needs an MCP-only service; where may MCP still be installed"
ocd: 2026-08-19
lmd: 2026-08-19
metadata:
  node_type: memory
  type: project
  tier: aspect
publish-globally: false
---

# mcp-discouraged-but-never-cut-off


^ATOM-GX1L-NH88 [desc: "MCP strongly discouraged in plugins but the install option must never be removed; INFO advisory only", keywords: mcp_discouraged prefer_cli_over_mcp do_not_remove_mcp_support mcp-only_service mcp_advisory_info_non-blocking register_mcp_policy, type: project, ocd: 2026-08-19, lmd: 2026-08-19]

Owner policy (2026-08-19): our plugins no longer use MCP and new MCP integrations are STRONGLY DISCOURAGED — prefer a CLI/script the agent invokes directly. But the OPTION must never be cut off: a user may need a service that runs exclusively over MCP, so MCP stays installable into agents (mcpServers on user/project agents), into plugins (.mcp.json — CC spec still forbids mcpServers on plugin-SHIPPED agents; that MAJOR is spec, not policy), and globally at user scope via settings. Enforcement shape: validate_mcp emits a non-blocking INFO advisory when a plugin ships .mcp.json; cpv-register-mcp carries the policy note but remains fully functional. Never escalate the advisory to a blocking severity, and never delete the MCP support paths.

## Notes and lessons learned
