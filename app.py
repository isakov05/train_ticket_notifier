import asyncio
import logging
from datetime import datetime

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin as adm
import db
import handlers as h
from admin import admin_handlers
from checker import RailwayClient, build_snapshot, diff_snapshots
from config import ADMIN_ID, BOT_TOKEN, CHECK_INTERVAL

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

railway = RailwayClient()

USER_COMMANDS = [
    BotCommand("watch", "Add a new ticket watch"),
    BotCommand("list", "Show your active watches"),
    BotCommand("help", "Show help"),
]

ADMIN_COMMANDS = [
    *USER_COMMANDS,
    BotCommand("stats", "Show bot statistics"),
    BotCommand("watches", "Show all active watches"),
    BotCommand("users", "Show bot users"),
    BotCommand("broadcast", "Send message to all users"),
    BotCommand("forcecheck", "Trigger a ticket check now"),
    BotCommand("removewatch", "Remove a watch by ID"),
    BotCommand("sendmessage", "Send message to a user by ID"),
    BotCommand("setinterval", "Change ticket check interval"),
]

MIRROR_IGNORED_COMMANDS = {"/watch", "/list", "/help"}


async def _resolve_code(name: str) -> str | None:
    results = await railway.search_stations(name)
    if not results:
        return None
    name_lower = name.lower()
    for s in results:
        if s.get("name", "").lower() == name_lower:
            return s["code"]
    return results[0]["code"]


async def _check_sub(sub: dict, bot, today) -> None:
    sub_date = datetime.strptime(sub["date"], "%Y-%m-%d").date()

    if sub_date < today:
        await db.deactivate(sub["id"])
        try:
            await bot.send_message(
                chat_id=sub["chat_id"],
                text=(
                    f"Watch expired: {sub['dep_name']} → {sub['arv_name']} "
                    f"on {sub_date.strftime('%d %B %Y')}."
                ),
            )
        except Exception:
            pass
        return

    async with railway.bg_semaphore:
        trains = await railway.get_trains(sub["dep_code"], sub["arv_code"], sub["date"])

    if trains == "invalid_route":
        new_dep, new_arv = await asyncio.gather(
            _resolve_code(sub["dep_name"]),
            _resolve_code(sub["arv_name"]),
        )
        if new_dep and new_arv and (new_dep != sub["dep_code"] or new_arv != sub["arv_code"]):
            logger.info(
                "Auto-fixing codes for sub %s: %s→%s became %s→%s",
                sub["id"], sub["dep_code"], sub["arv_code"], new_dep, new_arv,
            )
            await db.update_subscription_codes(sub["id"], new_dep, new_arv)
        else:
            logger.warning("Sub %s got invalid_route for %s→%s, skipping this cycle", sub["id"], sub["dep_code"], sub["arv_code"])
        return

    if trains is None:
        logger.warning("Skipping snapshot update after failed railway check for subscription %s", sub["id"])
        return

    new_snapshot = build_snapshot(trains)
    old_snapshot = await db.get_snapshot(sub["id"])
    changes = diff_snapshots(old_snapshot, new_snapshot, trains)

    if changes:
        sep = "─" * 22
        text = (
            f"Tickets available!\n\n"
            f"<b>{sub['dep_name']} → {sub['arv_name']}</b>\n"
            f"{sub_date.strftime('%d %B %Y')}\n"
            f"{sep}\n"
            + "\n\n".join(changes)
            + f"\n\n{sep}\n"
            f'<a href="https://eticket.railway.uz">Book tickets</a>'
        )
        try:
            await bot.send_message(
                chat_id=sub["chat_id"],
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await db.set_user_blocked(sub["user_id"], False)
        except Forbidden:
            await db.set_user_blocked(sub["user_id"], True)
            logger.info("User %s has blocked the bot", sub["user_id"])
        except Exception as exc:
            logger.error("Failed to notify chat %s: %s", sub["chat_id"], exc)

    if new_snapshot != old_snapshot:
        await db.save_snapshot(sub["id"], new_snapshot)


async def _notify_admin_broken_watch(bot, sub: dict, exc: Exception) -> None:
    text = (
        "Broken watch during scheduled check\n\n"
        f"Watch ID: {sub.get('id')}\n"
        f"User ID: {sub.get('user_id')}\n"
        f"Chat ID: {sub.get('chat_id')}\n"
        f"Route: {sub.get('dep_name')} ({sub.get('dep_code')}) -> "
        f"{sub.get('arv_name')} ({sub.get('arv_code')})\n"
        f"Date: {sub.get('date')}\n"
        f"Error: {type(exc).__name__}: {str(exc)[:500]}"
    )
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as notify_exc:
        logger.error("Failed to notify admin about broken watch %s: %s", sub.get("id"), notify_exc)


async def _check_sub_safe(sub: dict, bot, today) -> None:
    try:
        await _check_sub(sub, bot, today)
    except Exception as exc:
        logger.exception("Unexpected error checking subscription %s", sub.get("id"))
        await _notify_admin_broken_watch(bot, sub, exc)


async def check_all(ctx) -> None:
    subs = await db.get_all_active()
    today = datetime.now().date()
    active_dates = {s["date"] for s in subs if datetime.strptime(s["date"], "%Y-%m-%d").date() >= today}
    railway.purge_cache(active_dates)
    if not subs:
        return
    await asyncio.gather(*[_check_sub_safe(sub, ctx.bot, today) for sub in subs])


async def mirror_user_message_to_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if message is None or user is None or chat is None or user.id == ADMIN_ID:
        return

    # Skip messages typed during the /watch conversation
    if ctx.user_data.get("_in_watch"):
        return

    if message.text:
        command = message.text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command in MIRROR_IGNORED_COMMANDS:
            return

    try:
        forwarded = await ctx.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
    except Exception as exc:
        logger.warning("Could not forward message %s from chat %s: %s", message.message_id, chat.id, exc)
        try:
            forwarded = await ctx.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=chat.id,
                message_id=message.message_id,
            )
        except Exception as copy_exc:
            logger.error("Could not copy message %s from chat %s: %s", message.message_id, chat.id, copy_exc)
            return

    # Store mapping in DB so admin can reply back to this user after restarts
    await db.save_forward_map(forwarded.message_id, chat.id)


async def handle_admin_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or update.effective_user.id != ADMIN_ID:
        return
    if message.reply_to_message is None:
        return

    original_chat_id = await db.get_forward_chat(message.reply_to_message.message_id)
    if original_chat_id is None:
        return

    try:
        await ctx.bot.copy_message(
            chat_id=original_chat_id,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
    except Exception as exc:
        logger.error("Failed to forward admin reply to chat %s: %s", original_chat_id, exc)


async def post_init(app: Application) -> None:
    await db.init_db()
    await app.bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    await app.bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    # Share the single railway client with handlers module
    h._railway = railway
    adm._check_all = check_all
    logger.info("Database initialised")


async def post_shutdown(app: Application) -> None:
    await railway.close()


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", h.cmd_start))
    app.add_handler(CommandHandler("help", h.cmd_help))
    app.add_handler(CommandHandler("list", h.cmd_list))
    app.add_handler(MessageHandler(filters.ALL, mirror_user_message_to_admin), group=-1)
    app.add_handler(MessageHandler(filters.Chat(ADMIN_ID) & filters.REPLY, handle_admin_reply))
    app.add_handler(h.watch_conversation())
    app.add_handler(CallbackQueryHandler(h.handle_remove, pattern=r"^remove:"))
    app.add_handler(CallbackQueryHandler(h.handle_check_now, pattern=r"^check:"))

    for handler in admin_handlers():
        app.add_handler(handler)

    job = app.job_queue.run_repeating(check_all, interval=CHECK_INTERVAL, first=10)
    adm._check_job = job
    adm._check_interval = CHECK_INTERVAL

    logger.info("Bot starting — checking every %ds", CHECK_INTERVAL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
