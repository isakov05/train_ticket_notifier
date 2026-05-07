import asyncio
import logging
from datetime import datetime

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
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
]


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
            sub = {**sub, "dep_code": new_dep, "arv_code": new_arv}
            async with railway.bg_semaphore:
                trains = await railway.get_trains(new_dep, new_arv, sub["date"])

        if trains == "invalid_route" or trains is None:
            await db.deactivate(sub["id"])
            try:
                await bot.send_message(
                    chat_id=sub["chat_id"],
                    text=(
                        f"Watch removed: {sub['dep_name']} → {sub['arv_name']} "
                        f"on {sub_date.strftime('%d %B %Y')}.\n"
                        "This route is no longer available on uzrailways."
                    ),
                )
            except Exception:
                pass
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


async def check_all(ctx) -> None:
    subs = await db.get_all_active()
    today = datetime.now().date()
    active_dates = {s["date"] for s in subs if datetime.strptime(s["date"], "%Y-%m-%d").date() >= today}
    railway.purge_cache(active_dates)
    if not subs:
        return
    await asyncio.gather(*[_check_sub(sub, ctx.bot, today) for sub in subs])


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
    app.add_handler(h.watch_conversation())
    app.add_handler(CallbackQueryHandler(h.handle_remove, pattern=r"^remove:"))
    app.add_handler(CallbackQueryHandler(h.handle_check_now, pattern=r"^check:"))

    for handler in admin_handlers():
        app.add_handler(handler)

    app.job_queue.run_repeating(check_all, interval=CHECK_INTERVAL, first=10)

    logger.info("Bot starting — checking every %ds", CHECK_INTERVAL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
