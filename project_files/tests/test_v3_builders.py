import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_bundle_fastf1 as bf
import build_classic
import bundle_io
import config
import engine
import points
from bundle_common import generated_colour
from tests import v3_helpers as H

HOME_MARK = "/Us" + "ers/"


def no_look_ahead(bundle, times):
    for t in times:
        assert engine.state_at(bundle, float(t)) == engine.state_at(engine.truncate(bundle, float(t)), float(t)), f"t={t}"


@pytest.fixture(scope="module")
def ff(tmp_path_factory):
    base = tmp_path_factory.mktemp("ff")
    H.make_archive(base / "archive")
    out = bf.build(2025, 1, "Race", archive_dir=base / "archive", bundle_dir=base / "bundle", start_points_fn=H.stub_points,
                   forecast_fn=lambda *a: None, write_log=False)
    return base, out


def test_3_fastf1_tables_have_expected_columns(ff):
    base, out = ff
    folder = Path(out["folder"])
    for name, cols in bundle_io.TABLE_COLUMNS.items():
        parts = sorted(folder.glob(f"{name}*.parquet"))
        assert parts, name
        df = pd.concat([pd.read_parquet(p) for p in parts])
        assert list(df.columns) == cols, name


def test_3_fastf1_pit_stint_grid_drs_and_intervals(ff):
    base, out = ff
    b = bundle_io.load_bundle("ff_2025_01_R", base=base / "bundle")
    tb = b["tables"]
    pit = tb["pit"]
    assert len(pit) == 1
    row = pit.iloc[0]
    assert row["driver_number"] == 44 and row["lap_number"] == 2
    lap3 = tb["laps"][(tb["laps"].driver_number == 44) & (tb["laps"].lap_number == 3)].iloc[0]
    assert row["t"] == pytest.approx(lap3["lap_start_t"] + 20.0)          # pit.t is the pit-out time
    lap2_end = tb["laps"][(tb["laps"].driver_number == 44) & (tb["laps"].lap_number == 2)].iloc[0]["lap_end_t"]
    assert row["lane_duration"] == pytest.approx(row["t"] - (lap2_end - 3.0))
    assert bool(lap3["is_pit_out_lap"]) is True
    st = tb["stints"]
    s63 = st[st.driver_number == 63].iloc[0]
    assert np.isnan(s63["tyre_age_at_start"])                              # TyreLife null gives null age
    s1 = st[(st.driver_number == 1)].iloc[0]
    assert s1["tyre_age_at_start"] == 2.0                                  # TyreLife 3 on the first lap
    s44 = st[st.driver_number == 44].sort_values("stint_number")
    assert list(s44["compound"]) == ["SOFT", "HARD"] and s44.iloc[1]["tyre_age_at_start"] == 0.0
    iv = tb["intervals"]
    assert iv[iv.gap_txt == "LAP 1"].shape[0] >= 1 and iv[iv.gap_txt == "1 L"].shape[0] >= 1
    num_rows = iv[(iv.driver_number == 44) & (iv.gap_num == 1.5)]
    assert len(num_rows) == 1 and pd.isna(num_rows.iloc[0]["gap_txt"])
    assert iv[(iv.driver_number == 63) & (iv.gap_num == 3.25)].shape[0] == 1
    grid = {d["driver_number"]: d["grid_position"] for d in b["meta"]["drivers"]}
    assert grid == {1: 1, 44: None, 63: 3}
    assert b["meta"]["has_drs"] is True and b["meta"]["kind"] == "full" and b["meta"]["source"] == "fastf1"
    assert b["meta"]["start_points"]["source"] == "stub standings"
    assert b["meta"]["corners"][1] == {"number": 2, "letter": "a", "x": 2.0, "y": 4.0}
    assert (tb["location"]["x"] == 0).sum() == 0                           # no-fix rows dropped
    colours = {d["driver_number"]: d["team_colour"] for d in b["meta"]["drivers"]}
    assert colours[1] == "112233" and colours[44] == generated_colour("Team44")


def test_3_missing_stream_uses_position_fallback(tmp_path):
    H.make_archive(tmp_path / "archive", with_stream=False)
    out = bf.build(2025, 1, "Race", archive_dir=tmp_path / "archive", bundle_dir=tmp_path / "bundle", start_points_fn=H.stub_points,
                   forecast_fn=lambda *a: None, write_log=False)
    b = bundle_io.load_bundle("ff_2025_01_R", base=tmp_path / "bundle")
    assert len(b["tables"]["intervals"]) == 0
    assert any("intervals and gaps (timing stream not in the archive)" in m for m in b["meta"]["missing"])
    pos = b["tables"]["position"]
    assert len(pos) == 3 * 5 + 2                                           # one per completed lap plus the two grid slots
    assert pos["t"].min() == pytest.approx(b["meta"]["replay_start_t"])


def test_6_fastf1_no_look_ahead(ff):
    base, _ = ff
    b = bundle_io.load_bundle("ff_2025_01_R", base=base / "bundle")
    lo, hi = b["meta"]["replay_start_t"], b["meta"]["replay_end_t"]
    no_look_ahead(b, np.linspace(lo, hi, 8))


@pytest.fixture(scope="module")
def classic(tmp_path_factory):
    base = tmp_path_factory.mktemp("classic")
    H.make_classic_raw(base / "raw")
    out = build_classic.build(2012, 1, raw_base=base / "raw", bundle_dir=base / "bundle")
    return base, out


def test_5_classic_laps_positions_and_gaps(classic):
    base, out = classic
    b = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    tb, meta = b["tables"], b["meta"]
    laps = tb["laps"].set_index(["driver_number", "lap_number"])
    assert laps.loc[(1, 2), "lap_end_t"] == pytest.approx(200.0)
    assert laps.loc[(1, 3), "lap_start_t"] == pytest.approx(200.0)
    assert laps.loc[(3, 3), "lap_end_t"] == pytest.approx(450.0)
    assert laps["d1"].isna().all()
    pos = tb["position"]
    assert pos[(pos.t == 0.0)].shape[0] == 3                               # grid at t = 0
    assert pos[(pos.driver_number == 2) & np.isclose(pos.t, 220.0)].iloc[0]["position"] == 2
    iv = tb["intervals"].set_index(["driver_number", "t"])
    assert iv.loc[(2, 220.0), "gap_num"] == pytest.approx(20.0)
    assert iv.loc[(2, 220.0), "interval_num"] == pytest.approx(20.0)
    assert iv.loc[(3, 150.0), "gap_num"] == pytest.approx(50.0) and iv.loc[(3, 150.0), "interval_num"] == pytest.approx(40.0)
    lapped = iv.loc[(3, 450.0)]
    assert lapped["gap_txt"] == "+1 LAP" and np.isnan(lapped["gap_num"]) and np.isnan(lapped["interval_num"])
    assert meta["kind"] == "lap_only" and meta["has_drs"] is False and meta["outline"] == []


def test_5_classic_pits_chequered_and_colours(classic, tmp_path):
    base, out = classic
    b = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    pit = b["tables"]["pit"].set_index("driver_number")
    assert pit.loc[2, "t"] == pytest.approx(220.0 + 22.5) and pit.loc[2, "lane_duration"] == 22.5
    assert pit.loc[3, "t"] == pytest.approx(150.0) and np.isnan(pit.loc[3, "lane_duration"])     # 2:30.000 is above 120 s
    assert pit.loc[1, "t"] == pytest.approx(300.0 + 65.0)                                         # M:SS.sss parses
    laps = b["tables"]["laps"].set_index(["driver_number", "lap_number"])
    assert bool(laps.loc[(2, 3), "is_pit_out_lap"]) and not bool(laps.loc[(2, 2), "is_pit_out_lap"])
    rc = b["tables"]["race_control"]
    assert len(rc) == 1
    r = rc.iloc[0]
    assert (r["category"], r["flag"], r["scope"]) == ("Flag", "CHEQUERED", "Track")
    assert r["message"] == "CHEQUERED FLAG (derived from lap times)" and r["t"] == pytest.approx(400.0)
    again = build_classic.build(2012, 1, raw_base=base / "raw", bundle_dir=tmp_path / "bundle2")
    m2 = json.loads((Path(again["folder"]) / "meta.json").read_text())
    assert [d["team_colour"] for d in m2["drivers"]] == [d["team_colour"] for d in b["meta"]["drivers"]]
    assert any("safety car" in m for m in b["meta"]["missing"]) and any("generated" in m for m in b["meta"]["missing"])
    assert str(base) not in json.dumps(b["meta"])


def test_5_classic_start_points_use_points_table(classic):
    base, _ = classic
    b = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    s = engine.state_at(b, b["meta"]["replay_end_t"])
    top = s["standings"]["drivers"][0]
    assert points.points_table(2012, "Race")[0] == 25 and top["race_points"] == 25


def test_6_classic_no_look_ahead(classic):
    base, _ = classic
    b = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    no_look_ahead(b, np.linspace(b["meta"]["replay_start_t"], b["meta"]["replay_end_t"], 8))


def test_6_real_bundles_no_look_ahead():
    ids = ["of_9920"] + [p.name for p in sorted(bundle_io.BUNDLE_DIR.glob("jl_*")) + sorted(bundle_io.BUNDLE_DIR.glob("ff_*"))
                         if (p / "meta.json").exists()]
    for rid in ids:
        b = bundle_io.load_bundle(rid)
        no_look_ahead(b, np.linspace(b["meta"]["replay_start_t"], b["meta"]["replay_end_t"], 8))


def test_7_lap_trace_uses_only_the_last_completed_lap():
    b = bundle_io.load_bundle("of_9920")
    L = b["tables"]["laps"]
    lap = L[(L.driver_number == 81) & (L.lap_number == 10)].iloc[0]
    just_before, at_end = lap["lap_end_t"] - 0.001, lap["lap_end_t"]
    a, c = engine.lap_trace(b, just_before, 81), engine.lap_trace(b, at_end, 81)
    assert len(a["points"]) == config.TRACE_POINTS and a["lap"] == 9
    assert c["lap"] == 10 and len(c["points"]) == config.TRACE_POINTS
    assert engine.lap_trace(engine.truncate(b, at_end), at_end, 81) == c
    assert engine.lap_trace(b, 0.0, 81)["points"] == []
    assert c["points"][0][0] == 0.0 and c["points"][-1][0] == 1.0


def test_8_lap_only_state_hides_fields(classic):
    base, _ = classic
    lap = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    full = bundle_io.load_bundle("of_9920")
    s_lap = engine.hide_for_kind(engine.state_at(lap, 300.0), "lap_only")
    s_full = engine.hide_for_kind(engine.state_at(full, 3000.0), "full")
    for k in engine.HIDDEN_TOP_FIELDS:
        assert k not in s_lap and k in s_full
    for k in engine.HIDDEN_ROW_FIELDS:
        assert all(k not in r for r in s_lap["rows"]) and all(k in r for r in s_full["rows"])
    for k in engine.HIDDEN_STRATEGY_FIELDS:
        assert all(k not in r for r in s_lap["strategy"]["rows"]) and all(k in r for r in s_full["strategy"]["rows"])
    assert "pit_loss" in s_lap["strategy"] and "pace_chart" in s_lap and "stint_chart" in s_lap and "standings" in s_lap


def test_20_no_absolute_paths_in_written_files(ff, classic):
    root = str(Path(__file__).resolve().parent.parent)
    for base in (ff[0], classic[0]):
        for p in base.rglob("meta.json"):
            assert root not in p.read_text() and str(base) not in p.read_text()
    for p in list((Path(root) / "data").glob("library_index.json")) + list((Path(root) / "data" / "bundle").glob("*/meta.json")):
        assert HOME_MARK not in p.read_text(), p
