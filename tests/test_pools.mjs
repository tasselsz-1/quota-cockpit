import assert from 'node:assert/strict';
import test from 'node:test';
import { buildPoolsPayload, parseQuotaSummary, resolveActivePool } from '../sidecars/cockpit/pools_logic.mjs';

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
