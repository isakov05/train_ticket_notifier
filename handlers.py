import logging
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from checker import RailwayClient
from stations import search_stations

_railway = RailwayClient()


async def _search_stations(query: str) -> list[tuple[str, str]]:
    """Try live API first, fall back to local list."""
    results = await _railway.search_stations(query)
    if results:
        return [(s["name"].title(), s["code"]) for s in results]
    return search_stations(query)

logger = logging.getLogger(__name__)

SEARCH_DEP, SEARCH_ARV, ENTER_DATE = range(3)


# ── /start ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"Hi {user.first_name}! I monitor train tickets on uzrailways.\n\n"
        f"Your chat ID: <code>{chat_id}</code>\n\n"
        "Commands:\n"
        "/watch — add a new ticket watch\n"
        "/list  — your active watches\n"
        "/help  — show this message",
        parse_mode="HTML",
    )


# ── /watch conversation ──────────────────────────────────────────────────────

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Type the *departure* city or station name:",
        parse_mode="Markdown",
    )
    return SEARCH_DEP


async def handle_dep_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            "No stations found. Try a different spelling (e.g. Toshkent, Samarqand):"
        )
        return SEARCH_DEP

    buttons = [
        [InlineKeyboardButton(name, callback_data=f"dep:{code}:{name}")]
        for name, code in matches
    ]
    await update.message.reply_text(
        "Select departure station:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_DEP


async def handle_dep_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, code, name = query.data.split(":", 2)
    ctx.user_data["dep"] = {"code": code, "name": name}
    await query.edit_message_text(f"Departure: *{name}*", parse_mode="Markdown")
    await query.message.reply_text(
        "Now type the *arrival* city or station name:",
        parse_mode="Markdown",
    )
    return SEARCH_ARV


async def handle_arv_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            "No stations found. Try a different spelling:"
        )
        return SEARCH_ARV

    buttons = [
        [InlineKeyboardButton(name, callback_data=f"arv:{code}:{name}")]
        for name, code in matches
    ]
    await update.message.reply_text(
        "Select arrival station:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_ARV


async def handle_arv_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, code, name = query.data.split(":", 2)
    ctx.user_data["arv"] = {"code": code, "name": name}
    await query.edit_message_text(f"Arrival: *{name}*", parse_mode="Markdown")
    await query.message.reply_text(
        "Enter the travel date in *DD.MM.YYYY* format (e.g. 25.05.2025):",
        parse_mode="Markdown",
    )
    return ENTER_DATE


async def handle_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text(
            "Invalid format. Please use DD.MM.YYYY (e.g. 25.05.2025):"
        )
        return ENTER_DATE

    if dt.date() < datetime.now().date():
        await update.message.reply_text(
            "That date is in the past. Please enter a future date:"
        )
        return ENTER_DATE

    dep = ctx.user_data["dep"]
    arv = ctx.user_data["arv"]
    date_str = dt.strftime("%Y-%m-%d")

    sub_id = await db.add_subscription(
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        dep_code=dep["code"],
        dep_name=dep["name"],
        arv_code=arv["code"],
        arv_name=arv["name"],
        date=date_str,
    )

    await update.message.reply_text(
        f"Watching tickets:\n"
        f"*{dep['name']}* → *{arv['name']}*\n"
        f"Date: {dt.strftime('%d %B %Y')}\n\n"
        f"I'll notify you when tickets become available. (ID: {sub_id})\n"
        f"Use /list to see all your watches.",
        parse_mode="Markdown",
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ── /list ────────────────────────────────────────────────────────────────────

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    subs = await db.get_user_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text(
            "You have no active watches. Use /watch to add one."
        )
        return

    for sub in subs:
        dt = datetime.strptime(sub["date"], "%Y-%m-%d")
        text = (
            f"*{sub['dep_name']}* → *{sub['arv_name']}*\n"
            f"Date: {dt.strftime('%d %B %Y')}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Check now", callback_data=f"check:{sub['id']}"),
            InlineKeyboardButton("Remove", callback_data=f"remove:{sub['id']}"),
        ]])
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


async def handle_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    sub_id = int(query.data.split(":")[1])
    await db.deactivate(sub_id)
    await query.edit_message_text("Watch removed.")


async def handle_check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Checking...")

    sub_id = int(query.data.split(":")[1])
    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await query.answer("Watch not found.", show_alert=True)
        return

    trains = await _railway.get_trains(sub["dep_code"], sub["arv_code"], sub["date"])

    dt = datetime.strptime(sub["date"], "%Y-%m-%d")
    header = (
        f"*{sub['dep_name']}* → *{sub['arv_name']}*\n"
        f"Date: {dt.strftime('%d %B %Y')}\n\n"
    )

    if not trains:
        await query.message.reply_text(header + "No trains found for this route and date.", parse_mode="Markdown")
        return

    lines = []
    for train in trains:
        number = str(train.get("number", "")).strip()
        dep_time = train.get("departureDate", "")
        arv_time = train.get("arrivalDate", "")
        duration = train.get("timeOnWay", "")

        time_str = f"{dep_time} → {arv_time}"
        if duration:
            time_str += f"  ({duration})"

        cars = train.get("cars", [])
        available = [(c["type"], c["freeSeats"]) for c in cars if c.get("freeSeats", 0) > 0]

        if available:
            seat_lines = "  " + ",  ".join(f"{t}: {s} seats" for t, s in available)
            lines.append(f"Train {number}  {time_str}\n{seat_lines}")
        else:
            lines.append(f"Train {number}  {time_str}\n  No seats available")

    await query.message.reply_text(
        header + "\n\n".join(lines) + "\n\nBook: https://eticket.railway.uz",
        parse_mode="Markdown",
    )


# ── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/watch — add a new ticket watch\n"
        "/list  — your active watches\n"
        "/help  — this message\n\n"
        "I check for new tickets every 5 minutes and notify you instantly."
    )


# ── conversation handler factory ─────────────────────────────────────────────

def watch_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("watch", cmd_watch)],
        states={
            SEARCH_DEP: [
                CallbackQueryHandler(handle_dep_pick, pattern=r"^dep:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dep_search),
            ],
            SEARCH_ARV: [
                CallbackQueryHandler(handle_arv_pick, pattern=r"^arv:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_arv_search),
            ],
            ENTER_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
