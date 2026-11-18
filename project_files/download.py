"""Download raw data for one race session from OpenF1, Jolpica-F1 and Open-Meteo."""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

import config

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"

REQUIRED = ["drivers", "laps", "position", "stints", "race_control"]
OPTIONAL = ["intervals", "pit", "weather", "starting_grid", "session_result",
            "championship_drivers", "championship_teams"]
PER_DRIVER = ["car_data", "location"]


class TooLarge(Exception):
    pass


class Http:
    def __init__(self):
        self.last = 0.0
        self.urls = []
        self.failures = []

    def get(self, url):
        """GET a JSON url with spacing, timeout and retries. Returns parsed JSON.

        A 404 whose body says no results returns an empty list.
        """
        wait = config.RETRY_WAIT_S
        last_error = None
        for attempt in range(config.REQUEST_RETRIES + 1):
            gap = config.REQUEST_SPACING_S - (time.monotonic() - self.last)
            if gap > 0:
                time.sleep(gap)
            self.last = time.monotonic()
            self.urls.append(url)
            try:
                resp = requests.get(url, timeout=config.REQUEST_TIMEOUT_S)
            except requests.RequestException as exc:
                last_error = f"network error: {exc}"
                self.failures.append({"url": url, "attempt": attempt, "error": last_error})
                print(f"  failed ({last_error}), attempt {attempt + 1}", file=sys.stderr)
            else:
                self.last = time.monotonic()
                if resp.status_code == 200:
                    return resp.json()
                body = resp.text[:300]
                if resp.status_code == 404 and "no results" in body.lower():
                    return []
                if "too large" in body.lower():
                    raise TooLarge(body)
                last_error = f"HTTP {resp.status_code}: {body}"
                self.failures.append({"url": url, "attempt": attempt, "error": last_error})
                print(f"  failed ({last_error}), attempt {attempt + 1}", file=sys.stderr)
                if resp.status_code != 429 and resp.status_code < 500:
                    raise RuntimeError(last_error)
            if attempt < config.REQUEST_RETRIES:
                time.sleep(wait)
                wait *= 2
        raise RuntimeError(f"giving up on {url}: {last_error}")


def build_url(base, path, query):
    """Build a URL. A key ending in >= or < is an OpenF1 filter and carries its own operator."""
    parts = []
    for k, v in query:
        if k.endswith(">=") or k.endswith("<"):
            parts.append(f"{k}{quote(str(v), safe=':,')}")
        else:
            parts.append(f"{k}={quote(str(v), safe=':,')}")
    return f"{base}/{path}?" + "&".join(parts) if parts else f"{base}/{path}"


def parse_iso(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def fetch_window(http, endpoint, session_key, driver, start, end, log):
    """Fetch one driver's rows in [start, end). Halve the window on a size error."""
    query = [("session_key", session_key), ("driver_number", driver),
             ("date>=", iso(start)), ("date<", iso(end))]
    try:
        return http.get(build_url(config.OPENF1_BASE, endpoint, query))
    except TooLarge:
        mid = start + (end - start) / 2
        log.append(f"{endpoint} driver {driver}: window {iso(start)} to {iso(end)} too large, halved")
        return (fetch_window(http, endpoint, session_key, driver, start, mid, log)
                + fetch_window(http, endpoint, session_key, driver, mid, end, log))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=config.DEFAULT_YEAR)
    ap.add_argument("--country", default=config.DEFAULT_COUNTRY)
    ap.add_argument("--session-key", type=int, help="skip the year and country lookup and use this OpenF1 session")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    http = Http()
    notes = []
    print("Looking up the session")
    if args.session_key:
        sessions = http.get(build_url(config.OPENF1_BASE, "sessions", [("session_key", args.session_key)]))
    else:
        sessions = http.get(build_url(config.OPENF1_BASE, "sessions", [
            ("year", args.year), ("country_name", args.country), ("session_name", config.SESSION_NAME)]))
    if len(sessions) != 1:
        sys.exit(f"HARD STOP 2: session lookup returned {len(sessions)} sessions, expected exactly 1")
    session = sessions[0]
    key = session["session_key"]
    meeting_key = session["meeting_key"]
    out = RAW_DIR / str(key)
    out.mkdir(parents=True, exist_ok=True)
    save(out / "session.json", session)
    t0 = parse_iso(session["date_start"])
    t1 = parse_iso(session["date_end"])

    counts = {}

    def wanted(name):
        return args.refresh or not (out / f"{name}.json").exists()

    if wanted("meeting"):
        print("meetings")
        save(out / "meeting.json", http.get(build_url(config.OPENF1_BASE, "meetings", [("meeting_key", meeting_key)])))

    for name in REQUIRED + OPTIONAL:
        if wanted(name):
            print(name)
            try:
                rows = http.get(build_url(config.OPENF1_BASE, name, [("session_key", key)]))
            except RuntimeError as exc:
                if name in REQUIRED:
                    sys.exit(f"HARD STOP 3: required endpoint {name} failed: {exc}")
                notes.append(f"optional endpoint {name} failed: {exc}")
                rows = []
            save(out / f"{name}.json", rows)
        else:
            print(f"{name}: already downloaded, skipping")
        counts[name] = len(json.loads((out / f"{name}.json").read_text()))

    drivers = json.loads((out / "drivers.json").read_text())
    numbers = sorted({d["driver_number"] for d in drivers})
    chunk = timedelta(minutes=config.CHUNK_MINUTES)
    for endpoint in PER_DRIVER:
        total = 0
        for n in numbers:
            path = out / f"{endpoint}_{n}.json"
            if path.exists() and not args.refresh:
                total += len(json.loads(path.read_text()))
                continue
            print(f"{endpoint} driver {n}")
            rows, start = [], t0
            while start < t1:
                end = min(start + chunk, t1 + timedelta(seconds=1))
                rows += fetch_window(http, endpoint, key, n, start, end, notes)
                start = end
            save(path, rows)
            total += len(rows)
        counts[endpoint] = total

    for name in REQUIRED:
        if counts[name] == 0:
            sys.exit(f"HARD STOP 3: required endpoint {name} returned no rows")
    empty = [n for n in OPTIONAL + PER_DRIVER if counts.get(n, 0) == 0]

    coords, jolpica_round = None, None
    print("Jolpica race lookup")
    try:
        sched = http.get(f"{config.JOLPICA_BASE}/{session['year']}.json")
        races = sched["MRData"]["RaceTable"]["Races"]
        match = [r for r in races if r["date"] == t0.date().isoformat()]
        if match:
            race = match[0]
            jolpica_round = int(race["round"])
            loc = race["Circuit"]["Location"]
            coords = {"lat": float(loc["lat"]), "lon": float(loc["long"]),
                      "circuit": race["Circuit"]["circuitName"], "round": jolpica_round}
        else:
            notes.append("no Jolpica race matches the session date, coordinates are n/a")
    except Exception as exc:
        notes.append(f"Jolpica schedule lookup failed: {exc}")
    save(out / "circuit.json", coords)

    def has_start_points(name):
        rows = json.loads((out / f"{name}.json").read_text())
        return bool(rows) and any(r.get("points_start") is not None for r in rows)

    need_fallback = not (has_start_points("championship_drivers") and has_start_points("championship_teams"))
    if need_fallback:
        print("Fallback standings")
        fb = {"round": jolpica_round, "drivers": None, "constructors": None}
        if jolpica_round is not None and jolpica_round > 1:
            try:
                prev = jolpica_round - 1
                fb["drivers"] = http.get(f"{config.JOLPICA_BASE}/{session['year']}/{prev}/driverStandings.json")
                fb["constructors"] = http.get(f"{config.JOLPICA_BASE}/{session['year']}/{prev}/constructorStandings.json")
            except Exception as exc:
                notes.append(f"fallback standings failed: {exc}")
        save(out / "fallback_standings.json", fb)
        notes.append("OpenF1 championship start points missing, Jolpica fallback saved")

    forecast = None
    if coords:
        print("Forecast")
        d0 = t0.date()
        d1 = t1.date()
        try:
            url = build_url(config.OPEN_METEO_HIST_FORECAST, "", [
                ("latitude", coords["lat"]), ("longitude", coords["lon"]),
                ("start_date", d0.isoformat()), ("end_date", d1.isoformat()),
                ("hourly", "precipitation_probability,precipitation,temperature_2m,wind_speed_10m"),
                ("timezone", "UTC")]).replace("/?", "?")
            forecast = http.get(url)
        except Exception as exc:
            notes.append(f"forecast request failed: {exc}")
    save(out / "forecast.json", forecast)

    manifest = {
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "year": args.year, "country": args.country,
        "session_key": key, "meeting_key": meeting_key,
        "date_start": session["date_start"], "date_end": session["date_end"],
        "urls": http.urls, "row_counts": counts, "empty_optional": empty,
        "failures": http.failures, "notes": notes,
    }
    save(out / "manifest.json", manifest)
    print(f"Done. session_key={key}. Empty optional endpoints: {empty or 'none'}")


if __name__ == "__main__":
    main()
