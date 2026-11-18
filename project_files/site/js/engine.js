(function () {
  window.F1APP = window.F1APP || {};
  const C = () => F1APP.CONFIG;

  // ---------------------------------------------------------------- Python and numpy helpers

  function bisectRight(arr, t) {
    // numpy searchsorted(side="right"): NaN counts as larger than every number
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      const v = arr[mid];
      if (t < v || v !== v) hi = mid; else lo = mid + 1;
    }
    return lo;
  }
  const lastLe = (arr, t) => bisectRight(arr, t) - 1;
  const isNaNv = (x) => x !== x;
  const num = (x) => (x === null || x === undefined || isNaNv(x) ? null : Number(x));

  function roundHalfEven(x) {
    const f = Math.floor(x);
    const d = x - f;
    if (d < 0.5) return f;
    if (d > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }
  const pyTrunc = (x) => Math.trunc(x);
  function floorDiv(a, b) { return Math.floor(a / b); }

  function pyFixed(x, d) {
    // Python f"{x:.{d}f}": exact ties round half to even (toFixed rounds them up)
    const scaled = x * Math.pow(10, d);
    const f = Math.floor(scaled);
    if (scaled - f === 0.5 && Number.isFinite(scaled)) {
      const even = f % 2 === 0 ? f : f + 1;
      return (even / Math.pow(10, d)).toFixed(d);
    }
    return x.toFixed(d);
  }

  function median(values) {
    const v = Array.from(values).sort((a, b) => a - b);
    const n = v.length;
    if (!n) return null;
    return n % 2 ? v[(n - 1) >> 1] : (v[n / 2 - 1] + v[n / 2]) / 2;
  }

  function stableOrder(n, cmp) {
    const idx = new Array(n);
    for (let i = 0; i < n; i += 1) idx[i] = i;
    return idx.sort((a, b) => cmp(a, b) || a - b);
  }

  function pick(col, idx) {
    if (col instanceof Float64Array) {
      const out = new Float64Array(idx.length);
      for (let i = 0; i < idx.length; i += 1) out[i] = col[idx[i]];
      return out;
    }
    const out = new Array(idx.length);
    for (let i = 0; i < idx.length; i += 1) out[i] = col[idx[i]];
    return out;
  }

  function selectRows(table, idx) {
    const cols = {};
    for (const k of Object.keys(table.cols)) cols[k] = pick(table.cols[k], idx);
    return { n: idx.length, cols };
  }

  function clean(obj) {
    if (Array.isArray(obj)) return obj.map(clean);
    if (obj && typeof obj === 'object') {
      const out = {};
      for (const k of Object.keys(obj)) out[k] = clean(obj[k]);
      return out;
    }
    if (typeof obj === 'number' && !Number.isFinite(obj)) return null;
    return obj;
  }

  // ---------------------------------------------------------------- points

  function lookupTable(table, season) {
    const keys = Object.keys(table).map(Number).filter((k) => k <= season);
    return keys.length ? table[String(Math.max(...keys))] : [];
  }
  function pointsTable(season, sessionName) {
    return sessionName === 'Sprint' ? lookupTable(C().SPRINT_POINTS_BY_SEASON, season) : lookupTable(C().POINTS_BY_SEASON, season);
  }
  function pointsForPosition(season, sessionName, position) {
    const table = pointsTable(season, sessionName);
    if (position === null || position === undefined || position < 1 || position > table.length) return 0;
    return table[Math.trunc(position) - 1];
  }
  const fastestLapPoint = (season, sessionName) => sessionName === 'Race' && C().FASTEST_LAP_POINT_SEASONS.includes(season);
  const racePoints = (position, season, sessionName) => pointsForPosition(season, sessionName, position);

  // ---------------------------------------------------------------- bundle (port of bundle_io.prepare)

  function records(table, order) {
    const names = Object.keys(table.cols);
    const out = [];
    const rows = order || stableOrder(table.n, () => 0);
    for (const i of rows) {
      const d = {};
      for (const c of names) {
        const v = table.cols[c][i];
        d[c] = v === undefined || isNaNv(v) ? null : v;
      }
      out.push(d);
    }
    return out;
  }

  function groupByDriver(table, drivers) {
    const g = new Map(drivers.map((n) => [n, []]));
    const dn = table.cols.driver_number;
    for (let i = 0; i < table.n; i += 1) {
      const a = g.get(dn[i]);
      if (a) a.push(i);
    }
    const t = table.cols.t;
    for (const [n, idx] of g) {
      let sorted = true;
      for (let i = 1; i < idx.length; i += 1) if (t[idx[i]] < t[idx[i - 1]]) { sorted = false; break; }
      g.set(n, sorted ? idx : stableOrder(idx.length, (a, b) => t[idx[a]] - t[idx[b]]).map((k) => idx[k]));
    }
    return g;
  }

  function makeBundle(meta, tables, extra) {
    const ix = {};
    const drivers = meta.drivers.map((d) => Number(d.driver_number));
    ix.drivers = drivers;
    ix.dpos = {};
    drivers.forEach((n, i) => { ix.dpos[n] = i; });

    const lt = tables.laps;
    const lorder = stableOrder(lt.n, (a, b) => (lt.cols.driver_number[a] - lt.cols.driver_number[b]) || (lt.cols.lap_number[a] - lt.cols.lap_number[b]));
    const lapCols = ['lap_number', 'lap_start_t', 'lap_end_t', 'lap_duration', 'd1', 'd2', 'd3', 's1_end_t', 's2_end_t', 's3_end_t'];
    const lg = {};
    for (const c of lapCols) lg[c] = pick(lt.cols[c], lorder);
    const pitoutAll = pick(lt.cols.is_pit_out_lap, lorder).map((x) => x === true);
    const dnAll = pick(lt.cols.driver_number, lorder);
    lg.pitout = pitoutAll;
    lg.drv = Array.from(dnAll, (n) => (n in ix.dpos ? ix.dpos[n] : -1));
    ix.lap_glob = lg;
    ix.laps = {};
    for (const n of drivers) {
      const idx = [];
      for (let i = 0; i < dnAll.length; i += 1) if (dnAll[i] === n) idx.push(i);
      const d = {};
      for (const c of lapCols) d[c] = pick(lg[c], idx);
      d.pitout = idx.map((i) => pitoutAll[i]);
      ix.laps[n] = d;
    }

    const ms = tables.minisectors;
    ix.ms_glob = {
      drv: Array.from(ms.cols.driver_number, (n) => (n in ix.dpos ? ix.dpos[n] : -1)),
      k: ms.cols.k, end: ms.cols.end_t, dur: ms.cols.duration,
    };
    const msBy = new Map();
    const msKeys = [];
    for (let i = 0; i < ms.n; i += 1) {
      const key = `${ms.cols.driver_number[i]}|${ms.cols.lap_number[i]}`;
      let g = msBy.get(key);
      if (!g) { g = { k: [], end: [], dur: [] }; msBy.set(key, g); msKeys.push(key); }
      g.k.push(ms.cols.k[i]); g.end.push(ms.cols.end_t[i]); g.dur.push(ms.cols.duration[i]);
    }
    ix.ms = msBy;

    const pg = groupByDriver(tables.position, drivers);
    ix.pos = {};
    for (const n of drivers) { const idx = pg.get(n); ix.pos[n] = [pick(tables.position.cols.t, idx), pick(tables.position.cols.position, idx)]; }
    const ig = groupByDriver(tables.intervals, drivers);
    const ic = tables.intervals.cols;
    ix.int = {};
    for (const n of drivers) {
      const idx = ig.get(n);
      ix.int[n] = [pick(ic.t, idx), pick(ic.interval_num, idx), pick(ic.interval_txt, idx), pick(ic.gap_num, idx), pick(ic.gap_txt, idx)];
    }
    const pitg = groupByDriver(tables.pit, drivers);
    const pc = tables.pit.cols;
    ix.pit = {};
    for (const n of drivers) {
      const idx = pitg.get(n);
      ix.pit[n] = [pick(pc.t, idx), pick(pc.lane_duration, idx), Array.from(pick(pc.lap_number, idx), (x) => (isNaNv(x) ? null : Math.trunc(x)))];
    }
    const cg = groupByDriver(tables.car_data, drivers);
    const cc = tables.car_data.cols;
    ix.car = {};
    for (const n of drivers) {
      const idx = cg.get(n);
      ix.car[n] = ['t', 'drs', 'speed', 'throttle', 'brake', 'n_gear', 'rpm'].map((c) => pick(cc[c], idx));
    }
    const lgp = groupByDriver(tables.location, drivers);
    const lc = tables.location.cols;
    ix.loc = {};
    for (const n of drivers) { const idx = lgp.get(n); ix.loc[n] = [pick(lc.t, idx), pick(lc.x, idx), pick(lc.y, idx)]; }

    const st = tables.stints;
    const sorder = stableOrder(st.n, (a, b) => (st.cols.driver_number[a] - st.cols.driver_number[b]) || (st.cols.stint_number[a] - st.cols.stint_number[b]));
    ix.stints = {};
    for (const n of drivers) ix.stints[n] = [];
    for (const r of records(st, sorder)) if (r.driver_number in ix.stints) ix.stints[r.driver_number].push(r);

    const rc = tables.race_control;
    const rorder = stableOrder(rc.n, (a, b) => rc.cols.t[a] - rc.cols.t[b]);
    ix.rc = records(rc, rorder);
    ix.rc_t = Float64Array.from(ix.rc, (r) => r.t);

    const w = tables.weather;
    const worder = stableOrder(w.n, (a, b) => w.cols.t[a] - w.cols.t[b]);
    ix.weather_t = Float64Array.from(worder, (i) => w.cols.t[i]);
    ix.weather = records(w, worder);
    return { meta, tables, extra: extra || {}, idx: ix };
  }

  // ---------------------------------------------------------------- race control

  const RE_TRACK_LIMITS = /^CAR (\d+) \([A-Z]{3}\) TIME \d+:\d+\.\d+ DELETED - TRACK LIMITS/;
  const RE_TIME_PENALTY = /^FIA STEWARDS: (\d+) SECOND TIME PENALTY FOR CAR (\d+) \([A-Z]{3}\)(?: - (.*))?$/;
  const RE_INCIDENT = /^(CORRECTION:: )?(?:FIA STEWARDS: )?(?:TURN (\d+) )?(?:PIT ENTRY )?INCIDENT INVOLVING CARS? (.+?) (NOTED|UNDER INVESTIGATION|WILL BE INVESTIGATED AFTER THE RACE|REVIEWED NO FURTHER INVESTIGATION)(?: - (.*))?$/;
  const RE_CAR = /(\d+) \([A-Z]{3}\)/g;
  const TRACK_FLAG_STATES = { GREEN: 'GREEN', CLEAR: 'GREEN', YELLOW: 'YELLOW', 'DOUBLE YELLOW': 'DOUBLE YELLOW', RED: 'RED', CHEQUERED: 'CHEQUERED' };
  const SECTOR_YELLOWS = ['YELLOW', 'DOUBLE YELLOW'];
  const SECTOR_CLEARS = ['CLEAR', 'GREEN'];

  function tagFor(row) {
    const msg = row.message || '';
    if (RE_TRACK_LIMITS.test(msg)) return 'TRACK LIMITS';
    if (RE_TIME_PENALTY.test(msg)) return 'PENALTY';
    if (RE_INCIDENT.test(msg)) return 'INVESTIGATION';
    const cat = row.category;
    if (cat === 'Flag') return row.flag || 'FLAG';
    if (cat === 'SafetyCar') return 'SC';
    if (cat === 'Drs') return 'DRS';
    if (cat === 'SessionStatus') return 'SESSION';
    return 'INFO';
  }

  function carsKey(text) {
    const set = new Set();
    for (const m of text.matchAll(RE_CAR)) set.add(Number(m[1]));
    return Array.from(set).sort((a, b) => a - b);
  }

  function parseRaceControl(rows) {
    const penS = {};
    const tl = {};
    let open = [];
    const add = (o, k, v) => { o[k] = (o[k] || 0) + v; };
    for (const r of rows) {
      const msg = r.message || '';
      let m = RE_TRACK_LIMITS.exec(msg);
      if (m) { add(tl, Number(m[1]), 1); continue; }
      m = RE_TIME_PENALTY.exec(msg);
      if (m) {
        const secs = Number(m[1]); const car = Number(m[2]); const reason = m[3] || '';
        add(penS, car, secs);
        for (let i = open.length - 1; i >= 0; i -= 1) {
          const inc = open[i];
          if (inc.cars.includes(car) && (inc.reason === reason || inc.reason === '')) { open.splice(i, 1); break; }
        }
        continue;
      }
      m = RE_INCIDENT.exec(msg);
      if (m) {
        const correction = m[1]; const turn = m[2] === undefined ? null : m[2]; const cars = carsKey(m[3]);
        const decision = m[4]; const reason = m[5] || '';
        const key = cars.join(',');
        if (decision === 'REVIEWED NO FURTHER INVESTIGATION') {
          open = open.filter((i) => !(i.key === key && (i.turn === turn || turn === null || i.turn === null)));
          continue;
        }
        if (correction && turn) open = open.filter((i) => !(i.turn === turn && i.reason === reason));
        const same = open.filter((i) => i.key === key && i.turn === turn);
        if (same.length) {
          if (reason) same[0].reason = reason;
        } else {
          open.push({ cars, key, turn, reason });
        }
      }
    }
    const inv = {};
    for (const inc of open) for (const c of inc.cars) add(inv, c, 1);
    return { penalty_s: penS, track_limits: tl, investigations: inv };
  }

  function applyTrackMessage(state, sectors, drs, row) {
    const cat = row.category; const flag = row.flag; const scope = row.scope;
    const msg = row.message || '';
    if (cat === 'Flag' && scope === 'Track') {
      const nw = TRACK_FLAG_STATES[flag];
      if (nw && state !== 'CHEQUERED') {
        state = nw;
        if (nw === 'GREEN') sectors = {};
      }
    } else if (cat === 'Flag' && scope === 'Sector' && row.sector !== null && row.sector !== undefined) {
      const sec = Math.trunc(row.sector);
      sectors = Object.assign({}, sectors);
      if (SECTOR_YELLOWS.includes(flag)) sectors[sec] = flag;
      else if (SECTOR_CLEARS.includes(flag)) delete sectors[sec];
    } else if (cat === 'SafetyCar' && state !== 'CHEQUERED') {
      if (msg.includes('VIRTUAL SAFETY CAR DEPLOYED')) state = 'VSC';
      else if (msg.includes('SAFETY CAR DEPLOYED')) state = 'SC';
      else if (msg.includes('VIRTUAL SAFETY CAR ENDING') && state === 'VSC') state = 'GREEN';
      else if (msg.includes('SAFETY CAR IN THIS LAP') && state === 'SC') state = 'GREEN';
    } else if (cat === 'Drs') {
      if (msg.includes('DRS ENABLED')) drs = true;
      else if (msg.includes('DRS DISABLED')) drs = false;
    }
    return [state, sectors, drs];
  }

  function buildTimeline(rows) {
    let state = 'GREEN'; let sectors = {}; let drs = null;
    const out = [];
    for (const r of rows) {
      [state, sectors, drs] = applyTrackMessage(state, sectors, drs, r);
      out.push([r.t, state, sectors, drs]);
    }
    return out;
  }

  function timelineAt(tl, t) {
    const times = tl.map((e) => e[0]);
    const i = bisectRight(times, t) - 1;
    if (i < 0) return ['GREEN', {}, null, null];
    let since = tl[i][0];
    let j = i;
    while (j > 0 && tl[j - 1][1] === tl[i][1]) { j -= 1; since = tl[j][0]; }
    return [tl[i][1], tl[i][2], tl[i][3], since];
  }

  const noSectors = (s) => Object.keys(s).length === 0;

  function greenThroughout(tl, a, b) {
    const times = tl.map((e) => e[0]);
    const i = bisectRight(times, a) - 1;
    if (i >= 0 && !(tl[i][1] === 'GREEN' && noSectors(tl[i][2]))) return false;
    let j = i + 1;
    while (j < tl.length && tl[j][0] <= b) {
      if (!(tl[j][1] === 'GREEN' && noSectors(tl[j][2]))) return false;
      j += 1;
    }
    return true;
  }

  // ---------------------------------------------------------------- small rules

  function drsLabel(code, age) {
    if (code === null || code === undefined || age === null || age === undefined || age > C().DRS_STALE_S || isNaNv(code)) return null;
    const c = Math.trunc(code);
    if (C().DRS_ON.includes(c)) return 'ON';
    if (C().DRS_ELIGIBLE.includes(c)) return 'ELIG';
    return 'OFF';
  }

  function sectorColour(value, ownMin, overallMin) {
    if (value === null) return null;
    if (overallMin !== null && value <= overallMin) return 'purple';
    if (ownMin !== null && value <= ownMin) return 'green';
    return 'yellow';
  }

  function latestSample(ts, xs, ys, t) {
    const i = lastLe(ts, t);
    if (i < 0) return null;
    return [xs[i], ys[i], t - ts[i]];
  }

  function lapIsClean(L, i, t, tl, pitLaps) {
    const end = L.lap_end_t[i]; const dur = L.lap_duration[i];
    if (!(end <= t) || isNaNv(dur) || L.pitout[i]) return false;
    if (pitLaps.has(Math.trunc(L.lap_number[i]))) return false;
    return greenThroughout(tl, L.lap_start_t[i], end);
  }

  function pitLapsFor(ix, n, t, tlStarted) {
    const laps = new Set();
    const [pt, , plap] = ix.pit[n];
    const k = bisectRight(pt, t);
    for (let i = 0; i < k; i += 1) {
      const lap = plap[i];
      if (lap !== null) { laps.add(lap); laps.add(lap + 1); }
    }
    const L = ix.laps[n];
    for (let i = 0; i < tlStarted; i += 1) {
      if (L.pitout[i]) { laps.add(Math.trunc(L.lap_number[i]) - 1); laps.add(Math.trunc(L.lap_number[i])); }
    }
    return laps;
  }

  function estimatePitLoss(ix, t, tl) {
    const losses = [];
    for (const n of ix.drivers) {
      const L = ix.laps[n];
      if (!L.lap_number.length) continue;
      const started = bisectRight(L.lap_start_t, t);
      const pitLaps = pitLapsFor(ix, n, t, started);
      const [pt, , plap] = ix.pit[n];
      const k = bisectRight(pt, t);
      const lapIdx = new Map();
      for (let i = 0; i < L.lap_number.length; i += 1) lapIdx.set(Math.trunc(L.lap_number[i]), i);
      for (let q = 0; q < k; q += 1) {
        const lap = plap[q];
        if (lap === null || !lapIdx.has(lap) || !lapIdx.has(lap + 1)) continue;
        const a = lapIdx.get(lap); const b = lapIdx.get(lap + 1);
        if (!(L.lap_end_t[a] <= t && L.lap_end_t[b] <= t)) continue;
        if (isNaNv(L.lap_duration[a]) || isNaNv(L.lap_duration[b])) continue;
        if (!greenThroughout(tl, L.lap_start_t[a], L.lap_end_t[b])) continue;
        const ref = [];
        for (let back = 1; back <= C().PIT_LOSS_CLEAN_LAPS; back += 1) {
          const j = lapIdx.get(lap - back);
          if (j !== undefined && lapIsClean(L, j, t, tl, pitLaps)) ref.push(L.lap_duration[j]);
        }
        if (ref.length < 2) continue;
        losses.push(L.lap_duration[a] + L.lap_duration[b] - 2 * median(ref));
      }
    }
    if (losses.length < C().PIT_LOSS_MIN_STOPS) return [null, losses.length];
    return [median(losses), losses.length];
  }

  function rejoinProjection(rows, pitLoss) {
    const out = {};
    const running = rows.filter((r) => r.status === 'RUNNING' && r.gap_num !== null);
    for (const r of rows) {
      const n = r.driver_number;
      if (pitLoss === null || r.status !== 'RUNNING' || r.gap_num === null) { out[n] = null; continue; }
      const after = r.gap_num + pitLoss;
      const ahead = running.filter((o) => o.driver_number !== n && o.gap_num < after);
      let car = null;
      for (const o of ahead) if (car === null || o.gap_num > car.gap_num) car = o;
      out[n] = { position: 1 + ahead.length, car_ahead: car ? car.acronym : null, gap_to_car_ahead: car ? after - car.gap_num : null };
    }
    return out;
  }

  function projectStandings(entries, racePts, season, fastestDriver, positions, outs, order) {
    const rows = [];
    for (const key of (order || Object.keys(entries)).map(String)) {
      const e = entries[key];
      const gained = key in racePts ? racePts[key] : 0;
      const start = e.points;
      rows.push(Object.assign({}, e, { key, race_points: gained, projected: start !== null ? start + gained : null }));
    }
    const big = Math.pow(10, 6);
    rows.sort((a, b) => {
      const an = a.projected === null; const bn = b.projected === null;
      if (an !== bn) return an ? 1 : -1;
      const av = -(a.projected || 0); const bv = -(b.projected || 0);
      if (av !== bv) return av - bv;
      const ar = a.rank !== null ? a.rank : big; const br = b.rank !== null ? b.rank : big;
      return ar - br;
    });
    rows.forEach((r, i) => {
      r.new_rank = r.projected !== null ? i + 1 : null;
      r.rank_change = r.rank !== null && r.new_rank !== null ? r.rank - r.new_rank : null;
    });
    return rows;
  }

  function startRanks(pointsByKey, positionByKey, order) {
    const big = Math.pow(10, 6);
    const keyed = order.filter((k) => pointsByKey[k] !== null && pointsByKey[k] !== undefined);
    keyed.sort((a, b) => {
      if (pointsByKey[a] !== pointsByKey[b]) return -pointsByKey[a] - -pointsByKey[b];
      const pa = positionByKey[a] !== null && positionByKey[a] !== undefined ? positionByKey[a] : big;
      const pb = positionByKey[b] !== null && positionByKey[b] !== undefined ? positionByKey[b] : big;
      if (pa !== pb) return pa - pb;
      return order.indexOf(a) - order.indexOf(b);
    });
    const out = {};
    keyed.forEach((k, i) => { out[k] = i + 1; });
    return out;
  }

  // ---------------------------------------------------------------- state

  function ownMin(L, s, t) {
    let best = null;
    const e = L[`s${s}_end_t`]; const d = L[`d${s}`];
    for (let i = 0; i < e.length; i += 1) if (e[i] <= t && !isNaNv(d[i]) && (best === null || d[i] < best)) best = d[i];
    return best;
  }

  function pad2(n) { return String(n).padStart(2, '0'); }

  function parseIsoMs(text) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$/.exec(text);
    const frac = m[7] ? Number(('0.' + m[7])) * 1000 : 0;
    let ms = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])) + frac;
    if (m[8] && m[8] !== 'Z') {
      const sign = m[8][0] === '-' ? -1 : 1;
      ms -= sign * (Number(m[8].slice(1, 3)) * 60 + Number(m[8].slice(4, 6))) * 60000;
    }
    return ms;
  }

  function forecast(bundle, t) {
    const fc = bundle.extra.forecast;
    if (!fc || !fc.hourly) return null;
    const hourly = fc.hourly;
    const times = hourly.time || [];
    const now = parseIsoMs(bundle.meta.t0_utc) + roundHalfEven(t * 1e6) / 1000;
    const d = new Date(now);
    const key = `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}T${pad2(d.getUTCHours())}:00`;
    const i = times.indexOf(key);
    if (i < 0) return [];
    const out = [];
    for (let h = 1; h <= C().FORECAST_HOURS; h += 1) {
      const j = i + h;
      if (j >= times.length) { out.push(null); continue; }
      const row = { time: times[j] };
      for (const f of ['precipitation_probability', 'precipitation', 'temperature_2m', 'wind_speed_10m']) {
        const vals = hourly[f];
        row[f] = vals && j < vals.length ? vals[j] : null;
      }
      out.push(row);
    }
    return out;
  }

  function stateAt(bundle, t) {
    const meta = bundle.meta; const ix = bundle.idx;
    t = Number(t);
    const drivers = ix.drivers;
    const dinfo = {};
    for (const d of meta.drivers) dinfo[Number(d.driver_number)] = d;
    const totalLaps = meta.total_laps;
    const nMs = C().N_MINISECTORS;

    const rcK = bisectRight(ix.rc_t, t);
    const rcRows = ix.rc.slice(0, rcK);
    const parsed = parseRaceControl(rcRows);
    const tl = buildTimeline(rcRows);
    const [trackState, sectors, drsEnabled, redSince] = timelineAt(tl, t);
    let cheqT = null;
    for (const r of rcRows) if (r.category === 'Flag' && r.flag === 'CHEQUERED' && r.scope === 'Track') { cheqT = r.t; break; }

    const G = ix.lap_glob;
    const secOverall = {};
    for (const s of [1, 2, 3]) {
      const se = G[`s${s}_end_t`]; const dd = G[`d${s}`];
      const own = new Array(drivers.length).fill(Infinity);
      for (let i = 0; i < se.length; i += 1) {
        if (se[i] <= t && !isNaNv(dd[i]) && G.drv[i] >= 0 && dd[i] < own[G.drv[i]]) own[G.drv[i]] = dd[i];
      }
      let mn = Infinity;
      for (const v of own) if (v < mn) mn = v;
      secOverall[s] = own.length ? mn : Infinity;
    }
    const MG = ix.ms_glob;
    const msOwn = drivers.map(() => new Array(nMs).fill(Infinity));
    for (let i = 0; i < MG.end.length; i += 1) {
      if (MG.end[i] <= t && MG.drv[i] >= 0) {
        const cell = msOwn[MG.drv[i]];
        if (MG.dur[i] < cell[MG.k[i]]) cell[MG.k[i]] = MG.dur[i];
      }
    }
    const msOverall = new Array(nMs).fill(Infinity);
    for (const row of msOwn) for (let k = 0; k < nMs; k += 1) if (row[k] < msOverall[k]) msOverall[k] = row[k];

    const info = {};
    drivers.forEach((n) => {
      const L = ix.laps[n];
      const d = { n };
      const starts = L.lap_start_t;
      const curI = starts.length ? lastLe(starts, t) : -1;
      d.cur_i = curI;
      d.cur_lap = curI >= 0 ? Math.trunc(L.lap_number[curI]) : 0;
      d.cur_start = curI >= 0 ? starts[curI] : null;
      const completed = new Array(starts.length);
      let completedN = 0;
      for (let i = 0; i < starts.length; i += 1) { completed[i] = L.lap_end_t[i] <= t; if (completed[i]) completedN += 1; }
      d.completed_mask = completed;
      d.completed = completedN;
      const known = completed.map((c, i) => c && !isNaNv(L.lap_duration[i]));
      d.known_mask = known;
      const ki = [];
      for (let i = 0; i < known.length; i += 1) if (known[i]) ki.push(i);
      if (ki.length) {
        const last = ki[ki.length - 1];
        d.last_lap = L.lap_duration[last];
        d.last_lap_number = Math.trunc(L.lap_number[last]);
        let prev = null;
        if (last > 0 && known[last - 1] && L.lap_number[last - 1] === L.lap_number[last] - 1) prev = L.lap_duration[last - 1];
        d.delta_prev = prev !== null ? d.last_lap - prev : null;
        let best = Infinity;
        for (const i of ki) if (L.lap_duration[i] < best) best = L.lap_duration[i];
        d.best = best;
      } else {
        d.last_lap = null; d.delta_prev = null; d.best = null; d.last_lap_number = null;
      }
      d.sec_vals = [1, 2, 3].map((s) => {
        let val = null;
        if (starts.length) {
          const e = L[`s${s}_end_t`]; const dd = L[`d${s}`];
          for (let i = 0; i < e.length; i += 1) if (e[i] <= t && !isNaNv(dd[i])) val = dd[i];
        }
        return val;
      });
      const [pt, pp] = ix.pos[n];
      const pi = lastLe(pt, t);
      d.position = pi >= 0 ? Math.trunc(pp[pi]) : null;
      const [ptt, , ] = ix.pit[n];
      d.pit_stops = bisectRight(ptt, t);
      let inPit = false;
      if (curI >= 0 && L.pitout[curI] && L.lap_number[curI] >= 2) {
        const s0 = starts[curI];
        let any = false;
        for (let i = 0; i < ptt.length; i += 1) if (ptt[i] >= s0 && ptt[i] <= t) { any = true; break; }
        inPit = !any;
      }
      d.in_pit = inPit;
      d.stints = ix.stints[n].filter((s) => s.known_t !== null && s.known_t <= t);
      info[n] = d;
    });

    const startedSince = (j, a, b) => {
      const sj = ix.laps[j].lap_start_t;
      return bisectRight(sj, b) - bisectRight(sj, a);
    };
    const isOut = (n, d) => {
      const te = trackState !== 'RED' ? t : redSince;
      const cs = d.cur_start;
      if (cs === null || te === null || cs > te || te - cs <= C().OUT_TIMEOUT_S) return false;
      let others = 0;
      for (const j of drivers) if (j !== n && startedSince(j, cs, te) >= 2) others += 1;
      return others >= C().OUT_MIN_OTHERS;
    };
    for (const n of drivers) {
      const d = info[n];
      const L = ix.laps[n];
      let fin = false;
      if (cheqT !== null && L.lap_end_t.length) {
        for (let i = 0; i < L.lap_end_t.length; i += 1) if (L.lap_end_t[i] >= cheqT && L.lap_end_t[i] <= t) { fin = true; break; }
      }
      if (fin) d.status = 'FINISHED';
      else if (d.in_pit) d.status = 'PIT';
      else if (isOut(n, d)) d.status = 'OUT';
      else d.status = 'RUNNING';
    }

    const byPos = drivers.map((n) => info[n]).filter((d) => d.position !== null).sort((a, b) => (a.position - b.position) || (a.n - b.n));
    const leader = byPos.length && byPos[0].position === 1 ? byPos[0] : null;
    const posToDriver = {};
    for (const d of byPos) if (!(d.position in posToDriver)) posToDriver[d.position] = d.n;

    let fastestN = null; let fastestT = null;
    for (const n of drivers) {
      const b = info[n].best;
      if (b !== null && (fastestT === null || b < fastestT)) { fastestN = n; fastestT = b; }
    }

    const rows = [];
    for (const n of drivers) {
      const d = info[n]; const metaD = dinfo[n]; const L = ix.laps[n];
      const grid = metaD.grid_position === undefined ? null : metaD.grid_position;
      const pos = d.position;
      let tyre = null;
      if (d.stints.length) {
        const s = d.stints[d.stints.length - 1];
        const startAge = s.tyre_age_at_start;
        const unknown = startAge === null;
        let nLaps = null;
        if (s.lap_start !== null) {
          nLaps = 0;
          for (let i = 0; i < L.lap_number.length; i += 1) if (L.lap_number[i] >= s.lap_start && d.completed_mask[i]) nLaps += 1;
        }
        tyre = { compound: s.compound, age: nLaps !== null ? (unknown ? 0 : Math.trunc(startAge)) + nLaps : null, age_unknown_start: unknown };
      }
      const [it, inum, itxt, gnum, gtxt] = ix.int[n];
      const k = lastLe(it, t);
      let interval = null; let gap = null; let gapNum = null;
      if (k >= 0) {
        interval = num(inum[k]) !== null ? num(inum[k]) : itxt[k];
        gap = num(gnum[k]) !== null ? num(gnum[k]) : gtxt[k];
        gapNum = num(gnum[k]);
      }
      if (pos === 1) { interval = 'LEADER'; gap = 'LEADER'; gapNum = 0.0; }
      const sectorsOut = [];
      for (const s of [1, 2, 3]) {
        const v = d.sec_vals[s - 1];
        const own = num(ownMin(L, s, t));
        sectorsOut.push(v === null ? null : { value: v, colour: sectorColour(v, own, Number.isFinite(secOverall[s]) ? secOverall[s] : null) });
      }
      const msOut = new Array(nMs).fill(null);
      const got = ix.ms.get(`${n}|${d.cur_lap}`);
      const di = ix.dpos[n];
      if (got !== undefined) {
        for (let q = 0; q < got.k.length; q += 1) {
          if (got.end[q] <= t) {
            const kk = got.k[q];
            const ov = msOverall[kk];
            const ownv = msOwn[di][kk];
            msOut[Math.trunc(kk)] = { value: got.dur[q], colour: sectorColour(got.dur[q], Number.isFinite(ownv) ? ownv : null, Number.isFinite(ov) ? ov : null) };
          }
        }
      }
      let rowDrs = null;
      if (meta.has_drs) {
        const [ct, cd] = ix.car[n];
        const ci = lastLe(ct, t);
        rowDrs = ci >= 0 ? drsLabel(cd[ci], t - ct[ci]) : null;
      }
      let bestOverall = d.best !== null;
      if (bestOverall) for (const o of drivers) if (o !== n && info[o].best !== null && info[o].best < d.best) { bestOverall = false; break; }
      const row = {
        driver_number: n, acronym: metaD.acronym, full_name: metaD.full_name, team_name: metaD.team_name, team_colour: metaD.team_colour,
        position: pos, grid, gained: grid !== null && pos !== null ? grid - pos : null,
        tyre, pit_stops: d.pit_stops, in_pit: d.in_pit, interval, gap, gap_num: gapNum,
        lap: d.cur_lap, completed_laps: d.completed,
        current_lap_time: d.cur_start !== null ? t - d.cur_start : null,
        sectors: sectorsOut, minisectors: msOut,
        last_lap: d.last_lap, delta_prev: d.delta_prev, best_lap: d.best, best_is_overall: Boolean(bestOverall),
        penalty_s: Math.trunc(parsed.penalty_s[n] || 0), drive_through_stop_go: 0,
        investigations: Math.trunc(parsed.investigations[n] || 0), track_limits: Math.trunc(parsed.track_limits[n] || 0),
        black_white: false, status: d.status,
      };
      if (meta.has_drs) row.drs = rowDrs;
      rows.push(row);
    }
    rows.sort((a, b) => {
      const an = a.position === null; const bn = b.position === null;
      if (an !== bn) return an ? 1 : -1;
      const av = an ? 0 : a.position; const bv = bn ? 0 : b.position;
      return (av - bv) || (a.driver_number - b.driver_number);
    });
    const byN = {};
    for (const r of rows) byN[r.driver_number] = r;

    let lap = null;
    const elapsed = Math.max(0.0, t - meta.race_start_t);
    let remaining = null;
    if (leader !== null) {
      lap = Math.min(leader.cur_lap, totalLaps);
      if (leader.last_lap !== null) remaining = Math.max(0, totalLaps - leader.completed) * leader.last_lap;
    }
    const header = { lap, total_laps: totalLaps, elapsed, time_remaining_est: remaining };

    const track = {
      state: trackState,
      yellow_sectors: Object.keys(sectors).map(Number).sort((a, b) => a - b).map((s) => ({ sector: s, level: sectors[s] })),
      drs_enabled: drsEnabled,
    };

    const feed = rcRows.slice(-C().FEED_LENGTH).reverse().map((r) => ({
      t: r.t, lap: r.lap_number, tag: tagFor(r), message: r.message, category: r.category, flag: r.flag }));

    const wi = lastLe(ix.weather_t, t);
    let weather = null;
    if (wi >= 0) {
      const w = ix.weather[wi];
      weather = {};
      for (const k of ['air_temperature', 'track_temperature', 'humidity', 'wind_speed', 'wind_direction', 'rainfall', 'pressure']) weather[k] = w[k] === undefined ? null : w[k];
    }

    const fc = forecast(bundle, t);

    const trackMap = [];
    for (const n of drivers) {
      const [lt, lx, ly] = ix.loc[n];
      const got = latestSample(lt, lx, ly, t);
      if (got !== null) {
        trackMap.push({ driver_number: n, acronym: dinfo[n].acronym, team_colour: dinfo[n].team_colour, x: got[0], y: got[1],
                        stale: got[2] > C().MAP_HOLD_S, status: info[n].status });
      }
    }

    const season = meta.season;
    const sessionName = meta.session_name === undefined ? 'Race' : meta.session_name;
    const racePts = {};
    for (const n of drivers) {
      const pos = info[n].position;
      if (info[n].status === 'OUT') { racePts[n] = 0; continue; }
      let p = racePoints(pos, season, sessionName);
      if (fastestLapPoint(season, sessionName) && fastestN === n && pos !== null && pos <= C().RACE_POINTS.length) p += 1;
      racePts[n] = p;
    }
    const sp = meta.start_points;
    const dEntries = {}; const dPts = {}; const dPosn = {};
    for (const n of drivers) {
      const e = sp.drivers[String(n)];
      dPts[n] = e ? e.points : null;
      dPosn[n] = e ? e.position : null;
    }
    const ranks = startRanks(dPts, dPosn, drivers);
    for (const n of drivers) {
      dEntries[n] = { driver_number: n, acronym: dinfo[n].acronym, name: dinfo[n].full_name, team_name: dinfo[n].team_name,
                      points: dPts[n], rank: n in ranks ? ranks[n] : null };
    }
    const teams = Array.from(new Set(drivers.map((n) => dinfo[n].team_name))).sort();
    const tPts = {}; const tPosn = {}; const tGain = {};
    for (const tn of teams) {
      const e = sp.teams[tn];
      tPts[tn] = e ? e.points : null;
      tPosn[tn] = e ? e.position : null;
      tGain[tn] = drivers.filter((n) => dinfo[n].team_name === tn).reduce((a, n) => a + racePts[n], 0);
    }
    const tRanks = startRanks(tPts, tPosn, teams);
    const tEntries = {};
    for (const tn of teams) tEntries[tn] = { team_name: tn, points: tPts[tn], rank: tn in tRanks ? tRanks[tn] : null };
    const standings = {
      source: sp.source,
      drivers: projectStandings(dEntries, racePts, season, fastestN, null, null, drivers),
      constructors: projectStandings(tEntries, tGain, season, fastestN, null, null, teams),
    };
    for (const e of standings.drivers) delete e.key;
    for (const e of standings.constructors) delete e.key;

    const [pitLoss, pitN] = estimatePitLoss(ix, t, tl);
    const rejoin = rejoinProjection(rows, pitLoss);
    let usedWet = false;
    for (const n of drivers) for (const s of info[n].stints) if (C().WET_COMPOUNDS.includes(s.compound)) usedWet = true;
    const strategyRows = [];
    for (const r of rows) {
      const n = r.driver_number;
      const d = info[n];
      const dry = new Set(d.stints.filter((s) => C().DRY_COMPOUNDS.includes(s.compound)).map((s) => s.compound));
      const behindN = r.position !== null ? posToDriver[r.position + 1] : undefined;
      let threat = false;
      if (r.status === 'RUNNING' && behindN !== undefined) {
        const b = byN[behindN];
        const bi = ix.int[behindN];
        const kk = lastLe(bi[0], t);
        const bInt = kk >= 0 ? num(bi[1][kk]) : null;
        threat = Boolean(b.status === 'RUNNING' && bInt !== null && bInt < C().UNDERCUT_GAP_S && b.pit_stops === r.pit_stops);
      }
      const curStint = d.stints.length ? d.stints[d.stints.length - 1] : null;
      const stintLaps = curStint && curStint.lap_start !== null ? d.cur_lap - curStint.lap_start + 1 : null;
      strategyRows.push({ driver_number: n, acronym: r.acronym, stops: r.pit_stops, stint_laps: stintLaps,
                          needs_second_dry_compound: Boolean(!usedWet && dry.size === 1), rejoin: rejoin[n], undercut_threat: threat });
    }
    const strategy = { pit_loss: pitLoss, pit_loss_stops: pitN, rows: strategyRows };

    const tyreSets = {}; const stintChart = {}; const paceChart = {};
    for (const n of drivers) {
      const d = info[n]; const L = ix.laps[n];
      tyreSets[n] = d.stints.map((s) => ({ compound: s.compound, used: Boolean((s.tyre_age_at_start || 0) > 0) }));
      const chart = [];
      d.stints.forEach((s, i) => {
        let last;
        if (i + 1 < d.stints.length && d.stints[i + 1].lap_start !== null) last = d.stints[i + 1].lap_start - 1;
        else last = d.cur_lap;
        chart.push({ compound: s.compound, first_lap: s.lap_start, last_lap: last });
      });
      stintChart[n] = chart;
      const pitLaps = pitLapsFor(ix, n, t, d.cur_i + 1);
      const pts = [];
      for (let i = 0; i < d.known_mask.length; i += 1) {
        if (d.known_mask[i]) pts.push({ lap: Math.trunc(L.lap_number[i]), duration: L.lap_duration[i], clean: Boolean(lapIsClean(L, i, t, tl, pitLaps)) });
      }
      paceChart[n] = pts;
    }

    return clean({
      t, header, track, rows, feed, weather, forecast: fc, map: trackMap, standings, strategy,
      tyre_sets: tyreSets, stint_chart: stintChart, pace_chart: paceChart, fastest_lap: { driver_number: fastestN, time: fastestT },
    });
  }

  // ---------------------------------------------------------------- truncate, hide, telemetry, trace

  function truncate(bundle, t) {
    const tb = bundle.tables;
    const nw = {};
    const lt = tb.laps;
    const keep = [];
    for (let i = 0; i < lt.n; i += 1) if (lt.cols.lap_start_t[i] <= t) keep.push(i);
    const laps = selectRows(lt, keep);
    const LC = laps.cols;
    const pairs = [['s1_end_t', ['d1']], ['s2_end_t', ['d2']], ['lap_end_t', ['lap_duration', 'd3']]];
    for (const [col, durs] of pairs) {
      const late = [];
      for (let i = 0; i < laps.n; i += 1) late.push(LC[col][i] > t);
      for (const dc of durs) for (let i = 0; i < laps.n; i += 1) if (late[i]) LC[dc][i] = NaN;
    }
    for (let i = 0; i < laps.n; i += 1) if (LC.s3_end_t[i] > t) LC.s3_end_t[i] = NaN;
    for (const col of ['s1_end_t', 's2_end_t', 'lap_end_t']) for (let i = 0; i < laps.n; i += 1) if (LC[col][i] > t) LC[col][i] = NaN;
    nw.laps = laps;
    const filt = (table, col) => {
      const idx = [];
      for (let i = 0; i < table.n; i += 1) if (table.cols[col][i] <= t) idx.push(i);
      return selectRows(table, idx);
    };
    nw.minisectors = filt(tb.minisectors, 'end_t');
    for (const name of ['position', 'intervals', 'pit', 'race_control', 'weather', 'car_data', 'location']) nw[name] = filt(tb[name], 't');
    const st = filt(tb.stints, 'known_t');
    st.cols.lap_end = new Float64Array(st.n).fill(NaN);
    nw.stints = st;
    return makeBundle(bundle.meta, nw, bundle.extra);
  }

  const HIDDEN_ROW_FIELDS = ['tyre', 'sectors', 'minisectors', 'drs', 'penalty_s', 'drive_through_stop_go', 'investigations', 'track_limits', 'black_white', 'current_lap_time'];
  const HIDDEN_TOP_FIELDS = ['track', 'feed', 'map', 'forecast', 'tyre_sets'];
  const HIDDEN_STRATEGY_FIELDS = ['rejoin', 'undercut_threat', 'needs_second_dry_compound'];

  function without(obj, names) {
    const out = {};
    for (const k of Object.keys(obj)) if (!names.includes(k)) out[k] = obj[k];
    return out;
  }

  function hideForKind(state, kind) {
    if (kind !== 'lap_only') return state;
    const out = without(state, HIDDEN_TOP_FIELDS);
    out.rows = state.rows.map((r) => without(r, HIDDEN_ROW_FIELDS));
    const strat = Object.assign({}, state.strategy);
    strat.rows = strat.rows.map((r) => without(r, HIDDEN_STRATEGY_FIELDS));
    out.strategy = strat;
    return out;
  }

  function telemetryAt(bundle, t, driver) {
    const ix = bundle.idx;
    const n = Math.trunc(driver);
    const out = { driver_number: n, speed: null, gear: null, throttle: null, brake: null, rpm: null, drs: null };
    if (!(n in ix.car)) return out;
    const [ct, cd, sp, th, br, gr, rp] = ix.car[n];
    const i = lastLe(ct, t);
    if (i < 0 || t - ct[i] > C().DRS_STALE_S) return out;
    out.speed = num(sp[i]); out.gear = num(gr[i]); out.throttle = num(th[i]);
    out.brake = isNaNv(br[i]) ? null : br[i] > 0;
    out.rpm = num(rp[i]); out.drs = drsLabel(cd[i], t - ct[i]);
    return clean(out);
  }

  function interp(x, xp, fp) {
    const n = xp.length;
    if (x < xp[0]) return fp[0];
    if (x > xp[n - 1]) return fp[n - 1];
    let lo = 0; let hi = n - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (xp[mid] <= x) lo = mid; else hi = mid - 1;
    }
    const j = lo;
    if (j >= n - 1) return fp[n - 1];
    const slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]);
    return slope * (x - xp[j]) + fp[j];
  }

  function lapTrace(bundle, t, driver) {
    const ix = bundle.idx;
    const n = Math.trunc(driver);
    const empty = { driver_number: n, lap: null, points: [] };
    const L = ix.laps[n];
    if (L === undefined || !(n in ix.car) || !L.lap_end_t.length) return empty;
    let best = -1;
    for (let i = 0; i < L.lap_end_t.length; i += 1) {
      if (L.lap_end_t[i] <= t && !isNaNv(L.lap_start_t[i]) && (best < 0 || L.lap_end_t[i] > L.lap_end_t[best])) best = i;
    }
    if (best < 0) return empty;
    const start = L.lap_start_t[best]; const end = L.lap_end_t[best];
    const ct = ix.car[n][0]; const sp = ix.car[n][2];
    const tt = []; const spd = [];
    for (let i = 0; i < ct.length; i += 1) if (ct[i] >= start && ct[i] <= end && !isNaNv(sp[i])) { tt.push(ct[i]); spd.push(sp[i]); }
    if (tt.length < 2) return empty;
    const dist = [0];
    for (let i = 1; i < tt.length; i += 1) dist.push(dist[i - 1] + ((spd[i] / 3.6 + spd[i - 1] / 3.6) / 2) * (tt[i] - tt[i - 1]));
    const total = dist[dist.length - 1];
    if (!(total > 0)) return empty;
    const frac = dist.map((d) => d / total);
    const np = C().TRACE_POINTS;
    const step = 1 / (np - 1);
    const points = [];
    for (let i = 0; i < np; i += 1) {
      const g = i === np - 1 ? 1.0 : i * step;
      points.push([g, interp(g, frac, spd)]);
    }
    return clean({ driver_number: n, lap: Math.trunc(L.lap_number[best]), points });
  }

  F1APP.engine = {
    stateAt, truncate, hideForKind, telemetryAt, lapTrace, forecast, makeBundle, clean,
    bisectRight, lastLe, roundHalfEven, floorDiv, median, pyTrunc, pyFixed, pointsTable, pointsForPosition, fastestLapPoint,
    parseRaceControl, buildTimeline, timelineAt, greenThroughout, tagFor, drsLabel, sectorColour,
  };
})();
