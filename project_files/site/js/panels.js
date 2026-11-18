(function () {
  window.F1APP = window.F1APP || {};
const { NA, isNum, esc, fmtClock, hex } = F1APP.format;

function renderTrack(el, track) {
  const sectors = track.yellow_sectors.length
    ? track.yellow_sectors.map((s) => `${s.sector}${s.level === 'DOUBLE YELLOW' ? ' (double)' : ''}`).join(', ')
    : 'none';
  const drs = track.drs_enabled === null || track.drs_enabled === undefined ? NA : track.drs_enabled ? 'ENABLED' : 'DISABLED';
  el.innerHTML = `<span class="kv">Yellow sectors: <b>${esc(sectors)}</b></span>
    <span class="kv">DRS: <b>${drs}</b></span>`;
}

function renderFeed(el, feed) {
  if (!feed.length) { el.innerHTML = `<div class="caption">No messages yet.</div>`; return; }
  el.innerHTML = feed.map((m) => `<div class="feed-row">
    <span class="mono dim">${fmtClock(m.t)}</span>
    <span class="mono dim">${isNum(m.lap) ? 'L' + m.lap : NA}</span>
    <span class="feed-tag tg-${esc(m.tag)}">${esc(m.tag)}</span>
    <span>${esc(m.message ?? NA)}</span></div>`).join('');
}

function renderWeather(el, w) {
  if (!w) { el.innerHTML = `<div class="caption">${NA}</div>`; return; }
  const v = (x, unit, d = 1) => (isNum(x) ? `${x.toFixed(d)}${unit}` : NA);
  const dir = isNum(w.wind_direction)
    ? `<span class="arrow" style="transform: rotate(${(w.wind_direction + 180) % 360}deg)">↑</span> ${Math.round(w.wind_direction)}°` : NA;
  const rain = isNum(w.rainfall) ? (w.rainfall > 0 ? 'yes' : 'no') : NA;
  el.innerHTML = `<div class="kvgrid">
    <div><span>AIR</span><b class="mono">${v(w.air_temperature, ' C')}</b></div>
    <div><span>TRACK</span><b class="mono">${v(w.track_temperature, ' C')}</b></div>
    <div><span>HUMIDITY</span><b class="mono">${v(w.humidity, ' %', 0)}</b></div>
    <div><span>WIND</span><b class="mono">${v(w.wind_speed, ' m/s')} ${dir}</b></div>
    <div><span>RAIN</span><b class="mono">${rain}</b></div>
    <div><span>PRESSURE</span><b class="mono">${v(w.pressure, ' mbar')}</b></div></div>`;
}

function renderForecast(el, fc) {
  if (!fc || !fc.length) { el.innerHTML = `<div class="caption">${NA}</div>`; return; }
  const c = (x, unit, d = 1) => (isNum(x) ? `${x.toFixed(d)}${unit}` : NA);
  el.innerHTML = `<table><tr><th>HOUR (UTC)</th><th class="r">RAIN PROB</th><th class="r">PRECIP</th><th class="r">TEMP</th><th class="r">WIND</th></tr>` +
    fc.map((r) => (r ? `<tr><td class="mono">${esc(r.time.slice(11, 16))}</td>
      <td class="r mono">${c(r.precipitation_probability, ' %', 0)}</td><td class="r mono">${c(r.precipitation, ' mm')}</td>
      <td class="r mono">${c(r.temperature_2m, ' C')}</td><td class="r mono">${c(r.wind_speed_10m, ' km/h')}</td></tr>`
      : `<tr><td colspan="5" class="na">${NA}</td></tr>`)).join('') + '</table>';
}

function createMap(el, meta) {
  const pts = meta.outline || [];
  if (!pts.length) { el.innerHTML = `<div class="caption">${NA}</div>`; return { update() {} }; }
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => -p[1]);
  const pad = 600;
  const minX = Math.min(...xs) - pad; const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad; const maxY = Math.max(...ys) + pad;
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${-p[1]}`).join(' ') + ' Z';
  const corners = (meta.corners || []).map((c) => `<text class="corner" x="${c.x + 260}" y="${-c.y - 120}" style="font-size:230px">${c.number}${esc(c.letter || '')}</text>`).join('');
  const dots = meta.drivers.map((d) => `<g class="dot" id="dot-${d.driver_number}" style="display:none">
    <circle r="150" fill="${hex(d.team_colour)}" stroke="#0b0d10" stroke-width="30"></circle>
    <text x="190" y="60" style="font-size:300px">${esc(d.acronym)}</text></g>`).join('');
  el.innerHTML = `<svg viewBox="${minX} ${minY} ${maxX - minX} ${maxY - minY}" preserveAspectRatio="xMidYMid meet">
    <path d="${path}" fill="none" stroke="#3a444f" stroke-width="160" stroke-linejoin="round"></path>
    <path d="${path}" fill="none" stroke="#14181d" stroke-width="90" stroke-linejoin="round"></path>${corners}${dots}</svg>`;
  const g = new Map(meta.drivers.map((d) => [d.driver_number, el.querySelector(`#dot-${d.driver_number}`)]));
  function update(state) {
    const seen = new Set();
    for (const m of state.map) {
      const dot = g.get(m.driver_number);
      if (!dot) continue;
      seen.add(m.driver_number);
      dot.style.display = '';
      dot.style.transform = `translate(${m.x}px, ${-m.y}px)`;
      dot.classList.toggle('dim', m.stale || m.status === 'PIT' || m.status === 'OUT');
    }
    for (const [n, dot] of g) if (!seen.has(n)) dot.style.display = 'none';
  }
  return { update };
}

const GEAR_NA = NA;

function renderTelemetry(el, label, t) {
  if (!t) { el.innerHTML = `<div class="caption">${NA}</div>`; label.textContent = ''; return; }
  const v = (x, d = 0, u = '') => (isNum(x) ? `${x.toFixed(d)}${u}` : NA);
  const thr = isNum(t.throttle) ? Math.max(0, Math.min(100, t.throttle)) : null;
  el.innerHTML = `<div class="tel-grid">
    <div><span>SPEED</span><b class="mono">${v(t.speed, 0, ' km/h')}</b></div>
    <div><span>GEAR</span><b class="mono">${isNum(t.gear) ? t.gear : GEAR_NA}</b></div>
    <div><span>RPM</span><b class="mono">${v(t.rpm, 0)}</b></div>
    <div><span>BRAKE</span><b class="mono">${t.brake === null || t.brake === undefined ? NA : t.brake ? 'ON' : 'off'}</b></div>
    <div><span>DRS</span><b class="mono">${t.drs || NA}</b></div>
    <div style="grid-column: 1 / -1"><span>THROTTLE ${thr === null ? NA : thr.toFixed(0) + ' %'}</span><div class="thr"><i style="width:${thr === null ? 0 : thr}%"></i></div></div></div>`;
}

function renderTrace(el, traces, acr, colours, points) {
  const shown = traces.filter((t) => t && t.points && t.points.length);
  if (!shown.length) { el.innerHTML = `<div class="caption flat">${NA}: no completed lap with car data yet.</div>`; return; }
  let legend = '';
  shown.forEach((t, i) => {
    const col = hex(colours.get(t.driver_number));
    legend += `<span style="color:${col}">${esc(acr.get(t.driver_number))} lap ${t.lap} (${i === 0 ? 'solid' : 'dashed'})</span> `;
  });
  el.innerHTML = `<div class="trace-plot"></div>
    <div class="legend">${legend}<span class="dim">Speed in km/h against the share of lap distance.</span></div>`;
  const plot = el.querySelector('.trace-plot');
  const W = Math.max(200, plot.clientWidth); const H = Math.max(90, plot.clientHeight);
  const L = 36; const B = 18; const T = 6; const R = 8;
  const vmax = Math.max(...shown.flatMap((t) => t.points.map((p) => p[1]))) * 1.05;
  const x = (f) => L + f * (W - L - R);
  const y = (s) => T + (1 - s / vmax) * (H - T - B);
  let svg = '';
  for (let k = 0; k <= 4; k += 1) {
    const sv = (vmax * k) / 4;
    svg += `<line class="axis" x1="${L}" x2="${W - R}" y1="${y(sv)}" y2="${y(sv)}"></line><text x="2" y="${y(sv) + 3}">${sv.toFixed(0)}</text>`;
  }
  shown.forEach((t, i) => {
    const col = hex(colours.get(t.driver_number));
    const dash = i === 0 ? '' : 'stroke-dasharray="5 3"';
    svg += `<polyline points="${t.points.map((p) => `${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(' ')}" fill="none" stroke="${col}" stroke-width="1.6" ${dash}></polyline>`;
  });
  plot.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${svg}</svg>`;
}

  F1APP.panels = { renderTrack, renderFeed, renderWeather, renderForecast, createMap, renderTelemetry, renderTrace };
})();
