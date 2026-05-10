// CRITICAL — declares claude/channel/permission capability but the
// permission handler does NOT gate on sender ID. Any inbound sender
// can approve destructive tool calls.

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import TelegramBot from "node-telegram-bot-api";

const CHANNEL_ID = process.env.CHANNEL_ID ?? "default-channel";

const bot = new TelegramBot(process.env.TELEGRAM_BOT_TOKEN ?? "", { polling: true });
const mcp = new Server(
  { name: "telegram-channel", version: "1.0.0" },
  {
    capabilities: {
      experimental: {
        "claude/channel/permission": {},
      },
    },
  },
);

mcp.setNotificationHandler("claude/channel/permission", async (req) => {
  // BUG: no sender-ID gate. Just relays whatever the channel said.
  return await relayToUpstream(req);
});

bot.on("message", async (msg) => {
  // BUG: also no inbound gating.
  await mcp.notification({
    method: "notifications/claude/channel",
    params: {
      channelId: CHANNEL_ID,
      message: msg.text ?? "",
    },
  });
});

async function relayToUpstream(_req: unknown): Promise<{ approved: boolean }> {
  return { approved: true };
}
