import functools
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


def not_banned(func):
    @functools.wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user and await db.is_user_banned(user.id):
            msg = update.message or (update.callback_query.message if update.callback_query else None)
            if msg:
                await msg.reply_text("You are banned from using this bot. Contact the admin.")
            return ConversationHandler.END
        return await func(update, ctx)
    return wrapper


async def _lang(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    if "lang" not in ctx.user_data:
        ctx.user_data["lang"] = await db.get_user_language(user_id)
    return ctx.user_data["lang"]


logger = logging.getLogger(__name__)

SEARCH_DEP, SEARCH_ARV, ENTER_DATE, SELECT_FILTER = range(4)

_FILTER_TYPES = [
    ("platzkart", "Platzkart"),
    ("coupe", "Coupe"),
    ("sv", "SV"),
    ("lux", "Lux"),
]
_FILTER_LABELS: dict[str, str] = dict(_FILTER_TYPES)


def _filter_display(car_filter: str | None, lang: str) -> str:
    if not car_filter:
        return t("filter_any_label", lang)
    return ", ".join(_FILTER_LABELS.get(k, k.title()) for k in car_filter.split(","))


def _filter_keyboard(
    selected: set,
    lang: str,
    toggle_pfx: str,
    confirm_cb: str,
    any_cb: str,
    cancel_cb: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{'✅' if k in selected else '☐'} {label}",
                callback_data=f"{toggle_pfx}:{k}",
            )
            for k, label in _FILTER_TYPES[i:i + 2]
        ]
        for i in range(0, len(_FILTER_TYPES), 2)
    ]
    rows.append([
        InlineKeyboardButton(t("btn_filter_any", lang), callback_data=any_cb),
        InlineKeyboardButton(t("btn_filter_confirm", lang), callback_data=confirm_cb),
    ])
    rows.append([InlineKeyboardButton(t("btn_cancel", lang), callback_data=cancel_cb)])
    return InlineKeyboardMarkup(rows)


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


def _format_berth_detail(seat_detail: dict, lang: str) -> str:
    parts = []
    mapping = [
        ("down",      "berth_lower"),
        ("up",        "berth_upper"),
        ("lateralDn", "berth_bokovoy_lower"),
        ("lateralUp", "berth_bokovoy_upper"),
    ]
    for key, label_key in mapping:
        count = int(seat_detail.get(key) or 0)
        if count > 0:
            parts.append(f"{t(label_key, lang)}: {count}")
    return " · ".join(parts)


def _format_train_html(train: dict, lang: str = "en") -> str:
    number = _h(str(train.get("number", "")).strip() or "?")
    dep_time = _h(train.get("departureDate", ""))
    arv_time = _h(train.get("arrivalDate", ""))
    duration = _h(train.get("timeOnWay", ""))
    cars = train.get("cars", [])

    time_str = f"{dep_time} → {arv_time}" if dep_time or arv_time else ""
    if duration:
        time_str += f"   ({duration})"

    # Merge cars of same type, accumulating seatDetail counts
    merged_seats: dict[str, int] = {}
    merged_detail: dict[str, dict] = {}
    for c in cars:
        car_type = _normalize_car_type(str(c.get("type", "unknown")))
        free = _free_seats(c)
        if free <= 0:
            continue
        merged_seats[car_type] = merged_seats.get(car_type, 0) + free
        sd = c.get("seatDetail") or {}
        if car_type not in merged_detail:
            merged_detail[car_type] = {"down": 0, "up": 0, "lateralDn": 0, "lateralUp": 0}
        for k in ("down", "up", "lateralDn", "lateralUp"):
            merged_detail[car_type][k] += int(sd.get(k) or 0)

    lines = [f"<b>Train {number}</b>   {time_str}".strip()]
    if merged_seats:
        for car_type, seats in merged_seats.items():
            lines.append(f"  {_h(car_type)}: <b>{seats}</b> {_seat_label(seats, lang)}")
            detail_str = _format_berth_detail(merged_detail.get(car_type, {}), lang)
            if detail_str:
                lines.append(f"    {detail_str}")
    else:
        lines.append(f"  {t('no_seats', lang)}")

    return "\n".join(lines)


_SEP = "─" * 22


# ── /start ──────────────────────────────────────────────────────────────────

@not_banned
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    await db.upsert_user(user.id, user.username, user.first_name)
    await db.set_user_blocked(user.id, False)
    lang = await _lang(ctx, user.id)
    await update.message.reply_text(
        t("start", lang, name=_h(user.first_name), chat_id=chat_id),
        parse_mode="HTML",
    )


# ── /language ────────────────────────────────────────────────────────────────

@not_banned
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

@not_banned
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

    if any(s["dep_code"] == dep["code"] and s["arv_code"] == arv["code"] and s["date"] == date_str for s in existing):
        await update.message.reply_text(
            t("duplicate_watch", lang, dep=dep["name"], arv=arv["name"], date=dt.strftime("%d %B %Y"))
        )
        ctx.user_data.clear()
        return ConversationHandler.END

    ctx.user_data["pending_date"] = date_str
    ctx.user_data["filter_sel"] = set()
    await update.message.reply_text(
        t("filter_step", lang),
        reply_markup=_filter_keyboard(
            set(), lang,
            toggle_pfx="filter_toggle",
            confirm_cb="filter_confirm",
            any_cb="filter_any",
            cancel_cb="cancel_watch",
        ),
    )
    return SELECT_FILTER


async def handle_filter_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    key = query.data.split(":")[1]
    sel: set = ctx.user_data.get("filter_sel", set())
    sel = sel ^ {key}
    ctx.user_data["filter_sel"] = sel
    await query.edit_message_text(
        t("filter_step", lang),
        reply_markup=_filter_keyboard(
            sel, lang,
            toggle_pfx="filter_toggle",
            confirm_cb="filter_confirm",
            any_cb="filter_any",
            cancel_cb="cancel_watch",
        ),
    )
    return SELECT_FILTER


async def handle_filter_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sel: set = ctx.user_data.get("filter_sel", set())
    if not sel:
        await _safe_answer(query, t("filter_none_selected", lang), show_alert=True)
        return SELECT_FILTER
    return await _finish_watch(query, ctx, lang, ",".join(sorted(sel)))


async def handle_filter_any(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    return await _finish_watch(query, ctx, lang, None)


async def _finish_watch(query, ctx: ContextTypes.DEFAULT_TYPE, lang: str, car_filter: str | None) -> int:
    dep = ctx.user_data["dep"]
    arv = ctx.user_data["arv"]
    date_str = ctx.user_data["pending_date"]
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    sub_id = await db.add_subscription(
        chat_id=query.message.chat_id,
        user_id=query.from_user.id,
        dep_code=dep["code"],
        dep_name=dep["name"],
        arv_code=arv["code"],
        arv_name=arv["name"],
        date=date_str,
        car_filter=car_filter,
    )
    await query.edit_message_text(
        t("watch_added", lang,
          dep=dep["name"], arv=arv["name"],
          date=dt.strftime("%d %B %Y"),
          id=sub_id,
          filter=_filter_display(car_filter, lang))
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

@not_banned
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _lang(ctx, update.effective_user.id)
    subs = await db.get_user_subscriptions(update.effective_user.id)
    if not subs:
        await update.message.reply_text(t("no_watches", lang))
        return

    for sub in subs:
        dt = datetime.strptime(sub["date"], "%Y-%m-%d")
        filter_label = _filter_display(sub.get("car_filter"), lang)
        text = (
            f"<b>{_h(sub['dep_name'])} → {_h(sub['arv_name'])}</b>\n"
            f"Date: {dt.strftime('%d %B %Y')}\n"
            f"Car types: {_h(filter_label)}\n"
            f"Watch ID: <code>{sub['id']}</code>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(t("btn_check_now", lang), callback_data=f"check:{sub['id']}"),
                InlineKeyboardButton(t("btn_edit_filter", lang), callback_data=f"edit_filter:{sub['id']}"),
            ],
            [InlineKeyboardButton(t("btn_remove", lang), callback_data=f"remove:{sub['id']}")],
        ])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_watch_item(query, sub_id: int, lang: str) -> None:
    sub = await db.get_subscription(sub_id)
    if sub is None:
        await query.edit_message_text(t("watch_not_found", lang))
        return
    dt = datetime.strptime(sub["date"], "%Y-%m-%d")
    filter_label = _filter_display(sub.get("car_filter"), lang)
    text = (
        f"<b>{_h(sub['dep_name'])} → {_h(sub['arv_name'])}</b>\n"
        f"Date: {dt.strftime('%d %B %Y')}\n"
        f"Car types: {_h(filter_label)}\n"
        f"Watch ID: <code>{sub_id}</code>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t("btn_check_now", lang), callback_data=f"check:{sub_id}"),
            InlineKeyboardButton(t("btn_edit_filter", lang), callback_data=f"edit_filter:{sub_id}"),
        ],
        [InlineKeyboardButton(t("btn_remove", lang), callback_data=f"remove:{sub_id}")],
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_edit_filter(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sub_id = int(query.data.split(":")[1])
    subs = await db.get_user_subscriptions(update.effective_user.id)
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if sub is None:
        await _safe_answer(query, t("watch_not_found", lang), show_alert=True)
        return
    car_filter = sub.get("car_filter")
    sel = set(car_filter.split(",")) if car_filter else set()
    ctx.user_data[f"efilter_{sub_id}"] = sel
    await query.edit_message_text(
        t("filter_step", lang),
        reply_markup=_filter_keyboard(
            sel, lang,
            toggle_pfx=f"filter_edit_toggle:{sub_id}",
            confirm_cb=f"filter_edit_confirm:{sub_id}",
            any_cb=f"filter_edit_any:{sub_id}",
            cancel_cb=f"filter_edit_back:{sub_id}",
        ),
    )


async def handle_filter_edit_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    _, sub_id_str, key = query.data.split(":", 2)
    sub_id = int(sub_id_str)
    sel: set = ctx.user_data.get(f"efilter_{sub_id}", set())
    sel = sel ^ {key}
    ctx.user_data[f"efilter_{sub_id}"] = sel
    await query.edit_message_text(
        t("filter_step", lang),
        reply_markup=_filter_keyboard(
            sel, lang,
            toggle_pfx=f"filter_edit_toggle:{sub_id}",
            confirm_cb=f"filter_edit_confirm:{sub_id}",
            any_cb=f"filter_edit_any:{sub_id}",
            cancel_cb=f"filter_edit_back:{sub_id}",
        ),
    )


async def handle_filter_edit_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sub_id = int(query.data.split(":")[1])
    sel: set = ctx.user_data.get(f"efilter_{sub_id}", set())
    if not sel:
        await _safe_answer(query, t("filter_none_selected", lang), show_alert=True)
        return
    await db.update_subscription_filter(sub_id, ",".join(sorted(sel)))
    ctx.user_data.pop(f"efilter_{sub_id}", None)
    await _show_watch_item(query, sub_id, lang)


async def handle_filter_edit_any(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sub_id = int(query.data.split(":")[1])
    await db.update_subscription_filter(sub_id, None)
    ctx.user_data.pop(f"efilter_{sub_id}", None)
    await _show_watch_item(query, sub_id, lang)


async def handle_filter_edit_back(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _safe_answer(query)
    lang = await _lang(ctx, update.effective_user.id)
    sub_id = int(query.data.split(":")[1])
    ctx.user_data.pop(f"efilter_{sub_id}", None)
    await _show_watch_item(query, sub_id, lang)


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

@not_banned
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
            SELECT_FILTER: [
                CallbackQueryHandler(handle_filter_toggle, pattern=r"^filter_toggle:"),
                CallbackQueryHandler(handle_filter_confirm, pattern=r"^filter_confirm$"),
                CallbackQueryHandler(handle_filter_any, pattern=r"^filter_any$"),
                CallbackQueryHandler(handle_cancel_callback, pattern=r"^cancel_watch$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )
