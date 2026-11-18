"""Helpers for the website tests: compare Python and JavaScript results, drive the page with Playwright."""
import json
import math
from pathlib import Path

import numpy as np

import engine
import site_data

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT.parent / "click_here.html"
TOL = 1e-6


def compare(a, b, path="", out=None, limit=10):
    """Collect differences between two JSON values: same keys, same lengths, equal strings, numbers within TOL."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if a is None or b is None:
        if a is not b:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, bool) or isinstance(b, bool):
        if a is not b:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not math.isclose(a, b, rel_tol=0, abs_tol=TOL) and not math.isclose(a, b, rel_tol=1e-9):
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            out.append(f"{path}: keys differ {sorted(set(a) ^ set(b))[:6]}")
        for k in a:
            if k in b:
                compare(a[k], b[k], f"{path}/{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: lengths {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


def python_state(bundle, t):
    """What /api/state returned: state_at, then hide_for_kind, passed through a JSON round trip."""
    state = engine.hide_for_kind(engine.state_at(bundle, t), bundle["meta"].get("kind", "full"))
    return json.loads(json.dumps(engine._clean(state), allow_nan=False))


def site_bundle(bundle_id, bundle_io):
    """The Python bundle exactly as the website data was built (rounded times, same size rule)."""
    from build_site import FLAG
    return site_data.prepare_for_site(bundle_io.load_bundle(bundle_id), downsample=FLAG.exists())


def replay_times(bundle, n=60):
    meta = bundle["meta"]
    lo, hi = meta["replay_start_t"], meta["replay_end_t"]
    times = [float(x) for x in np.linspace(lo, hi, n)]
    times.append(float(meta["race_start_t"]))
    pit = bundle["tables"]["pit"]
    if len(pit):
        times.append(float(pit["t"].min()))
    laps = bundle["tables"]["laps"]
    times.append(float(laps["lap_end_t"].max()))
    return times


JS_RUN = """
async ([raceId, times, mode, drivers]) => {
  const E = F1APP.engine;
  const loaded = await F1DATA.load(raceId);
  const bundle = E.makeBundle(loaded.meta, loaded.tables, loaded.extra);
  const kind = loaded.meta.kind || 'full';
  const out = [];
  for (const t of times) {
    if (mode === 'state') out.push(E.hideForKind(E.stateAt(bundle, t), kind));
    else if (mode === 'lookahead') {
      const a = E.hideForKind(E.stateAt(bundle, t), kind);
      const b = E.hideForKind(E.stateAt(E.truncate(bundle, t), t), kind);
      out.push([a, b]);
    } else if (mode === 'telemetry') out.push(drivers.map((d) => [E.telemetryAt(bundle, t, d), E.lapTrace(bundle, t, d)]));
    else if (mode === 'forecast') out.push(E.forecast(bundle, t));
  }
  return JSON.stringify(out);
}
"""


def page_session(playwright, blocked=None, errors=None, width=1440, height=900, browser_name="chromium"):
    """Open click_here.html from disk. Every request that is not a file request is blocked and logged."""
    browser = getattr(playwright, browser_name).launch()
    page = browser.new_page(viewport={"width": width, "height": height})
    blocked = [] if blocked is None else blocked
    errors = [] if errors is None else errors
    page.on("console", lambda m: errors.append(f"console {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def route(r):
        if r.request.url.startswith("file:"):
            r.continue_()
        else:
            blocked.append(r.request.url)
            r.abort()
    page.route("**/*", route)
    return browser, page, blocked, errors
