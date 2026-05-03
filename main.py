import logging
from datetime import datetime

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

import db
import handlers as h
from checker import RailwayClient, build_snapshot, diff_snapshots
from config import BOT_TOKEN, CHECK_INTERVAL

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

railway = RailwayClient()


async def check_all(ctx) -> None:
    subs = await db.get_all_active()
    if not subs:
        return

    today = datetime.now().date()

    for sub in subs:
        sub_date = datetime.strptime(sub["date"], "%Y-%m-%d").date()

        if sub_date < today:
            await db.deactivate(sub["id"])
            try:
                await ctx.bot.send_message(
                    chat_id=sub["chat_id"],
                    text=(
                        f"Watch expired: {sub['dep_name']} → {sub['arv_name']} "
                        f"on {sub_date.strftime('%d %B %Y')}."
                    ),
                )
            except Exception:
                pass
            continue

        trains = await railway.get_trains(sub["dep_code"], sub["arv_code"], sub["date"])
        new_snapshot = build_snapshot(trains)
        old_snapshot = await db.get_snapshot(sub["id"])
        changes = diff_snapshots(old_snapshot, new_snapshot, trains)

        if changes:
            date_fmt = sub_date.strftime("%d %B %Y")
            text = (
                f"New tickets available!\n"
                f"Route: {sub['dep_name']} → {sub['arv_name']}\n"
                f"Date: {date_fmt}\n\n"
                + "\n\n".join(changes)
                + "\n\nBook: https://eticket.railway.uz"
            )
            try:
                await ctx.bot.send_message(chat_id=sub["chat_id"], text=text)
            except Exception as exc:
                logger.error("Failed to notify chat %s: %s", sub["chat_id"], exc)

        if new_snapshot != old_snapshot:
            await db.save_snapshot(sub["id"], new_snapshot)


async def post_init(app: Application) -> None:
    await db.init_db()
    # Share the single railway client with handlers module
    h._railway = railway
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

    app.job_queue.run_repeating(check_all, interval=CHECK_INTERVAL, first=10)

    logger.info("Bot starting — checking every %ds", CHECK_INTERVAL)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
