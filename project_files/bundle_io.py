"""Load a replay bundle from disk and build the lookup structures the engine uses.

The engine only ever receives the object this module returns. A live source would
have to produce the same tables.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import config

ROOT = Path(__file__).resolve().parent
BUNDLE_DIR = ROOT / "data" / "bundle"
UPGRADES_DIR = ROOT / "data" / "upgrades"

TABLE_COLUMNS = {
    "laps": ["driver_number", "lap_number", "lap_start_t", "lap_end_t", "lap_duration", "d1", "d2", "d3",
             "s1_end_t", "s2_end_t", "s3_end_t", "is_pit_out_lap"],
    "minisectors": ["driver_number", "lap_number", "k", "end_t", "duration"],
    "position": ["t", "driver_number", "position"],
    "intervals": ["t", "driver_number", "interval_num", "interval_txt", "gap_num", "gap_txt"],
    "stints": ["driver_number", "stint_number", "lap_start", "lap_end", "compound", "tyre_age_at_start", "known_t"],
    "pit": ["t", "driver_number", "lap_number", "lane_duration", "stop_duration"],
    "race_control": ["t", "lap_number", "category", "flag", "scope", "sector", "driver_number", "message"],
    "weather": ["t", "air_temperature", "track_temperature", "humidity", "wind_speed", "wind_direction",
                "rainfall", "pressure"],
    "car_data": ["driver_number", "t", "speed", "drs", "throttle", "brake", "n_gear", "rpm"],
    "location": ["driver_number", "t", "x", "y"],
}


def _arr(df, name, dtype="float64"):
    return df[name].to_numpy(dtype=dtype, na_value=np.nan) if dtype == "float64" else df[name].to_numpy(dtype=dtype)


def _records(df):
    """Rows as dicts with None in place of missing values and plain Python numbers."""
    out = []
    cols = list(df.columns)
    for row in df.itertuples(index=False):
        d = {}
        for c, v in zip(cols, row):
            if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA or v is pd.NaT:
                d[c] = None
            elif isinstance(v, np.generic):
                d[c] = v.item()
            else:
                d[c] = v
        out.append(d)
    return out


def make_bundle(meta, tables, extra=None):
    """Fill missing tables with empty ones and build the indexes."""
    full = {}
    for name, cols in TABLE_COLUMNS.items():
        df = tables.get(name)
        full[name] = df if df is not None else pd.DataFrame({c: [] for c in cols})
    return prepare(meta, full, extra or {})


def prepare(meta, tables, extra):
    ix = {}
    drivers = [int(d["driver_number"]) for d in meta["drivers"]]
    ix["drivers"] = drivers
    ix["dpos"] = {n: i for i, n in enumerate(drivers)}

    laps = tables["laps"].sort_values(["driver_number", "lap_number"], kind="stable").reset_index(drop=True)
    cols = ["lap_number", "lap_start_t", "lap_end_t", "lap_duration", "d1", "d2", "d3", "s1_end_t", "s2_end_t", "s3_end_t"]
    glob = {c: laps[c].to_numpy(dtype="float64", na_value=np.nan) for c in cols}
    glob["pitout"] = laps["is_pit_out_lap"].to_numpy(dtype=bool) if len(laps) else np.zeros(0, dtype=bool)
    glob["drv"] = np.array([ix["dpos"].get(int(n), -1) for n in laps["driver_number"]], dtype=int)
    ix["lap_glob"] = glob
    per = {}
    dn = laps["driver_number"].to_numpy(dtype=int) if len(laps) else np.zeros(0, dtype=int)
    for n in drivers:
        m = dn == n
        d = {c: glob[c][m] for c in cols}
        d["pitout"] = glob["pitout"][m]
        per[n] = d
    ix["laps"] = per

    ms = tables["minisectors"]
    ix["ms_glob"] = {
        "drv": np.array([ix["dpos"].get(int(n), -1) for n in ms["driver_number"]], dtype=int),
        "k": ms["k"].to_numpy(dtype=int) if len(ms) else np.zeros(0, dtype=int),
        "end": ms["end_t"].to_numpy(dtype="float64", na_value=np.nan),
        "dur": ms["duration"].to_numpy(dtype="float64", na_value=np.nan),
    }
    ms_by = {}
    for (n, lap), g in ms.groupby(["driver_number", "lap_number"]):
        ms_by[(int(n), int(lap))] = (g["k"].to_numpy(dtype=int), g["end_t"].to_numpy(dtype="float64"),
                                     g["duration"].to_numpy(dtype="float64"))
    ix["ms"] = ms_by

    def by_driver(df, fn):
        out = {}
        for n in drivers:
            out[n] = fn(df[df["driver_number"] == n].sort_values("t", kind="stable"))
        return out

    ix["pos"] = by_driver(tables["position"], lambda g: (g["t"].to_numpy(dtype="float64"),
                                                          g["position"].to_numpy(dtype=int)))
    ix["int"] = by_driver(tables["intervals"], lambda g: (
        g["t"].to_numpy(dtype="float64"), g["interval_num"].to_numpy(dtype="float64", na_value=np.nan),
        list(g["interval_txt"].where(g["interval_txt"].notna(), None)),
        g["gap_num"].to_numpy(dtype="float64", na_value=np.nan), list(g["gap_txt"].where(g["gap_txt"].notna(), None))))
    ix["pit"] = by_driver(tables["pit"], lambda g: (
        g["t"].to_numpy(dtype="float64"), g["lane_duration"].to_numpy(dtype="float64", na_value=np.nan),
        [None if pd.isna(x) else int(x) for x in g["lap_number"]]))
    ix["pit_all"] = _records(tables["pit"].sort_values("t", kind="stable"))
    ix["car"] = by_driver(tables["car_data"], lambda g: (
        g["t"].to_numpy(dtype="float64"), g["drs"].to_numpy(dtype="float64", na_value=np.nan),
        g["speed"].to_numpy(dtype="float64", na_value=np.nan), g["throttle"].to_numpy(dtype="float64", na_value=np.nan),
        g["brake"].to_numpy(dtype="float64", na_value=np.nan), g["n_gear"].to_numpy(dtype="float64", na_value=np.nan),
        g["rpm"].to_numpy(dtype="float64", na_value=np.nan)))
    ix["loc"] = by_driver(tables["location"], lambda g: (
        g["t"].to_numpy(dtype="float64"), g["x"].to_numpy(dtype="float64"), g["y"].to_numpy(dtype="float64")))

    st = tables["stints"].sort_values(["driver_number", "stint_number"], kind="stable")
    stints = {n: [] for n in drivers}
    for r in _records(st):
        if r["driver_number"] in stints:
            stints[r["driver_number"]].append(r)
    ix["stints"] = stints

    rc = _records(tables["race_control"].sort_values("t", kind="stable"))
    ix["rc"] = rc
    ix["rc_t"] = np.array([r["t"] for r in rc], dtype="float64")

    w = tables["weather"].sort_values("t", kind="stable")
    ix["weather_t"] = w["t"].to_numpy(dtype="float64")
    ix["weather"] = _records(w)
    return {"meta": meta, "tables": tables, "extra": extra, "idx": ix}


def parse_race_id(race_id):
    """Return a dict describing a race id: of_<session_key>, ff_<year>_<RR>_<code> or jl_<year>_<RR>."""
    rid = str(race_id)
    if rid.isdigit():
        rid = f"of_{rid}"
    parts = rid.split("_")
    try:
        if parts[0] == "of" and len(parts) == 2 and parts[1].isdigit():
            return {"source": "openf1", "race_id": rid, "session_key": int(parts[1]), "folder": parts[1]}
        if parts[0] == "ff" and len(parts) == 4 and parts[1].isdigit() and parts[2].isdigit() and parts[3] in ("R", "S"):
            return {"source": "fastf1", "race_id": rid, "year": int(parts[1]), "round": int(parts[2]), "code": parts[3],
                    "folder": rid}
        if parts[0] == "jl" and len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            return {"source": "jolpica", "race_id": rid, "year": int(parts[1]), "round": int(parts[2]), "folder": rid}
    except ValueError:
        pass
    raise ValueError(f"not a race id: {race_id}")


def make_race_id(source, year=None, rnd=None, code=None, session_key=None):
    if source == "openf1":
        return f"of_{int(session_key)}"
    if source == "fastf1":
        return f"ff_{int(year)}_{int(rnd):02d}_{code}"
    return f"jl_{int(year)}_{int(rnd):02d}"


def bundle_folder(race_id):
    return BUNDLE_DIR / parse_race_id(race_id)["folder"]


def bundle_exists(race_id):
    return (bundle_folder(race_id) / "meta.json").exists()


def load_bundle(session_key, base=None):
    path = (Path(base) / parse_race_id(session_key)["folder"]) if base else bundle_folder(session_key)
    if not (path / "meta.json").exists():
        raise FileNotFoundError(f"no bundle in {path}")
    meta = json.loads((path / "meta.json").read_text())
    tables = {}
    for name in TABLE_COLUMNS:
        parts = sorted(path.glob(f"{name}_*.parquet")) or ([path / f"{name}.parquet"] if (path / f"{name}.parquet").exists() else [])
        frames = [pd.read_parquet(p) for p in parts]
        tables[name] = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame({c: [] for c in TABLE_COLUMNS[name]})
    extra = {
        "forecast": json.loads((path / "forecast.json").read_text()) if (path / "forecast.json").exists() else None,
        "session_result": json.loads((path / "session_result.json").read_text()) if (path / "session_result.json").exists() else [],
    }
    return make_bundle(meta, tables, extra)


def latest_session_key():
    """The default race's folder when it exists (data/bundle/9920), else the newest bundle folder."""
    default = parse_race_id(config.DEFAULT_RACE_ID)["folder"]
    if (BUNDLE_DIR / default / "meta.json").exists():
        return default
    keys = [p.name for p in BUNDLE_DIR.iterdir() if (p / "meta.json").exists()] if BUNDLE_DIR.exists() else []
    if not keys:
        raise FileNotFoundError("no bundle found, run build_bundle.py")
    return max(keys, key=lambda k: (BUNDLE_DIR / k / "meta.json").stat().st_mtime)


def load_upgrades(meeting_key):
    path = UPGRADES_DIR / f"{meeting_key}.json"
    if not path.exists():
        return {"source": None, "items": []}
    return json.loads(path.read_text())
