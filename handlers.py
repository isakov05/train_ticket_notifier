import logging
from datetime import datetime, timedelta
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from checker import RailwayClient, _normalize_car_type
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


async def _safe_answer(query, text: str = "", show_alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=show_alert)
    except BadRequest:
        pass

_CANCEL_ROW = [InlineKeyboardButton("Cancel", callback_data="cancel_watch")]
_BACK_CANCEL_ROW = [
    InlineKeyboardButton("Back", callback_data="back_to_dep"),
    InlineKeyboardButton("Cancel", callback_data="cancel_watch"),
]


def _h(value: object) -> str:
    return escape(str(value or ""), quote=False)


def _date_example() -> str:
    return (datetime.now() + timedelta(days=7)).strftime("%d.%m.%Y")


def _seat_label(count: int) -> str:
    return "seat" if count == 1 else "seats"


def _free_seats(car: dict) -> int:
    try:
        return int(car.get("freeSeats", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _format_train_html(train: dict) -> str:
    number = _h(str(train.get("number", "")).strip() or "?")
    dep_time = _h(train.get("departureDate", ""))
    arv_time = _h(train.get("arrivalDate", ""))
    duration = _h(train.get("timeOnWay", ""))
    cars = train.get("cars", [])

    time_str = f"{dep_time} → {arv_time}" if dep_time or arv_time else ""
    if duration:
        time_str += f"   ({duration})"

    available = [
        (_normalize_car_type(str(c.get("type", "unknown"))), _free_seats(c))
        for c in cars
        if _free_seats(c) > 0
    ]

    lines = [f"<b>Train {number}</b>   {time_str}".strip()]
    if available:
        lines.extend(
            f"  {_h(car_type)}: <b>{seats}</b> {_seat_label(seats)}"
            for car_type, seats in available
        )
    else:
        lines.append("  No seats available")

    return "\n".join(lines)


_SEP = "─" * 22


# ── /start ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    await db.upsert_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        f"Hi {_h(user.first_name)}! I monitor train tickets on uzrailways.\n\n"
        f"Your chat ID: <code>{chat_id}</code>\n\n"
        "Commands:\n"
        "/watch — add a new ticket watch\n"
        "/list  — your active watches\n"
        "/help  — show this message",
        parse_mode="HTML",
    )


# ── /watch conversation ──────────────────────────────────────────────────────

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    ctx.user_data["_in_watch"] = True
    await update.message.reply_text(
        "Step 1/3 — Departure\n\n"
        "Type the departure city or station name:",
        reply_markup=InlineKeyboardMarkup([_CANCEL_ROW]),
    )
    return SEARCH_DEP


async def handle_dep_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            "No stations found. Try a different spelling (e.g. Toshkent, Samarqand):",
            reply_markup=InlineKeyboardMarkup([_CANCEL_ROW]),
        )
        return SEARCH_DEP

    ctx.user_data["dep_matches"] = matches
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"dep:{index}")]
        for index, (name, _code) in enumerate(matches)
    ]
    buttons.append(_CANCEL_ROW)
    await update.message.reply_text(
        "Select departure station:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_DEP


async def handle_dep_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    try:
        index = int(query.data.split(":", 1)[1])
        name, code = ctx.user_data["dep_matches"][index]
    except (KeyError, IndexError, ValueError):
        await _safe_answer(query, "Please search again.", show_alert=True)
        return SEARCH_DEP

    ctx.user_data["dep"] = {"code": code, "name": name}
    await query.edit_message_text(f"Departure: {name}")
    await query.message.reply_text(
        "Step 2/3 — Arrival\n\n"
        "Type the arrival city or station name:",
        reply_markup=InlineKeyboardMarkup([_BACK_CANCEL_ROW]),
    )
    return SEARCH_ARV


async def handle_arv_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            "No stations found. Try a different spelling:",
            reply_markup=InlineKeyboardMarkup([_BACK_CANCEL_ROW]),
        )
        return SEARCH_ARV

    ctx.user_data["arv_matches"] = matches
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"arv:{index}")]
        for index, (name, _code) in enumerate(matches)
    ]
    buttons.append(_BACK_CANCEL_ROW)
    await update.message.reply_text(
        "Select arrival station:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_ARV


async def handle_arv_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    try:
        index = int(query.data.split(":", 1)[1])
        name, code = ctx.user_data["arv_matches"][index]
    except (KeyError, IndexError, ValueError):
        await _safe_answer(query, "Please search again.", show_alert=True)
        return SEARCH_ARV

    ctx.user_data["arv"] = {"code": code, "name": name}
    await query.edit_message_text(f"Arrival: {name}")
    dep = ctx.user_data["dep"]
    await query.message.reply_text(
        f"Step 3/3 — Date\n\n"
        f"Route: {dep['name']} → {name}\n\n"
        "Enter the travel date in DD.MM.YYYY format\n"
        f"(e.g. {_date_example()}):",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Back", callback_data="back_to_arv"),
            InlineKeyboardButton("Cancel", callback_data="cancel_watch"),
        ]]),
    )
    return ENTER_DATE


async def handle_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text(
            f"Invalid format. Please use DD.MM.YYYY (e.g. {_date_example()}):",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="cancel_watch"),
            ]]),
        )
        return ENTER_DATE

    if dt.date() < datetime.now().date():
        await update.message.reply_text(
            "That date is in the past. Please enter a future date:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Cancel", callback_data="cancel_watch"),
            ]]),
        )
        return ENTER_DATE

    dep = ctx.user_data["dep"]
    arv = ctx.user_data["arv"]
    date_str = dt.strftime("%Y-%m-%d")

    existing = await db.get_user_subscriptions(update.effective_user.id)

    if len(existing) >= 3:
        await update.message.reply_text(
            "You have reached the limit of 3 active watches.\n"
            "Use /list to remove one before adding a new watch."
        )
        ctx.user_data.clear()
        return ConversationHandler.END

    duplicate = any(
        s["dep_code"] == dep["code"] and s["arv_code"] == arv["code"] and s["date"] == date_str
        for s in existing
    )
    if duplicate:
        await update.message.reply_text(
            f"You already have a watch for {dep['name']} -> {arv['name']} on {dt.strftime('%d %B %Y')}.\n"
            "Use /list to see your active watches."
        )
        ctx.user_data.clear()
        return ConversationHandler.END

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
        f"Watch added!\n\n"
        f"{dep['name']} -> {arv['name']}\n"
        f"Date: {dt.strftime('%d %B %Y')}\n\n"
        f"You will be notified when tickets become available. (ID: {sub_id})\n"
        f"Use /list to see all your watches.",
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def handle_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    ctx.user_data.clear()
    await query.edit_message_text("Watch cancelled.")
    return ConversationHandler.END


async def handle_back_to_dep(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    ctx.user_data.pop("dep", None)
    await query.edit_message_text(
        "Step 1/3 — Departure\n\n"
        "Type the departure city or station name:",
        reply_markup=InlineKeyboardMarkup([_CANCEL_ROW]),
    )
    return SEARCH_DEP


async def handle_back_to_arv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    ctx.user_data.pop("arv", None)
    dep = ctx.user_data.get("dep", {})
    dep_name = dep.get("name", "")
    await query.edit_message_text(
        f"Step 2/3 — Arrival\n\n"
        f"Departure: {dep_name}\n\n"
        "Type the arrival city or station name:",
        reply_markup=InlineKeyboardMarkup([_BACK_CANCEL_ROW]),
    )
    return SEARCH_ARV


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
            f"<b>{_h(sub['dep_name'])} → {_h(sub['arv_name'])}</b>\n"
            f"Date: {dt.strftime('%d %B %Y')}\n"
            f"Watch ID: <code>{sub['id']}</code>"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Check now", callback_data=f"check:{sub['id']}"),
            InlineKeyboardButton("Remove", callback_data=f"remove:{sub['id']}"),
        ]])
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def handle_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    sub_id = int(query.data.split(":")[1])

    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await _safe_answer(query, "Watch not found.", show_alert=True)
        return

    await db.deactivate(sub_id)
    await query.edit_message_text("Watch removed.")


async def handle_check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query, "Checking...")

    sub_id = int(query.data.split(":")[1])
    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await _safe_answer(query, "Watch not found.", show_alert=True)
        return

    trains = await _railway.get_trains(sub["dep_code"], sub["arv_code"], sub["date"])

    dt = datetime.strptime(sub["date"], "%Y-%m-%d")
    header = (
        f"<b>{_h(sub['dep_name'])} → {_h(sub['arv_name'])}</b>\n"
        f"{dt.strftime('%d %B %Y')}\n"
        f"{_SEP}"
    )

    if trains is None:
        await query.message.reply_text(
            header + "\nCould not reach railway.uz. Try again later.",
            parse_mode="HTML",
        )
        return

    if not trains:
        await query.message.reply_text(
            header + "\nNo trains found for this route and date.",
            parse_mode="HTML",
        )
        return

    body = f"\n\n".join(_format_train_html(t) for t in trains)
    await query.message.reply_text(
        f"{header}\n\n{body}\n\n{_SEP}\n"
        f'<a href="https://eticket.railway.uz">Book tickets</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
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
                CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel_watch$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_dep_search),
            ],
            SEARCH_ARV: [
                CallbackQueryHandler(handle_arv_pick, pattern=r"^arv:"),
                CallbackQueryHandler(handle_back_to_dep, pattern=r"^back_to_dep$"),
                CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel_watch$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_arv_search),
            ],
            ENTER_DATE: [
                CallbackQueryHandler(handle_back_to_arv, pattern=r"^back_to_arv$"),
                CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel_watch$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
