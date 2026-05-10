"""Safe Telegram channel — sender-ID allowlist enforced before each forward."""

import os

from aiogram import Dispatcher, types
from mcp_server import Server  # hypothetical MCP server library

ALLOWED_USER_IDS = {int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()}
CHANNEL_ID = os.environ.get("CHANNEL_ID", "default-channel")

mcp = Server("telegram-channel", "1.0.0")
dp = Dispatcher()


@dp.message_handler()
async def handle(message: types.Message) -> None:
    if message.from_user is None or message.from_user.id not in ALLOWED_USER_IDS:
        await message.reply("Unauthorized sender.")
        return
    await mcp.send_notification(
        "notifications/claude/channel",
        {"channelId": CHANNEL_ID, "message": message.text},
    )
