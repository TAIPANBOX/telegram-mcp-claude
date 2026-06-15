#!/usr/bin/env python3
"""
Telegram MCP server for Claude.

Exposes a comprehensive, safe set of Telegram tools so Claude can fully operate
inside a DEDICATED Telegram group (e.g. "YouTube Ops") where the user and the
Hermes agent also live. Uses the Telegram Bot API (not a user account), so the
bot only ever sees messages in chats it has been added to — it cannot read your
private 1:1 conversations.

Config (environment variables):
  TELEGRAM_BOT_TOKEN   (required)  Bot token from @BotFather
  TELEGRAM_CHAT_ID     (optional)  Default chat/group id to read & post in.
                                   If unset, pass chat_id per call.

Run (stdio transport, for Claude Desktop / Cowork):
  TELEGRAM_BOT_TOKEN=123:ABC TELEGRAM_CHAT_ID=-1001234567890 python3 server.py
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT = 30.0

mcp = FastMCP("telegram")


# ---------------------------------------------------------------------------
# Config / helpers
# ---------------------------------------------------------------------------
def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather, then "
            "set TELEGRAM_BOT_TOKEN in the MCP server environment."
        )
    return token


def _default_chat() -> str | None:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None


def _resolve_chat(chat_id: str | None) -> str:
    chat = (chat_id or "").strip() or _default_chat()
    if not chat:
        raise RuntimeError(
            "No chat_id provided and TELEGRAM_CHAT_ID is not set. Pass chat_id "
            "explicitly or configure a default group id."
        )
    return chat


def _check_parse_mode(parse_mode: str | None) -> str | None:
    if parse_mode is None:
        return None
    pm = parse_mode.strip()
    if not pm:
        return None
    allowed = {"Markdown", "MarkdownV2", "HTML"}
    if pm not in allowed:
        raise RuntimeError(f"parse_mode must be one of {sorted(allowed)} (or omit it).")
    return pm


def _raise_for_result(method: str, status: int, data: dict[str, Any]) -> dict[str, Any]:
    if not data.get("ok"):
        desc = data.get("description", "unknown error")
        code = data.get("error_code", status)
        hint = ""
        if code == 401:
            hint = " — token invalid; re-check TELEGRAM_BOT_TOKEN from @BotFather."
        elif code == 400 and "chat not found" in desc.lower():
            hint = " — bot must be in that group; group ids are negative (e.g. -100...)."
        elif code == 409:
            hint = " — webhook set or another getUpdates poller running; use a separate bot for Claude or delete the webhook."
        elif code == 403:
            hint = " — bot lacks permission (e.g. not admin for pin/delete) or was removed from the chat."
        raise RuntimeError(f"Telegram '{method}' failed ({code}): {desc}{hint}")
    return data.get("result", {})


async def _call(method: str, payload: dict[str, Any] | None = None) -> Any:
    """JSON Bot API call → returns `result` or raises with an actionable message."""
    url = f"{API_BASE}/bot{_token()}/{method}"
    # strip None values
    body = {k: v for k, v in (payload or {}).items() if v is not None}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error calling Telegram '{method}': {exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Telegram '{method}' returned non-JSON (HTTP {resp.status_code})."
        ) from exc
    return _raise_for_result(method, resp.status_code, data)


async def _call_file(method: str, field: str, source: str, data: dict[str, Any]) -> Any:
    """Multipart Bot API call for sending a local file, OR pass a URL/file_id as a
    normal field if `source` is an http(s) URL or an existing Telegram file_id."""
    url = f"{API_BASE}/bot{_token()}/{method}"
    body = {k: str(v) for k, v in data.items() if v is not None}
    is_url = source.startswith("http://") or source.startswith("https://")
    try:
        async with httpx.AsyncClient(timeout=max(HTTP_TIMEOUT, 120.0)) as client:
            if is_url:
                body[field] = source
                resp = await client.post(url, data=body)
            else:
                path = os.path.expanduser(source)
                if not os.path.isfile(path):
                    raise RuntimeError(f"File not found: {path}")
                with open(path, "rb") as fh:
                    resp = await client.post(url, data=body, files={field: (os.path.basename(path), fh)})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Network error calling Telegram '{method}': {exc}") from exc
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"Telegram '{method}' returned non-JSON (HTTP {resp.status_code}).") from exc
    return _raise_for_result(method, resp.status_code, payload)


def _fmt_message(msg: dict[str, Any]) -> dict[str, Any]:
    frm = msg.get("from", {}) or {}
    name = " ".join(p for p in [frm.get("first_name"), frm.get("last_name")] if p)
    sender = name or frm.get("username") or ("bot" if frm.get("is_bot") else "unknown")
    out = {
        "message_id": msg.get("message_id"),
        "date": msg.get("date"),
        "from": sender,
        "username": frm.get("username"),
        "is_bot": bool(frm.get("is_bot")),
        "text": msg.get("text") or msg.get("caption") or "",
        "chat_id": (msg.get("chat") or {}).get("id"),
    }
    if msg.get("reply_to_message"):
        out["reply_to_message_id"] = msg["reply_to_message"].get("message_id")
    for attach in ("photo", "document", "video", "audio", "voice", "sticker"):
        if attach in msg:
            out["attachment"] = attach
            break
    return out


# ---------------------------------------------------------------------------
# Tools — identity & chat
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_me() -> dict[str, Any]:
    """Verify the bot token and return the bot's identity (id, username, name).
    Call this first to confirm the server is configured correctly."""
    me = await _call("getMe")
    return {
        "id": me.get("id"),
        "username": me.get("username"),
        "name": me.get("first_name"),
        "default_chat_id": _default_chat(),
    }


@mcp.tool()
async def get_chat(chat_id: str | None = None) -> dict[str, Any]:
    """Get info about the target group/chat (title, type, id) plus member count."""
    chat = _resolve_chat(chat_id)
    info = await _call("getChat", {"chat_id": chat})
    result = {
        "id": info.get("id"),
        "title": info.get("title"),
        "type": info.get("type"),
        "username": info.get("username"),
    }
    try:
        result["member_count"] = await _call("getChatMemberCount", {"chat_id": chat})
    except RuntimeError:
        pass
    return result


# ---------------------------------------------------------------------------
# Tools — reading
# ---------------------------------------------------------------------------
@mcp.tool()
async def read_messages(limit: int = 20, chat_id: str | None = None) -> dict[str, Any]:
    """Read recent messages from the Telegram group.

    Fetches pending updates via the Bot API and returns the most recent messages
    (newest last) for the target chat. Note: the Bot API only retains updates for
    ~24h, only returns messages from chats the bot is in, and requires that no
    webhook is set on the bot (and that no other poller uses the same bot token).

    Args:
        limit: Max messages to return (1-100). Default 20.
        chat_id: Filter to this chat/group id. Defaults to TELEGRAM_CHAT_ID.
    """
    limit = max(1, min(int(limit), 100))
    want = _resolve_chat(chat_id)
    updates = await _call("getUpdates", {"allowed_updates": ["message", "channel_post"], "limit": 100})
    messages: list[dict[str, Any]] = []
    for upd in updates if isinstance(updates, list) else []:
        msg = upd.get("message") or upd.get("channel_post")
        if not msg:
            continue
        if str((msg.get("chat") or {}).get("id")) != str(want):
            continue
        messages.append(_fmt_message(msg))
    messages = messages[-limit:]
    return {"chat_id": want, "count": len(messages), "messages": messages}


# ---------------------------------------------------------------------------
# Tools — sending
# ---------------------------------------------------------------------------
@mcp.tool()
async def send_message(
    text: str,
    chat_id: str | None = None,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = None,
    disable_notification: bool = False,
    disable_web_page_preview: bool = False,
) -> dict[str, Any]:
    """Send a text message to the Telegram group.

    Args:
        text: Message body.
        chat_id: Target chat id. Defaults to TELEGRAM_CHAT_ID.
        reply_to_message_id: Reply to a specific message (threads it).
        parse_mode: 'Markdown', 'MarkdownV2' or 'HTML' for formatting (optional).
        disable_notification: Send silently (no sound).
        disable_web_page_preview: Hide link previews.
    """
    if not text or not text.strip():
        raise RuntimeError("`text` is empty — provide a non-empty message.")
    sent = await _call("sendMessage", {
        "chat_id": _resolve_chat(chat_id),
        "text": text,
        "reply_to_message_id": reply_to_message_id,
        "parse_mode": _check_parse_mode(parse_mode),
        "disable_notification": disable_notification or None,
        "disable_web_page_preview": disable_web_page_preview or None,
    })
    return {"message_id": sent.get("message_id"), "chat_id": (sent.get("chat") or {}).get("id")}


@mcp.tool()
async def send_photo(
    photo: str,
    caption: str | None = None,
    chat_id: str | None = None,
    parse_mode: str | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Send a photo to the group. `photo` may be a local file path, an http(s)
    URL, or an existing Telegram file_id. Optional caption."""
    sent = await _call_file("sendPhoto", "photo", photo, {
        "chat_id": _resolve_chat(chat_id),
        "caption": caption,
        "parse_mode": _check_parse_mode(parse_mode),
        "disable_notification": disable_notification or None,
    })
    return {"message_id": sent.get("message_id"), "chat_id": (sent.get("chat") or {}).get("id")}


@mcp.tool()
async def send_document(
    document: str,
    caption: str | None = None,
    chat_id: str | None = None,
    parse_mode: str | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Send a document/file to the group. `document` may be a local file path, an
    http(s) URL, or an existing Telegram file_id. Optional caption."""
    sent = await _call_file("sendDocument", "document", document, {
        "chat_id": _resolve_chat(chat_id),
        "caption": caption,
        "parse_mode": _check_parse_mode(parse_mode),
        "disable_notification": disable_notification or None,
    })
    return {"message_id": sent.get("message_id"), "chat_id": (sent.get("chat") or {}).get("id")}


# ---------------------------------------------------------------------------
# Tools — manage existing messages
# ---------------------------------------------------------------------------
@mcp.tool()
async def edit_message(
    message_id: int,
    text: str,
    chat_id: str | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    """Edit the text of a message the bot previously sent."""
    res = await _call("editMessageText", {
        "chat_id": _resolve_chat(chat_id),
        "message_id": message_id,
        "text": text,
        "parse_mode": _check_parse_mode(parse_mode),
    })
    mid = res.get("message_id") if isinstance(res, dict) else message_id
    return {"ok": True, "message_id": mid}


@mcp.tool()
async def delete_message(message_id: int, chat_id: str | None = None) -> dict[str, Any]:
    """Delete a message. The bot must be admin (or it's the bot's own recent
    message). Telegram only allows deleting messages younger than 48h in groups."""
    await _call("deleteMessage", {"chat_id": _resolve_chat(chat_id), "message_id": message_id})
    return {"ok": True, "message_id": message_id}


@mcp.tool()
async def forward_message(
    message_id: int,
    from_chat_id: str,
    chat_id: str | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Forward a message from `from_chat_id` into the target group."""
    sent = await _call("forwardMessage", {
        "chat_id": _resolve_chat(chat_id),
        "from_chat_id": from_chat_id,
        "message_id": message_id,
        "disable_notification": disable_notification or None,
    })
    return {"message_id": sent.get("message_id"), "chat_id": (sent.get("chat") or {}).get("id")}


@mcp.tool()
async def pin_message(
    message_id: int,
    chat_id: str | None = None,
    disable_notification: bool = True,
) -> dict[str, Any]:
    """Pin a message in the group (bot must be admin with pin rights)."""
    await _call("pinChatMessage", {
        "chat_id": _resolve_chat(chat_id),
        "message_id": message_id,
        "disable_notification": disable_notification,
    })
    return {"ok": True, "message_id": message_id}


@mcp.tool()
async def unpin_message(message_id: int | None = None, chat_id: str | None = None) -> dict[str, Any]:
    """Unpin a specific message, or omit message_id to unpin the most recent pin."""
    await _call("unpinChatMessage", {
        "chat_id": _resolve_chat(chat_id),
        "message_id": message_id,
    })
    return {"ok": True, "message_id": message_id}


@mcp.tool()
async def set_reaction(
    message_id: int,
    emoji: str,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """React to a message with a single emoji (e.g. '👍', '🔥', '✅'). Pass an
    empty emoji '' to clear the bot's reaction."""
    reaction = [{"type": "emoji", "emoji": emoji}] if emoji else []
    await _call("setMessageReaction", {
        "chat_id": _resolve_chat(chat_id),
        "message_id": message_id,
        "reaction": reaction,
    })
    return {"ok": True, "message_id": message_id, "emoji": emoji}


if __name__ == "__main__":
    mcp.run()
