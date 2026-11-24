// Pure model-pool & dual-window (5h + weekly) quota aggregation helpers.
// Combines `GetAvailableModels` (model catalog, effort families, provider pools)
// with `RetrieveUserQuotaSummary` (authoritative 5-hour + weekly buckets).

export const ROLE_RULES = [
  { re: /^gemini-4-neon/i, tier: 'T0', order: 10, role: '旗舰主力 · 视觉/多模态推理' },
  { re: /^claude-opus-5/i, tier: 'T0', order: 11, role: '旗舰主力 · 深度推理/编码' },
  { re: /^claude-opus-4/i, tier: 'T0', order: 12, role: 'T0 跨池 · 深度推理' },
  { re: /^gemini-4-carbon/i, tier: 'T0', order: 15, role: 'T0 旗舰 · 综合任务' },
  { re: /^claude-sonnet-5/i, tier: 'T1', order: 20, role: '主力模型 · 日常开发/精修' },
  { re: /^claude-sonnet-4/i, tier: 'T1', order: 21, role: 'T1 跨池 · 日常主力' },
  { re: /^gemini-4-hydrogen/i, tier: 'T1', order: 22, role: '主力模型 · 多模态/均衡' },
  { re: /^gemini-3(\.\d+)?-pro/i, tier: 'T1', order: 25, role: '主力模型 · 综合推理' },
  { re: /^gemini-4-argon/i, tier: 'T2', order: 30, role: '轻量主力 · 通用执行' },
  { re: /^gemini-pro-orchestrator/i, tier: 'T2', order: 35, role: '编排模型' },
  { re: /^gemini-3(\.\d+)?-flash/i, tier: 'T3', order: 40, role: '极速轻量 · 快速检索/批量任务' },
  { re: /^gpt-oss-/i, tier: 'T4', order: 50, role: '纯文本兜底 · 无视觉' },
  { re: /image/i, tier: 'IMG', order: 90, role: '生图专用 · 后端独立 5h 桶' },
];

export function classifyModelRole(id) {
  for (const rule of ROLE_RULES) {
    if (rule.re.test(id)) return { tier: rule.tier, order: rule.order, role: rule.role };
  }
  return { tier: '', order: 70, role: '' };
}

export function poolMetaForProvider(provider) {
  if (provider === 'MODEL_PROVIDER_GOOGLE') {
    return {
      key: 'pool_a',
      label: '池 A · Google Gemini',
      shortLabel: '池 A',
      accent: 'pool-a',
      description: 'Gemini 4 系列 / 3.8 Flash 共用对话算力池',
      order: 1,
    };
  }
  if (provider === 'MODEL_PROVIDER_ANTHROPIC' || provider === 'MODEL_PROVIDER_OPENAI') {
    return {
      key: 'pool_b',
      label: '池 B · Claude / 第三方',
      shortLabel: '池 B',
      accent: 'pool-b',
      description: 'Claude 5.5 系列 / GPT-OSS 共用独立算力池',
      order: 2,
    };
  }
  const slug = String(provider || 'other').replace(/^MODEL_PROVIDER_/i, '').toLowerCase();
  return {
    key: `pool_${slug}`,
    label: `其他池 · ${slug.toUpperCase()}`,
    shortLabel: slug.toUpperCase(),
    accent: 'pool-c',
    description: '扩展模型额度池',
    order: 3,
  };
}

export function baseFamilyName(id, displayName) {
  const raw = (displayName || id || '').trim();
  return raw.replace(/\s*\((Minimal|Low|Medium|High|XHigh|Max|Thinking)\)\s*$/i, '').trim() || raw;
}

export function pctToHealth(pct) {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return 'ok';
  if (pct <= 5) return 'critical';
  if (pct <= 20) return 'warning';
  return 'ok';
}

function clampFraction(v) {
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  return Math.max(0, Math.min(1, v));
}

function parseIsoSec(tsStr) {
  if (typeof tsStr !== 'string' || !tsStr.trim()) return 0;
  const ms = Date.parse(tsStr);
  return Number.isFinite(ms) && ms > 0 ? Math.round(ms / 1000) : 0;
}

function normalizeWindowKind(rawWindow, bucketId, displayName, resetTs, nowSec) {
  const w = String(rawWindow || '').trim().toLowerCase();
  const id = String(bucketId || '').trim().toLowerCase();
  const dn = String(displayName || '').trim().toLowerCase();
  if (w === '5h' || w === 'five_hour' || /(^|[-_])5h($|[-_])/.test(id) || /five\s*hour|5[\s-]*hour/.test(dn)) {
    return '5h';
  }
  if (w === 'weekly' || w === 'week' || w === '7d' || /week|7d/.test(id) || /weekly/.test(dn)) {
    return 'weekly';
  }
  if (resetTs > nowSec) {
    return (resetTs - nowSec) > 36 * 3600 ? 'weekly' : '5h';
  }
  return w || 'other';
}

function windowMeta(kind, fallbackLabel) {
  if (kind === '5h') return { label: '5 小时额度', shortLabel: '5h', order: 1 };
  if (kind === 'weekly') return { label: '周额度', shortLabel: '周', order: 2 };
  return { label: fallbackLabel || kind || '周期额度', shortLabel: kind || '其他', order: 3 };
}

function makeWindowEntry({
  bucketId = '',
  kind,
  displayName = '',
  remainingFraction,
  resetTime = '',
  description = '',
  source = 'summary',
  nowSec,
}) {
  const frac = clampFraction(remainingFraction);
  const resetTs = parseIsoSec(resetTime);
  const normKind = normalizeWindowKind(kind, bucketId, displayName, resetTs, nowSec);
  const meta = windowMeta(normKind, displayName);
  const remainingPct = frac !== null ? Number((frac * 100).toFixed(2)) : null;
  const usedPct = remainingPct !== null ? Number((100 - remainingPct).toFixed(2)) : null;
  const isFull = frac !== null && frac >= 0.9999;

  return {
    bucketId: String(bucketId || ''),
    window: normKind,
    label: meta.label,
    shortLabel: meta.shortLabel,
    order: meta.order,
    remainingFraction: frac,
    remainingPct,
    usedPct,
    resetTime: typeof resetTime === 'string' ? resetTime : '',
    resetTs,
    resetRemainingSec: resetTs > nowSec ? resetTs - nowSec : 0,
    isFull,
    description: String(description || ''),
    health: pctToHealth(remainingPct),
    source,
  };
}

function poolKeyForSummaryGroup(group) {
  const displayName = String(group?.displayName || '');
  const desc = String(group?.description || '');
  const buckets = Array.isArray(group?.buckets) ? group.buckets : [];
  const ids = buckets.map((b) => String(b?.bucketId || '').toLowerCase());

  if (ids.some((id) => id.startsWith('gemini')) || /\bgemini\b/i.test(displayName)) {
    return {
      key: 'pool_a',
      meta: poolMetaForProvider('MODEL_PROVIDER_GOOGLE'),
    };
  }
  if (
    ids.some((id) => /^(3p|claude|anthropic|openai|gpt)[-_]?/.test(id))
    || /\b(claude|gpt|anthropic|openai|third[\s-]*party|3p)\b/i.test(`${displayName} ${desc}`)
  ) {
    return {
      key: 'pool_b',
      meta: poolMetaForProvider('MODEL_PROVIDER_ANTHROPIC'),
    };
  }
  const slug = displayName.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'other';
  return {
    key: `pool_${slug}`,
    meta: {
      key: `pool_${slug}`,
      label: displayName || `其他池 · ${slug.toUpperCase()}`,
      shortLabel: displayName.slice(0, 10) || slug.toUpperCase(),
      accent: 'pool-c',
      description: desc || '扩展模型额度池',
      order: 3,
    },
  };
}

export function parseQuotaSummary(summaryResp, nowSec = Math.floor(Date.now() / 1000)) {
  const root = summaryResp?.response || summaryResp;
  const groups = Array.isArray(root?.groups) ? root.groups : [];
  const byPoolKey = new Map();

  for (const g of groups) {
    if (!g || typeof g !== 'object') continue;
    const { key, meta } = poolKeyForSummaryGroup(g);
    const rawBuckets = Array.isArray(g.buckets) ? g.buckets : [];
    const windows = rawBuckets
      .filter((b) => b && typeof b === 'object')
      .map((b) => makeWindowEntry({
        bucketId: b.bucketId,
        kind: b.window,
        displayName: b.displayName,
        remainingFraction: b.remainingFraction,
        resetTime: b.resetTime,
        description: b.description,
        source: 'summary',
        nowSec,
      }))
      .sort((a, b) => a.order - b.order);

    byPoolKey.set(key, {
      key,
      meta,
      displayName: String(g.displayName || ''),
      description: String(g.description || ''),
      windows,
    });
  }

  return {
    available: byPoolKey.size > 0,
    description: typeof root?.description === 'string' ? root.description : '',
    byPoolKey,
  };
}

export function buildPoolsPayload(modelsObj, summaryResp = null, nowMs = Date.now()) {
  const nowSec = Math.floor(nowMs / 1000);
  const summary = parseQuotaSummary(summaryResp, nowSec);
  const catalog = {};
  const modelToPool = {};
  const poolBuckets = new Map();
  let imageModelEntry = null;

  for (const [id, m] of Object.entries(modelsObj || {})) {
    if (!m || typeof m !== 'object') continue;
    const maxTokens = Number(m.maxTokens) || 0;
    const displayName = String(m.displayName || id);
    if (maxTokens > 0) {
      catalog[id] = { maxTokens, displayName };
    }

    // Skip internal autocomplete/tab models
    if (id.startsWith('tab_') || /^tab\b/i.test(displayName)) continue;

    const qi = m.quotaInfo && typeof m.quotaInfo === 'object' ? m.quotaInfo : null;
    if (!qi) continue;

    const provider = String(m.modelProvider || 'MODEL_PROVIDER_UNKNOWN');
    const meta = poolMetaForProvider(provider);
    modelToPool[id] = meta.key;

    const frac = clampFraction(qi.remainingFraction);
    const resetTime = typeof qi.resetTime === 'string' ? qi.resetTime : '';
    const resetTs = parseIsoSec(resetTime);
    const isImage = /image/i.test(id);
    const roleInfo = classifyModelRole(id);

    if (isImage) {
      imageModelEntry = {
        id,
        displayName,
        reportedPoolKey: meta.key,
        reportedRemainingPct: frac !== null ? Number((frac * 100).toFixed(2)) : null,
        reportedResetTime: resetTime,
      };
    }

    if (!poolBuckets.has(meta.key)) {
      poolBuckets.set(meta.key, {
        ...meta,
        providers: new Set(),
        rawModels: [],
      });
    }
    const bucket = poolBuckets.get(meta.key);
    bucket.providers.add(provider.replace(/^MODEL_PROVIDER_/, ''));
    bucket.rawModels.push({
      id,
      displayName,
      family: baseFamilyName(id, displayName),
      provider,
      maxTokens,
      remainingFraction: frac,
      remainingPct: frac !== null ? Number((frac * 100).toFixed(2)) : null,
      resetTime,
      resetTs,
      isImage,
      tier: roleInfo.tier,
      role: roleInfo.role,
      order: roleInfo.order,
    });
  }

  // Ensure any quota-summary-only pool group is still surfaced
  for (const [key, sg] of summary.byPoolKey.entries()) {
    if (!poolBuckets.has(key)) {
      poolBuckets.set(key, {
        ...sg.meta,
        providers: new Set(),
        rawModels: [],
      });
    }
  }

  const pools = [];

  for (const bucket of [...poolBuckets.values()].sort((a, b) => a.order - b.order)) {
    const nonImg = bucket.rawModels.filter((m) => !m.isImage && m.remainingFraction !== null);
    const sample = nonImg.length ? nonImg : bucket.rawModels.filter((m) => m.remainingFraction !== null);

    let modelMinFrac = null;
    let modelMaxFrac = null;
    let modelResetTime = '';
    let modelResetTs = 0;

    for (const m of sample) {
      if (modelMinFrac === null || m.remainingFraction < modelMinFrac) modelMinFrac = m.remainingFraction;
      if (modelMaxFrac === null || m.remainingFraction > modelMaxFrac) modelMaxFrac = m.remainingFraction;
      if (m.resetTs > 0 && (modelResetTs === 0 || m.resetTs < modelResetTs)) {
        modelResetTs = m.resetTs;
        modelResetTime = m.resetTime;
      }
    }

    // Group effort variants by family name for compact, readable UI display
    const familyMap = new Map();
    for (const m of bucket.rawModels) {
      const fkey = m.family;
      if (!familyMap.has(fkey)) {
        familyMap.set(fkey, {
          id: m.id,
          family: m.family,
          variantIds: [m.id],
          variantsCount: 1,
          tier: m.tier,
          role: m.role,
          order: m.order,
          isImage: m.isImage,
          maxTokens: m.maxTokens,
          remainingPct: m.remainingPct,
          resetTime: m.resetTime,
          resetTs: m.resetTs,
        });
      } else {
        const cur = familyMap.get(fkey);
        cur.variantIds.push(m.id);
        cur.variantsCount += 1;
        cur.maxTokens = Math.max(cur.maxTokens, m.maxTokens);
        if (m.remainingPct !== null && (cur.remainingPct === null || m.remainingPct < cur.remainingPct)) {
          cur.remainingPct = m.remainingPct;
        }
      }
    }

    const families = [...familyMap.values()].sort((a, b) => (a.order - b.order) || a.family.localeCompare(b.family));

    // Merge authoritative dual windows from RetrieveUserQuotaSummary; fallback to model quotaInfo
    const summaryGroup = summary.byPoolKey.get(bucket.key);
    let windows = summaryGroup && summaryGroup.windows.length ? summaryGroup.windows : [];
    if (!windows.length && modelMinFrac !== null) {
      windows = [
        makeWindowEntry({
          bucketId: `${bucket.key}-inferred`,
          kind: '',
          remainingFraction: modelMinFrac,
          resetTime: modelResetTime,
          source: 'models_fallback',
          nowSec,
        }),
      ];
    }

    const fiveHour = windows.find((w) => w.window === '5h') || null;
    const weekly = windows.find((w) => w.window === 'weekly') || null;

    // Effective pool availability is governed by the tightest (minimum) active window
    let bottleneck = null;
    for (const w of windows) {
      if (w.remainingFraction === null) continue;
      if (
        !bottleneck
        || w.remainingFraction < bottleneck.remainingFraction
        || (w.remainingFraction === bottleneck.remainingFraction && w.order < bottleneck.order)
      ) {
        bottleneck = w;
      }
    }

    const effectiveFrac = bottleneck ? bottleneck.remainingFraction : modelMinFrac;
    const remainingPct = effectiveFrac !== null ? Number((effectiveFrac * 100).toFixed(2)) : null;
    const resetTime = bottleneck ? bottleneck.resetTime : modelResetTime;
    const resetTs = bottleneck ? bottleneck.resetTs : modelResetTs;
    const health = pctToHealth(remainingPct);

    pools.push({
      key: bucket.key,
      label: bucket.label,
      shortLabel: bucket.shortLabel,
      accent: bucket.accent,
      description: bucket.description,
      providers: [...bucket.providers],
      remainingFraction: effectiveFrac,
      remainingPct,
      usedPct: remainingPct !== null ? Number((100 - remainingPct).toFixed(2)) : null,
      bottleneckWindow: bottleneck ? bottleneck.window : '',
      bottleneckLabel: bottleneck ? bottleneck.label : '',
      windows,
      fiveHour,
      weekly,
      hasDualWindows: Boolean(fiveHour && weekly),
      hasMixedQuotas: modelMinFrac !== null && modelMaxFrac !== null && (modelMaxFrac - modelMinFrac) > 0.015,
      resetTime,
      resetTs,
      resetRemainingSec: resetTs > nowSec ? resetTs - nowSec : 0,
      health,
      modelCount: bucket.rawModels.length,
      familyCount: families.length,
      models: families,
    });
  }

  return {
    catalog,
    payload: {
      fetchedAt: new Date(nowMs).toISOString(),
      quotaSummaryAvailable: summary.available,
      quotaSummaryDescription: summary.description,
      pools,
      modelToPool,
      imageModelEntry,
      imageModelPoolNote:
        '每个模型池同时受「5 小时额度」与「周额度」双重约束，任一耗尽即会限流。另外，服务端 GetAvailableModels 将生图模型（gemini-3.1-flash-image）归类显示在池 A 下，但实际调用 generate_image 会单独命中后端独立的 5 小时生图限额桶；请以「生图配额」模块的 429 实测倒计时与 5h 计数为准。',
    },
  };
}

function summarizePoolWindows(pool) {
  const parts = [];
  if (pool.fiveHour && pool.fiveHour.remainingPct !== null) {
    parts.push(`5h ${pool.fiveHour.remainingPct.toFixed(1)}%`);
  }
  if (pool.weekly && pool.weekly.remainingPct !== null) {
    parts.push(`周 ${pool.weekly.remainingPct.toFixed(1)}%`);
  }
  if (!parts.length && pool.remainingPct !== null) {
    parts.push(`${pool.remainingPct.toFixed(1)}%`);
  }
  return parts.join(' / ');
}

export function resolveActivePool(poolsPayload, modelId) {
  if (!poolsPayload || !Array.isArray(poolsPayload.pools) || !poolsPayload.pools.length) {
    return { activePoolKey: '', activePool: null, routingAdvice: null };
  }
  const slug = String(modelId || '').trim();
  let key = (poolsPayload.modelToPool && slug && poolsPayload.modelToPool[slug]) || '';
  if (!key && slug) {
    if (/^gemini-/i.test(slug)) key = 'pool_a';
    else if (/^(claude-|gpt-)/i.test(slug)) key = 'pool_b';
  }
  const activePool = poolsPayload.pools.find((p) => p.key === key) || null;

  let routingAdvice = null;
  if (activePool && activePool.remainingPct !== null && activePool.remainingPct < 20) {
    const bottleneckName = activePool.bottleneckLabel || '额度';
    const otherWin = activePool.bottleneckWindow === '5h'
      ? activePool.weekly
      : activePool.bottleneckWindow === 'weekly'
        ? activePool.fiveHour
        : null;
    const otherNote = otherWin && otherWin.remainingPct !== null
      ? `（${otherWin.label}余 ${otherWin.remainingPct.toFixed(1)}%）`
      : '';
    const alt = poolsPayload.pools
      .filter((p) => p.key !== activePool.key && p.remainingPct !== null)
      .sort((a, b) => b.remainingPct - a.remainingPct)[0];

    if (alt && alt.remainingPct >= 40) {
      routingAdvice = {
        level: activePool.remainingPct <= 5 ? 'critical' : 'warning',
        text: `当前 ${activePool.shortLabel} 的${bottleneckName}仅剩 ${activePool.remainingPct.toFixed(1)}%${otherNote}，建议切换至 ${alt.shortLabel}（${summarizePoolWindows(alt)}）继续作业`,
      };
    } else {
      routingAdvice = {
        level: activePool.remainingPct <= 5 ? 'critical' : 'warning',
        text: `当前 ${activePool.shortLabel} 的${bottleneckName}仅剩 ${activePool.remainingPct.toFixed(1)}%${otherNote}，请注意控制高耗时任务频率`,
      };
    }
  }

  return {
    activePoolKey: key,
    activePool: activePool ? {
      key: activePool.key,
      label: activePool.label,
      shortLabel: activePool.shortLabel,
      accent: activePool.accent,
      remainingPct: activePool.remainingPct,
      bottleneckWindow: activePool.bottleneckWindow,
      bottleneckLabel: activePool.bottleneckLabel,
      fiveHour: activePool.fiveHour,
      weekly: activePool.weekly,
      weeklyRecovery: activePool.weeklyRecovery || null,
      windows: activePool.windows,
      resetTime: activePool.resetTime,
      resetTs: activePool.resetTs,
      health: activePool.health,
    } : null,
    routingAdvice,
  };
}

// --- official quota snapshot ledger & dual-source 7-day weekly recovery -------

export const WINDOW_7D_SEC = 7 * 86400;
const HISTORY_RETENTION_SEC = 8 * 86400;
const HISTORY_HEARTBEAT_SEC = 1800;
const HISTORY_MAX_POINTS = 4000;
const PCT_EPS = 0.05;

export function createEmptyQuotaHistory() {
  return { version: 1, firstRecordedAt: 0, series: {} };
}

export function normalizeQuotaHistory(raw) {
  if (!raw || typeof raw !== 'object' || raw.version !== 1 || typeof raw.series !== 'object' || !raw.series) {
    return createEmptyQuotaHistory();
  }
  const cleanSeries = {};
  for (const [poolKey, winObj] of Object.entries(raw.series)) {
    if (!winObj || typeof winObj !== 'object') continue;
    cleanSeries[poolKey] = {};
    for (const [winKey, arr] of Object.entries(winObj)) {
      if (!Array.isArray(arr)) continue;
      cleanSeries[poolKey][winKey] = arr
        .filter((pt) => pt && typeof pt === 'object' && Number.isFinite(Number(pt.ts)) && Number.isFinite(Number(pt.pct)))
        .map((pt) => ({
          ts: Math.round(Number(pt.ts)),
          pct: Number(Math.max(0, Math.min(100, Number(pt.pct))).toFixed(2)),
          resetTs: Math.round(Number(pt.resetTs) || 0),
        }))
        .sort((a, b) => a.ts - b.ts);
    }
  }
  return {
    version: 1,
    firstRecordedAt: Number.isFinite(Number(raw.firstRecordedAt)) ? Math.round(Number(raw.firstRecordedAt)) : 0,
    series: cleanSeries,
  };
}

export function recordQuotaSnapshot(historyRaw, poolsPayload, nowSec = Math.floor(Date.now() / 1000)) {
  const history = normalizeQuotaHistory(historyRaw);
  const pools = Array.isArray(poolsPayload?.pools) ? poolsPayload.pools : [];
  let changed = false;
  const pruneBefore = nowSec - HISTORY_RETENTION_SEC;
  const edge7d = nowSec - WINDOW_7D_SEC;

  for (const pool of pools) {
    if (!pool || !pool.key) continue;
    const wins = Array.isArray(pool.windows) ? pool.windows : [];
    for (const w of wins) {
      if (!w || (w.window !== 'weekly' && w.window !== '5h')) continue;
      if (w.remainingPct === null || w.remainingPct === undefined || !Number.isFinite(Number(w.remainingPct))) continue;

      const pct = Number(Math.max(0, Math.min(100, Number(w.remainingPct))).toFixed(2));
      const resetTs = Math.round(Number(w.resetTs) || 0);

      if (!history.series[pool.key]) history.series[pool.key] = {};
      const arr = Array.isArray(history.series[pool.key][w.window])
        ? [...history.series[pool.key][w.window]]
        : [];
      const last = arr.length ? arr[arr.length - 1] : null;

      let shouldAppend = false;
      if (!last) {
        shouldAppend = true;
      } else if (nowSec < last.ts) {
        shouldAppend = false;
      } else if (Math.abs(pct - last.pct) >= PCT_EPS) {
        shouldAppend = true;
      } else {
        const bothFull = pct >= 99.99 && last.pct >= 99.99;
        const resetMoved = !bothFull && resetTs > 0 && last.resetTs > 0 && Math.abs(resetTs - last.resetTs) >= 60;
        if (resetMoved || (nowSec - last.ts) >= HISTORY_HEARTBEAT_SEC) {
          shouldAppend = true;
        }
      }

      if (shouldAppend) {
        arr.push({ ts: Math.round(nowSec), pct, resetTs });
        changed = true;
      }

      // Prune beyond 8d retention while preserving 1 boundary point immediately before the 7d edge
      let pruned = arr.filter((pt) => pt.ts >= pruneBefore);
      const beforeEdge = pruned.filter((pt) => pt.ts < edge7d);
      if (beforeEdge.length > 1) {
        const keepBoundary = beforeEdge[beforeEdge.length - 1];
        pruned = [keepBoundary, ...pruned.filter((pt) => pt.ts >= edge7d)];
      }
      if (pruned.length > HISTORY_MAX_POINTS) {
        pruned = pruned.slice(pruned.length - HISTORY_MAX_POINTS);
      }
      if (pruned.length !== arr.length) changed = true;

      history.series[pool.key][w.window] = pruned;
      if (history.firstRecordedAt === 0 && pruned.length) {
        history.firstRecordedAt = pruned[0].ts;
        changed = true;
      }
    }
  }

  return { history, changed };
}

function localDayInfo(tsSec, tzOffsetMin) {
  const offsetSec = tzOffsetMin * 60;
  const d = new Date((tsSec + offsetSec) * 1000);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth() + 1;
  const day = d.getUTCDate();
  const dateKey = `${y}-${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const label = `${String(m).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  const bucketStartTs = Math.round(Date.UTC(y, m - 1, day) / 1000 - offsetSec);
  return { dateKey, label, bucketStartTs, bucketEndTs: bucketStartTs + 86400 };
}

function roundPct(v) {
  return Number((Number(v) || 0).toFixed(2));
}

export function buildWeeklyRecovery({
  poolKey = '',
  weeklyWindow = null,
  historySamples = [],
  localPoolUsage = null,
  nowSec = Math.floor(Date.now() / 1000),
  tzOffsetMin = null,
}) {
  if (!weeklyWindow || weeklyWindow.remainingPct === null || weeklyWindow.remainingPct === undefined) {
    return null;
  }

  const tzMin = Number.isFinite(Number(tzOffsetMin))
    ? Number(tzOffsetMin)
    : -new Date(nowSec * 1000).getTimezoneOffset();

  const remainingPct = Number(Math.max(0, Math.min(100, Number(weeklyWindow.remainingPct))).toFixed(2));
  const usedPct = roundPct(Math.max(0, 100 - remainingPct));
  const edge7d = nowSec - WINDOW_7D_SEC;

  const localHourlyRaw = Array.isArray(localPoolUsage?.hourly) ? localPoolUsage.hourly : [];
  const localHourly = localHourlyRaw
    .filter((h) => h && Number(h.weighted) > 0 && Number(h.last_ts || (h.hour_ts + 3600)) >= edge7d && Number(h.first_ts || h.hour_ts) <= nowSec + 60)
    .map((h) => ({
      hourTs: Number(h.hour_ts) || 0,
      firstTs: Number(h.first_ts) || Number(h.hour_ts) || 0,
      lastTs: Number(h.last_ts) || (Number(h.hour_ts) + 3600) || 0,
      turns: Number(h.turns) || 0,
      weighted: Number(h.weighted) || 0,
    }))
    .sort((a, b) => a.hourTs - b.hourTs);

  const local7dSummary = localPoolUsage ? {
    turns: Number(localPoolUsage.turns_7d) || 0,
    turns5h: Number(localPoolUsage.turns_5h) || 0,
    uncachedIn: Number(localPoolUsage.uncached_in_7d) || 0,
    cacheRead: Number(localPoolUsage.cache_read_7d) || 0,
    out: Number(localPoolUsage.out_7d) || 0,
    oldestTs: Number(localPoolUsage.oldest_ts) || 0,
    newestTs: Number(localPoolUsage.newest_ts) || 0,
  } : null;

  // Analyze official snapshot series
  const samples = (Array.isArray(historySamples) ? historySamples : [])
    .filter((pt) => pt && Number.isFinite(Number(pt.ts)) && Number.isFinite(Number(pt.pct)) && Number(pt.ts) <= nowSec + 60 && Number(pt.ts) >= nowSec - HISTORY_RETENTION_SEC)
    .map((pt) => ({ ts: Number(pt.ts), pct: Number(pt.pct), resetTs: Number(pt.resetTs) || 0 }))
    .sort((a, b) => a.ts - b.ts);

  const historySpanHours = samples.length >= 2
    ? Number(((samples[samples.length - 1].ts - samples[0].ts) / 3600).toFixed(1))
    : 0;
  const inWindowSamples = samples.filter((pt) => pt.ts >= edge7d);
  const coverageStartTs = inWindowSamples.length ? inWindowSamples[0].ts : 0;

  const observedDrops = [];
  const recoverySteps = [];
  let lastNonFlat = null;

  for (let i = 1; i < samples.length; i += 1) {
    const prev = samples[i - 1];
    const cur = samples[i];
    const dt = cur.ts - prev.ts;
    if (dt <= 0 || cur.ts <= edge7d) continue;
    const delta = Number((cur.pct - prev.pct).toFixed(4));

    if (delta <= -PCT_EPS) {
      const dropPct = -delta;
      lastNonFlat = { delta, fromTs: prev.ts, toTs: cur.ts };
      // If there was a gap > 30m and local SQLite turns exist inside (prev.ts, cur.ts], distribute proportionally
      const matchingHours = dt > 1800
        ? localHourly.filter((h) => h.lastTs > prev.ts && h.firstTs <= cur.ts)
        : [];
      const matchWeight = matchingHours.reduce((acc, h) => acc + h.weighted, 0);
      if (matchingHours.length > 0 && matchWeight > 0) {
        for (const h of matchingHours) {
          const share = h.weighted / matchWeight;
          const effTs = Math.max(prev.ts, Math.min(cur.ts, h.firstTs || h.hourTs));
          observedDrops.push({ ts: effTs, pct: dropPct * share, turns: h.turns, source: 'snapshot' });
        }
      } else {
        observedDrops.push({ ts: cur.ts, pct: dropPct, turns: 0, source: 'snapshot' });
      }
    } else if (delta >= PCT_EPS) {
      lastNonFlat = { delta, fromTs: prev.ts, toTs: cur.ts };
      recoverySteps.push({ fromTs: prev.ts, toTs: cur.ts, gainPct: delta, dtSec: dt });
    }
  }

  // Observed real recovery metrics (24h gain + ongoing recovery rate)
  const rec24h = recoverySteps.filter((r) => r.toTs >= nowSec - 86400);
  const recovered24hPct = roundPct(rec24h.reduce((acc, r) => acc + r.gainPct, 0));
  const lastRecoveryTs = recoverySteps.length ? recoverySteps[recoverySteps.length - 1].toTs : 0;
  const isActivelyRecovering = Boolean(
    lastNonFlat
    && lastNonFlat.delta >= PCT_EPS
    && (nowSec - lastNonFlat.toTs) <= 2 * 3600
    && usedPct > PCT_EPS
  );

  // Compute observed recovery rate (%/h) from recent upward steps after the most recent drop (within 24h)
  const lastDropTs = observedDrops.length ? observedDrops[observedDrops.length - 1].ts : 0;
  const trailingRec = rec24h.filter((r) => r.fromTs >= lastDropTs);
  let ratePctPerHour = 0;
  if (trailingRec.length > 0) {
    const spanSec = trailingRec[trailingRec.length - 1].toTs - trailingRec[0].fromTs;
    const totalGain = trailingRec.reduce((acc, r) => acc + r.gainPct, 0);
    if (spanSec >= 900 && spanSec <= 24 * 3600 && totalGain >= 0.1) {
      const rawRate = totalGain / (spanSec / 3600);
      if (rawRate >= 0.05 && rawRate <= 25.0) {
        ratePctPerHour = Number(rawRate.toFixed(2));
      }
    }
  }
  const etaFullSec = ratePctPerHour > 0 && usedPct > PCT_EPS
    ? Math.round((usedPct / ratePctPerHour) * 3600)
    : 0;

  const observedRecovery = {
    isActive: isActivelyRecovering,
    recovered24hPct,
    ratePctPerHour,
    lastRecoveryTs,
    etaFullSec,
    sampleCount: samples.length,
    historySpanHours,
  };

  if (usedPct <= PCT_EPS) {
    return {
      poolKey,
      source: 'full',
      sourceLabel: '满额无待回血',
      confidence: 'high',
      usedPct: 0,
      remainingPct,
      nextRecoverTs: 0,
      nextRecoverSec: 0,
      nextRecoverPct: 0,
      fullClearTs: 0,
      fullClearSec: 0,
      recoverNext24hPct: 0,
      recoverNext48hPct: 0,
      recoverNext72hPct: 0,
      observedRecovery,
      local7dSummary,
      buckets: [],
      note: '当前周额度满额，无待回血消耗。',
    };
  }

  // Fuse official snapshot drops with local 7d SQLite cold-start distribution
  const obsTotal = observedDrops.reduce((acc, d) => acc + d.pct, 0);
  let pendingItems = [];
  let source = 'reset_anchor_only';
  let sourceLabel = '仅官方参考时间';
  let confidence = 'low';
  let note = '';

  const hasFullSnapshotCoverage = obsTotal > 0 && (
    obsTotal >= usedPct * 0.92
    || (coverageStartTs > 0 && coverageStartTs <= edge7d + 3600)
  );

  if (hasFullSnapshotCoverage) {
    // FIFO trim oldest observed drops if obsTotal exceeds current usedPct (earliest already rolled off)
    let working = observedDrops.map((d) => ({ ...d })).sort((a, b) => a.ts - b.ts);
    if (obsTotal > usedPct) {
      let excess = obsTotal - usedPct;
      const trimmed = [];
      for (const item of working) {
        if (excess <= 0) {
          trimmed.push(item);
        } else if (item.pct <= excess) {
          excess -= item.pct;
        } else {
          trimmed.push({ ...item, pct: item.pct - excess });
          excess = 0;
        }
      }
      working = trimmed;
    } else if (obsTotal > 0 && obsTotal < usedPct) {
      const scale = usedPct / obsTotal;
      working = working.map((item) => ({ ...item, pct: item.pct * scale }));
    }
    pendingItems = working;
    source = 'snapshot';
    sourceLabel = '官方快照实测';
    confidence = 'high';
    note = `基于近 ${historySpanHours}h 官方配额变动快照按 +7 天滚出推算，自动绕过模型黑盒权重。`;
  } else if (obsTotal > 0) {
    // Hybrid: observed drops since coverageStartTs + pre-coverage residual from local SQLite (or anchor)
    pendingItems = observedDrops.map((d) => ({ ...d }));
    const residual = Math.max(0, usedPct - obsTotal);
    const preLocal = localHourly.filter((h) => h.firstTs < coverageStartTs);
    const preWeight = preLocal.reduce((acc, h) => acc + h.weighted, 0);

    if (residual > PCT_EPS && preLocal.length > 0 && preWeight > 0) {
      for (const h of preLocal) {
        const effTs = Math.max(edge7d + 60, Math.min(coverageStartTs - 60, h.firstTs || h.hourTs));
        pendingItems.push({
          ts: effTs,
          pct: residual * (h.weighted / preWeight),
          turns: h.turns,
          source: 'local_estimate',
        });
      }
      source = 'hybrid';
      sourceLabel = '实测+冷启动估算';
      confidence = 'medium';
      note = `已录到 ${roundPct(obsTotal)}% 官方掉血轨迹，其余 ${roundPct(residual)}% 暂按本机 7 天对话加权分布估算。`;
    } else if (residual > PCT_EPS && weeklyWindow.resetTs > nowSec) {
      pendingItems.push({
        ts: Math.max(edge7d + 60, weeklyWindow.resetTs - WINDOW_7D_SEC),
        pct: residual,
        turns: 0,
        source: 'untracked',
      });
      source = 'hybrid';
      sourceLabel = '实测+参考锚点';
      confidence = 'medium';
      note = `已录到 ${roundPct(obsTotal)}% 官方掉血轨迹，其余 ${roundPct(residual)}% 未见本机历史，暂按官方参考锚点估算。`;
    } else {
      source = 'snapshot';
      sourceLabel = '官方快照实测';
      confidence = 'medium';
      note = `基于已记录的官方配额变动快照推算（持续运行满 7 天后将全周期实测）。`;
    }
  } else {
    // Cold start: no official snapshot drops recorded yet
    const totalLocalWeight = localHourly.reduce((acc, h) => acc + h.weighted, 0);
    if (localHourly.length > 0 && totalLocalWeight > 0) {
      for (const h of localHourly) {
        const effTs = Math.max(edge7d + 60, h.firstTs || h.hourTs);
        pendingItems.push({
          ts: effTs,
          pct: usedPct * (h.weighted / totalLocalWeight),
          turns: h.turns,
          source: 'local_estimate',
        });
      }
      source = 'local_estimate';
      sourceLabel = '本地7天冷启动估算';
      confidence = 'medium';
      note = `冷启动期：按本机 7 天 ${local7dSummary?.turns || 0} 轮加权消耗锚定官方已用 ${usedPct.toFixed(1)}%，后台快照正自动积累中。`;
    } else {
      if (weeklyWindow.resetTs > nowSec) {
        pendingItems.push({
          ts: Math.max(edge7d + 60, weeklyWindow.resetTs - WINDOW_7D_SEC),
          pct: usedPct,
          turns: 0,
          source: 'untracked',
        });
      }
      source = 'reset_anchor_only';
      sourceLabel = '仅官方参考时间';
      confidence = 'low';
      note = '本机近 7 天暂无该池对话记录（可能跨设备消耗），已开始后台记录官方变动快照。';
    }
  }

  // Shift every pending consumption event +7 days forward to compute roll-off timestamps
  const rollItems = pendingItems
    .map((item) => ({
      ...item,
      freeTs: Math.round(item.ts + WINDOW_7D_SEC),
    }))
    .filter((item) => item.freeTs > nowSec && item.pct > 0.001)
    .sort((a, b) => a.freeTs - b.freeTs);

  if (!rollItems.length) {
    return {
      poolKey,
      source,
      sourceLabel,
      confidence,
      usedPct,
      remainingPct,
      nextRecoverTs: weeklyWindow.resetTs > nowSec ? weeklyWindow.resetTs : 0,
      nextRecoverSec: weeklyWindow.resetTs > nowSec ? weeklyWindow.resetTs - nowSec : 0,
      nextRecoverPct: usedPct,
      fullClearTs: weeklyWindow.resetTs > nowSec ? weeklyWindow.resetTs : 0,
      fullClearSec: weeklyWindow.resetTs > nowSec ? weeklyWindow.resetTs - nowSec : 0,
      recoverNext24hPct: 0,
      recoverNext48hPct: 0,
      recoverNext72hPct: 0,
      observedRecovery,
      local7dSummary,
      buckets: [],
      note,
    };
  }

  const sumHorizon = (horizonSec) => roundPct(
    rollItems
      .filter((it) => it.freeTs <= nowSec + horizonSec)
      .reduce((acc, it) => acc + it.pct, 0)
  );

  // Group by local calendar day
  const dayMap = new Map();
  for (const it of rollItems) {
    const info = localDayInfo(it.freeTs, tzMin);
    let cur = dayMap.get(info.dateKey);
    if (!cur) {
      cur = {
        dateKey: info.dateKey,
        label: info.label,
        bucketStartTs: info.bucketStartTs,
        bucketEndTs: info.bucketEndTs,
        firstFreeTs: it.freeTs,
        lastFreeTs: it.freeTs,
        recoverPct: 0,
        turns: 0,
        sources: new Set(),
      };
      dayMap.set(info.dateKey, cur);
    }
    cur.firstFreeTs = Math.min(cur.firstFreeTs, it.freeTs);
    cur.lastFreeTs = Math.max(cur.lastFreeTs, it.freeTs);
    cur.recoverPct += it.pct;
    cur.turns += Number(it.turns) || 0;
    cur.sources.add(it.source);
  }

  const buckets = [];
  let cum = 0;
  for (const key of [...dayMap.keys()].sort()) {
    const b = dayMap.get(key);
    const rec = roundPct(b.recoverPct);
    cum = roundPct(cum + rec);
    const srcArr = [...b.sources];
    const bSource = srcArr.length === 1 ? srcArr[0] : 'hybrid';
    buckets.push({
      dateKey: b.dateKey,
      label: b.label,
      bucketStartTs: b.bucketStartTs,
      bucketEndTs: b.bucketEndTs,
      firstFreeTs: b.firstFreeTs,
      lastFreeTs: b.lastFreeTs,
      recoverPct: rec,
      cumRecoverPct: cum,
      projectedRemainingPct: roundPct(Math.min(100, remainingPct + cum)),
      turns: b.turns,
      source: bSource,
    });
  }

  const nextRecoverTs = rollItems[0].freeTs;
  const fullClearTs = rollItems[rollItems.length - 1].freeTs;

  return {
    poolKey,
    source,
    sourceLabel,
    confidence,
    usedPct,
    remainingPct,
    nextRecoverTs,
    nextRecoverSec: Math.max(0, nextRecoverTs - nowSec),
    nextRecoverPct: buckets.length ? buckets[0].recoverPct : 0,
    fullClearTs,
    fullClearSec: Math.max(0, fullClearTs - nowSec),
    recoverNext24hPct: sumHorizon(86400),
    recoverNext48hPct: sumHorizon(2 * 86400),
    recoverNext72hPct: sumHorizon(3 * 86400),
    observedRecovery,
    local7dSummary,
    buckets,
    note,
  };
}

export function enrichPoolsWithWeeklyRecovery(
  poolsPayload,
  quotaHistory,
  poolUsageReport = null,
  nowSec = Math.floor(Date.now() / 1000),
  tzOffsetMin = null,
) {
  if (!poolsPayload || !Array.isArray(poolsPayload.pools)) return poolsPayload;
  const hist = normalizeQuotaHistory(quotaHistory);
  const usagePools = (poolUsageReport && typeof poolUsageReport.pools === 'object') ? poolUsageReport.pools : {};

  const enrichedPools = poolsPayload.pools.map((pool) => {
    const series = hist.series?.[pool.key]?.weekly || [];
    const localUsage = usagePools[pool.key] || null;
    const weeklyRecovery = pool.weekly
      ? buildWeeklyRecovery({
        poolKey: pool.key,
        weeklyWindow: pool.weekly,
        historySamples: series,
        localPoolUsage: localUsage,
        nowSec,
        tzOffsetMin,
      })
      : null;
    return {
      ...pool,
      weeklyRecovery,
    };
  });

  return {
    ...poolsPayload,
    pools: enrichedPools,
    quotaHistoryMeta: {
      firstRecordedAt: hist.firstRecordedAt || 0,
      poolUsageScannedAt: Number(poolUsageReport?.generated_at) || 0,
    },
  };
}
