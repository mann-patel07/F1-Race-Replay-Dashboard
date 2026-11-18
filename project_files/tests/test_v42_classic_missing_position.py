"""Version 4.2: a lap timing entry with no position, and the viewing copy called by the update run."""
import json
import sys

import pytest

import build_classic
import bundle_io
import make_share_zip as sz
from tests import v3_helpers as H
from tests.test_v3_share import build_tree


def test_timing_entry_without_position_builds_and_keeps_the_lap_time(tmp_path):
    base = tmp_path
    folder = H.make_classic_raw(base / "raw")
    laps = json.loads((folder / "laps.json").read_text())
    for tm in laps[1]["Timings"]:                  # lap 2, driver bravo (number 2)
        if tm["driverId"] == "bravo":
            del tm["position"]
    (folder / "laps.json").write_text(json.dumps(laps))
    out = build_classic.build(2012, 1, raw_base=base / "raw", bundle_dir=base / "bundle")
    b = bundle_io.load_bundle("jl_2012_01", base=base / "bundle")
    table = b["tables"]
    lap = table["laps"].set_index(["driver_number", "lap_number"])
    assert lap.loc[(2, 2), "lap_end_t"] == pytest.approx(220.0)         # the lap time is kept
    pos = table["position"]
    assert pos[(pos.driver_number == 2) & ((pos.t - 220.0).abs() < 1e-6)].empty   # no position row on that lap
    assert not pos[(pos.driver_number == 2) & ((pos.t - 110.0).abs() < 1e-6)].empty  # other laps still have one
    assert any("no position" in line for line in out["log"])


def test_update_run_may_make_the_viewing_copy_but_a_direct_run_is_refused(tmp_path):
    root, proj = build_tree(tmp_path, lock=True)
    with pytest.raises(sz.ShareRefused):
        sz.make(project=proj, root=root)
    assert not (proj / "share").exists()
    target = sz.make(project=proj, root=root, called_by_update=True)
    assert target.exists()


def test_update_data_passes_called_by_update(monkeypatch):
    import update_data
    seen = {}
    monkeypatch.setattr(sz, "make", lambda **kw: seen.update(kw) or "zip")
    assert update_data.Deps().make_zip() == "zip"
    assert seen == {"called_by_update": True}


def test_driver_with_no_usable_first_lap_builds_without_stint_rows(tmp_path):
    folder = H.make_classic_raw(tmp_path / "raw")
    laps = json.loads((folder / "laps.json").read_text())
    laps[0]["Timings"] = [t for t in laps[0]["Timings"] if t["driverId"] != "charlie"]   # charlie has laps 2 and 3 only
    (folder / "laps.json").write_text(json.dumps(laps))
    out = build_classic.build(2012, 1, raw_base=tmp_path / "raw", bundle_dir=tmp_path / "bundle")
    stints = bundle_io.load_bundle("jl_2012_01", base=tmp_path / "bundle")["tables"]["stints"]
    assert 3 not in set(stints["driver_number"])
    assert any("no usable lap from lap 1" in line for line in out["log"])
