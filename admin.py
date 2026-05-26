import asyncio
import functools
import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Forbidden, TimedOut
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

import db
from config import ADMIN_ID

logger = logging.getLogger(__name__)

_check_all = None   # set by app.py after init
_check_job = None   # set by app.py after init
_check_interval = None  # current interval in seconds


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


_USERS_PAGE_SIZE = 15


def _build_users_page(users: list[dict], page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = len(users)
    active = sum(1 for u in users if not u.get("blocked"))
    blocked = total - active
    total_pages = max(1, (total + _USERS_PAGE_SIZE - 1) // _USERS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * _USERS_PAGE_SIZE
    page_users = users[start:start + _USERS_PAGE_SIZE]

    lines = [f"Users: {total}  |  Active: {active}  |  Blocked: {blocked}  |  Page {page + 1}/{total_pages}\n"]
    for u in page_users:
        label = _user_label(u)
        status = "blocked" if u.get("blocked") else "active"
        lines.append(f"[{status}] {label}  W:{u['watch_count']}  {u['last_seen'][:10]}")

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("« Prev", callback_data=f"users_page:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next »", callback_data=f"users_page:{page + 1}"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else InlineKeyboardMarkup([])
    return "\n".join(lines), markup


@admin_only
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    users = await db.get_all_users()
    if not users:
        await update.message.reply_text("No users yet.")
        return
    text, markup = _build_users_page(users, 0)
    await update.message.reply_text(text, reply_markup=markup)


async def handle_users_page(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer()
        return
    page = int(query.data.split(":")[1])
    users = await db.get_all_users()
    text, markup = _build_users_page(users, page)
    await query.answer()
    await query.edit_message_text(text, reply_markup=markup)


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
            await db.set_user_blocked(user_id, False)
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
async def cmd_activatewatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await update.message.reply_text("Usage: /activatewatch <watch_id>")
        return
    sub_id = int(arg)
    sub = await db.get_subscription(sub_id)
    if sub is None:
        await update.message.reply_text(f"Watch {sub_id} not found.")
        return
    if sub["active"]:
        await update.message.reply_text(f"Watch {sub_id} is already active.")
        return
    await db.activate(sub_id)
    dt = datetime.strptime(sub["date"], "%Y-%m-%d")
    await update.message.reply_text(
        f"Watch {sub_id} activated.\n"
        f"{sub['dep_name']} → {sub['arv_name']}  |  {dt.strftime('%d %b %Y')}\n"
        f"User: {_user_label(sub)}"
    )


@admin_only
async def cmd_setinterval(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    global _check_job, _check_interval
    arg = update.message.text.partition(" ")[2].strip()
    if not arg.isdigit() or int(arg) < 15:
        current = f"{_check_interval}s" if _check_interval else "unknown"
        await update.message.reply_text(
            f"Current interval: {current}\n"
            "Usage: /setinterval <seconds>  (minimum 15)"
        )
        return
    new_interval = int(arg)
    if _check_job is None or _check_all is None:
        await update.message.reply_text("Scheduler not available.")
        return
    _check_job.schedule_removal()
    _check_job = ctx.application.job_queue.run_repeating(
        _check_all, interval=new_interval, first=new_interval
    )
    _check_interval = new_interval
    await update.message.reply_text(f"Check interval updated to {new_interval}s.")


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
        CommandHandler("activatewatch", cmd_activatewatch),
        CommandHandler("setinterval", cmd_setinterval),
        CallbackQueryHandler(handle_users_page, pattern=r"^users_page:"),
    ]
