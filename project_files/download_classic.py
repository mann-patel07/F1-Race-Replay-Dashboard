"""Download lap-only data for one race from Jolpica-F1 (and one Open-Meteo archive request).

python download_classic.py --season Y --round R
Saves raw JSON to data/raw_classic/<Y>_<RR>/.
"""
import argparse
import json
import shutil
from pathlib import Path

import config
import jolpica
from http_util import read_all_pages

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw_classic"


def raw_dir(season, rnd):
    return RAW / f"{int(season)}_{int(rnd):02d}"


def merge_laps(pages):
    """Pages of Races lists to one list of {number, Timings}, merging a lap that is split across pages."""
    by = {}
    for races in pages:
        if not races:
            continue
        for lap in races[0].get("Laps", []):
            by.setdefault(int(lap["number"]), []).extend(lap["Timings"])
    return [{"number": k, "Timings": v} for k, v in sorted(by.items())]


def merge_list(pages, key):
    out = []
    for races in pages:
        if races:
            out.extend(races[0].get(key, []))
    return out


def download(season, rnd, entry=None, fetch=None, weather_fn=None):
    """Download everything for one race into a temp folder, then rename. Returns the folder."""
    fetch = fetch or jolpica.JOLPICA_HTTP.get
    entry = entry or jolpica.library_entry(season, rnd) or {}
    final = raw_dir(season, rnd)
    tmp = final.with_name(final.name + "_tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    base = f"{config.JOLPICA_BASE}/{season}/{rnd}"

    def paged(name, extract):
        return read_all_pages(fetch, f"{base}/{name}.json", extract)

    races = lambda b: b["MRData"]["RaceTable"]["Races"]
    results = fetch(f"{base}/results.json?limit={config.JOLPICA_PAGE}")
    (tmp / "results.json").write_text(json.dumps(results))
    laps = merge_laps(paged("laps", races))
    (tmp / "laps.json").write_text(json.dumps(laps))
    try:
        pits = merge_list(paged("pitstops", races), "PitStops")
    except RuntimeError:
        pits = []
    (tmp / "pitstops.json").write_text(json.dumps(pits))
    (tmp / "qualifying.json").write_text(json.dumps(fetch(f"{base}/qualifying.json?limit={config.JOLPICA_PAGE}")))
    has_sprint = bool(entry.get("sprint_date"))
    if has_sprint:
        (tmp / "sprint.json").write_text(json.dumps(fetch(f"{base}/sprint.json?limit={config.JOLPICA_PAGE}")))
    start_points = jolpica.fetch_start_points(season, int(rnd), with_sprint=has_sprint, results_body=results, fetch=fetch)
    (tmp / "start_points.json").write_text(json.dumps(start_points))
    weather = None
    if entry.get("time") and entry.get("lat") is not None:
        get_weather = weather_fn or jolpica.archive_weather_for
        weather = get_weather(entry["lat"], entry["lon"], entry["date"])
    (tmp / "weather.json").write_text(json.dumps(weather))
    (tmp / "entry.json").write_text(json.dumps(entry))
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    a = ap.parse_args()
    print(download(a.season, a.round))


if __name__ == "__main__":
    main()
