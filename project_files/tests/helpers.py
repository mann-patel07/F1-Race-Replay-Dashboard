"""Small hand-built bundles for the engine tests."""
import numpy as np
import pandas as pd

from bundle_io import make_bundle

NAN = float("nan")


def make_meta(n_drivers, total_laps=10, season=2025, start_points=None, race_start_t=0.0, teams=None):
    drivers = []
    for i in range(1, n_drivers + 1):
        drivers.append({"driver_number": i, "acronym": f"D{i:02d}", "full_name": f"Driver {i}",
                        "team_name": (teams or {}).get(i, f"Team{(i + 1) // 2}"), "team_colour": "FFFFFF",
                        "grid_position": i})
    sp = {"source": "test", "drivers": {}, "teams": {}}
    for i in range(1, n_drivers + 1):
        pts = (start_points or {}).get(i, 0.0)
        sp["drivers"][str(i)] = {"points": pts, "position": i} if pts is not None else None
    for tn in sorted({d["team_name"] for d in drivers}):
        sp["teams"][tn] = {"points": 0.0, "position": 1}
    return {"drivers": drivers, "total_laps": total_laps, "season": season, "start_points": sp,
            "t0_utc": "2025-01-01T12:00:00+00:00", "race_start_t": race_start_t, "has_drs": False,
            "replay_start_t": race_start_t - 60, "replay_end_t": 1000.0}


def lap(n, number, start, d1, d2, d3, pitout=False):
    end = start + d1 + d2 + d3
    return {"driver_number": n, "lap_number": number, "lap_start_t": start, "lap_end_t": end,
            "lap_duration": d1 + d2 + d3, "d1": d1, "d2": d2, "d3": d3, "s1_end_t": start + d1,
            "s2_end_t": start + d1 + d2, "s3_end_t": end, "is_pit_out_lap": pitout}


def laps_df(rows):
    return pd.DataFrame(rows)


def rc_row(t, category, flag, scope, message, sector=None, lap_number=1, driver_number=None):
    return {"t": t, "lap_number": lap_number, "category": category, "flag": flag, "scope": scope,
            "sector": sector, "driver_number": driver_number, "message": message}


def build(meta, **tables):
    frames = {k: (v if isinstance(v, pd.DataFrame) else pd.DataFrame(v)) for k, v in tables.items() if len(v)}
    return make_bundle(meta, frames, {"forecast": None, "session_result": []})


# ---------------------------------------------------------------- skips when race data is not on disk
SKIP_REASON = "needs the race bundles and the full race library, which are not in this repository"


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent


def _bundle_id(race_id):
    return race_id[3:] if race_id.startswith("of_") else race_id


def missing_races(*race_ids):
    """The race ids whose page data folder or bundle folder is not on disk."""
    root = _root()
    return [r for r in race_ids
            if not ((root / "site" / "data" / "races" / r / "meta.js").exists()
                    and (root / "data" / "bundle" / _bundle_id(r) / "meta.json").exists())]


def needs_races(*race_ids):
    """Skip the running test when any of these races is not on disk."""
    import pytest
    if missing_races(*race_ids):
        pytest.skip(SKIP_REASON)


def has_full_library():
    root = _root()
    races = root / "site" / "data" / "races"
    bundles = root / "data" / "bundle"
    return races.is_dir() and bundles.is_dir() and len(list(races.iterdir())) >= 400 and len(list(bundles.iterdir())) >= 400


def needs_full_library():
    """Skip the running test unless the whole race library is on disk."""
    import pytest
    if not has_full_library():
        pytest.skip(SKIP_REASON)


def race_mark(race_id):
    """A skipif mark for a parametrized test: decided before any fixture or browser starts."""
    import pytest
    return pytest.mark.skipif(bool(missing_races(race_id)), reason=SKIP_REASON)
