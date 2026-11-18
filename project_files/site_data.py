"""Website data format: bundles become classic .js files that hand their data to a global loader (F1DATA.put).

A browser blocks fetch, ES modules and .json reads on file://, so a plain script tag is the only way to read data from disk.
Column encoding: see encode_column."""
import base64
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import bundle_io
import config

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
RACES_DIR = SITE / "data" / "races"
LIBRARY_JS = SITE / "data" / "library.js"
CONSTANTS_JS = SITE / "js" / "constants.js"
MAX_FILE_BYTES = 45 * 1000 * 1000
MS_NULL = -2147483648
U8_NULL, U16_NULL, I32_NULL, STR_NULL = 255, 65535, 2147483647, 65535
TIME_SKIP = ()
TABLES_WITH_DRIVER = ("car_data", "location", "laps", "minisectors", "position", "intervals", "stints", "pit")
SAMPLE_TABLES = ("car_data", "location")


def frontend_constants():
    return {
        "REPLAY_SPEEDS": config.REPLAY_SPEEDS, "POLL_MS": config.POLL_MS, "N_MINISECTORS": config.N_MINISECTORS,
        "FEED_LENGTH": config.FEED_LENGTH, "FORECAST_HOURS": config.FORECAST_HOURS,
        "MAP_HOLD_S": config.MAP_HOLD_S, "PACE_MAX_DRIVERS": config.PACE_MAX_DRIVERS,
        "PACE_LOW_PERCENTILE": config.PACE_LOW_PERCENTILE, "PACE_BELOW_S": config.PACE_BELOW_S,
        "PACE_ABOVE_S": config.PACE_ABOVE_S, "UNDERCUT_GAP_S": config.UNDERCUT_GAP_S,
        "TRACE_POINTS": config.TRACE_POINTS, "DRS_STALE_S": config.DRS_STALE_S,
    }


def is_time_column(name):
    return name == "t" or name.endswith("_t") or name == "known_t"


def round_ms(values):
    """Round seconds to whole milliseconds (half to even), keeping NaN."""
    arr = np.asarray(values, dtype="float64")
    return np.rint(arr * 1000.0) / 1000.0


def prepare_for_site(bundle, downsample=False):
    """The bundle the website shows: every time column rounded to whole milliseconds.
    With downsample, every second car_data and location sample per driver is kept (size rule)."""
    tables = {}
    for name, df in bundle["tables"].items():
        df = df.copy()
        for col in df.columns:
            if is_time_column(col):
                df[col] = round_ms(df[col].to_numpy(dtype="float64", na_value=np.nan))
        if downsample and name in SAMPLE_TABLES and len(df):
            df = df.sort_values(["driver_number", "t"], kind="stable")
            keep = df.groupby("driver_number").cumcount() % 2 == 0
            df = df[keep]
        tables[name] = df.reset_index(drop=True)
    return bundle_io.make_bundle(bundle["meta"], tables, bundle["extra"])


def b64(arr):
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def encode_column(name, series):
    """Encode one column. Returns a dict with type, b64 (and dict for strings)."""
    s = pd.Series(series).reset_index(drop=True)
    n = len(s)
    if is_time_column(name):
        v = s.to_numpy(dtype="float64", na_value=np.nan)
        null = np.isnan(v)
        ms = np.zeros(n, dtype="<i4")
        ok = ~null
        scaled = np.rint(v[ok] * 1000.0)
        if len(scaled) and (np.abs(scaled).max() >= 2 ** 31 - 1):
            raise ValueError(f"time column {name} does not fit in 32 bits")
        ms[ok] = scaled.astype("int64").astype("<i4")
        ms[null] = MS_NULL
        return {"type": "ms", "b64": b64(ms)}
    if s.dtype == bool or (s.dtype == object and len(s.dropna()) and all(isinstance(x, (bool, np.bool_)) for x in s.dropna())):
        out = np.zeros(n, dtype="u1")
        for i, x in enumerate(s):
            out[i] = 2 if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA else (1 if x else 0)
        return {"type": "bool", "b64": b64(out)}
    if s.dtype == object or str(s.dtype).startswith("string") or s.dtype.name == "category":
        if n == 0:
            return {"type": "f64", "b64": ""}
        vals = [None if (x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA) else str(x) for x in s]
        distinct = sorted({x for x in vals if x is not None})
        if len(distinct) >= STR_NULL:
            raise ValueError(f"string column {name} has too many distinct values")
        index = {v: i for i, v in enumerate(distinct)}
        idx = np.array([STR_NULL if v is None else index[v] for v in vals], dtype="<u2")
        return {"type": "str", "dict": distinct, "b64": b64(idx)}
    v = s.to_numpy(dtype="float64", na_value=np.nan)
    if n == 0:
        return {"type": "f64", "b64": ""}
    nulls = np.isnan(v)
    present = v[~nulls]
    integral = bool(len(present) == 0 or (np.isfinite(present).all() and np.all(present == np.floor(present))))
    if integral:
        lo = float(present.min()) if len(present) else 0.0
        hi = float(present.max()) if len(present) else 0.0
        if lo >= 0 and hi <= U8_NULL - 1:
            out = np.full(n, U8_NULL, dtype="u1")
            out[~nulls] = present.astype("u1")
            return {"type": "u8", "b64": b64(out)}
        if lo >= 0 and hi <= U16_NULL - 1:
            out = np.full(n, U16_NULL, dtype="<u2")
            out[~nulls] = present.astype("<u2")
            return {"type": "u16", "b64": b64(out)}
        if lo >= -(2 ** 31) and hi <= I32_NULL - 1:
            out = np.full(n, I32_NULL, dtype="<i4")
            out[~nulls] = present.astype("<i4")
            return {"type": "i32", "b64": b64(out)}
    return {"type": "f64", "b64": b64(np.where(nulls, np.nan, v).astype("<f8"))}


# ---------------------------------------------------------------- packing (version 4.2)
# A packed column is the plain column plus a key "enc". Without "enc" the column is plain.
#   rle: b64 = value of each run (raw width), runs = base64 of little-endian uint32 run lengths
#   d16 / d8: b64 = int16 / int8 deltas, escape -32768 / -128, exc = base64 of the raw values at the escapes
RAW_DTYPE = {"ms": "<i4", "i32": "<i4", "u16": "<u2", "str": "<u2", "u8": "u1", "bool": "u1"}
RAW_NULL = {"ms": MS_NULL, "i32": I32_NULL, "u16": U16_NULL, "str": STR_NULL, "u8": U8_NULL, "bool": 2}
DELTA = {"d16": ("<i2", 32767, -32768), "d8": ("i1", 127, -128)}
PACK_ORDER = ("rle", "d16", "d8")


def _raw_array(col):
    return np.frombuffer(base64.b64decode(col["b64"]), dtype=RAW_DTYPE[col["type"]])


def _pack_rle(raw):
    change = np.flatnonzero(raw[1:] != raw[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(raw)]))
    return {"enc": "rle", "b64": b64(raw[starts]), "runs": b64((ends - starts).astype("<u4"))}


def _pack_delta(raw, kind, null):
    dtype, limit, escape = DELTA[kind]
    wide = raw.astype("int64")
    diff = np.zeros(len(raw), dtype="int64")
    diff[1:] = wide[1:] - wide[:-1]
    is_null = wide == null
    esc = np.zeros(len(raw), dtype=bool)
    esc[0] = True
    esc[1:] = is_null[1:] | is_null[:-1] | (np.abs(diff[1:]) > limit)
    deltas = np.where(esc, escape, diff).astype(dtype)
    return {"enc": kind, "b64": b64(deltas), "exc": b64(raw[esc])}


def pack_column(col):
    """Return the smallest of plain, rle, d16 and d8 for a plain column. f64 and empty columns stay plain."""
    kind = col["type"]
    if kind not in RAW_DTYPE or not col["b64"]:
        return col
    raw = _raw_array(col)
    candidates = [(len(col["b64"]), 0, col)]
    for rank, name in enumerate(PACK_ORDER, 1):
        packed = _pack_rle(raw) if name == "rle" else _pack_delta(raw, name, RAW_NULL[kind])
        size = sum(len(packed.get(k, "")) for k in ("b64", "runs", "exc"))
        candidates.append((size, rank, packed))
    best = min(candidates, key=lambda c: (c[0], c[1]))[2]
    if best is col:
        return col
    out = {"type": kind}
    if "dict" in col:
        out["dict"] = col["dict"]
    out.update(best)
    return out


def unpack_column(col):
    """The raw bytes of a column, plain or packed."""
    enc = col.get("enc")
    if enc is None:
        return base64.b64decode(col["b64"])
    dtype = np.dtype(RAW_DTYPE[col["type"]])
    if enc == "rle":
        values = np.frombuffer(base64.b64decode(col["b64"]), dtype=dtype)
        runs = np.frombuffer(base64.b64decode(col["runs"]), dtype="<u4")
        return np.repeat(values, runs).astype(dtype).tobytes()
    dtype_d, _, escape = DELTA[enc]
    deltas = np.frombuffer(base64.b64decode(col["b64"]), dtype=dtype_d)
    exc = np.frombuffer(base64.b64decode(col["exc"]), dtype=dtype).astype("int64")
    esc = deltas == escape
    steps = np.where(esc, 0, deltas.astype("int64"))
    total = np.cumsum(steps)
    group = np.cumsum(esc) - 1
    at_escape = np.flatnonzero(esc)
    values = exc[group] + total - total[at_escape[group]]
    return values.astype(dtype).tobytes()


def encode_table(df, columns=None, pack=True):
    cols = columns or list(df.columns)
    out = {c: encode_column(c, df[c]) for c in cols}
    if pack:
        out = {c: pack_column(v) for c, v in out.items()}
    return {"n": int(len(df)), "cols": out}


def js_put(race_id, table, obj, part=False):
    payload = dict(obj)
    if part:
        payload["part"] = True
    return f'F1DATA.put({json.dumps(race_id)}, {json.dumps(table)}, {json.dumps(payload, separators=(",", ":"), allow_nan=False)});\n'


def split_frame(df, parts):
    """Split a table by driver number into parts with about equal row counts."""
    if "driver_number" not in df.columns or parts <= 1:
        return [df]
    df = df.sort_values("driver_number", kind="stable")
    drivers = list(dict.fromkeys(df["driver_number"].tolist()))
    target = len(df) / parts
    groups, cur, size = [], [], 0
    counts = df["driver_number"].value_counts().to_dict()
    for d in drivers:
        if cur and size + counts[d] > target and len(groups) < parts - 1:
            groups.append(cur)
            cur, size = [], 0
        cur.append(d)
        size += counts[d]
    groups.append(cur)
    return [df[df["driver_number"].isin(g)] for g in groups]


def table_files(race_id, table, df, limit=MAX_FILE_BYTES):
    """List of (file name, text) for a table. A table over the size limit is split by driver number.
    The split is decided from the size of the plain encoding; every file is then packed on its own (version 4.2)."""
    plain = encode_table(df, pack=False)
    if len(js_put(race_id, table, plain).encode("utf-8")) <= limit:
        return [(f"{table}.js", js_put(race_id, table, {"n": plain["n"], "cols": {c: pack_column(v) for c, v in plain["cols"].items()}}))]
    parts = 2
    while True:
        frames = [p.reset_index(drop=True) for p in split_frame(df, parts)]
        sizes = [len(js_put(race_id, table, encode_table(f, list(df.columns), pack=False), part=True).encode("utf-8")) for f in frames]
        if all(x <= limit for x in sizes) or parts >= len(set(df["driver_number"])):
            return [(f"{table}.{i}.js", js_put(race_id, table, encode_table(f, list(df.columns)), part=True)) for i, f in enumerate(frames, 1)]
        parts += 1


def race_id_of(meta):
    return meta.get("race_id") or f"of_{meta['session']['session_key']}"


def race_files(bundle, race_id, downsample=False, limit=MAX_FILE_BYTES):
    """All files of one race: {file name: text}. meta.js lists the table files."""
    prepared = prepare_for_site(bundle, downsample)
    files, listing = {}, {}
    for name in bundle_io.TABLE_COLUMNS:
        parts = table_files(race_id, name, prepared["tables"][name], limit)
        listing[name] = [f for f, _ in parts]
        files.update(dict(parts))
    meta = bundle["meta"]
    extra = bundle["extra"]
    body = {"meta": meta, "forecast": extra.get("forecast"), "session_result": extra.get("session_result", []),
            "upgrades": bundle_io.load_upgrades(meta["session"].get("meeting_key")),
            "constants": frontend_constants(), "files": listing, "downsampled": bool(downsample)}
    files["meta.js"] = js_put(race_id, "meta", body)
    return files


def folder_bytes(path):
    return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file()) if Path(path).exists() else 0


# ---------------------------------------------------------------- library

def race_ids_for(entry, name):
    out = []
    code = "R" if name == "Race" else "S"
    if entry["season"] >= config.ARCHIVE_FIRST_SEASON:
        out.append(bundle_io.make_race_id("fastf1", entry["season"], entry["round"], code))
    if name == "Race" and entry.get("openf1_session_key"):
        out.append(bundle_io.make_race_id("openf1", session_key=entry["openf1_session_key"]))
    lap = bundle_io.make_race_id("jolpica", entry["season"], entry["round"]) if name == "Race" else None
    return out, lap


def library_payload(index, races_dir=None, today=None, ready=None):
    """ready: optional set of race ids to treat as present (for a copy with only some races)."""
    races_dir = Path(races_dir or RACES_DIR)
    today = today or date.today().isoformat()
    seasons = {}
    for season in sorted(index.get("seasons", {}), key=int, reverse=True):
        rows = []
        for r in index["seasons"][season]:
            sessions = []
            for name in ["Race"] + (["Sprint"] if r.get("sprint_date") else []):
                full_ids, lap_id = race_ids_for(r, name)
                present = (lambda i: i in ready) if ready is not None else (lambda i: (races_dir / i / "meta.js").exists())
                full = [i for i in full_ids if present(i)]
                lap = bool(lap_id and present(lap_id))
                sdate = r["date"] if name == "Race" else r.get("sprint_date")
                state, load_id = "none", None
                if full:
                    state, load_id = "full", full[0]
                elif lap:
                    state, load_id = "lap_only", lap_id
                elif sdate and sdate > today:
                    state = "not_held"
                sessions.append({"name": name, "date": sdate, "state": state, "load_id": load_id,
                                 "has_lap_only": bool(full and lap),
                                 "bytes": folder_bytes(races_dir / load_id) if load_id else 0})
            rows.append({"season": r["season"], "round": r["round"], "name": r["name"], "circuit": r["circuit"],
                         "locality": r.get("locality"), "country": r.get("country"), "date": r["date"], "sessions": sessions})
        seasons[season] = rows
    return {"built": today, "seasons": seasons}


def write_library(index, races_dir=None, out=None, today=None):
    payload = library_payload(index, races_dir, today)
    out = Path(out or LIBRARY_JS)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(js_put("_library", "index", payload), encoding="utf-8")
    return payload


def write_constants(out=None):
    """site/js/constants.js: the constants of config.py, generated, never typed by hand."""
    values = {}
    for name in dir(config):
        if name.isupper():
            v = getattr(config, name)
            if isinstance(v, str) and "://" in v:
                continue
            if isinstance(v, (int, float, str, bool, list, dict, tuple)):
                values[name] = v
    out = Path(out or CONSTANTS_JS)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "window.F1APP = window.F1APP || {};\nF1APP.CONFIG = " + json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False) + ";\n"
    out.write_text(text, encoding="utf-8")
    return out
