(function (QC) {
  'use strict';

  const {
    esc, fmtTok, fmtHM, fmtDateTime, fmtMD, fmtDuration, cdSpan, healthCls, barFillColor,
  } = QC;

  function weeklyRecSourceMeta(rec) {
    if (!rec) return { pill: '', text: '' };
    if (rec.source === 'snapshot') return { pill: 'ok', text: '快照实测' };
    if (rec.source === 'hybrid') return { pill: 'pool-a', text: '实测+估算' };
    if (rec.source === 'local_estimate') return { pill: 'warn', text: '7d冷启动估算' };
    if (rec.source === 'full') return { pill: 'ok', text: '满额' };
    return { pill: '', text: '官方参考' };
  }

  function renderPoolWeeklyRecoveryOverview(rec) {
    if (!rec || rec.source === 'full' || Number(rec.usedPct || 0) <= 0.05) return '';
    const sm = weeklyRecSourceMeta(rec);
    const obs = rec.observedRecovery || {};
    let leftHtml = '';
    if (obs.isActive) {
      const ratePart = obs.ratePctPerHour > 0 ? ` +${obs.ratePctPerHour}%/h` : '';
      leftHtml = `<span style="color:var(--ok);font-weight:600">回血中${ratePart}</span>${rec.recoverNext24hPct >= 0.1 ? ` · 24h预计 +${rec.recoverNext24hPct.toFixed(1)}%` : ''}`;
    } else if (Number(rec.recoverNext24hPct || 0) >= 0.1) {
      leftHtml = `24h周回血 <strong>+${Number(rec.recoverNext24hPct).toFixed(1)}%</strong>${Number(rec.recoverNext48hPct || 0) > Number(rec.recoverNext24hPct || 0) + 0.05 ? ` · 48h +${Number(rec.recoverNext48hPct).toFixed(1)}%` : ''}`;
    } else if (rec.nextRecoverTs > 0) {
      leftHtml = `下批周回血 <strong>${fmtMD(rec.nextRecoverTs)}</strong> (+${Number(rec.nextRecoverPct || 0).toFixed(1)}%)`;
    } else {
      leftHtml = '周回血追踪中';
    }
    const rightTxt = obs.recovered24hPct >= 0.1
      ? `24h已回 +${Number(obs.recovered24hPct).toFixed(1)}% · ${sm.text}`
      : sm.text;
    const tip = `${rec.sourceLabel || ''}：${rec.note || ''}`;
    return `<div class="rec-sub num" title="${esc(tip)}">
      <span>${leftHtml}</span>
      <span class="dim">${esc(rightTxt)}</span>
    </div>`;
  }

  function renderPoolWeeklyRecoveryDetail(rec, accent) {
    if (!rec || rec.source === 'full' || Number(rec.usedPct || 0) <= 0.05) return '';
    const sm = weeklyRecSourceMeta(rec);
    const obs = rec.observedRecovery || {};
    const buckets = rec.buckets || [];
    const maxPct = Math.max(0.1, ...buckets.map((b) => Number(b.recoverPct) || 0));
    const fillCls = rec.source === 'snapshot' ? '' : (accent || '');

    const obsBanner = (obs.isActive || Number(obs.recovered24hPct || 0) >= 0.1)
      ? `<div class="callout info" style="margin-top:6px;padding:5px 8px">
          ${obs.isActive ? '<strong style="color:var(--ok)">正在回血</strong> · ' : ''}近 24h 实测已回血 <strong class="num">+${Number(obs.recovered24hPct || 0).toFixed(1)}%</strong>${obs.ratePctPerHour > 0 ? ` · 速率 <strong class="num">+${obs.ratePctPerHour}%/h</strong>` : ''}${obs.etaFullSec > 0 ? ` · 按速率回满约 <span class="num">${fmtDuration(obs.etaFullSec)}</span>` : ''}
        </div>`
      : '';

    const rowsHtml = buckets.map((b) => {
      const pctW = Math.max(6, Math.round((Number(b.recoverPct || 0) / maxPct) * 100));
      const srcTxt = b.source === 'snapshot' ? '官方快照实测' : (b.source === 'local_estimate' ? '本机7d消耗估算' : (b.source === 'hybrid' ? '实测+估算混合' : '官方参考时间'));
      const tip = `${b.dateKey} (${fmtHM(b.firstFreeTs)}–${fmtHM(b.lastFreeTs)})：预计释放 +${Number(b.recoverPct || 0).toFixed(2)}%，累计恢复 +${Number(b.cumRecoverPct || 0).toFixed(1)}%（预计余 ${Number(b.projectedRemainingPct || 0).toFixed(1)}%）· ${srcTxt}${b.turns > 0 ? ` · 对应 ${b.turns} 轮` : ''}`;
      return `<div class="rec-row num" title="${esc(tip)}">
        <span class="roll-time">${esc(b.label)}</span>
        <div class="roll-bar"><div class="roll-fill ${esc(fillCls)}" style="width:${pctW}%"></div></div>
        <span class="roll-val"><strong>+${Number(b.recoverPct || 0).toFixed(1)}%</strong> <span class="dim">→ ${Number(b.projectedRemainingPct || 0).toFixed(0)}%</span></span>
      </div>`;
    }).join('');

    return `<div class="rec-panel">
      <div class="meta-row" style="margin-bottom:5px">
        <span style="color:var(--fg);font-weight:600;display:inline-flex;align-items:center;gap:5px">
          <span>7 天滚动回血预测</span>
          <span class="pill ${sm.pill}" style="font-size:10px;padding:1px 5px">${esc(sm.text)}</span>
        </span>
        <span class="num dim">${obs.sampleCount > 1 ? `已录 ${obs.historySpanHours}h 快照` : '后台快照已启动'}</span>
      </div>
      ${obsBanner}
      <div class="roll-head" style="margin-top:6px;margin-bottom:5px">
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">下批周回血</div>
          <div class="roll-kpi-val num" style="color:var(--ok)">${rec.nextRecoverTs > 0 ? cdSpan(rec.nextRecoverTs, '即将释放') : '--'}</div>
          <div class="roll-kpi-sub num">${rec.nextRecoverTs > 0 ? `${fmtDateTime(rec.nextRecoverTs)} · 当日 +${Number(rec.nextRecoverPct || 0).toFixed(1)}%` : '等待数据'}</div>
        </div>
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">未来 24h / 48h 预计</div>
          <div class="roll-kpi-val num">+${Number(rec.recoverNext24hPct || 0).toFixed(1)}%</div>
          <div class="roll-kpi-sub num">48h +${Number(rec.recoverNext48hPct || 0).toFixed(1)}% · 72h +${Number(rec.recoverNext72hPct || 0).toFixed(1)}%</div>
        </div>
      </div>
      ${buckets.length ? `<div class="roll-list">${rowsHtml}</div>` : ''}
      ${rec.note ? `<div class="sub" style="margin-top:6px;line-height:1.4">${esc(rec.note)}</div>` : ''}
    </div>`;
  }

  function poolWindowsList(p) {
    if (Array.isArray(p?.windows) && p.windows.length) return p.windows;
    const out = [];
    if (p?.fiveHour) out.push(p.fiveHour);
    if (p?.weekly) out.push(p.weekly);
    if (!out.length && p && p.remainingPct !== null && p.remainingPct !== undefined) {
      out.push({
        window: 'quota',
        label: '当前额度',
        shortLabel: '余',
        remainingPct: p.remainingPct,
        usedPct: p.usedPct,
        resetTs: p.resetTs || 0,
        isFull: p.remainingPct >= 99.99,
        health: p.health || 'ok',
      });
    }
    return out;
  }

  function winResetHtml(w, compact = false) {
    if (!w) return '';
    if (w.isFull) {
      const tip = w.resetTs ? ` title="满额未消耗（参考时间 ${esc(fmtDateTime(w.resetTs))}）"` : '';
      return `<span class="dim"${tip}>${compact ? '满额' : '满额（尚无消耗）'}</span>`;
    }
    if (w.resetTs > 0) {
      if (compact) return cdSpan(w.resetTs);
      const lbl = w.window === 'weekly' ? '参考锚点' : '重置';
      return `${lbl} ${fmtDateTime(w.resetTs)} (${cdSpan(w.resetTs)})`;
    }
    return `<span class="dim">${compact ? '未计窗' : '尚无重置计时'}</span>`;
  }

  function renderPools(d) {
    const poolsObj = d.pools || {};
    const pools = poolsObj.pools || [];
    const parts = [];

    if (poolsObj.routingAdvice) {
      const cls = poolsObj.routingAdvice.level === 'critical' ? 'bad' : 'warn';
      parts.push(`<div class="callout ${cls}">${esc(poolsObj.routingAdvice.text)}</div>`);
    }

    if (!pools.length) {
      parts.push(`<section class="card"><div class="ch"><span class="ctitle">模型配额池</span></div>
        <div class="sub">${esc(poolsObj.error || '暂未从 Language Server 获取到模型配额数据，请点击右上角刷新重试。')}</div></section>`);
    } else {
      for (const p of pools) {
        const isActive = p.key === poolsObj.activePoolKey;
        const activeCls = isActive ? `active-${p.accent}` : '';
        const wins = poolWindowsList(p);
        const pillsHtml = wins.length
          ? wins.map((w) => `<span class="pill ${healthCls(w.health)} num">${esc(w.shortLabel)} ${w.remainingPct !== null ? `${w.remainingPct.toFixed(1)}%` : '--'}</span>`).join('')
          : `<span class="pill ${healthCls(p.health)} num">${p.remainingPct !== null ? `余 ${p.remainingPct.toFixed(1)}%` : '--'}</span>`;
        const winBoxesHtml = wins.map((w) => {
          const wpct = w.remainingPct !== null ? w.remainingPct : 0;
          const pctCls = w.health === 'critical' ? 'bad' : w.health === 'warning' ? 'warn' : '';
          const isBottleneck = wins.length > 1 && p.bottleneckWindow === w.window && w.remainingPct !== null && w.remainingPct < 50;
          return `<div class="win-box">
            <div class="win-head">
              <span class="win-name">${esc(w.label)}${isBottleneck ? ' <span class="pill warn" style="font-size:10px;padding:1px 5px">当前瓶颈</span>' : ''}</span>
              <span class="num">
                <span class="win-pct ${pctCls}">余 ${w.remainingPct !== null ? `${w.remainingPct.toFixed(1)}%` : '--'}</span>
                ${w.usedPct !== null ? `<span class="dim" style="margin-left:5px;font-size:11px">已用 ${w.usedPct.toFixed(1)}%</span>` : ''}
              </span>
            </div>
            <div class="bar-wrap" style="margin:5px 0">
              <div class="bar-fill" style="width:${Math.max(0, Math.min(100, wpct))}%;background:${barFillColor(w.health, p.accent)}"></div>
            </div>
            <div class="meta-row">
              <span>${w.window === '5h' ? '5 小时平滑峰值窗口' : w.window === 'weekly' ? '7 天滚动回血池' : '周期额度'}</span>
              <span>${winResetHtml(w, false)}</span>
            </div>
            ${w.window === 'weekly' ? renderPoolWeeklyRecoveryDetail(p.weeklyRecovery, p.accent) : ''}
          </div>`;
        }).join('');
        parts.push(`<section class="card ${activeCls}">
          <div class="ch">
            <span class="ctitle">${esc(p.label)} ${isActive ? `<span class="pill ${p.accent}">当前对话所在池</span>` : ''}</span>
            <span class="pill-row">${pillsHtml}</span>
          </div>
          <div class="sub">${esc(p.description || '')}${p.familyCount ? ` · ${p.familyCount} 模型族 (${p.modelCount} 档位)` : ''}</div>
          <div class="win-list" style="margin:8px 0 6px">${winBoxesHtml || '<div class="sub">暂未获取到窗口额度明细</div>'}</div>
          ${!p.hasDualWindows && poolsObj.quotaSummaryAvailable === false ? '<div class="sub" style="margin-bottom:4px">注：RetrieveUserQuotaSummary 暂不可用，当前仅展示单窗口回退数据。</div>' : ''}
          <div class="sec-sep" style="margin-top:6px;padding-top:6px">
            ${(() => {
              const rowHtml = (m) => `<div class="model-row">
                <div class="model-left">
                  <div class="model-name">
                    ${m.tier ? `<span class="tier ${m.tier === 'IMG' ? 'IMG' : ''}">${esc(m.tier)}</span>` : ''}
                    <span>${esc(m.family)}</span>
                    ${m.variantsCount > 1 ? `<span class="dim" style="font-size:11px">×${m.variantsCount}</span>` : ''}
                  </div>
                  ${m.role ? `<div class="ev-sub">${esc(m.role)}</div>` : ''}
                </div>
                <div class="num dim" style="font-size:11.5px;text-align:right;white-space:nowrap">
                  ${p.hasMixedQuotas && m.remainingPct !== null ? `<div style="color:var(--fg);font-weight:600">${m.remainingPct.toFixed(1)}%</div>` : ''}
                  ${m.maxTokens ? `<div>${fmtTok(m.maxTokens)}</div>` : ''}
                </div>
              </div>`;
              const all = p.models || [];
              const isPrimary = (m) => Boolean(m.tier) && !/(-sonar|-tiered|orchestrator|worker-agy)/i.test(m.id || '') && !/^orchestrator\b/i.test(m.family || '');
              const primary = all.filter(isPrimary);
              const extra = all.filter((m) => !isPrimary(m));
              if (primary.length && extra.length) {
                return primary.map(rowHtml).join('') +
                  `<details style="margin-top:4px"><summary class="sub" style="cursor:pointer;padding:4px 0;user-select:none">展开其余 ${extra.length} 个内部/兼容模型</summary>${extra.map(rowHtml).join('')}</details>`;
              }
              return all.map(rowHtml).join('');
            })()}
          </div>
        </section>`);
      }
    }

    if (poolsObj.imageModelPoolNote) {
      parts.push(`<div class="callout info">${esc(poolsObj.imageModelPoolNote)}</div>`);
    }
    return parts.join('');
  }

  Object.assign(QC, {
    weeklyRecSourceMeta,
    renderPoolWeeklyRecoveryOverview,
    renderPoolWeeklyRecoveryDetail,
    poolWindowsList,
    winResetHtml,
    renderPools,
  });
})(window.__QC__ = window.__QC__ || {});
