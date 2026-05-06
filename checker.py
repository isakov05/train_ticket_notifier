import logging
from urllib.parse import unquote

import httpx

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
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._xsrf: str | None = None

    async def _session(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=_BASE_HEADERS,
                follow_redirects=True,
                timeout=30,
            )
        if self._xsrf is None:
            await self._refresh_xsrf()
        return self._client

    async def _refresh_xsrf(self) -> None:
        assert self._client is not None
        try:
            await self._client.get(CSRF_ENDPOINT)
            raw = self._client.cookies.get("XSRF-TOKEN")
            if raw:
                self._xsrf = unquote(raw)
                logger.debug("XSRF token refreshed: %s", self._xsrf)
            else:
                logger.warning("XSRF-TOKEN cookie not found after csrf-token request")
        except Exception as exc:
            logger.error("Failed to refresh XSRF token: %s", exc)

    def _post_headers(self) -> dict:
        return {"Content-Type": "application/json", "X-XSRF-TOKEN": self._xsrf or ""}

    async def _post(self, url: str, payload: dict) -> httpx.Response:
        client = await self._session()
        resp = await client.post(url, json=payload, headers=self._post_headers())
        if resp.status_code in (403, 419):
            self._xsrf = None
            await self._refresh_xsrf()
            resp = await client.post(url, json=payload, headers=self._post_headers())
        return resp

    async def get_trains(self, dep_code: str, arv_code: str, date: str) -> list[dict] | None:
        """Fetch trains for a route on a given date (date: YYYY-MM-DD).

        Returns list of train dicts, or None on request/API failure.
        """
        payload = {
            "directions": {
                "forward": {
                    "date": date,
                    "depStationCode": dep_code,
                    "arvStationCode": arv_code,
                }
            }
        }
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
            logger.error("HTTP %s fetching trains %s→%s on %s", exc.response.status_code, dep_code, arv_code, date)
            return None
        except Exception as exc:
            logger.error("Error fetching trains: %s", exc)
            return None

    async def search_stations(self, query: str) -> list[dict] | None:
        """Search stations by name. Returns list of {code, name} dicts, or None on failure."""
        try:
            resp = await self._post(STATIONS_ENDPOINT, {"name": query})
            resp.raise_for_status()
            stations = resp.json().get("data", {}).get("stations", [])
            if isinstance(stations, list):
                return stations
            logger.error("Unexpected stations response shape for query %r", query)
            return None
        except Exception as exc:
            logger.error("Error searching stations: %s", exc)
            return None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            self._xsrf = None


def build_snapshot(trains: list[dict]) -> dict[str, dict[str, int]]:
    """Build {train_number: {car_type: free_seats}} snapshot from API trains list."""
    snapshot: dict[str, dict[str, int]] = {}
    for train in trains:
        number = str(train.get("number", "")).strip()
        if not number:
            continue
        cars: dict[str, int] = {}
        for car in train.get("cars", []):
            car_type = str(car.get("type", "unknown")).strip()
            free = int(car.get("freeSeats", 0) or 0)
            if free > 0:
                cars[car_type] = free
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
