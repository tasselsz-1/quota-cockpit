// Sidecar backend for the Antigravity Quota & Context Cockpit panel.
//
// Combines four real-time views in a single zero-token AUX pane:
// 1. Image Generation Quota (`quota_report.py`): 5h rolling/429-locked window,
//    24h/7d stats, EOF tracking, empirical 429 ceiling calibration, and
//    optional workspace `api_call_ledger.json` linking.
// 2. Google Flow Video Quota (`flow_report.py`): local zero-network rolling
//    1h/24h traffic caps, daily credit budget, circuit breaker alerts, model
//    segment estimates, gflow.db 30d history, and on-demand manual credit sync.
// 3. Live Dual Quota Pools (`GetAvailableModels` + `RetrieveUserQuotaSummary` RPCs):
//    Pool A (Google Gemini) vs Pool B (Claude / OpenAI) 5-hour & weekly remaining %,
//    reset times, active model pool highlighting, and cross-pool routing advice.
// 4. Current Conversation Context (`context_report.py`): exact protobuf token
//    breakdown, 100-dot grid, checkpoints, and loaded files.

import { existsSync, unlinkSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Response, SidecarApp } from 'sidecar_sdk';
import { createPoolsService } from './lib/ls_client.mjs';
import {
  createEmptyQuotaHistory,
  normalizeQuotaHistory,
  resolveActivePool,
} from './pools_logic.mjs';
import { createReportsService } from './lib/reports.mjs';
import { findPython, registerWebAssets, runCommand } from './lib/runner.mjs';
import { clampInt, createQuotaHistoryStore, createSettingsStore } from './lib/settings.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const GEMINI_DIR = process.env.ANTIGRAVITY_GEMINI_DIR || join(homedir(), '.gemini');
const CONVERSATIONS = join(GEMINI_DIR, 'antigravity', 'conversations');
const DATA_DIR = process.env.ANTIGRAVITY_EXECUTABLE_DATA_DIR || join(HERE, '.data');

const CATALOG_PATH = join(DATA_DIR, 'model-catalog.json');
const SETTINGS_PATH = join(DATA_DIR, 'settings.json');
const IMAGE_CACHE_PATH = join(DATA_DIR, 'image-scan-cache.json');
const IMAGE_CALIBRATION_PATH = join(DATA_DIR, 'image-calibration.json');
const POOL_USAGE_CACHE_PATH = join(DATA_DIR, 'pool-usage-cache.json');
const QUOTA_HISTORY_PATH = join(DATA_DIR, 'quota-history.json');
const FLOW_CREDITS_CACHE_PATH = join(DATA_DIR, 'flow-credits-cache.json');
const FLOW_LEDGER_PATH = process.env.GFLOW_TRAFFIC_LEDGER || join(GEMINI_DIR, 'gflow_traffic_ledger.json');

const settingsStore = createSettingsStore(SETTINGS_PATH);
const quotaHistoryStore = createQuotaHistoryStore(QUOTA_HISTORY_PATH, {
  createEmptyQuotaHistory,
  normalizeQuotaHistory,
});

const poolsService = createPoolsService({
  conversationsDir: CONVERSATIONS,
  catalogPath: CATALOG_PATH,
  poolUsageScript: join(HERE, 'pool_usage_report.py'),
  poolUsageCachePath: POOL_USAGE_CACHE_PATH,
  poolUsageTtlMs: 30 * 1000,
  poolsTtlMs: 30 * 1000,
  poolsRetryMs: 5 * 1000,
  findPython,
  runCommand,
  quotaHistoryStore,
});

const reportsService = createReportsService({
  conversationsDir: CONVERSATIONS,
  catalogPath: CATALOG_PATH,
  contextScript: join(HERE, 'context_report.py'),
  quotaScript: join(HERE, 'quota_report.py'),
  flowScript: join(HERE, 'flow_report.py'),
  imageCachePath: IMAGE_CACHE_PATH,
  imageCalibrationPath: IMAGE_CALIBRATION_PATH,
  flowCreditsCachePath: FLOW_CREDITS_CACHE_PATH,
  flowLedgerPath: FLOW_LEDGER_PATH,
  imageTtlMs: 12 * 1000,
  flowTtlMs: 10 * 1000,
  getSettings: settingsStore.get,
  findPython,
  runCommand,
});

const app = new SidecarApp();

registerWebAssets(app, join(HERE, 'web'), Response);

app.api('/api/dashboard', async (data) => {
  const conversationId = String(data.conversationId || '').trim();
  const workspace = String(data.workspace || '').trim();
  const force = String(data.force || '') === '1' || data.force === true;

  const pools = await poolsService.refreshPools(force);
  const [context, imageQuota, flowQuota] = await Promise.all([
    reportsService.getContextReport(conversationId, workspace, force).catch((err) => ({ error: 'failed', message: err.message })),
    reportsService.getImageQuotaReport(workspace, force).catch((err) => ({ error: 'failed', message: err.message })),
    reportsService.getFlowQuotaReport(workspace, force).catch((err) => ({ error: 'failed', message: err.message })),
  ]);

  const activeModelId = context && !context.error ? (context.model_id || '') : '';
  const activeMeta = resolveActivePool(pools, activeModelId);

  return {
    serverNow: Math.floor(Date.now() / 1000),
    updatedAt: new Date().toISOString(),
    settings: settingsStore.get(),
    context,
    pools: { ...pools, ...activeMeta },
    imageQuota,
    flowQuota,
  };
}, 'GET');

app.api('/api/settings', async (data) => {
  try {
    const nextSettings = settingsStore.save(data, settingsStore.get());
    reportsService.invalidateImageCache();
    reportsService.invalidateFlowCache();
    const workspace = String(data.workspace || '').trim();
    const [imageQuota, flowQuota] = await Promise.all([
      reportsService.getImageQuotaReport(workspace, true).catch((err) => ({ error: 'failed', message: err.message })),
      reportsService.getFlowQuotaReport(workspace, true).catch((err) => ({ error: 'failed', message: err.message })),
    ]);
    return {
      ok: true,
      settings: nextSettings,
      imageQuota,
      flowQuota,
      serverNow: Math.floor(Date.now() / 1000),
    };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}, 'POST');

app.api('/api/flow/sync-credits', async (data) => {
  try {
    const workspace = String(data.workspace || '').trim();
    const forceIgnoreBusy = data.force === true || data.force === '1';
    const flowQuota = await reportsService.syncFlowCredits(workspace, forceIgnoreBusy);
    const syncRes = flowQuota?.sync_result || {};
    return {
      ok: Boolean(syncRes.ok),
      sync: syncRes,
      error: syncRes.ok ? '' : (syncRes.error || '同步 Flow 积分失败'),
      flowQuota,
      serverNow: Math.floor(Date.now() / 1000),
    };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}, 'POST');

app.api('/api/calibration/pin', async (data) => {
  try {
    const unpin = data.unpin === true || data.unpin === '1' || Number(data.calls) <= 0;
    const extraArgs = unpin
      ? ['--unpin-peak']
      : ['--pin-peak', String(clampInt(data.calls, 1, 5000, 100))];
    reportsService.invalidateImageCache();
    const workspace = String(data.workspace || '').trim();
    const imageQuota = await reportsService.getImageQuotaReport(workspace, true, extraArgs);
    return {
      ok: true,
      pinnedPeak: imageQuota?.calibration?.pinned_peak || null,
      imageQuota,
      serverNow: Math.floor(Date.now() / 1000),
    };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}, 'POST');

app.api('/api/calibration/reset', async (data) => {
  try {
    if (existsSync(IMAGE_CALIBRATION_PATH)) unlinkSync(IMAGE_CALIBRATION_PATH);
    reportsService.invalidateImageCache();
    const workspace = String(data.workspace || '').trim();
    const imageQuota = await reportsService.getImageQuotaReport(workspace, true).catch((err) => ({ error: 'failed', message: err.message }));
    return { ok: true, imageQuota, serverNow: Math.floor(Date.now() / 1000) };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}, 'POST');

app.api('/api/report', async (data) => {
  try {
    await poolsService.refreshPools(false);
    return await reportsService.getContextReport(data.conversationId, data.workspace);
  } catch (err) {
    return { error: 'failed', message: err.message };
  }
}, 'GET');

app.run();
poolsService.refreshPools(true);
