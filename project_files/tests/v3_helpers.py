"""Hand-built archives and raw classic folders for the Version 3 tests. Nothing here needs the network."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

T0 = "2025-03-02T12:00:00+00:00"


def td(seconds):
    return pd.to_timedelta(seconds, unit="s") if seconds is not None else pd.NaT


def make_archive(base, with_stream=True, year=2025, rnd=1):
    """3 drivers, 5 laps, one pit stop (driver 44 after lap 2), timing stream, race control, weather, car and position samples."""
    folder = Path(base) / str(year) / f"{rnd:02d}" / "R"
    folder.mkdir(parents=True)
    drivers = {1: "VER", 44: "HAM", 63: "RUS"}
    rows = []
    for n in drivers:
        t = 100.0
        for lap in range(1, 6):
            dur = 90.0 + (n % 5)
            pit_in = pit_out = None
            stint, comp, life = 1, "SOFT", float(lap + 2)
            if n == 44:
                if lap == 2:
                    pit_in = t + dur - 3
                if lap >= 3:
                    stint, comp, life = 2, "HARD", float(lap - 2)
                if lap == 3:
                    pit_out = t + 20
            if n == 63:
                life = np.nan
            rows.append({
                "Time": td(t + dur), "Driver": drivers[n], "DriverNumber": str(n), "LapTime": td(dur), "LapNumber": float(lap),
                "Stint": float(stint), "PitOutTime": td(pit_out), "PitInTime": td(pit_in),
                "Sector1Time": td(30.0), "Sector2Time": td(30.0), "Sector3Time": td(dur - 60.0),
                "Sector1SessionTime": td(t + 30.0), "Sector2SessionTime": td(t + 60.0), "Sector3SessionTime": td(t + dur),
                "Compound": comp, "TyreLife": life, "Team": f"Team{n}", "LapStartTime": td(t), "Position": float(list(drivers).index(n) + 1)})
            t += dur
    pd.DataFrame(rows).to_parquet(folder / "laps.parquet")
    pd.DataFrame({
        "DriverNumber": [str(n) for n in drivers], "BroadcastName": list(drivers.values()), "Abbreviation": list(drivers.values()),
        "TeamName": [f"Team{n}" for n in drivers], "TeamColor": ["112233", "", "AABBCC"], "FullName": [f"Driver {n}" for n in drivers],
        "Position": [1.0, 2.0, 3.0], "GridPosition": [1.0, 0.0, 3.0], "Status": ["Finished", "+1 Lap", "Retired"],
        "Laps": [5.0, 5.0, 3.0]}).to_parquet(folder / "results.parquet")
    car = []
    for n in drivers:
        for k in range(0, 700, 2):
            car.append({"driver_number": n, "SessionTime": td(100 + k * 0.5), "Speed": 200.0 + (k % 7), "DRS": [0, 8, 12][k % 3],
                        "Throttle": 80.0, "Brake": False, "nGear": 6, "RPM": 11000.0})
    pd.DataFrame(car).to_parquet(folder / "car_data.parquet")
    pos = []
    for n in drivers:
        for k in range(0, 700, 2):
            ang = k / 100.0
            pos.append({"driver_number": n, "SessionTime": td(100 + k * 0.5), "X": 1000 * np.cos(ang) + n, "Y": 1000 * np.sin(ang), "Z": 0.0})
    pos.append({"driver_number": 1, "SessionTime": td(50), "X": 0.0, "Y": 0.0, "Z": 0.0})
    pd.DataFrame(pos).to_parquet(folder / "pos_data.parquet")
    pd.DataFrame({"Time": [pd.Timestamp("2025-03-02 12:01:00"), pd.Timestamp("2025-03-02 12:03:00")],
                  "Category": ["Flag", "SafetyCar"], "Message": ["YELLOW IN TRACK SECTOR 4", "SAFETY CAR DEPLOYED"],
                  "Status": [None, None], "Flag": ["YELLOW", None], "Scope": ["Sector", None], "Sector": [4.0, np.nan],
                  "RacingNumber": [None, None], "Lap": [1, 2]}).to_parquet(folder / "race_control.parquet")
    pd.DataFrame({"Time": [td(10), td(70)], "AirTemp": [20.0, 21.0], "Humidity": [50.0, 51.0], "Pressure": [1000.0, 1000.5],
                  "Rainfall": [False, True], "TrackTemp": [30.0, 31.0], "WindDirection": [90, 100], "WindSpeed": [2.0, 3.0]}
                 ).to_parquet(folder / "weather.parquet")
    if with_stream:
        stream = []
        for n, name in drivers.items():
            stream.append({"Time": td(120), "Driver": str(n), "Position": list(drivers).index(n) + 1,
                           "GapToLeader": "LAP 1" if n == 1 else "+1.500" if n == 44 else "1 L",
                           "IntervalToPositionAhead": "LAP 1" if n == 1 else "+1.500" if n == 44 else "1 L"})
            stream.append({"Time": td(150), "Driver": str(n), "Position": list(drivers).index(n) + 1,
                           "GapToLeader": None if n == 1 else "+3.250", "IntervalToPositionAhead": None if n == 1 else "2.000"})
        pd.DataFrame(stream).to_parquet(folder / "timing_stream.parquet")
    pd.DataFrame({"X": [1.0, 2.0], "Y": [3.0, 4.0], "Number": [1, 2], "Letter": ["", "a"], "Angle": [0.0, 0.0], "Distance": [0.0, 1.0]}
                 ).to_parquet(folder / "corners.parquet")
    (folder / "info.json").write_text(json.dumps({"year": year, "round": rnd, "event": "Test Grand Prix", "official_event": "TEST",
                                                  "country": "Testland", "location": "Testville", "session": "Race", "t0_date": T0,
                                                  "total_laps": 5, "rows": {}}))
    return folder


def stub_points(year, rnd, with_sprint, drivers, log):
    return {"source": "stub standings", "drivers": {str(d["driver_number"]): {"points": 10.0 * (i + 1), "position": 3 - i}
                                                    for i, d in enumerate(drivers)},
            "teams": {d["team_name"]: {"points": 5.0, "position": i + 1} for i, d in enumerate(drivers)}}


def make_classic_raw(base, season=2012, rnd=1):
    folder = Path(base) / f"{season}_{rnd:02d}"
    folder.mkdir(parents=True)

    def drv(i, code, fam):
        return {"driverId": fam.lower(), "code": code, "givenName": "G", "familyName": fam, "permanentNumber": str(i)}

    results = [("A", "AAA", "Alpha", 1, 1, 4, "Finished"), ("B", "BBB", "Bravo", 2, 2, 4, "Finished"), ("C", "CCC", "Charlie", 3, 3, 3, "+1 Lap")]
    body = {"MRData": {"total": "3", "RaceTable": {"Races": [{
        "season": str(season), "round": str(rnd), "raceName": "Test Grand Prix", "date": "2012-03-18", "time": "05:00:00Z",
        "Circuit": {"circuitName": "Test Circuit", "Location": {"lat": "1.0", "long": "2.0", "locality": "Testville", "country": "Testland"}},
        "Results": [{"number": str(num), "position": str(pos), "grid": str(grid), "laps": str(laps), "status": st,
                     "Driver": drv(num, code, fam), "Constructor": {"constructorId": f"team_{fam.lower()}", "name": f"Team {fam}"}}
                    for (_, code, fam, num, pos, laps, st), grid in zip(results, (1, 3, 2))]}]}}}
    (folder / "results.json").write_text(json.dumps(body))
    durations = {"alpha": [100.0] * 4, "bravo": [110.0] * 4, "charlie": [150.0] * 3}
    laps = []
    for k in range(1, 5):
        timings = []
        for pos, fam in enumerate(("alpha", "bravo", "charlie"), 1):
            if k <= len(durations[fam]):
                t = durations[fam][k - 1]
                timings.append({"driverId": fam, "position": str(pos), "time": f"{int(t // 60)}:{t % 60:06.3f}"})
        laps.append({"number": str(k), "Timings": timings})
    (folder / "laps.json").write_text(json.dumps(laps))
    (folder / "pitstops.json").write_text(json.dumps([
        {"driverId": "bravo", "stop": "1", "lap": "2", "time": "10:00:00", "duration": "22.5"},
        {"driverId": "charlie", "stop": "1", "lap": "1", "time": "10:00:00", "duration": "2:30.000"},
        {"driverId": "alpha", "stop": "1", "lap": "3", "time": "10:00:00", "duration": "1:05.000"}]))
    (folder / "qualifying.json").write_text("{}")
    (folder / "start_points.json").write_text(json.dumps({
        "source": "Jolpica-F1 standings after round 0 (test)", "drivers": {"AAA": [10.0, 1], "BBB": [8.0, 2], "CCC": [6.0, 3]},
        "constructors": {"team_alpha": [10.0, 1], "team_bravo": [8.0, 2], "team_charlie": [6.0, 3]},
        "driver_constructor": {"AAA": "team_alpha", "BBB": "team_bravo", "CCC": "team_charlie"}}))
    (folder / "entry.json").write_text(json.dumps({"date": "2012-03-18", "time": "05:00:00Z", "lat": 1.0, "lon": 2.0, "circuit": "Test Circuit"}))
    (folder / "weather.json").write_text(json.dumps({"hourly": {
        "time": ["2012-03-18T04:00", "2012-03-18T05:00"], "temperature_2m": [20.0, 22.0], "relative_humidity_2m": [60, 55],
        "precipitation": [0.0, 0.4], "wind_speed_10m": [5.0, 6.0]}}))
    return folder
