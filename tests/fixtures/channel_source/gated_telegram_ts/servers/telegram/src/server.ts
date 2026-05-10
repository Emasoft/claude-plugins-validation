// Safe Telegram bot — sender-ID allowlist enforced before every forward.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import TelegramBot from "node-telegram-bot-api";

const CHANNEL_ID = process.env.CHANNEL_ID ?? "default-channel";

// Allowlist sourced from env. Empty allowlist would close the channel
// to everyone — see the chat_id_only fixture for the bug shape we forbid.
const ALLOWED_USER_IDS = new Set<number>(
  (process.env.ALLOWED_USER_IDS ?? "")
    .split(",")
    .map((s) => Number.parseInt(s.trim(), 10))
    .filter((n) => Number.isFinite(n) && n > 0),
);

const bot = new TelegramBot(process.env.TELEGRAM_BOT_TOKEN ?? "", { polling: true });
const mcp = new Server({ name: "telegram-channel", version: "1.0.0" }, {
  capabilities: { experimental: {} },
});

bot.on("message", async (msg) => {
  if (!msg.from || !ALLOWED_USER_IDS.has(msg.from.id)) {
    await bot.sendMessage(msg.chat.id, "Unauthorized sender.");
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
