STATIONS: dict[str, str] = {
    "Toshkent": "2900000",
    "Samarqand": "2900680",
    "Buxoro": "2900100",
    "Namangan": "2900540",
    "Andijon": "2900020",
    "Farg'ona": "2900240",
    "Nukus": "2900580",
    "Termiz": "2900760",
    "Urganch": "2900800",
    "Qarshi": "2900440",
    "Navoiy": "2900560",
    "Jizzax": "2900320",
    "Guliston": "2900280",
    "Denov": "2900160",
    "Qo'qon": "2900460",
    "Margilon": "2900500",
    "Xiva": "2900840",
    "G'uzor": "2900260",
    "Muborak": "2900520",
    "Beyneu": "2900080",
}

# Aliases for alternate spellings
ALIASES: dict[str, str] = {
    "tashkent": "Toshkent",
    "toshkent": "Toshkent",
    "samarkand": "Samarqand",
    "samarqand": "Samarqand",
    "bukhara": "Buxoro",
    "buxoro": "Buxoro",
    "bukhoro": "Buxoro",
    "andijan": "Andijon",
    "andijon": "Andijon",
    "fergana": "Farg'ona",
    "namangan": "Namangan",
    "nukus": "Nukus",
    "termez": "Termiz",
    "termiz": "Termiz",
    "urgench": "Urganch",
    "urganch": "Urganch",
    "karshi": "Qarshi",
    "qarshi": "Qarshi",
    "navoi": "Navoiy",
    "navoiy": "Navoiy",
    "jizzakh": "Jizzax",
    "jizzax": "Jizzax",
    "kokand": "Qo'qon",
    "margilan": "Margilon",
    "khiva": "Xiva",
    "xiva": "Xiva",
}


def search_stations(query: str) -> list[tuple[str, str]]:
    """Return list of (name, code) matching the query string."""
    q = query.lower().strip()
    if not q:
        return list(STATIONS.items())[:10]

    results: dict[str, str] = {}

    # Direct substring match on station names
    for name, code in STATIONS.items():
        if q in name.lower():
            results[name] = code

    # Alias match
    for alias, canonical in ALIASES.items():
        if q in alias and canonical not in results:
            results[canonical] = STATIONS[canonical]

    return list(results.items())[:10]


def get_code(name: str) -> str | None:
    return STATIONS.get(name)
