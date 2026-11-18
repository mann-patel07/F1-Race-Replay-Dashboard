(function () {
  window.F1APP = window.F1APP || {};
const NA = 'n/a';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fmtLap(s) {
  if (!isNum(s)) return NA;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return `${m}:${r.toFixed(3).padStart(6, '0')}`;
}

function fmtSec(s, d = 3) {
  return isNum(s) ? s.toFixed(d) : NA;
}

function fmtSigned(s, d = 3) {
  if (!isNum(s)) return NA;
  const sign = s > 0 ? '+' : s < 0 ? '-' : '';
  return sign + Math.abs(s).toFixed(d);
}

function fmtClock(s) {
  if (!isNum(s)) return NA;
  const neg = s < 0;
  const a = Math.abs(Math.floor(s));
  const h = Math.floor(a / 3600);
  const m = Math.floor((a % 3600) / 60);
  const sec = a % 60;
  const mm = String(m).padStart(2, '0');
  const ss = String(sec).padStart(2, '0');
  return `${neg ? '-' : ''}${h}:${mm}:${ss}`;
}

function fmtGap(v) {
  if (v === null || v === undefined) return NA;
  if (isNum(v)) return '+' + v.toFixed(3);
  return String(v);
}

function fmtPoints(v) {
  return isNum(v) ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : NA;
}

const MARK = { purple: '◆', green: '●', yellow: '○' };

const TYRE_LETTER = { SOFT: 'S', MEDIUM: 'M', HARD: 'H', INTERMEDIATE: 'I', WET: 'W' };

function tyreLetter(c) {
  return TYRE_LETTER[c] || '?';
}

function hex(c) {
  return c ? '#' + c : '#888';
}

  F1APP.format = { NA, isNum, esc, fmtLap, fmtSec, fmtSigned, fmtClock, fmtGap, fmtPoints, MARK, TYRE_LETTER, tyreLetter, hex };
})();
