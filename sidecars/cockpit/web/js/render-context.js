(function (QC) {
  'use strict';

  const {
    CAT_COLOR, CAT_LABEL, CTX_GROUPS, esc, fmtTok, fmtClock, elapsedSpan, gridCells, splitPath,
  } = QC;

  function fmtStepInt(n) {
    return Math.max(0, Math.round(Number(n) || 0)).toLocaleString('en-US');
  }

  function cacheStatusMeta(cache) {
    if (!cache || !cache.has_data || !cache.latest) {
      return { pill: '', text: '暂无数据', color: 'var(--dim)' };
    }
    if (cache.compacted_pending) {
      return { pill: 'pool-a', text: '压缩待首轮', color: 'var(--pool-a)' };
    }
    const lat = cache.latest;
    if (lat.status === 'high') return { pill: 'ok', text: '高命中', color: 'var(--ok)' };
    if (lat.status === 'mid') return { pill: 'warn', text: '中命中', color: 'var(--warn)' };
    if (lat.status === 'low') return { pill: 'warn', text: '新增较多', color: 'var(--warn)' };
    if (lat.status === 'miss') return { pill: 'bad', text: '失效重建', color: 'var(--bad)' };
    if (lat.status === 'cold') {
      return { pill: 'pool-a', text: lat.after_compact ? '压缩首轮' : '冷启动', color: 'var(--pool-a)' };
    }
    return { pill: '', text: '正常', color: 'var(--dim)' };
  }

  function cacheTurnStatusLabel(t) {
    if (!t) return '';
    if (t.status === 'high') return '高命中';
    if (t.status === 'mid') return '中命中';
    if (t.status === 'low') return '新增较多（旧前缀保留）';
    if (t.status === 'miss') return '缓存失效重建';
    if (t.status === 'cold') return t.after_compact ? '压缩后首轮（冷启）' : '会话首轮（冷启）';
    return t.status || '';
  }

  function renderCacheOverviewStrip(cache) {
    if (!cache || !cache.has_data || !cache.latest) return '';
    const lat = cache.latest;
    const seg = cache.segment || {};
    const ses = cache.session || {};
    const cm = cacheStatusMeta(cache);
    const barPct = Math.max(0, Math.min(100, Number(lat.hit_rate_pct || 0)));
    const barColor = lat.status === 'miss' ? 'var(--bad)' : (lat.status === 'cold' ? 'var(--pool-a)' : cm.color);
    const scopeTxt = cache.has_compaction && seg.turns > 0
      ? `本段 ${Number(seg.hit_rate_pct || 0).toFixed(1)}% · 整场 ${Number(ses.hit_rate_pct || 0).toFixed(1)}%`
      : `累计 ${Number(ses.hit_rate_pct || 0).toFixed(1)}% (${ses.turns || 0}轮${ses.misses > 0 ? ` · ${ses.misses}失效` : ''})`;
    const elHtml = lat.ts > 0 ? elapsedSpan(lat.ts, !cache.compacted_pending) : '';
    return `<div class="cache-strip" data-jump="context" title="点击查看 Prompt 缓存逐轮命中轨迹">
      <div class="meta-row" style="margin-top:0">
        <span style="color:var(--fg);font-weight:600;display:inline-flex;align-items:center;gap:5px">
          <span>Prompt 缓存</span>
          <span class="pill ${cm.pill} num" style="font-size:10px;padding:1px 5px">${esc(cm.text)} ${Number(lat.hit_rate_pct || 0).toFixed(1)}%</span>
        </span>
        <span class="num">读 <strong style="color:var(--fg)">${fmtTok(lat.cache_read_tokens)}</strong> · 新 ${fmtTok(lat.uncached_input_tokens)}</span>
      </div>
      <div class="cache-bar"><div class="cache-bar-fill" style="width:${barPct}%;background:${barColor}"></div></div>
      <div class="meta-row">
        <span class="num">${esc(scopeTxt)}</span>
        <span>${elHtml}</span>
      </div>
    </div>`;
  }

  function renderCacheCard(cache) {
    if (!cache || !cache.has_data || !cache.latest) return '';
    const lat = cache.latest;
    const seg = cache.segment || {};
    const ses = cache.session || {};
    const recent = cache.recent || [];
    const cm = cacheStatusMeta(cache);
    const showSeg = Boolean(cache.has_compaction && seg.turns > 0);
    const elHtml = lat.ts > 0 ? elapsedSpan(lat.ts, !cache.compacted_pending) : '';
    const prefTxt = lat.prefix_retention_pct !== null && lat.prefix_retention_pct !== undefined
      ? ` · 前缀复用 ${Number(lat.prefix_retention_pct).toFixed(1)}%`
      : '';
    const totalTurns = Number(ses.turns || recent.length);
    const startTurnNo = Math.max(1, totalTurns - recent.length + 1);

    const barsHtml = recent.map((t, i) => {
      const turnNo = startTurnNo + i;
      const pctVal = Number(t.hit_rate_pct || 0);
      const minH = (t.status === 'miss' || t.status === 'cold') ? 22 : 12;
      const h = Math.max(minH, Math.min(100, Math.round(pctVal)));
      const pfx = t.prefix_retention_pct !== null && t.prefix_retention_pct !== undefined
        ? ` · 上轮前缀复用 ${Number(t.prefix_retention_pct).toFixed(1)}%`
        : '';
      const timeTxt = t.ts > 0 ? ` · ${fmtClock(t.ts)}` : '';
      const tip = `第 ${turnNo} 轮 · ${cacheTurnStatusLabel(t)} · 命中 ${pctVal.toFixed(1)}%（读 ${fmtTok(t.cache_read_tokens)} / 新 ${fmtTok(t.uncached_input_tokens)}）${pfx}${timeTxt}`;
      const cls = `cache-col${t.after_compact && i > 0 ? ' after-compact' : ''}`;
      return `<div class="${cls}" title="${esc(tip)}"><div class="cache-seg ${esc(t.status || 'high')}" style="height:${h}%"></div></div>`;
    }).join('');

    const rightBoxTitle = showSeg ? `本段压缩后 (${seg.turns}轮)` : `整场累计 (${ses.turns}轮)`;
    const rightBoxPct = showSeg ? Number(seg.hit_rate_pct || 0) : Number(ses.hit_rate_pct || 0);
    const rightBoxRead = showSeg ? seg.cache_read_tokens : ses.cache_read_tokens;
    const rightBoxMiss = showSeg ? (seg.misses || 0) : (ses.misses || 0);

    return `<section class="card">
      <div class="ch">
        <span class="ctitle">Prompt 缓存命中 <span class="pill ${cm.pill}">${esc(cm.text)}</span></span>
        <span class="sub">${elHtml || `${ses.turns} 轮记录`}</span>
      </div>
      <div class="roll-head" style="margin-bottom:6px">
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">最新一轮命中率</div>
          <div class="roll-kpi-val num" style="color:${cm.color}">${Number(lat.hit_rate_pct || 0).toFixed(1)}%</div>
          <div class="roll-kpi-sub num">读 ${fmtTok(lat.cache_read_tokens)} / 新 ${fmtTok(lat.uncached_input_tokens)}${esc(prefTxt)}</div>
        </div>
        <div class="roll-kpi">
          <div class="roll-kpi-lbl">${esc(rightBoxTitle)}</div>
          <div class="roll-kpi-val num">${rightBoxPct.toFixed(1)}%</div>
          <div class="roll-kpi-sub num">读 ${fmtTok(rightBoxRead)} · ${rightBoxMiss > 0 ? `<span style="color:var(--bad)">${rightBoxMiss}次失效</span>` : '0次失效'}</div>
        </div>
      </div>
      ${showSeg ? `<div class="chips" style="margin-top:0;margin-bottom:4px">
        <span class="chip num">整场累计 <strong>${Number(ses.hit_rate_pct || 0).toFixed(1)}%</strong> (${ses.turns}轮)</span>
        <span class="chip num">累计缓存读 <strong>${fmtTok(ses.cache_read_tokens)}</strong></span>
        <span class="chip num">失效重建 <strong>${ses.misses || 0}次</strong></span>
      </div>` : ''}
      ${cache.compacted_pending ? `<div class="callout info" style="margin-top:6px">已执行 <span class="mono">/compact</span>，下条消息将基于压缩摘要重新冷启写入缓存。</div>` : ''}
      ${!cache.compacted_pending && lat.status === 'miss' ? `<div class="callout bad" style="margin-top:6px">上一轮发生缓存失效重建（新增写入 ${fmtTok(lat.uncached_input_tokens)} tok${lat.prefix_retention_pct !== null && lat.prefix_retention_pct !== undefined ? `，前缀仅保留 ${Number(lat.prefix_retention_pct).toFixed(1)}%` : ''}），继续对话下一轮起将恢复高命中。</div>` : ''}
      ${recent.length ? `
        <div class="cache-spark">${barsHtml}</div>
        <div class="cache-legend">
          <span class="cache-legend-items">
            <span><i class="cache-dot" style="background:var(--ok)"></i>≥80%</span>
            <span><i class="cache-dot" style="background:var(--warn)"></i>中/新增多</span>
            <span><i class="cache-dot" style="background:var(--bad)"></i>失效</span>
            <span><i class="cache-dot" style="background:var(--pool-a)"></i>冷启</span>
          </span>
          <span class="num">近 ${recent.length} 轮轨迹（悬停看明细）</span>
        </div>` : ''}
      <div class="sub" style="margin-top:6px;line-height:1.4">取自对话库原生 <span class="mono">model_usage</span>（缓存读 / 新增输入）；独立生图调用不走对话前缀缓存。</div>
    </section>`;
  }

  function renderStepsOverviewStrip(steps) {
    if (!steps || !steps.has_data) return '';
    const ses = steps.session || {};
    const seg = steps.segment || {};
    const total = Number(steps.total ?? ses.total ?? 0);
    const hasCompact = Boolean(steps.has_compaction);
    const compactions = Number(steps.compactions || 0);
    const segTotal = Number(seg.total || 0);
    const tip = hasCompact
      ? `本段（压缩后）${fmtStepInt(segTotal)} 步（用户 ${fmtStepInt(seg.user)} · 模型 ${fmtStepInt(seg.agent)} · 工具 ${fmtStepInt(seg.tool)}）；整场 ${fmtStepInt(total)} 步（用户 ${fmtStepInt(ses.user)} · 模型 ${fmtStepInt(ses.agent)} · 工具 ${fmtStepInt(ses.tool)} · 系统 ${fmtStepInt(ses.system)} · 压缩 ${compactions} 次）`
      : `当前对话 ${fmtStepInt(total)} 步（用户 ${fmtStepInt(ses.user)} · 模型 ${fmtStepInt(ses.agent)} · 工具 ${fmtStepInt(ses.tool)} · 系统 ${fmtStepInt(ses.system)}）`;
    const pillHtml = hasCompact
      ? `<span class="pill pool-a num" style="font-size:10px;padding:1px 5px">本段 ${fmtStepInt(segTotal)} 步</span>`
      : `<span class="pill num" style="font-size:10px;padding:1px 5px">${fmtStepInt(total)} 步</span>`;
    const rightTopHtml = hasCompact
      ? `总计 <strong style="color:var(--fg)">${fmtStepInt(total)}</strong> 步`
      : `模型 <strong style="color:var(--fg)">${fmtStepInt(ses.agent)}</strong> · 工具 ${fmtStepInt(ses.tool)} · 用户 ${fmtStepInt(ses.user)}`;
    const secondRowHtml = hasCompact
      ? `<div class="meta-row">
          <span class="num">本段 模型 ${fmtStepInt(seg.agent)} · 工具 ${fmtStepInt(seg.tool)} · 用户 ${fmtStepInt(seg.user)}</span>
          <span class="num">整场 模型 ${fmtStepInt(ses.agent)} · 压缩 ${compactions}次</span>
        </div>`
      : '';
    return `<div class="steps-strip" data-jump="context" title="${esc(tip)}">
      <div class="meta-row" style="margin-top:0">
        <span style="color:var(--fg);font-weight:600;display:inline-flex;align-items:center;gap:5px">
          <span>对话步数</span>
          ${pillHtml}
        </span>
        <span class="num">${rightTopHtml}</span>
      </div>
      ${secondRowHtml}
    </div>`;
  }

  function renderStepsCard(steps) {
    if (!steps || !steps.has_data) return '';
    const ses = steps.session || {};
    const seg = steps.segment || {};
    const total = Number(steps.total ?? ses.total ?? 0);
    const hasCompact = Boolean(steps.has_compaction);
    const compactions = Number(steps.compactions || 0);
    const segTotal = Number(seg.total || 0);
    const kpiHtml = hasCompact
      ? `<div class="roll-head" style="margin-bottom:6px">
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">本段步数（压缩后）</div>
            <div class="roll-kpi-val num" style="color:var(--pool-a)">${fmtStepInt(segTotal)} 步</div>
            <div class="roll-kpi-sub num">模型 ${fmtStepInt(seg.agent)} · 工具 ${fmtStepInt(seg.tool)} · 用户 ${fmtStepInt(seg.user)}</div>
          </div>
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">整场总步数</div>
            <div class="roll-kpi-val num">${fmtStepInt(total)} 步</div>
            <div class="roll-kpi-sub num">模型 ${fmtStepInt(ses.agent)} · 工具 ${fmtStepInt(ses.tool)} · 用户 ${fmtStepInt(ses.user)}</div>
          </div>
        </div>`
      : `<div class="roll-head" style="margin-bottom:6px">
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">当前对话步数</div>
            <div class="roll-kpi-val num">${fmtStepInt(total)} 步</div>
            <div class="roll-kpi-sub num">用户 ${fmtStepInt(ses.user)} · 模型 ${fmtStepInt(ses.agent)} · 工具 ${fmtStepInt(ses.tool)}</div>
          </div>
          <div class="roll-kpi">
            <div class="roll-kpi-lbl">模型 / 工具步数</div>
            <div class="roll-kpi-val num">${fmtStepInt(ses.agent)} / ${fmtStepInt(ses.tool)}</div>
            <div class="roll-kpi-sub num">系统 ${fmtStepInt(ses.system)} · 检查点 ${fmtStepInt(ses.checkpoint)}</div>
          </div>
        </div>`;
    return `<section class="card">
      <div class="ch">
        <span class="ctitle">对话步数 <span class="pill ${hasCompact ? 'pool-a' : ''}">${hasCompact ? `已压缩 ${compactions} 次` : '未压缩'}</span></span>
        <span class="sub num">共 ${fmtStepInt(total)} 步</span>
      </div>
      ${kpiHtml}
      <div class="chips" style="margin-top:0">
        <span class="chip num">用户 <strong>${fmtStepInt(ses.user)}</strong></span>
        <span class="chip num">模型 <strong>${fmtStepInt(ses.agent)}</strong></span>
        <span class="chip num">工具 <strong>${fmtStepInt(ses.tool)}</strong></span>
        ${ses.system > 0 ? `<span class="chip num">系统 <strong>${fmtStepInt(ses.system)}</strong></span>` : ''}
        ${compactions > 0 ? `<span class="chip num">压缩 <strong>${compactions}次</strong></span>` : ''}
      </div>
      <div class="sub" style="margin-top:6px;line-height:1.4">取自对话库原生 <span class="mono">steps</span> 表；本段步数自最近一次已完成压缩（<span class="mono">/compact</span>）起计。</div>
    </section>`;
  }

  function renderFileList(title, hint, items) {
    if (!items || !items.length) return '';
    return `<section class="card">
      <div class="h2"><span>${esc(title)}</span><span class="hint">${esc(hint)}</span></div>
      <ul class="list files">${items.map((it) => {
        const [name, dir] = splitPath(it.path || '');
        const tok = Number(it.tokens) > 0 ? `<span class="tok num">${fmtTok(it.tokens)}</span>` : '<span></span>';
        return `<li><div class="name" title="${esc(it.path || '')}">${esc(name)}${dir ? `<span class="dir">${esc(dir)}</span>` : ''}</div>${tok}</li>`;
      }).join('')}</ul>
    </section>`;
  }

  function renderContext(d) {
    const ctx = d.context;
    if (!ctx || ctx.error) {
      const msg = ctx?.error === 'no-conversation'
        ? '在具体对话中打开本面板，即可查看实时上下文占用明细。'
        : ctx?.error === 'no-database'
          ? '新对话尚无落盘数据，发送首条消息后自动更新。'
          : (ctx?.message || '读取上下文报告失败。');
      return `<p class="empty">${esc(msg)}</p>`;
    }

    const parts = [];
    const budget = Number(ctx.request_budget_tokens) || 0;
    const nearBudget = budget > 0 && ctx.used_tokens >= budget * 0.8;
    const cells = gridCells(ctx.grid_segments, ctx.limit_tokens);

    const byKey = new Map((ctx.categories || []).map((c) => [c.key, c]));
    const groupsHtml = CTX_GROUPS.map((g) => {
      const items = g.keys.map((k) => byKey.get(k)).filter(Boolean);
      if (!items.length) return '';
      return `<div class="group">${items.map((c) => `<div class="crow">
        <span class="mark${g.sq ? ' sq' : ''}" style="background:${CAT_COLOR[c.key] || 'var(--dim)'}"></span>
        <span>${esc(CAT_LABEL[c.key] || c.name)}</span>
        <span class="tok num">${fmtTok(c.tokens)}</span>
        <span class="p num">${Number(c.pct || 0).toFixed(1)}%</span>
      </div>`).join('')}</div>`;
    }).join('');

    const freePct = ctx.limit_tokens ? ((ctx.free_tokens / ctx.limit_tokens) * 100).toFixed(1) : '0.0';
    parts.push(`<section class="card">
      <div class="ch">
        <span class="ctitle">${esc(ctx.model || '当前上下文')}</span>
        <button id="btn-copy-ctx" type="button">复制文本</button>
      </div>
      <div class="bigrow">
        <div class="big num" style="font-size:18px">${fmtTok(ctx.used_tokens)} <span class="dim" style="font-size:12.5px;font-weight:500">/ ${ctx.limit_tokens ? fmtTok(ctx.limit_tokens) : '?'} tokens</span></div>
        <span class="pill ${nearBudget ? 'warn' : ''}">${ctx.limit_tokens ? `${ctx.used_pct.toFixed(1)}%` : '--'}</span>
      </div>
      ${nearBudget ? `<div class="callout warn" style="margin-bottom:6px">接近单次请求预算（${fmtTok(budget)} tokens），建议及时执行 <span class="mono">/compact</span> 瘦身。</div>` : ''}
      <div class="grid">${cells.map((col) => `<span class="dot"${col ? ` style="background:${col}"` : ''}></span>`).join('')}</div>
      <div class="sec-sep">
        <div class="h2"><span>${esc(ctx.heading || '分类占用明细')}</span></div>
        ${groupsHtml}
        <div class="group">
          <div class="crow">
            <span class="mark free"></span>
            <span class="dim">剩余可用空间</span>
            <span class="tok num dim">${fmtTok(ctx.free_tokens)}</span>
            <span class="p num">${freePct}%</span>
          </div>
          ${ctx.checkpoint_buffer > 0 ? `<div class="crow">
            <span class="mark sq" style="background:var(--empty)"></span>
            <span class="dim">检查点缓冲（不计入占用）</span>
            <span class="tok num dim">${fmtTok(ctx.checkpoint_buffer)}</span>
            <span class="p"></span>
          </div>` : ''}
        </div>
      </div>
    </section>`);

    if (ctx.cache && ctx.cache.has_data) {
      parts.push(renderCacheCard(ctx.cache));
    }

    if (ctx.steps && ctx.steps.has_data) {
      parts.push(renderStepsCard(ctx.steps));
    }

    if (ctx.checkpoint_view) {
      const ck = ctx.checkpoint_view;
      parts.push(`<section class="card">
        <div class="h2"><span>检查点 (${ck.count})</span><span class="hint mono">/rewind</span></div>
        <div style="font-size:12px">Checkpoint ${ck.active_number}（活跃）：<span class="dim">${esc(ck.active_text || '')}</span></div>
        ${ck.historical > 0 ? `<div class="sub" style="margin-top:4px">另有 ${ck.historical} 个历史检查点（已归档，不占上下文）</div>` : ''}
      </section>`);
    }

    parts.push(renderFileList('知识库文件 (Knowledge)', '/rules', ctx.knowledge_files));
    parts.push(renderFileList('产物文件 (Artifacts)', '/artifact', ctx.artifact_files));
    parts.push(renderFileList('技能文件 (Skills)', '/skill', ctx.skill_files));
    parts.push(renderFileList('系统文件 (System)', '自动加载', ctx.system_files));

    parts.push(`<footer>
      <span class="mono">/compact · /artifact · /skill · /rewind</span>
      <span class="num">${ctx.updatedAt ? `更新 ${fmtClock(Date.parse(ctx.updatedAt) / 1000)}` : ''}</span>
    </footer>`);

    return parts.join('');
  }

  Object.assign(QC, {
    cacheStatusMeta,
    cacheTurnStatusLabel,
    renderCacheOverviewStrip,
    renderCacheCard,
    renderStepsOverviewStrip,
    renderStepsCard,
    renderFileList,
    renderContext,
  });
})(window.__QC__ = window.__QC__ || {});
