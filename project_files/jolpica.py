"""Jolpica-F1 and Open-Meteo requests through one spaced HTTP helper, plus the start points rule."""
import json
from datetime import timedelta
from pathlib import Path

import config
from http_util import Http, read_all_pages

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "data" / "library_index.json"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"

POSITION_UNKNOWN = 99
JOLPICA_HTTP = Http(config.JOLPICA_SPACING_S)
WEATHER_HTTP = Http(config.REQUEST_SPACING_S)


def jget(path):
    return JOLPICA_HTTP.get(f"{config.JOLPICA_BASE}/{path}")


def jpaged(path, extract):
    return read_all_pages(JOLPICA_HTTP.get, f"{config.JOLPICA_BASE}/{path}", extract)


def library_entry(year, rnd):
    try:
        index = json.loads(INDEX_PATH.read_text())
    except (OSError, ValueError):
        return None
    for r in index.get("seasons", {}).get(str(year), []):
        if int(r["round"]) == int(rnd):
            return r
    return None


def forecast_for(lat, lon, start_utc, end_utc):
    """Open-Meteo Historical Forecast response for the session dates, or None."""
    if lat is None or lon is None:
        return None
    url = (f"{config.OPEN_METEO_HIST_FORECAST}?latitude={lat}&longitude={lon}&start_date={start_utc.date().isoformat()}"
           f"&end_date={end_utc.date().isoformat()}&hourly=precipitation_probability,precipitation,temperature_2m,"
           f"wind_speed_10m&timezone=UTC")
    try:
        return WEATHER_HTTP.get(url)
    except RuntimeError:
        return None


def archive_weather_for(lat, lon, date_iso):
    url = (f"{OPEN_METEO_ARCHIVE}?latitude={lat}&longitude={lon}&start_date={date_iso}&end_date={date_iso}"
           f"&hourly=temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m&timezone=UTC")
    try:
        return WEATHER_HTTP.get(url)
    except RuntimeError:
        return None


def driver_key(driver):
    """Match key for a Jolpica driver: the three-letter code, else the driver id."""
    return driver.get("code") or driver["driverId"]


def _standings_list(body, key):
    lists = body["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0][key] if lists else []


def fetch_start_points(year, rnd, with_sprint, results_body=None, fetch=None):
    """Jolpica standings after the previous round, plus sprint points for the Race of a sprint event.

    Returns {"drivers": {code: (points, position)}, "constructors": {constructorId: (points, position)},
    "driver_constructor": {code: constructorId}, "source": text}."""
    if fetch is None:
        fetch = jget
    else:
        raw_fetch = fetch
        fetch = lambda path: raw_fetch(path if path.startswith("http") else f"{config.JOLPICA_BASE}/{path}")
    res = results_body if results_body is not None else fetch(f"{year}/{rnd}/results.json?limit={config.JOLPICA_PAGE}")
    races = res["MRData"]["RaceTable"]["Races"]
    d_con = {}
    if races:
        for r in races[0]["Results"]:
            d_con[driver_key(r["Driver"])] = r["Constructor"]["constructorId"]
    drivers, cons = {}, {}
    if rnd > 1:
        ds = _standings_list(fetch(f"{year}/{rnd - 1}/driverStandings.json?limit={config.JOLPICA_PAGE}"), "DriverStandings")
        cs = _standings_list(fetch(f"{year}/{rnd - 1}/constructorStandings.json?limit={config.JOLPICA_PAGE}"), "ConstructorStandings")
        for x in ds:
            drivers[driver_key(x["Driver"])] = [float(x["points"]), int(x.get("position") or POSITION_UNKNOWN)]
        for x in cs:
            cons[x["Constructor"]["constructorId"]] = [float(x["points"]), int(x.get("position") or POSITION_UNKNOWN)]
        source = f"Jolpica-F1 standings after round {rnd - 1}"
    else:
        source = "Jolpica-F1: round 1, all start points are 0"
    if with_sprint:
        sp = fetch(f"{year}/{rnd}/sprint.json?limit={config.JOLPICA_PAGE}")
        sraces = sp["MRData"]["RaceTable"]["Races"]
        if sraces:
            for r in sraces[0].get("SprintResults", []):
                code, pts = driver_key(r["Driver"]), float(r["points"])
                drivers.setdefault(code, [0.0, 99])[0] += pts
                cid = r["Constructor"]["constructorId"]
                cons.setdefault(cid, [0.0, 99])[0] += pts
                d_con.setdefault(code, cid)
            source += " plus this event's sprint points"
    for table in (drivers, cons):
        order = sorted(table, key=lambda k: (-table[k][0], table[k][1]))
        for i, k in enumerate(order):
            table[k][1] = i + 1
    return {"drivers": drivers, "constructors": cons, "driver_constructor": d_con, "source": source}


def start_points_for_bundle(raw, drivers, log):
    """Write the start_points shape of the existing bundles. drivers: list of dicts with driver_number, acronym, team_name."""
    out = {"source": raw["source"], "drivers": {}, "teams": {}}
    unmatched = []
    for i, d in enumerate(drivers):
        key = d.get("match_key", d["acronym"])
        v = raw["drivers"].get(key)
        if v is None:
            unmatched.append(d["acronym"])
            v = [0.0, 100 + i]
        out["drivers"][str(d["driver_number"])] = {"points": v[0], "position": v[1]}
    team_cid = {}
    for d in drivers:
        cid = raw["driver_constructor"].get(d.get("match_key", d["acronym"]))
        if cid and d["team_name"] not in team_cid:
            team_cid[d["team_name"]] = cid
    for j, tn in enumerate(sorted({d["team_name"] for d in drivers})):
        v = raw["constructors"].get(team_cid.get(tn))
        if v is None:
            if raw["constructors"]:
                log.append(f"no constructor match for team {tn}: 0 points")
            v = [0.0, 100 + j]
        out["teams"][tn] = {"points": v[0], "position": v[1]}
    if unmatched and raw["drivers"]:
        log.append(f"no Jolpica match for drivers {unmatched}: 0 points")
    return out
