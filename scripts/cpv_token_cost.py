#!/usr/bin/env python3
"""Look up token cost for a specific agent execution by ID.

Each validation skill runs in a dedicated agent. The agent result includes
total_tokens, tool_uses, and duration_ms. This script looks up that data
from the session JSONL transcript.

Usage:
    # Look up a specific agent execution
    uv run python scripts/cpv_token_cost.py --agent-id ab877e35cac0cdf30

    # Look up the last agent execution in the current session
    uv run python scripts/cpv_token_cost.py --latest --last-agent

    # List all agent executions in a session (summary)
    uv run python scripts/cpv_token_cost.py --latest --list

    # JSON output
    uv run python scripts/cpv_token_cost.py --latest --last-agent --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Anthropic pricing per million tokens (as of 2025-05)
PRICING = {
    "opus": {"input": 15.0, "output": 75.0, "cache_write": 18.75},
    "sonnet": {"input": 3.0, "output": 15.0, "cache_write": 3.75},
    "haiku": {"input": 0.80, "output": 4.0, "cache_write": 1.0},
}

DEFAULT_MODEL = "opus"

# Regex to extract agent metadata from tool result text
AGENT_TOKENS_RE = re.compile(r"total_tokens:\s*(\d+)")
AGENT_TOOL_USES_RE = re.compile(r"tool_uses:\s*(\d+)")
AGENT_DURATION_RE = re.compile(r"duration_ms:\s*(\d+)")
AGENT_ID_RE = re.compile(r"agentId:\s*(\w+)")


def find_project_dirs() -> list[Path]:
    """Find all Claude project directories."""
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return []
    return [d for d in claude_dir.iterdir() if d.is_dir()]


def find_jsonl(session_id: str | None, project_dir: Path | None, latest: bool) -> Path | None:
    """Find the JSONL transcript file."""
    dirs = [project_dir] if project_dir else find_project_dirs()

    if session_id:
        for d in dirs:
            p = d / f"{session_id}.jsonl"
            if p.exists():
                return p
        return None

    if latest:
        newest: Path | None = None
        newest_mtime = 0.0
        for d in dirs:
            for f in d.glob("*.jsonl"):
                if f.stat().st_mtime > newest_mtime:
                    newest = f
                    newest_mtime = f.stat().st_mtime
        return newest

    return None


def get_block_text(block: dict) -> str:
    """Extract text from a content block."""
    block_content = block.get("content", "")
    if isinstance(block_content, str):
        return block_content
    if isinstance(block_content, list):
        parts = []
        for sub in block_content:
            if isinstance(sub, dict) and "text" in sub:
                parts.append(sub["text"])
            elif isinstance(sub, str):
                parts.append(sub)
        return "\n".join(parts)
    return ""


def find_agent_executions(jsonl_path: Path) -> list[dict]:
    """Find all agent executions in a JSONL transcript.

    Returns list of dicts, each representing one agent execution with:
      - description, subagent_type, spawn_ts, result_ts
      - agent_id, total_tokens, tool_uses, duration_ms
      - result_summary (first line of result)
    """
    agents: list[dict] = []
    pending: dict[str, dict] = {}  # tool_use_id -> spawn info

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = obj.get("type", "")
            ts = obj.get("timestamp", "")
            msg = obj.get("message", {})
            content = msg.get("content", [])

            if not isinstance(content, list):
                continue

            # Track Agent spawns from assistant messages
            if entry_type == "assistant":
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") == "Agent":
                        tool_use_id = block.get("id", "")
                        inp = block.get("input", {})
                        pending[tool_use_id] = {
                            "description": inp.get("description", "unknown"),
                            "subagent_type": inp.get("subagent_type", "general-purpose"),
                            "spawn_ts": ts,
                        }

            # Match agent results from user messages (tool_result)
            if entry_type == "user":
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    tool_use_id = block.get("tool_use_id", "")
                    if tool_use_id and tool_use_id in pending:
                        spawn = pending.pop(tool_use_id)
                        result_text = get_block_text(block)
                        tokens_match = AGENT_TOKENS_RE.search(result_text)
                        if tokens_match:
                            info: dict[str, object] = {
                                **spawn,
                                "result_ts": ts,
                                "total_tokens": int(tokens_match.group(1)),
                                "tool_uses": 0,
                                "duration_ms": 0,
                                "agent_id": "",
                                "result_summary": result_text.split("\n")[0][:150],
                            }
                            m = AGENT_TOOL_USES_RE.search(result_text)
                            if m:
                                info["tool_uses"] = int(m.group(1))
                            m = AGENT_DURATION_RE.search(result_text)
                            if m:
                                info["duration_ms"] = int(m.group(1))
                            m = AGENT_ID_RE.search(result_text)
                            if m:
                                info["agent_id"] = m.group(1)
                            agents.append(info)

    return agents


def estimate_cost(total_tokens: int, model: str) -> tuple[float, float]:
    """Estimate USD cost range from agent total_tokens.

    Agent total_tokens includes input + output + cache_read + cache_write
    but provides no breakdown. We return (lower_bound, upper_bound):
      - lower: assumes 70% cache_read (90% cheaper), 10% input, 10% output, 10% cache_write
      - upper: assumes all tokens are full-price input (worst case)
    """
    prices = PRICING.get(model, PRICING[DEFAULT_MODEL])
    per_m = 1_000_000.0
    # Upper bound: all tokens at full input price
    upper = total_tokens / per_m * prices["input"]
    # Lower bound: realistic agent breakdown with heavy caching
    cache_read_price = prices["input"] * 0.10  # cache reads are 90% cheaper
    lower = (
        int(total_tokens * 0.70) / per_m * cache_read_price
        + int(total_tokens * 0.10) / per_m * prices["input"]
        + int(total_tokens * 0.10) / per_m * prices["output"]
        + int(total_tokens * 0.10) / per_m * prices["cache_write"]
    )
    return (lower, upper)


def format_tokens(n: int) -> str:
    """Format token count with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def print_single(agent: dict, model: str) -> None:
    """Print cost report for a single agent execution."""
    tokens = int(agent["total_tokens"])
    low, high = estimate_cost(tokens, model)
    duration_s = int(agent["duration_ms"]) / 1000

    print(f"  Tokens: {format_tokens(tokens)}  |  Est. cost: ${low:.4f}-${high:.4f}  |  Time: {duration_s:.1f}s")
    print(f"  Agent: {agent.get('agent_id', 'N/A')}  |  Tool calls: {agent.get('tool_uses', 0)}")


def print_list(agents: list[dict], model: str) -> None:
    """Print summary table of all agent executions."""
    print(f"\n  {'#':<3} {'Description':<35} {'Tokens':>10} {'Est. Cost Range':>16} {'Time':>8} {'Agent ID':<20}")
    print(f"  {'-'*3} {'-'*35} {'-'*10} {'-'*16} {'-'*8} {'-'*20}")

    total_tokens = 0
    total_cost_low = 0.0
    total_cost_high = 0.0
    for i, agent in enumerate(agents, 1):
        tokens = int(agent["total_tokens"])
        low, high = estimate_cost(tokens, model)
        duration_s = int(agent["duration_ms"]) / 1000
        total_tokens += tokens
        total_cost_low += low
        total_cost_high += high

        desc = str(agent["description"])[:35]
        aid = str(agent.get("agent_id", ""))[:20]
        print(f"  {i:<3} {desc:<35} {format_tokens(tokens):>10} ${low:.4f}-${high:.4f} {duration_s:>6.1f}s {aid:<20}")

    print(f"  {'-'*3} {'-'*35} {'-'*10} {'-'*16} {'-'*8}")
    print(f"  {'':3} {'TOTAL':<35} {format_tokens(total_tokens):>10} ${total_cost_low:.4f}-${total_cost_high:.4f}")
    print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Look up token cost for agent executions.")
    parser.add_argument("session_id", nargs="?", help="Session UUID (or use --latest)")
    parser.add_argument("--latest", action="store_true", help="Use the most recently modified JSONL")
    parser.add_argument("--agent-id", help="Look up a specific agent by ID")
    parser.add_argument("--last-agent", action="store_true", help="Show the last agent execution")
    parser.add_argument("--list", action="store_true", help="List all agent executions")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=PRICING.keys(), help="Pricing model")
    parser.add_argument("--project-dir", type=Path, help="Specific project directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not args.session_id and not args.latest:
        parser.error("Provide a session_id or use --latest")

    if not args.agent_id and not args.last_agent and not args.list:
        parser.error("Specify --agent-id ID, --last-agent, or --list")

    jsonl_path = find_jsonl(args.session_id, args.project_dir, args.latest)
    if not jsonl_path:
        print("Error: Could not find JSONL transcript file.", file=sys.stderr)
        return 1

    agents = find_agent_executions(jsonl_path)
    if not agents:
        print("No agent executions found.", file=sys.stderr)
        return 1

    # Filter
    if args.agent_id:
        agents = [a for a in agents if a.get("agent_id") == args.agent_id]
        if not agents:
            print(f"Agent {args.agent_id} not found.", file=sys.stderr)
            return 1
    elif args.last_agent:
        agents = [agents[-1]]

    # Output
    if args.json:
        output = {
            "model": args.model,
            "agents": [
                {
                    "description": a["description"],
                    "agent_id": a.get("agent_id", ""),
                    "total_tokens": a["total_tokens"],
                    "tool_uses": a.get("tool_uses", 0),
                    "duration_ms": a.get("duration_ms", 0),
                    "estimated_cost_usd_range": [
                        round(estimate_cost(int(a["total_tokens"]), args.model)[0], 6),
                        round(estimate_cost(int(a["total_tokens"]), args.model)[1], 6),
                    ],
                    "result_summary": a.get("result_summary", ""),
                }
                for a in agents
            ],
        }
        if len(agents) > 1:
            output["total_tokens"] = sum(int(a["total_tokens"]) for a in agents)
            ranges = [estimate_cost(int(a["total_tokens"]), args.model) for a in agents]
            output["total_estimated_cost_usd_range"] = [
                round(sum(r[0] for r in ranges), 6),
                round(sum(r[1] for r in ranges), 6),
            ]
        print(json.dumps(output, indent=2))
    elif args.list:
        print_list(agents, args.model)
    else:
        for agent in agents:
            print_single(agent, args.model)

    return 0


if __name__ == "__main__":
    sys.exit(main())
