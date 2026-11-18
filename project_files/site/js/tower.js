(function () {
  window.F1APP = window.F1APP || {};
const { NA, isNum, esc, fmtLap, fmtSec, fmtSigned, fmtGap, MARK, tyreLetter, hex } = F1APP.format;

const ROW_MIN = 22;
const ROW_MAX = 44;
const ROW_STACKED = 24;
const HEAD_H = 24;

const HEAD = [
  ['pos', 'P', ''], ['gain', '+/-', ''], ['bar', '', ''], ['drv', 'DRIVER', ''], ['tyre', 'TYRE', ''],
  ['pit', 'PIT', 'r'], ['int', 'INT', 'r'], ['gap', 'GAP', 'r'], ['cur', 'CUR LAP', 'r'],
  ['s1', 'S1', 'r'], ['s2', 'S2', 'r'], ['s3', 'S3', 'r'], ['mini', 'MINI', ''],
  ['last', 'LAST', 'r'], ['delta', 'DELTA', 'r'], ['best', 'BEST', 'r'], ['drs', 'DRS', ''],
  ['notes', 'NOTES', ''], ['status', 'STATUS', ''],
];

function createTower(container, meta, onSelect) {
  const nms = meta.constants.N_MINISECTORS;
  const head = document.createElement('div');
  head.className = 'thead';
  for (const [key, label, align] of HEAD) {
    const s = document.createElement('span');
    s.className = `c-${key} ${align}`;
    s.textContent = meta.kind === 'lap_only' && (key === 'int' || key === 'gap') ? `${label} (computed)` : label;
    if (key === 'mini') s.title = 'Mini-sectors computed from car speed. Not official.';
    head.appendChild(s);
  }
  container.appendChild(head);
  if (!meta.has_drs) container.classList.add('no-drs');
  const rows = new Map();
  for (const d of meta.drivers) {
    const el = document.createElement('div');
    el.className = 'trow';
    el.dataset.n = d.driver_number;
    for (const [key, , align] of HEAD) {
      const s = document.createElement('span');
      s.className = `c-${key} ${align}`;
      el.appendChild(s);
    }
    const bar = el.querySelector('.c-bar');
    bar.style.background = hex(d.team_colour);
    el.querySelector('.c-drv').innerHTML = `<b>${esc(d.acronym)}</b><i>${d.driver_number}</i>`;
    el.addEventListener('click', () => onSelect(d.driver_number));
    container.appendChild(el);
    rows.set(d.driver_number, el);
  }
  let rowH = ROW_MIN;
  let lastState = null;
  let lastSelected = [];
  const scroll = container.parentElement;
  const stacked = window.matchMedia('(max-width: 1279px)');

  function measure() {
    if (stacked.matches) return ROW_STACKED;
    const body = scroll.clientHeight;
    if (!body) return rowH;
    return Math.max(ROW_MIN, Math.min(ROW_MAX, Math.floor((body - HEAD_H) / meta.drivers.length)));
  }

  function applyHeight() {
    container.style.setProperty('--row-h', `${rowH}px`);
    container.style.height = `${HEAD_H + meta.drivers.length * rowH + 2}px`;
  }
  applyHeight();

  function cell(el, key) {
    return el.querySelector(`.c-${key}`);
  }

  function sectorHtml(s) {
    if (!s) return `<span class="na">${NA}</span>`;
    return `<span class="${s.colour}"><span class="mk">${MARK[s.colour]}</span>${fmtSec(s.value)}</span>`;
  }

  function update(state, selected) {
    lastState = state; lastSelected = selected;
    const want = measure();
    if (want !== rowH) { rowH = want; applyHeight(); }
    state.rows.forEach((r, i) => {
      const el = rows.get(r.driver_number);
      if (!el) return;
      el.style.transform = `translateY(${i * rowH}px)`;
      el.className = `trow st-${r.status}` + (i % 2 ? ' alt' : '') + (selected.includes(r.driver_number) ? ' sel' : '');
      cell(el, 'pos').textContent = isNum(r.position) ? r.position : NA;
      const g = cell(el, 'gain');
      if (!isNum(r.gained)) { g.className = 'c-gain na'; g.textContent = NA; }
      else if (r.gained > 0) { g.className = 'c-gain up'; g.textContent = `▲${r.gained}`; }
      else if (r.gained < 0) { g.className = 'c-gain down'; g.textContent = `▼${-r.gained}`; }
      else { g.className = 'c-gain flat'; g.textContent = '='; }
      const ty = cell(el, 'tyre');
      if (r.tyre) {
        const age = isNum(r.tyre.age) ? `${r.tyre.age}${r.tyre.age_unknown_start ? '?' : ''}` : NA;
        ty.innerHTML = `<span class="tyre"><span class="t ${r.tyre.compound}">${tyreLetter(r.tyre.compound)}</span><span class="a mono">${age}</span></span>`;
      } else ty.innerHTML = `<span class="na">${NA}</span>`;
      cell(el, 'pit').textContent = r.pit_stops;
      if (meta.kind === 'lap_only') {
        cell(el, 'int').textContent = fmtGap(r.interval);
        cell(el, 'gap').textContent = fmtGap(r.gap);
        cell(el, 'last').textContent = fmtLap(r.last_lap);
        const dl0 = cell(el, 'delta');
        dl0.textContent = fmtSigned(r.delta_prev);
        dl0.className = 'c-delta r ' + (isNum(r.delta_prev) ? (r.delta_prev < 0 ? 'green' : r.delta_prev > 0 ? 'yellow' : '') : 'na');
        const b0 = cell(el, 'best');
        b0.innerHTML = r.best_is_overall && isNum(r.best_lap)
          ? `<span class="purple"><span class="mk">${MARK.purple}</span>${fmtLap(r.best_lap)}</span>` : fmtLap(r.best_lap);
        const st0 = cell(el, 'status');
        st0.className = 'c-status status';
        st0.textContent = r.status;
        return;
      }
      cell(el, 'int').textContent = fmtGap(r.interval);
      cell(el, 'gap').textContent = fmtGap(r.gap);
      cell(el, 'cur').textContent = fmtLap(r.current_lap_time);
      for (let k = 0; k < 3; k += 1) cell(el, `s${k + 1}`).innerHTML = sectorHtml(r.sectors[k]);
      let ms = '<span class="ms">';
      for (let k = 0; k < nms; k += 1) {
        const m = r.minisectors[k];
        ms += m ? `<i class="${m.colour}" title="${fmtSec(m.value)}"></i>` : '<i></i>';
      }
      cell(el, 'mini').innerHTML = ms + '</span>';
      cell(el, 'last').textContent = fmtLap(r.last_lap);
      const dl = cell(el, 'delta');
      dl.textContent = fmtSigned(r.delta_prev);
      dl.className = 'c-delta r ' + (isNum(r.delta_prev) ? (r.delta_prev < 0 ? 'green' : r.delta_prev > 0 ? 'yellow' : '') : 'na');
      const b = cell(el, 'best');
      b.innerHTML = r.best_is_overall && isNum(r.best_lap)
        ? `<span class="purple"><span class="mk">${MARK.purple}</span>${fmtLap(r.best_lap)}</span>` : fmtLap(r.best_lap);
      if (meta.has_drs) {
        const d = cell(el, 'drs');
        d.textContent = r.drs || NA;
        d.className = 'c-drs ' + (r.drs === 'ON' ? 'green' : r.drs === 'ELIG' ? 'yellow' : 'dim');
      }
      let notes = '';
      if (r.penalty_s > 0) notes += `<span class="tag pen" title="Time penalty seconds issued">+${r.penalty_s}s</span>`;
      if (r.investigations > 0) notes += `<span class="tag inv" title="Open investigations">INV</span>`;
      if (r.track_limits > 0) notes += `<span class="tag tl" title="Lap times deleted for track limits">TL ${r.track_limits}</span>`;
      if (r.black_white) notes += '<span class="tag bw">B/W</span>';
      const notesCell = cell(el, 'notes');
      notesCell.innerHTML = notes;
      notesCell.title = [...notesCell.children].map((n) => n.textContent).join(' ');
      const st = cell(el, 'status');
      st.className = 'c-status status';
      st.textContent = r.status;
    });
  }
  function relayout() {
    const want = measure();
    if (want === rowH) return;
    rowH = want; applyHeight();
    if (lastState) update(lastState, lastSelected);
  }
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(relayout).observe(scroll);
  window.addEventListener('resize', relayout);
  return { update, relayout };
}

  F1APP.tower = { createTower };
})();
