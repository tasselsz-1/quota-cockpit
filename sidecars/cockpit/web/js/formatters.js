(function (QC) {
  'use strict';

  const { state, SEG_COLORS } = QC;

  const nowSec = () => Math.floor(Date.now() / 1000) + state.clockOffsetSec;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  function fmtTok(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
    return String(Math.round(v));
  }

  function fmtClock(ts) {
    if (!ts || ts <= 0) return '--:--:--';
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('zh-CN', { hour12: false });
  }

  function fmtHM(ts) {
    if (!ts || ts <= 0) return '--:--';
    const d = new Date(ts * 1000);
    const pad = (x) => String(x).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtUsd(v) {
    const n = Number(v) || 0;
    if (n <= 0) return '$0.00';
    if (n < 0.1) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }

  function fmtDateTime(ts) {
    if (!ts || ts <= 0) return '--';
    const d = new Date(ts * 1000);
    const pad = (x) => String(x).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function fmtMD(ts) {
    if (!ts || ts <= 0) return '--';
    const d = new Date(ts * 1000);
    const pad = (x) => String(x).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function fmtDuration(sec, zeroText = '已重置') {
    const s = Math.floor(Number(sec) || 0);
    if (s <= 0) return zeroText;
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (d > 0) return `${d}天${h}时`;
    if (h > 0) return `${h}时${String(m).padStart(2, '0')}分${String(r).padStart(2, '0')}秒`;
    if (m > 0) return `${m}分${String(r).padStart(2, '0')}秒`;
    return `${r}秒`;
  }

  function cdSpan(ts, zeroText = '已重置') {
    if (!ts || ts <= 0) return `<span class="num">${esc(zeroText)}</span>`;
    return `<span class="num" data-cd-ts="${Math.round(ts)}" data-cd-zero="${esc(zeroText)}">${esc(fmtDuration(ts - nowSec(), zeroText))}</span>`;
  }

  function fmtElapsed(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    if (s < 60) return `${s}秒前`;
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (d > 0) return `${d}天${h}时前`;
    if (h > 0) return `${h}时${String(m).padStart(2, '0')}分前`;
    return `${m}分${String(r).padStart(2, '0')}秒前`;
  }

  function elapsedTextWithHint(elapsedSec, hintCool) {
    const base = fmtElapsed(elapsedSec);
    if (!hintCool) return base;
    if (elapsedSec >= 360) return `${base} · 可能已冷却`;
    if (elapsedSec >= 270) return `${base} · 临近冷却`;
    return base;
  }

  function elapsedSpan(ts, hintCool = true) {
    if (!ts || ts <= 0) return '';
    const el = Math.max(0, nowSec() - ts);
    const warnCls = hintCool && el >= 270 ? ' up-warn' : '';
    return `<span class="num${warnCls}" data-up-ts="${Math.round(ts)}" data-up-cool="${hintCool ? '1' : '0'}">${esc(elapsedTextWithHint(el, hintCool))}</span>`;
  }

  function tickCountdowns() {
    const cur = nowSec();
    document.querySelectorAll('[data-cd-ts]').forEach((el) => {
      const ts = Number(el.dataset.cdTs || 0);
      const zero = el.dataset.cdZero || '已重置';
      if (ts > 0) el.textContent = fmtDuration(ts - cur, zero);
    });
    document.querySelectorAll('[data-up-ts]').forEach((el) => {
      const ts = Number(el.dataset.upTs || 0);
      const hintCool = el.dataset.upCool === '1';
      if (ts > 0) {
        const diff = Math.max(0, cur - ts);
        el.textContent = elapsedTextWithHint(diff, hintCool);
        el.classList.toggle('up-warn', hintCool && diff >= 270);
      }
    });
  }

  function healthCls(h) {
    if (h === 'locked' || h === 'critical') return 'bad';
    if (h === 'warning') return 'warn';
    return 'ok';
  }

  function barFillColor(h, accent) {
    if (h === 'locked' || h === 'critical') return 'var(--bad)';
    if (h === 'warning') return 'var(--warn)';
    return accent ? `var(--${accent})` : 'var(--ok)';
  }

  function gridCells(segs, limit) {
    const N = 100;
    const c = (segs || []).map((t) => (t && limit ? Math.max(1, Math.floor((t / limit) * N)) : 0));
    const sum = c.reduce((a, b) => a + b, 0);
    if (sum > N) {
      const f = N / sum;
      for (let i = 0; i < c.length; i++) c[i] = Math.floor(c[i] * f);
    }
    const out = [];
    c.forEach((n, i) => { for (let k = 0; k < n; k++) out.push(SEG_COLORS[i]); });
    while (out.length < N) out.push(null);
    return out;
  }

  function splitPath(p) {
    const i = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'));
    return i < 0 ? [p, ''] : [p.slice(i + 1), p.slice(0, i + 1)];
  }

  function fmtCredits(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return '--';
    const n = Number(v);
    return Number.isInteger(n) ? n.toLocaleString('en-US') : n.toFixed(1);
  }

  Object.assign(QC, {
    nowSec,
    esc,
    fmtTok,
    fmtClock,
    fmtHM,
    fmtUsd,
    fmtDateTime,
    fmtMD,
    fmtDuration,
    cdSpan,
    fmtElapsed,
    elapsedTextWithHint,
    elapsedSpan,
    tickCountdowns,
    healthCls,
    barFillColor,
    gridCells,
    splitPath,
    fmtCredits,
  });
})(window.__QC__ = window.__QC__ || {});
