(function (QC) {
  'use strict';

  const {
    state, esc, fmtTok, fmtClock, fmtHM, fmtUsd, fmtDateTime, cdSpan, barFillColor,
  } = QC;

  function imgStatusMeta(w5, anchor) {
    if (!w5) return { pill: '', text: '未知', big: '', mode: '' };
    if (w5.weekly_cooldown_active) {
      return { pill: 'bad', text: '周池 429 冷却', big: 'bad', mode: '官方周池 429 锁定' };
    }
    if (w5.cooldown_active || w5.health === 'locked') {
      return { pill: 'bad', text: '429 冷却中', big: 'bad', mode: '官方 429 锁定窗口' };
    }
    if (anchor?.exceeded_safe_peak || w5.exceeded_safe_peak) {
      return { pill: 'warn', text: '刷新安全峰', big: 'warn', mode: '连续 5h 时间滚轴' };
    }
    if (w5.health === 'critical') {
      return { pill: 'bad', text: '已达上限', big: 'bad', mode: '连续 5h 时间滚轴' };
    }
    if (anchor?.eof_warning || w5.eof_warning) {
      return { pill: 'warn', text: 'EOF 偏高', big: 'warn', mode: '连续 5h 时间滚轴' };
    }
    if (w5.health === 'warning') {
      const txt = anchor?.is_hard_ceiling === false ? '接近安全峰' : '接近预警线';
      return { pill: 'warn', text: txt, big: 'warn', mode: '连续 5h 时间滚轴' };
    }
    if (w5.mode === 'idle_full') {
      return { pill: 'ok', text: '空窗满血', big: 'ok', mode: '5h 滚轴已清空' };
    }
    return { pill: 'ok', text: '滚轴活跃', big: 'ok', mode: '连续 5h 时间滚轴' };
  }

  function eventTagInfo(kind) {
    if (kind === '429_WEEKLY') return { cls: 'E429_WEEKLY', text: '周429', tip: '7 天周配额耗尽（跨日冷却）' };
    if (kind === '429') return { cls: 'E429', text: '429', tip: '5h 配额耗尽 (QUOTA_EXHAUSTED)' };
    if (kind === '429_RPM') return { cls: 'E429_RPM', text: 'RPM', tip: '突发每分钟速率限流 (已与 5h 池剥离)' };
    if (kind === 'CLIENT_ERR') return { cls: 'CLIENT_ERR', text: '参数错', tip: '本地参数校验失败 (未发往服务端、不扣配额)' };
    if (kind === 'RUNNING') return { cls: 'RUNNING', text: '生成中', tip: '进行中' };
    return { cls: kind || 'ERR', text: kind || 'ERR', tip: kind || '' };
  }

  function renderImage(d) {
    const iq = d.imageQuota;
    if (!iq || iq.error) {
      return `<section class="card"><div class="ch"><span class="ctitle">生图配额详情</span></div>
        <div class="callout bad">${esc(iq?.message || '读取本地对话记录失败')}</div></section>`;
    }
    const w5 = iq.window_5h;
    const w24 = iq.window_24h || {};
    const w7 = iq.window_7d || {};
    const cal = iq.calibration || {};
    const rs = iq.rolling_schedule || {};
    const avg = iq.per_image_avg || {};
    const wrAvg = avg.with_refs || {};
    const anc = iq.anchor || {};
    const peak = cal.observed_safe_peak || {};
    const meta = imgStatusMeta(w5, anc);
    const refCalls = anc.ref_calls || w5.hard_cap || 60;
    const refTokens = anc.ref_tokens || 0;
    const softCalls = anc.soft_calls || w5.soft_cap || 54;
    const tickPct = refCalls > 0 ? Math.min(100, (softCalls / refCalls) * 100) : 85;
    const wrTok = Number(anc.avg_with_refs_tokens || wrAvg.avg_tokens_total || avg.avg_tokens_total || 3880);
    const wrRem = Number(anc.est_remaining_imgs_with_refs ?? 0);
    const wrRemExact = Number(anc.est_remaining_imgs_with_refs_exact ?? wrRem);
    const byRefRem = anc.est_remaining_imgs_by_refs || {};
    const callsPct = Number(anc.calls_pct ?? w5.calls_pct ?? 0);
    const tokensPct = Number(anc.tokens_pct ?? w5.tokens_pct ?? 0);
    const remCalls = Number(anc.remaining_calls ?? w5.remaining_est ?? 0);
    const tokBarPct = w5.cooldown_active ? 100 : Math.min(100, tokensPct);
    const callsBarPct = w5.cooldown_active ? 100 : Math.min(100, callsPct);
    const tokBarHealth = w5.cooldown_active
      ? 'locked'
      : (anc.is_hard_ceiling !== false && tokensPct >= 100 ? 'critical' : (tokensPct >= 88 ? 'warning' : 'ok'));
    const callsBarHealth = w5.cooldown_active
      ? 'locked'
      : (anc.is_hard_ceiling !== false && callsPct >= 100 ? 'critical' : (callsPct >= 88 ? 'warning' : 'ok'));
    const parts = [];

    // 1. 5h rolling occupancy & Token breakdown card
    const anchorRight = (anc.is_hard_ceiling === false && anc.exceeded_safe_peak && wrRem === 0)
      ? (remCalls > 0 ? `破峰中 · 次数余 ${remCalls}` : '破峰中 · 未触429')
      : `带垫图余 ~${wrRem} 张 (Tok ${tokensPct.toFixed(0)}%)`;
    const byRefChips = ['1', '2', '3+', '0']
      .filter((k) => byRefRem[k] !== undefined)
      .map((k) => `<span class="chip num">${k === '0' ? '无参' : `${k}参`} <strong>~${byRefRem[k]}张</strong></span>`)
      .join('');
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">5h 连续滚轴占用 <span class="pill ${meta.pill}">${meta.text}</span></span>
        <span class="sub mono">${esc(iq.model || 'gemini-3.1-flash-image')}</span>
      </div>
      ${w5.cooldown_active
        ? `<div class="bigrow"><div class="big bad num">剩余冷却 ${cdSpan(w5.next_recover_ts || w5.window_end, '已解封')}</div><span class="sub num">解封 ${fmtDateTime(w5.next_recover_ts || w5.window_end)}</span></div>`
        : `<div class="bigrow"><div class="big ${meta.big} num">${w5.metric_used} 次 · ${fmtTok(w5.tokens_total)} tok</div><span class="sub num" style="color:var(--fg);font-weight:600" title="预估上限 ${refCalls} 次 (${callsPct.toFixed(1)}%) / ${fmtTok(refTokens)} tok (${tokensPct.toFixed(1)}%)；剩余 ${fmtTok(anc.remaining_tokens || 0)} tok">${esc(anchorRight)}</span></div>`}
      <div class="win-list" style="margin-top:6px">
        <div class="win-mini" title="Token 占用：${fmtTok(w5.tokens_total)} / ${fmtTok(refTokens)} tok (${tokensPct.toFixed(1)}%)">
          <div class="meta-row" style="margin-top:0">
            <span style="color:var(--fg);font-weight:500">Token 占用</span>
            <span class="num"><strong>${fmtTok(w5.tokens_total)}</strong> / ${fmtTok(refTokens)} <span class="dim">(${tokensPct.toFixed(1)}%)</span></span>
          </div>
          <div class="bar-wrap sm">
            <div class="bar-fill" style="width:${tokBarPct}%;background:${barFillColor(tokBarHealth)}"></div>
          </div>
        </div>
        <div class="win-mini" title="次数占用：${w5.metric_used} / ${refCalls} 次 (${callsPct.toFixed(1)}%)">
          <div class="meta-row" style="margin-top:0">
            <span style="color:var(--fg);font-weight:500">次数占用</span>
            <span class="num"><strong>${w5.metric_used}</strong> / ${refCalls} 次 <span class="dim">(${callsPct.toFixed(1)}%)</span></span>
          </div>
          <div class="bar-wrap sm">
            <div class="bar-fill" style="width:${callsBarPct}%;background:${barFillColor(callsBarHealth)}"></div>
            ${anc.is_hard_ceiling !== false ? `<div class="bar-tick" style="left:${tickPct.toFixed(1)}%" title="预警线 ${softCalls} 次"></div>` : ''}
          </div>
        </div>
      </div>
      <div class="meta-row">
        <span>预估上限：<strong>${refCalls}次 / ${fmtTok(refTokens)} tok</strong></span>
        <span class="num">${w5.oldest_ts > 0 ? `在窗 ${fmtHM(w5.oldest_ts)} → ${fmtHM(w5.newest_ts)}` : '当前 5h 无在窗调用'}</span>
      </div>
      ${!w5.cooldown_active ? `<div class="callout" style="margin-top:7px;background:color-mix(in srgb,var(--accent) 8%,transparent);border-color:color-mix(in srgb,var(--accent) 28%,transparent)">
        <div class="meta-row" style="margin-top:0">
          <span style="color:var(--fg);font-weight:600">按带垫图均图（${fmtTok(wrTok)} tok）预估余 <strong class="num" style="color:var(--accent);font-size:13px">~${wrRem} 张</strong> <span class="dim num">(${wrRemExact}张)</span></span>
          <span class="num dim">余 ${fmtTok(anc.remaining_tokens || 0)} tok</span>
        </div>
        ${byRefChips ? `<div class="chips" style="margin-top:5px">${byRefChips}</div>` : ''}
      </div>` : ''}
      <div class="tok-breakdown">
        <span class="chip num">Prompt <strong>${fmtTok(w5.prompt_tokens)}</strong></span>
        <span class="chip num">参考图 <strong>${fmtTok(w5.input_image_tokens)}</strong> (${w5.ref_images || 0}张)</span>
        <span class="chip num">1K出图 <strong>${fmtTok(w5.output_image_tokens)}</strong></span>
        <span class="chip num" title="不含最小内部思考的确定性 Token 为 ${fmtTok(w5.tokens_raw)}">思考估 <strong>~${fmtTok(w5.thinking_tokens)}</strong></span>
        <span class="chip num">API等效 <strong>${fmtUsd(w5.est_usd)}</strong></span>
      </div>
      ${(anc.notes || []).length ? `<div class="sub" style="margin-top:6px;line-height:1.45">${anc.notes.map((n) => esc(n)).join('；')}。</div>` : ''}
      ${w5.last_429_msg ? `<div class="callout ${w5.cooldown_active ? 'bad' : 'warn'}" style="margin-top:7px">
        <div style="font-weight:600;margin-bottom:2px">最近官方 5h 429 记录（${fmtDateTime(w5.last_429_at)}）</div>
        <div class="mono" style="font-size:11px;word-break:break-all">${esc(w5.last_429_msg)}</div>
      </div>` : ''}
    </section>`);

    // 2. Rolling recovery schedule (30-min clock-aligned buckets)
    const buckets = rs.buckets || [];
    const maxBucketCalls = Math.max(1, ...buckets.map((b) => Number(b.calls) || 0));
    const fb = rs.first_batch;
    const m30 = rs.recover_next_30m || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    const m1h = rs.recover_next_1h || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    const m2h = rs.recover_next_2h || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">5h 时间滚轴 · 分段回血时间表</span>
        <span class="sub num">${buckets.length ? `共 ${rs.total_calls} 次待滚出` : '无排队释放'}</span>
      </div>
      ${buckets.length ? `
        <div class="roll-head">
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">${w5.cooldown_active ? '预计解封 / 下批回血' : '下批回血倒计时'}</div>
            <div class="roll-kpi-val num" style="color:${w5.cooldown_active ? 'var(--bad)' : 'var(--ok)'}">${cdSpan(w5.next_recover_ts, '即将释放')}</div>
            <div class="roll-kpi-sub num">${fmtClock(w5.next_recover_ts)}${fb ? ` · 先释 +${fb.calls}次 (${fmtTok(fb.tokens_total)}${fb.est_imgs_with_refs > 0 ? ` ≈+${fb.est_imgs_with_refs}张` : ''})` : ''}</div>
          </div>
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">完全清空倒计时</div>
            <div class="roll-kpi-val num">${cdSpan(w5.full_clear_ts, '已清空')}</div>
            <div class="roll-kpi-sub num">${fmtDateTime(w5.full_clear_ts)} 全量归零</div>
          </div>
        </div>
        <div class="chips" style="margin-top:0">
          <span class="chip num" title="释放 ${m30.calls} 次 / ${fmtTok(m30.tokens_total)} tok">30分内 <strong>+${m30.calls}次</strong> (${m30.est_imgs_with_refs > 0 ? `≈+${m30.est_imgs_with_refs}张` : fmtTok(m30.tokens_total)})</span>
          <span class="chip num" title="释放 ${m1h.calls} 次 / ${fmtTok(m1h.tokens_total)} tok">1小时内 <strong>+${m1h.calls}次</strong> (${m1h.est_imgs_with_refs > 0 ? `≈+${m1h.est_imgs_with_refs}张` : fmtTok(m1h.tokens_total)})</span>
          <span class="chip num" title="释放 ${m2h.calls} 次 / ${fmtTok(m2h.tokens_total)} tok">2小时内 <strong>+${m2h.calls}次</strong> (${m2h.est_imgs_with_refs > 0 ? `≈+${m2h.est_imgs_with_refs}张` : fmtTok(m2h.tokens_total)})</span>
        </div>
        <div class="roll-list">${buckets.map((b) => {
          const pctW = Math.max(6, Math.round((b.calls / maxBucketCalls) * 100));
          const wrPart = b.est_imgs_with_refs > 0 ? `（约合 +${b.est_imgs_with_refs} 张带垫图）` : '';
          const tip = `${fmtClock(b.first_free_ts)}–${fmtClock(b.last_free_ts)}：释放 ${b.calls} 次（${b.ok}成/${b.eof}EOF/${b.err}错），约 ${fmtTok(b.tokens_total)} tok${wrPart}，累计已释放 ${b.cum_calls}/${rs.total_calls} (${b.cum_pct}%)`;
          return `<div class="roll-row num" title="${esc(tip)}">
            <span class="roll-time">${fmtHM(b.bucket_start_ts)}–${fmtHM(b.bucket_end_ts)}</span>
            <div class="roll-bar"><div class="roll-fill" style="width:${pctW}%"></div></div>
            <span class="roll-val"><strong>+${b.calls}次</strong> · ${fmtTok(b.tokens_total)} <span class="dim">(${b.cum_pct}%)</span></span>
          </div>`;
        }).join('')}</div>
      ` : `<div class="sub">当前 5 小时滚动窗口已完全清空，暂无待释出调用。每次生图都会在发起满 5 小时后自动滚出释放。</div>`}
    </section>`);

    // 2b. 7-day weekly rolling schedule (day-aligned) & weekly 429 tracking
    const wrs = iq.weekly_rolling_schedule || {};
    const wBuckets = wrs.buckets || [];
    const maxWBucketCalls = Math.max(1, ...wBuckets.map((b) => Number(b.calls) || 0));
    const wfb = wrs.first_batch;
    const wm24 = wrs.recover_next_24h || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    const wm48 = wrs.recover_next_48h || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    const wm72 = wrs.recover_next_72h || { calls: 0, tokens_total: 0, est_imgs_with_refs: 0 };
    const peak7d = cal.observed_7d_safe_peak || {};
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">7 天周滚轴 · 按日回血时间表 ${wrs.cooldown_active ? '<span class="pill bad">周池冷却</span>' : ''}</span>
        <span class="sub num">${wBuckets.length ? `近7天 ${wrs.total_calls} 次 (${fmtTok(wrs.total_tokens_total)})` : '7天无占用'}</span>
      </div>
      ${wBuckets.length ? `
        <div class="roll-head">
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">${wrs.cooldown_active ? '预计周解封 / 下批回血' : '下批周滚出倒计时'}</div>
            <div class="roll-kpi-val num" style="color:${wrs.cooldown_active ? 'var(--bad)' : 'var(--ok)'}">${cdSpan(wrs.next_recover_ts, '即将释放')}</div>
            <div class="roll-kpi-sub num">${fmtDateTime(wrs.next_recover_ts)}${wfb ? ` · 先释 +${wfb.calls}次 (${fmtTok(wfb.tokens_total)})` : ''}</div>
          </div>
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">7 天完全清空</div>
            <div class="roll-kpi-val num">${cdSpan(wrs.full_clear_ts, '已清空')}</div>
            <div class="roll-kpi-sub num">${fmtDateTime(wrs.full_clear_ts)} 全量滚出</div>
          </div>
        </div>
        <div class="chips" style="margin-top:0">
          <span class="chip num" title="未来 24 小时滚出 ${wm24.calls} 次 / ${fmtTok(wm24.tokens_total)} tok">24h内 <strong>+${wm24.calls}次</strong></span>
          <span class="chip num" title="未来 48 小时滚出 ${wm48.calls} 次 / ${fmtTok(wm48.tokens_total)} tok">48h内 <strong>+${wm48.calls}次</strong></span>
          <span class="chip num" title="未来 72 小时滚出 ${wm72.calls} 次 / ${fmtTok(wm72.tokens_total)} tok">72h内 <strong>+${wm72.calls}次</strong></span>
          ${peak7d.max_attempts > 0 ? `<span class="chip num" title="近 30 天内实测无周池 429 的最高 7 天滚动峰">30d安全周峰 <strong>${peak7d.max_attempts}次</strong></span>` : ''}
          ${cal.weekly_lock_count > 0 ? `<span class="chip num" style="border-color:color-mix(in srgb,var(--bad) 45%,transparent)">周429均值 <strong>${cal.avg_attempts_at_weekly_lock}次</strong></span>` : ''}
        </div>
        <div class="roll-list">${wBuckets.map((b) => {
          const pctW = Math.max(6, Math.round((b.calls / maxWBucketCalls) * 100));
          const wrPart = b.est_imgs_with_refs > 0 ? `（约合 +${b.est_imgs_with_refs} 张带垫图）` : '';
          const tip = `${b.date_key} (${fmtHM(b.first_free_ts)}–${fmtHM(b.last_free_ts)})：滚出释放 ${b.calls} 次（${b.ok}成/${b.eof}EOF/${b.err}错），约 ${fmtTok(b.tokens_total)} tok${wrPart}，累计已释放 ${b.cum_calls}/${wrs.total_calls} (${b.cum_pct}%)`;
          return `<div class="rec-row num" title="${esc(tip)}">
            <span class="roll-time">${esc(b.label)}</span>
            <div class="roll-bar"><div class="roll-fill pool-a" style="width:${pctW}%"></div></div>
            <span class="roll-val"><strong>+${b.calls}次</strong> · ${fmtTok(b.tokens_total)} <span class="dim">(${b.cum_pct}%)</span></span>
          </div>`;
        }).join('')}</div>
      ` : `<div class="sub">近 7 天暂无生图调用记录，每次生图都会在发起满 7 天后从周池自动滚出释放。</div>`}
      ${wrs.last_weekly_429_msg ? `<div class="callout ${wrs.cooldown_active ? 'bad' : 'warn'}" style="margin-top:7px">
        <div style="font-weight:600;margin-bottom:2px">最近官方周池 429 记录（${fmtDateTime(wrs.last_weekly_429_at)}）</div>
        <div class="mono" style="font-size:11px;word-break:break-all">${esc(wrs.last_weekly_429_msg)}</div>
      </div>` : ''}
    </section>`);

    // 3. Self-learning watermark band & per-image token profile
    const latestLock = cal.latest_lock;
    const hasCleanLock = (cal.clean_lock_count || 0) > 0;
    const lockMain = hasCleanLock
      ? `${cal.clean_avg_attempts_at_lock} 次 · ${fmtTok(cal.clean_avg_tokens_at_lock)}`
      : (latestLock
        ? (latestLock.is_truncated
          ? `${latestLock.attempts} 次 (不完整窗口)`
          : `${latestLock.attempts} 次 (${latestLock.eof_rate_pct}% EOF)`)
        : '暂无 5h 429');
    const lockSub = hasCleanLock
      ? `基于 ${cal.clean_lock_count} 次干净锁自动校准（均值 ${cal.clean_avg_ok_at_lock} 成功）`
      : (latestLock
        ? (latestLock.is_truncated
          ? `窗口开头缺失 ${Math.round((latestLock.start_gap_sec || 0) / 60)} 分钟（旧对话清理/跨端调用），未作硬上限`
          : `仅高 EOF 噪声锁（${latestLock.ok}成/${latestLock.eof}EOF），未作硬上限`)
        : '近 30 天未触发 5h 配额耗尽，仅依赖安全峰值推算');
    const byRefs = avg.by_refs || [];
    const pinnedPeak = cal.pinned_peak || null;
    const pinnedCalls = Number(anc.pinned_calls || pinnedPeak?.calls || 0);
    const isPinned = pinnedCalls >= 1 && anc.mode !== 'manual';
    const persistedTag = isPinned
      ? ' · 已固定锁定'
      : (cal.persisted_safe_peak_used ? ' · 永久账本保护' : '');
    const safeTitle = isPinned
      ? '已固定峰值上限 (永久锁定)'
      : (anc.ref_tokens_source === 'calls_x_with_refs' ? '预估上限 (已证次数×带垫图)' : '已证安全下界 (30d 无 429)');
    const safeSub = isPinned
      ? `当前实测 0-429 峰 ${anc.safe_lower_calls || peak.max_attempts || 0} 次 · 按带垫图均图折算${persistedTag}`
      : (anc.ref_tokens_source === 'calls_x_with_refs'
        ? `实测0-429峰 ${fmtTok(anc.safe_lower_tokens || peak.max_tokens_total || 0)} · 最高成功 ${peak.max_ok || 0} 次${persistedTag}`
        : `最高成功 ${peak.max_ok || 0} 次${peak.max_tokens_at ? ` · 峰值 ${fmtDateTime(peak.max_tokens_at)}` : ''}${persistedTag}`);
    const higherObsCalls = Number(anc.safe_lower_calls || peak.max_attempts || 0);
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">自学习水位带 & 均图 Token ${isPinned ? `<span class="pill pool-a num">固定 ${pinnedCalls}次</span>` : ''}</span>
        <span class="sub num">30d 样本 ${avg.sample_count || 0} 次</span>
      </div>
      <div class="band-grid">
        <div class="band-box safe">
          <div class="band-title">${esc(safeTitle)}</div>
          <div class="band-main num">${refCalls} 次 · ${fmtTok(refTokens)}</div>
          <div class="band-sub num">${esc(safeSub)}</div>
        </div>
        <div class="band-box lock">
          <div class="band-title">实测 5h 429 上界 (30d)</div>
          <div class="band-main num">${esc(lockMain)}</div>
          <div class="band-sub">${esc(lockSub)}${cal.rpm_burst_count ? ` · 另剥离 ${cal.rpm_burst_count} 次瞬时 RPM` : ''}</div>
        </div>
      </div>
      <form id="pin-peak-form" class="pin-bar${isPinned ? ' pinned' : ''}">
        <label class="pin-left" for="inp-pin-calls">
          <span>${isPinned ? '峰值已固定' : '峰值固定'}</span>
          <input id="inp-pin-calls" name="calls" class="num" type="number" min="1" max="5000" value="${isPinned ? pinnedCalls : refCalls}" title="输入要固定的 5h 生图峰值张数（如 100），固定后永不回退">
          <span class="dim" style="font-weight:500">次</span>
        </label>
        <div class="pin-actions">
          <button type="submit" class="primary">${isPinned ? '更新固定' : '固定峰值'}</button>
          ${isPinned && higherObsCalls > pinnedCalls ? `<button type="button" id="btn-pin-observed" data-calls="${higherObsCalls}">用实测 ${higherObsCalls}</button>` : ''}
          ${isPinned ? '<button type="button" id="btn-unpin-peak">取消固定</button>' : ''}
        </div>
      </form>
      ${state.pinToast ? `<div class="sub" style="margin:-3px 0 7px;color:${state.pinToast.err ? 'var(--bad)' : 'var(--ok)'}">${esc(state.pinToast.msg)}</div>` : ''}
      ${byRefs.length ? `<table class="stat-table num">
        <thead><tr><th>参考图</th><th>样本</th><th>均Prompt</th><th>均含思考Tok</th><th>API等效</th></tr></thead>
        <tbody>
          <tr style="font-weight:600">
            <td>总均值 (${avg.avg_refs}张)</td>
            <td>${avg.sample_count}</td>
            <td>${fmtTok(avg.avg_prompt_tokens)}</td>
            <td>${fmtTok(avg.avg_tokens_total)}</td>
            <td>${fmtUsd(avg.avg_est_usd)}</td>
          </tr>
          ${wrAvg.sample_count > 0 ? `<tr style="font-weight:600;background:color-mix(in srgb,var(--accent) 10%,transparent)" title="用于预估剩余可出图张数（占全部生图 ${wrAvg.share_pct}%）">
            <td style="color:var(--accent)">带垫图均 (${wrAvg.avg_refs}张)</td>
            <td>${wrAvg.sample_count}</td>
            <td>${fmtTok(wrAvg.avg_prompt_tokens)}</td>
            <td style="color:var(--accent)">${fmtTok(wrAvg.avg_tokens_total)}</td>
            <td>${fmtUsd(wrAvg.avg_est_usd)}</td>
          </tr>` : ''}
          ${byRefs.map((g) => `<tr>
            <td>${esc(g.label)} 张参考图</td>
            <td>${g.count}</td>
            <td>${fmtTok(g.avg_prompt_tokens)}</td>
            <td>${fmtTok(g.avg_tokens_total)}</td>
            <td>${fmtUsd(g.avg_est_usd)}</td>
          </tr>`).join('')}
        </tbody>
      </table>` : `<div class="sub">暂无足够生图样本，默认估算单图 ~${fmtTok(avg.avg_tokens_total || 1850)} tok（${fmtUsd(avg.avg_est_usd || 0.0682)}）。</div>`}
      <div class="sub" style="margin-top:6px;line-height:1.4">公式（Nano Banana 2 · 1K）：出图固定 1,120 tok（$0.0672，占 API 成本 ${avg.output_cost_share_pct ?? 97.6}%）+ 参考图 1,120 tok/张 + Prompt 文本 + 最小思考估 ~250 tok/次。</div>
    </section>`);

    // 4. 5h / 24h / 7d stats table
    const rows = [
      ['5h 滚轴', w5],
      ['近 24h', w24],
      ['近 7 天', w7],
    ];
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">多周期生图统计</span><span class="sub">含参考图与 Token 估算</span></div>
      <table class="stat-table num">
        <thead><tr><th>周期</th><th>成功</th><th>EOF</th><th>429</th><th>参考图</th><th>估算Tok</th><th>尝试</th></tr></thead>
        <tbody>${rows.map(([lbl, b]) => {
          const cnt429 = (b.rate_limited || 0) + (b.weekly_rate_limited || 0);
          const tip = `失败 ${b.err ?? 0} · 5h-429 ${b.rate_limited ?? 0} · 周429 ${b.weekly_rate_limited ?? 0} · RPM限流 ${b.rpm_limited ?? 0} · 参数错 ${b.client_err ?? 0} · EOF率 ${b.eof_rate_pct ?? 0}% · 不含思考 ${fmtTok(b.tokens_raw)} · API等效 ${fmtUsd(b.est_usd)}`;
          return `<tr title="${esc(tip)}">
            <td>${lbl}</td>
            <td style="color:var(--ok)">${b.ok ?? 0}</td>
            <td style="color:${(b.eof || 0) > 0 ? 'var(--warn)' : 'var(--dim)'}">${b.eof ?? 0}</td>
            <td style="color:${cnt429 > 0 ? 'var(--bad)' : 'var(--dim)'}">${cnt429}</td>
            <td>${b.ref_images ?? 0}</td>
            <td>${fmtTok(b.tokens_total)}</td>
            <td><strong>${b.attempts ?? 0}</strong></td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </section>`);

    // 5. Settings form (Auto watermark band vs Manual threshold)
    const s = d.settings || { anchorMode: 'auto', softCap: 54, hardCap: 60, tokenCap: 0, countMode: 'attempts' };
    const isManual = s.anchorMode === 'manual';
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">锚定模式与计数口径</span><span class="sub">持久化保存</span></div>
      <form id="settings-form">
        <div class="form-grid">
          <div class="field full">
            <label for="inp-anchor">配额锚定模式</label>
            <select id="inp-anchor" name="anchorMode">
              <option value="auto"${!isManual ? ' selected' : ''}>自学习水位带（推荐，无需填总数）</option>
              <option value="manual"${isManual ? ' selected' : ''}>手动阈值模式（自定义次/Token 上限）</option>
            </select>
          </div>
          <div class="field full">
            <label for="inp-mode">计数口径</label>
            <select id="inp-mode" name="countMode">
              <option value="attempts"${s.countMode === 'attempts' ? ' selected' : ''}>含 EOF / 异常全部尝试（推荐，防断流偷扣额度）</option>
              <option value="ok_only"${s.countMode === 'ok_only' ? ' selected' : ''}>仅计成功出图次数</option>
            </select>
          </div>
          <div id="manual-fields" class="field full manual-box${isManual ? '' : ' dimmed'}">
            <div class="form-grid" style="margin-top:0">
              <div class="field">
                <label for="inp-soft">软上限预警 (次)</label>
                <input id="inp-soft" name="softCap" type="number" min="1" max="2000" value="${s.softCap}">
              </div>
              <div class="field">
                <label for="inp-hard">硬上限估计 (次)</label>
                <input id="inp-hard" name="hardCap" type="number" min="1" max="2000" value="${s.hardCap}">
              </div>
              <div class="field full">
                <label for="inp-tokcap">可选 5h Token 上限（0 = 按次数×带垫图均图折算，当前参考 ~${fmtTok(refTokens)}）</label>
                <input id="inp-tokcap" name="tokenCap" type="number" min="0" max="50000000" step="1000" value="${s.tokenCap || 0}">
              </div>
            </div>
            ${!isManual ? '<div class="sub" style="margin-top:4px">当前为自学习模式，上述手动数值仅在切至「手动阈值」或样本不足 10 次时生效。</div>' : ''}
          </div>
        </div>
        <div class="form-actions">
          <button type="submit" class="primary">保存设置</button>
          <button type="button" id="btn-reset-auto">恢复自动锚定</button>
          <button type="button" id="btn-reset-cal" title="仅在更换套餐或配额下调时重置永久校准账本（双击确认）">${Date.now() < state.calResetArmUntil ? '确认重置峰值？' : '重置自学习峰值'}</button>
          <span id="save-toast" class="toast ${state.toast?.err ? 'err' : ''}">${esc(state.toast?.msg || '')}</span>
        </div>
      </form>
    </section>`);

    // 6. Workspace ledgers
    const ledgers = iq.workspace_ledgers || [];
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">工作区生产账本联动</span><span class="sub mono">api_call_ledger.json</span></div>
      ${ledgers.length ? ledgers.map((l) => `
        <div class="pool-mini" style="margin-top:6px">
          <div class="meta-row">
            <span style="color:var(--fg);font-weight:600">${esc(l.episode_label)}</span>
            <span class="sub num">${l.global_cooldown_remaining_sec > 0 ? `全局冷却 ${cdSpan(l.global_cooldown_until)}` : `更新 ${fmtDateTime(l.updated_at)}`}</span>
          </div>
          <div class="sub mono" style="font-size:10.5px;margin:2px 0 4px">${esc(l.path)}</div>
          ${(l.windows || []).map((w) => `<div class="meta-row num" style="padding:2px 0">
            <span>窗 ${esc(w.window)}：<strong>${w.used_calls}</strong>/${w.hard_cap}${w.pending_calls ? ` (+${w.pending_calls}在途)` : ''}</span>
            <span>余 ${w.remaining_calls}${w.cooldown_remaining_sec > 0 ? ` · 冷却 ${cdSpan(w.cooldown_until)}` : ''}</span>
          </div>`).join('') || '<div class="sub">暂无已登记窗口</div>'}
        </div>`).join('')
      : `<div class="sub">当前工作区未发现可选的 <span class="mono">api_call_ledger.json</span> 账本文件。</div>`}
    </section>`);

    // 7. Recent events
    const evs = iq.recent_events || [];
    const archDbs = Number(iq.scan_meta?.archived_dbs || 0);
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">最近生图事件 (最新 20 条)</span><span class="sub num">扫描 ${iq.scan_meta?.active_dbs ?? 0} 库${archDbs > 0 ? ` + ${archDbs} 归档` : ''}</span></div>
      ${evs.length ? `<ul class="list">${evs.map((e) => {
        const ti = eventTagInfo(e.kind);
        const title = e.image_name || e.prompt_preview || '(未命名图像)';
        const detail = e.error_summary || e.prompt_preview || '';
        const badges = [];
        if (Number(e.n_refs) > 0) badges.push(`<span class="mini-badge num">${e.n_refs}参</span>`);
        if (Number(e.tokens_total) > 0) badges.push(`<span class="mini-badge num" title="Prompt ${e.prompt_tokens || 0} + 参考图 ${e.input_image_tokens || 0} + 1K出图 ${e.output_image_tokens || 0} + 思考估 ${e.thinking_tokens || 0}">${fmtTok(e.tokens_total)} tok</span>`);
        if (Number(e.duration_sec) > 0) badges.push(`<span class="mini-badge num">${e.duration_sec}s</span>`);
        return `<li class="ev-item">
          <span class="dim num" title="${esc(fmtDateTime(e.ts))}">${fmtClock(e.ts)}</span>
          <span class="tag ${esc(ti.cls)}" title="${esc(ti.tip)}">${esc(ti.text)}</span>
          <div style="min-width:0">
            <div class="ev-name" title="${esc(title)}">${esc(title)}</div>
            ${badges.length ? `<div class="ev-meta">${badges.join('')}</div>` : ''}
            ${detail && detail !== title ? `<div class="ev-sub" title="${esc(detail)}">${esc(detail)}</div>` : ''}
          </div>
        </li>`;
      }).join('')}</ul>` : '<div class="sub">近 30 天无 generate_image 调用记录。</div>'}
    </section>`);

    return parts.join('');
  }

  Object.assign(QC, {
    imgStatusMeta,
    eventTagInfo,
    renderImage,
  });
})(window.__QC__ = window.__QC__ || {});
