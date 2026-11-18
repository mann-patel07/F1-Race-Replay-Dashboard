"""Layout tests for the redesigned page: playwright, from disk, no network."""
import time

import pytest

from tests import v4_helpers as H
from tests.helpers import needs_races

playwright = pytest.importorskip("playwright.sync_api")

FULL = ("ff_2025_24_R", 5000)
LAP = ("jl_2014_07", 2000)
SIZES = [(1280, 800), (1440, 900), (1920, 1080)]
CASES = [(r, s) for r in (FULL, LAP) for s in SIZES]
IDS = [f"{'full' if r is FULL else 'lap'}-{s[0]}" for r, s in CASES]
FOOTER = "Unofficial replay of past data from OpenF1, Jolpica-F1 and Open-Meteo. Not associated with Formula 1, the FIA or any team."


@pytest.fixture(scope="module")
def pw():
    with playwright.sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def pages(pw):
    cache = {}
    opened = []

    def get(race, size):
        needs_races(race[0])
        key = (race[0], size)
        if key not in cache:
            browser, page, blocked, errors = H.page_session(pw, width=size[0], height=size[1])
            page.goto(f"file://{H.PAGE}#race={race[0]}&t={race[1]}")
            page.wait_for_selector("#tower .trow", timeout=90000)
            time.sleep(2)
            cache[key] = page
            opened.append(browser)
        return cache[key]
    yield get
    for b in opened:
        b.close()


def rect(page, sel):
    return page.evaluate("""(s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
        return {x: r.x, y: r.y + window.scrollY, w: r.width, h: r.height, b: r.bottom + window.scrollY}; }""", sel)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_1_no_sideways_scroll(pages, case):
    page = pages(*case)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_2_top_bar_one_row(pages, case):
    assert rect(pages(*case), "#topbar")["h"] <= 52.5


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_3_stage_has_no_gap(pages, case):
    page = pages(*case)
    tower = rect(page, "#tower-panel")
    right = rect(page, "#right")
    assert abs(tower["b"] - right["b"]) <= 2
    panels = page.evaluate("""() => [...document.querySelectorAll('#right > .panel')].filter((p) => p.offsetParent !== null)
        .map((p) => { const r = p.getBoundingClientRect(); return [r.top, r.bottom]; })""")
    assert len(panels) >= 2
    for a, b in zip(panels, panels[1:]):
        assert abs(b[0] - a[1] - 12) <= 0.6, panels
    last = page.evaluate("""() => { const rows = [...document.querySelectorAll('#tower .trow')];
        const body = document.getElementById('tower-scroll').getBoundingClientRect();
        const bottom = Math.max(...rows.map((r) => r.getBoundingClientRect().bottom));
        return {gap: body.bottom - bottom, rowH: rows[0].getBoundingClientRect().height, inside: bottom <= body.bottom + 1}; }""")
    assert last["inside"]
    assert last["gap"] <= last["rowH"] + 2, last


@pytest.mark.parametrize("case", [c for c in CASES if c[1][0] >= 1440], ids=[i for c, i in zip(CASES, IDS) if c[1][0] >= 1440])
def test_4_no_clipped_text(pages, case):
    page = pages(*case)
    bad = page.evaluate("""() => { const out = [];
        for (const e of document.querySelectorAll('#tower .trow *, #tower .thead *, #tower .thead, #headerbar *')) {
          if (e.id === 'h-ticker') continue;
          if (getComputedStyle(e).textOverflow === 'ellipsis') continue;
          if (e.scrollWidth > e.clientWidth + 1 && e.clientWidth > 0) out.push(e.className + ':' + e.textContent.slice(0, 20) + ' ' + e.scrollWidth + '>' + e.clientWidth);
        }
        for (const row of document.querySelectorAll('#tower .trow, #tower .thead')) {
          for (const c of row.children) {
            if (getComputedStyle(c).display === 'none') continue;
            if (getComputedStyle(c).textOverflow === 'ellipsis') continue;
            if (c.scrollWidth > c.clientWidth + 1) out.push('cell ' + c.className + ' ' + c.scrollWidth + '>' + c.clientWidth);
          }
        }
        return out.slice(0, 8); }""")
    assert not bad, bad


@pytest.mark.parametrize("race", [FULL, LAP], ids=["full", "lap"])
def test_5_charts_fill_panels(pages, race):
    page = pages(race, (1440, 900))
    measure = """() => ['stintchart', 'pacechart'].map((id) => { const c = document.getElementById(id); const s = c.querySelector('svg.chart-svg');
        return [id, c.clientWidth, s ? s.getBoundingClientRect().width : null]; })"""
    for _, cw, sw in page.evaluate(measure):
        assert sw is not None and abs(cw - sw) <= 2, (cw, sw)
    page.set_viewport_size({"width": 1920, "height": 1080})
    time.sleep(1.5)
    got = page.evaluate(measure)
    page.set_viewport_size({"width": 1440, "height": 900})
    for _, cw, sw in got:
        assert sw is not None and abs(cw - sw) <= 2, (cw, sw)
    assert got[0][1] > 1300


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_6_rows_are_even(pages, case):
    page = pages(*case)
    rows = page.evaluate("""() => { const m = {};
        for (const p of document.querySelectorAll('#analysis > .panel')) { if (p.offsetParent === null) continue;
          const r = p.getBoundingClientRect(); const k = Math.round(r.top + window.scrollY); (m[k] = m[k] || []).push(r.height); }
        return Object.values(m); }""")
    assert len(rows) >= 3
    for heights in rows:
        assert max(heights) - min(heights) <= 1, heights


def test_7_library_dates_line_up(pages):
    page = pages(FULL, (1440, 900))
    page.click("#btn-library")
    page.wait_for_selector("#library .race-row")
    xs = page.evaluate("[...document.querySelectorAll('#library .race-row > span:nth-child(3)')].map((e) => e.getBoundingClientRect().x)")
    assert len(xs) >= 5 and max(xs) - min(xs) <= 1
    page.keyboard.press("Escape")
    assert page.evaluate("document.getElementById('library').hidden")


def test_8_tabs_work(pw):
    needs_races(FULL[0])
    browser, page, _, _ = H.page_session(pw, width=1440, height=900)
    page.goto(f"file://{H.PAGE}#race={FULL[0]}&t={FULL[1]}")
    page.wait_for_selector("#tower .trow", timeout=90000)
    time.sleep(1.5)
    vis = lambda sel: page.evaluate(f"document.querySelector('{sel}').offsetParent !== null")
    assert vis("#pane-telemetry") and not vis("#pane-feed") and not vis("#pane-weather")
    page.click("#info-panel .tabs button[data-tab=feed]")
    assert vis("#pane-feed") and not vis("#pane-telemetry")
    page.click("#info-panel .tabs button[data-tab=weather]")
    assert vis("#pane-weather") and vis("#forecast-wrap")
    browser.close()


def test_9_lap_only_right_column(pages):
    page = pages(LAP, (1440, 900))
    titles = page.evaluate("[...document.querySelectorAll('#right > .panel')].filter((p) => p.offsetParent !== null).map((p) => p.querySelector('h2').textContent.trim())")
    assert titles == ["Weather now", "Pit stops", "Not available for this race"], titles
    for sel in ("#map-panel", "#strategy-panel", "#tyreset-panel", "#pane-telemetry", "#pane-feed", "#forecast-wrap"):
        assert page.evaluate(f"document.querySelector('{sel}').offsetParent === null"), sel
    assert page.evaluate("document.querySelectorAll('#missing-list li').length") >= 1


def test_10_labels_stay(pages):
    full = pages(FULL, (1440, 900)).evaluate("document.body.textContent")
    for s in ("EST", "heuristic", "Replay of past data, not live", FOOTER, "Mini-sectors are computed from car speed"):
        assert s in full, s
    lap = pages(LAP, (1440, 900)).evaluate("document.body.textContent")
    for s in ("computed", "EST", "Replay of past data, not live", FOOTER, "safety car laps not excluded", "not available for this race"):
        assert s in lap, s


TOWER_GAP_JS = """() => {
  let min = 1e9, where = '';
  const rows = [document.querySelector('#tower .thead'), ...document.querySelectorAll('#tower .trow')];
  for (const row of rows) {
    let prev = null;
    for (const c of row.children) {
      if (getComputedStyle(c).display === 'none' || c.classList.contains('c-bar')) continue;
      if (!c.textContent.trim() && !c.querySelector('i')) continue;
      const range = document.createRange(); range.selectNodeContents(c);
      const r = range.getBoundingClientRect(); const box = c.getBoundingClientRect();
      const clip = getComputedStyle(c).overflowX === 'hidden';
      const left = clip ? Math.max(r.left, box.left) : r.left;
      const right = clip ? Math.min(r.right, box.right) : r.right;
      if (right - left <= 0) continue;
      if (prev && left - prev.right < min) { min = left - prev.right; where = prev.name + '|' + c.className.split(' ')[0]; }
      prev = {right, name: c.className.split(' ')[0]};
    }
  }
  return [min, where];
}"""


@pytest.mark.parametrize("size", SIZES, ids=[str(s[0]) for s in SIZES])
def test_tower_columns_do_not_touch(pw, size):
    needs_races("ff_2026_20_R")
    browser, page, _, _ = H.page_session(pw, width=size[0], height=size[1])
    page.goto(f"file://{H.PAGE}#race=ff_2026_20_R")
    page.wait_for_selector("#tower .trow", timeout=90000)
    time.sleep(1.5)
    page.evaluate("""() => { const s = document.getElementById('scrub'); s.value = String(Number(s.min) + 0.5 * (Number(s.max) - Number(s.min)));
        s.dispatchEvent(new Event('input', {bubbles: true})); }""")
    time.sleep(1.5)
    gap, where = page.evaluate(TOWER_GAP_JS)
    browser.close()
    assert gap >= 4 - 0.01, (gap, where)
