import assert from 'node:assert/strict';
import test from 'node:test';
import {
  WINDOW_7D_SEC,
  buildPoolsPayload,
  buildWeeklyRecovery,
  createEmptyQuotaHistory,
  enrichPoolsWithWeeklyRecovery,
  parseQuotaSummary,
  recordQuotaSnapshot,
  resolveActivePool,
} from '../sidecars/cockpit/pools_logic.mjs';

const NOW_MS = Date.parse('2026-11-23T03:26:00Z');

const SAMPLE_MODELS = {
  'gemini-4-neon-xhigh': {
    displayName: 'Gemini 4 Neon (XHigh)',
    modelProvider: 'MODEL_PROVIDER_GOOGLE',
    maxTokens: 1048576,
    quotaInfo: { remainingFraction: 0.75, resetTime: '2026-11-23T06:03:08Z' },
  },
  'gemini-3.1-flash-image': {
    displayName: 'Gemini 3.1 Flash Image',
    modelProvider: 'MODEL_PROVIDER_GOOGLE',
    maxTokens: 65536,
    quotaInfo: { remainingFraction: 0.75, resetTime: '2026-11-23T06:03:08Z' },
  },
  'claude-opus-5-5-high': {
    displayName: 'Claude Opus 5.5 (High)',
    modelProvider: 'MODEL_PROVIDER_ANTHROPIC',
    maxTokens: 200000,
    quotaInfo: { remainingFraction: 0.4369, resetTime: '2026-11-29T07:31:51Z' },
  },
  'gpt-oss-120b-medium': {
    displayName: 'GPT-OSS 120B (Medium)',
    modelProvider: 'MODEL_PROVIDER_OPENAI',
    maxTokens: 131072,
    quotaInfo: { remainingFraction: 0.4369, resetTime: '2026-11-29T07:31:51Z' },
  },
};

const SAMPLE_SUMMARY = {
  response: {
    groups: [
      {
        displayName: 'Gemini Models',
        description: 'Models within this group: Gemini 4 Neon',
        buckets: [
          {
            bucketId: 'gemini-weekly',
            displayName: 'Weekly Limit Remaining',
            description: 'You have used some of your weekly limit.',
            window: 'weekly',
            remainingFraction: 0.686216,
            resetTime: '2026-11-28T08:57:17Z',
          },
          {
            bucketId: 'gemini-5h',
            displayName: 'Five Hour Limit Remaining',
            description: 'You have used some of your 5-hour limit.',
            window: '5h',
            remainingFraction: 0.75027657,
            resetTime: '2026-11-23T06:03:08Z',
          },
        ],
      },
      {
        displayName: 'Claude and GPT models',
        description: 'Models within this group: Claude Opus 5.5, GPT-OSS 120B',
        buckets: [
          {
            bucketId: '3p-weekly',
            displayName: 'Weekly Limit Remaining',
            description: 'You have used some of your weekly limit.',
            window: 'weekly',
            remainingFraction: 0.43693975,
            resetTime: '2026-11-29T07:31:51Z',
          },
          {
            bucketId: '3p-5h',
            displayName: 'Five Hour Limit Remaining',
            window: '5h',
            remainingFraction: 1,
            resetTime: '2026-11-23T08:26:22Z',
          },
        ],
      },
    ],
    description: 'Within each group, models share a weekly limit and a 5-hour limit.',
  },
};

test('parseQuotaSummary extracts ordered 5h and weekly windows for both pools', () => {
  const parsed = parseQuotaSummary(SAMPLE_SUMMARY, Math.floor(NOW_MS / 1000));
  assert.equal(parsed.available, true);
  assert.equal(parsed.byPoolKey.size, 2);

  const poolA = parsed.byPoolKey.get('pool_a');
  assert.equal(poolA.windows.length, 2);
  assert.equal(poolA.windows[0].window, '5h');
  assert.equal(poolA.windows[0].remainingPct, 75.03);
  assert.equal(poolA.windows[0].isFull, false);
  assert.equal(poolA.windows[1].window, 'weekly');
  assert.equal(poolA.windows[1].remainingPct, 68.62);

  const poolB = parsed.byPoolKey.get('pool_b');
  assert.equal(poolB.windows.length, 2);
  assert.equal(poolB.windows[0].window, '5h');
  assert.equal(poolB.windows[0].remainingPct, 100);
  assert.equal(poolB.windows[0].isFull, true);
  assert.equal(poolB.windows[1].window, 'weekly');
  assert.equal(poolB.windows[1].remainingPct, 43.69);
  assert.equal(poolB.windows[1].isFull, false);
});

test('buildPoolsPayload merges dual windows and computes bottleneck + active pool', () => {
  const { payload } = buildPoolsPayload(SAMPLE_MODELS, SAMPLE_SUMMARY, NOW_MS);
  assert.equal(payload.quotaSummaryAvailable, true);
  assert.equal(payload.pools.length, 2);

  const [poolA, poolB] = payload.pools;
  assert.equal(poolA.key, 'pool_a');
  assert.equal(poolA.hasDualWindows, true);
  assert.equal(poolA.fiveHour.remainingPct, 75.03);
  assert.equal(poolA.weekly.remainingPct, 68.62);
  assert.equal(poolA.bottleneckWindow, 'weekly');
  assert.equal(poolA.remainingPct, 68.62);

  assert.equal(poolB.key, 'pool_b');
  assert.equal(poolB.hasDualWindows, true);
  assert.equal(poolB.fiveHour.remainingPct, 100);
  assert.equal(poolB.fiveHour.isFull, true);
  assert.equal(poolB.weekly.remainingPct, 43.69);
  assert.equal(poolB.bottleneckWindow, 'weekly');
  assert.equal(poolB.remainingPct, 43.69);

  const active = resolveActivePool(payload, 'gemini-4-neon-xhigh');
  assert.equal(active.activePoolKey, 'pool_a');
  assert.equal(active.activePool.fiveHour.remainingPct, 75.03);
  assert.equal(active.activePool.weekly.remainingPct, 68.62);
  assert.equal(active.routingAdvice, null);
});

test('resolveActivePool emits accurate bottleneck routing advice when 5h or weekly drops below 20%', () => {
  const stressedSummary = structuredClone(SAMPLE_SUMMARY);
  stressedSummary.response.groups[0].buckets[1].remainingFraction = 0.12; // gemini-5h -> 12%
  const { payload } = buildPoolsPayload(SAMPLE_MODELS, stressedSummary, NOW_MS);

  const active = resolveActivePool(payload, 'gemini-4-neon-xhigh');
  assert.equal(active.activePool.bottleneckWindow, '5h');
  assert.equal(active.activePool.health, 'warning');
  assert.ok(active.routingAdvice);
  assert.equal(active.routingAdvice.level, 'warning');
  assert.match(active.routingAdvice.text, /5 小时额度仅剩 12\.0%/);
  assert.match(active.routingAdvice.text, /周额度余 68\.6%/);
  assert.match(active.routingAdvice.text, /建议切换至 池 B/);
});

test('buildPoolsPayload falls back cleanly when RetrieveUserQuotaSummary is unavailable', () => {
  const { payload } = buildPoolsPayload(SAMPLE_MODELS, null, NOW_MS);
  assert.equal(payload.quotaSummaryAvailable, false);
  const [poolA, poolB] = payload.pools;
  assert.equal(poolA.hasDualWindows, false);
  assert.equal(poolA.fiveHour.remainingPct, 75);
  assert.equal(poolA.weekly, null);
  assert.equal(poolB.hasDualWindows, false);
  assert.equal(poolB.fiveHour, null);
  assert.equal(poolB.weekly.remainingPct, 43.69);
});

test('recordQuotaSnapshot deduplicates steady points, ignores full reset churn, and records drops/recoveries', () => {
  const t0 = Math.floor(NOW_MS / 1000);
  const { payload: p0 } = buildPoolsPayload(SAMPLE_MODELS, SAMPLE_SUMMARY, t0 * 1000);

  let state = recordQuotaSnapshot(createEmptyQuotaHistory(), p0, t0);
  assert.equal(state.changed, true);
  assert.equal(state.history.series.pool_a.weekly.length, 1);
  assert.equal(state.history.series.pool_b['5h'].length, 1);

  // 60s later with identical pct and untouched full 3p-5h resetTime drifting by +60s -> no redundant write
  const summaryDrift = structuredClone(SAMPLE_SUMMARY);
  summaryDrift.response.groups[1].buckets[1].resetTime = new Date((t0 + 5 * 3600 + 82) * 1000).toISOString();
  const { payload: p1 } = buildPoolsPayload(SAMPLE_MODELS, summaryDrift, (t0 + 60) * 1000);
  state = recordQuotaSnapshot(state.history, p1, t0 + 60);
  assert.equal(state.changed, false);
  assert.equal(state.history.series.pool_a.weekly.length, 1);

  // Drop of 5% on pool_a weekly -> recorded immediately
  const summaryDrop = structuredClone(SAMPLE_SUMMARY);
  summaryDrop.response.groups[0].buckets[0].remainingFraction = 0.6362;
  const { payload: p2 } = buildPoolsPayload(SAMPLE_MODELS, summaryDrop, (t0 + 180) * 1000);
  state = recordQuotaSnapshot(state.history, p2, t0 + 180);
  assert.equal(state.changed, true);
  assert.equal(state.history.series.pool_a.weekly.length, 2);
  assert.equal(state.history.series.pool_a.weekly[1].pct, 63.62);

  // Heartbeat after 31m with unchanged pct -> recorded
  state = recordQuotaSnapshot(state.history, p2, t0 + 180 + 1860);
  assert.equal(state.changed, true);
  assert.equal(state.history.series.pool_a.weekly.length, 3);
});

test('buildWeeklyRecovery cold-start anchors local 7d weighted usage to official usedPct and shifts +7d', () => {
  const nowSec = Math.floor(Date.parse('2026-11-24T12:00:00Z') / 1000); // 20:00 UTC+8
  const weeklyWindow = {
    window: 'weekly',
    remainingPct: 60.0, // usedPct = 40%
    resetTs: nowSec + 4 * 86400,
    isFull: false,
  };
  const localPoolUsage = {
    turns_7d: 100,
    turns_5h: 10,
    uncached_in_7d: 1_000_000,
    cache_read_7d: 5_000_000,
    out_7d: 100_000,
    oldest_ts: nowSec - 6.5 * 86400,
    newest_ts: nowSec - 3600,
    hourly: [
      // 6.5 days ago -> rolls off in 12h (inside 24h milestone), 25% of weight -> +10%
      { hour_ts: nowSec - Math.round(6.5 * 86400), first_ts: nowSec - Math.round(6.5 * 86400), last_ts: nowSec - Math.round(6.5 * 86400) + 600, turns: 25, weighted: 100 },
      // 3.5 days ago -> rolls off in 3.5 days, 75% of weight -> +30%
      { hour_ts: nowSec - Math.round(3.5 * 86400), first_ts: nowSec - Math.round(3.5 * 86400), last_ts: nowSec - Math.round(3.5 * 86400) + 600, turns: 75, weighted: 300 },
    ],
  };

  const rec = buildWeeklyRecovery({
    poolKey: 'pool_a',
    weeklyWindow,
    historySamples: [],
    localPoolUsage,
    nowSec,
    tzOffsetMin: 480,
  });

  assert.equal(rec.source, 'local_estimate');
  assert.equal(rec.usedPct, 40);
  assert.equal(rec.recoverNext24hPct, 10);
  assert.equal(rec.buckets.length, 2);
  assert.equal(rec.buckets[0].recoverPct, 10);
  assert.equal(rec.buckets[0].projectedRemainingPct, 70);
  assert.equal(rec.buckets[1].recoverPct, 30);
  assert.equal(rec.buckets[1].projectedRemainingPct, 100);
});

test('buildWeeklyRecovery hybrid mode combines observed snapshot drops with pre-coverage local usage without double counting', () => {
  const nowSec = Math.floor(Date.parse('2026-11-24T12:00:00Z') / 1000);
  const coverageStart = nowSec - 12 * 3600;
  const weeklyWindow = {
    window: 'weekly',
    remainingPct: 50.0, // 50% used total
    resetTs: nowSec + 5 * 86400,
    isFull: false,
  };
  // Snapshots recorded a 20% drop inside the last 12h -> 30% residual came from before coverageStart
  const historySamples = [
    { ts: coverageStart, pct: 70.0, resetTs: weeklyWindow.resetTs },
    { ts: nowSec - 2 * 3600, pct: 50.0, resetTs: weeklyWindow.resetTs },
  ];
  const localPoolUsage = {
    turns_7d: 50,
    hourly: [
      // Pre-coverage (5.5 days ago) -> absorbs the 30% residual
      { hour_ts: nowSec - Math.round(5.5 * 86400), first_ts: nowSec - Math.round(5.5 * 86400), last_ts: nowSec - Math.round(5.5 * 86400) + 300, turns: 30, weighted: 300 },
      // Post-coverage (2h ago) -> must NOT absorb pre-coverage residual
      { hour_ts: nowSec - 2 * 3600, first_ts: nowSec - 2 * 3600, last_ts: nowSec - 2 * 3600 + 300, turns: 20, weighted: 200 },
    ],
  };

  const rec = buildWeeklyRecovery({
    poolKey: 'pool_a',
    weeklyWindow,
    historySamples,
    localPoolUsage,
    nowSec,
    tzOffsetMin: 480,
  });

  assert.equal(rec.source, 'hybrid');
  assert.equal(rec.buckets.length, 2);
  assert.equal(rec.buckets[0].recoverPct, 30);
  assert.equal(rec.buckets[0].source, 'local_estimate');
  assert.equal(rec.buckets[1].recoverPct, 20);
  assert.equal(rec.buckets[1].source, 'snapshot');
  assert.equal(rec.buckets[1].projectedRemainingPct, 100);
});

test('buildWeeklyRecovery full snapshot mode trims FIFO roll-off and measures live recovery rate', () => {
  const nowSec = Math.floor(Date.parse('2026-11-24T12:00:00Z') / 1000);
  const weeklyWindow = {
    window: 'weekly',
    remainingPct: 70.0, // 30% used currently
    resetTs: nowSec + 4 * 86400,
    isFull: false,
  };
  // Recorded 25% drop 3d ago + 15% drop 1d ago (= 40% drops), then 10% recovered over the last 4h -> 30% remaining used
  const historySamples = [
    { ts: nowSec - 4 * 86400, pct: 100.0, resetTs: 0 },
    { ts: nowSec - 3 * 86400, pct: 75.0, resetTs: weeklyWindow.resetTs },
    { ts: nowSec - 1 * 86400, pct: 60.0, resetTs: weeklyWindow.resetTs },
    { ts: nowSec - 4 * 3600, pct: 60.0, resetTs: weeklyWindow.resetTs },
    { ts: nowSec - 2 * 3600, pct: 65.0, resetTs: weeklyWindow.resetTs },
    { ts: nowSec - 300, pct: 70.0, resetTs: weeklyWindow.resetTs },
  ];

  const rec = buildWeeklyRecovery({
    poolKey: 'pool_a',
    weeklyWindow,
    historySamples,
    localPoolUsage: null,
    nowSec,
    tzOffsetMin: 480,
  });

  assert.equal(rec.source, 'snapshot');
  assert.equal(rec.confidence, 'high');
  // FIFO trim removes 10% from oldest drop (25% -> 15%), keeping 15% from 3d ago + 15% from 1d ago = 30%
  assert.equal(rec.buckets.length, 2);
  assert.equal(rec.buckets[0].recoverPct, 15);
  assert.equal(rec.buckets[1].recoverPct, 15);
  assert.equal(rec.buckets[1].projectedRemainingPct, 100);
  // Real recovery rate observed: +10% across ~3.92h (~2.55%/h)
  assert.equal(rec.observedRecovery.isActive, true);
  assert.equal(rec.observedRecovery.recovered24hPct, 10);
  assert.ok(rec.observedRecovery.ratePctPerHour > 2.0 && rec.observedRecovery.ratePctPerHour < 3.0);
  assert.ok(rec.observedRecovery.etaFullSec > 0);
});

test('enrichPoolsWithWeeklyRecovery attaches weeklyRecovery to pools and handles full / anchor-only cleanly', () => {
  const nowSec = Math.floor(NOW_MS / 1000);
  const { payload } = buildPoolsPayload(SAMPLE_MODELS, SAMPLE_SUMMARY, NOW_MS);
  const enriched = enrichPoolsWithWeeklyRecovery(payload, createEmptyQuotaHistory(), null, nowSec, 480);

  const [poolA, poolB] = enriched.pools;
  assert.ok(poolA.weeklyRecovery);
  assert.equal(poolA.weeklyRecovery.source, 'reset_anchor_only');
  assert.equal(poolA.weeklyRecovery.usedPct, 31.38);
  assert.equal(poolA.weeklyRecovery.buckets.length, 1);
  assert.ok(poolB.weeklyRecovery);
  assert.equal(poolB.weeklyRecovery.usedPct, 56.31);
  assert.equal(WINDOW_7D_SEC, 7 * 86400);
});
