(function (QC) {
  'use strict';

  const {
    state, esc, fmtClock, fmtHM, fmtDateTime, cdSpan, barFillColor, fmtCredits,
  } = QC;

  function flowStatusMeta(fq) {
    if (!fq || !fq.available) return { pill: '', big: '', text: '未检测' };
    if (fq.health === 'locked') {
      const manual = fq.breaker?.requires_manual_reset;
      return { pill: 'bad', big: 'bad', text: manual ? '人工熔断' : '限流冷却' };
    }
    if (fq.health === 'critical') return { pill: 'bad', big: 'bad', text: fq.status_label || '已达上限' };
    if (fq.health === 'warning') return { pill: 'warn', big: 'warn', text: fq.status_label || '逼近上限' };
    return { pill: fq.credits?.synced ? 'ok' : '', big: fq.credits?.synced ? 'ok' : '', text: fq.status_label || '正常' };
  }

  function renderFlowOverviewCard(fq) {
    if (!fq || fq.error) {
      return `<section class="card">
        <div class="ch"><span class="ctitle">Flow 生视频/生图配额</span><span class="pill bad">读取失败</span></div>
        <div class="sub">${esc(fq?.message || '无法读取本地 Flow 账本与记录')}</div>
      </section>`;
    }
    if (!fq.available) {
      return `<section class="card">
        <div class="ch"><span class="ctitle">Flow 生视频/生图配额 <span class="pill">未安装</span></span><button class="link-btn" data-jump="flow">配置 →</button></div>
        <div class="sub">未检测到 <span class="mono">gflow-cli</span> 或本地 Flow 账本，安装并登录后即可实时监控。</div>
      </section>`;
    }
    const cr = fq.credits || {};
    const rl = fq.rolling || {};
    const br = fq.breaker || {};
    const iq = fq.image_quota || {};
    const meta = flowStatusMeta(fq);
    const models = fq.model_estimates || [];
    const veo = models.find((m) => m.id === 'veo_3_1_lite') || models[0];
    const omni = models.find((m) => m.id === 'omni_1_2_flash') || models[1];
    const wafUsed = Number(rl.waf_window_tokens || 0);
    const wafCap = Number(rl.waf_effective_limit || 8);
    const wafPct = Number(rl.waf_pct || 0);
    const hourEnabled = Boolean(rl.hour_enabled ?? (Number(rl.hour_limit || 0) > 0));
    const bnPct = Math.min(100, Number(rl.bottleneck_pct || 0));
    const bnHealth = br.open ? 'locked' : (bnPct >= 100 ? 'critical' : (bnPct >= 80 ? 'warning' : 'ok'));
    const syncBtnTxt = state.flowSyncing ? '同步中…' : (cr.synced ? '同步积分' : '同步真实积分');

    let bigHtml = '';
    if (cr.synced && cr.estimated_total !== null) {
      const approx = Number(cr.spent_since_sync || 0) > 0 ? '≈ ' : '';
      const veoTxt = veo && veo.est_segments !== null ? `Veo Lite ~${veo.est_segments}段` : `净可用 ${fmtCredits(cr.spendable)}`;
      bigHtml = `<div class="bigrow">
        <div class="big ${cr.below_floor ? 'bad' : meta.big} num">${approx}${fmtCredits(cr.estimated_total)} <span class="dim" style="font-size:12.5px;font-weight:500">积分</span></div>
        <span class="sub num" style="color:var(--fg);font-weight:600" title="已扣安全底线 ${fmtCredits(cr.reserve_floor)} 积分，净可用 ${fmtCredits(cr.spendable)} 积分">${esc(veoTxt)}</span>
      </div>`;
    } else {
      bigHtml = `<div class="bigrow">
        <div class="big dim" style="font-size:16px">余额未同步</div>
        <button class="primary" data-flow-sync="1" ${state.flowSyncing ? 'disabled' : ''}>${esc(syncBtnTxt)}</button>
      </div>`;
    }

    const nextSlotHtml = wafUsed > 0 && rl.waf_next_recover_ts > 0
      ? `10m WAF 回血 ${cdSpan(rl.waf_next_recover_ts, '即将释放')}`
      : (hourEnabled && rl.hour_used > 0 && rl.hour_next_recover_ts > 0
        ? `1h 下槽回血 ${cdSpan(rl.hour_next_recover_ts, '即将释放')}`
        : (rl.day_used > 0 && rl.day_next_recover_ts > 0
          ? `24h 视频滚轴回血 ${cdSpan(rl.day_next_recover_ts, '即将释放')}`
          : '10m WAF / 24h 滚轴空闲'));
    const syncMeta = cr.synced && cr.synced_at
      ? `同步于 ${fmtHM(cr.synced_at)}${cr.spent_since_sync > 0 ? ` · 新扣 ${fmtCredits(cr.spent_since_sync)}` : ''}`
      : (cr.default_profile ? `账号 ${cr.default_profile}` : '点击同步获取余额');

    const modeBadges = [
      rl.is_peak_hours ? '<span class="pill warn" title="全球高峰期 (22:00–01:00 CST)，10m WAF 自动压回 ≤6">高峰≤6</span>' : '',
      rl.recent_waf_recovery ? '<span class="pill warn" title="24h WAF 恢复期内，10m WAF 压回 ≤6">恢复≤6</span>' : '',
    ].filter(Boolean).join('');

    const poolsList = iq.pools || [];
    const proPool = poolsList.find((p) => p.id === 'nano_pro');
    const n2Pool = poolsList.find((p) => p.id === 'nano2_shared');
    const upUsed = poolsList
      .filter((p) => p.id === 'image_upscale_2k' || p.id === 'image_upscale_4k')
      .reduce((acc, p) => acc + Number(p.used || 0), 0);
    const hasImgActivity = Number(iq.total_units_24h || 0) > 0 || Number(iq.active_model_breaker_count || 0) > 0;

    const breakerHtml = br.open
      ? `<div class="callout bad" style="margin-top:6px">${br.requires_manual_reset
        ? `人工熔断锁定：${esc(br.reason || '需人工验证并执行 reset-breaker')}`
        : `限流熔断冷却中：${cdSpan(br.until_ts, '已解封')}（${esc(br.reason || '请等待冷却')}）`}</div>`
      : '';
    const activeModelBreakers = Object.values(fq.model_breakers || iq.model_breakers || {});
    const mbHtml = !br.open && activeModelBreakers.length
      ? `<div class="callout warn" style="margin-top:6px">单池隔离：${activeModelBreakers.map((mb) => `${esc(mb.label)}（至 ${fmtHM(mb.until_ts)}）`).join('、')}</div>`
      : '';
    const heatHtml = !br.open && iq.heat_rest_due
      ? `<div class="callout warn" style="margin-top:6px">24h 生图提交已满 ${iq.daily_image_submits} 次，建议深休 20–30m 避开日内热度墙。</div>`
      : '';

    const barTip = `防风控水位取 10m WAF(${wafPct}%)${hourEnabled ? ` / 1h(${rl.hour_pct}%)` : ''} / 24h视频(${rl.day_pct}%) / 24h积分(${rl.day_credits_pct}%) 最高值`;
    const wafChipTip = `视频 ${rl.waf_video_tokens || 0} + 生图/放大 ${rl.waf_image_tokens || 0} tok（配置上限 ${rl.waf_configured_limit || wafCap}，当前有效上限 ${wafCap}）`;

    return `<section class="card">
      <div class="ch">
        <span class="ctitle">Flow 生视频/生图 · 10m WAF <span class="pill ${meta.pill}">${esc(meta.text)}</span>${modeBadges}</span>
        <span class="pill-row">
          ${cr.synced ? `<button data-flow-sync="1" style="padding:1px 6px;font-size:10.5px" ${state.flowSyncing ? 'disabled' : ''} title="手动触发 gflow 读取真实余额（约 8–10s，绝不后台自动轮询）">${esc(syncBtnTxt)}</button>` : ''}
          <button class="link-btn" data-jump="flow">详情 →</button>
        </span>
      </div>
      ${bigHtml}
      <div class="bar-wrap" title="${esc(barTip)}">
        <div class="bar-fill" style="width:${bnPct}%;background:${barFillColor(bnHealth, 'pool-c')}"></div>
      </div>
      <div class="meta-row">
        <span class="num">${nextSlotHtml}</span>
        <span class="num">${esc(syncMeta)}</span>
      </div>
      <div class="chips">
        <span class="chip num" title="${esc(wafChipTip)}">10m WAF <strong>${wafUsed}/${wafCap}tok</strong></span>
        ${hourEnabled ? `<span class="chip num">1h <strong>${rl.hour_used}/${rl.hour_limit}次</strong></span>` : ''}
        <span class="chip num">24h视频 <strong>${rl.day_used}/${rl.day_limit}次</strong></span>
        <span class="chip num">24h积分 <strong>${fmtCredits(rl.day_credits_used)}/${fmtCredits(rl.day_credits_limit)}</strong></span>
        ${hasImgActivity && proPool && proPool.used > 0 ? `<span class="chip num">Pro图 <strong>${proPool.used}/${proPool.cap}</strong></span>` : ''}
        ${hasImgActivity && n2Pool && n2Pool.used > 0 ? `<span class="chip num">Nano2图 <strong>${n2Pool.used}/${n2Pool.cap}</strong></span>` : ''}
        ${hasImgActivity && upUsed > 0 ? `<span class="chip num">图放大 <strong>${upUsed}次</strong></span>` : ''}
        ${omni && omni.est_segments !== null ? `<span class="chip num">Omni 1.2 <strong>~${omni.est_segments}段</strong></span>` : ''}
        ${(fq.usage?.window_7d?.total || 0) > 0 ? `<span class="chip num">7天 <strong>${fq.usage.window_7d.total}条</strong></span>` : ''}
      </div>
      ${breakerHtml}
      ${mbHtml}
      ${heatHtml}
    </section>`;
  }

  function renderFlowSchedule(buckets, label, unit = '次') {
    if (!buckets || !buckets.length) return '';
    const maxCnt = Math.max(1, ...buckets.map((b) => Number(b.count || 0)));
    return `<div class="rec-panel">
      <div class="sub" style="margin-bottom:4px;font-weight:600;color:var(--fg)">${esc(label)}</div>
      <div class="roll-list">${buckets.map((b) => {
        const pct = Math.max(6, Math.round((Number(b.count || 0) / maxCnt) * 100));
        const crTxt = Number(b.credits || 0) > 0 ? ` / +${fmtCredits(b.credits)}分` : '';
        return `<div class="roll-row">
          <span class="roll-time num">${fmtHM(b.recover_ts)} (${cdSpan(b.recover_ts, '释放')})</span>
          <div class="roll-bar"><div class="roll-fill pool-c" style="width:${pct}%"></div></div>
          <span class="roll-val num">+${b.count}${esc(unit)}${crTxt}</span>
        </div>`;
      }).join('')}</div>
    </div>`;
  }

  function renderFlow(d) {
    const fq = d.flowQuota;
    if (!fq || fq.error) {
      return `<section class="card"><div class="ch"><span class="ctitle">Flow 生视频配额</span></div>
        <div class="callout bad">${esc(fq?.message || '读取 Flow 数据失败')}</div></section>`;
    }
    if (!fq.available) {
      return `<section class="card">
        <div class="ch"><span class="ctitle">Flow 生视频配额 <span class="pill">未检测到</span></span></div>
        <div class="callout info">未在本机检测到 <span class="mono">gflow-cli</span>、本地 <span class="mono">gflow.db</span> 或滚动防限流账本。<br>安装 <span class="mono">gflow-cli</span> 并执行 <span class="mono">gflow auth login</span> 登录 Google Flow 后，即可在此实时查看账号积分、模型可生段数与 1h/24h 防限流水位。</div>
      </section>`;
    }

    const cr = fq.credits || {};
    const rl = fq.rolling || {};
    const br = fq.breaker || {};
    const pol = fq.policy || {};
    const usage = fq.usage || {};
    const ds = fq.data_sources || {};
    const meta = flowStatusMeta(fq);
    const parts = [];

    // 1. Credits & Model Capacity Card
    const syncBtnTxt = state.flowSyncing ? '同步中(约10s)…' : '同步真实积分';
    const profilesHtml = (cr.profiles || []).map((p) => {
      const cTxt = p.credits !== null && p.credits !== undefined ? ` · ${fmtCredits(p.credits)} 积分` : '';
      const ckTxt = p.cookies_present || p.authenticated ? 'Cookie 正常' : '未登录';
      return `<span class="chip num" title="${esc(p.email || p.profile)}">${esc(p.profile)}${p.is_default ? ' (默认)' : ''}: <strong>${esc(ckTxt)}${esc(cTxt)}</strong></span>`;
    }).join('');

    const modelsHtml = (fq.model_estimates || []).map((m) => {
      const segTxt = m.est_segments !== null ? `<strong>~${m.est_segments} 段</strong>` : '<span class="dim">同步后计算</span>';
      const statTxt = (m.recent_30d_ok + m.recent_30d_fail) > 0
        ? `${m.recent_30d_ok}成/${m.recent_30d_fail}败`
        : '<span class="dim">--</span>';
      return `<tr>
        <td><div style="font-weight:600">${esc(m.label)}</div><div class="dim" style="font-size:10px">${m.max_duration}s · ≤${m.ref_cap}参</div></td>
        <td class="num">~${fmtCredits(m.unit_credits)}</td>
        <td class="num">${segTxt}</td>
        <td class="num">${statTxt}</td>
      </tr>`;
    }).join('');

    const retiringHtml = (fq.retiring_models || []).length
      ? `<details class="flow-details">
          <summary>即将退役模型参考（2026-12-05 下线，生产禁用）</summary>
          <table class="stat-table" style="margin-top:4px">
            <thead><tr><th>模型</th><th>参考单价</th><th>可生估算</th><th>退役日期</th></tr></thead>
            <tbody>${fq.retiring_models.map((m) => `<tr>
              <td class="dim">${esc(m.label)}</td>
              <td class="num dim">~${fmtCredits(m.unit_credits)}</td>
              <td class="num dim">${m.est_segments !== null ? `~${m.est_segments}段` : '--'}</td>
              <td class="num dim">${esc(m.retires_on || '--')}</td>
            </tr>`).join('')}</tbody>
          </table>
        </details>`
      : '';

    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">Google Flow 积分余额 <span class="pill ${meta.pill}">${esc(meta.text)}</span></span>
        <button class="primary" data-flow-sync="1" ${state.flowSyncing ? 'disabled' : ''} title="手动执行 gflow credits list --json（零扣费，代理下约耗时 8–10 秒）">${esc(syncBtnTxt)}</button>
      </div>
      ${cr.synced && cr.estimated_total !== null
        ? `<div class="bigrow">
            <div class="big ${cr.below_floor ? 'bad' : meta.big} num">${cr.spent_since_sync > 0 ? '≈ ' : ''}${fmtCredits(cr.estimated_total)} <span class="dim" style="font-size:12.5px;font-weight:500">积分</span></div>
            <span class="sub num">净可用 <strong style="color:var(--fg)">${fmtCredits(cr.spendable)}</strong>（底线 ${fmtCredits(cr.reserve_floor)}）</span>
          </div>
          <div class="meta-row">
            <span class="num">上次同步：${fmtDateTime(cr.synced_at)}（${fmtCredits(cr.synced_total)} 积分）</span>
            <span class="num">${cr.spent_since_sync > 0 ? `同步后账本新扣 -${fmtCredits(cr.spent_since_sync)}` : '与上次同步一致'}</span>
          </div>`
        : `<div class="callout info" style="margin:4px 0 6px">尚未同步过 Flow 实时积分。为避免后台高频唤起浏览器触发风控，余额采用<strong>手动按需同步</strong>，同步后将根据本地账本自动实时扣减估算。</div>`}
      ${state.flowToast ? `<div class="toast ${state.flowToast.err ? 'err' : ''}" style="margin-top:5px">${esc(state.flowToast.msg)}</div>` : ''}
      ${cr.last_error && !state.flowToast ? `<div class="callout warn" style="margin-top:6px">上次同步提示（${fmtHM(cr.last_error_at)}）：${esc(cr.last_error)}</div>` : ''}
      ${cr.profile_busy ? `<div class="callout warn" style="margin-top:6px">检测到活跃 Profile 锁：${esc(cr.busy_detail)}（同步会自动避让防抢锁）。</div>` : ''}
      ${br.open ? `<div class="callout bad" style="margin-top:6px"><strong>${br.requires_manual_reset ? '人工熔断已锁定' : '限流熔断冷却中'}</strong>：${esc(br.reason || '已阻断自动生视频')}${br.requires_manual_reset ? '<br><span class="dim">请先在浏览器核验 Flow 账号状态，确认正常后执行 reset-breaker 解锁。</span>' : `（剩余 ${cdSpan(br.until_ts, '已解封')}）`}</div>` : ''}
      ${profilesHtml ? `<div class="chips">${profilesHtml}</div>` : ''}
      <div class="sec-sep">
        <div class="h2"><span>主力视频模型可生估算</span><span class="hint">已扣安全底线 · 1080p超分免费</span></div>
        <table class="stat-table">
          <thead><tr><th>模型</th><th>参考积分</th><th>净可生</th><th>近30天</th></tr></thead>
          <tbody>${modelsHtml}</tbody>
        </table>
        ${retiringHtml}
      </div>
    </section>`);

    // 2. Cross-Modal Rolling Anti-Throttle Card (10m WAF / optional 1h / 24h video & credits)
    const barRow = (title, usedTxt, pct, extraRight, extraBadge = '') => {
      const p = Math.min(100, Number(pct || 0));
      const h = p >= 100 ? 'critical' : (p >= 80 ? 'warning' : 'ok');
      return `<div class="win-box">
        <div class="win-head">
          <span class="win-name">${esc(title)}${extraBadge}</span>
          <span class="num"><strong class="win-pct ${h === 'critical' ? 'bad' : h === 'warning' ? 'warn' : ''}">${esc(usedTxt)}</strong> <span class="dim">(${Number(pct || 0).toFixed(0)}%)</span></span>
        </div>
        <div class="bar-wrap sm"><div class="bar-fill" style="width:${p}%;background:${barFillColor(h, 'pool-c')}"></div></div>
        <div class="meta-row"><span>${extraRight}</span></div>
      </div>`;
    };
    const wafUsed = Number(rl.waf_window_tokens || 0);
    const wafCap = Number(rl.waf_effective_limit || pol.effective_max_recaptcha_10m || 8);
    const wafCfgCap = Number(rl.waf_configured_limit || pol.max_recaptcha_10m || 8);
    const wafPct = Number(rl.waf_pct || 0);
    const hourEnabled = Boolean(rl.hour_enabled ?? (Number(rl.hour_limit || 0) > 0));
    const s2sGap = Number(rl.effective_s2s_gap_sec || pol.effective_s2s_gap_sec || 65);

    const gapHtml = rl.min_gap_wait_sec > 0
      ? `<span class="pill warn num">间隔冷却 ${cdSpan(rl.min_gap_ready_ts, '就绪')}</span>`
      : (rl.s2s_wait_sec > 0
        ? `<span class="pill warn num" title="生图 Start-to-Start 节拍 ${s2sGap}s">S2S ${cdSpan(rl.s2s_ready_ts, '就绪')}</span>`
        : `<span class="pill ok" title="视频最小间隔 ${pol.min_gap_sec ?? 35}s · 生图 S2S ${s2sGap}s">间隔就绪 (${pol.min_gap_sec ?? 35}s)</span>`);
    const peakPill = rl.is_peak_hours
      ? '<span class="pill warn" title="全球高峰期 北京 22:00–01:00 (UTC 14–17)：10m WAF 自动压回 ≤6，生图 S2S 拉宽至 75s">高峰期 ≤6</span>'
      : '';
    const recovPill = rl.recent_waf_recovery
      ? `<span class="pill warn" title="近 24h 触发过 WAF：10m 压回 ≤6，生图 S2S 拉宽至 80s，预计 ${esc(fmtDateTime(rl.waf_recovery_until_ts))} 解除">24h恢复 ≤6</span>`
      : '';

    const wafCapNote = wafCap < wafCfgCap ? ` · 已压回(配${wafCfgCap})` : '';
    const wafSub = wafUsed > 0
      ? `视频 ${rl.waf_video_tokens || 0} + 生图/放大 ${rl.waf_image_tokens || 0} tok${wafCapNote} · 最早释放 ${cdSpan(rl.waf_next_recover_ts, '即将释放')} · ${fmtHM(rl.waf_full_clear_ts)} 清空`
      : `当前 10m 窗空闲${wafCapNote} · 角色建档与未发包计 0 tok`;
    const hourBlock = hourEnabled
      ? barRow('1 小时视频频次（自定）', `${rl.hour_used} / ${rl.hour_limit} 次`, rl.hour_pct, rl.hour_used > 0 ? `最早一笔释放：${cdSpan(rl.hour_next_recover_ts, '即将释放')} · ${fmtHM(rl.hour_full_clear_ts)} 清空` : '当前 1h 窗无占用')
      : `<div class="win-box"><div class="meta-row"><span style="color:var(--fg);font-weight:500">1 小时视频硬限已关闭</span><span class="num">近 1h 已跑 ${rl.hour_used || 0} 次 · 由 10m WAF 墙与 24h 预算统一管控</span></div></div>`;
    const charHint = Number(rl.day_character_used || 0) > 0 ? `（含 ${rl.day_character_used} 建档）` : '';

    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">跨模态防风控水位 · ${ds.rate_limit_source === 'ledger' ? `全局账本 v${ds.ledger_version || 2}` : ds.rate_limit_source === 'gflow_db' ? 'gflow.db' : '暂无记录'}</span>
        <span class="pill-row">${peakPill}${recovPill}${gapHtml}</span>
      </div>
      <div class="win-list">
        ${barRow('10 分钟合并 WAF 令牌（视频+生图+放大）', `${wafUsed} / ${wafCap} tok`, wafPct, wafSub)}
        ${hourBlock}
        ${barRow('24 小时视频软预算（次数）', `${rl.day_used} / ${rl.day_limit} 次`, rl.day_pct, rl.day_used > 0 ? `最早释放 ${cdSpan(rl.day_next_recover_ts, '即将释放')}（${fmtDateTime(rl.day_next_recover_ts)}）${charHint}` : '当前 24h 视频窗无占用')}
        ${barRow('24 小时视频软预算（积分）', `${fmtCredits(rl.day_credits_used)} / ${fmtCredits(rl.day_credits_limit)} 积分`, rl.day_credits_pct, `余 ${fmtCredits(rl.day_credits_remaining)} 积分预算 · 生图 S2S 节拍 ${s2sGap}s`)}
      </div>
      ${rl.untracked_db_24h > 0 ? `<div class="callout warn" style="margin-top:6px">本地 <span class="mono">gflow.db</span> 近 24h 额外记录了 <strong>+${rl.untracked_db_24h}</strong> 次未入账本的视频直调/MCP 生成，请注意实际风控频次。</div>` : ''}
      ${rl.untracked_db_image_24h > 0 ? `<div class="callout warn" style="margin-top:6px">本地 <span class="mono">gflow.db</span> 近 24h 额外记录了 <strong>+${rl.untracked_db_image_24h}</strong> 次未入账本的生图直调/MCP 生成。</div>` : ''}
      ${renderFlowSchedule(rl.waf_schedule, '10m WAF 令牌回血时间表', 'tok')}
      ${hourEnabled ? renderFlowSchedule(rl.hour_schedule, '1h 槽位回血时间表', '次') : ''}
      ${renderFlowSchedule(rl.day_schedule, '24h 视频滚轴回血预告', '次')}
    </section>`);

    // 2.5. Flow 0-Credit Daily Image & Upscale Pools
    const iq = fq.image_quota || {};
    const imgPools = iq.pools || [];
    const activeModelBreakers = Object.values(fq.model_breakers || iq.model_breakers || {});
    if (imgPools.length) {
      const heatEvery = Number(iq.heat_rest_every || 80);
      const dailySubmits = Number(iq.daily_image_submits || 0);
      const heatPill = iq.heat_rest_due
        ? `<span class="pill warn">满 ${dailySubmits} 次 · 建议深休 20–30m</span>`
        : `<span class="sub num">24h 生图提交 ${dailySubmits}/${heatEvery} · 4x=1tok扣4张</span>`;
      parts.push(`<section class="card">
        <div class="ch">
          <span class="ctitle">Flow 0 积分生图与放大日池</span>
          ${heatPill}
        </div>
        <div class="win-list">
          ${imgPools.map((p) => {
            const brBadge = p.breaker
              ? ` <span class="pill bad" title="${esc(p.breaker.reason || '当日配额触顶隔离')}">隔离至 ${fmtHM(p.breaker.until_ts)}</span>`
              : '';
            const subTxt = `余 ${p.remaining} ${p.unit}${p.submits > 0 ? ` · ${p.submits}次提交` : ''} · ${p.note || ''}`;
            return barRow(p.label, `${p.used} / ${p.cap} ${p.unit}`, p.pct, subTxt, brBadge);
          }).join('')}
        </div>
        ${activeModelBreakers.length ? `<div class="callout warn" style="margin-top:6px"><strong>单模型池配额隔离（不影响视频与其他池）</strong>：${activeModelBreakers.map((mb) => `${esc(mb.label)} 冷却剩余 ${cdSpan(mb.until_ts, '已解封')}`).join('；')}</div>` : ''}
        ${iq.heat_rest_due ? `<div class="callout warn" style="margin-top:6px">近 24h 生图提交已达 <strong>${dailySubmits}</strong> 次（每 ${heatEvery} 次触发主动深休线），建议休息 20–30 分钟避开日内热度墙。</div>` : ''}
      </section>`);
    }

    // 3. 7d / 30d Usage & Risk Summary
    const w7 = usage.window_7d || {};
    const w30 = usage.window_30d || {};
    const byMod = usage.by_model_30d || [];
    const byImgMod = usage.by_image_model_30d || [];
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">近 7 天 / 30 天生成统计</span><span class="sub">只读 gflow.db</span></div>
      <div class="roll-head">
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">近 7 天</div>
          <div class="roll-kpi-val num">${w7.total || 0} 视频${(w7.image_total || 0) > 0 ? ` · ${w7.image_total} 图` : ''}</div>
          <div class="roll-kpi-sub num">${w7.ok || 0} 成 / ${w7.failed || 0} 败 · WAF ${w7.waf || 0}</div>
        </div>
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">近 30 天</div>
          <div class="roll-kpi-val num">${w30.total || 0} 视频${(w30.image_total || 0) > 0 ? ` · ${w30.image_total} 图` : ''}</div>
          <div class="roll-kpi-sub num">${w30.ok || 0} 成 / ${w30.failed || 0} 败 · 角色 ${w30.character_total || 0}</div>
        </div>
      </div>
      ${byMod.length ? `<table class="stat-table">
        <thead><tr><th>近 30 天视频模型</th><th>成功</th><th>失败</th><th>合计</th></tr></thead>
        <tbody>${byMod.map((m) => `<tr>
          <td>${esc(m.label)}</td>
          <td class="num">${m.ok}</td>
          <td class="num">${m.failed}</td>
          <td class="num"><strong>${m.total}</strong></td>
        </tr>`).join('')}</tbody>
      </table>` : '<div class="sub">近 30 天暂无视频生成记录。</div>'}
      ${byImgMod.length ? `<table class="stat-table" style="margin-top:6px">
        <thead><tr><th>近 30 天生图模型</th><th>成功</th><th>失败</th><th>合计</th></tr></thead>
        <tbody>${byImgMod.map((m) => `<tr>
          <td>${esc(m.label)}</td>
          <td class="num">${m.ok}</td>
          <td class="num">${m.failed}</td>
          <td class="num"><strong>${m.total}</strong></td>
        </tr>`).join('')}</tbody>
      </table>` : ''}
      ${(usage.recent_waf_errors || []).length ? `<div class="callout warn" style="margin-top:7px">最近 WAF/风控记录（${fmtDateTime(usage.recent_waf_errors[0].ts)}）：${esc(usage.recent_waf_errors[0].detail || usage.recent_waf_errors[0].error_type)}</div>` : ''}
    </section>`);

    // 4. Optional Workspace Run Progress (Video Episodes & Image Batches + Project Rotation)
    const runs = fq.workspace_runs || [];
    if (runs.length) {
      parts.push(`<section class="card">
        <div class="ch"><span class="ctitle">工作区在制 Flow 进度</span><span class="sub">${runs.length} 个</span></div>
        <table class="stat-table">
          <thead><tr><th>项目</th><th>完成</th><th>待/败</th><th>轮换 / 积分</th></tr></thead>
          <tbody>${runs.map((r) => {
            const isImg = r.kind === 'image';
            const kindBadge = `<span class="mini-badge">${isImg ? '生图' : '视频'}</span>`;
            const rot = r.project_rotation;
            const rotNear = rot && rot.limit > 0 && (rot.used / rot.limit) >= 0.8;
            const rightCol = rot
              ? `<span class="${rotNear ? 'warn' : ''}" title="单 Project 轮换水位（已建 ${rot.project_count || 1} 个项目）">${rot.used}/${rot.limit}${esc(rot.unit)}</span>${!isImg && r.credits_used > 0 ? ` · ${fmtCredits(r.credits_used)}分` : ''}`
              : (isImg ? '0分' : `${fmtCredits(r.credits_used)}分`);
            return `<tr>
              <td title="${esc(r.path)}">${kindBadge} ${esc(r.episode)}</td>
              <td class="num"><strong>${r.completed}/${r.total}</strong></td>
              <td class="num">${r.counts.pending}/${r.counts.failed}${r.counts.uncertain ? ` (${r.counts.uncertain}待核)` : ''}${r.counts.blocked ? ` (${r.counts.blocked}缓)` : ''}</td>
              <td class="num">${rightCol}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>
      </section>`);
    }

    // 5. Recent Events
    const evs = fq.recent_events || [];
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">最近 Flow 记录</span><span class="sub">最新 ${evs.length} 条</span></div>
      ${evs.length ? `<ul class="list">${evs.map((e) => `<li class="ev-item">
        <span class="dim num" title="${esc(fmtDateTime(e.ts))}">${fmtClock(e.ts)}</span>
        <span class="tag ${esc(e.tag)}">${esc(e.tag)}</span>
        <div style="min-width:0">
          <div class="ev-name" title="${esc(e.title)}">${esc(e.title)}</div>
          <div class="ev-meta">
            <span class="mini-badge">${esc(e.action)}</span>
            ${e.model_label ? `<span class="mini-badge">${esc(e.model_label)}</span>` : ''}
            ${Number(e.quota_units || 1) > 1 ? `<span class="mini-badge num">${e.quota_units}张</span>` : ''}
            ${Number(e.credits) > 0 ? `<span class="mini-badge num">-${fmtCredits(e.credits)}分</span>` : ''}
            ${e.is_local_unsubmitted ? '<span class="mini-badge">未发包 0tok</span>' : ''}
            ${e.outcome && e.outcome !== 'OK' ? `<span class="mini-badge">${esc(e.outcome)}</span>` : ''}
          </div>
        </div>
      </li>`).join('')}</ul>` : '<div class="sub">暂无 Flow 生成记录。</div>'}
    </section>`);

    // 6. Anti-Throttle Threshold Settings Form
    const s = d.settings || {};
    parts.push(`<section class="card">
      <div class="ch"><span class="ctitle">防限流警戒阈值设置</span><span class="sub">仅影响监控告警</span></div>
      <form id="flow-settings-form">
        <div class="form-grid">
          <div class="field"><label for="inp-f-waf" title="非高峰最高 10；高峰期 22:00–01:00 与 24h WAF 恢复期自动压回 ≤6">10m WAF 令牌 (1–10)</label><input id="inp-f-waf" name="flowMaxRecaptcha10m" type="number" min="1" max="10" value="${Number(s.flowMaxRecaptcha10m ?? 8)}"></div>
          <div class="field"><label for="inp-f-hr" title="0 表示关闭人为小时硬限，由 10m WAF 墙与 24h 预算统一管控">1h 视频上限 (0=关闭)</label><input id="inp-f-hr" name="flowMaxPerHour" type="number" min="0" max="200" value="${Number(s.flowMaxPerHour ?? 0)}"></div>
          <div class="field"><label for="inp-f-day">24h 视频软预算 (次)</label><input id="inp-f-day" name="flowMaxPerDay" type="number" min="1" max="1000" value="${Number(s.flowMaxPerDay ?? 36)}"></div>
          <div class="field"><label for="inp-f-dcr">24h 视频积分预算</label><input id="inp-f-dcr" name="flowMaxDailyCredits" type="number" min="10" max="50000" value="${Number(s.flowMaxDailyCredits ?? 600)}"></div>
          <div class="field"><label for="inp-f-min">账号留存底线 (积分)</label><input id="inp-f-min" name="flowMinCredits" type="number" min="0" max="10000" value="${Number(s.flowMinCredits ?? 100)}"></div>
          <div class="field"><label for="inp-f-gap">最小调用间隔 (秒)</label><input id="inp-f-gap" name="flowMinGapSec" type="number" min="0" max="600" value="${Number(s.flowMinGapSec ?? 35)}"></div>
        </div>
        <div class="form-actions">
          <button class="primary" type="submit">保存阈值</button>
          <button type="button" id="btn-flow-reset-default">恢复默认</button>
          ${state.flowSettingsToast ? `<span class="toast ${state.flowSettingsToast.err ? 'err' : ''}">${esc(state.flowSettingsToast.msg)}</span>` : ''}
        </div>
      </form>
    </section>`);

    return parts.join('');
  }

  Object.assign(QC, {
    flowStatusMeta,
    renderFlowOverviewCard,
    renderFlowSchedule,
    renderFlow,
  });
})(window.__QC__ = window.__QC__ || {});
