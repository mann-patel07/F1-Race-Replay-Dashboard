(function () {
  window.F1APP = window.F1APP || {};
const { NA, isNum, esc, fmtPoints, hex } = F1APP.format;

function change(c) {
  if (!isNum(c)) return `<span class="na">${NA}</span>`;
  if (c > 0) return `<span class="up">▲${c}</span>`;
  if (c < 0) return `<span class="down">▼${-c}</span>`;
  return '<span class="flat">=</span>';
}

function table(title, rows, label, colourOf) {
  const body = rows.map((r) => `<tr><td class="mono">${isNum(r.new_rank) ? r.new_rank : NA}</td>
    <td><span class="tbar" style="background:${colourOf(r)}"></span>${esc(label(r))}</td>
    <td class="r mono"><b>${fmtPoints(r.projected)}</b></td>
    <td class="r mono">${isNum(r.race_points) ? '+' + r.race_points : NA}</td><td class="r">${change(r.rank_change)}</td></tr>`).join('');
  return `<table><tr><th colspan="5">${title}</th></tr><tr><th>#</th><th>NAME</th><th class="r">PTS</th><th class="r">THIS RACE</th><th class="r">RANK</th></tr>${body}</table>`;
}

function renderStandings(elD, elT, noteEl, st, meta) {
  const col = new Map(meta.drivers.map((d) => [d.driver_number, hex(d.team_colour)]));
  const teamCol = new Map(meta.drivers.map((d) => [d.team_name, hex(d.team_colour)]));
  elD.innerHTML = table('DRIVERS', st.drivers, (r) => r.acronym + ' ' + r.name, (r) => col.get(r.driver_number) || '#888');
  elT.innerHTML = table('CONSTRUCTORS', st.constructors, (r) => r.team_name, (r) => teamCol.get(r.team_name) || '#888');
  noteEl.textContent = st.source && st.source.startsWith('OpenF1') ? '' : `Start points source: ${st.source}`;
}

  F1APP.standings = { renderStandings };
})();
