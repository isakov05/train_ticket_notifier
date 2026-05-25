import asyncio
import functools
import logging
from datetime import datetime

from telegram import Update
from telegram.error import Forbidden, TimedOut
from telegram.ext import CommandHandler, ContextTypes

import db
from config import ADMIN_ID

logger = logging.getLogger(__name__)

_check_all = None  # set by main.py after init


def admin_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("Not authorised.")
            return
        return await func(update, ctx)
    return wrapper


def _user_label(row: dict) -> str:
    name = row.get("first_name") or ""
    username = f"@{row['username']}" if row.get("username") else ""
    uid = row["user_id"]
    if username and name:
        return f"{name} {username} ({uid})"
    if username:
        return f"{username} ({uid})"
    if name:
        return f"{name} ({uid})"
    return str(uid)


@admin_only
async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    stats = await db.get_stats()
    await update.message.reply_text(
        f"Bot stats\n\n"
        f"Total users:      {stats['total_users']}\n"
        f"Active watches:   {stats['active_watches']}\n"
        f"All-time watches: {stats['total_watches_ever']}"
    )


@admin_only
async def cmd_watches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    watches = await db.get_all_watches()
    if not watches:
        await update.message.reply_text("No active watches.")
        return

    # Group by user
    grouped: dict[int, list[dict]] = {}
    for w in watches:
        grouped.setdefault(w["user_id"], []).append(w)

    lines = []
    for user_id, subs in grouped.items():
        label = _user_label(subs[0])
        lines.append(label)
        for s in subs:
            dt = datetime.strptime(s["date"], "%Y-%m-%d")
            lines.append(f"  • [{s['id']}] {s['dep_name']} → {s['arv_name']}  |  {dt.strftime('%d %b %Y')}")
        lines.append("")

    await _send_chunks(update, "\n".join(lines))


@admin_only
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("No users yet.")
        return

    lines = []
    for u in users:
        label = _user_label(u)
        watches = u["watch_count"]
        last = u["last_seen"][:10]
        status = "blocked" if u.get("blocked") else "active"
        lines.append(f"{label}  [{status}]\n  Watches: {watches}  |  Last seen: {last}")

    await _send_chunks(update, "\n\n".join(lines))


@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.partition(" ")[2].strip()
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    user_ids = await db.get_all_user_ids()
    sent = blocked = failed = 0

    for user_id in user_ids:
        try:
            await ctx.bot.send_message(chat_id=user_id, text=text)
            sent += 1
        except Forbidden:
            await db.set_user_blocked(user_id, True)
            blocked += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"Broadcast done.\n\nSent: {sent}  |  Blocked: {blocked}  |  Failed: {failed}"
    )


@admin_only
async def cmd_sendmessage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    parts = update.message.text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        await update.message.reply_text("Usage: /sendmessage <user_id> <message>")
        return
    user_id = int(parts[1])
    text = parts[2]
    try:
        await ctx.bot.send_message(chat_id=user_id, text=text)
        await update.message.reply_text(f"Sent to {user_id}.")
    except Forbidden:
        await db.set_user_blocked(user_id, True)
        await update.message.reply_text(f"User {user_id} has blocked the bot.")
    except Exception as exc:
        await update.message.reply_text(f"Failed: {exc}")


@admin_only
async def cmd_removewatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Usage: /removewatch <watch_id>")
        return
    sub_id = int(arg)
    subs = await db.get_all_watches()
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await update.message.reply_text(f"Watch {sub_id} not found or already inactive.")
        return
    await db.deactivate(sub_id)
    dt = datetime.strptime(sub["date"], "%Y-%m-%d")
    await update.message.reply_text(
        f"Watch {sub_id} removed.\n"
        f"{sub['dep_name']} → {sub['arv_name']}  |  {dt.strftime('%d %b %Y')}\n"
        f"User: {_user_label(sub)}"
    )


@admin_only
async def cmd_forcecheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if _check_all is None:
        await update.message.reply_text("Force check not available.")
        return
    await update.message.reply_text("Running check...")
    await _check_all(ctx)
    await update.message.reply_text("Done.")


async def _send_chunks(update: Update, text: str) -> None:
    for i in range(0, len(text), 4000):
        chunk = text[i:i + 4000]
        for attempt in range(3):
            try:
                await update.message.reply_text(chunk)
                break
            except TimedOut:
                if attempt == 2:
                    await update.message.reply_text("(message timed out, some results may be missing)")
                    return
                await asyncio.sleep(2)


def admin_handlers() -> list:
    return [
        CommandHandler("stats", cmd_stats),
        CommandHandler("watches", cmd_watches),
        CommandHandler("users", cmd_users),
        CommandHandler("broadcast", cmd_broadcast),
        CommandHandler("forcecheck", cmd_forcecheck),
        CommandHandler("removewatch", cmd_removewatch),
        CommandHandler("sendmessage", cmd_sendmessage),
    ]
