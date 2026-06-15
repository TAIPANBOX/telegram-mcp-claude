# Telegram MCP for Claude

A small, self-contained [MCP](https://modelcontextprotocol.io) server that gives
Claude (Claude Desktop / Cowork / any MCP client) a full set of **Telegram** tools,
so it can read and act inside a **dedicated Telegram group**.

It uses the **Telegram Bot API** (not a user account), so the bot only sees
messages in chats it has been added to — it never touches your private 1:1 chats.

Typical use: a shared "ops" group where you, Claude, and another agent (e.g. a
self-hosted assistant) coordinate — Claude reads the conversation, posts updates,
pins decisions, reacts, and shares files.

## Tools (12)
| Tool | What it does |
|---|---|
| `get_me` | Verify token, show bot identity |
| `get_chat` | Group info (title, type, member count) |
| `read_messages` | Recent messages from the group |
| `send_message` | Post text (parse_mode, reply, silent, no-preview) |
| `send_photo` | Send a photo (local path / URL / file_id) |
| `send_document` | Send a file (local path / URL / file_id) |
| `edit_message` | Edit the bot's own message |
| `delete_message` | Delete a message (admin; <48h) |
| `forward_message` | Forward a message from another chat |
| `pin_message` / `unpin_message` | Pin / unpin |
| `set_reaction` | React with an emoji (or clear) |

`parse_mode` accepts `Markdown`, `MarkdownV2`, or `HTML`.

## Setup

**1. Create a bot** — message [@BotFather](https://t.me/BotFather) → `/newbot` → get the **token**.

**2. Disable privacy mode** (so the bot sees all group messages):
`@BotFather` → `/setprivacy` → choose bot → **Disable**.

**3. Create a group**, add your bot (and any other members).

**4. Get the group chat_id** — post a message, then:
```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```
Read `result[].message.chat.id` (groups are negative, e.g. `-1001234567890`).

**5. Install & run**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 server.py   # stdio MCP
```

## Connect to Claude
Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "telegram": {
      "command": "/ABSOLUTE/PATH/.venv/bin/python3",
      "args": ["/ABSOLUTE/PATH/server.py"],
      "env": {
        "TELEGRAM_BOT_TOKEN": "123456:ABC...",
        "TELEGRAM_CHAT_ID": "-1001234567890"
      }
    }
  }
}
```
Restart the client → the `telegram` tools appear.

## Notes & limits
- `read_messages` uses `getUpdates`: Telegram retains updates ~24h, only from chats the bot is in, and **only one `getUpdates` poller per bot** (a webhook or a second poller causes HTTP 409). If another agent also polls Telegram, give Claude its **own bot** in the same group.
- The bot can't see messages from before it joined the group.
- Pin/delete require the bot to be a group admin.

## Security
- Never commit your real `.env` (the token lives there). `.gitignore` already excludes it.
- Bot API scope only — no access to your personal Telegram account.

## License
MIT — see [LICENSE](LICENSE).
