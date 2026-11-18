"""Build a lap-only bundle data/bundle/jl_<Y>_<RR>/ from data/raw_classic/<Y>_<RR>/.

python build_classic.py --season Y --round R
"""
import argparse
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import bundle_io
import config
import download_classic
import jolpica
import points
from bundle_common import compute_lap_table, generated_colour, race_start_from_laps, seek_index

ROOT = Path(__file__).resolve().parent

MISSING_REASON = "not in the public lap data for this season"
MISSING = [f"{x} ({MISSING_REASON})" for x in (
    "tyres", "sectors", "mini-sectors", "DRS", "penalties", "track status", "race control feed", "track map",
    "telemetry", "forecast", "tyre sets")] + [
    "team colours are generated, not official",
    "gaps are computed from lap times",
    "pit status is approximate (shown for the recorded stop duration after the in-lap ends)",
    "the pit loss estimate cannot leave out safety car periods, because track status is not public for this season",
    "chequered flag is derived from lap times"]


def parse_time(text):
    """Seconds from 'SS.sss' or 'M:SS.sss' (or H:MM:SS.sss). None when it does not parse."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)", text.strip())
    if not m:
        return None
    a, b, c = m.groups()
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + float(c)
    if a is not None:
        return int(a) * 60 + float(c)
    return float(c)


def parse_pit_duration(text):
    v = parse_time(text)
    if v is None or v > config.PIT_DURATION_MAX_S:
        return None
    return v


def driver_numbers(results):
    nums = [r.get("number") for r in results]
    if all(n is not None and str(n).isdigit() for n in nums) and len(set(nums)) == len(nums):
        return [int(n) for n in nums]
    return list(range(1, len(results) + 1))


def acronym_of(driver):
    return driver.get("code") or driver["familyName"][:3].upper()


def build(season, rnd, raw_base=None, bundle_dir=None, write_log=False):
    raw = Path(raw_base) / f"{int(season)}_{int(rnd):02d}" if raw_base else download_classic.raw_dir(season, rnd)
    bundle_dir = Path(bundle_dir or bundle_io.BUNDLE_DIR)
    race_id = bundle_io.make_race_id("jolpica", season, rnd)
    results_body = json.loads((raw / "results.json").read_text())
    race = results_body["MRData"]["RaceTable"]["Races"][0]
    results = race["Results"]
    lap_list = json.loads((raw / "laps.json").read_text())
    pits_raw = json.loads((raw / "pitstops.json").read_text())
    entry = json.loads((raw / "entry.json").read_text()) if (raw / "entry.json").exists() else {}
    start_raw = json.loads((raw / "start_points.json").read_text())
    weather_raw = json.loads((raw / "weather.json").read_text()) if (raw / "weather.json").exists() else None
    log = []

    numbers = driver_numbers(results)
    num_of = {r["Driver"]["driverId"]: n for r, n in zip(results, numbers)}
    drivers = []
    for r, n in zip(results, numbers):
        d = r["Driver"]
        grid = int(r["grid"]) if str(r.get("grid", "0")).isdigit() and int(r["grid"]) > 0 else None
        drivers.append({"driver_number": n, "acronym": acronym_of(d), "full_name": f"{d['givenName']} {d['familyName']}",
                        "team_name": r["Constructor"]["name"], "team_colour": generated_colour(r["Constructor"]["constructorId"]),
                        "grid_position": grid, "match_key": jolpica.driver_key(d)})

    # lap times per driver
    per = {}
    pos_at = {}
    missing_pos = 0
    for lap in lap_list:
        for tm in lap["Timings"]:
            n = num_of.get(tm["driverId"])
            if n is None:
                continue
            per.setdefault(n, {})[int(lap["number"])] = parse_time(tm.get("time"))
            if tm.get("position") in (None, ""):
                missing_pos += 1
            else:
                pos_at[(n, int(lap["number"]))] = int(tm["position"])
    if missing_pos:
        log.append(f"{missing_pos} timing entries had no position, so there is no position row for them")
    stops = {}
    for p in pits_raw:
        n = num_of.get(p["driverId"])
        if n is not None:
            stops.setdefault(n, []).append((int(p["lap"]), parse_pit_duration(p.get("duration"))))
    pit_out_laps = {(n, lap + 1) for n, lst in stops.items() for lap, _ in lst}

    rows, end_of = [], {}
    for n, laps_d in per.items():
        cum, k = 0.0, 1
        while k in laps_d and laps_d[k] is not None:
            start = cum
            cum += laps_d[k]
            end_of[(n, k)] = cum
            rows.append({"driver_number": n, "lap_number": k, "raw_start": start, "raw_end": cum, "dur": laps_d[k],
                         "d1": np.nan, "d2": np.nan, "d3": np.nan, "raw_s1_end": np.nan, "raw_s2_end": np.nan,
                         "pit_out": (n, k) in pit_out_laps})
            k += 1
        if k - 1 < max(laps_d):
            log.append(f"driver {n}: lap {k} has no usable time, later laps dropped")
    laps = compute_lap_table(pd.DataFrame(rows))
    numbers_present = sorted(per)
    race_start_t, _ = race_start_from_laps(laps, numbers_present)
    replay_start = race_start_t - config.REPLAY_PAD_S
    replay_end = float(laps["lap_end_t"].max()) + config.REPLAY_PAD_S

    # positions
    pos_rows = [{"t": end_of[(n, k)], "driver_number": n, "position": p} for (n, k), p in pos_at.items() if (n, k) in end_of]
    for d in drivers:
        if d["grid_position"] is not None:
            pos_rows.append({"t": 0.0, "driver_number": d["driver_number"], "position": d["grid_position"]})
    position = pd.DataFrame(pos_rows, columns=bundle_io.TABLE_COLUMNS["position"]).sort_values("t", kind="stable").reset_index(drop=True)

    # computed gaps and intervals
    leader_of = {}
    for (n, k), p in pos_at.items():
        if p == 1:
            leader_of[k] = n
    gap_of = {}
    int_rows = []
    for (n, k), end in sorted(end_of.items(), key=lambda kv: kv[1]):
        lead = leader_of.get(k)
        if lead is None or (lead, k) not in end_of:
            continue
        gap = end - end_of[(lead, k)]
        lead_laps_done = sum(1 for kk in range(1, max(per[lead]) + 1) if (lead, kk) in end_of and end_of[(lead, kk)] <= end + 1e-9)
        behind = lead_laps_done - k
        if n == lead:
            gap_of[(n, k)] = ("num", 0.0)
        elif behind >= 1:
            gap_of[(n, k)] = ("txt", f"+{behind} LAP")
        else:
            gap_of[(n, k)] = ("num", gap)
    ahead_of = {}
    for (n, k), p in pos_at.items():
        ahead_of[(k, p)] = n
    for (n, k), end in sorted(end_of.items(), key=lambda kv: kv[1]):
        g = gap_of.get((n, k))
        if g is None:
            continue
        p = pos_at.get((n, k))
        interval = np.nan
        if g[0] == "num" and p is not None and p > 1:
            a = ahead_of.get((k, p - 1))
            ga = gap_of.get((a, k)) if a is not None else None
            if ga is not None and ga[0] == "num":
                interval = g[1] - ga[1]
        int_rows.append({"t": end, "driver_number": n, "interval_num": interval, "interval_txt": None,
                         "gap_num": g[1] if g[0] == "num" else np.nan, "gap_txt": g[1] if g[0] == "txt" else None})
    intervals = pd.DataFrame(int_rows, columns=bundle_io.TABLE_COLUMNS["intervals"]).sort_values("t", kind="stable")

    # pit stops
    pit_rows = []
    for n, lst in stops.items():
        for lap, dur in lst:
            if (n, lap) not in end_of:
                continue
            pit_rows.append({"t": end_of[(n, lap)] + (dur or 0.0), "driver_number": n, "lap_number": lap,
                             "lane_duration": dur if dur is not None else np.nan, "stop_duration": np.nan})
    pit = pd.DataFrame(pit_rows, columns=bundle_io.TABLE_COLUMNS["pit"]).sort_values("t", kind="stable")

    # stints, split at pit stops
    st_rows = []
    for n in numbers_present:
        done = [k for (m, k) in end_of if m == n]
        if not done:
            log.append(f"driver {n}: no usable lap from lap 1, no stint rows")
            continue
        last = max(done)
        cuts = sorted(lap for lap, _ in stops.get(n, []) if lap < last)
        starts = [1] + [c + 1 for c in cuts]
        for i, s in enumerate(starts):
            e = (starts[i + 1] - 1) if i + 1 < len(starts) else last
            known = replay_start if i == 0 else end_of.get((n, s - 1), np.nan)
            st_rows.append({"driver_number": n, "stint_number": i + 1, "lap_start": s, "lap_end": e, "compound": None,
                            "tyre_age_at_start": np.nan, "known_t": known})
    stints = pd.DataFrame(st_rows, columns=bundle_io.TABLE_COLUMNS["stints"])

    # total laps, chequered row, result
    winner = results[0]
    total_laps = int(winner["laps"])
    win_n = num_of[winner["Driver"]["driverId"]]
    rc_rows = []
    if (win_n, total_laps) in end_of:
        rc_rows.append({"t": end_of[(win_n, total_laps)], "lap_number": total_laps, "category": "Flag", "flag": "CHEQUERED",
                        "scope": "Track", "sector": np.nan, "driver_number": np.nan,
                        "message": "CHEQUERED FLAG (derived from lap times)"})
    else:
        log.append("winner's last lap time missing: no chequered row")
    race_control = pd.DataFrame(rc_rows, columns=bundle_io.TABLE_COLUMNS["race_control"])
    result_rows = []
    for r, n in zip(results, numbers):
        dnf, dns, dsq = points.classify_status(r.get("status"))
        result_rows.append({"position": int(r["position"]) if str(r["position"]).isdigit() else None, "driver_number": n,
                            "number_of_laps": int(r["laps"]), "dnf": dnf, "dns": dns, "dsq": dsq, "status": r.get("status")})

    # weather
    missing = list(MISSING)
    t_race = None
    if entry.get("time"):
        t_race = datetime.fromisoformat(f"{entry['date']}T{entry['time'].rstrip('Z')}+00:00")
    weather = pd.DataFrame(columns=bundle_io.TABLE_COLUMNS["weather"])
    if t_race is not None and weather_raw and weather_raw.get("hourly"):
        h = weather_raw["hourly"]
        ts = [(datetime.fromisoformat(x + ":00+00:00") - t_race).total_seconds() if len(x) == 16 else
              (datetime.fromisoformat(x).replace(tzinfo=timezone.utc) - t_race).total_seconds() for x in h["time"]]
        weather = pd.DataFrame({
            "t": ts, "air_temperature": h.get("temperature_2m"), "track_temperature": np.nan,
            "humidity": h.get("relative_humidity_2m"), "wind_speed": h.get("wind_speed_10m"), "wind_direction": np.nan,
            "rainfall": [None if p is None else (1.0 if p > 0 else 0.0) for p in h.get("precipitation", [])],
            "pressure": np.nan})
    else:
        missing.append("weather (no start time or no archive weather for this race)")
    t0 = t_race or datetime.fromisoformat(f"{entry.get('date', race['date'])}T00:00:00+00:00")

    meta = {
        "session": {"session_key": race_id, "session_name": "Race", "session_type": "Race", "date_start": t0.isoformat(),
                    "date_end": (t0 + timedelta(seconds=replay_end)).isoformat(), "meeting_key": race_id, "year": int(season)},
        "meeting": {"meeting_name": race["raceName"], "meeting_official_name": None, "location": race["Circuit"]["Location"].get("locality"),
                    "country_name": race["Circuit"]["Location"].get("country"), "circuit_short_name": race["Circuit"]["circuitName"]},
        "season": int(season), "t0_utc": t0.isoformat(), "race_start_t": race_start_t, "total_laps": total_laps,
        "L_ref": None, "reference_lap": None, "drivers": drivers,
        "grid_source": "Jolpica results grid (0 means pit lane start, stored as null)",
        "start_points": jolpica.start_points_for_bundle(start_raw, drivers, log),
        "circuit": ({"lat": entry["lat"], "lon": entry["lon"], "circuit": entry.get("circuit"), "round": int(rnd)}
                    if entry.get("lat") is not None else None),
        "has_drs": False, "replay_start_t": replay_start, "replay_end_t": replay_end, "outline": [],
        "seek_index": seek_index(laps), "source": "jolpica", "kind": "lap_only", "race_id": race_id,
        "session_name_short": "R", "corners": [], "missing": missing,
    }
    tmp = bundle_dir / f"_tmp_{race_id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    (tmp / "meta.json").write_text(json.dumps(meta))
    (tmp / "forecast.json").write_text("null")
    (tmp / "session_result.json").write_text(json.dumps(result_rows))
    tables = {"laps": laps, "position": position, "intervals": intervals, "stints": stints, "pit": pit,
              "race_control": race_control, "weather": weather}
    for name, df in tables.items():
        df.to_parquet(tmp / f"{name}.parquet", index=False)
    final = bundle_dir / bundle_io.parse_race_id(race_id)["folder"]
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    summary = [f"{race_id}: laps {len(laps)}, position {len(position)}, intervals {len(intervals)}, pit {len(pit)}, "
               f"stints {len(stints)}, weather {len(weather)}, race_control {len(race_control)}"] + [f"- {x}" for x in log]
    if write_log:
        build_fastf1_log(summary)
    return {"race_id": race_id, "folder": str(final), "log": summary,
            "rows": {k: int(len(v)) for k, v in tables.items()}}


def build_fastf1_log(lines):
    import build_bundle_fastf1
    build_bundle_fastf1.append_build_log("jl", lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    a = ap.parse_args()
    print("\n".join(build(a.season, a.round, write_log=True)["log"]))


if __name__ == "__main__":
    main()
