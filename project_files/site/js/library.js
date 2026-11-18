(function () {
  window.F1APP = window.F1APP || {};
  const { NA, esc } = F1APP.format;
  const STATE_TEXT = { full: 'Full replay ready', lap_only: 'Lap-only replay ready', none: 'Not downloaded', not_held: 'Not held yet' };
  const NOT_DOWNLOADED = 'Not downloaded yet. To add it, open the project_files folder and double-click update_data.command.';

  function libraryIndex() {
    const lib = F1DATA.get('_library', 'index');
    return lib || { seasons: {}, built: null };
  }

  function newestFullRace() {
    const lib = libraryIndex();
    const seasons = Object.keys(lib.seasons).sort((a, b) => b - a);
    for (const s of seasons) {
      const races = lib.seasons[s].slice().sort((a, b) => (a.date < b.date ? 1 : -1));
      for (const r of races) for (const x of r.sessions) if (x.state === 'full') return x.load_id;
    }
    return null;
  }

  function createLibrary(root, onLoad) {
    let season = null;
    let open = null;

    function render() {
      const lib = libraryIndex();
      const seasons = Object.keys(lib.seasons).sort((a, b) => b - a);
      if (season === null && seasons.length) season = seasons[0];
      const races = (lib.seasons[season] || []).map((r) => {
        const chips = r.sessions.map((s) => {
          const key = `${r.season}-${r.round}-${s.name}`;
          let label = `${s.name}: ${STATE_TEXT[s.state]}`;
          if (s.state === 'full' && s.has_lap_only) label += ' (also lap-only)';
          const attr = s.state === 'full' || s.state === 'lap_only' ? `data-load="${esc(s.load_id)}"` : s.state === 'none' ? `data-open="${esc(key)}"` : '';
          const size = s.bytes ? ` ${(s.bytes / 1e6).toFixed(1)} MB` : '';
          return `<span class="chip2 ${s.state}" ${attr}>${esc(label)}${size}</span>`;
        }).join('');
        const note = r.sessions.some((s) => open === `${r.season}-${r.round}-${s.name}`) ? `<div class="choices"><span class="na">${esc(NOT_DOWNLOADED)}</span></div>` : '';
        return `<div class="race-row"><span class="mono dim">${r.round}</span><span class="rn">${esc(r.name)}<small>${esc(r.circuit)}</small></span>
          <span class="mono dim">${esc(r.date)}</span><span class="sess">${chips}</span>${note}</div>`;
      }).join('');
      const keep = root.querySelector('.lib-list');
      const scrollTop = keep ? keep.scrollTop : 0;
      root.innerHTML = `<div class="modal" role="dialog" aria-label="Library"><div class="lib-head"><h2>Library</h2>
        <span class="dim">Built ${esc(lib.built || NA)}</span><button type="button" id="lib-close">Close</button></div>
        <div class="seasons">${seasons.map((s) => `<button type="button" data-season="${s}" class="${s === season ? 'on' : ''}">${s}</button>`).join('')}</div>
        <p class="dim lib-note">Seasons 2007 to 2020 are lap-only. Full replays start at 2021.</p>
        <div class="lib-list">${races || `<span class="na">${NA}</span>`}</div></div>`;
      const list = root.querySelector('.lib-list');
      if (list) list.scrollTop = scrollTop;
    }

    root.addEventListener('click', (e) => {
      if (e.target === root) { root.hidden = true; return; }
      const t = e.target.closest('button, .chip2');
      if (!t) return;
      if (t.id === 'lib-close') { root.hidden = true; return; }
      if (t.dataset.season) { season = t.dataset.season; open = null; render(); return; }
      if (t.dataset.load) { root.hidden = true; onLoad(t.dataset.load); return; }
      if (t.dataset.open) { open = open === t.dataset.open ? null : t.dataset.open; render(); }
    });

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !root.hidden) root.hidden = true; });
    return { show() { root.hidden = false; render(); }, hide() { root.hidden = true; } };
  }

  F1APP.library = { createLibrary, newestFullRace, libraryIndex };
})();
