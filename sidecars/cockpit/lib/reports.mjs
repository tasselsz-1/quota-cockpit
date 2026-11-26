// Context, image quota, and Google Flow video quota report runners & TTL caches.

import { existsSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileStamp, workspacePath } from './runner.mjs';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function createReportsService({
  conversationsDir,
  catalogPath,
  contextScript,
  quotaScript,
  flowScript,
  imageCachePath,
  imageCalibrationPath,
  flowCreditsCachePath,
  flowLedgerPath,
  imageTtlMs,
  flowTtlMs,
  getSettings,
  findPython,
  runCommand,
}) {
  const imageDataDir = dirname(imageCachePath);
  const flowDataDir = dirname(flowCreditsCachePath);

  const contextCache = new Map(); // conversationId -> { key, report }
  let imageQuotaCache = { key: '', fetchedAt: 0, data: null };
  let imageQuotaInFlight = null;
  let flowQuotaCache = { key: '', fetchedAt: 0, data: null };
  let flowQuotaInFlight = null;
  let flowSyncInFlight = null;

  async function getContextReport(conversationId, workspaceUri, force = false) {
    if (!UUID.test(conversationId || '')) {
      return { error: 'no-conversation' };
    }
    const db = join(conversationsDir, `${conversationId}.db`);
    if (!existsSync(db)) {
      return { error: 'no-database' };
    }
    const workspace = workspacePath(workspaceUri);
    const catalogStamp = catalogPath ? fileStamp(catalogPath) : '-';
    const key = [fileStamp(db), fileStamp(`${db}-wal`), workspace, catalogStamp].join('|');
    const cached = contextCache.get(conversationId);
    if (!force && cached && cached.key === key) return cached.report;

    const { cmd, pre } = await findPython();
    const args = [...pre, contextScript, '--db', db, '--json'];
    if (catalogPath && existsSync(catalogPath)) args.push('--model-catalog', catalogPath);
    if (workspace) args.push('--workspace', workspace);
    const out = await runCommand(cmd, args);
    const result = { ...JSON.parse(out), updatedAt: new Date().toISOString() };
    contextCache.set(conversationId, { key, report: result });
    return result;
  }

  async function getImageQuotaReport(workspaceUri, force = false, extraArgs = []) {
    const workspace = workspacePath(workspaceUri);
    const s = getSettings();
    const key = `${workspace}|${s.softCap}|${s.hardCap}|${s.tokenCap}|${s.countMode}|${s.anchorMode}`;
    const now = Date.now();
    const hasMutation = Array.isArray(extraArgs) && extraArgs.length > 0;

    if (!force && !hasMutation && imageQuotaCache.data && imageQuotaCache.key === key && (now - imageQuotaCache.fetchedAt) < imageTtlMs) {
      return imageQuotaCache.data;
    }
    if (!force && !hasMutation && imageQuotaInFlight && imageQuotaInFlight.key === key) {
      return imageQuotaInFlight.promise;
    }

    const promise = (async () => {
      mkdirSync(imageDataDir, { recursive: true });
      const { cmd, pre } = await findPython();
      const args = [
        ...pre,
        quotaScript,
        '--conv-dir', conversationsDir,
        '--soft-cap', String(s.softCap),
        '--hard-cap', String(s.hardCap),
        '--token-cap', String(s.tokenCap),
        '--count-mode', s.countMode,
        '--anchor-mode', s.anchorMode,
        '--cache', imageCachePath,
        '--calibration', imageCalibrationPath,
        ...(hasMutation ? extraArgs : []),
      ];
      if (workspace) args.push('--workspace', workspace);
      const out = await runCommand(cmd, args, 30000);
      const parsed = JSON.parse(out);
      imageQuotaCache = { key, fetchedAt: Date.now(), data: parsed };
      return parsed;
    })();

    if (!hasMutation) {
      imageQuotaInFlight = { key, promise };
    }
    try {
      return await promise;
    } finally {
      if (imageQuotaInFlight && imageQuotaInFlight.promise === promise) {
        imageQuotaInFlight = null;
      }
    }
  }

  function buildFlowScriptArgs(pre, workspace, extraArgs = []) {
    const s = getSettings();
    const args = [
      ...pre,
      flowScript,
      '--ledger', flowLedgerPath,
      '--credits-cache', flowCreditsCachePath,
      '--max-per-hour', String(s.flowMaxPerHour),
      '--max-per-day', String(s.flowMaxPerDay),
      '--max-daily-credits', String(s.flowMaxDailyCredits),
      '--min-remaining-credits', String(s.flowMinCredits),
      '--min-gap-sec', String(s.flowMinGapSec),
      ...extraArgs,
    ];
    if (workspace) args.push('--workspace', workspace);
    return args;
  }

  function flowCacheKey(workspace) {
    const s = getSettings();
    return `${workspace}|${s.flowMaxPerHour}|${s.flowMaxPerDay}|${s.flowMaxDailyCredits}|${s.flowMinCredits}|${s.flowMinGapSec}`;
  }

  async function getFlowQuotaReport(workspaceUri, force = false) {
    const workspace = workspacePath(workspaceUri);
    const key = flowCacheKey(workspace);
    const now = Date.now();

    if (!force && flowQuotaCache.data && flowQuotaCache.key === key && (now - flowQuotaCache.fetchedAt) < flowTtlMs) {
      return flowQuotaCache.data;
    }
    if (!force && flowQuotaInFlight && flowQuotaInFlight.key === key) {
      return flowQuotaInFlight.promise;
    }

    const promise = (async () => {
      mkdirSync(flowDataDir, { recursive: true });
      const { cmd, pre } = await findPython();
      const args = buildFlowScriptArgs(pre, workspace);
      const out = await runCommand(cmd, args, 15000);
      const parsed = JSON.parse(out);
      flowQuotaCache = { key, fetchedAt: Date.now(), data: parsed };
      return parsed;
    })();

    flowQuotaInFlight = { key, promise };
    try {
      return await promise;
    } finally {
      if (flowQuotaInFlight && flowQuotaInFlight.promise === promise) {
        flowQuotaInFlight = null;
      }
    }
  }

  async function syncFlowCredits(workspaceUri, forceIgnoreBusy = false) {
    if (flowSyncInFlight) return flowSyncInFlight;
    const workspace = workspacePath(workspaceUri);
    const key = flowCacheKey(workspace);

    const promise = (async () => {
      mkdirSync(flowDataDir, { recursive: true });
      const { cmd, pre } = await findPython();
      const extra = ['--sync-credits'];
      if (forceIgnoreBusy) extra.push('--force-sync');
      const args = buildFlowScriptArgs(pre, workspace, extra);
      const out = await runCommand(cmd, args, 60000);
      const parsed = JSON.parse(out);
      flowQuotaCache = { key, fetchedAt: Date.now(), data: parsed };
      return parsed;
    })();

    flowSyncInFlight = promise;
    try {
      return await promise;
    } finally {
      if (flowSyncInFlight === promise) {
        flowSyncInFlight = null;
      }
    }
  }

  function invalidateImageCache() {
    imageQuotaCache = { key: '', fetchedAt: 0, data: null };
  }

  function invalidateFlowCache() {
    flowQuotaCache = { key: '', fetchedAt: 0, data: null };
  }

  return {
    getContextReport,
    getImageQuotaReport,
    getFlowQuotaReport,
    syncFlowCredits,
    invalidateImageCache,
    invalidateFlowCache,
  };
}
