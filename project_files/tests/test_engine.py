import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_bundle
import bundle_io
import config
import engine
from helpers import build, lap, make_meta, rc_row

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def real():
    return bundle_io.load_bundle(bundle_io.latest_session_key())


def row_of(state, n):
    return next(r for r in state["rows"] if r["driver_number"] == n)


# ------------------------------------------------------------ real bundle tests

def test_1_no_look_ahead(real):
    m = real["meta"]
    times = np.linspace(m["replay_start_t"], m["replay_end_t"], 12)
    for t in times:
        full = engine.state_at(real, t)
        cut = engine.state_at(engine.truncate(real, t), t)
        assert full == cut, f"state differs at t={t}"


def test_2_monotonic_facts(real):
    m = real["meta"]
    times = np.linspace(m["replay_start_t"], m["replay_end_t"], 200)
    prev = {}
    for t in times:
        s = engine.state_at(real, t)
        for r in s["rows"]:
            n = r["driver_number"]
            best = r["best_lap"] if r["best_lap"] is not None else float("inf")
            cur = (r["pit_stops"], r["lap"], best)
            if n in prev:
                assert cur[0] >= prev[n][0]
                assert cur[1] >= prev[n][1]
                assert cur[2] <= prev[n][2]
            prev[n] = cur


def test_3_positions_are_a_permutation(real):
    m = real["meta"]
    times = np.linspace(m["race_start_t"], m["replay_end_t"], 50)
    bad = []
    for t in times:
        s = engine.state_at(real, t)
        pos = [r["position"] for r in s["rows"] if r["position"] is not None]
        if len(pos) != len(set(pos)):
            bad.append(float(t))
    assert not bad, f"duplicate positions at {bad}"


def test_4_retirement_check(real):
    end = real["meta"]["replay_end_t"]
    s = engine.state_at(real, end)
    result = real["extra"]["session_result"]
    assert result, "session_result is empty"
    status = {r["driver_number"]: r["status"] for r in s["rows"]}
    counts = {"out_and_not_classified": 0, "out_but_classified": 0, "not_out_but_not_classified": 0,
              "not_out_and_classified": 0}
    full_distance_out = []
    for r in result:
        n = r["driver_number"]
        gone = bool(r.get("dnf") or r.get("dns") or r.get("dsq"))
        out = status.get(n) == "OUT"
        key = ("out" if out else "not_out") + ("_and_not_classified" if (out and gone) else
              "_but_classified" if out else "_and_classified" if not gone else "_but_not_classified")
        key = {"out_and_not_classified": "out_and_not_classified", "out_but_classified": "out_but_classified",
               "not_out_and_classified": "not_out_and_classified",
               "not_out_but_not_classified": "not_out_but_not_classified"}[key]
        counts[key] += 1
        if out and not gone and r.get("number_of_laps") == real["meta"]["total_laps"]:
            full_distance_out.append(n)
    path = ROOT / "results" / "out_heuristic_check.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"session_key": real["meta"]["session"]["session_key"], "counts": counts,
                                "meaning": {"out_and_not_classified": "status OUT and dnf, dns or dsq in session_result",
                                            "out_but_classified": "status OUT but classified as a finisher",
                                            "not_out_but_not_classified": "dnf, dns or dsq but status is not OUT",
                                            "not_out_and_classified": "not OUT and classified"}}, indent=2))
    assert path.exists()
    assert not full_distance_out


def test_real_race_control_parsing(real):
    rows = real["idx"]["rc"]
    p = engine.parse_race_control(rows)
    assert p["penalty_s"][55] == 10
    assert p["penalty_s"][12] == 15
    assert p["track_limits"][16] == 2
    inv = p["investigations"]
    assert inv[44] == 1 and inv[16] == 1 and inv[63] == 1 and inv[5] == 1
    assert inv[12] == 0 and inv[18] == 0 and inv[22] == 0


# ------------------------------------------------------------ hand-built tests

def test_5_sector_colours():
    meta = make_meta(3)
    laps = [lap(1, 1, 0, 30.0, 26.0, 20.0), lap(1, 2, 100, 31.0, 25.5, 20.0),
            lap(2, 1, 0, 29.0, 25.0, 20.0), lap(2, 2, 100, 29.0, 25.0, 20.0),
            lap(3, 1, 0, 29.0, 27.0, 21.0), lap(3, 2, 100, 29.0, 27.0, 21.0)]
    b = build(meta, laps=laps)
    s = engine.state_at(b, 150.0)
    a, bb, c = row_of(s, 1), row_of(s, 2), row_of(s, 3)
    assert a["sectors"][0]["colour"] == "yellow"      # 31.0, own best is 30.0
    assert bb["sectors"][0]["colour"] == "purple"     # overall best 29.0
    assert c["sectors"][0]["colour"] == "purple"      # tie with the overall best
    assert a["sectors"][1]["colour"] == "green"       # 25.5 is own best, overall best is 25.0
    assert bb["sectors"][1]["colour"] == "purple"
    assert c["sectors"][1]["colour"] == "green"       # 27.0 is its own best but not the overall best


def test_5b_sector_not_overall_best():
    meta = make_meta(2)
    laps = [lap(1, 1, 0, 30.0, 26.0, 20.0), lap(1, 2, 100, 30.5, 26.0, 20.0),
            lap(2, 1, 0, 29.0, 25.0, 20.0)]
    s = engine.state_at(build(meta, laps=laps), 150.0)
    assert row_of(s, 1)["sectors"][0]["colour"] == "yellow"
    assert row_of(s, 1)["sectors"][1]["colour"] == "green"
    laps2 = [lap(1, 1, 0, 30.0, 26.0, 20.0), lap(2, 1, 0, 29.0, 25.0, 20.0)]
    s2 = engine.state_at(build(meta, laps=laps2), 100.0)
    assert row_of(s2, 1)["sectors"][0]["colour"] == "green"   # a first value is the driver's own best


def test_5c_green_when_own_best_shown():
    meta = make_meta(2)
    laps = [lap(1, 1, 0, 31.0, 26.0, 20.0), lap(1, 2, 100, 30.0, 26.0, 20.0),
            lap(2, 1, 0, 29.0, 25.0, 20.0)]
    s = engine.state_at(build(meta, laps=laps), 150.0)
    assert row_of(s, 1)["sectors"][0]["colour"] == "green"


def test_6_tyre_age():
    meta = make_meta(1)
    laps = [lap(1, i, (i - 1) * 100.0, 30.0, 30.0, 30.0) for i in range(1, 9)]
    stints = [{"driver_number": 1, "stint_number": 1, "lap_start": 1, "lap_end": 4, "compound": "SOFT",
               "tyre_age_at_start": 0.0, "known_t": -60.0},
              {"driver_number": 1, "stint_number": 2, "lap_start": 5, "lap_end": 8, "compound": "HARD",
               "tyre_age_at_start": float("nan"), "known_t": 400.0}]
    b = build(meta, laps=laps, stints=stints)
    s = engine.state_at(b, 690.0)   # laps 5, 6 and 7 of the stint have ended (490, 590, 690)
    r = row_of(s, 1)
    assert r["tyre"]["compound"] == "HARD"
    assert r["tyre"]["age"] == 3
    s2 = engine.state_at(b, 589.0)
    assert row_of(s2, 1)["tyre"]["age"] == 1
    assert row_of(s2, 1)["tyre"]["age_unknown_start"] is True
    stints[1]["tyre_age_at_start"] = 3.0
    s3 = engine.state_at(build(meta, laps=laps, stints=stints), 589.0)
    assert row_of(s3, 1)["tyre"]["age"] == 4
    assert row_of(s3, 1)["tyre"]["age_unknown_start"] is False


def test_7_lap_visibility():
    meta = make_meta(1)
    laps = [{"driver_number": 1, "lap_number": 1, "lap_start_t": 0.0, "lap_end_t": 100.0, "lap_duration": 100.0,
             "d1": 30.0, "d2": 30.0, "d3": 40.0, "s1_end_t": 30.0, "s2_end_t": 60.0, "s3_end_t": 100.0,
             "is_pit_out_lap": False}]
    b = build(meta, laps=laps)
    assert row_of(engine.state_at(b, 99.999), 1)["last_lap"] is None
    assert row_of(engine.state_at(b, 100.0), 1)["last_lap"] == 100.0
    assert row_of(engine.state_at(b, 29.999), 1)["sectors"][0] is None
    assert row_of(engine.state_at(b, 30.0), 1)["sectors"][0]["value"] == 30.0


def _pit_bundle(extras):
    meta = make_meta(len(extras))
    laps, pits = [], []
    for n, (in_lap, out_lap) in enumerate(extras, start=1):
        start = 0.0
        for k in range(1, 13):
            dur = 80.0
            pitout = False
            if k == 6:
                dur = in_lap
            if k == 7:
                dur, pitout = out_lap, True
            laps.append(lap(n, k, start, dur, 0.0, 0.0, pitout))
            start += dur
        pits.append({"t": 6 * 80.0 + in_lap + 10.0 - 80.0, "driver_number": n, "lap_number": 6,
                     "lane_duration": 20.0, "stop_duration": 2.5})
    return build(meta, laps=laps, pit=pits)


def test_8_pit_loss():
    b = _pit_bundle([(85.0, 105.0), (84.0, 104.0), (87.0, 107.0)])   # losses 30, 28, 34 -> median 30
    s = engine.state_at(b, 5000.0)
    assert s["strategy"]["pit_loss"] == 30.0
    assert s["strategy"]["pit_loss_stops"] == 3
    b2 = _pit_bundle([(85.0, 105.0), (84.0, 104.0)])
    s2 = engine.state_at(b2, 5000.0)
    assert config.PIT_LOSS_MIN_STOPS == 3
    assert s2["strategy"]["pit_loss"] is None


def test_9_rejoin_projection():
    rows = [{"driver_number": 1, "acronym": "AAA", "status": "RUNNING", "gap_num": 0.0},
            {"driver_number": 2, "acronym": "BBB", "status": "RUNNING", "gap_num": 5.0},
            {"driver_number": 3, "acronym": "CCC", "status": "RUNNING", "gap_num": 12.0},
            {"driver_number": 4, "acronym": "DDD", "status": "RUNNING", "gap_num": 30.0},
            {"driver_number": 5, "acronym": "EEE", "status": "RUNNING", "gap_num": 45.0},
            {"driver_number": 6, "acronym": "FFF", "status": "OUT", "gap_num": 50.0},
            {"driver_number": 7, "acronym": "GGG", "status": "RUNNING", "gap_num": None}]
    r = engine.rejoin_projection(rows, 20.0)
    assert r[1] == {"position": 3, "car_ahead": "CCC", "gap_to_car_ahead": 8.0}
    assert r[4] == {"position": 5, "car_ahead": "EEE", "gap_to_car_ahead": 5.0}
    assert r[5] == {"position": 5, "car_ahead": "DDD", "gap_to_car_ahead": 35.0}
    assert r[6] is None and r[7] is None
    assert engine.rejoin_projection(rows, None)[1] is None


def test_10_projected_standings():
    assert [engine.race_points(p) for p in (1, 2, 10, 11, None)] == [25, 18, 1, 0, 0]
    meta = make_meta(3, start_points={1: 10.0, 2: 10.0, 3: 0.0})
    pos = [{"t": -10.0, "driver_number": i, "position": i} for i in (1, 2, 3)]
    laps = [lap(1, 1, 0, 30, 30, 30), lap(2, 1, 0, 30, 30, 29.0), lap(3, 1, 0, 30, 30, 31)]
    b = build(meta, position=pos, laps=laps)
    s = engine.state_at(b, 95.0)
    d = {e["driver_number"]: e for e in s["standings"]["drivers"]}
    assert d[1]["projected"] == 35.0 and d[2]["projected"] == 28.0 and d[3]["projected"] == 15.0
    # 2024 gives the fastest lap point to driver 2, who is in the top 10
    meta24 = make_meta(3, season=2024, start_points={1: 10.0, 2: 10.0, 3: 0.0})
    s24 = engine.state_at(build(meta24, position=pos, laps=laps), 95.0)
    d24 = {e["driver_number"]: e for e in s24["standings"]["drivers"]}
    assert d24[2]["projected"] == 29.0 and d24[1]["projected"] == 35.0
    # a tie keeps the start order
    tie = engine.project_standings({1: {"points": 10.0, "rank": 1}, 2: {"points": 5.0, "rank": 2}},
                                   {1: 0, 2: 5}, 2025, None, None, None)
    assert [r["key"] for r in tie] == [1, 2]
    assert tie[0]["rank_change"] == 0


def _status_bundle(rc=None, pit_lap_for_dead=False):
    meta = make_meta(9)
    laps, pos = [], []
    for n in range(4, 10):
        for k in range(1, 6):
            laps.append(lap(n, k, (k - 1) * 100.0, 100.0, 0.0, 0.0))
    # driver 1: finished, driver 2: in the pit and stalled, driver 3: stalled only
    laps.append(lap(1, 1, 0.0, 60.0, 0.0, 0.0))
    laps.append(lap(1, 2, 60.0, 0.0, 0.0, 0.0))
    laps[-1].update({"lap_end_t": float("nan"), "lap_duration": float("nan"), "s3_end_t": float("nan"),
                     "is_pit_out_lap": True})
    laps.append({**lap(2, 2, 0.0, 1.0, 1.0, 1.0, pitout=True)})
    laps[-1].update({"lap_end_t": float("nan"), "lap_duration": float("nan"), "s3_end_t": float("nan")})
    laps.append({**lap(3, 1, 0.0, 1.0, 1.0, 1.0)})
    laps[-1].update({"lap_end_t": float("nan"), "lap_duration": float("nan"), "s3_end_t": float("nan")})
    return build(meta, laps=laps, race_control=rc or [])


def test_11_status_order_and_red():
    cheq = [rc_row(50.0, "Flag", "CHEQUERED", "Track", "CHEQUERED FLAG")]
    # driver 1 ends a lap after the chequered flag and is also in a pit-out lap and stalled
    b = _status_bundle(rc=cheq)
    s = engine.state_at(b, 400.0)
    assert row_of(s, 1)["status"] == "FINISHED"
    assert row_of(s, 2)["status"] == "PIT"          # in-pit beats OUT (stalled for 400 s, 5+ others lapped twice)
    assert row_of(s, 3)["status"] == "OUT"
    assert row_of(s, 4)["status"] in ("RUNNING", "FINISHED")
    red = [rc_row(150.0, "Flag", "RED", "Track", "RED FLAG")]
    s2 = engine.state_at(_status_bundle(rc=red), 400.0)
    assert s2["track"]["state"] == "RED"
    assert row_of(s2, 3)["status"] == "RUNNING"     # not raised under red
    s3 = engine.state_at(_status_bundle(), 400.0)
    assert row_of(s3, 3)["status"] == "OUT"
    # OUT drivers get no projected points
    meta_pts = {e["driver_number"]: e for e in s3["standings"]["drivers"]}
    assert meta_pts[3]["race_points"] == 0


def test_12_track_status_state_machine():
    seq = [
        (rc_row(1, "Flag", "GREEN", "Track", "GREEN LIGHT - PIT EXIT OPEN"), "GREEN", []),
        (rc_row(2, "Flag", "YELLOW", "Sector", "YELLOW IN TRACK SECTOR 16", sector=16), "GREEN", [16]),
        (rc_row(3, "Flag", "DOUBLE YELLOW", "Sector", "DOUBLE YELLOW IN TRACK SECTOR 5", sector=5), "GREEN", [5, 16]),
        (rc_row(4, "Flag", "CLEAR", "Sector", "CLEAR IN TRACK SECTOR 16", sector=16), "GREEN", [5]),
        (rc_row(5, "SafetyCar", None, None, "SAFETY CAR DEPLOYED"), "SC", [5]),
        (rc_row(6, "SafetyCar", None, None, "SAFETY CAR IN THIS LAP"), "GREEN", [5]),
        (rc_row(7, "SafetyCar", None, None, "VIRTUAL SAFETY CAR DEPLOYED"), "VSC", [5]),
        (rc_row(8, "SafetyCar", None, None, "VIRTUAL SAFETY CAR ENDING"), "GREEN", [5]),
        (rc_row(9, "Flag", "RED", "Track", "RED FLAG"), "RED", [5]),
        (rc_row(10, "Flag", "CLEAR", "Track", "TRACK CLEAR"), "GREEN", []),
        (rc_row(11, "Flag", "CHEQUERED", "Track", "CHEQUERED FLAG"), "CHEQUERED", []),
        (rc_row(12, "Flag", "GREEN", "Track", "GREEN LIGHT"), "CHEQUERED", []),
    ]
    state, sectors, drs = "GREEN", {}, None
    for row, exp_state, exp_sectors in seq:
        state, sectors, drs = engine.apply_track_message(state, sectors, drs, row)
        assert state == exp_state, row["message"]
        assert sorted(sectors) == exp_sectors, row["message"]
    _, _, drs = engine.apply_track_message("GREEN", {}, None, rc_row(1, "Drs", None, None, "DRS DISABLED"))
    assert drs is False
    _, _, drs = engine.apply_track_message("GREEN", {}, False, rc_row(1, "Drs", None, None, "DRS ENABLED"))
    assert drs is True


def test_13_race_control_parsing_real_messages():
    rows = [
        rc_row(1, "Other", None, None, "CAR 16 (LEC) TIME 1:17.076 DELETED - TRACK LIMITS AT TURN 3 LAP 2 15:04:59"),
        rc_row(2, "Other", None, None, "TURN 11 INCIDENT INVOLVING CARS 18 (STR) AND 5 (BOR) NOTED - CAUSING A COLLISION"),
        rc_row(3, "Other", None, None, "INCIDENT INVOLVING CAR 5 (BOR) NOTED - UNSAFE CONDITION"),
        rc_row(4, "Other", None, None, "FIA STEWARDS: INCIDENT INVOLVING CAR 5 (BOR) UNDER INVESTIGATION - UNSAFE CONDITION"),
        rc_row(5, "Other", None, None, "FIA STEWARDS: INCIDENT INVOLVING CAR 5 (BOR) WILL BE INVESTIGATED AFTER THE RACE - UNSAFE CONDITION"),
        rc_row(6, "Other", None, None, "FIA STEWARDS: TURN 11 INCIDENT INVOLVING CARS 18 (STR) AND 5 (BOR) REVIEWED NO FURTHER INVESTIGATION - CAUSING A COLLISION"),
        rc_row(7, "Other", None, None, "TURN 1 INCIDENT INVOLVING CARS 55 (SAI) AND 30 (LAW) NOTED"),
        rc_row(8, "Other", None, None, "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 55 (SAI) - CAUSING A COLLISION"),
        rc_row(9, "Other", None, None, "TURN 3 INCIDENT INVOLVING CARS 63 (RUS) AND 16 (LEC) NOTED - CAUSING A COLLISION"),
        rc_row(10, "Other", None, None, "CORRECTION:: TURN 3 INCIDENT INVOLVING CARS 12 (ANT) AND 16 (LEC) UNDER INVESTIGATION - CAUSING A COLLISION"),
        rc_row(11, "Other", None, None, "FIA STEWARDS: 10 SECOND TIME PENALTY FOR CAR 12 (ANT) - CAUSING A COLLISION"),
    ]
    p = engine.parse_race_control(rows)
    assert p["track_limits"][16] == 1
    assert p["penalty_s"][55] == 10 and p["penalty_s"][12] == 10
    assert p["investigations"][5] == 1          # opened three times, one incident, still open
    assert p["investigations"][18] == 0         # closed by "no further investigation"
    assert p["investigations"][55] == 0 and p["investigations"][30] == 0   # closed by the penalty
    assert p["investigations"][63] == 0         # replaced by the correction
    assert p["investigations"][16] == 0 and p["investigations"][12] == 0
    assert engine.tag_for(rows[0]) == "TRACK LIMITS" and engine.tag_for(rows[7]) == "PENALTY"
    assert engine.tag_for(rows[1]) == "INVESTIGATION"


def test_14_minisectors_constant_speed():
    n_ms = config.N_MINISECTORS
    t = np.arange(0.0, 101.0, 1.0)
    cum = 50.0 * t
    res = build_bundle.minisectors_for_lap(t, cum, 0.0, 100.0, 5000.0, n_ms)
    assert all(x is not None for x in res) and len(res) == n_ms
    durs = [x[1] for x in res]
    assert all(abs(d - 100.0 / n_ms) < 1e-9 for d in durs)
    assert abs(sum(durs) - 100.0) < 1e-9
    assert res[-1][0] == 100.0


def test_15_map_latest_sample_and_hold():
    ts, xs, ys = np.array([0.0, 10.0]), np.array([0.0, 100.0]), np.array([0.0, 50.0])
    x, y, age = engine.latest_sample(ts, xs, ys, 5.0)
    assert (x, y, age) == (0.0, 0.0, 5.0)         # nothing from after t is used
    assert engine.latest_sample(ts, xs, ys, -1.0) is None
    meta = make_meta(1)
    loc = [{"driver_number": 1, "t": 0.0, "x": 0.0, "y": 0.0}, {"driver_number": 1, "t": 20.0, "x": 9.0, "y": 9.0}]
    b = build(meta, location=loc)
    near = engine.state_at(b, config.MAP_HOLD_S - 1.0)["map"][0]
    far = engine.state_at(b, config.MAP_HOLD_S + 1.0)["map"][0]
    assert near["stale"] is False and far["stale"] is True
    assert far["x"] == 0.0 and far["y"] == 0.0
    assert engine.state_at(b, -5.0)["map"] == []


def test_16_drs_mapping():
    assert [engine.drs_label(c, 0.5) for c in (0, 1, 8, 10, 12, 14)] == ["OFF", "OFF", "ELIG", "ON", "ON", "ON"]
    assert engine.drs_label(12, config.DRS_STALE_S + 0.1) is None
    assert engine.drs_label(None, 0.0) is None
