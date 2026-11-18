"""Helpers shared by every bundle builder (OpenF1, FastF1, Jolpica)."""
import re
from pathlib import Path

import numpy as np
import pandas as pd

import config

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "RUN_LOG.md"
NO_SAMPLE_GAP_S = config.MINISECTOR_MAX_GAP_S


def write_log_section(title, body):
    text = LOG_PATH.read_text() if LOG_PATH.exists() else "# RUN_LOG\n"
    marker = f"## {title}"
    if marker in text:
        head, rest = text.split(marker, 1)
        nxt = re.search(r"\n## ", rest)
        tail = rest[nxt.start():] if nxt else ""
        text = head.rstrip("\n") + "\n" + tail.lstrip("\n")
    text = text.rstrip("\n") + f"\n\n{marker}\n\n{body.rstrip()}\n"
    LOG_PATH.write_text(text)


def compute_lap_table(df):
    """Shared null-handling for lap times.

    df columns: driver_number, lap_number, raw_start, raw_end, dur, d1, d2, d3, raw_s1_end, raw_s2_end, pit_out
    (all times in seconds on the bundle time base, NaN when missing).
    Rules: a null start becomes the end of the previous lap. A null end becomes start plus duration, else the next
    lap's start. Sector end times are start plus sector durations unless the source gives them. A null sector
    duration makes that sector's end and the ones after it null, except the third which is the lap end."""
    df = df.sort_values(["driver_number", "lap_number"]).reset_index(drop=True)
    out = []
    for _, g in df.groupby("driver_number", sort=True):
        g = g.reset_index(drop=True)
        n = len(g)
        raw_start = g["raw_start"].to_numpy(dtype=float)
        raw_end = g["raw_end"].to_numpy(dtype=float)
        dur = g["dur"].to_numpy(dtype=float)
        start = np.full(n, np.nan)
        end = np.full(n, np.nan)
        for i in range(n):
            s = raw_start[i]
            if np.isnan(s) and i > 0:
                s = end[i - 1]
            start[i] = s
            if not np.isnan(raw_end[i]):
                end[i] = raw_end[i]
            elif not np.isnan(s) and not np.isnan(dur[i]):
                end[i] = s + dur[i]
            elif i + 1 < n and not np.isnan(raw_start[i + 1]):
                end[i] = raw_start[i + 1]
        for i in range(n):
            if np.isnan(start[i]) and i > 0 and not np.isnan(end[i - 1]):
                start[i] = end[i - 1]
        d1, d2, d3 = (g[c].to_numpy(dtype=float) for c in ("d1", "d2", "d3"))
        r1, r2 = g["raw_s1_end"].to_numpy(dtype=float), g["raw_s2_end"].to_numpy(dtype=float)
        for i in range(n):
            s1 = r1[i] if not np.isnan(r1[i]) else start[i] + d1[i]
            s2 = r2[i] if not np.isnan(r2[i]) else s1 + d2[i]
            if np.isnan(d1[i]):
                s1 = r1[i] if not np.isnan(r1[i]) else np.nan
            out.append({
                "driver_number": int(g.loc[i, "driver_number"]), "lap_number": int(g.loc[i, "lap_number"]),
                "lap_start_t": start[i], "lap_end_t": end[i], "lap_duration": dur[i],
                "d1": d1[i], "d2": d2[i], "d3": d3[i], "s1_end_t": s1, "s2_end_t": s2, "s3_end_t": end[i],
                "is_pit_out_lap": bool(g.loc[i, "pit_out"]),
            })
    return pd.DataFrame(out, columns=["driver_number", "lap_number", "lap_start_t", "lap_end_t", "lap_duration",
                                      "d1", "d2", "d3", "s1_end_t", "s2_end_t", "s3_end_t", "is_pit_out_lap"])


def max_gap(ts, start, end):
    pts = np.concatenate([[start], ts[(ts >= start) & (ts <= end)], [end]])
    return float(np.max(np.diff(pts)))


def minisectors_for_lap(t, cum, start, end, l_ref, n_ms):
    """Return a list of (end_t, duration) or None per mini-sector."""
    result = [None] * n_ms
    mask = (t >= start) & (t <= end)
    if mask.sum() < 2:
        return result
    ts = t[mask]
    grid = np.concatenate([[start], ts, [end]])
    d = np.interp(grid, t, cum) - np.interp(start, t, cum)
    d = np.maximum.accumulate(d)
    gaps = np.diff(grid)
    bad = np.where(gaps > NO_SAMPLE_GAP_S)[0]
    cutoff = grid[bad[0]] if len(bad) else np.inf
    ends = [None] * n_ms
    for k in range(n_ms - 1):
        b = (k + 1) * l_ref / n_ms
        j = int(np.searchsorted(d, b, side="left"))
        if j >= len(d) or j == 0:
            break
        span = d[j] - d[j - 1]
        tt = grid[j - 1] + ((b - d[j - 1]) / span * (grid[j] - grid[j - 1]) if span > 0 else 0.0)
        if tt > cutoff:
            break
        ends[k] = float(tt)
    if all(e is not None for e in ends[:n_ms - 1]):
        ends[n_ms - 1] = float(end)
    prev = float(start)
    for k in range(n_ms):
        if ends[k] is None:
            break
        result[k] = (ends[k], ends[k] - prev)
        prev = ends[k]
    return result


def speed_integrals(cars):
    """Per driver (t, cumulative distance in metres) from car_data with t and speed in km/h."""
    per_car = {}
    for n, g in cars.groupby("driver_number"):
        g = g.sort_values("t")
        t = g["t"].to_numpy(dtype=float)
        v = np.nan_to_num(g["speed"].to_numpy(dtype=float)) / 3.6
        if len(t) < 2:
            continue
        cum = np.concatenate([[0.0], np.cumsum((v[1:] + v[:-1]) / 2 * np.diff(t))])
        per_car[int(n)] = (t, cum)
    return per_car


def find_reference_lap(laps, per_car, per_loc):
    """Fastest lap with car_data and location samples covering it."""
    cand = laps.dropna(subset=["lap_duration", "lap_start_t", "lap_end_t"]).sort_values("lap_duration")
    for r in cand.itertuples():
        n = int(r.driver_number)
        if n not in per_car or n not in per_loc:
            continue
        s, e = r.lap_start_t, r.lap_end_t
        if max_gap(per_car[n][0], s, e) <= NO_SAMPLE_GAP_S and max_gap(per_loc[n], s, e) <= NO_SAMPLE_GAP_S:
            t, cum = per_car[n]
            return {"driver_number": n, "lap_number": int(r.lap_number), "start": float(s), "end": float(e),
                    "L_ref": float(np.interp(e, t, cum) - np.interp(s, t, cum))}
    return None


def compute_minisectors(laps, per_car, l_ref):
    rows, complete, eligible = [], 0, 0
    for r in laps.itertuples():
        n = int(r.driver_number)
        if n not in per_car or np.isnan(r.lap_start_t) or np.isnan(r.lap_end_t):
            continue
        eligible += 1
        t, cum = per_car[n]
        res = minisectors_for_lap(t, cum, r.lap_start_t, r.lap_end_t, l_ref, config.N_MINISECTORS)
        if all(x is not None for x in res):
            complete += 1
        for k, x in enumerate(res):
            if x is not None:
                rows.append((n, int(r.lap_number), k, x[0], x[1]))
    return pd.DataFrame(rows, columns=["driver_number", "lap_number", "k", "end_t", "duration"]), complete, eligible


def reference_outline(locs, ref):
    lg = locs[(locs.driver_number == ref["driver_number"]) & (locs.t >= ref["start"]) & (locs.t <= ref["end"])].sort_values("t")
    return [[int(x), int(y)] for x, y in zip(lg.x, lg.y)]


def write_sample_tables(out, name, df):
    """Write car_data or location in files small enough for the 20 MB rule."""
    parts = max(1, int(np.ceil(len(df) / config.ROWS_PER_SAMPLE_FILE)))
    for i, chunk in enumerate(np.array_split(np.arange(len(df)), parts)):
        df.iloc[chunk].to_parquet(out / f"{name}_{i}.parquet", index=False)


CAR_COLUMNS = ["driver_number", "t", "speed", "drs", "throttle", "brake", "n_gear", "rpm"]


def finish_car_data(df):
    df = df.copy()
    for c in CAR_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    df = df[CAR_COLUMNS].astype({"driver_number": "int16"})
    for c in ("speed", "drs", "throttle", "brake", "n_gear", "rpm"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    return df


def finish_location(df):
    df = df.astype({"driver_number": "int16"})
    return df.astype({"x": "float32", "y": "float32"})


def seek_index(laps):
    s = laps.dropna(subset=["lap_start_t"]).groupby("lap_number")["lap_start_t"].min()
    return {str(int(k)): float(v) for k, v in s.items()}


def generated_colour(text):
    """A stable colour (hex without #) from a text: hash to a hue, saturation 55 percent, lightness 55 percent."""
    import colorsys
    import hashlib
    hue = int(hashlib.md5(str(text).encode()).hexdigest()[:8], 16) % 360 / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.55, 0.55)
    return "%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def race_start_from_laps(laps, numbers):
    """Smallest non-null lap 1 start, else lap 2 start minus lap 1 duration, else 0. Returns (value, rule)."""
    lap1 = laps[laps.lap_number == 1]["lap_start_t"].dropna()
    if len(lap1):
        return float(lap1.min()), "smallest non-null lap 1 lap_start_t"
    cands = []
    for n in numbers:
        g = laps[laps.driver_number == n].set_index("lap_number")
        if 1 in g.index and 2 in g.index and not np.isnan(g.loc[2, "lap_start_t"]) and not np.isnan(g.loc[1, "lap_duration"]):
            cands.append(g.loc[2, "lap_start_t"] - g.loc[1, "lap_duration"])
    if cands:
        return float(min(cands)), "lap 2 start minus lap 1 duration"
    return 0.0, "fallback 0"
