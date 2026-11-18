"""Write data/library_index.json: every race from CLASSIC_FIRST_SEASON to this year, and which sources can provide it."""
import json
import sys
from datetime import date
from pathlib import Path

import config
import jolpica
from http_util import Http, read_all_pages

ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "data" / "library_index.json"
OPENF1_FIRST_SEASON = 2023


def race_row(r):
    circuit = r["Circuit"]
    loc = circuit["Location"]
    sprint = r.get("Sprint") or {}
    return {
        "season": int(r["season"]), "round": int(r["round"]), "name": r["raceName"], "circuit": circuit["circuitName"],
        "locality": loc.get("locality"), "country": loc.get("country"), "date": r["date"], "time": r.get("time"),
        "lat": float(loc["lat"]) if loc.get("lat") else None, "lon": float(loc["long"]) if loc.get("long") else None,
        "sprint_date": sprint.get("date"),
    }


def sources_for(row, openf1_by_date):
    src = ["jolpica"]
    if row["season"] >= config.ARCHIVE_FIRST_SEASON:
        src.append("fastf1")
    key = openf1_by_date.get(row["date"])
    if row["season"] >= OPENF1_FIRST_SEASON and key is not None:
        src.append("openf1")
    return src, key


def season_races(season, fetch):
    pages = read_all_pages(fetch, f"{config.JOLPICA_BASE}/{season}.json", lambda b: b["MRData"]["RaceTable"]["Races"])
    races = {}
    for page in pages:
        for r in page:
            races[int(r["round"])] = r
    return [race_row(races[k]) for k in sorted(races)]


def openf1_sessions(season, http, log):
    url = f"{config.OPENF1_BASE}/sessions?year={season}&session_name={config.SESSION_NAME}"
    try:
        rows = http.get(url)
    except RuntimeError as exc:
        log.append(f"OpenF1 sessions for {season}: {exc}")
        rows = []
    return {s["date_start"][:10]: s["session_key"] for s in rows}


def build(fetch=None, http=None, this_year=None, log=None):
    fetch = fetch or jolpica.JOLPICA_HTTP.get
    http = http or jolpica.WEATHER_HTTP
    this_year = this_year or date.today().year
    log = log if log is not None else []
    seasons = {}
    for season in range(config.CLASSIC_FIRST_SEASON, this_year + 1):
        rows = season_races(season, fetch)
        by_date = openf1_sessions(season, http, log) if season >= OPENF1_FIRST_SEASON else {}
        for row in rows:
            row["sources"], row["openf1_session_key"] = sources_for(row, by_date)
            if season >= OPENF1_FIRST_SEASON and row["openf1_session_key"] is None and row["date"] <= date.today().isoformat():
                log.append(f"no OpenF1 race session for {season} round {row['round']} ({row['date']})")
        seasons[str(season)] = rows
        print(f"{season}: {len(rows)} races", file=sys.stderr)
    return {"generated": date.today().isoformat(), "seasons": seasons}


def main():
    log = []
    index = build(log=log)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=0))
    for line in log:
        print(line)


if __name__ == "__main__":
    main()
