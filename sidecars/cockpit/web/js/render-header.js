import { state, $ } from './state.js';
import { esc, fmtDateTime, cdSpan, tickCountdowns, healthCls } from './formatters.js';
import { poolWindowsList, winResetHtml } from './render-pools.js';

export function updateHeader(d) {
  const dot = $('live-dot');
  const mdl = $('hdr-model');
  const pill = $('hdr-pool');
  const hq = $('hdr-quota');
  const tabDot = $('tab-img-dot');

  if (!d) return;
  dot.className = 'live on';

  const ctx = d.context;
  if (ctx && !ctx.error && ctx.model) {
    mdl.textContent = ctx.model;
    mdl.title = ctx.model_id ? `${ctx.model} (${ctx.model_id})` : ctx.model;
  } else if (ctx && ctx.error === 'no-conversation') {
    mdl.textContent = '未关联具体对话';
    mdl.title = '';
  } else {
    mdl.textContent = '配额与上下文';
    mdl.title = '';
  }

  const ap = d.pools && d.pools.activePool;
  if (ap) {
    const wins = poolWindowsList(ap);
    const pctStr = ap.remainingPct !== null ? `${ap.remainingPct.toFixed(1)}%` : '--';
    pill.textContent = wins.length ? ap.shortLabel : `${ap.shortLabel} · ${pctStr}`;
    pill.className = `pill click ${ap.accent || ''}`;
    pill.style.display = '';

    const hqKey = JSON.stringify([
      ap.key, ap.accent,
      wins.map((w) => [w.window, w.remainingPct, w.resetTs, w.isFull, w.health]),
    ]);
    if (wins.length) {
      hq.style.display = '';
      hq.className = `hquota ${ap.accent || ''}`;
      if (hqKey !== state.lastHdrQuotaKey) {
        state.lastHdrQuotaKey = hqKey;
        hq.innerHTML = wins.map((w, idx) => {
          const valCls = w.health === 'critical' ? 'bad' : w.health === 'warning' ? 'warn' : '';
          const pctTxt = w.remainingPct !== null ? `${w.remainingPct.toFixed(1)}%` : '--';
          const cdHtml = w.isFull
            ? '<span class="hq-cd">满额</span>'
            : (w.resetTs > 0 ? `<span class="hq-cd">· ${cdSpan(w.resetTs)}</span>` : '');
          return `${idx > 0 ? '<span class="hq-sep"></span>' : ''}<span class="hq-item" title="${esc(w.label)}：余 ${pctTxt}${w.resetTs && !w.isFull ? ` · 重置 ${fmtDateTime(w.resetTs)}` : ''}">
            <span class="hq-lbl">${esc(w.shortLabel)}</span>
            <span class="hq-val num ${valCls}">${pctTxt}</span>
            ${cdHtml}
          </span>`;
        }).join('');
        tickCountdowns();
      }
    } else {
      hq.style.display = 'none';
      state.lastHdrQuotaKey = '';
    }
  } else {
    pill.style.display = 'none';
    hq.style.display = 'none';
    state.lastHdrQuotaKey = '';
  }

  const w5 = d.imageQuota && !d.imageQuota.error ? d.imageQuota.window_5h : null;
  if (w5) {
    tabDot.style.display = '';
    const cls = w5.weekly_cooldown_active ? 'bad' : healthCls(w5.health);
    tabDot.style.background = cls === 'bad' ? 'var(--bad)' : cls === 'warn' ? 'var(--warn)' : 'var(--ok)';
  } else {
    tabDot.style.display = 'none';
  }

  const flowDot = $('tab-flow-dot');
  const fq = d.flowQuota && !d.flowQuota.error ? d.flowQuota : null;
  if (flowDot && fq && fq.available) {
    flowDot.style.display = '';
    const fcls = healthCls(fq.health);
    flowDot.style.background = fcls === 'bad' ? 'var(--bad)' : fcls === 'warn' ? 'var(--warn)' : 'var(--ok)';
  } else if (flowDot) {
    flowDot.style.display = 'none';
  }
}
