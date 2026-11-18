"""Archive one FastF1 session as plain parquet files so later replays need no account.

Run as its own process: python archive_one.py --year Y --round R --session NAME
Exit codes: 0 ok, 2 rate limit, 3 not signed in, 4 no data for this session, 5 other error.
"""
import os

for _name in ("F1_USERNAME", "F1_PASSWORD"):
    os.environ.pop(_name, None)

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import config

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / config.ARCHIVE_DIR

EXIT_OK, EXIT_RATE, EXIT_AUTH, EXIT_NO_DATA, EXIT_OTHER = 0, 2, 3, 4, 5


def session_dir(year, rnd, name, base=None):
    return (base or ARCHIVE) / str(year) / f"{int(rnd):02d}" / config.SESSION_CODES[name]


def write_table(df, path, notes):
    """Write a DataFrame with pyarrow and zstd. Object columns that cannot be written become strings."""
    df = pd.DataFrame(df).reset_index(drop=True)
    for col in df.columns:
        if df[col].dtype == object:
            try:
                import pyarrow as pa
                pa.array(df[col])
            except Exception:
                df[col] = df[col].astype(str)
                notes.append(f"column {col} of {path.name} cast to string")
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)


def archive(year, rnd, name):
    import fastf1
    from fastf1.exceptions import RateLimitExceededError

    started = time.time()
    cache = ROOT / config.FASTF1_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(cache))
    error = None
    session = None
    try:
        session = fastf1.get_session(year, rnd, name)
        session.load(laps=True, telemetry=True, weather=True, messages=True)
    except RateLimitExceededError as exc:
        print(f"rate limit: {exc}", file=sys.stderr)
        return EXIT_RATE
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    laps_ok = False
    if session is not None:
        try:
            laps_ok = len(session.laps) > 0
        except Exception:
            laps_ok = False
    if error or not laps_ok:
        import f1_auth
        probe = f1_auth.probe()
        if not probe["signed_in"]:
            print("not signed in: " + probe["detail"], file=sys.stderr)
            return EXIT_AUTH
        if not laps_ok and (error is None or "data" in error.lower() or "no " in error.lower()):
            print("no data for this session" + (f" ({error})" if error else ""), file=sys.stderr)
            return EXIT_NO_DATA
        if error:
            print(error, file=sys.stderr)
            return EXIT_OTHER
        return EXIT_NO_DATA

    tmp = ARCHIVE / "_tmp" / str(year) / f"{int(rnd):02d}" / config.SESSION_CODES[name]
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    notes, missing, rows = [], [], {}

    def put(table, df):
        write_table(df, tmp / f"{table}.parquet", notes)
        rows[table] = int(len(df))

    put("laps", session.laps)
    frames = []
    for number, df in session.car_data.items():
        d = pd.DataFrame(df).copy()
        d.insert(0, "driver_number", int(number))
        frames.append(d)
    if frames:
        put("car_data", pd.concat(frames, ignore_index=True))
    else:
        missing.append("car_data")
    frames = []
    for number, df in session.pos_data.items():
        d = pd.DataFrame(df).copy()
        d.insert(0, "driver_number", int(number))
        frames.append(d)
    if frames:
        put("pos_data", pd.concat(frames, ignore_index=True))
    else:
        missing.append("pos_data")
    for table, getter in (("weather", lambda: session.weather_data), ("race_control", lambda: session.race_control_messages),
                          ("track_status", lambda: session.track_status), ("session_status", lambda: session.session_status),
                          ("results", lambda: session.results)):
        try:
            df = getter()
            if df is None or len(df) == 0:
                missing.append(table)
                continue
            put(table, df)
        except Exception as exc:
            missing.append(table)
            notes.append(f"{table}: {type(exc).__name__}: {exc}")
    try:
        import fastf1.api
        stream = fastf1.api.timing_data(session.api_path)[1]
        put("timing_stream", stream)
    except Exception as exc:
        missing.append("timing_stream")
        notes.append(f"timing_stream: {type(exc).__name__}: {exc}")
    circuit = {}
    try:
        ci = session.get_circuit_info()
        put("corners", ci.corners)
        circuit = {"rotation": float(ci.rotation)}
        (tmp / "circuit.json").write_text(json.dumps(circuit))
    except Exception as exc:
        missing.append("corners")
        notes.append(f"corners: {type(exc).__name__}: {exc}")

    t0 = pd.Timestamp(session.t0_date)
    t0 = t0.tz_localize("UTC") if t0.tzinfo is None else t0.tz_convert("UTC")
    info = {
        "year": year, "round": int(rnd), "event": str(session.event["EventName"]), "official_event": str(session.event.get("OfficialEventName", "")),
        "country": str(session.event.get("Country", "")), "location": str(session.event.get("Location", "")), "session": name,
        "t0_date": t0.isoformat(), "session_start": None,
        "total_laps": int(session.total_laps) if getattr(session, "total_laps", None) else None,
        "fastf1_version": fastf1.__version__, "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows, "missing": missing, "notes": notes, "seconds": round(time.time() - started, 1),
    }
    try:
        info["session_start"] = str(session.session_start_time)
    except Exception:
        pass
    (tmp / "info.json").write_text(json.dumps(info, indent=1))
    final = session_dir(year, rnd, name)
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    shutil.rmtree(ARCHIVE / "_tmp", ignore_errors=True)
    return EXIT_OK


def list_sessions():
    """Write archive/sessions.json: every wanted session from ARCHIVE_FIRST_SEASON to now (date check is done by the caller)."""
    import fastf1
    cache = ROOT / config.FASTF1_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    fastf1.Cache.enable_cache(str(cache))
    wanted_names = {n for names in config.ARCHIVE_PHASES for n in names}
    rows = []
    for year in range(config.ARCHIVE_FIRST_SEASON, datetime.now(timezone.utc).year + 1):
        sched = fastf1.get_event_schedule(year, include_testing=False)
        for ev in sched.itertuples():
            if int(ev.RoundNumber) < 1:
                continue
            for i in range(1, 6):
                name = getattr(ev, f"Session{i}")
                when = getattr(ev, f"Session{i}DateUtc")
                if name in wanted_names and not pd.isna(when):
                    rows.append({"year": year, "round": int(ev.RoundNumber), "event": ev.EventName, "session": name,
                                 "date": pd.Timestamp(when).tz_localize("UTC").isoformat()})
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    (ARCHIVE / "sessions.json").write_text(json.dumps(rows, indent=0))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-sessions", action="store_true")
    ap.add_argument("--year", type=int)
    ap.add_argument("--round", type=int)
    ap.add_argument("--session")
    a = ap.parse_args()
    if a.list_sessions:
        sys.exit(list_sessions())
    if a.year is None or a.round is None or not a.session:
        ap.error("--year, --round and --session are required")
    sys.exit(archive(a.year, a.round, a.session))


if __name__ == "__main__":
    main()
