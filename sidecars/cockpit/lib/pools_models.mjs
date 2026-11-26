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
