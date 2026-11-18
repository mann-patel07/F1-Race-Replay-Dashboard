"""Verification set: archive five FastF1 sessions, build bundles, compare with OpenF1 and project the full archive.

Run as a job from the Library ("Run verification") or: python verify_fastf1.py"""
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import archive
import build_bundle_fastf1
import bundle_io
import config
import engine

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
CROSS_PATH = RESULTS / "cross_source_check.json"
VERIFY_PATH = RESULTS / "fastf1_verification.json"
COMPARE_TIMES = 20
LAP_TOLERANCE_S = 0.005
OPENF1_ID = "of_9920"


def pick_sessions(sessions):
    """The verification set from the saved session list."""
    def find(year, pred):
        return next((s for s in sessions if s["year"] == year and pred(s)), None)
    picks = []
    dutch = find(2025, lambda s: s["session"] == "Race" and "Dutch" in str(s.get("event")))
    if dutch:
        picks.append(dutch)
    for year in (2021, 2023):
        s = find(year, lambda s: s["round"] == 1 and s["session"] == "Race")
        if s:
            picks.append(s)
    sprints = sorted((s for s in sessions if s["year"] == 2024 and s["session"] == "Sprint"), key=lambda s: s["round"])
    if sprints:
        first = sprints[0]
        picks.append(first)
        race = find(2024, lambda s: s["round"] == first["round"] and s["session"] == "Race")
        if race:
            picks.append(race)
    return picks


def cross_source(ff_id, of_id=OPENF1_ID):
    a, b = bundle_io.load_bundle(of_id), bundle_io.load_bundle(ff_id)
    la, lb = a["tables"]["laps"], b["tables"]["laps"]
    m = la.merge(lb, on=["driver_number", "lap_number"], suffixes=("_of", "_ff"))
    both = m.dropna(subset=["lap_duration_of", "lap_duration_ff"])
    diff = (both["lap_duration_of"] - both["lap_duration_ff"]).abs()
    t0a, t0b = pd.Timestamp(a["meta"]["t0_utc"]), pd.Timestamp(b["meta"]["t0_utc"])
    offset_a = (t0a - t0b).total_seconds()
    end_diff = (m["lap_end_t_of"] + (t0a - t0b).total_seconds()) - m["lap_end_t_ff"]
    start_utc = max(t0a.timestamp() + a["meta"]["race_start_t"], t0b.timestamp() + b["meta"]["race_start_t"]) + 60
    end_utc = min(t0a.timestamp() + a["meta"]["replay_end_t"], t0b.timestamp() + b["meta"]["replay_end_t"])
    agree = []
    for u in np.linspace(start_utc, end_utc, COMPARE_TIMES):
        sa = engine.state_at(a, u - t0a.timestamp())
        sb = engine.state_at(b, u - t0b.timestamp())
        pa = {r["driver_number"]: r["position"] for r in sa["rows"] if r["position"] is not None}
        pb = {r["driver_number"]: r["position"] for r in sb["rows"] if r["position"] is not None}
        common = set(pa) & set(pb)
        if common:
            agree.append(sum(1 for n in common if pa[n] == pb[n]) / len(common))
    pits_a = a["tables"]["pit"].groupby("driver_number").size().to_dict()
    pits_b = b["tables"]["pit"].groupby("driver_number").size().to_dict()
    return {
        "laps_matched": int(len(m)), "laps_with_both_durations": int(len(both)),
        "lap_duration_median_abs_diff_s": float(diff.median()) if len(diff) else None,
        "lap_duration_max_abs_diff_s": float(diff.max()) if len(diff) else None,
        "share_laps_over_0_005_s": float((diff > LAP_TOLERANCE_S).mean()) if len(diff) else None,
        "position_agreement_share_per_time": [round(x, 4) for x in agree],
        "position_agreement_mean": float(np.mean(agree)) if agree else None,
        "pit_stops_openf1": {str(k): int(v) for k, v in sorted(pits_a.items())},
        "pit_stops_fastf1": {str(k): int(v) for k, v in sorted(pits_b.items())},
        "time_base_offset_s_median_of_lap_end_utc": float(end_diff.median()) if len(end_diff.dropna()) else None,
        "bundle_t0_difference_s": offset_a,
    }


def projection(records, wanted_total):
    ok = [r for r in records if r["status"] == "ok"]
    if not ok:
        return None
    avg_s = statistics.mean(r["seconds"] for r in ok)
    avg_archive = statistics.mean(r["archive_bytes"] for r in ok)
    avg_bundle = statistics.mean(r["bundle_bytes"] for r in ok)
    return {"sessions_measured": len(ok), "wanted_sessions": wanted_total,
            "archive_hours": round(avg_s * wanted_total / 3600, 1), "archive_gb": round(avg_archive * wanted_total / 1e9, 1),
            "bundle_gb": round(avg_bundle * wanted_total / 1e9, 1), "share_gb": round(avg_bundle * wanted_total / 1e9, 1)}


def run(ctx=None):
    class Quiet:
        def progress(self, *a, **k):
            pass

        def cancelled(self):
            return False
    ctx = ctx or Quiet()
    ctx.progress("reading the FastF1 schedule")
    sessions = archive.list_sessions()
    picks = pick_sessions(sessions)
    records = []
    for i, s in enumerate(picks):
        year, rnd, name = int(s["year"]), int(s["round"]), s["session"]
        label = f"{year} round {rnd:02d} {s.get('event', '')} {name}"
        ctx.progress(label, i, len(picks))
        started = time.time()
        code, message = archive.run_one_process(year, rnd, name)
        rec = {"year": year, "round": rnd, "session": name, "event": s.get("event"), "exit_code": code,
               "status": "ok" if code == 0 else "no_data" if code == 4 else "failed", "message": message}
        folder = archive.ARCHIVE / str(year) / f"{rnd:02d}" / config.SESSION_CODES[name]
        if code == 0:
            info = json.loads((folder / "info.json").read_text())
            rec["rows"] = info["rows"]
            rec["archive_bytes"] = archive.folder_size(folder)
            try:
                out = build_bundle_fastf1.build(year, rnd, name)
                rec["race_id"] = out["race_id"]
                rec["bundle_bytes"] = archive.folder_size(bundle_io.bundle_folder(out["race_id"]))
            except Exception as exc:
                rec["status"], rec["message"] = "failed", f"bundle build failed: {type(exc).__name__}: {exc}"
        rec["seconds"] = round(time.time() - started, 1)
        archive.record(year, rnd, name, status=rec["status"], seconds=rec["seconds"], size=rec.get("archive_bytes", 0),
                       rows=rec.get("rows", {}), event=s.get("event"), message=rec["message"] if rec["status"] == "failed" else None,
                       finished=datetime.now(timezone.utc).isoformat())
        archive.cleanup_cache()
        records.append(rec)
    checks = {}
    ff_dutch = next((r["race_id"] for r in records if r["year"] == 2025 and r.get("race_id")), None)
    cross = None
    if ff_dutch and bundle_io.bundle_exists(OPENF1_ID):
        ctx.progress("cross-source check")
        cross = cross_source(ff_dutch)
        RESULTS.mkdir(exist_ok=True)
        CROSS_PATH.write_text(json.dumps(cross, indent=1))
        checks["median_lap_difference_below_0_005_s"] = bool(cross["lap_duration_median_abs_diff_s"] is not None
                                                             and cross["lap_duration_median_abs_diff_s"] < LAP_TOLERANCE_S)
    wanted = sum(1 for s in sessions if (datetime.now(timezone.utc) - datetime.fromisoformat(s["date"])).days >= 1)
    result = {"generated": datetime.now(timezone.utc).isoformat(), "sessions": records, "cross_source": cross,
              "checks": checks, "projection": projection(records, wanted)}
    RESULTS.mkdir(exist_ok=True)
    VERIFY_PATH.write_text(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
