"""The replay state engine.

Pure Python on top of a bundle. No HTTP, no web framework, no global state.
state_at(bundle, t) returns everything the dashboard shows at replay time t and uses
only data stamped at or before t.
"""
import math
import re
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import config
import points
from bundle_io import make_bundle

NAN = float("nan")

TRACK_FLAG_STATES = {"GREEN": "GREEN", "CLEAR": "GREEN", "YELLOW": "YELLOW", "DOUBLE YELLOW": "DOUBLE YELLOW",
                     "RED": "RED", "CHEQUERED": "CHEQUERED"}
SECTOR_YELLOWS = ("YELLOW", "DOUBLE YELLOW")
SECTOR_CLEARS = ("CLEAR", "GREEN")

RE_TRACK_LIMITS = re.compile(r"^CAR (\d+) \([A-Z]{3}\) TIME \d+:\d+\.\d+ DELETED - TRACK LIMITS")
RE_TIME_PENALTY = re.compile(r"^FIA STEWARDS: (\d+) SECOND TIME PENALTY FOR CAR (\d+) \([A-Z]{3}\)(?: - (.*))?$")
RE_INCIDENT = re.compile(
    r"^(CORRECTION:: )?(?:FIA STEWARDS: )?(?:TURN (\d+) )?(?:PIT ENTRY )?INCIDENT INVOLVING CARS? (.+?) "
    r"(NOTED|UNDER INVESTIGATION|WILL BE INVESTIGATED AFTER THE RACE|REVIEWED NO FURTHER INVESTIGATION)(?: - (.*))?$")
RE_CAR = re.compile(r"(\d+) \([A-Z]{3}\)")


def _clean(obj):
    """Make a value JSON friendly: plain types, NaN and Infinity become None."""
    if isinstance(obj, dict):
        return {str(k) if not isinstance(k, str) else k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        v = float(obj)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _last_le(arr, t):
    """Index of the last element of a sorted array that is <= t, or -1."""
    return int(np.searchsorted(arr, t, side="right")) - 1


# ---------------------------------------------------------------- race control

def tag_for(row):
    msg = row.get("message") or ""
    if RE_TRACK_LIMITS.match(msg):
        return "TRACK LIMITS"
    if RE_TIME_PENALTY.match(msg):
        return "PENALTY"
    if RE_INCIDENT.match(msg):
        return "INVESTIGATION"
    cat = row.get("category")
    if cat == "Flag":
        return row.get("flag") or "FLAG"
    if cat == "SafetyCar":
        return "SC"
    if cat == "Drs":
        return "DRS"
    if cat == "SessionStatus":
        return "SESSION"
    return "INFO"


def parse_race_control(rows):
    """Per-driver penalty and investigation facts from the messages known so far."""
    pen_s, tl_count = Counter(), Counter()
    open_inc = []
    for r in rows:
        msg = r.get("message") or ""
        m = RE_TRACK_LIMITS.match(msg)
        if m:
            tl_count[int(m.group(1))] += 1
            continue
        m = RE_TIME_PENALTY.match(msg)
        if m:
            secs, car, reason = int(m.group(1)), int(m.group(2)), m.group(3) or ""
            pen_s[car] += secs
            for i in range(len(open_inc) - 1, -1, -1):
                inc = open_inc[i]
                if car in inc["cars"] and (inc["reason"] == reason or inc["reason"] == ""):
                    del open_inc[i]
                    break
            continue
        m = RE_INCIDENT.match(msg)
        if m:
            correction, turn, cars_text, decision, reason = m.groups()
            cars = frozenset(int(c) for c in RE_CAR.findall(cars_text))
            reason = reason or ""
            if decision == "REVIEWED NO FURTHER INVESTIGATION":
                open_inc = [i for i in open_inc if not (i["cars"] == cars and (i["turn"] == turn or turn is None or i["turn"] is None))]
                continue
            if correction and turn:
                open_inc = [i for i in open_inc if not (i["turn"] == turn and i["reason"] == reason)]
            same = [i for i in open_inc if i["cars"] == cars and i["turn"] == turn]
            if same:
                if reason:
                    same[0]["reason"] = reason
            else:
                open_inc.append({"cars": cars, "turn": turn, "reason": reason})
    inv = Counter()
    for inc in open_inc:
        for c in inc["cars"]:
            inv[c] += 1
    return {"penalty_s": pen_s, "track_limits": tl_count, "investigations": inv}


def unparsed_patterns(rows):
    """Message patterns no parsing or track state rule handles (numbers and codes replaced)."""
    out = Counter()
    for r in rows:
        msg = r.get("message") or ""
        if RE_TRACK_LIMITS.match(msg) or RE_TIME_PENALTY.match(msg) or RE_INCIDENT.match(msg):
            continue
        if r.get("category") in ("Flag", "SafetyCar", "Drs") and r.get("scope") != "Driver" and tag_for(r) != "INFO":
            if r.get("category") != "SafetyCar" or "SAFETY CAR" in msg:
                continue
        pat = re.sub(r"\(([A-Z]{3})\)", "(<DRV>)", msg)
        pat = re.sub(r"\d+(?::\d+)*(?:\.\d+)?", "<N>", pat)
        out[pat] += 1
    return out


def apply_track_message(state, sectors, drs, row):
    """Advance the track state machine by one race control row."""
    cat, flag, scope = row.get("category"), row.get("flag"), row.get("scope")
    msg = row.get("message") or ""
    if cat == "Flag" and scope == "Track":
        new = TRACK_FLAG_STATES.get(flag)
        if new and state != "CHEQUERED":
            state = new
            if new == "GREEN":
                sectors = {}
    elif cat == "Flag" and scope == "Sector" and row.get("sector") is not None:
        sec = int(row["sector"])
        sectors = dict(sectors)
        if flag in SECTOR_YELLOWS:
            sectors[sec] = flag
        elif flag in SECTOR_CLEARS:
            sectors.pop(sec, None)
    elif cat == "SafetyCar" and state != "CHEQUERED":
        if "VIRTUAL SAFETY CAR DEPLOYED" in msg:
            state = "VSC"
        elif "SAFETY CAR DEPLOYED" in msg:
            state = "SC"
        elif "VIRTUAL SAFETY CAR ENDING" in msg and state == "VSC":
            state = "GREEN"
        elif "SAFETY CAR IN THIS LAP" in msg and state == "SC":
            state = "GREEN"
    elif cat == "Drs":
        if "DRS ENABLED" in msg:
            drs = True
        elif "DRS DISABLED" in msg:
            drs = False
    return state, sectors, drs


def build_timeline(rows):
    """List of (t, state, sectors dict, drs_enabled) after each message."""
    state, sectors, drs = "GREEN", {}, None
    out = []
    for r in rows:
        state, sectors, drs = apply_track_message(state, sectors, drs, r)
        out.append((r["t"], state, sectors, drs))
    return out


def timeline_at(tl, t):
    """State tuple (state, sectors, drs) at time t, and the time that state began (None if initial)."""
    times = [e[0] for e in tl]
    i = bisect_right(times, t) - 1
    if i < 0:
        return "GREEN", {}, None, None
    since = tl[i][0]
    j = i
    while j > 0 and tl[j - 1][1] == tl[i][1]:
        j -= 1
        since = tl[j][0]
    return tl[i][1], tl[i][2], tl[i][3], since


def green_throughout(tl, a, b):
    """True when the track was GREEN with no yellow sector at every moment of [a, b]."""
    times = [e[0] for e in tl]
    i = bisect_right(times, a) - 1
    if i >= 0 and not (tl[i][1] == "GREEN" and not tl[i][2]):
        return False
    j = i + 1
    while j < len(tl) and tl[j][0] <= b:
        if not (tl[j][1] == "GREEN" and not tl[j][2]):
            return False
        j += 1
    return True


# ---------------------------------------------------------------- small rules

def drs_label(code, age):
    """DRS text from a car_data code and the age of the sample in seconds."""
    if code is None or age is None or age > config.DRS_STALE_S or (isinstance(code, float) and math.isnan(code)):
        return None
    code = int(code)
    if code in config.DRS_ON:
        return "ON"
    if code in config.DRS_ELIGIBLE:
        return "ELIG"
    return "OFF"


def sector_colour(value, own_min, overall_min):
    """purple if the value ties the overall best, green if the driver's own best, else yellow."""
    if value is None:
        return None
    if overall_min is not None and value <= overall_min:
        return "purple"
    if own_min is not None and value <= own_min:
        return "green"
    return "yellow"


def race_points(position, season=None, session_name="Race"):
    """Points for a finishing position. Without a season the current Race table (config.RACE_POINTS) is used."""
    if season is None:
        if position is not None and 1 <= position <= len(config.RACE_POINTS):
            return config.RACE_POINTS[position - 1]
        return 0
    return points.points_for_position(season, session_name, position)


def latest_sample(ts, xs, ys, t):
    """Latest location sample at or before t: (x, y, age) or None."""
    i = _last_le(ts, t)
    if i < 0:
        return None
    return float(xs[i]), float(ys[i]), float(t - ts[i])


def _median(values):
    return float(np.median(values)) if len(values) else None


def lap_is_clean(L, i, t, tl, pit_laps):
    """A lap with known duration, not a pit lap, run entirely under a green track."""
    end, dur = L["lap_end_t"][i], L["lap_duration"][i]
    if not (end <= t) or np.isnan(dur) or L["pitout"][i]:
        return False
    if int(L["lap_number"][i]) in pit_laps:
        return False
    return green_throughout(tl, L["lap_start_t"][i], end)


def _pit_laps(ix, n, t, tl_started):
    """Laps that contain a pit stop for driver n, known at t: the in-lap and the out-lap."""
    laps = set()
    pt, _, plap = ix["pit"][n]
    k = int(np.searchsorted(pt, t, side="right"))
    for lap in plap[:k]:
        if lap is not None:
            laps.add(lap)
            laps.add(lap + 1)
    L = ix["laps"][n]
    for i in range(tl_started):
        if L["pitout"][i]:
            laps.add(int(L["lap_number"][i]) - 1)
            laps.add(int(L["lap_number"][i]))
    return laps


def estimate_pit_loss(ix, t, tl):
    """Median pit loss over the stops that qualify, and how many stops were used."""
    losses = []
    for n in ix["drivers"]:
        L = ix["laps"][n]
        if not len(L["lap_number"]):
            continue
        started = int(np.searchsorted(L["lap_start_t"], t, side="right"))
        pit_laps = _pit_laps(ix, n, t, started)
        pt, _, plap = ix["pit"][n]
        k = int(np.searchsorted(pt, t, side="right"))
        lap_idx = {int(x): i for i, x in enumerate(L["lap_number"])}
        for lap in plap[:k]:
            if lap is None or lap not in lap_idx or (lap + 1) not in lap_idx:
                continue
            a, b = lap_idx[lap], lap_idx[lap + 1]
            if not (L["lap_end_t"][a] <= t and L["lap_end_t"][b] <= t):
                continue
            if np.isnan(L["lap_duration"][a]) or np.isnan(L["lap_duration"][b]):
                continue
            if not green_throughout(tl, L["lap_start_t"][a], L["lap_end_t"][b]):
                continue
            ref = []
            for back in range(1, config.PIT_LOSS_CLEAN_LAPS + 1):
                j = lap_idx.get(lap - back)
                if j is not None and lap_is_clean(L, j, t, tl, pit_laps):
                    ref.append(float(L["lap_duration"][j]))
            if len(ref) < 2:
                continue
            losses.append(float(L["lap_duration"][a] + L["lap_duration"][b] - 2 * np.median(ref)))
    if len(losses) < config.PIT_LOSS_MIN_STOPS:
        return None, len(losses)
    return float(np.median(losses)), len(losses)


def rejoin_projection(rows, pit_loss):
    """For each RUNNING driver: where would they rejoin after a stop now. rows have status and gap_num."""
    out = {}
    running = [r for r in rows if r["status"] == "RUNNING" and r["gap_num"] is not None]
    for r in rows:
        n = r["driver_number"]
        if pit_loss is None or r["status"] != "RUNNING" or r["gap_num"] is None:
            out[n] = None
            continue
        after = r["gap_num"] + pit_loss
        ahead = [o for o in running if o["driver_number"] != n and o["gap_num"] < after]
        car = max(ahead, key=lambda o: o["gap_num"]) if ahead else None
        out[n] = {"position": 1 + len(ahead), "car_ahead": car["acronym"] if car else None,
                  "gap_to_car_ahead": (after - car["gap_num"]) if car else None}
    return out


def project_standings(entries, race_pts, season, fastest_driver, positions, outs):
    """entries: {key: {"points": start points or None, "rank": start rank or None, ...}}.

    Returns a list sorted by projected points. Ties keep the start order.
    """
    rows = []
    for key, e in entries.items():
        gained = race_pts.get(key, 0)
        start = e["points"]
        rows.append({**e, "key": key, "race_points": gained,
                     "projected": (start + gained) if start is not None else None})
    rows.sort(key=lambda r: (r["projected"] is None, -(r["projected"] or 0), r["rank"] if r["rank"] is not None else 10 ** 6))
    for i, r in enumerate(rows):
        r["new_rank"] = i + 1 if r["projected"] is not None else None
        r["rank_change"] = (r["rank"] - r["new_rank"]) if r["rank"] is not None and r["new_rank"] is not None else None
    return rows


# ---------------------------------------------------------------- state

def _start_ranks(points_by_key, position_by_key, order):
    keyed = [k for k in order if points_by_key.get(k) is not None]
    keyed.sort(key=lambda k: (-points_by_key[k], position_by_key.get(k) if position_by_key.get(k) is not None else 10 ** 6, order.index(k)))
    return {k: i + 1 for i, k in enumerate(keyed)}


def state_at(bundle, t):
    meta, ix = bundle["meta"], bundle["idx"]
    t = float(t)
    drivers = ix["drivers"]
    dinfo = {int(d["driver_number"]): d for d in meta["drivers"]}
    total_laps = meta["total_laps"]
    n_ms = config.N_MINISECTORS

    rc_k = int(np.searchsorted(ix["rc_t"], t, side="right"))
    rc_rows = ix["rc"][:rc_k]
    parsed = parse_race_control(rc_rows)
    tl = build_timeline(rc_rows)
    track_state, sectors, drs_enabled, red_since = timeline_at(tl, t)
    cheq_t = next((r["t"] for r in rc_rows if r.get("category") == "Flag" and r.get("flag") == "CHEQUERED"
                   and r.get("scope") == "Track"), None)

    # global minima for colours
    G = ix["lap_glob"]
    sec_overall, sec_own = {}, {}
    for s in (1, 2, 3):
        se, d = G[f"s{s}_end_t"], G[f"d{s}"]
        ok = (se <= t) & ~np.isnan(d) & (G["drv"] >= 0)
        own = np.full(len(drivers), np.inf)
        if ok.any():
            np.minimum.at(own, G["drv"][ok], d[ok])
        sec_own[s] = own
        sec_overall[s] = own.min() if len(own) else np.inf
    MG = ix["ms_glob"]
    mok = (MG["end"] <= t) & (MG["drv"] >= 0)
    ms_own = np.full((len(drivers), n_ms), np.inf)
    if mok.any():
        np.minimum.at(ms_own, (MG["drv"][mok], MG["k"][mok]), MG["dur"][mok])
    ms_overall = ms_own.min(axis=0)

    info = {}
    for di, n in enumerate(drivers):
        L = ix["laps"][n]
        d = {"n": n}
        starts = L["lap_start_t"]
        cur_i = _last_le(starts, t) if len(starts) else -1
        d["cur_i"] = cur_i
        d["cur_lap"] = int(L["lap_number"][cur_i]) if cur_i >= 0 else 0
        d["cur_start"] = float(starts[cur_i]) if cur_i >= 0 else None
        completed = L["lap_end_t"] <= t if len(starts) else np.zeros(0, dtype=bool)
        d["completed_mask"] = completed
        d["completed"] = int(completed.sum())
        known = completed & ~np.isnan(L["lap_duration"]) if len(starts) else completed
        d["known_mask"] = known
        ki = np.nonzero(known)[0]
        if len(ki):
            last = ki[-1]
            d["last_lap"] = float(L["lap_duration"][last])
            d["last_lap_number"] = int(L["lap_number"][last])
            prev = None
            if last > 0 and known[last - 1] and L["lap_number"][last - 1] == L["lap_number"][last] - 1:
                prev = float(L["lap_duration"][last - 1])
            d["delta_prev"] = d["last_lap"] - prev if prev is not None else None
            d["best"] = float(L["lap_duration"][ki].min())
        else:
            d["last_lap"] = d["delta_prev"] = d["best"] = None
            d["last_lap_number"] = None
        sec_vals = []
        for s in (1, 2, 3):
            ok = (L[f"s{s}_end_t"] <= t) & ~np.isnan(L[f"d{s}"]) if len(starts) else np.zeros(0, dtype=bool)
            idxs = np.nonzero(ok)[0]
            sec_vals.append(float(L[f"d{s}"][idxs[-1]]) if len(idxs) else None)
        d["sec_vals"] = sec_vals
        # position
        pt, pp = ix["pos"][n]
        pi = _last_le(pt, t)
        d["position"] = int(pp[pi]) if pi >= 0 else None
        # pit
        ptt, plane, plap = ix["pit"][n]
        d["pit_stops"] = int(np.searchsorted(ptt, t, side="right"))
        in_pit = False
        if cur_i >= 0 and L["pitout"][cur_i] and L["lap_number"][cur_i] >= 2:
            s0 = starts[cur_i]
            in_pit = not bool(((ptt >= s0) & (ptt <= t)).any())
        d["in_pit"] = in_pit
        # stints
        d["stints"] = [s for s in ix["stints"][n] if s["known_t"] is not None and s["known_t"] <= t]
        info[n] = d

    # status helpers
    def started_since(j, a, b):
        sj = ix["laps"][j]["lap_start_t"]
        return int(np.searchsorted(sj, b, side="right") - np.searchsorted(sj, a, side="right"))

    def is_out(n, d):
        te = t if track_state != "RED" else red_since
        cs = d["cur_start"]
        if cs is None or te is None or cs > te or te - cs <= config.OUT_TIMEOUT_S:
            return False
        others = sum(1 for j in drivers if j != n and started_since(j, cs, te) >= 2)
        return others >= config.OUT_MIN_OTHERS

    for n, d in info.items():
        L = ix["laps"][n]
        fin = False
        if cheq_t is not None and len(L["lap_end_t"]):
            fin = bool(((L["lap_end_t"] >= cheq_t) & (L["lap_end_t"] <= t)).any())
        if fin:
            d["status"] = "FINISHED"
        elif d["in_pit"]:
            d["status"] = "PIT"
        elif is_out(n, d):
            d["status"] = "OUT"
        else:
            d["status"] = "RUNNING"

    by_pos = sorted((d for d in info.values() if d["position"] is not None), key=lambda d: (d["position"], d["n"]))
    leader = by_pos[0] if by_pos and by_pos[0]["position"] == 1 else None
    pos_to_driver = {}
    for d in by_pos:
        pos_to_driver.setdefault(d["position"], d["n"])

    fastest_n, fastest_t = None, None
    for n in drivers:
        b = info[n]["best"]
        if b is not None and (fastest_t is None or b < fastest_t):
            fastest_n, fastest_t = n, b

    rows = []
    for n in drivers:
        d, meta_d, L = info[n], dinfo[n], ix["laps"][n]
        grid = meta_d.get("grid_position")
        pos = d["position"]
        # tyre
        tyre = None
        if d["stints"]:
            s = d["stints"][-1]
            start_age = s["tyre_age_at_start"]
            unknown = start_age is None
            n_laps = None
            if s["lap_start"] is not None:
                n_laps = int(((L["lap_number"] >= s["lap_start"]) & d["completed_mask"]).sum()) if len(L["lap_number"]) else 0
            tyre = {"compound": s["compound"],
                    "age": (0 if unknown else int(start_age)) + n_laps if n_laps is not None else None,
                    "age_unknown_start": unknown}
        # interval and gap
        it, inum, itxt, gnum, gtxt = ix["int"][n]
        k = _last_le(it, t)
        interval = gap = None
        gap_num = None
        if k >= 0:
            interval = _num(inum[k]) if _num(inum[k]) is not None else itxt[k]
            gap = _num(gnum[k]) if _num(gnum[k]) is not None else gtxt[k]
            gap_num = _num(gnum[k])
        if pos == 1:
            interval = gap = "LEADER"
            gap_num = 0.0
        # sectors
        sectors_out = []
        for s in (1, 2, 3):
            v = d["sec_vals"][s - 1]
            own = _num(_own_min(L, s, t))
            sectors_out.append(None if v is None else {"value": v, "colour": sector_colour(v, own, _num(sec_overall[s]) if np.isfinite(sec_overall[s]) else None)})
        ms_out = [None] * n_ms
        got = ix["ms"].get((n, d["cur_lap"]))
        di = ix["dpos"][n]
        if got is not None:
            ks, ends, durs = got
            for kk, e, du in zip(ks, ends, durs):
                if e <= t:
                    ov = ms_overall[kk]
                    ms_out[int(kk)] = {"value": float(du), "colour": sector_colour(
                        float(du), float(ms_own[di][kk]) if np.isfinite(ms_own[di][kk]) else None,
                        float(ov) if np.isfinite(ov) else None)}
        # drs
        row_drs = None
        if meta.get("has_drs"):
            ct, cd = ix["car"][n][:2]
            ci = _last_le(ct, t)
            row_drs = drs_label(cd[ci], t - ct[ci]) if ci >= 0 else None
        best_overall = d["best"] is not None and not any(
            info[o]["best"] is not None and info[o]["best"] < d["best"] for o in drivers if o != n)
        row = {
            "driver_number": n, "acronym": meta_d["acronym"], "full_name": meta_d["full_name"],
            "team_name": meta_d["team_name"], "team_colour": meta_d["team_colour"],
            "position": pos, "grid": grid, "gained": (grid - pos) if grid is not None and pos is not None else None,
            "tyre": tyre, "pit_stops": d["pit_stops"], "in_pit": d["in_pit"],
            "interval": interval, "gap": gap, "gap_num": gap_num,
            "lap": d["cur_lap"], "completed_laps": d["completed"],
            "current_lap_time": (t - d["cur_start"]) if d["cur_start"] is not None else None,
            "sectors": sectors_out, "minisectors": ms_out,
            "last_lap": d["last_lap"], "delta_prev": d["delta_prev"], "best_lap": d["best"],
            "best_is_overall": bool(best_overall),
            "penalty_s": int(parsed["penalty_s"].get(n, 0)), "drive_through_stop_go": 0,
            "investigations": int(parsed["investigations"].get(n, 0)),
            "track_limits": int(parsed["track_limits"].get(n, 0)), "black_white": False,
            "status": d["status"],
        }
        if meta.get("has_drs"):
            row["drs"] = row_drs
        rows.append(row)
    rows.sort(key=lambda r: (r["position"] is None, r["position"] if r["position"] is not None else 0, r["driver_number"]))
    by_n = {r["driver_number"]: r for r in rows}

    # header
    lap = None
    elapsed = max(0.0, t - meta["race_start_t"])
    remaining = None
    if leader is not None:
        lap = min(leader["cur_lap"], total_laps)
        if leader["last_lap"] is not None:
            remaining = max(0, total_laps - leader["completed"]) * leader["last_lap"]
    header = {"lap": lap, "total_laps": total_laps, "elapsed": elapsed, "time_remaining_est": remaining}

    track = {"state": track_state,
             "yellow_sectors": [{"sector": s, "level": lv} for s, lv in sorted(sectors.items())],
             "drs_enabled": drs_enabled}

    feed = [{"t": r["t"], "lap": r.get("lap_number"), "tag": tag_for(r), "message": r.get("message"),
             "category": r.get("category"), "flag": r.get("flag")} for r in reversed(rc_rows[-config.FEED_LENGTH:])]

    # weather
    wi = _last_le(ix["weather_t"], t)
    weather = None
    if wi >= 0:
        w = ix["weather"][wi]
        weather = {k: w.get(k) for k in ("air_temperature", "track_temperature", "humidity", "wind_speed",
                                         "wind_direction", "rainfall", "pressure")}

    forecast = _forecast(bundle, t)

    # map
    track_map = []
    for n in drivers:
        lt, lx, ly = ix["loc"][n]
        got = latest_sample(lt, lx, ly, t)
        if got is not None:
            x, y, age = got
            track_map.append({"driver_number": n, "acronym": dinfo[n]["acronym"], "team_colour": dinfo[n]["team_colour"],
                              "x": x, "y": y, "stale": age > config.MAP_HOLD_S, "status": info[n]["status"]})

    # standings
    season = meta["season"]
    session_name = meta.get("session_name", "Race")
    race_pts = {}
    for n in drivers:
        pos = info[n]["position"]
        if info[n]["status"] == "OUT":
            race_pts[n] = 0
            continue
        p = race_points(pos, season, session_name)
        if points.fastest_lap_point(season, session_name) and fastest_n == n and pos is not None and pos <= len(config.RACE_POINTS):
            p += 1
        race_pts[n] = p
    sp = meta["start_points"]
    d_entries, d_pts, d_posn = {}, {}, {}
    for n in drivers:
        e = sp["drivers"].get(str(n))
        d_pts[n] = e["points"] if e else None
        d_posn[n] = e["position"] if e else None
    ranks = _start_ranks(d_pts, d_posn, drivers)
    for n in drivers:
        d_entries[n] = {"driver_number": n, "acronym": dinfo[n]["acronym"], "name": dinfo[n]["full_name"],
                        "team_name": dinfo[n]["team_name"], "points": d_pts[n], "rank": ranks.get(n)}
    teams = sorted({dinfo[n]["team_name"] for n in drivers})
    t_pts, t_posn, t_gain = {}, {}, {}
    for tn in teams:
        e = sp["teams"].get(tn)
        t_pts[tn] = e["points"] if e else None
        t_posn[tn] = e["position"] if e else None
        t_gain[tn] = sum(race_pts[n] for n in drivers if dinfo[n]["team_name"] == tn)
    t_ranks = _start_ranks(t_pts, t_posn, teams)
    t_entries = {tn: {"team_name": tn, "points": t_pts[tn], "rank": t_ranks.get(tn)} for tn in teams}
    standings = {
        "source": sp["source"],
        "drivers": project_standings(d_entries, race_pts, season, fastest_n, None, None),
        "constructors": project_standings(t_entries, t_gain, season, fastest_n, None, None),
    }
    for e in standings["drivers"]:
        e.pop("key", None)
    for e in standings["constructors"]:
        e.pop("key", None)

    # strategy
    pit_loss, pit_n = estimate_pit_loss(ix, t, tl)
    rejoin = rejoin_projection(rows, pit_loss)
    used_wet = any(s["compound"] in config.WET_COMPOUNDS for d in info.values() for s in d["stints"])
    strategy_rows = []
    for r in rows:
        n = r["driver_number"]
        d = info[n]
        dry = {s["compound"] for s in d["stints"] if s["compound"] in config.DRY_COMPOUNDS}
        behind_n = pos_to_driver.get(r["position"] + 1) if r["position"] is not None else None
        threat = False
        if r["status"] == "RUNNING" and behind_n is not None:
            b = by_n[behind_n]
            bi = ix["int"][behind_n]
            kk = _last_le(bi[0], t)
            b_int = _num(bi[1][kk]) if kk >= 0 else None
            threat = bool(b["status"] == "RUNNING" and b_int is not None and b_int < config.UNDERCUT_GAP_S
                          and b["pit_stops"] == r["pit_stops"])
        cur_stint = d["stints"][-1] if d["stints"] else None
        stint_laps = (d["cur_lap"] - cur_stint["lap_start"] + 1) if cur_stint and cur_stint["lap_start"] is not None else None
        strategy_rows.append({
            "driver_number": n, "acronym": r["acronym"], "stops": r["pit_stops"], "stint_laps": stint_laps,
            "needs_second_dry_compound": bool((not used_wet) and len(dry) == 1),
            "rejoin": rejoin[n], "undercut_threat": threat})
    strategy = {"pit_loss": pit_loss, "pit_loss_stops": pit_n, "rows": strategy_rows}

    tyre_sets, stint_chart, pace_chart = {}, {}, {}
    for n in drivers:
        d = info[n]
        L = ix["laps"][n]
        tyre_sets[n] = [{"compound": s["compound"], "used": bool((s["tyre_age_at_start"] or 0) > 0)} for s in d["stints"]]
        chart = []
        for i, s in enumerate(d["stints"]):
            if i + 1 < len(d["stints"]) and d["stints"][i + 1]["lap_start"] is not None:
                last = d["stints"][i + 1]["lap_start"] - 1
            else:
                last = d["cur_lap"]
            chart.append({"compound": s["compound"], "first_lap": s["lap_start"], "last_lap": last})
        stint_chart[n] = chart
        pit_laps = _pit_laps(ix, n, t, d["cur_i"] + 1)
        pts = []
        for i in np.nonzero(d["known_mask"])[0]:
            pts.append({"lap": int(L["lap_number"][i]), "duration": float(L["lap_duration"][i]),
                        "clean": bool(lap_is_clean(L, i, t, tl, pit_laps))})
        pace_chart[n] = pts

    state = {
        "t": t, "header": header, "track": track, "rows": rows, "feed": feed, "weather": weather,
        "forecast": forecast, "map": track_map, "standings": standings, "strategy": strategy,
        "tyre_sets": tyre_sets, "stint_chart": stint_chart, "pace_chart": pace_chart,
        "fastest_lap": {"driver_number": fastest_n, "time": fastest_t},
    }
    return _clean(state)


def _own_min(L, s, t):
    ok = (L[f"s{s}_end_t"] <= t) & ~np.isnan(L[f"d{s}"])
    return float(L[f"d{s}"][ok].min()) if ok.any() else None


def _forecast(bundle, t):
    fc = bundle["extra"].get("forecast")
    if not fc or "hourly" not in fc:
        return None
    hourly = fc["hourly"]
    times = hourly.get("time") or []
    now = datetime.fromisoformat(bundle["meta"]["t0_utc"]) + timedelta(seconds=t)
    hour = now.replace(minute=0, second=0, microsecond=0)
    key = hour.strftime("%Y-%m-%dT%H:%M")
    if key not in times:
        return []
    i = times.index(key)
    out = []
    for h in range(1, config.FORECAST_HOURS + 1):
        j = i + h
        if j >= len(times):
            out.append(None)
            continue
        row = {"time": times[j]}
        for f in ("precipitation_probability", "precipitation", "temperature_2m", "wind_speed_10m"):
            vals = hourly.get(f)
            row[f] = vals[j] if vals and j < len(vals) else None
        out.append(row)
    return out


def truncate(bundle, t):
    """Copy of the bundle with every time-stamped row after t removed, following the visibility rules."""
    tb = bundle["tables"]
    new = {}
    laps = tb["laps"][tb["laps"]["lap_start_t"] <= t].copy()
    for col, durs in (("s1_end_t", ["d1"]), ("s2_end_t", ["d2"]), ("lap_end_t", ["lap_duration", "d3"])):
        late = laps[col] > t
        laps.loc[late, durs] = NAN
    late = laps["s3_end_t"] > t
    laps.loc[late, "s3_end_t"] = NAN
    for col in ("s1_end_t", "s2_end_t", "lap_end_t"):
        laps.loc[laps[col] > t, col] = NAN
    new["laps"] = laps
    new["minisectors"] = tb["minisectors"][tb["minisectors"]["end_t"] <= t]
    for name in ("position", "intervals", "pit", "race_control", "weather", "car_data", "location"):
        new[name] = tb[name][tb[name]["t"] <= t]
    st = tb["stints"][tb["stints"]["known_t"] <= t].copy()
    st["lap_end"] = None
    new["stints"] = st
    return make_bundle(bundle["meta"], new, bundle["extra"])


# ---------------------------------------------------------------- telemetry, lap trace, lap-only view

HIDDEN_ROW_FIELDS = ("tyre", "sectors", "minisectors", "drs", "penalty_s", "drive_through_stop_go", "investigations",
                     "track_limits", "black_white", "current_lap_time")
HIDDEN_TOP_FIELDS = ("track", "feed", "map", "forecast", "tyre_sets")
HIDDEN_STRATEGY_FIELDS = ("rejoin", "undercut_threat", "needs_second_dry_compound")


def hide_for_kind(state, kind):
    """A lap-only state has none of the fields that need data the source does not have."""
    if kind != "lap_only":
        return state
    out = {k: v for k, v in state.items() if k not in HIDDEN_TOP_FIELDS}
    out["rows"] = [{k: v for k, v in r.items() if k not in HIDDEN_ROW_FIELDS} for r in state["rows"]]
    strat = dict(state["strategy"])
    strat["rows"] = [{k: v for k, v in r.items() if k not in HIDDEN_STRATEGY_FIELDS} for r in strat["rows"]]
    out["strategy"] = strat
    return out


def telemetry_at(bundle, t, driver):
    """Latest car sample at or before t for one driver. Samples older than DRS_STALE_S give None for every value."""
    ix = bundle["idx"]
    n = int(driver)
    out = {"driver_number": n, "speed": None, "gear": None, "throttle": None, "brake": None, "rpm": None, "drs": None}
    if n not in ix["car"]:
        return out
    ct, cd, sp, th, br, gr, rp = ix["car"][n]
    i = _last_le(ct, t)
    if i < 0 or t - ct[i] > config.DRS_STALE_S:
        return out
    out.update({"speed": _num(sp[i]), "gear": _num(gr[i]), "throttle": _num(th[i]),
                "brake": None if np.isnan(br[i]) else bool(br[i] > 0), "rpm": _num(rp[i]),
                "drs": drs_label(cd[i], t - ct[i])})
    return out


def lap_trace(bundle, t, driver):
    """Speed against the fraction of lap distance for the driver's most recent lap with lap_end_t <= t."""
    ix = bundle["idx"]
    n = int(driver)
    empty = {"driver_number": n, "lap": None, "points": []}
    L = ix["laps"].get(n)
    if L is None or n not in ix["car"] or not len(L["lap_end_t"]):
        return empty
    done = np.nonzero((L["lap_end_t"] <= t) & ~np.isnan(L["lap_start_t"]))[0]
    if not len(done):
        return empty
    i = done[np.argmax(L["lap_end_t"][done])]
    start, end = float(L["lap_start_t"][i]), float(L["lap_end_t"][i])
    ct, sp = ix["car"][n][0], ix["car"][n][2]
    m = (ct >= start) & (ct <= end) & ~np.isnan(sp)
    if m.sum() < 2:
        return empty
    tt, v = ct[m], sp[m] / 3.6
    dist = np.concatenate([[0.0], np.cumsum((v[1:] + v[:-1]) / 2 * np.diff(tt))])
    if dist[-1] <= 0:
        return empty
    frac = dist / dist[-1]
    grid = np.linspace(0.0, 1.0, config.TRACE_POINTS)
    speed = np.interp(grid, frac, sp[m])
    return {"driver_number": n, "lap": int(L["lap_number"][i]),
            "points": [[float(a), float(b)] for a, b in zip(grid, speed)]}
