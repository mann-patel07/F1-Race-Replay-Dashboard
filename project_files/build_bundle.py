"""Turn raw OpenF1 JSON into the replay bundle (parquet tables plus meta.json)."""
import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import config
from bundle_common import (CAR_COLUMNS, compute_lap_table, compute_minisectors, find_reference_lap, finish_car_data,
                           finish_location, max_gap, minisectors_for_lap, reference_outline, seek_index,
                           speed_integrals, race_start_from_laps, write_log_section, write_sample_tables)

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
BUNDLE_DIR = ROOT / "data" / "bundle"
LOG_PATH = ROOT / "RUN_LOG.md"
MAX_FILE_BYTES = config.MAX_BUNDLE_FILE_MB * 1024 * 1024
NO_SAMPLE_GAP_S = config.MINISECTOR_MAX_GAP_S


def load(raw, name, default=None):
    path = raw / f"{name}.json"
    if not path.exists():
        return default
    return json.loads(path.read_text())


def to_t(values, t0):
    """Convert ISO timestamps to seconds since t0. Missing values become NaN."""
    s = pd.to_datetime(pd.Series(values, dtype="object"), format="ISO8601", utc=True)
    return (s - t0).dt.total_seconds().to_numpy(dtype="float64")


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else np.nan


def txt(x):
    return x if isinstance(x, str) else None


def build_laps(raw_laps, t0):
    df = pd.DataFrame(raw_laps)
    table = pd.DataFrame({
        "driver_number": df["driver_number"], "lap_number": df["lap_number"], "raw_start": to_t(df["date_start"], t0),
        "raw_end": np.nan, "dur": [num(x) for x in df["lap_duration"]],
        "d1": [num(x) for x in df["duration_sector_1"]], "d2": [num(x) for x in df["duration_sector_2"]],
        "d3": [num(x) for x in df["duration_sector_3"]], "raw_s1_end": np.nan, "raw_s2_end": np.nan,
        "pit_out": [bool(x) if x is not None else False for x in df["is_pit_out_lap"]]})
    return compute_lap_table(table)


def load_samples(raw, endpoint, drivers, t0, cols):
    frames = []
    for n in drivers:
        rows = load(raw, f"{endpoint}_{n}", [])
        if not rows:
            continue
        d = pd.DataFrame(rows)
        f = pd.DataFrame({"driver_number": np.int16(n), "t": to_t(d["date"], t0)})
        for c in cols:
            f[c] = d[c].to_numpy()
        frames.append(f.dropna(subset=["t"]).sort_values("t"))
    if not frames:
        return pd.DataFrame(columns=["driver_number", "t"] + cols)
    return pd.concat(frames, ignore_index=True)


def fallback_points(raw, drivers, teams):
    fb = load(raw, "fallback_standings")
    if not fb:
        return None
    rnd = fb.get("round")
    if rnd == 1:
        return {"source": "all start points are 0 (round 1)",
                "drivers": {d["name_acronym"]: (0.0, i + 1) for i, d in enumerate(drivers)},
                "teams": {t: (0.0, i + 1) for i, t in enumerate(teams)}}
    if not fb.get("drivers") or not fb.get("constructors"):
        return None
    ds = fb["drivers"]["MRData"]["StandingsTable"]["StandingsLists"][0]["DriverStandings"]
    cs = fb["constructors"]["MRData"]["StandingsTable"]["StandingsLists"][0]["ConstructorStandings"]
    dmap = {x["Driver"].get("code"): (float(x["points"]), int(x["position"])) for x in ds}
    tmap = {x["Constructor"]["name"]: (float(x["points"]), int(x["position"])) for x in cs}
    return {"source": f"Jolpica-F1 standings after round {rnd - 1}", "drivers": dmap, "teams": tmap}


def build(session_key):
    raw = RAW_DIR / str(session_key)
    if not raw.exists():
        sys.exit(f"no raw data in {raw}")
    final_dir = BUNDLE_DIR / str(session_key)
    out = BUNDLE_DIR / f"_tmp_{session_key}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    log = []
    session = load(raw, "session")
    meeting = (load(raw, "meeting") or [{}])[0]
    t0 = pd.Timestamp(session["date_start"]).tz_convert("UTC")
    drivers_raw = load(raw, "drivers")
    numbers = sorted({d["driver_number"] for d in drivers_raw})

    laps = build_laps(load(raw, "laps"), t0)

    race_start_t, rs_rule = race_start_from_laps(laps, numbers)
    log.append(f"race_start_t = {race_start_t:.3f} s ({rs_rule})")

    result = load(raw, "session_result", [])
    winner = [r for r in result if r.get("position") == 1]
    if winner and winner[0].get("number_of_laps"):
        total_laps = int(winner[0]["number_of_laps"])
        tl_rule = "number_of_laps of position 1 in session_result"
    else:
        total_laps = int(laps.lap_number.max())
        tl_rule = "largest lap_number in laps"
    log.append(f"total_laps = {total_laps} ({tl_rule})")

    cars = load_samples(raw, "car_data", numbers, t0, ["speed", "drs", "throttle", "brake", "n_gear", "rpm"])
    locs = load_samples(raw, "location", numbers, t0, ["x", "y"])
    per_car = speed_integrals(cars)
    per_loc = {int(n): g["t"].to_numpy() for n, g in locs.groupby("driver_number")}

    ref = find_reference_lap(laps, per_car, per_loc)
    outline = []
    if ref:
        outline = reference_outline(locs, ref)
        log.append(f"reference lap: driver {ref['driver_number']} lap {ref['lap_number']}, L_ref = {ref['L_ref']:.1f} m, outline points {len(outline)}")
    else:
        log.append("no reference lap found: L_ref, outline and mini-sectors are n/a")

    complete = eligible = 0
    ms = pd.DataFrame(columns=["driver_number", "lap_number", "k", "end_t", "duration"])
    if ref:
        ms, complete, eligible = compute_minisectors(laps, per_car, ref["L_ref"])

    replay_start = race_start_t - config.REPLAY_PAD_S
    replay_end = float(laps["lap_end_t"].max()) + config.REPLAY_PAD_S

    drv_rows = []
    for d in drivers_raw:
        drv_rows.append({"driver_number": int(d["driver_number"]), "acronym": d["name_acronym"],
                         "full_name": d["full_name"], "team_name": d["team_name"],
                         "team_colour": d["team_colour"]})

    pos_raw = load(raw, "position")
    position = pd.DataFrame({"t": to_t([r["date"] for r in pos_raw], t0),
                             "driver_number": [int(r["driver_number"]) for r in pos_raw],
                             "position": [int(r["position"]) for r in pos_raw]}).sort_values("t", kind="stable")

    grid_raw = load(raw, "starting_grid", [])
    grid_src = "OpenF1 starting_grid"
    grid = {int(r["driver_number"]): int(r["position"]) for r in grid_raw if r.get("position") is not None}
    if not grid:
        first = position.sort_values("t", kind="stable").groupby("driver_number").head(1)
        grid = {int(a): int(b) for a, b in zip(first.driver_number, first.position)}
        grid_src = "first position record of each driver (starting_grid was empty)"
        log.append("starting_grid empty: grid taken from each driver's first position record")
    for d in drv_rows:
        d["grid_position"] = grid.get(d["driver_number"])

    int_raw = load(raw, "intervals", [])
    intervals = pd.DataFrame({
        "t": to_t([r["date"] for r in int_raw], t0),
        "driver_number": [int(r["driver_number"]) for r in int_raw],
        "interval_num": [num(r.get("interval")) for r in int_raw],
        "interval_txt": [txt(r.get("interval")) for r in int_raw],
        "gap_num": [num(r.get("gap_to_leader")) for r in int_raw],
        "gap_txt": [txt(r.get("gap_to_leader")) for r in int_raw],
    }).sort_values("t", kind="stable")

    st_raw = load(raw, "stints")
    lap_start_lookup = {(int(r.driver_number), int(r.lap_number)): r.lap_start_t for r in laps.itertuples()}
    lap_end_lookup = {(int(r.driver_number), int(r.lap_number)): r.lap_end_t for r in laps.itertuples()}
    first_stint = {}
    for s in st_raw:
        n = int(s["driver_number"])
        first_stint[n] = min(first_stint.get(n, 10 ** 6), int(s["stint_number"]))
    st_rows = []
    unknown_stint = 0
    for s in st_raw:
        n, ls = int(s["driver_number"]), s.get("lap_start")
        if int(s["stint_number"]) == first_stint[n]:
            known = replay_start
        else:
            known = lap_start_lookup.get((n, ls), np.nan)
            if ls is not None and np.isnan(known):
                known = lap_end_lookup.get((n, ls - 1), np.nan)
            if np.isnan(known):
                unknown_stint += 1
        st_rows.append({"driver_number": n, "stint_number": int(s["stint_number"]),
                        "lap_start": ls, "lap_end": s.get("lap_end"),
                        "compound": s.get("compound"), "tyre_age_at_start": num(s.get("tyre_age_at_start")),
                        "known_t": known})
    stints = pd.DataFrame(st_rows)
    if unknown_stint:
        log.append(f"{unknown_stint} stints have no usable start time and are never shown")

    pit_raw = load(raw, "pit", [])
    pit = pd.DataFrame({
        "t": to_t([r["date"] for r in pit_raw], t0),
        "driver_number": [int(r["driver_number"]) for r in pit_raw],
        "lap_number": [r.get("lap_number") for r in pit_raw],
        "lane_duration": [num(r.get("lane_duration")) for r in pit_raw],
        "stop_duration": [num(r.get("stop_duration")) for r in pit_raw],
    }).sort_values("t", kind="stable") if pit_raw else pd.DataFrame(
        columns=["t", "driver_number", "lap_number", "lane_duration", "stop_duration"])

    rc_raw = load(raw, "race_control")
    race_control = pd.DataFrame({
        "t": to_t([r["date"] for r in rc_raw], t0),
        "lap_number": [r.get("lap_number") for r in rc_raw],
        "category": [r.get("category") for r in rc_raw],
        "flag": [r.get("flag") for r in rc_raw],
        "scope": [r.get("scope") for r in rc_raw],
        "sector": [r.get("sector") for r in rc_raw],
        "driver_number": [r.get("driver_number") for r in rc_raw],
        "message": [r.get("message") for r in rc_raw],
    }).sort_values("t", kind="stable").reset_index(drop=True)

    w_raw = load(raw, "weather", [])
    wcols = ["air_temperature", "track_temperature", "humidity", "wind_speed", "wind_direction", "rainfall", "pressure"]
    weather = pd.DataFrame({"t": to_t([r["date"] for r in w_raw], t0),
                            **{c: [num(r.get(c)) for r in w_raw] for c in wcols}}).sort_values("t", kind="stable") if w_raw else pd.DataFrame(columns=["t"] + wcols)

    drs_vals = set(int(v) for v in cars["drs"].dropna().unique()) if len(cars) else set()
    has_drs = bool(drs_vals & set(config.DRS_ON + config.DRS_ELIGIBLE))
    if not has_drs:
        log.append("no DRS values in car_data: DRS column hidden")

    cd = load(raw, "championship_drivers", [])
    ct = load(raw, "championship_teams", [])
    teams = sorted({d["team_name"] for d in drv_rows})
    start_points = {"source": "OpenF1 championship_drivers and championship_teams (points_start)", "drivers": {}, "teams": {}}
    ok_d = bool(cd) and any(r.get("points_start") is not None for r in cd)
    ok_t = bool(ct) and any(r.get("points_start") is not None for r in ct)
    if ok_d and ok_t:
        by_num = {int(r["driver_number"]): r for r in cd}
        for d in drv_rows:
            r = by_num.get(d["driver_number"])
            start_points["drivers"][str(d["driver_number"])] = (
                {"points": r["points_start"], "position": r["position_start"]} if r and r.get("points_start") is not None else None)
        by_team = {r["team_name"]: r for r in ct}
        for t_name in teams:
            r = by_team.get(t_name)
            start_points["teams"][t_name] = (
                {"points": r["points_start"], "position": r["position_start"]} if r and r.get("points_start") is not None else None)
    else:
        fb = fallback_points(raw, drv_rows, teams)
        if fb:
            start_points["source"] = fb["source"]
            for d in drv_rows:
                v = fb["drivers"].get(d["acronym"])
                start_points["drivers"][str(d["driver_number"])] = {"points": v[0], "position": v[1]} if v else None
            for t_name in teams:
                v = fb["teams"].get(t_name)
                start_points["teams"][t_name] = {"points": v[0], "position": v[1]} if v else None
            log.append(f"start points from fallback: {fb['source']}")
        else:
            start_points["source"] = "n/a"
            log.append("no start points available: standings show n/a")

    coords = load(raw, "circuit")
    forecast = load(raw, "forecast")

    meta = {
        "session": session, "meeting": {k: meeting.get(k) for k in ("meeting_name", "meeting_official_name", "location", "country_name", "circuit_short_name")},
        "season": int(session["year"]), "t0_utc": t0.isoformat(), "race_start_t": race_start_t,
        "total_laps": total_laps, "L_ref": ref["L_ref"] if ref else None, "reference_lap": ref,
        "drivers": drv_rows, "grid_source": grid_src, "start_points": start_points,
        "circuit": coords, "has_drs": has_drs, "replay_start_t": replay_start, "replay_end_t": replay_end,
        "outline": outline, "seek_index": seek_index(laps),
        "source": "openf1", "kind": "full", "race_id": f"of_{session['session_key']}", "session_name": session["session_name"],
        "corners": [], "missing": [],
    }
    (out / "meta.json").write_text(json.dumps(meta))
    (out / "forecast.json").write_text(json.dumps(forecast))
    (out / "session_result.json").write_text(json.dumps(result))
    upg = ROOT / "data" / "upgrades" / f"{session['meeting_key']}.json"
    if not upg.exists():
        upg.parent.mkdir(parents=True, exist_ok=True)
        upg.write_text(json.dumps({"source": None, "items": []}))

    tables = {"laps": laps, "minisectors": ms, "position": position, "intervals": intervals, "stints": stints,
              "pit": pit, "race_control": race_control, "weather": weather}
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
    cars = finish_car_data(cars)
    locs = finish_location(locs)
    for name, df in (("car_data", cars), ("location", locs)):
        write_sample_tables(out, name, df)
        tables[name] = df

    big = [p.name for p in out.glob("*") if p.stat().st_size > MAX_FILE_BYTES]
    if big:
        log.append(f"files over 20 MB: {big}")

    rep = ["Rows per table: " + ", ".join(f"{k} {len(v)}" for k, v in tables.items()),
           f"Laps with null lap_duration: {int(laps.lap_duration.isna().sum())}",
           f"Laps with null lap_start_t after fill rules: {int(laps.lap_start_t.isna().sum())}",
           f"Laps with null raw date_start: {int(pd.Series([r.get('date_start') is None for r in load(raw, 'laps')]).sum())}",
           f"Laps with complete mini-sectors: {complete} of {eligible} eligible ({(100 * complete / eligible if eligible else 0):.1f} percent)",
           f"DRS values seen in car_data: {sorted(drs_vals)}", ""]
    rep += [f"- {x}" for x in log] + [""]
    combos = Counter(zip(race_control.category.fillna("None"), race_control.flag.fillna("None"), race_control.scope.fillna("None")))
    rep.append("Distinct (category, flag, scope) in race control:")
    rep += [f"- {k}: {v}" for k, v in sorted(combos.items())] + [""]

    def norm(m):
        m = re.sub(r"\(([A-Z]{3})\)", "(<DRV>)", m or "")
        return re.sub(r"\d+(?::\d+)*(?:\.\d+)?", "<N>", m)
    pats = Counter(norm(m) for m in race_control.message)
    rep.append("Distinct race control messages (numbers and driver codes replaced):")
    rep += [f"- {v}x {k}" for k, v in sorted(pats.items())]
    if final_dir.exists():
        shutil.rmtree(final_dir)
    out.rename(final_dir)
    out = final_dir
    write_log_section("Data report", "\n".join(rep))
    sizes = {p.name: p.stat().st_size for p in sorted(out.glob("*"))}
    print(json.dumps(sizes, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, help="session_key, default is the newest downloaded")
    a = ap.parse_args()
    if a.session:
        key = a.session
    else:
        dirs = sorted((p for p in RAW_DIR.iterdir() if (p / "manifest.json").exists()), key=lambda p: p.stat().st_mtime)
        if not dirs:
            sys.exit("no downloaded session found, run download.py first")
        key = int(dirs[-1].name)
    build(key)


if __name__ == "__main__":
    main()
