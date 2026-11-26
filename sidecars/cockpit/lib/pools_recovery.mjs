// Official quota snapshot ledger & dual-source 7-day weekly recovery helpers.

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
