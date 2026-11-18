(function () {
  window.F1APP = window.F1APP || {};
  const { NA, isNum, fmtClock, esc } = F1APP.format;
  const { createTower } = F1APP.tower;
  const { renderTrack, renderFeed, renderWeather, renderForecast, createMap, renderTelemetry, renderTrace } = F1APP.panels;
  const { renderStandings } = F1APP.standings;
  const { renderStrategy, renderTyreSets, renderStintChart, renderPace, renderUpgrades } = F1APP.strategy;
  const E = F1APP.engine;
  const $ = (id) => document.getElementById(id);
  const TRACE_MAX = 2;
  const POINTS_NOTE = 'Standard points table for that season. Half points, double points and other special cases are not applied.';

  let timer = null;
  let cur = null;

  function parseHash() {
    const out = {};
    for (const part of window.location.hash.replace(/^#/, '').split('&')) {
      const [k, v] = part.split('=');
      if (k) out[k] = decodeURIComponent(v || '');
    }
    return out;
  }

  function setHash(race, t) {
    const text = `#race=${encodeURIComponent(race)}&t=${Math.round(t * 10) / 10}`;
    if (window.location.hash !== text) window.history.replaceState(null, '', text);
  }

  function resetDom() {
    for (const id of ['tower', 'map', 'trace', 'telemetry']) $(id).innerHTML = '';
    $('tower').className = '';
    document.body.classList.remove('lap-only');
    $('banner').hidden = true;
    $('missing-list').innerHTML = '';
    $('h-ticker').textContent = '';
    $('h-ticker').removeAttribute('title');
    $('speeds').innerHTML = '';
  }

  async function openRace(raceId, startT) {
    if (timer) { clearInterval(timer); timer = null; }
    $('loading').hidden = false;
    $('loading').textContent = 'Loading race data';
    let loaded;
    try {
      loaded = await F1DATA.load(raceId, (done, total) => { $('loading').textContent = `Loading race data: ${done} of ${total} files`; });
    } catch (err) {
      $('loading').textContent = `Could not load ${raceId}: ${err.message}`;
      return;
    }
    $('loading').hidden = true;
    resetDom();
    const bundle = E.makeBundle(loaded.meta, loaded.tables, loaded.extra);
    const m = loaded.meta;
    const meta = Object.assign({}, m, {
      constants: loaded.constants, start_points_source: m.start_points.source, race_id: m.race_id || raceId,
      session_name: m.session_name || m.session.session_name || 'Race', kind: m.kind || 'full', missing: m.missing || [], corners: m.corners || [],
    });
    cur = { raceId, bundle, meta };
    start(meta, bundle, loaded, startT);
  }

  function start(meta, bundle, loaded, startT) {
    const C = meta.constants;
    const lapOnly = meta.kind === 'lap_only';
    if (lapOnly) document.body.classList.add('lap-only');
    const acr = new Map(meta.drivers.map((d) => [d.driver_number, d.acronym]));
    const colours = new Map(meta.drivers.map((d) => [d.driver_number, d.team_colour]));
    const date = (meta.session.date_start || '').slice(0, 10);
    const sprint = meta.session_name === 'Sprint';
    $('event-name').textContent = `${meta.meeting.meeting_name || NA} ${meta.season}, ${meta.session_name}`;
    $('event-date').textContent = date;
    $('source-tag').textContent = meta.source === 'fastf1' ? 'data: FastF1 archive' : meta.source === 'openf1' ? 'data: OpenF1' : 'data: Jolpica-F1 (lap-only)';
    document.title = `Replay: ${meta.meeting.meeting_name || 'F1'} ${meta.season} ${meta.session_name}`;
    if (meta.missing.length) {
      $('banner').hidden = false;
      $('banner-text').textContent = `Lap-only replay: ${meta.missing.length} things are not available for this race. See the list on the right.`;
      $('missing-list').innerHTML = meta.missing.map((x) => `<li>${esc(x)}</li>`).join('');
    }

    const clock = { t: isNum(startT) ? startT : meta.replay_start_t, speed: C.REPLAY_SPEEDS[0], playing: false, wall: 0 };
    const lo = meta.replay_start_t; const hi = meta.replay_end_t;
    clock.t = Math.min(hi, Math.max(lo, clock.t));
    const now = () => (clock.playing ? Math.min(hi, clock.t + (clock.speed * (performance.now() - clock.wall)) / 1000) : clock.t);
    const freeze = () => { clock.t = now(); clock.wall = performance.now(); };
    function setPlaying(p) { freeze(); clock.playing = p; $('btn-play').textContent = p ? 'Pause' : 'Play'; }
    $('btn-play').textContent = 'Play';
    function seek(t) { clock.t = Math.min(hi, Math.max(lo, t)); clock.wall = performance.now(); poll(); }

    const scrub = $('scrub');
    scrub.min = lo; scrub.max = hi; scrub.value = clock.t;
    scrub.oninput = () => seek(Number(scrub.value));
    $('btn-play').onclick = () => { if (clock.t >= hi) seek(lo); setPlaying(!clock.playing); };
    for (const sp of C.REPLAY_SPEEDS) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = `${sp}x`; b.dataset.speed = sp;
      b.addEventListener('click', () => {
        freeze(); clock.speed = sp;
        document.querySelectorAll('#speeds button').forEach((x) => x.classList.toggle('on', Number(x.dataset.speed) === sp));
      });
      if (sp === clock.speed) b.classList.add('on');
      $('speeds').appendChild(b);
    }
    const jump = () => {
      const t = meta.seek_index[String(Math.round(Number($('jump-lap').value)))];
      if (isNum(t)) seek(t);
    };
    $('btn-jump').onclick = jump;
    $('jump-lap').onkeydown = (e) => { if (e.key === 'Enter') jump(); };
    $('jump-lap').max = meta.total_laps;

    let selected = [];
    let lastState = null;
    const tower = createTower($('tower'), meta, (n) => {
      selected = selected.includes(n) ? selected.filter((x) => x !== n) : [...selected, n].slice(-C.PACE_MAX_DRIVERS);
      poll();
    });
    const map = createMap($('map'), meta);
    renderUpgrades($('upgrades'), loaded.extra.upgrades);
    const traceCache = new Map();

    function render(s) {
      lastState = s;
      const h = s.header;
      $('h-lap').textContent = isNum(h.lap) ? `${h.lap} / ${h.total_laps}` : `${NA} / ${h.total_laps}`;
      $('h-elapsed').textContent = fmtClock(h.elapsed);
      $('h-remaining').textContent = isNum(h.time_remaining_est) ? fmtClock(h.time_remaining_est) : NA;
      const pill = $('h-track');
      if (lapOnly) { pill.textContent = NA; pill.className = 'pill'; } else {
        const extra = s.track.yellow_sectors.length ? ` +Y${s.track.yellow_sectors.length}` : '';
        pill.textContent = s.track.state + extra;
        pill.className = `pill ${s.track.state.replace(' ', '')}`;
      }
      $('h-clock').textContent = `REPLAY t = ${fmtClock(s.t)} (session clock)`;
      if (document.activeElement !== scrub) scrub.value = s.t;
      scrub.style.setProperty('--fill', `${hi > lo ? Math.max(0, Math.min(100, ((Number(scrub.value) - lo) / (hi - lo)) * 100)) : 0}%`);
      if (document.activeElement !== $('jump-lap') && isNum(h.lap)) $('jump-lap').value = h.lap;
      const order = s.rows.map((r) => r.driver_number);
      const top3 = s.rows.filter((r) => isNum(r.position)).slice(0, 3).map((r) => r.driver_number);
      const chosen = selected.length ? selected : top3;
      tower.update(s, selected);
      if (!lapOnly) {
        renderTrack($('track-status'), s.track);
        const latest = s.feed.length ? s.feed[0] : null;
        const msg = latest ? `${latest.tag}: ${latest.message ?? NA}` : 'No race control messages yet.';
        $('h-ticker').textContent = msg;
        $('h-ticker').title = latest ? `${fmtClock(latest.t)} ${isNum(latest.lap) ? 'L' + latest.lap + ' ' : ''}${msg}` : msg;
        map.update(s);
        renderFeed($('feed'), s.feed);
        renderForecast($('forecast'), s.forecast);
        renderTyreSets($('tyresets'), order, s.tyre_sets, acr);
        const first = selected[0] !== undefined ? selected[0] : top3[0];
        $('tel-driver').textContent = first !== undefined ? `${acr.get(first)}. Click a tower row to change the driver.` : '';
        renderTelemetry($('telemetry'), $('tel-driver'), first !== undefined ? E.telemetryAt(bundle, s.t, first) : null);
        const list = [];
        for (const n of (selected.length ? selected : top3.slice(0, 1)).slice(0, TRACE_MAX)) {
          const row = s.rows.find((r) => r.driver_number === n);
          const key = `${n}:${row ? row.completed_laps : 0}`;
          if (!traceCache.has(key)) traceCache.set(key, E.lapTrace(bundle, s.t, n));
          list.push(traceCache.get(key));
        }
        renderTrace($('trace'), list, acr, colours);
      }
      renderWeather($('weather'), s.weather);
      renderStandings($('st-drivers'), $('st-teams'), $('standings-note'), s.standings, meta);
      $('standings-note').textContent = `${POINTS_NOTE}${sprint ? ' Sprint points table.' : ''} ${$('standings-note').textContent}`;
      if (lapOnly) renderStrategy($('pitloss-lap'), $('strategy-lap'), s.strategy, true);
      else renderStrategy($('pitloss'), $('strategy'), s.strategy, false);
      renderStintChart($('stintchart'), order, s.stint_chart, acr, meta.total_laps, h.lap, lapOnly, $('stint-note'));
      renderPace($('pacechart'), $('pace-note'), chosen, s.pace_chart, acr, colours, meta.total_laps, C);
    }

    let lastHash = 0;
    function poll() {
      const t = now();
      render(E.hideForKind(E.stateAt(bundle, t), meta.kind));
      if (clock.playing && t >= hi) setPlaying(false);
      if (performance.now() - lastHash > 1000) { setHash(meta.race_id, t); lastHash = performance.now(); }
    }
    F1APP.current = { poll, seek, bundle, meta, clock };
    timer = setInterval(poll, C.POLL_MS);
    poll();
    setHash(meta.race_id, clock.t);
  }

  function initTabs() {
    let tab = 'telemetry';
    try { const v = window.localStorage.getItem('f1_info_tab'); if (v === 'feed' || v === 'weather' || v === 'telemetry') tab = v; } catch (e) { /* storage unavailable */ }
    const show = (name) => {
      document.querySelectorAll('#info-panel .tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
      document.querySelectorAll('#info-panel .tabpane').forEach((p) => { p.hidden = p.dataset.pane !== name; });
    };
    show(tab);
    document.querySelectorAll('#info-panel .tabs button').forEach((b) => b.addEventListener('click', () => {
      show(b.dataset.tab);
      try { window.localStorage.setItem('f1_info_tab', b.dataset.tab); } catch (e) { /* storage unavailable */ }
      if (F1APP.current) F1APP.current.poll();
    }));
  }

  function boot() {
    const lib = F1APP.library.createLibrary($('library'), (id) => openRace(id, null));
    $('btn-library').addEventListener('click', () => lib.show());
    initTabs();
    const h = parseHash();
    const id = h.race || F1APP.library.newestFullRace();
    if (!id) { $('loading').hidden = false; $('loading').textContent = 'No race is ready yet. See the README, Add more races.'; return; }
    openRace(id, h.t !== undefined ? Number(h.t) : null);
    window.addEventListener('hashchange', () => {
      const n = parseHash();
      if (n.race && cur && n.race !== cur.raceId) openRace(n.race, n.t !== undefined ? Number(n.t) : null);
    });
  }

  F1APP.app = { openRace, boot };
  document.addEventListener('DOMContentLoaded', boot);
})();
