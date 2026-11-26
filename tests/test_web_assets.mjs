import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');
const COCKPIT_DIR = path.join(ROOT, 'sidecars', 'cockpit');
const WEB_DIR = path.join(COCKPIT_DIR, 'web');
const JS_DIR = path.join(WEB_DIR, 'js');

const EXPECTED_JS_ORDER = [
  'state.js',
  'formatters.js',
  'render-pools.js',
  'render-header.js',
  'render-image.js',
  'render-context.js',
  'render-flow.js',
  'render-overview.js',
  'actions.js',
  'app.js',
];

function readText(relPath) {
  return fs.readFileSync(path.join(WEB_DIR, relPath), 'utf8');
}

function stripCommentsAndStrings(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/\/\/[^\n]*/g, ' ')
    .replace(/`(?:\\[\s\S]|\$\{[^}]*\}|[^`\\])*`/g, ' ')
    .replace(/'(?:\\.|[^'\\])*'/g, ' ')
    .replace(/"(?:\\.|[^"\\])*"/g, ' ');
}

function parseQcDestructured(source) {
  const names = [];
  const re = /const\s*\{([\s\S]*?)\}\s*=\s*QC\s*;/g;
  for (const m of source.matchAll(re)) {
    for (const part of m[1].split(',')) {
      const sym = part.trim().split(':')[0].trim();
      if (sym) names.push(sym);
    }
  }
  return names;
}

function parseQcExports(source) {
  const exported = new Set();
  const assignRe = /Object\.assign\(\s*QC\s*,\s*\{([\s\S]*?)\}\s*\)\s*;/g;
  for (const m of source.matchAll(assignRe)) {
    for (const part of m[1].split(',')) {
      const sym = part.trim().split(':')[0].trim();
      if (sym) exported.add(sym);
    }
  }
  const propRe = /\bQC\.([A-Za-z_$][\w$]*)\s*=/g;
  for (const m of source.matchAll(propRe)) {
    exported.add(m[1]);
  }
  return exported;
}

function createDomSandbox({ dashboardPayload, fetchImpl }) {
  const createEl = (id = '', tagName = 'DIV') => {
    const classes = new Set();
    const listeners = new Map();
    const el = {
      id,
      tagName,
      style: {},
      dataset: {},
      textContent: '',
      title: '',
      innerHTML: '',
      disabled: false,
      value: '',
      elements: {},
      className: '',
      classList: {
        add: (...cls) => cls.forEach((c) => classes.add(c)),
        remove: (...cls) => cls.forEach((c) => classes.delete(c)),
        toggle: (c, force) => {
          const next = force === undefined ? !classes.has(c) : Boolean(force);
          if (next) classes.add(c);
          else classes.delete(c);
          return next;
        },
        contains: (c) => classes.has(c),
      },
      addEventListener: (ev, fn) => {
        if (!listeners.has(ev)) listeners.set(ev, []);
        listeners.get(ev).push(fn);
      },
      dispatchEvent: (ev) => {
        for (const fn of listeners.get(ev.type) || []) fn(ev);
      },
      closest: () => null,
      select: () => {},
      remove: () => {},
      appendChild: () => {},
    };
    return el;
  };

  const elementsById = new Map([
    ['live-dot', createEl('live-dot', 'SPAN')],
    ['hdr-model', createEl('hdr-model', 'SPAN')],
    ['hdr-pool', createEl('hdr-pool', 'SPAN')],
    ['hdr-quota', createEl('hdr-quota', 'DIV')],
    ['btn-refresh', createEl('btn-refresh', 'BUTTON')],
    ['tab-img-dot', createEl('tab-img-dot', 'SPAN')],
    ['tab-flow-dot', createEl('tab-flow-dot', 'SPAN')],
    ['view', createEl('view', 'MAIN')],
  ]);

  const tabButtons = ['overview', 'image', 'pools', 'context', 'flow'].map((tab) => {
    const btn = createEl('', 'BUTTON');
    btn.dataset.tab = tab;
    if (tab === 'overview') btn.classList.add('active');
    return btn;
  });

  const docListeners = new Map();
  const document = {
    hidden: false,
    activeElement: null,
    body: createEl('body', 'BODY'),
    getElementById: (id) => {
      if (!elementsById.has(id)) elementsById.set(id, createEl(id));
      return elementsById.get(id);
    },
    querySelectorAll: (sel) => {
      if (sel === '.tab') return tabButtons;
      return [];
    },
    createElement: (tag) => createEl('', String(tag || 'div').toUpperCase()),
    execCommand: () => true,
    addEventListener: (ev, fn) => {
      if (!docListeners.has(ev)) docListeners.set(ev, []);
      docListeners.get(ev).push(fn);
    },
  };

  const sidecar = {
    conversationId: 'test-conv-123',
    fetch: fetchImpl || (async (url) => {
      if (String(url).startsWith('/api/dashboard')) {
        return {
          ok: true,
          status: 200,
          json: async () => dashboardPayload,
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      };
    }),
    getWorkspaceUris: async () => [],
    onWorkspaceChange: () => {},
  };

  const sandbox = {
    window: null,
    document,
    navigator: { clipboard: { writeText: async () => {} } },
    sidecar,
    console,
    Date,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Map,
    Set,
    JSON,
    URLSearchParams,
    Error,
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  return { sandbox, elementsById, tabButtons };
}

function buildFixtureDashboard() {
  const now = Math.floor(Date.now() / 1000);
  return {
    serverNow: now,
    updatedAt: new Date(now * 1000).toISOString(),
    settings: {
      anchorMode: 'auto',
      softCap: 54,
      hardCap: 60,
      tokenCap: 0,
      countMode: 'attempts',
      flowMaxPerHour: 6,
      flowMaxPerDay: 20,
      flowMaxDailyCredits: 400,
      flowMinCredits: 100,
      flowMinGapSec: 35,
    },
    imageQuota: {
      model: 'gemini-3.1-flash-image',
      window_5h: {
        mode: 'rolling',
        health: 'ok',
        metric_used: 12,
        ok: 11,
        eof: 1,
        err: 0,
        rate_limited: 0,
        weekly_cooldown_active: false,
        cooldown_active: false,
        tokens_total: 46560,
        tokens_raw: 43560,
        prompt_tokens: 3000,
        input_image_tokens: 26880,
        output_image_tokens: 13440,
        thinking_tokens: 3000,
        ref_images: 24,
        est_usd: 0.82,
        used_pct: 20,
        calls_pct: 20,
        tokens_pct: 18,
        used_pct_basis: 'calls',
        remaining_est: 48,
        soft_cap: 54,
        hard_cap: 60,
        next_recover_ts: now + 1800,
        full_clear_ts: now + 14400,
        oldest_ts: now - 3600,
        newest_ts: now - 120,
      },
      window_24h: { ok: 15, eof: 1, attempts: 16, ref_images: 30, tokens_total: 61000 },
      window_7d: { ok: 40, eof: 2, attempts: 42, ref_images: 80, tokens_total: 158000 },
      anchor: {
        status: 'safe_peak',
        source: 'safe_peak',
        label: '实测安全水位',
        ref_calls: 60,
        ref_tokens: 232800,
        soft_calls: 54,
        calls_pct: 20,
        tokens_pct: 20,
        remaining_calls: 48,
        remaining_tokens: 186240,
        avg_with_refs_tokens: 3880,
        est_remaining_imgs_with_refs: 48,
        est_remaining_imgs_with_refs_exact: 48,
        est_remaining_imgs_by_refs: { 0: 110, 1: 66, 2: 48, '3+': 36 },
      },
      per_image_avg: {
        sample_count: 42,
        avg_refs: 2.0,
        avg_prompt_tokens: 250,
        avg_tokens_total: 3880,
        avg_est_usd: 0.0682,
        with_refs: { sample_count: 38, avg_refs: 2.1, avg_tokens_total: 3880, share_pct: 90.5 },
        by_refs: [{ label: '2', count: 30, avg_prompt_tokens: 250, avg_tokens_total: 3880, avg_est_usd: 0.0682 }],
      },
      rolling_schedule: {
        total_calls: 12,
        first_batch: { calls: 4, tokens_total: 15520, est_imgs_with_refs: 4 },
        recover_next_30m: { calls: 4, tokens_total: 15520, est_imgs_with_refs: 4 },
        recover_next_1h: { calls: 6, tokens_total: 23280, est_imgs_with_refs: 6 },
        recover_next_2h: { calls: 10, tokens_total: 38800, est_imgs_with_refs: 10 },
        buckets: [{
          bucket_start_ts: now + 1800,
          bucket_end_ts: now + 3600,
          first_free_ts: now + 1800,
          last_free_ts: now + 3500,
          calls: 4,
          ok: 4,
          eof: 0,
          err: 0,
          tokens_total: 15520,
          est_imgs_with_refs: 4,
          cum_calls: 4,
          cum_pct: 33,
        }],
      },
      weekly_rolling_schedule: { total_calls: 42, total_tokens_total: 158000, buckets: [] },
      calibration: { observed_safe_peak: { max_attempts: 60, max_ok: 58, max_tokens_total: 232800 } },
      workspace_ledgers: [],
      recent_events: [],
      scan_meta: { active_dbs: 3, archived_dbs: 0 },
    },
    pools: {
      fetchedAt: now,
      activePoolKey: 'pool_a',
      quotaSummaryAvailable: true,
      pools: [
        {
          key: 'pool_a',
          label: '池 A · Gemini',
          shortLabel: 'Gemini',
          accent: 'pool-a',
          remainingPct: 85.0,
          health: 'ok',
          familyCount: 2,
          modelCount: 4,
          hasDualWindows: true,
          windows: [
            { window: '5h', label: '5h 窗口', shortLabel: '5h', remainingPct: 85.0, usedPct: 15.0, resetTs: now + 9000, isFull: false, health: 'ok' },
            { window: 'weekly', label: '7d 周窗口', shortLabel: '周', remainingPct: 92.0, usedPct: 8.0, resetTs: now + 86400, isFull: false, health: 'ok' },
          ],
          weeklyRecovery: { source: 'snapshot', usedPct: 8.0, recoverNext24hPct: 4.5, buckets: [] },
          models: [{ id: 'gemini-3-pro', family: 'Gemini 3 Pro', tier: 'PRO', role: '主力推理', remainingPct: 85.0, maxTokens: 1000000 }],
        },
      ],
    },
    context: {
      updatedAt: new Date(now * 1000).toISOString(),
      model: 'Gemini 3.1 Pro',
      model_id: 'gemini-3.1-pro',
      used_tokens: 42000,
      limit_tokens: 1000000,
      free_tokens: 958000,
      used_pct: 4.2,
      request_budget_tokens: 200000,
      grid_segments: [10000, 12000, 8000, 7000, 5000],
      categories: [{ key: 'user', name: '用户消息', tokens: 12000, pct: 1.2 }],
      cache: {
        has_data: true,
        latest: { status: 'high', hit_rate_pct: 91.5, cache_read_tokens: 35000, uncached_input_tokens: 3250, ts: now - 30 },
        session: { hit_rate_pct: 89.2, turns: 12, misses: 0, cache_read_tokens: 280000 },
        recent: [{ status: 'high', hit_rate_pct: 91.5, cache_read_tokens: 35000, uncached_input_tokens: 3250, ts: now - 30 }],
      },
      steps: {
        has_data: true,
        total: 1581,
        has_compaction: true,
        compactions: 14,
        segment: { total: 12, user: 1, agent: 3, tool: 8, system: 0, checkpoint: 0 },
        session: { total: 1581, user: 50, agent: 478, tool: 1004, system: 33, checkpoint: 16 },
      },
      text: 'context text summary',
    },
    flowQuota: {
      available: true,
      health: 'ok',
      status_label: '正常',
      credits: {
        synced: true,
        synced_at: now - 300,
        synced_total: 1200,
        estimated_total: 1180,
        spent_since_sync: 20,
        reserve_floor: 100,
        spendable: 1080,
        default_profile: 'default',
        profiles: [{ profile: 'default', is_default: true, cookies_present: true, credits: 1200 }],
      },
      rolling: {
        hour_used: 1,
        hour_limit: 6,
        hour_pct: 17,
        day_used: 4,
        day_limit: 20,
        day_pct: 20,
        day_credits_used: 40,
        day_credits_limit: 400,
        day_credits_pct: 10,
        day_credits_remaining: 360,
        bottleneck_pct: 20,
        hour_next_recover_ts: now + 1800,
        hour_full_clear_ts: now + 1800,
        day_next_recover_ts: now + 36000,
        min_gap_wait_sec: 0,
      },
      breaker: { open: false, requires_manual_reset: false },
      policy: { min_gap_sec: 35 },
      model_estimates: [{ id: 'veo_3_1_lite', label: 'Veo 3.1 Lite', max_duration: 8, ref_cap: 3, unit_credits: 10, est_segments: 108, recent_30d_ok: 4, recent_30d_fail: 0 }],
      retiring_models: [],
      usage: { window_7d: { total: 4, ok: 4, failed: 0, waf: 0 }, window_30d: { total: 10, ok: 10, failed: 0, character_total: 2 }, by_model_30d: [] },
      data_sources: { rate_limit_source: 'ledger' },
      workspace_runs: [],
      recent_events: [],
    },
  };
}

test('all expected web assets exist and are non-empty', () => {
  for (const rel of ['index.html', 'styles.css', ...EXPECTED_JS_ORDER.map((f) => path.join('js', f))]) {
    const full = path.join(WEB_DIR, rel);
    assert.ok(fs.existsSync(full), `Missing web asset: ${rel}`);
    const content = fs.readFileSync(full, 'utf8');
    assert.ok(content.trim().length > 0, `Empty web asset: ${rel}`);
  }
});

test('web/index.html has NO <script type="module"> and loads all 10 classic scripts in exact dependency order', () => {
  const html = readText('index.html');
  assert.ok(!/<script[^>]*type\s*=\s*["']?module/i.test(html), 'Regression: <script type="module"> is blocked in Antigravity sidecar iframe');

  const scriptSrcs = [...html.matchAll(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)].map((m) => m[1]);
  const jsSrcs = scriptSrcs.filter((s) => s.startsWith('/web/js/'));
  assert.deepEqual(
    jsSrcs,
    EXPECTED_JS_ORDER.map((f) => `/web/js/${f}`),
    'Classic <script src="/web/js/..."> tags must match exact dependency order',
  );

  const refs = [...html.matchAll(/(?:href|src)=["'](\/web\/[^"']+)["']/g)].map((m) => m[1]);
  assert.ok(refs.includes('/web/styles.css'), 'index.html must load /web/styles.css');
  for (const ref of refs) {
    const rel = ref.replace(/^\/web\//, '');
    assert.ok(fs.existsSync(path.join(WEB_DIR, rel)), `Referenced asset missing on disk: ${ref}`);
  }
});

test('all web/js/*.js pass node --check syntax validation', () => {
  for (const file of EXPECTED_JS_ORDER) {
    const full = path.join(JS_DIR, file);
    execFileSync(process.execPath, ['--check', full], { stdio: 'pipe' });
  }
});

test('all web/js/*.js use classic IIFE pattern with zero ESM import/export and valid QC dependency order', () => {
  const availableOnQc = new Set();

  for (const file of EXPECTED_JS_ORDER) {
    const src = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
    const codeOnly = stripCommentsAndStrings(src);

    assert.ok(!/\bimport\b/.test(codeOnly), `${file} must not contain ESM import keyword`);
    assert.ok(!/\bexport\b/.test(codeOnly), `${file} must not contain ESM export keyword`);
    assert.ok(
      src.includes('(function (QC) {') && src.includes('})(window.__QC__ = window.__QC__ || {});'),
      `${file} must use classic IIFE wrapper sharing window.__QC__`,
    );

    const needed = parseQcDestructured(src);
    for (const sym of needed) {
      assert.ok(
        availableOnQc.has(sym),
        `${file} destructures '${sym}' from QC before any earlier script exposed it`,
      );
    }

    for (const sym of parseQcExports(src)) {
      availableOnQc.add(sym);
    }
  }
});

test('end-to-end VM execution of all 10 classic scripts renders all 5 tabs without errors and puts Flow after Context', async () => {
  const html = readText('index.html');
  const jsSrcs = [...html.matchAll(/<script\b[^>]*\bsrc=["'](\/web\/js\/[^"']+)["'][^>]*>/gi)].map((m) => m[1]);
  const { sandbox, elementsById } = createDomSandbox({ dashboardPayload: buildFixtureDashboard() });
  const ctx = vm.createContext(sandbox);

  for (const srcPath of jsSrcs) {
    const rel = srcPath.replace(/^\/web\//, '');
    const code = readText(rel);
    vm.runInContext(code, ctx, { filename: srcPath });
  }

  // Allow initial async refresh(false) promise triggered by app.js to settle
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const QC = sandbox.window.__QC__;
  assert.ok(QC, 'window.__QC__ must be initialized');
  assert.equal(typeof QC.render, 'function', 'QC.render must be exposed');
  assert.equal(typeof QC.setTab, 'function', 'QC.setTab must be exposed');

  const viewEl = elementsById.get('view');
  assert.ok(viewEl.innerHTML.trim().length > 0, 'Initial overview render must produce non-empty HTML');

  for (const tab of ['overview', 'image', 'pools', 'context', 'flow']) {
    QC.setTab(tab);
    assert.equal(QC.state.tab, tab);
    const rendered = viewEl.innerHTML;
    assert.ok(rendered.trim().length > 0, `Tab '${tab}' rendered empty HTML`);
    assert.ok(!rendered.includes('读取面板数据失败'), `Tab '${tab}' rendered error fallback`);
  }

  QC.setTab('overview');
  const overviewHtml = viewEl.innerHTML;
  const ctxCardIdx = overviewHtml.indexOf('当前上下文');
  const ovCacheIdx = overviewHtml.indexOf('Prompt 缓存');
  const ovStepsIdx = overviewHtml.indexOf('对话步数');
  const flowCardIdx = overviewHtml.indexOf('Flow 生视频');
  assert.ok(ctxCardIdx >= 0, 'Overview HTML must contain Context card');
  assert.ok(ovCacheIdx > ctxCardIdx, 'Overview HTML must contain Prompt cache strip');
  assert.ok(ovStepsIdx > ovCacheIdx, 'Steps strip must appear directly AFTER Prompt cache strip in Overview HTML');
  assert.ok(overviewHtml.includes('本段 12 步') && overviewHtml.includes('1,581'), 'Overview steps strip must show segment and total step counts');
  assert.ok(flowCardIdx > ovStepsIdx, 'Flow card must appear AFTER Context/Steps card in rendered Overview HTML');

  QC.setTab('context');
  const contextHtml = viewEl.innerHTML;
  const ctxCacheIdx = contextHtml.indexOf('Prompt 缓存命中');
  const ctxStepsIdx = contextHtml.indexOf('对话步数');
  assert.ok(ctxCacheIdx >= 0, 'Context tab HTML must contain Prompt cache hit card');
  assert.ok(ctxStepsIdx > ctxCacheIdx, 'Steps card must appear directly AFTER Prompt cache hit card in Context tab HTML');
  assert.ok(contextHtml.includes('本段步数（压缩后）') && contextHtml.includes('1,581 步'), 'Context tab steps card must display segment and session step counts');

  const uncompactedStrip = QC.renderStepsOverviewStrip({
    has_data: true,
    total: 63,
    has_compaction: false,
    compactions: 0,
    segment: { total: 63, user: 1, agent: 12, tool: 50, system: 0, checkpoint: 0 },
    session: { total: 63, user: 1, agent: 12, tool: 50, system: 0, checkpoint: 0 },
  });
  assert.ok(uncompactedStrip.includes('63 步') && !uncompactedStrip.includes('本段'), 'Uncompacted steps strip should show total steps concisely');
});

test('.tabs in web/index.html places Flow last on the far right', () => {
  const html = readText('index.html');
  const tabsBlock = html.match(/<nav\s+class="tabs"[\s\S]*?<\/nav>/);
  assert.ok(tabsBlock, 'Missing <nav class="tabs"> block');
  const tabs = [...tabsBlock[0].matchAll(/data-tab="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(tabs, ['overview', 'image', 'pools', 'context', 'flow']);
});

test('renderOverview places Flow card at the very bottom after Context card', () => {
  const overviewSrc = readText(path.join('js', 'render-overview.js'));
  const imgIdx = overviewSrc.indexOf('Image Quota Summary Card');
  const poolsIdx = overviewSrc.indexOf('Model Quota Pools Summary Card');
  const ctxIdx = overviewSrc.indexOf('Current Context Summary Card');
  const flowIdx = overviewSrc.indexOf('renderFlowOverviewCard(d.flowQuota)');

  assert.ok(imgIdx >= 0, 'Missing Image card section in renderOverview');
  assert.ok(poolsIdx > imgIdx, 'Pools card must follow Image card');
  assert.ok(ctxIdx > poolsIdx, 'Context card must follow Pools card');
  assert.ok(flowIdx > ctxIdx, 'Flow card must appear AFTER Context card at the very bottom');
});

test('preserves public ledger text, actions callback wiring, and setTab order', () => {
  const imgSrc = readText(path.join('js', 'render-image.js'));
  assert.ok(
    imgSrc.includes('当前工作区未发现可选的 <span class="mono">api_call_ledger.json</span> 账本文件。'),
    'Missing expected public ledger empty-state string',
  );

  const actionsSrc = readText(path.join('js', 'actions.js'));
  assert.ok(actionsSrc.includes('function setActionsCallbacks'), 'actions.js must define setActionsCallbacks');
  assert.ok(actionsSrc.includes('setActionsCallbacks'), 'actions.js must expose setActionsCallbacks on QC');

  const appSrc = readText(path.join('js', 'app.js'));
  assert.ok(
    appSrc.includes("['overview', 'image', 'pools', 'context', 'flow']"),
    'app.js setTab allowed list must match reordered tabs',
  );
  assert.ok(appSrc.includes('setActionsCallbacks({ render })'), 'app.js must wire render callback into actions.js');
  assert.ok(appSrc.includes('QC.render = render;'), 'app.js must expose QC.render');
  assert.ok(appSrc.includes('QC.setTab = setTab;'), 'app.js must expose QC.setTab');
});
