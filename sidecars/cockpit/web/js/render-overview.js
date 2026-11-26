(function (QC) {
  'use strict';

  const {
    CAT_COLOR, CAT_LABEL,
    esc, fmtTok, fmtClock, fmtHM, fmtUsd, fmtDateTime, fmtMD, cdSpan, barFillColor, gridCells,
    imgStatusMeta, poolWindowsList, winResetHtml, renderPoolWeeklyRecoveryOverview,
    renderCacheOverviewStrip, renderFlowOverviewCard,
  } = QC;

  function renderOverview(d) {
    const parts = [];
    const iq = d.imageQuota;
    const w5 = iq && !iq.error ? iq.window_5h : null;

    // 1. Image Quota Summary Card
    if (!w5) {
      parts.push(`<section class="card">
        <div class="ch"><span class="ctitle">生图配额 (5h 滚轴)</span><span class="pill bad">读取失败</span></div>
        <div class="sub">${esc(iq?.message || '无法读取本地生图记录')}</div>
      </section>`);
    } else {
      const anc = iq.anchor || {};
      const avg = iq.per_image_avg || {};
      const wrAvg = avg.with_refs || {};
      const rs = iq.rolling_schedule || {};
      const wrs = iq.weekly_rolling_schedule || {};
      const w7 = iq.window_7d || {};
      const meta = imgStatusMeta(w5, anc);
      const refCalls = anc.ref_calls || w5.hard_cap || 60;
      const refTokens = anc.ref_tokens || 0;
      const softCalls = anc.soft_calls || w5.soft_cap || 54;
      const tickPct = refCalls > 0 ? Math.min(100, (softCalls / refCalls) * 100) : 85;
      const ledgers = iq.workspace_ledgers || [];
      const tokActive = Number(w5.tokens_total || 0);
      const wrTok = Number(anc.avg_with_refs_tokens || wrAvg.avg_tokens_total || avg.avg_tokens_total || 3880);
      const wrRem = Number(anc.est_remaining_imgs_with_refs ?? 0);
      const wrRemExact = Number(anc.est_remaining_imgs_with_refs_exact ?? wrRem);
      const byRefRem = anc.est_remaining_imgs_by_refs || {};
      const callsPct = Number(anc.calls_pct ?? w5.calls_pct ?? 0);
      const tokensPct = Number(anc.tokens_pct ?? w5.tokens_pct ?? 0);
      const pctBasis = anc.used_pct_basis || w5.used_pct_basis || 'calls';
      const remCalls = Number(anc.remaining_calls ?? w5.remaining_est ?? 0);
      const m30 = rs.recover_next_30m || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };

      let mainVal = '';
      if (w5.weekly_cooldown_active) {
        mainVal = `<div class="bigrow">
          <div class="big bad num">周冷却 ${cdSpan(w5.weekly_reset_ts || wrs.active_reset_ts, '已解封')}</div>
          <span class="sub num">解封 ${fmtDateTime(w5.weekly_reset_ts || wrs.active_reset_ts)}</span>
        </div>`;
      } else if (w5.cooldown_active) {
        mainVal = `<div class="bigrow">
          <div class="big bad num">冷却 ${cdSpan(w5.next_recover_ts || w5.window_end, '已解封')}</div>
          <span class="sub num">解封 ${fmtClock(w5.next_recover_ts || w5.window_end)}</span>
        </div>`;
      } else {
        let rightHint = `带垫图预估余 ~${wrRem} 张`;
        if (anc.is_hard_ceiling === false && anc.exceeded_safe_peak && wrRem === 0) {
          rightHint = remCalls > 0 ? `已破安全峰 · 仍余 ${remCalls}次` : '已破安全峰 · 未触429';
        } else if (pctBasis === 'calls' && (callsPct - tokensPct) >= 8 && remCalls < wrRem) {
          rightHint = `次余 ${remCalls} · Tok余 ~${wrRem}张`;
        }
        const rightTip = `${anc.label || ''}：预估上限 ${refCalls} 次 (${callsPct.toFixed(1)}%) / ${fmtTok(refTokens)} tok (${tokensPct.toFixed(1)}%)；剩余 ${fmtTok(anc.remaining_tokens || 0)} tok（按带垫图均 ${fmtTok(wrTok)} 约 ${wrRemExact} 张，次数余 ${remCalls} 次）`;
        mainVal = `<div class="bigrow">
          <div class="big ${meta.big} num">${w5.metric_used} <span class="dim" style="font-size:12.5px;font-weight:500">次 · ${fmtTok(tokActive)} tok</span></div>
          <span class="sub num" style="color:var(--fg);font-weight:600" title="${esc(rightTip)}">${esc(rightHint)}</span>
        </div>`;
      }

      const fbWrTxt = rs.first_batch?.est_imgs_with_refs > 0 ? ` / ~${rs.first_batch.est_imgs_with_refs}张` : '';
      const nextRecoverHtml = w5.cooldown_active
        ? `预计解封 ${fmtHM(w5.next_recover_ts || w5.window_end)}`
        : (w5.next_recover_ts > 0
          ? `下批回血 ${cdSpan(w5.next_recover_ts, '即将释放')}${rs.first_batch?.calls ? ` (+${rs.first_batch.calls}次${fbWrTxt})` : ''}`
          : '当前 5h 滚轴已清空');
      const fullClearHtml = w5.full_clear_ts > 0
        ? `<span title="预估上限 ${refCalls}次 / ${fmtTok(refTokens)} tok；最后一条在窗调用将于 ${esc(fmtDateTime(w5.full_clear_ts))} 滚出">上限 ${refCalls}次/${fmtTok(refTokens)} · ${fmtHM(w5.full_clear_ts)}清空</span>`
        : esc(anc.label || meta.mode);
      const pctChipHtml = !w5.cooldown_active && w5.metric_used > 0
        ? `<span class="chip num" title="进度条取次数(${callsPct.toFixed(1)}%)与Token(${tokensPct.toFixed(1)}%)中的较高值：${w5.used_pct}%">占比 ${pctBasis === 'tokens' ? `<strong>Tok${Math.round(tokensPct)}%</strong>/次${Math.round(callsPct)}%` : `<strong>次${Math.round(callsPct)}%</strong>/Tok${Math.round(tokensPct)}%`}</span>`
        : '';

      const pinPill = anc.source === 'pinned_peak'
        ? ` <span class="pill pool-a num" title="已固定 5h 峰值上限，不受旧对话清理或时间滚出影响">固定 ${refCalls}次</span>`
        : '';
      parts.push(`<section class="card">
        <div class="ch">
          <span class="ctitle">生图配额 · 5h 滚轴 <span class="pill ${meta.pill}">${meta.text}</span>${pinPill}</span>
          <button class="link-btn" data-jump="image">详情 →</button>
        </div>
        ${mainVal}
        <div class="bar-wrap" title="${esc(anc.label || '')}：进度条 ${w5.used_pct}%（${pctBasis === 'tokens' ? 'Token主导' : pctBasis === 'locked' ? '冷却锁定' : '次数主导'}）· 次数 ${w5.metric_used}/${refCalls} (${callsPct.toFixed(1)}%) · Token ${fmtTok(tokActive)}/${fmtTok(refTokens)} (${tokensPct.toFixed(1)}%)">
          <div class="bar-fill" style="width:${Math.min(100, w5.used_pct || 0)}%;background:${barFillColor(w5.health)}"></div>
          ${anc.is_hard_ceiling !== false ? `<div class="bar-tick" style="left:${tickPct.toFixed(1)}%" title="预警线 ${softCalls} 次"></div>` : ''}
        </div>
        <div class="meta-row">
          <span class="num">${nextRecoverHtml}</span>
          <span class="num">${fullClearHtml}</span>
        </div>
        <div class="chips">
          ${pctChipHtml}
          <span class="chip num" title="近 30 天 ${wrAvg.sample_count || 0} 次带垫图（≥1参，均 ${wrAvg.avg_refs ?? 1} 张参考图）实测均值">带垫图均 <strong>~${fmtTok(wrTok)}</strong></span>
          ${!w5.cooldown_active && byRefRem['2'] !== undefined ? `<span class="chip num" title="按 2 张参考图实测均图 Token 换算">2参余 <strong>~${byRefRem['2']}张</strong></span>` : ''}
          ${m30.calls > 0 ? `<span class="chip num" title="未来 30 分钟滚轴将释放 ${m30.calls} 次 / ${fmtTok(m30.tokens_total)} tok">30分回血 <strong>+${m30.est_imgs_with_refs || m30.calls}张</strong></span>` : ''}
          <span class="chip num">窗内 <strong>${w5.ok}成/${w5.eof}EOF</strong>${w5.est_usd > 0 ? ` · ${fmtUsd(w5.est_usd)}` : ''}</span>
          ${(w7.attempts || 0) > 0 ? `<span class="chip num" title="近 7 天累计生图 ${w7.attempts} 次（${fmtTok(w7.tokens_total)} tok）；最早一批将于 ${esc(fmtDateTime(wrs.next_recover_ts))} 滚出回血">7天 <strong>${w7.attempts}次</strong>${wrs.next_recover_ts > 0 ? ` · ${fmtMD(wrs.next_recover_ts)}起回血` : ''}</span>` : ''}
          ${ledgers.length ? `<span class="chip">账本 <strong>${ledgers.length}集</strong></span>` : ''}
        </div>
        ${w5.weekly_cooldown_active ? `<div class="callout bad" style="margin-top:6px">命中官方周池 429 冷却，预计解封 ${fmtDateTime(w5.weekly_reset_ts)}（${cdSpan(w5.weekly_reset_ts, '已解封')}）。</div>` : ''}
        ${w5.eof_warning ? `<div class="callout warn" style="margin-top:6px">近 5h EOF 率 ${w5.eof_rate_pct}%（${w5.eof} 次），断连重试可能放大隐性配额消耗。</div>` : ''}
      </section>`);
    }

    // 2. Model Quota Pools Summary Card
    const poolsObj = d.pools || {};
    const pools = poolsObj.pools || [];
    let poolsBody = '';
    if (poolsObj.routingAdvice) {
      const advCls = poolsObj.routingAdvice.level === 'critical' ? 'bad' : 'warn';
      poolsBody += `<div class="callout ${advCls}" style="margin-bottom:7px">${esc(poolsObj.routingAdvice.text)}</div>`;
    }
    if (!pools.length) {
      poolsBody += `<div class="sub">${esc(poolsObj.error || '暂未获取到模型池数据，请点击右上角刷新。')}</div>`;
    } else {
      poolsBody += pools.map((p) => {
        const isActive = p.key === poolsObj.activePoolKey;
        const activeCls = isActive ? `active-${p.accent}` : '';
        const wins = poolWindowsList(p);
        const winsHtml = wins.map((w) => {
          const wpct = w.remainingPct !== null ? w.remainingPct : 0;
          const pctCls = w.health === 'critical' ? 'bad' : w.health === 'warning' ? 'warn' : '';
          const resetTip = w.isFull
            ? '满额（尚无消耗）'
            : (w.resetTs ? `重置时间 ${fmtDateTime(w.resetTs)}` : '尚无重置计时');
          return `<div class="win-mini" title="${esc(w.label)}：余 ${w.remainingPct !== null ? `${w.remainingPct.toFixed(1)}%` : '--'} · ${esc(resetTip)}">
            <div class="meta-row">
              <span style="color:var(--fg);font-weight:500">${esc(w.label)}</span>
              <span class="num">
                <strong class="win-pct ${pctCls}">${w.remainingPct !== null ? `${w.remainingPct.toFixed(1)}%` : '--'}</strong>
                <span class="dim" style="margin-left:5px">· ${winResetHtml(w, true)}</span>
              </span>
            </div>
            <div class="bar-wrap sm">
              <div class="bar-fill" style="width:${Math.max(0, Math.min(100, wpct))}%;background:${barFillColor(w.health, p.accent)}"></div>
            </div>
            ${w.window === 'weekly' ? renderPoolWeeklyRecoveryOverview(p.weeklyRecovery) : ''}
          </div>`;
        }).join('');
        return `<div class="pool-mini ${activeCls}">
          <div class="meta-row" style="margin-bottom:2px">
            <span style="color:var(--fg);font-weight:600">${esc(p.label)} ${isActive ? `<span class="pill ${p.accent}" style="margin-left:3px">当前对话</span>` : ''}</span>
            <span class="dim">${p.familyCount ? `${p.familyCount} 模型族` : ''}</span>
          </div>
          <div class="win-list">${winsHtml || '<div class="sub">暂无窗口配额数据</div>'}</div>
        </div>`;
      }).join('');
    }
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">当前模型配额池 ${poolsObj.stale ? '<span class="pill warn">缓存</span>' : ''}</span>
        <button class="link-btn" data-jump="pools">详情 →</button>
      </div>
      ${poolsBody}
    </section>`);

    // 3. Current Context Summary Card
    const ctx = d.context;
    if (!ctx || ctx.error) {
      const msg = ctx?.error === 'no-conversation'
        ? '在具体对话窗口中打开本侧边栏即可实时监控该对话上下文。'
        : ctx?.error === 'no-database'
          ? '新对话尚无落盘记录，发送首条消息后自动显示。'
          : (ctx?.message || '暂无法读取当前上下文。');
      parts.push(`<section class="card">
        <div class="ch"><span class="ctitle">当前上下文</span><button class="link-btn" data-jump="context">详情 →</button></div>
        <div class="sub">${esc(msg)}</div>
      </section>`);
    } else {
      const budget = Number(ctx.request_budget_tokens) || 0;
      const nearBudget = budget > 0 && ctx.used_tokens >= budget * 0.8;
      const cells = gridCells(ctx.grid_segments, ctx.limit_tokens);
      const topCats = [...(ctx.categories || [])]
        .filter((c) => c.tokens > 0)
        .sort((a, b) => b.tokens - a.tokens)
        .slice(0, 4);
      parts.push(`<section class="card">
        <div class="ch">
          <span class="ctitle">当前上下文 ${ctx.compacted ? '<span class="pill warn">已压缩</span>' : ''}</span>
          <button class="link-btn" data-jump="context">完整明细 →</button>
        </div>
        <div class="bigrow">
          <div class="big num" style="font-size:17px">${fmtTok(ctx.used_tokens)} <span class="dim" style="font-size:12.5px;font-weight:500">/ ${ctx.limit_tokens ? fmtTok(ctx.limit_tokens) : '?'}</span></div>
          <span class="pill ${nearBudget ? 'warn' : ''}">${ctx.limit_tokens ? `${ctx.used_pct.toFixed(1)}%` : '--'}${nearBudget ? ` · 接近单次预算 ${fmtTok(budget)}` : ''}</span>
        </div>
        <div class="grid">${cells.map((col) => `<span class="dot"${col ? ` style="background:${col}"` : ''}></span>`).join('')}</div>
        ${renderCacheOverviewStrip(ctx.cache)}
        ${topCats.length ? `<div class="sum-grid">${topCats.map((c) => `
          <div class="sum-item">
            <span class="sum-left"><span class="mark" style="background:${CAT_COLOR[c.key] || 'var(--dim)'}"></span><span>${esc(CAT_LABEL[c.key] || c.name)}</span></span>
            <span class="num dim">${fmtTok(c.tokens)}</span>
          </div>`).join('')}</div>` : ''}
      </section>`);
    }

    // 4. Google Flow Video Quota Summary Card
    parts.push(renderFlowOverviewCard(d.flowQuota));

    return parts.join('');
  }

  Object.assign(QC, {
    renderOverview,
  });
})(window.__QC__ = window.__QC__ || {});
