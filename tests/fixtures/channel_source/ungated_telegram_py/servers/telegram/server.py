"""Vulnerable Telegram channel — forwards everything, no sender check."""

import os

from aiogram import Dispatcher
from mcp_server import Server  # hypothetical MCP server library

CHANNEL_ID = os.environ.get("CHANNEL_ID", "default-channel")

mcp = Server("telegram-channel", "1.0.0")
dp = Dispatcher()


@dp.message_handler()
async def handle(message):
    # BUG: no sender allowlist; every inbound message is forwarded.
    await mcp.send_notification(
        "notifications/claude/channel",
        {"channelId": CHANNEL_ID, "message": message.text},
    )
