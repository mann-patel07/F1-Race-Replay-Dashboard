(function () {
  window.F1APP = window.F1APP || {};
  const DATA_BASE = 'project_files/site/data/';
  const MS_NULL = -2147483648;
  const store = {};
  const loadedFiles = new Set();

  function put(race, table, obj) {
    const r = store[race] || (store[race] = {});
    if (obj && obj.part) {
      const cur = r[table] && r[table].isParts ? r[table] : (r[table] = { isParts: true, parts: [] });
      cur.parts.push(obj);
    } else {
      r[table] = obj;
    }
  }

  function bytesOf(b64) {
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) u8[i] = bin.charCodeAt(i);
    return u8;
  }

  const WIDTH = { ms: 4, i32: 4, u16: 2, str: 2, u8: 1, bool: 1 };

  function rawBytes(col) {
    if (!col.enc) return bytesOf(col.b64);
    const w = WIDTH[col.type];
    const vals = bytesOf(col.b64);
    if (col.enc === 'rle') {
      const rb = bytesOf(col.runs);
      const runs = new Uint32Array(rb.buffer, 0, rb.length >> 2);
      let n = 0;
      for (let i = 0; i < runs.length; i += 1) n += runs[i];
      const out = new Uint8Array(n * w);
      let o = 0;
      for (let i = 0; i < runs.length; i += 1) {
        const piece = vals.subarray(i * w, i * w + w);
        for (let r = 0; r < runs[i]; r += 1) { out.set(piece, o); o += w; }
      }
      return out;
    }
    const wide = col.enc === 'd16';
    const deltas = wide ? new Int16Array(vals.buffer, 0, vals.length >> 1) : new Int8Array(vals.buffer, 0, vals.length);
    const escape = wide ? -32768 : -128;
    const eb = bytesOf(col.exc);
    const Raw = w === 4 ? Int32Array : w === 2 ? Uint16Array : Uint8Array;
    const exc = new Raw(eb.buffer, 0, eb.length / w);
    const out = new Raw(deltas.length);
    let k = 0;
    let prev = 0;
    for (let i = 0; i < deltas.length; i += 1) {
      prev = deltas[i] === escape ? exc[k++] : prev + deltas[i];
      out[i] = prev;
    }
    return new Uint8Array(out.buffer, 0, out.byteLength);
  }

  function decodeColumn(col, n) {
    const u8 = rawBytes(col);
    let out;
    switch (col.type) {
      case 'ms': {
        const a = new Int32Array(u8.buffer, 0, u8.length >> 2);
        out = new Float64Array(a.length);
        for (let i = 0; i < a.length; i += 1) out[i] = a[i] === MS_NULL ? NaN : a[i] / 1000;
        return out;
      }
      case 'u8': {
        out = new Float64Array(u8.length);
        for (let i = 0; i < u8.length; i += 1) out[i] = u8[i] === 255 ? NaN : u8[i];
        return out;
      }
      case 'u16': {
        const a = new Uint16Array(u8.buffer, 0, u8.length >> 1);
        out = new Float64Array(a.length);
        for (let i = 0; i < a.length; i += 1) out[i] = a[i] === 65535 ? NaN : a[i];
        return out;
      }
      case 'i32': {
        const a = new Int32Array(u8.buffer, 0, u8.length >> 2);
        out = new Float64Array(a.length);
        for (let i = 0; i < a.length; i += 1) out[i] = a[i] === 2147483647 ? NaN : a[i];
        return out;
      }
      case 'f64':
        return new Float64Array(u8.buffer, 0, u8.length >> 3).slice();
      case 'bool': {
        out = new Array(u8.length);
        for (let i = 0; i < u8.length; i += 1) out[i] = u8[i] === 2 ? null : u8[i] === 1;
        return out;
      }
      case 'str': {
        const a = new Uint16Array(u8.buffer, 0, u8.length >> 1);
        out = new Array(a.length);
        for (let i = 0; i < a.length; i += 1) out[i] = a[i] === 65535 ? null : col.dict[a[i]];
        return out;
      }
      default:
        throw new Error('unknown column type ' + col.type);
    }
  }

  function concatColumns(list) {
    if (list.length === 1) return list[0];
    if (list[0] instanceof Float64Array) {
      let total = 0;
      for (const c of list) total += c.length;
      const out = new Float64Array(total);
      let o = 0;
      for (const c of list) { out.set(c, o); o += c.length; }
      return out;
    }
    return [].concat(...list);
  }

  function decodeTable(obj) {
    const parts = obj.isParts ? obj.parts : [obj];
    const names = Object.keys(parts[0].cols);
    const cols = {};
    let n = 0;
    for (const p of parts) n += p.n;
    for (const name of names) cols[name] = concatColumns(parts.map((p) => decodeColumn(p.cols[name], p.n)));
    return { n, cols };
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error('could not load ' + src));
      document.head.appendChild(s);
    });
  }

  async function loadMeta(race) {
    if (!store[race] || !store[race].meta) await loadScript(`${DATA_BASE}races/${race}/meta.js`);
    loadedFiles.add(`${race}/meta.js`);
    return store[race].meta;
  }

  async function load(race, progress) {
    const info = await loadMeta(race);
    const files = [];
    for (const table of Object.keys(info.files)) {
      for (const f of info.files[table]) files.push([table, f]);
    }
    let done = 0;
    if (progress) progress(done, files.length);
    for (const [table, f] of files) {
      const key = `${race}/${f}`;
      if (!loadedFiles.has(key)) {
        await loadScript(`${DATA_BASE}races/${race}/${f}`);
        loadedFiles.add(key);
      }
      done += 1;
      if (progress) progress(done, files.length);
    }
    const tables = {};
    for (const table of Object.keys(info.files)) tables[table] = decodeTable(store[race][table]);
    return { meta: info.meta, tables, extra: { forecast: info.forecast, session_result: info.session_result, upgrades: info.upgrades },
             constants: info.constants, downsampled: info.downsampled };
  }

  window.F1DATA = { put, load, loadMeta, decodeColumn, rawBytes, decodeTable, get: (race, table) => (store[race] || {})[table], store };
  F1APP.data = window.F1DATA;
})();
