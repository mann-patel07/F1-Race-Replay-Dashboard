import json
import re
import statistics
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import bundle_io
import build_site
import site_data
from tests import v4_helpers as H
from tests.helpers import needs_full_library, needs_races

playwright = pytest.importorskip("playwright.sync_api")
ROOT = H.ROOT
SITE = ROOT / "site"
RACES = SITE / "data" / "races"
SHOTS = ROOT / "screenshots"
PLAY_S = 30


@pytest.fixture(scope="module")
def pw():
    with playwright.sync_playwright() as p:
        yield p


def decode_js_put(path):
    text = Path(path).read_text(encoding="utf-8")
    start = text.index(", {") + 2
    return json.loads(text[start:text.rindex(");")])


# ---------------------------------------------------------------- 3: encoding

def hand_table():
    n = 8
    return pd.DataFrame({
        "t": [0.0, 1.0004, 2.0006, np.nan, 3.5, 4.0, 5.25, 6.125],
        "known_t": [-60.0, np.nan, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
        "driver_number": [1, 4, 44, 63, 81, 1, 4, 44],
        "u8_edge": [0, 1, 254, 254, 3, np.nan, 5, 6],
        "u16_low": [0, 255, 300, 65534, 7, 8, 9, 10],
        "u16_up": [0, 1, 2, 3, 254, 255, 5, 6],
        "i32_big": [0, 70000, 2147483646, -5, 1, 2, np.nan, 3],
        "i32_null_clash": [0, 1, 65535, 7, 8, 9, 10, 11],
        "negative": [-1, 2, 3, 4, 5, 6, 7, 8],
        "float": [0.5, 1.25, np.nan, 3.0, 1e-9, -2.5, 1e12, 7.75],
        "flag": [True, False, None, True, True, False, False, None],
        "name": ["a", None, "b", "a", "ccc", "b", None, "a"],
    }).assign(flag=lambda d: d["flag"].astype(object))


def js_decode(page, table):
    enc = site_data.encode_table(table)
    return page.evaluate("""(enc) => {
        const t = F1DATA.decodeTable(enc);
        const out = {};
        for (const k of Object.keys(t.cols)) out[k] = Array.from(t.cols[k], (x) => (typeof x === 'number' && x !== x ? null : x));
        return {n: t.n, cols: out};
    }""", enc), enc


def test_3_encoding_round_trip(pw):
    browser, page, _, _ = H.page_session(pw)
    page.goto("file://" + str(H.PAGE))
    page.wait_for_function("window.F1DATA")
    table = hand_table()
    got, enc = js_decode(page, table)
    assert got["n"] == len(table)
    for col in table.columns:
        want = [None if (x is None or (isinstance(x, float) and np.isnan(x))) else x for x in table[col]]
        if col in ("t", "known_t"):
            want = [None if x is None else round(x * 1000) / 1000 for x in want]
        for a, b in zip(want, got["cols"][col]):
            if isinstance(a, bool) or isinstance(a, str) or a is None:
                assert a == b, col
            else:
                assert b == pytest.approx(a, rel=1e-12, abs=1e-12), col
    types = {c: v["type"] for c, v in enc["cols"].items()}
    assert types["t"] == "ms" and types["known_t"] == "ms"
    assert types["driver_number"] == "u8" and types["u8_edge"] == "u8"
    assert types["u16_low"] == "u16" and types["u16_up"] == "u16"
    assert types["i32_big"] == "i32" and types["i32_null_clash"] == "i32"
    assert types["negative"] == "i32" and types["float"] == "f64" and types["flag"] == "bool" and types["name"] == "str"
    browser.close()


def test_3_integer_type_boundaries():
    def kind(values):
        return site_data.encode_column("x", pd.Series(values))["type"]
    assert kind([0, 254]) == "u8" and kind([0, 255]) == "u16"
    assert kind([0, 65534]) == "u16" and kind([0, 65535]) == "i32"
    assert kind([0, 2147483646]) == "i32" and kind([0, 2147483647]) == "f64"
    assert kind([-1, 5]) == "i32" and kind([1.5, 2]) == "f64"


def test_3_empty_table_and_split_tables(pw):
    browser, page, _, _ = H.page_session(pw)
    page.goto("file://" + str(H.PAGE))
    page.wait_for_function("window.F1DATA")
    empty = pd.DataFrame({"t": [], "driver_number": [], "message": []})
    got, _ = js_decode(page, empty)
    assert got["n"] == 0 and all(v == [] for v in got["cols"].values())
    big = pd.DataFrame({"driver_number": np.repeat([1, 4, 44, 63], 400), "t": np.tile(np.arange(400) / 4.0, 4),
                        "speed": np.arange(1600) * 0.37})
    files = site_data.table_files("_split", "samples", big, limit=8000)
    assert len(files) >= 2 and all(len(text.encode()) <= 8000 for _, text in files)
    assert [f for f, _ in files][0] == "samples.1.js"
    for _, text in files:
        page.evaluate("(txt) => (0, eval)(txt)", text)
    joined = page.evaluate("""() => { const t = F1DATA.decodeTable(F1DATA.get('_split', 'samples'));
        return {n: t.n, d: Array.from(t.cols.driver_number), s: Array.from(t.cols.speed)}; }""")
    ordered = big.sort_values("driver_number", kind="stable")
    assert joined["n"] == 1600 and joined["d"] == ordered["driver_number"].tolist()
    assert joined["s"] == pytest.approx(ordered["speed"].tolist())
    browser.close()


# ---------------------------------------------------------------- 4: helpers

def test_4_python_javascript_helpers(pw):
    browser, page, _, _ = H.page_session(pw)
    page.goto("file://" + str(H.PAGE))
    page.wait_for_function("window.F1APP && F1APP.engine")
    rounds = [0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5, 2.4999, 2.5001, 10.5, 0.0, -7.5]
    assert page.evaluate("(xs) => xs.map(F1APP.engine.roundHalfEven)", rounds) == [round(x) for x in rounds]
    pairs = [[7, 2], [-7, 2], [7, -2], [-7, -2], [0, 5], [-1, 3], [5, 5], [-5, 5]]
    assert page.evaluate("(ps) => ps.map(([a, b]) => F1APP.engine.floorDiv(a, b))", pairs) == [a // b for a, b in pairs]
    medians = [[3, 1, 2], [4, 1, 3, 2], [5.5, 1.5], [9], [2, 2, 2, 8], [0.1, 0.2, 0.3, 0.4]]
    got = page.evaluate("(ms) => ms.map((m) => F1APP.engine.median(m))", medians)
    assert got == pytest.approx([float(np.median(m)) for m in medians])
    values = [0.125, 0.375, 2.5, 1.005, 3.14159, -0.125, 12345.6789, 0.5, 1.5, 100.0]
    assert page.evaluate("(vs) => vs.map((v) => F1APP.engine.pyFixed(v, 2))", values) == [f"{v:.2f}" for v in values]
    assert page.evaluate("(vs) => vs.map((v) => F1APP.engine.pyFixed(v, 0))", [0.5, 1.5, 2.5, 3.5]) == [f"{v:.0f}" for v in [0.5, 1.5, 2.5, 3.5]]
    truncs = [2.7, -2.7, 0.0, -0.4, 5.0]
    assert page.evaluate("(xs) => xs.map(F1APP.engine.pyTrunc)", truncs) == [int(x) for x in truncs]
    arr = [1.0, 2.0, 2.0, 3.0]
    assert page.evaluate("(a) => [F1APP.engine.bisectRight(a, 2.0), F1APP.engine.bisectRight(a, 0.0), F1APP.engine.bisectRight(a, 9.0)]", arr) == \
        [int(np.searchsorted(arr, 2.0, side="right")), 0, 4]
    assert page.evaluate("() => F1APP.engine.bisectRight([1, 2, NaN], 5)") == int(np.searchsorted([1, 2, np.nan], 5, side="right"))
    browser.close()


# ---------------------------------------------------------------- 5: library

def library():
    return decode_js_put(SITE / "data" / "library.js")


def test_5_library_matches_the_files_on_disk():
    lib = library()
    ready = 0
    for races in lib["seasons"].values():
        for r in races:
            for s in r["sessions"]:
                if s["state"] in ("full", "lap_only"):
                    ready += 1
                    meta = decode_js_put(RACES / s["load_id"] / "meta.js")
                    for files in meta["files"].values():
                        for f in files:
                            assert (RACES / s["load_id"] / f).exists(), (s["load_id"], f)
                if s["state"] == "not_held":
                    assert s["date"] > date.today().isoformat()
                if s["date"] and s["date"] > date.today().isoformat():
                    assert s["state"] == "not_held"
    assert ready > 0
    ready_ids = {s["load_id"] for races in lib["seasons"].values() for r in races for s in r["sessions"] if s["load_id"]}
    for folder in bundle_io.BUNDLE_DIR.iterdir():
        if (folder / "meta.json").exists():
            rid = json.loads((folder / "meta.json").read_text()).get("race_id") or f"of_{folder.name}"
            assert (RACES / rid / "meta.js").exists(), f"{rid} has a bundle but no site folder"
    assert (RACES / "of_9920" / "meta.js").exists() and ready_ids


# ---------------------------------------------------------------- 6: opens from disk

def wait_state(page):
    page.wait_for_selector("#tower .trow", timeout=60000)
    time.sleep(1.5)


def scrub(page, fraction):
    page.evaluate("""(f) => { const s = document.getElementById('scrub'); s.value = String(Number(s.min) + f * (Number(s.max) - Number(s.min)));
        s.dispatchEvent(new Event('input', {bubbles: true})); }""", fraction)
    time.sleep(1.5)


def open_from_library(page, label_part, season):
    page.click("#btn-library")
    page.click(f"button[data-season='{season}']")
    chip = page.locator(f".chip2:has-text('{label_part}')").first
    chip.click()
    page.wait_for_function(f"document.getElementById('event-name').textContent.includes(' {season},')", timeout=60000)
    wait_state(page)


def test_6_opens_from_disk_and_works(pw):
    needs_full_library()
    SHOTS.mkdir(exist_ok=True)
    browser, page, blocked, errors = H.page_session(pw)
    page.goto("file://" + str(H.PAGE))
    wait_state(page)
    assert page.inner_text("#h-lap") != "n/a"
    assert page.evaluate("document.querySelectorAll('#tower .trow').length") >= 20
    page.click("#speeds button:nth-child(3)")
    page.click("#btn-play")
    time.sleep(PLAY_S)
    page.click("#btn-play")
    page.screenshot(path=str(SHOTS / "v4_replay.png"), full_page=True)
    before = page.inner_text("#h-lap")
    scrub(page, 0.5)
    middle = page.inner_text("#h-lap")
    scrub(page, 1.0)
    end = page.inner_text("#h-lap")
    assert middle != before or end != middle
    assert end.split(" / ")[0] == end.split(" / ")[1]
    rows = page.locator("#tower .trow")
    rows.nth(0).click()
    rows.nth(1).click()
    time.sleep(1.5)
    assert "km/h" in page.inner_text("#telemetry") and page.locator("#trace svg polyline").count() >= 1
    page.screenshot(path=str(SHOTS / "v4_telemetry.png"), full_page=True)
    page.click("#btn-library")
    page.wait_for_selector("#library .race-row")
    page.screenshot(path=str(SHOTS / "v4_library.png"))
    page.click("#lib-close")
    open_from_library(page, "Lap-only replay ready", 2012)
    assert "lap-only" in page.inner_text("#source-tag").lower() and page.locator("#banner").is_visible()
    scrub(page, 0.5)
    page.screenshot(path=str(SHOTS / "v4_lap_only.png"), full_page=True)
    open_from_library(page, "Sprint: Full replay ready", 2024)
    assert "Sprint" in page.inner_text("#event-name")
    scrub(page, 0.5)
    assert "#race=ff_2024_05_S" in page.evaluate("location.hash")
    assert blocked == [] and errors == [], (blocked, errors)
    browser.close()


# ---------------------------------------------------------------- 7: static rules

def test_7_static_rules():
    files = [p for p in list(SITE.rglob("*")) + [H.PAGE] if p.is_file() and p.name not in ("downsample.flag", ".DS_Store")]
    assert files
    for p in files:
        assert p.stat().st_size <= 45 * 1000 * 1000, p
        if "/data/races/" in p.as_posix():
            continue
        text = p.read_text(encoding="utf-8")
        for bad in ("fetch(", 'type="module"', "import ", "XMLHttpRequest", "http://", "https://"):
            assert bad not in text, (p.name, bad)
    for p in (RACES / "of_9920").glob("*.js"):
        text = p.read_text(encoding="utf-8")
        for bad in ("fetch(", "XMLHttpRequest"):
            assert bad not in text


def test_7_build_is_byte_identical(tmp_path):
    needs_races("of_9920", "jl_2012_01")
    for rid, bid in (("of_9920", "9920"), ("jl_2012_01", "jl_2012_01")):
        bundle = bundle_io.load_bundle(bid)
        a = site_data.race_files(bundle, rid)
        b = site_data.race_files(bundle_io.load_bundle(bid), rid)
        assert a == b
    one = tmp_path / "one"
    two = tmp_path / "two"
    for out in (one, two):
        build_site.write_race("jl_2012_01", site_data.race_files(bundle_io.load_bundle("jl_2012_01"), "jl_2012_01"), out)
    for f in (one / "jl_2012_01").iterdir():
        assert f.read_bytes() == (two / "jl_2012_01" / f.name).read_bytes()


# ---------------------------------------------------------------- 8: speed

def test_8_speed(pw):
    needs_full_library()
    biggest = max((p for p in RACES.iterdir() if p.name.startswith("ff_")), key=lambda p: sum(f.stat().st_size for f in p.iterdir()))
    browser, page, _, errors = H.page_session(pw)
    t0 = time.time()
    page.goto(f"file://{H.PAGE}#race={biggest.name}")
    page.wait_for_selector("#tower .trow", timeout=60000)
    first = time.time() - t0
    times = page.evaluate("""() => {
        const b = F1APP.current.bundle; const m = F1APP.current.meta; const out = [];
        for (let i = 0; i < 200; i += 1) {
            const t = m.replay_start_t + (m.replay_end_t - m.replay_start_t) * (i / 199);
            const a = performance.now(); F1APP.engine.stateAt(b, t); out.push(performance.now() - a);
        }
        return out; }""")
    median = statistics.median(times)
    text = f"Largest race {biggest.name}: first state after {first:.1f} s, median stateAt over 200 calls {median:.1f} ms (limits 10 s and 50 ms)."
    build_site_log(text)
    assert first < 10 and median < 50, text
    browser.close()


def build_site_log(text):
    path = ROOT / "RUN_LOG.md"
    body = path.read_text() if path.exists() else ""
    marker = "- Speed measurement:"
    lines = [l for l in body.split("\n") if not l.startswith(marker)]
    lines.append(f"{marker} {text}")
    path.write_text("\n".join(lines).rstrip("\n") + "\n")
