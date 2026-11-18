import json

import numpy as np
import pytest

import bundle_io
import engine
from tests import v4_helpers as H
from tests.helpers import race_mark

playwright = pytest.importorskip("playwright.sync_api")


def newest(prefix_year, which):
    ids = sorted(p.name for p in bundle_io.BUNDLE_DIR.glob(f"ff_{prefix_year}_*_R") if (H.ROOT / "site" / "data" / "races" / p.name / "meta.js").exists())
    if not ids:
        return f"ff_{prefix_year}_not_on_disk"
    return ids[-1] if which == "newest" else ids[0]


def race_list():
    return [pytest.param(r, marks=race_mark(r)) for r in ["of_9920", newest(2025, "newest"), newest(2021, "oldest"), "ff_2024_05_S", "jl_2012_01"]]


@pytest.fixture(scope="module")
def page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto("file://" + str(H.PAGE))
        pg.wait_for_function("window.F1APP && F1APP.engine && window.F1DATA")
        yield pg
        browser.close()


def run_js(page, race, times, mode, drivers=None):
    return json.loads(page.evaluate(H.JS_RUN, [race, times, mode, drivers or []]))


@pytest.mark.parametrize("race", race_list())
def test_1_state_parity(page, race):
    bundle = H.site_bundle("9920" if race == "of_9920" else race, bundle_io)
    times = H.replay_times(bundle)
    js = run_js(page, race, times, "state")
    diffs = []
    for t, got in zip(times, js):
        H.compare(H.python_state(bundle, t), got, f"t={t}", diffs)
    assert not diffs, diffs[:10]


@pytest.mark.parametrize("race", race_list())
def test_1_telemetry_trace_and_forecast_parity(page, race):
    bundle = H.site_bundle("9920" if race == "of_9920" else race, bundle_io)
    meta = bundle["meta"]
    drivers = [int(d["driver_number"]) for d in meta["drivers"]][:3]
    times = [float(x) for x in np.linspace(meta["race_start_t"] + 600, meta["replay_end_t"], 10)]
    js = run_js(page, race, times, "telemetry", drivers)
    diffs = []
    for t, got in zip(times, js):
        for d, (tel, trace) in zip(drivers, got):
            H.compare(json.loads(json.dumps(engine._clean(engine.telemetry_at(bundle, t, d)))), tel, f"tel t={t} d={d}", diffs)
            H.compare(json.loads(json.dumps(engine._clean(engine.lap_trace(bundle, t, d)))), trace, f"trace t={t} d={d}", diffs)
    fc = run_js(page, race, times, "forecast")
    for t, got in zip(times, fc):
        H.compare(json.loads(json.dumps(engine._forecast(bundle, t))), got, f"forecast t={t}", diffs)
    assert not diffs, diffs[:10]


@pytest.mark.parametrize("race", race_list())
def test_2_no_look_ahead_in_javascript(page, race):
    bundle = H.site_bundle("9920" if race == "of_9920" else race, bundle_io)
    meta = bundle["meta"]
    times = [float(x) for x in np.linspace(meta["replay_start_t"], meta["replay_end_t"], 8)]
    pairs = run_js(page, race, times, "lookahead")
    diffs = []
    for t, (a, b) in zip(times, pairs):
        H.compare(a, b, f"t={t}", diffs)
    assert not diffs, diffs[:10]
