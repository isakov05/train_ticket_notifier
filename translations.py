STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # /start
        "start": (
            "Hi {name}! I monitor train tickets on uzrailways.\n\n"
            "Your chat ID: <code>{chat_id}</code>\n\n"
            "Commands:\n"
            "/watch — add a new ticket watch\n"
            "/list  — your active watches\n"
            "/help  — show this message"
        ),
        # /help
        "help": (
            "Commands:\n"
            "/watch    — add a new ticket watch\n"
            "/list     — your active watches\n"
            "/language — change language\n"
            "/help     — this message\n\n"
            "I check for new tickets every {interval} and notify you instantly."
        ),
        # /language
        "language_choose": "Choose your language:",
        "language_set": "Language set to English.",
        # /watch steps
        "watch_step1": "Step 1/4 — Departure\n\nType the departure city or station name:",
        "watch_step2": "Step 2/4 — Arrival\n\nType the arrival city or station name:",
        "watch_step2_with_dep": "Step 2/4 — Arrival\n\nDeparture: {dep_name}\n\nType the arrival city or station name:",
        "watch_step3": "Step 3/4 — Date\n\nRoute: {dep} → {arv}\n\nEnter the travel date in DD.MM.YYYY format\n(e.g. {example}):",
        "dep_selected": "Departure: {name}",
        "arv_selected": "Arrival: {name}",
        "no_stations": "No stations found. Try a different spelling (e.g. Toshkent, Samarqand):",
        "no_stations_arv": "No stations found. Try a different spelling:",
        "select_dep": "Select departure station:",
        "select_arv": "Select arrival station:",
        "invalid_date": "Invalid format. Please use DD.MM.YYYY (e.g. {example}):",
        "past_date": "That date is in the past. Please enter a future date:",
        "watch_limit": "You have reached the limit of 3 active watches.\nUse /list to remove one before adding a new watch.",
        "duplicate_watch": "You already have a watch for {dep} → {arv} on {date}.\nUse /list to see your active watches.",
        "watch_added": (
            "Watch added!\n\n"
            "{dep} → {arv}\n"
            "Date: {date}\n"
            "Car types: {filter}\n\n"
            "You will be notified when tickets become available. (ID: {id})\n"
            "Use /list to see all your watches."
        ),
        "watch_cancelled": "Watch cancelled.",
        "cancelled": "Cancelled.",
        # /list
        "no_watches": "You have no active watches. Use /watch to add one.",
        "watch_removed": "Watch removed.",
        "watch_not_found": "Watch not found.",
        # check now
        "checking": "Checking...",
        "railway_unreachable": "Could not reach railway.uz. Try again later.",
        "no_trains": "No trains found for this route and date.",
        "no_seats": "No seats available",
        "book_tickets": "Book tickets",
        # seat labels
        "seat_one": "seat",
        "seat_many": "seats",
        # berth position labels
        "berth_lower": "lower",
        "berth_upper": "upper",
        "berth_bokovoy_lower": "bokovoy lower",
        "berth_bokovoy_upper": "bokovoy upper",
        # car type filter
        "filter_step": "Step 4/4 — Car types\n\nSelect the car types to watch:\n(tap to toggle on/off)",
        "filter_none_selected": "Select at least one car type.",
        "filter_any_label": "Any type",
        "btn_filter_confirm": "✓ Confirm",
        "btn_filter_any": "Any (all types)",
        "btn_edit_filter": "Edit filter",
        # buttons
        "btn_cancel": "Cancel",
        "btn_back": "Back",
        "btn_check_now": "Check now",
        "btn_remove": "Remove",
        "btn_english": "English",
        "btn_karakalpak": "Qaraqalpaqsha",
        # notifications
        "tickets_available": "Tickets available!",
        "watch_expired": "Watch expired: {dep} → {arv} on {date}.",
    },

    "kaa": {
        # /start
        "start": (
            "Salam {name}! Men uzrailways'ta poyezd biletlerin baqlayman.\n\n"
            "Siziń chat ID: <code>{chat_id}</code>\n\n"
            "Buyırıqlar:\n"
            "/watch — jańa baqlaw qosıw\n"
            "/list  — aktiv baqławlarıńız\n"
            "/help  — bul xabar"
        ),
        # /help
        "help": (
            "Buyırıqlar:\n"
            "/watch    — jańa baqlaw qosıw\n"
            "/list     — aktiv baqławlarıńız\n"
            "/language — tildi ózgertiriw\n"
            "/help     — bul xabar\n\n"
            "Jańa biletlerdi {interval} sayın tekserip, dereq beremin."
        ),
        # /language
        "language_choose": "Tilińizdi tańlań:",
        "language_set": "Til Qaraqalpaq tiline ózgertirildi.",
        # /watch steps
        "watch_step1": "1/4-qadem — Ketiw stanciyası\n\nKetiw qalası yaki stanciya atın jazıń:",
        "watch_step2": "2/4-qadem — Keliw stanciyası\n\nKeliw qalası yaki stanciya atın jazıń:",
        "watch_step2_with_dep": "2/4-qadem — Keliw stanciyası\n\nKetiw: {dep_name}\n\nKeliw qalası yaki stanciya atın jazıń:",
        "watch_step3": "3/4-qadem — Sáne\n\nJónelis: {dep} → {arv}\n\nKetiw sánesin DD.MM.YYYY formatında jazıń\n(mısalı: {example}):",
        "dep_selected": "Ketiw: {name}",
        "arv_selected": "Keliw: {name}",
        "no_stations": "Stanciyalar tabılmadı. Basqa jazılıwın sınap kóriń (mısalı: Toshkent, Nukus):",
        "no_stations_arv": "Stanciyalar tabılmadı. Basqa jazılıwın sınap kóriń:",
        "select_dep": "Ketiw stanciyasın tańlań:",
        "select_arv": "Keliw stanciyasın tańlań:",
        "invalid_date": "Nadurıs format. DD.MM.YYYY formatın isletiń (mısalı: {example}):",
        "past_date": "Bul sáne otip ketken. Bugin yaki endi keletin sáneni jazıń:",
        "watch_limit": "Siz 3 aktiv baqlaw limitine jettińiz.\nJańa qosıwdan aldın /list arqalı birewin alıp taslań.",
        "duplicate_watch": "Sizde {date} kúni {dep} → {arv} ushın baqlaw bar.\nAktiv baqławlarıńızdı kóriw ushın /list isletiń.",
        "watch_added": (
            "Baqlaw qosıldı!\n\n"
            "{dep} → {arv}\n"
            "Sáne: {date}\n"
            "Vagon túrleri: {filter}\n\n"
            "Jańf biletler payda bolğanda xabar beremen. (ID: {id})\n"
            "Barlıq baqławlarıńızdı kóriw ushın /list isletiń."
        ),
        "watch_cancelled": "Baqlaw biykar etildi.",
        "cancelled": "Biykar etildi.",
        # /list
        "no_watches": "Sizde aktiv baqławlar joq. Qosıw ushın /watch isletiń.",
        "watch_removed": "Baqlaw alıp taslandı.",
        "watch_not_found": "Baqlaw tabılmadı.",
        # check now
        "checking": "Tekserilmekte...",
        "railway_unreachable": "railway.uz'ga soraw jiberiw múmkin emes. Keyinrek urınıp kóriń.",
        "no_trains": "Bul jónelis hám sáne ushın poyezdler tabılmadı.",
        "no_seats": "Orınlar joq",
        "book_tickets": "Bilet satıp alıw",
        # seat labels
        "seat_one": "orın",
        "seat_many": "orın",
        # berth position labels
        "berth_lower": "Tómengi",
        "berth_upper": "Joqarı",
        "berth_bokovoy_lower": "Bokovoy tómengi",
        "berth_bokovoy_upper": "Bokovoy joqarı",
        # car type filter
        "filter_step": "4/4-qadem — Vagon túrleri\n\nQaysi vagon túrlerini baqlaw kerek?\n(basıp belgileń yamasa alıń)",
        "filter_none_selected": "Keminde bir vagon túrin tańlań.",
        "filter_any_label": "Hámme túrler",
        "btn_filter_confirm": "✓ Tastıyıqlaw",
        "btn_filter_any": "Hámme túrler (barlıǵı)",
        "btn_edit_filter": "Filterdi ózgertiriw",
        # buttons
        "btn_cancel": "Biykar etiw",
        "btn_back": "Artqa",
        "btn_check_now": "Házir tekseriw",
        "btn_remove": "Alıp taslaw",
        "btn_english": "English",
        "btn_karakalpak": "Qaraqalpaqsha",
        # notifications
        "tickets_available": "Jańa biletler bar!",
        "watch_expired": "Baqlaw waqtı ótken: {dep} → {arv}, {date}.",
    },
}


def t(key: str, lang: str, **kwargs) -> str:
    template = STRINGS.get(lang, STRINGS["en"]).get(key) or STRINGS["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template
