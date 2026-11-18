(function () {
  window.F1APP = window.F1APP || {};
const { NA, isNum, esc, fmtSec, tyreLetter, hex } = F1APP.format;

const TYRE_COL = { SOFT: '#ff3b30', MEDIUM: '#ffd60a', HARD: '#f2f2f7', INTERMEDIATE: '#34c759', WET: '#0a84ff' };
const SHAPES = ['circle', 'square', 'triangle', 'diamond'];

function shape(kind, x, y, r, fill, stroke, hollow) {
  const f = hollow ? 'none' : fill;
  const attrs = `fill="${f}" stroke="${stroke}" stroke-width="1.5"`;
  if (kind === 'square') return `<rect x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" ${attrs}></rect>`;
  if (kind === 'triangle') return `<polygon points="${x},${y - r - 1} ${x - r - 1},${y + r} ${x + r + 1},${y + r}" ${attrs}></polygon>`;
  if (kind === 'diamond') return `<polygon points="${x},${y - r - 1} ${x + r + 1},${y} ${x},${y + r + 1} ${x - r - 1},${y}" ${attrs}></polygon>`;
  return `<circle cx="${x}" cy="${y}" r="${r}" ${attrs}></circle>`;
}

function percentile(values, p) {
  const v = [...values].sort((a, b) => a - b);
  if (!v.length) return null;
  const idx = (p / 100) * (v.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  return v[lo] + (v[hi] - v[lo]) * (idx - lo);
}

function renderStrategy(pitEl, tableEl, strat, lapOnly = false) {
  const pl = strat.pit_loss;
  const tag = lapOnly ? 'EST, safety car laps not excluded' : 'EST';
  pitEl.innerHTML = isNum(pl)
    ? `Pit loss <span class="est">${tag}</span> <b class="mono">${pl.toFixed(1)} s</b> from ${strat.pit_loss_stops} stops`
    : `Pit loss <span class="est">${tag}</span> <b class="mono">${NA}</b> (${strat.pit_loss_stops} usable stops so far)`;
  if (lapOnly) {
    tableEl.innerHTML = '<div class="stopgrid">' +
      strat.rows.map((r) => `<div><b>${esc(r.acronym)}</b><span class="mono">${r.stops}</span></div>`).join('') + '</div>';
    return;
  }
  const body = strat.rows.map((r) => {
    let rj = NA;
    if (r.rejoin) {
      rj = `P${r.rejoin.position}`;
      if (r.rejoin.car_ahead) rj += ` behind ${esc(r.rejoin.car_ahead)} +${fmtSec(r.rejoin.gap_to_car_ahead, 1)}s`;
    }
    return `<tr><td><b>${esc(r.acronym)}</b></td><td class="r mono">${r.stops}</td>
      <td class="r mono">${isNum(r.stint_laps) ? r.stint_laps : NA}</td>
      <td>${r.needs_second_dry_compound ? 'yes' : 'no'}</td>
      <td class="mono">${rj}</td><td>${r.undercut_threat ? 'threat' : '-'}</td></tr>`;
  }).join('');
  tableEl.innerHTML = `<table><tr><th>DRIVER</th><th class="r">STOPS</th><th class="r">STINT LAPS</th><th>2ND DRY COMPOUND NEEDED</th>
    <th>REJOIN IF PIT NOW <span class="est">EST</span></th><th>UNDERCUT <span class="est">heuristic</span></th></tr>${body}</table>`;
}

function renderTyreSets(el, order, sets, acr) {
  el.innerHTML = '<div class="chips">' + order.map((n) => {
    const chips = (sets[n] || []).map((s) => `<span class="chip ${s.used ? 'used' : 'new'}">
      <span class="t" style="color:${TYRE_COL[s.compound] || '#888'};border-color:${TYRE_COL[s.compound] || '#888'}">${tyreLetter(s.compound)}</span>
      <span class="nu">${s.used ? 'used' : 'new'}</span></span>`).join('');
    return `<div class="chiprow"><span class="nm">${esc(acr.get(n))}</span>${chips || `<span class="na">${NA}</span>`}</div>`;
  }).join('') + '</div>';
}

function attachResize(el, draw) {
  el._draw = draw;
  if (el._ro || typeof ResizeObserver === 'undefined') return;
  let timer = null;
  el._ro = new ResizeObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => { if (el._draw && el.clientWidth !== el._w) el._draw(); }, 100);
  });
  el._ro.observe(el);
}

function renderStintChart(el, order, chart, acr, total, lap, lapOnly = false, noteEl = null) {
  const rowH = 16; const left = 48; const right = 10; const top = 4;
  function draw() {
    const W = Math.max(300, el.clientWidth);
    el._w = W;
    const bw = W - left - right;
    const x = (l) => left + (l / total) * bw;
    const step = bw / total >= 12 ? 5 : 10;
    let svg = '';
    order.forEach((n, i) => {
      const y = top + i * rowH;
      svg += `<text x="2" y="${y + 11}">${esc(acr.get(n))}</text>`;
      for (const s of chart[n] || []) {
        if (!isNum(s.first_lap) || !isNum(s.last_lap) || s.last_lap < s.first_lap) continue;
        const x0 = x(s.first_lap - 1); const x1 = x(Math.min(s.last_lap, total));
        svg += `<rect x="${x0}" y="${y}" width="${Math.max(1, x1 - x0 - 1)}" height="${rowH - 3}" fill="${TYRE_COL[s.compound] || '#888'}"></rect>`;
        if (!lapOnly && x1 - x0 > 14) svg += `<text x="${x0 + 3}" y="${y + 10}" style="fill:#0b0d10;font-weight:700">${tyreLetter(s.compound)}</text>`;
      }
    });
    const h = top + order.length * rowH + 18;
    for (let l = 0; l <= total; l += step) {
      svg += `<line class="axis" x1="${x(l)}" x2="${x(l)}" y1="${top}" y2="${h - 16}"></line><text x="${x(l) - 6}" y="${h - 4}">${l}</text>`;
    }
    el.innerHTML = `<svg class="chart-svg" width="${W}" height="${h}" viewBox="0 0 ${W} ${h}">${svg}</svg>`;
  }
  draw();
  attachResize(el, draw);
  if (noteEl) noteEl.textContent = `Drawn up to lap ${isNum(lap) ? lap : NA} of ${total}. ${lapOnly ? 'Grey segments are split at pit stops. Tyre compounds are not in the public lap data for this season.' : 'Letters: S soft, M medium, H hard, I intermediate, W wet.'}`;
}

function renderPace(el, noteEl, selected, pace, acr, colours, total, consts) {
  if (!selected.length) { el._draw = null; el.innerHTML = `<div class="na">${NA}</div>`; return; }
  const clean = [];
  for (const n of selected) for (const p of pace[n] || []) if (p.clean) clean.push(p.duration);
  if (!clean.length) {
    el._draw = null;
    el.innerHTML = `<div class="na">No clean laps yet (${NA}).</div>`;
    noteEl.textContent = '';
    return;
  }
  const p5 = percentile(clean, consts.PACE_LOW_PERCENTILE);
  const lo = p5 - consts.PACE_BELOW_S; const hi = p5 + consts.PACE_ABOVE_S;
  const H = 320; const L = 48; const B = 24; const T = 8; const R = 12;
  let legend = '';
  selected.forEach((n, i) => {
    const col = hex(colours.get(n)); const kind = SHAPES[i % SHAPES.length];
    legend += `<span>${`<svg width="12" height="12" viewBox="0 0 12 12">${shape(kind, 6, 6, 3, col, col, false)}</svg>`}${esc(acr.get(n))}</span>`;
  });
  function draw() {
    const W = Math.max(300, el.clientWidth);
    el._w = W;
    const x = (l) => L + ((l - 1) / Math.max(1, total - 1)) * (W - L - R);
    const y = (s) => T + (1 - (s - lo) / (hi - lo)) * (H - T - B);
    let svg = '';
    for (let k = 0; k <= 4; k += 1) {
      const v = lo + ((hi - lo) * k) / 4;
      svg += `<line class="axis" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"></line><text x="2" y="${y(v) + 3}">${v.toFixed(1)}</text>`;
    }
    for (let l = 0; l <= total; l += 10) {
      const lx = x(Math.max(1, l));
      svg += `<line class="axis" x1="${lx}" x2="${lx}" y1="${T}" y2="${H - B}"></line><text x="${lx - 6}" y="${H - 8}">${l}</text>`;
    }
    selected.forEach((n, i) => {
      const col = hex(colours.get(n)); const kind = SHAPES[i % SHAPES.length];
      const pts = (pace[n] || []).filter((p) => p.duration >= lo && p.duration <= hi);
      const line = pts.filter((p) => p.clean).map((p) => `${x(p.lap)},${y(p.duration)}`).join(' ');
      if (line) svg += `<polyline points="${line}" fill="none" stroke="${col}" stroke-opacity="0.35"></polyline>`;
      for (const p of pts) svg += shape(kind, x(p.lap), y(p.duration), 3, col, col, !p.clean);
    });
    el.innerHTML = `<svg class="chart-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${svg}</svg>
      <div class="legend">${legend}</div>`;
  }
  noteEl.textContent = `Solid points are clean laps, hollow points are other laps. Seconds per lap. Axis from the 5th percentile of shown clean laps minus ${consts.PACE_BELOW_S} s to plus ${consts.PACE_ABOVE_S} s.`;
  draw();
  attachResize(el, draw);
}

function renderUpgrades(el, data) {
  const panel = el.closest('.panel');
  if (!data || !data.items || !data.items.length) {
    if (panel) panel.classList.add('slim');
    el.innerHTML = '<span class="na">Team upgrades: no FIA car presentation document for this event.</span>';
    return;
  }
  if (panel) panel.classList.remove('slim');
  el.innerHTML = `<div class="caption flat">Source: ${esc(data.source ?? NA)}</div>` +
    '<table><tr><th>TEAM</th><th>COMPONENT</th><th>DESCRIPTION</th></tr>' +
    data.items.map((i) => `<tr><td>${esc(i.team ?? NA)}</td><td>${esc(i.component ?? NA)}</td><td style="white-space:normal">${esc(i.description ?? NA)}</td></tr>`).join('') + '</table>';
}

  F1APP.strategy = { percentile, renderStrategy, renderTyreSets, renderStintChart, renderPace, renderUpgrades };
})();
