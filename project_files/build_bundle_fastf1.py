"""Build a replay bundle from archive/<Y>/<RR>/<code>/. Reads only saved files (plus Jolpica and Open-Meteo).

python build_bundle_fastf1.py --year Y --round R --session Race|Sprint
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import bundle_io
import config
import jolpica
import points
from bundle_common import (compute_lap_table, compute_minisectors, find_reference_lap, finish_car_data, finish_location,
                           generated_colour, race_start_from_laps, reference_outline, seek_index, speed_integrals,
                           write_log_section, write_sample_tables)

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / config.ARCHIVE_DIR

REQUIRED_LAP_COLUMNS = ["DriverNumber", "LapNumber", "LapTime", "LapStartTime", "Time", "Sector1Time", "Sector2Time",
                        "Sector3Time", "Sector1SessionTime", "Sector2SessionTime", "PitOutTime", "PitInTime", "Stint",
                        "Compound", "TyreLife", "Position"]
NUMBER_RE = re.compile(r"^\+?\d+(\.\d+)?$")


def secs(series):
    """Timedelta series to float seconds (NaT to NaN)."""
    return pd.to_timedelta(series).dt.total_seconds().to_numpy(dtype="float64")


def describe_archive(folder):
    """Columns and dtypes of every archived table, as text for the log."""
    lines = []
    for p in sorted(Path(folder).glob("*.parquet")):
        df = pd.read_parquet(p)
        lines.append(f"{p.stem} ({len(df)} rows): " + ", ".join(f"{c} {t}" for c, t in df.dtypes.astype(str).items()))
    return "\n".join(lines)


def split_gap(value):
    """A plain number with an optional leading plus goes to the number column, anything else to the text column."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan, None
    text = str(value).strip()
    if NUMBER_RE.match(text):
        return float(text.lstrip("+")), None
    return np.nan, str(value)


def default_start_points(year, rnd, has_sprint, drivers, log):
    raw = jolpica.fetch_start_points(year, rnd, has_sprint)
    return jolpica.start_points_for_bundle(raw, drivers, log)


def build(year, rnd, session_name, archive_dir=None, bundle_dir=None, start_points_fn=None, forecast_fn=None,
          write_log=True):
    archive_dir = Path(archive_dir or ARCHIVE)
    bundle_dir = Path(bundle_dir or bundle_io.BUNDLE_DIR)
    code = config.SESSION_CODES[session_name]
    src = archive_dir / str(year) / f"{int(rnd):02d}" / code
    if not (src / "info.json").exists():
        raise FileNotFoundError(f"no archive in {src}")
    info = json.loads((src / "info.json").read_text())
    race_id = bundle_io.make_race_id("fastf1", year, rnd, code)
    log, missing = [], []

    laps_raw = pd.read_parquet(src / "laps.parquet")
    absent = [c for c in REQUIRED_LAP_COLUMNS if c not in laps_raw.columns]
    if absent:
        raise ValueError(f"required lap columns missing: {absent}")
    results = pd.read_parquet(src / "results.parquet")
    t0 = pd.Timestamp(info["t0_date"])
    t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")

    numbers = sorted(int(x) for x in results["DriverNumber"])
    laps_raw = laps_raw[laps_raw["DriverNumber"].astype(str).str.isdigit()].copy()
    table = pd.DataFrame({
        "driver_number": laps_raw["DriverNumber"].astype(int).to_numpy(), "lap_number": laps_raw["LapNumber"].astype(int).to_numpy(),
        "raw_start": secs(laps_raw["LapStartTime"]), "raw_end": secs(laps_raw["Time"]), "dur": secs(laps_raw["LapTime"]),
        "d1": secs(laps_raw["Sector1Time"]), "d2": secs(laps_raw["Sector2Time"]), "d3": secs(laps_raw["Sector3Time"]),
        "raw_s1_end": secs(laps_raw["Sector1SessionTime"]), "raw_s2_end": secs(laps_raw["Sector2SessionTime"]),
        "pit_out": laps_raw["PitOutTime"].notna().to_numpy()})
    laps = compute_lap_table(table)
    race_start_t, rs_rule = race_start_from_laps(laps, numbers)
    log.append(f"race_start_t = {race_start_t:.3f} s ({rs_rule})")
    replay_start = race_start_t - config.REPLAY_PAD_S
    replay_end = float(laps["lap_end_t"].max()) + config.REPLAY_PAD_S

    # drivers
    drivers, grid_zero = [], []
    for r in results.itertuples():
        n = int(r.DriverNumber)
        grid = None if pd.isna(r.GridPosition) or int(r.GridPosition) == 0 else int(r.GridPosition)
        if grid is None:
            grid_zero.append(r.Abbreviation)
        colour = r.TeamColor if isinstance(r.TeamColor, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", r.TeamColor) else generated_colour(r.TeamName)
        drivers.append({"driver_number": n, "acronym": r.Abbreviation, "full_name": r.FullName, "team_name": r.TeamName,
                        "team_colour": colour, "grid_position": grid})
    grid_source = "FastF1 results GridPosition"
    if grid_zero:
        grid_source += f" (a grid position of 0 means pit lane start or no grid slot: stored as null for {', '.join(grid_zero)})"

    # session result and total laps
    rows = []
    for r in results.itertuples():
        dnf, dns, dsq = points.classify_status(r.Status)
        rows.append({"position": None if pd.isna(r.Position) else int(r.Position), "driver_number": int(r.DriverNumber),
                     "number_of_laps": None if pd.isna(r.Laps) else int(r.Laps), "dnf": dnf, "dns": dns, "dsq": dsq,
                     "status": r.Status})
    log.append("distinct results Status values: " + ", ".join(sorted({str(x) for x in results["Status"]})))
    if info.get("total_laps"):
        total_laps = int(info["total_laps"])
    else:
        first = [x for x in rows if x["position"] == 1]
        mine = laps[laps.driver_number == first[0]["driver_number"]] if first else laps
        total_laps = int(mine.lap_number.max())

    # stints
    laps_sorted = laps_raw.assign(driver_number=laps_raw["DriverNumber"].astype(int), lap_number=laps_raw["LapNumber"].astype(int))
    start_of = {(int(r.driver_number), int(r.lap_number)): r.lap_start_t for r in laps.itertuples()}
    end_of = {(int(r.driver_number), int(r.lap_number)): r.lap_end_t for r in laps.itertuples()}
    st_rows = []
    for n, g in laps_sorted.groupby("driver_number"):
        stint_ids = sorted(g["Stint"].dropna().unique())
        for k, sid in enumerate(stint_ids):
            gs = g[g["Stint"] == sid].sort_values("lap_number")
            first_lap, last_lap = int(gs["lap_number"].iloc[0]), int(gs["lap_number"].iloc[-1])
            comp = gs["Compound"].dropna()
            life = gs["TyreLife"].iloc[0]
            age = np.nan if pd.isna(life) else max(0.0, float(life) - 1)
            if k == 0:
                known = replay_start
            else:
                known = start_of.get((n, first_lap), np.nan)
                if np.isnan(known):
                    known = end_of.get((n, first_lap - 1), np.nan)
            st_rows.append({"driver_number": int(n), "stint_number": int(sid), "lap_start": first_lap, "lap_end": last_lap,
                            "compound": comp.iloc[0] if len(comp) else None, "tyre_age_at_start": age, "known_t": known})
    stints = pd.DataFrame(st_rows, columns=bundle_io.TABLE_COLUMNS["stints"])

    # pit: t is the pit lane exit (next lap's PitOutTime)
    pit_rows = []
    lap_by = {(int(r.driver_number), int(r.lap_number)): r for r in laps_sorted.itertuples()}
    for (n, ln), r in lap_by.items():
        if pd.isna(r.PitInTime):
            continue
        nxt = lap_by.get((n, ln + 1))
        if nxt is None or pd.isna(nxt.PitOutTime):
            continue
        pin, pout = r.PitInTime.total_seconds(), nxt.PitOutTime.total_seconds()
        pit_rows.append({"t": pout, "driver_number": n, "lap_number": ln, "lane_duration": pout - pin, "stop_duration": np.nan})
    pit = pd.DataFrame(pit_rows, columns=bundle_io.TABLE_COLUMNS["pit"]).sort_values("t", kind="stable")

    # race control
    if (src / "race_control.parquet").exists():
        rc = pd.read_parquet(src / "race_control.parquet")
        tt = pd.to_datetime(rc["Time"]).dt.tz_localize("UTC") if pd.to_datetime(rc["Time"]).dt.tz is None else pd.to_datetime(rc["Time"]).dt.tz_convert("UTC")
        race_control = pd.DataFrame({
            "t": (tt - t0).dt.total_seconds().to_numpy(), "lap_number": rc["Lap"].astype(float).to_numpy(),
            "category": rc["Category"].to_numpy(), "flag": rc["Flag"].to_numpy(), "scope": rc["Scope"].to_numpy(),
            "sector": rc["Sector"].astype(float).to_numpy(),
            "driver_number": pd.to_numeric(rc["RacingNumber"], errors="coerce").to_numpy(),
            "message": rc["Message"].to_numpy()}).sort_values("t", kind="stable").reset_index(drop=True)
    else:
        race_control = pd.DataFrame(columns=bundle_io.TABLE_COLUMNS["race_control"])
        missing.append("race control feed (not in the archive)")

    # weather
    if (src / "weather.parquet").exists():
        w = pd.read_parquet(src / "weather.parquet")
        weather = pd.DataFrame({
            "t": secs(w["Time"]), "air_temperature": w["AirTemp"].astype(float).to_numpy(),
            "track_temperature": w["TrackTemp"].astype(float).to_numpy(), "humidity": w["Humidity"].astype(float).to_numpy(),
            "wind_speed": w["WindSpeed"].astype(float).to_numpy(), "wind_direction": w["WindDirection"].astype(float).to_numpy(),
            "rainfall": w["Rainfall"].astype(float).to_numpy(), "pressure": w["Pressure"].astype(float).to_numpy()}).sort_values("t")
    else:
        weather = pd.DataFrame(columns=bundle_io.TABLE_COLUMNS["weather"])
        missing.append("weather (not in the archive)")

    # car data and location
    cd = pd.read_parquet(src / "car_data.parquet")
    cars = finish_car_data(pd.DataFrame({
        "driver_number": cd["driver_number"], "t": secs(cd["SessionTime"]), "speed": cd["Speed"], "drs": cd["DRS"],
        "throttle": cd["Throttle"], "brake": cd["Brake"].astype(float), "n_gear": cd["nGear"], "rpm": cd["RPM"]}))
    cars = cars.dropna(subset=["t"]).sort_values(["driver_number", "t"], kind="stable").reset_index(drop=True)
    pdx = pd.read_parquet(src / "pos_data.parquet")
    locs = pd.DataFrame({"driver_number": pdx["driver_number"], "t": secs(pdx["SessionTime"]), "x": pdx["X"], "y": pdx["Y"]})
    zero = (locs["x"] == 0) & (locs["y"] == 0)
    if zero.any():
        log.append(f"{int(zero.sum())} location rows with x = 0 and y = 0 dropped (no position fix)")
    locs = finish_location(locs[~zero].dropna(subset=["t"]).sort_values(["driver_number", "t"], kind="stable").reset_index(drop=True))
    has_drs = bool(set(int(v) for v in cars["drs"].dropna().unique()) & set(config.DRS_ON + config.DRS_ELIGIBLE))

    # position and intervals
    if (src / "timing_stream.parquet").exists():
        ts = pd.read_parquet(src / "timing_stream.parquet")
        ts = ts[ts["Driver"].astype(str).str.isdigit()].copy()
        ts["driver_number"] = ts["Driver"].astype(int)
        ts["t"] = secs(ts["Time"])
        ts = ts.sort_values("t", kind="stable")
        pos = ts.dropna(subset=["Position"])
        keep = (pos["Position"] != pos.groupby("driver_number")["Position"].shift()) | ~pos.duplicated("driver_number")
        position = pd.DataFrame({"t": pos["t"][keep], "driver_number": pos["driver_number"][keep],
                                 "position": pos["Position"][keep].astype(int)}).reset_index(drop=True)
        g = [split_gap(v) for v in ts["GapToLeader"]]
        i = [split_gap(v) for v in ts["IntervalToPositionAhead"]]
        intervals = pd.DataFrame({"t": ts["t"].to_numpy(), "driver_number": ts["driver_number"].to_numpy(),
                                  "interval_num": [x[0] for x in i], "interval_txt": [x[1] for x in i],
                                  "gap_num": [x[0] for x in g], "gap_txt": [x[1] for x in g]})
    else:
        done = laps.dropna(subset=["lap_end_t"]).merge(
            laps_sorted[["driver_number", "lap_number", "Position"]], on=["driver_number", "lap_number"])
        done = done.dropna(subset=["Position"])
        pos_rows = pd.DataFrame({"t": done["lap_end_t"], "driver_number": done["driver_number"], "position": done["Position"].astype(int)})
        grid_rows = pd.DataFrame([{"t": replay_start, "driver_number": d["driver_number"], "position": d["grid_position"]}
                                  for d in drivers if d["grid_position"] is not None])
        position = pd.concat([grid_rows, pos_rows], ignore_index=True).sort_values("t", kind="stable").reset_index(drop=True)
        intervals = pd.DataFrame(columns=bundle_io.TABLE_COLUMNS["intervals"])
        missing.append("intervals and gaps (timing stream not in the archive)")
        log.append("timing stream missing: positions come from the lap table")

    # reference lap, mini-sectors, outline
    per_car = speed_integrals(cars)
    per_loc = {int(n): g["t"].to_numpy() for n, g in locs.groupby("driver_number")}
    ref = find_reference_lap(laps, per_car, per_loc)
    outline, ms = [], pd.DataFrame(columns=bundle_io.TABLE_COLUMNS["minisectors"])
    complete = eligible = 0
    if ref:
        outline = reference_outline(locs, ref)
        ms, complete, eligible = compute_minisectors(laps, per_car, ref["L_ref"])
        log.append(f"reference lap: driver {ref['driver_number']} lap {ref['lap_number']}, L_ref = {ref['L_ref']:.1f} m")
    else:
        log.append("no reference lap found")
        missing.append("mini-sectors and track outline (no reference lap)")
    corners = []
    if (src / "corners.parquet").exists():
        cdf = pd.read_parquet(src / "corners.parquet")
        corners = [{"number": int(r.Number), "letter": r.Letter if isinstance(r.Letter, str) and r.Letter else "", "x": float(r.X), "y": float(r.Y)}
                   for r in cdf.itertuples()]

    # context: calendar entry, start points, forecast
    entry = jolpica.library_entry(year, rnd)
    circuit = None
    if entry and entry.get("lat") is not None:
        circuit = {"lat": entry["lat"], "lon": entry["lon"], "circuit": entry.get("circuit"), "round": int(rnd)}
    has_sprint = bool(entry and entry.get("sprint_date")) and code == "R"
    spf = start_points_fn or (lambda y, r, s, d, lg: default_start_points(y, r, s, d, lg))
    try:
        start_points = spf(year, rnd, has_sprint, drivers, log)
    except Exception as exc:
        log.append(f"start points failed: {exc}")
        start_points = {"source": "n/a", "drivers": {}, "teams": {}}
    forecast = None
    race_start_utc = t0 + timedelta(seconds=race_start_t)
    if year >= 2022:
        if forecast_fn is not None:
            forecast = forecast_fn(circuit, t0, t0 + timedelta(seconds=replay_end))
        elif circuit:
            forecast = jolpica.forecast_for(circuit["lat"], circuit["lon"], t0, t0 + timedelta(seconds=replay_end))
    if forecast is None:
        log.append("no forecast for this bundle")

    meta = {
        "session": {"session_key": race_id, "session_name": session_name, "session_type": session_name,
                    "date_start": race_start_utc.isoformat(), "date_end": (t0 + timedelta(seconds=replay_end)).isoformat(),
                    "meeting_key": f"ff_{year}_{int(rnd):02d}", "year": year},
        "meeting": {"meeting_name": info["event"], "meeting_official_name": info.get("official_event"),
                    "location": info.get("location"), "country_name": info.get("country"),
                    "circuit_short_name": info.get("location")},
        "season": year, "t0_utc": t0.isoformat(), "race_start_t": race_start_t, "total_laps": total_laps,
        "L_ref": ref["L_ref"] if ref else None, "reference_lap": ref, "drivers": drivers, "grid_source": grid_source,
        "start_points": start_points, "circuit": circuit, "has_drs": has_drs, "replay_start_t": replay_start,
        "replay_end_t": replay_end, "outline": outline, "seek_index": seek_index(laps),
        "source": "fastf1", "kind": "full", "race_id": race_id, "session_name_short": code, "corners": corners,
        "missing": missing,
    }

    tmp = bundle_dir / f"_tmp_{race_id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    (tmp / "meta.json").write_text(json.dumps(meta))
    (tmp / "forecast.json").write_text(json.dumps(forecast))
    (tmp / "session_result.json").write_text(json.dumps(rows))
    tables = {"laps": laps, "minisectors": ms, "position": position, "intervals": intervals, "stints": stints,
              "pit": pit, "race_control": race_control, "weather": weather}
    for name, df in tables.items():
        df.to_parquet(tmp / f"{name}.parquet", index=False)
    write_sample_tables(tmp, "car_data", cars)
    write_sample_tables(tmp, "location", locs)
    big = [p.name for p in tmp.glob("*") if p.stat().st_size > config.MAX_BUNDLE_FILE_MB * 1024 * 1024]
    if big:
        log.append(f"files over {config.MAX_BUNDLE_FILE_MB} MB: {big}")
    final = bundle_dir / bundle_io.parse_race_id(race_id)["folder"]
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    summary = [f"{race_id}: laps {len(laps)}, complete mini-sector laps {complete} of {eligible}, "
               f"position rows {len(position)}, interval rows {len(intervals)}, car rows {len(cars)}, location rows {len(locs)}"]
    summary += [f"- {x}" for x in log]
    if write_log:
        append_build_log(race_id, summary)
    return {"race_id": race_id, "folder": str(final), "log": summary}


def append_build_log(race_id, lines):
    text = (ROOT / "RUN_LOG.md").read_text() if (ROOT / "RUN_LOG.md").exists() else ""
    head = "## Version 3: bundle builds"
    block = "\n".join(lines)
    if head not in text:
        text = text.rstrip("\n") + f"\n\n{head}\n\n"
    text = text.rstrip("\n") + "\n" + block + "\n"
    (ROOT / "RUN_LOG.md").write_text(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--session", default="Race", choices=config.REPLAY_SESSION_NAMES)
    a = ap.parse_args()
    out = build(a.year, a.round, a.session)
    print("\n".join(out["log"]))


if __name__ == "__main__":
    main()
