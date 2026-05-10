// Vulnerable Telegram bot — forwards every inbound message to Claude
// without any sender-ID allowlist. Anyone with the bot URL can inject
// prompts into the Claude session.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import TelegramBot from "node-telegram-bot-api";

const CHANNEL_ID = process.env.CHANNEL_ID ?? "default-channel";
const bot = new TelegramBot(process.env.TELEGRAM_BOT_TOKEN ?? "", { polling: true });
const mcp = new Server({ name: "telegram-channel", version: "1.0.0" }, {
  capabilities: { experimental: {} },
});

bot.on("message", async (msg) => {
  // BUG: no sender-ID allowlist check. Every inbound message is forwarded.
  await mcp.notification({
    method: "notifications/claude/channel",
    params: {
      channelId: CHANNEL_ID,
      message: msg.text ?? "",
    },
  });
});
