import asyncio
import logging
import time
from urllib.parse import unquote

import httpx

_RETRY_DELAYS = (2, 5)  # seconds between attempts: try, wait 2s, retry, wait 5s, retry
_XSRF_TTL = 25 * 60  # refresh token proactively after 25 minutes
_TRAINS_CACHE_TTL = 15  # seconds to cache train results per route+date

logger = logging.getLogger(__name__)

BASE_URL = "https://eticket.railway.uz"
CSRF_ENDPOINT = f"{BASE_URL}/api/v1/csrf-token"
TRAINS_ENDPOINT = f"{BASE_URL}/api/v3/handbook/trains/list"
STATIONS_ENDPOINT = f"{BASE_URL}/api/v1/handbook/stations/list"

_BASE_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "uz",
    "X-Custom-Language": "uz",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/uz/home",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


class RailwayClient:
    def __init__(self, max_concurrent: int = 8) -> None:
        self._client: httpx.AsyncClient | None = None
        self._xsrf: str | None = None
        self._xsrf_fetched_at: float = 0.0
        self._refresh_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self.bg_semaphore = asyncio.Semaphore(3)
        # (dep_code, arv_code, date) -> (fetched_at, trains)
        self._trains_cache: dict[tuple, tuple[float, list]] = {}
        self._cache_locks: dict[tuple, asyncio.Lock] = {}

    async def _session(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=_BASE_HEADERS,
                follow_redirects=True,
                timeout=httpx.Timeout(connect=15, read=20, write=10, pool=5),
            )
        token_age = time.monotonic() - self._xsrf_fetched_at
        if self._xsrf is None or token_age > _XSRF_TTL:
            await self._refresh_xsrf()
            if self._xsrf is None:
                raise httpx.ConnectError("Railway API unreachable (XSRF refresh failed)")
        return self._client

    async def _refresh_xsrf(self) -> None:
        async with self._refresh_lock:
            # Re-check inside lock — another coroutine may have refreshed already
            token_age = time.monotonic() - self._xsrf_fetched_at
            if self._xsrf is not None and token_age <= _XSRF_TTL:
                return
            assert self._client is not None
            try:
                await self._client.get(CSRF_ENDPOINT)
                raw = self._client.cookies.get("XSRF-TOKEN")
                if raw:
                    self._xsrf = unquote(raw)
                    self._xsrf_fetched_at = time.monotonic()
                    logger.debug("XSRF token refreshed")
                else:
                    logger.warning("XSRF-TOKEN cookie not found after csrf-token request")
            except Exception as exc:
                logger.error("Failed to refresh XSRF token: %s: %s", type(exc).__name__, exc)

    def _post_headers(self) -> dict:
        return {"Content-Type": "application/json", "X-XSRF-TOKEN": self._xsrf or ""}

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        async with self._semaphore:
            client = await self._session()
            resp = await client.post(url, json=payload, headers=self._post_headers())
            if resp.status_code in (403, 419):
                self._xsrf = None
                await self._refresh_xsrf()
                resp = await client.post(url, json=payload, headers=self._post_headers())
            return resp

    def purge_cache(self, active_dates: set[str] | None = None) -> None:
        """Remove stale and past-date entries from the trains cache."""
        now = time.monotonic()
        dead_keys = [
            key for key, (fetched_at, _) in self._trains_cache.items()
            if (now - fetched_at > _TRAINS_CACHE_TTL)
            or (active_dates is not None and key[2] not in active_dates)
        ]
        for key in dead_keys:
            self._trains_cache.pop(key, None)
            self._cache_locks.pop(key, None)
        if dead_keys:
            logger.debug("Cache purged %d stale entries", len(dead_keys))

    async def get_trains(self, dep_code: str, arv_code: str, date: str) -> list[dict] | str | None:
        """Fetch trains for a route on a given date (date: YYYY-MM-DD).

        Returns cached result if fresh, otherwise fetches from API.
        Retries up to 2 times with backoff on transient errors.
        """
        cache_key = (dep_code, arv_code, date)

        # Return cached result if still fresh
        cached = self._trains_cache.get(cache_key)
        if cached is not None:
            fetched_at, trains = cached
            if time.monotonic() - fetched_at < _TRAINS_CACHE_TTL:
                logger.debug("Cache hit for %s→%s on %s", dep_code, arv_code, date)
                return trains

        # One lock per route+date so concurrent requests for the same route
        # wait for the first fetch instead of all firing at once
        if cache_key not in self._cache_locks:
            self._cache_locks[cache_key] = asyncio.Lock()
        async with self._cache_locks[cache_key]:
            # Re-check cache inside lock — another coroutine may have just fetched
            cached = self._trains_cache.get(cache_key)
            if cached is not None:
                fetched_at, trains = cached
                if time.monotonic() - fetched_at < _TRAINS_CACHE_TTL:
                    return trains

            result = await self._fetch_trains(dep_code, arv_code, date)
            if result is not None:
                self._trains_cache[cache_key] = (time.monotonic(), result)
            return result

    async def _fetch_trains(self, dep_code: str, arv_code: str, date: str) -> list[dict] | None:
        payload = {
            "directions": {
                "forward": {
                    "date": date,
                    "depStationCode": dep_code,
                    "arvStationCode": arv_code,
                }
            }
        }
        delays = iter(_RETRY_DELAYS)
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._post(TRAINS_ENDPOINT, payload)
                resp.raise_for_status()
                trains = (
                    resp.json()
                    .get("data", {})
                    .get("directions", {})
                    .get("forward", {})
                    .get("trains", [])
                )
                if isinstance(trains, list):
                    return trains
                logger.error("Unexpected trains response shape for %s→%s on %s", dep_code, arv_code, date)
                return None
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:
                    logger.warning("HTTP 400 for %s→%s on %s — invalid route, deactivating", dep_code, arv_code, date)
                    return "invalid_route"
                logger.error("HTTP %s fetching trains %s→%s on %s", exc.response.status_code, dep_code, arv_code, date)
                return None
            except httpx.RemoteProtocolError:
                # Server closed the keep-alive connection — reset client and retry silently
                await self._reset_client()
                delay = next(delays, None)
                if delay is None:
                    return None
                await asyncio.sleep(delay)
            except Exception as exc:
                delay = next(delays, None)
                if delay is None:
                    logger.error("Error fetching trains (attempt %d, giving up): %s: %s", attempt, type(exc).__name__, exc)
                    return None
                logger.warning("Error fetching trains (attempt %d, retrying in %ds): %s", attempt, delay, type(exc).__name__)
                await asyncio.sleep(delay)

    async def search_stations(self, query: str) -> list[dict] | None:
        """Search stations by name. Returns list of {code, name} dicts, or None on failure."""
        delays = iter(_RETRY_DELAYS)
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._post(STATIONS_ENDPOINT, {"name": query})
                resp.raise_for_status()
                stations = resp.json().get("data", {}).get("stations", [])
                if isinstance(stations, list):
                    return stations
                logger.error("Unexpected stations response shape for query %r", query)
                return None
            except Exception as exc:
                delay = next(delays, None)
                if delay is None:
                    logger.error("Error searching stations (attempt %d, giving up): %s: %s", attempt, type(exc).__name__, exc)
                    return None
                logger.warning("Error searching stations (attempt %d, retrying in %ds): %s", attempt, delay, type(exc).__name__)
                await asyncio.sleep(delay)

    async def _reset_client(self) -> None:
        if self._client:
            await self._client.aclose()
        self._client = None
        self._xsrf = None
        self._xsrf_fetched_at = 0.0

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            self._xsrf = None


# Maps all known Cyrillic/Latin/Uzbek variants to a stable canonical name
_CAR_TYPE_ALIASES: dict[str, str] = {
    # Platzkart
    "плацкартный": "platzkart",
    "плацкарт": "platzkart",
    "plaskartli": "platzkart",
    "plaskart": "platzkart",
    # Coupe
    "купе": "coupe",
    "kupe": "coupe",
    # Lux / SV
    "люкс": "lux",
    "lyuks": "lux",
    "св": "lux",
    "sv": "lux",
    # General / Sitting
    "общий": "general",
    "umumiy": "general",
    "сиденье": "seat",
    "o'rindiq": "seat",
    "o'rindiqli": "seat",
    "orindiq": "seat",
    "orindiqli": "seat",
}


def _normalize_car_type(raw: str) -> str:
    return _CAR_TYPE_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def build_snapshot(trains: list[dict]) -> dict[str, dict[str, int]]:
    """Build {train_number: {car_type: free_seats}} snapshot from API trains list."""
    snapshot: dict[str, dict[str, int]] = {}
    for train in trains:
        number = str(train.get("number", "")).strip()
        if not number:
            continue
        cars: dict[str, int] = {}
        for car in train.get("cars", []):
            car_type = _normalize_car_type(str(car.get("type", "unknown")))
            free = int(car.get("freeSeats", 0) or 0)
            if free > 0:
                cars[car_type] = max(cars.get(car_type, 0), free)
        if cars:
            snapshot[number] = cars
    return snapshot


def diff_snapshots(
    old: dict[str, dict[str, int]],
    new: dict[str, dict[str, int]],
    trains: list[dict],
) -> list[str]:
    """Return notification lines for newly available tickets.

    Triggers on: new train appeared, or a car type went from 0 to available.
    """
    train_meta = {str(t.get("number", "")).strip(): t for t in trains}
    messages: list[str] = []

    for train_num, new_cars in new.items():
        if not new_cars:
            continue

        old_cars = old.get(train_num, {})
        is_new_train = train_num not in old

        newly_available = {
            ct: seats
            for ct, seats in new_cars.items()
            if seats > 0 and (is_new_train or old_cars.get(ct, 0) == 0)
        }

        if not newly_available:
            continue

        meta = train_meta.get(train_num, {})
        dep_time = meta.get("departureDate", "")
        arv_time = meta.get("arrivalDate", "")
        duration = meta.get("timeOnWay", "")

        time_str = ""
        if dep_time and arv_time:
            time_str = f"{dep_time} → {arv_time}"
            if duration:
                time_str += f"  ({duration} travel time)"

        header = f"Train {train_num}" + (f"\n  {time_str}" if time_str else "")
        seat_lines = [f"  • {ct}: {seats} seats" for ct, seats in newly_available.items()]
        messages.append(header + "\n" + "\n".join(seat_lines))

    return messages
