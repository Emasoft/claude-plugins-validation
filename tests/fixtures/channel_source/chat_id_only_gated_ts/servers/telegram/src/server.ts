// MAJOR pattern — gates on chat-ID instead of sender-ID.
// Anyone in the authorized chat can inject prompts.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import TelegramBot from "node-telegram-bot-api";

const CHANNEL_ID = process.env.CHANNEL_ID ?? "default-channel";
const ALLOWED_CHAT_ID = Number.parseInt(process.env.ALLOWED_CHAT_ID ?? "0", 10);

const bot = new TelegramBot(process.env.TELEGRAM_BOT_TOKEN ?? "", { polling: true });
const mcp = new Server({ name: "telegram-channel", version: "1.0.0" }, {
  capabilities: { experimental: {} },
});

bot.on("message", async (msg) => {
  // BUG: chat-ID-only gating — no sender-ID check.
  if (msg.chat.id !== ALLOWED_CHAT_ID) {
    return;
  }
  await mcp.notification({
    method: "notifications/claude/channel",
    params: {
      channelId: CHANNEL_ID,
      message: msg.text ?? "",
    },
  });
});
