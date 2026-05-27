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

import admin as adm
import db
from checker import RailwayClient, _normalize_car_type
from config import TZ
from stations import search_stations
from translations import t

_railway = RailwayClient()


def _interval_str() -> str:
    secs = adm._check_interval or 300
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''}"
    mins = secs // 60
    return f"{mins} minute{'s' if mins != 1 else ''}"


async def _search_stations(query: str) -> list[tuple[str, str]]:
    """Try live API first, fall back to local list."""
    results = await _railway.search_stations(query)
    if results:
        return [(s["name"].title(), s["code"]) for s in results]
    return search_stations(query)


async def _lang(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    if "lang" not in ctx.user_data:
        ctx.user_data["lang"] = await db.get_user_language(user_id)
    return ctx.user_data["lang"]


logger = logging.getLogger(__name__)

SEARCH_DEP, SEARCH_ARV, ENTER_DATE = range(3)


async def _safe_answer(query, text: str = "", show_alert: bool = False) -> None:
    try:
        await query.answer(text, show_alert=show_alert)
    except BadRequest:
        pass


def _cancel_row(lang: str) -> list:
    return [InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_watch")]


def _back_cancel_row(lang: str) -> list:
    return [
        InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_dep"),
        InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_watch"),
    ]


def _h(value: object) -> str:
    return escape(str(value or ""), quote=False)


def _date_example() -> str:
    return (datetime.now(TZ) + timedelta(days=7)).strftime("%d.%m.%Y")


def _seat_label(count: int, lang: str) -> str:
    return t("seat_one", lang) if count == 1 else t("seat_many", lang)


def _free_seats(car: dict) -> int:
    try:
        return int(car.get("freeSeats", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _format_train_html(train: dict, lang: str = "en") -> str:
    number = _h(str(train.get("number", "")).strip() or "?")
    dep_time = _h(train.get("departureDate", ""))
    arv_time = _h(train.get("arrivalDate", ""))
    duration = _h(train.get("timeOnWay", ""))
    cars = train.get("cars", [])

    time_str = f"{dep_time} → {arv_time}" if dep_time or arv_time else ""
    if duration:
        time_str += f"   ({duration})"

    merged: dict[str, int] = {}
    for c in cars:
        car_type = _normalize_car_type(str(c.get("type", "unknown")))
        free = _free_seats(c)
        if free > 0:
            merged[car_type] = merged.get(car_type, 0) + free
    available = list(merged.items())

    lines = [f"<b>Train {number}</b>   {time_str}".strip()]
    if available:
        lines.extend(
            f"  {_h(car_type)}: <b>{seats}</b> {_seat_label(seats, lang)}"
            for car_type, seats in available
        )
    else:
        lines.append(f"  {t('no_seats', lang)}")

    return "\n".join(lines)


_SEP = "─" * 22


# ── /start ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    await db.upsert_user(user.id, user.username, user.first_name)
    lang = await _lang(ctx, user.id)
    await update.message.reply_text(
        t("start", lang, name=_h(user.first_name), chat_id=chat_id),
        parse_mode="HTML",
    )


# ── /language ────────────────────────────────────────────────────────────────

async def cmd_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _lang(ctx, update.effective_user.id)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(t("btn_english", lang), callback_data="setlang:en"),
        InlineKeyboardButton(t("btn_karakalpak", lang), callback_data="setlang:kaa"),
    ]])
    await update.message.reply_text(t("language_choose", lang), reply_markup=keyboard)


async def handle_setlang(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    new_lang = query.data.split(":")[1]
    ctx.user_data["lang"] = new_lang
    await db.set_user_language(update.effective_user.id, new_lang)
    await query.edit_message_text(t("language_set", new_lang))


# ── /watch conversation ──────────────────────────────────────────────────────

async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await db.upsert_user(user.id, user.username, user.first_name)
    ctx.user_data["_in_watch"] = True
    lang = await _lang(ctx, user.id)
    await update.message.reply_text(
        t("watch_step1", lang),
        reply_markup=InlineKeyboardMarkup([_cancel_row(lang)]),
    )
    return SEARCH_DEP


async def handle_dep_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await _lang(ctx, update.effective_user.id)
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            t("no_stations", lang),
            reply_markup=InlineKeyboardMarkup([_cancel_row(lang)]),
        )
        return SEARCH_DEP

    ctx.user_data["dep_matches"] = matches
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"dep:{index}")]
        for index, (name, _code) in enumerate(matches)
    ]
    buttons.append(_cancel_row(lang))
    await update.message.reply_text(
        t("select_dep", lang),
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_DEP


async def handle_dep_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    try:
        index = int(query.data.split(":", 1)[1])
        name, code = ctx.user_data["dep_matches"][index]
    except (KeyError, IndexError, ValueError):
        await _safe_answer(query, "Please search again.", show_alert=True)
        return SEARCH_DEP

    ctx.user_data["dep"] = {"code": code, "name": name}
    await query.edit_message_text(t("dep_selected", lang, name=name))
    await query.message.reply_text(
        t("watch_step2", lang),
        reply_markup=InlineKeyboardMarkup([_back_cancel_row(lang)]),
    )
    return SEARCH_ARV


async def handle_arv_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await _lang(ctx, update.effective_user.id)
    query = update.message.text.strip()
    matches = await _search_stations(query)

    if not matches:
        await update.message.reply_text(
            t("no_stations_arv", lang),
            reply_markup=InlineKeyboardMarkup([_back_cancel_row(lang)]),
        )
        return SEARCH_ARV

    ctx.user_data["arv_matches"] = matches
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"arv:{index}")]
        for index, (name, _code) in enumerate(matches)
    ]
    buttons.append(_back_cancel_row(lang))
    await update.message.reply_text(
        t("select_arv", lang),
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SEARCH_ARV


async def handle_arv_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    try:
        index = int(query.data.split(":", 1)[1])
        name, code = ctx.user_data["arv_matches"][index]
    except (KeyError, IndexError, ValueError):
        await _safe_answer(query, "Please search again.", show_alert=True)
        return SEARCH_ARV

    ctx.user_data["arv"] = {"code": code, "name": name}
    await query.edit_message_text(t("arv_selected", lang, name=name))
    dep = ctx.user_data["dep"]
    await query.message.reply_text(
        t("watch_step3", lang, dep=dep["name"], arv=name, example=_date_example()),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(t("btn_back", lang), callback_data="back_to_arv"),
            InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_watch"),
        ]]),
    )
    return ENTER_DATE


async def handle_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await _lang(ctx, update.effective_user.id)
    text = update.message.text.strip()
    try:
        dt = datetime.strptime(text, "%d.%m.%Y")
    except ValueError:
        await update.message.reply_text(
            t("invalid_date", lang, example=_date_example()),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_watch"),
            ]]),
        )
        return ENTER_DATE

    if dt.date() < datetime.now(TZ).date():
        await update.message.reply_text(
            t("past_date", lang),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(t("btn_cancel", lang), callback_data="cancel_watch"),
            ]]),
        )
        return ENTER_DATE

    dep = ctx.user_data["dep"]
    arv = ctx.user_data["arv"]
    date_str = dt.strftime("%Y-%m-%d")

    existing = await db.get_user_subscriptions(update.effective_user.id)

    if len(existing) >= 3:
        await update.message.reply_text(t("watch_limit", lang))
        ctx.user_data.clear()
        return ConversationHandler.END

    duplicate = any(
        s["dep_code"] == dep["code"] and s["arv_code"] == arv["code"] and s["date"] == date_str
        for s in existing
    )
    if duplicate:
        await update.message.reply_text(
            t("duplicate_watch", lang, dep=dep["name"], arv=arv["name"], date=dt.strftime("%d %B %Y"))
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
        t("watch_added", lang, dep=dep["name"], arv=arv["name"], date=dt.strftime("%d %B %Y"), id=sub_id)
    )
    ctx.user_data.clear()
    return ConversationHandler.END


async def handle_cancel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    ctx.user_data.clear()
    await query.edit_message_text(t("watch_cancelled", lang))
    return ConversationHandler.END


async def handle_back_to_dep(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    ctx.user_data.pop("dep", None)
    await query.edit_message_text(
        t("watch_step1", lang),
        reply_markup=InlineKeyboardMarkup([_cancel_row(lang)]),
    )
    return SEARCH_DEP


async def handle_back_to_arv(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    ctx.user_data.pop("arv", None)
    dep_name = ctx.user_data.get("dep", {}).get("name", "")
    await query.edit_message_text(
        t("watch_step2_with_dep", lang, dep_name=dep_name),
        reply_markup=InlineKeyboardMarkup([_back_cancel_row(lang)]),
    )
    return SEARCH_ARV


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    lang = await _lang(ctx, update.effective_user.id)
    ctx.user_data.clear()
    await update.message.reply_text(t("cancelled", lang))
    return ConversationHandler.END


# ── /list ────────────────────────────────────────────────────────────────────

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _lang(ctx, update.effective_user.id)
    subs = await db.get_user_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text(t("no_watches", lang))
        return

    for sub in subs:
        dt = datetime.strptime(sub["date"], "%Y-%m-%d")
        text = (
            f"<b>{_h(sub['dep_name'])} → {_h(sub['arv_name'])}</b>\n"
            f"Date: {dt.strftime('%d %B %Y')}\n"
            f"Watch ID: <code>{sub['id']}</code>"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(t("btn_check_now", lang), callback_data=f"check:{sub['id']}"),
            InlineKeyboardButton(t("btn_remove", lang), callback_data=f"remove:{sub['id']}"),
        ]])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sub_id = int(query.data.split(":")[1])

    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await _safe_answer(query, t("watch_not_found", lang), show_alert=True)
        return

    await db.deactivate(sub_id)
    await query.edit_message_text(t("watch_removed", lang))


async def handle_check_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    lang = await _lang(ctx, update.effective_user.id)
    await _safe_answer(query, t("checking", lang))

    sub_id = int(query.data.split(":")[1])
    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await _safe_answer(query, t("watch_not_found", lang), show_alert=True)
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
            header + f"\n{t('railway_unreachable', lang)}",
            parse_mode="HTML",
        )
        return

    if not trains:
        await query.message.reply_text(
            header + f"\n{t('no_trains', lang)}",
            parse_mode="HTML",
        )
        return

    body = "\n\n".join(_format_train_html(tr, lang) for tr in trains)
    await query.message.reply_text(
        f"{header}\n\n{body}\n\n{_SEP}\n"
        f'<a href="https://eticket.railway.uz">{t("book_tickets", lang)}</a>',
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

    from checker import build_snapshot
    new_snapshot = build_snapshot(trains)
    old_snapshot = await db.get_snapshot(sub_id)
    if new_snapshot != old_snapshot:
        await db.save_snapshot(sub_id, new_snapshot)


# ── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _lang(ctx, update.effective_user.id)
    await update.message.reply_text(t("help", lang, interval=_interval_str()))


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
