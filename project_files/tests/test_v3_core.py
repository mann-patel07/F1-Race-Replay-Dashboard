import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import archive
import bundle_io
import config
import engine
import f1_auth
import jobs
import points
import settings
from http_util import read_all_pages

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- points and ids

def test_1_points_table():
    assert points.points_table(2008, "Race")[0] == 10 and points.points_for_position(2008, "Race", 8) == 1
    assert points.points_for_position(2008, "Race", 9) == 0
    assert points.points_for_position(2012, "Race", 1) == 25 and points.points_for_position(2012, "Race", 10) == 1
    assert points.points_table(2025, "Race") == config.RACE_POINTS
    assert points.points_table(2021, "Sprint") == [3, 2, 1]
    assert points.points_table(2023, "Sprint") == [8, 7, 6, 5, 4, 3, 2, 1]
    assert points.fastest_lap_point(2021, "Race") and not points.fastest_lap_point(2012, "Race") and not points.fastest_lap_point(2025, "Race")
    assert not points.fastest_lap_point(2021, "Sprint")


def test_2_race_id_to_folder():
    assert bundle_io.bundle_folder("of_9920") == bundle_io.BUNDLE_DIR / "9920"
    assert bundle_io.bundle_folder("9920") == bundle_io.BUNDLE_DIR / "9920"
    assert bundle_io.bundle_folder("ff_2024_05_S") == bundle_io.BUNDLE_DIR / "ff_2024_05_S"
    assert bundle_io.bundle_folder("jl_2012_01") == bundle_io.BUNDLE_DIR / "jl_2012_01"
    assert bundle_io.make_race_id("fastf1", 2024, 5, "S") == "ff_2024_05_S"
    assert bundle_io.make_race_id("jolpica", 2012, 1) == "jl_2012_01"
    for bad in ("ff_2024_5", "xx_1", "ff_2024_05_Q", "../etc"):
        with pytest.raises(ValueError):
            bundle_io.parse_race_id(bad)


def test_4_result_status_rule():
    assert points.classify_status("Finished") == (False, False, False)
    assert points.classify_status("+1 Lap") == (False, False, False)
    assert points.classify_status("Lapped") == (False, False, False)
    assert points.classify_status("Retired") == (True, False, False)
    assert points.classify_status("Engine") == (True, False, False)
    assert points.classify_status("Disqualified") == (False, False, True)
    assert points.classify_status("Did not start") == (False, True, False)


def test_9_paging_helper_reads_three_pages():
    pages = {0: [1, 2], 100: [3, 4], 200: [5]}
    seen = []

    def fetch(url):
        offset = int(url.split("offset=")[1])
        seen.append(url)
        return {"MRData": {"total": "250", "items": pages[offset]}}

    out = read_all_pages(fetch, "http://example.test/x.json", lambda b: b["MRData"]["items"])
    assert out == [[1, 2], [3, 4], [5]] and len(seen) == 3 and "limit=100" in seen[0]


def test_10_library_index_sources():
    index = json.loads((ROOT / "data" / "library_index.json").read_text())
    for season, races in index["seasons"].items():
        for r in races:
            if int(season) <= 2020:
                assert r["sources"] == ["jolpica"], (season, r["round"])
            elif int(season) <= 2022:
                assert r["sources"] == ["jolpica", "fastf1"]
            if int(season) < 2023:
                assert "openf1" not in r["sources"]
    assert min(int(s) for s in index["seasons"]) == config.CLASSIC_FIRST_SEASON
    assert any("openf1" in r["sources"] for r in index["seasons"]["2025"])


def test_15_state_median_budget():
    b = bundle_io.load_bundle("of_9920")
    lo, hi = b["meta"]["replay_start_t"], b["meta"]["replay_end_t"]
    times = []
    for i in range(30):
        t0 = time.perf_counter()
        engine.state_at(b, lo + (hi - lo) * i / 29)
        times.append(time.perf_counter() - t0)
    assert sorted(times)[len(times) // 2] < config.STATE_MEDIAN_BUDGET_S


def test_21_requirements_and_run():
    lines = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip()]
    assert lines and all("==" in l for l in lines) and not any("fastf1" in l.lower() for l in lines)
    assert "fastf1==" in (ROOT / "requirements-archive.txt").read_text()
    assert "-r requirements.txt" in (ROOT / "requirements-archive.txt").read_text()
    assert "-r requirements-archive.txt" in (ROOT / "requirements-dev.txt").read_text()


def test_22_classic_order():
    seasons = {str(y): [{"date": f"{y}-03-01", "round": r} for r in (1, 2)] for y in range(2007, 2022)}
    existing = {"jl_2020_02", "jl_2015_01"}
    order = archive.classic_order({"seasons": seasons}, existing)
    assert order[0] == (2020, 1) and order[-1][0] == 2007
    assert (2020, 2) not in order and (2015, 1) not in order and (2021, 1) not in order
    years = [y for y, _ in order]
    assert years == sorted(years, reverse=True)


# ---------------------------------------------------------------- auth module

def test_13_f1tv_environment_and_stdin(monkeypatch):
    monkeypatch.setenv("F1_USERNAME", "someone")
    monkeypatch.setenv("F1_PASSWORD", "secret")
    env = f1_auth.clean_env()
    assert "F1_USERNAME" not in env and "F1_PASSWORD" not in env
    code = "import os, sys; print(os.environ.get('F1_USERNAME'), os.environ.get('F1_PASSWORD')); print(repr(sys.stdin.read()))"
    r = f1_auth.run_clean([sys.executable, "-c", code], 30)
    assert r.stdout.split("\n")[0] == "None None" and r.stdout.split("\n")[1] == "''"


# ---------------------------------------------------------------- server: health, shutdown, idle, archive start


# ---------------------------------------------------------------- job runner

def wait_until(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_11_jobs_run_one_at_a_time_in_queue_order(tmp_path):
    order, active, peak = [], [0], [0]

    def make(name):
        def handler(ctx, params):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            ctx.progress(f"{name} step", 1, 2)
            time.sleep(0.05)
            order.append(name)
            active[0] -= 1
            return {"ok": name}
        return handler

    runner = jobs.JobRunner({"a": make("a"), "b": make("b"), "c": make("c")}, state_path=tmp_path / "jobs.json")
    for k in ("a", "b", "c"):
        runner.submit(k)
    assert runner.wait_idle(10)
    assert order == ["a", "b", "c"] and peak[0] == 1
    saved = json.loads((tmp_path / "jobs.json").read_text())
    assert [j["state"] for j in saved["jobs"]] == ["done"] * 3 and saved["jobs"][0]["text"]
    runner.stop()


def test_11_cancel_and_failing_job_leaves_no_half_built_folder(tmp_path):
    final = tmp_path / "bundle" / "x"

    def slow(ctx, params):
        for _ in range(200):
            if ctx.cancelled():
                return None
            time.sleep(0.02)
        return {"ok": 1}

    def failing(ctx, params):
        with jobs.atomic_dir(final) as tmp:
            (tmp / "partial.txt").write_text("half")
            raise RuntimeError("boom")

    runner = jobs.JobRunner({"slow": slow, "failing": failing}, state_path=tmp_path / "jobs.json")
    runner.submit("slow")
    assert wait_until(lambda: runner.running() is not None)
    runner.cancel()
    assert wait_until(lambda: not runner.busy())
    assert runner.jobs[0]["state"] == "cancelled"
    runner.submit("failing")
    assert wait_until(lambda: not runner.busy())
    assert runner.jobs[1]["state"] == "failed" and "boom" in runner.jobs[1]["error"]
    assert not final.exists() and not final.with_name("x_tmp").exists()
    runner.stop()


@pytest.fixture()
def fake_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(archive, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(archive, "INDEX", tmp_path / "archive" / "index.json")
    monkeypatch.setattr(archive, "SESSIONS", tmp_path / "archive" / "sessions.json")
    monkeypatch.setattr(archive, "free_gb", lambda path=None: 500.0)
    monkeypatch.setattr(archive, "cleanup_cache", lambda: None)
    monkeypatch.setattr(archive, "build_replay", lambda *a: None)
    monkeypatch.setattr(config, "FASTF1_RATE_WAIT_S", 0)
    monkeypatch.setattr(config, "RETRY_AFTER_FAILURE_S", 0)
    sessions = [{"year": y, "round": r, "event": f"E{r}", "session": s, "date": f"{y}-03-0{r}T12:00:00+00:00"}
                for y in (2022, 2024) for r in (1, 2) for s in ("Race", "Sprint")]
    monkeypatch.setattr(archive, "list_sessions", lambda: sessions)
    calls = []

    class Ctx:
        def progress(self, *a, **k):
            pass

        def cancelled(self):
            return False

        def sleep(self, s):
            return True

    return calls, Ctx()


def test_12_archive_order_and_resume_skips_ok(fake_archive, monkeypatch):
    calls, ctx = fake_archive
    sessions = archive.list_sessions()
    phases = archive.order_sessions(sessions)
    assert [s["session"] for s in phases[0]] == ["Race"] * 4 and [s["session"] for s in phases[1]] == ["Sprint"] * 4
    assert [(s["year"], s["round"]) for s in phases[0]] == [(2024, 2), (2024, 1), (2022, 2), (2022, 1)]

    def one(year, rnd, name):
        calls.append((year, rnd, name))
        return (0, "") if len(calls) <= 3 else (3, "not signed in")

    monkeypatch.setattr(archive, "run_one_process", one)
    with pytest.raises(jobs.JobPaused) as exc:
        archive.run_archive(ctx)
    assert exc.value.state_text == "sign-in needed"
    assert calls[:3] == [(2024, 2, "Race"), (2024, 1, "Race"), (2022, 2, "Race")]
    calls.clear()
    monkeypatch.setattr(archive, "run_one_process", lambda y, r, n: (calls.append((y, r, n)) or (0, "")))
    archive.run_archive(ctx)
    assert (2024, 2, "Race") not in calls and (2024, 1, "Race") not in calls and (2022, 2, "Race") not in calls
    assert calls[0] == (2022, 1, "Race") and len(calls) == 5


def test_11_exit_code_2_waits_then_retries(fake_archive, monkeypatch):
    calls, ctx = fake_archive
    codes = iter([2, 2, 0] + [0] * 20)
    monkeypatch.setattr(archive, "run_one_process", lambda y, r, n: (calls.append((y, r, n)) or (next(codes), "")))
    archive.run_archive(ctx)
    assert calls[0] == calls[1] == calls[2] == (2024, 2, "Race")


def test_16_missing_settings_reads_on_and_runner_refuses_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "none.json")
    assert settings.read()["f1tv_downloads"] == "on" and settings.f1tv_enabled()
    settings.set_f1tv("off")
    started, probed, ran = [], [], []
    monkeypatch.setattr(archive, "run_one_process", lambda *a: started.append(a))
    monkeypatch.setattr(f1_auth, "probe", lambda: probed.append(1))
    runner = jobs.JobRunner({"fast": (archive.run_archive, True), "plain": lambda ctx, p: ran.append(1) or {"ok": 1}},
                            state_path=tmp_path / "jobs.json")
    runner.submit("fast")
    runner.submit("plain")
    assert runner.wait_idle(10)
    assert runner.jobs[0]["state"] == "failed" and runner.jobs[0]["error"] == "F1TV downloads are off"
    assert runner.jobs[1]["state"] == "done" and ran == [1]
    assert started == [] and probed == []
    runner.stop()


def test_16_switch_turns_off_only_after_a_clean_archive(fake_archive, monkeypatch):
    calls, ctx = fake_archive
    monkeypatch.setattr(archive, "run_one_process", lambda y, r, n: (0, "") if r != 2 else (4, "no data"))
    out = archive.run_archive(ctx)
    assert out["finished"] is True and settings.read()["f1tv_downloads"] == "off"
    settings.set_f1tv("on")
    shutil.rmtree(archive.ARCHIVE)
    monkeypatch.setattr(archive, "run_one_process", lambda y, r, n: (0, "") if (y, r) != (2022, 1) else (5, "broken"))
    out = archive.run_archive(ctx)
    assert out["finished"] is False and settings.read()["f1tv_downloads"] == "on"


# ---------------------------------------------------------------- routes that need a bundle


def test_cross_source_check_file():
    path = ROOT / "results" / "cross_source_check.json"
    if not path.exists():
        pytest.skip("results/cross_source_check.json is written by the verification run, which needs the F1TV sign-in")
    data = json.loads(path.read_text())
    assert data["lap_duration_median_abs_diff_s"] < 0.005


def test_start_points_survive_standings_without_position():
    import jolpica

    def drv(code, pts, pos=None):
        d = {"points": pts, "wins": "0", "positionText": "-" if pos is None else str(pos),
             "Driver": {"driverId": code.lower(), "code": code}}
        if pos is not None:
            d["position"] = str(pos)
        return d

    pages = {
        "2021/2/results.json?limit=100": {"MRData": {"RaceTable": {"Races": [{"Results": [
            {"Driver": {"driverId": "aaa", "code": "AAA"}, "Constructor": {"constructorId": "team_a"}},
            {"Driver": {"driverId": "bbb", "code": "BBB"}, "Constructor": {"constructorId": "team_b"}}]}]}}},
        "2021/1/driverStandings.json?limit=100": {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [
            drv("AAA", "25", 1), drv("BBB", "0")]}]}}},
        "2021/1/constructorStandings.json?limit=100": {"MRData": {"StandingsTable": {"StandingsLists": [{"ConstructorStandings": [
            {"position": "1", "points": "25", "Constructor": {"constructorId": "team_a"}},
            {"points": "0", "Constructor": {"constructorId": "team_b"}}]}]}}},
    }
    seen = []
    raw = jolpica.fetch_start_points(2021, 2, False, fetch=lambda url: seen.append(url) or pages[url.split("f1/")[1]])
    assert raw["drivers"]["AAA"] == [25.0, 1] and raw["drivers"]["BBB"] == [0.0, 2]
    assert raw["constructors"]["team_b"] == [0.0, 2] and all(u.startswith(config.JOLPICA_BASE) for u in seen)
