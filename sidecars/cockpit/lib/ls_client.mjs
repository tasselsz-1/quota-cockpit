// Language Server RPC client and dual-pool quota/usage aggregation service.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { dirname } from 'node:path';
import { buildPoolsPayload } from './pools_models.mjs';
import { enrichPoolsWithWeeklyRecovery, recordQuotaSnapshot } from './pools_recovery.mjs';

export function lsRequest(method, body) {
  const addr = (process.env.ANTIGRAVITY_LS_ADDRESS || '').trim();
  const csrf = (process.env.ANTIGRAVITY_CSRF_TOKEN || '').trim();
  if (!addr || !csrf) return Promise.reject(new Error('Language Server 环境变量不可用'));
  const base = new URL(addr.startsWith('http') ? addr : `http://${addr}`);
  const data = JSON.stringify(body);
  const transport = base.protocol === 'https:' ? https : http;
  return new Promise((resolve, reject) => {
    const req = transport.request({
      hostname: base.hostname,
      port: base.port,
      path: `/exa.language_server_pb.LanguageServerService/${method}`,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Connect-Protocol-Version': '1',
        'x-codeium-csrf-token': csrf,
        'Content-Length': Buffer.byteLength(data),
      },
      timeout: 5000,
      rejectUnauthorized: false,
    }, (res) => {
      let text = '';
      res.on('data', (chunk) => { text += chunk; });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          reject(new Error(`${method}: HTTP ${res.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(text));
        } catch (e) {
          reject(e);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error(`${method}: timeout`)));
    req.on('error', reject);
    req.end(data);
  });
}

export function createPoolsService({
  conversationsDir,
  catalogPath,
  poolUsageScript,
  poolUsageCachePath,
  poolUsageTtlMs,
  poolsTtlMs,
  poolsRetryMs,
  findPython,
  runCommand,
  quotaHistoryStore,
}) {
  const usageDataDir = dirname(poolUsageCachePath);
  const catalogDataDir = dirname(catalogPath);

  let poolUsageCache = { fetchedAt: 0, data: null };
  let poolUsageInFlight = null;

  let poolsCache = null;
  let poolsFetchedAt = 0;
  let poolsLastError = '';
  let poolsInFlight = null;

  async function getPoolUsageReport(force = false) {
    const now = Date.now();
    if (!force && poolUsageCache.data && (now - poolUsageCache.fetchedAt) < poolUsageTtlMs) {
      return poolUsageCache.data;
    }
    if (!force && poolUsageInFlight) {
      return poolUsageInFlight;
    }
    const promise = (async () => {
      mkdirSync(usageDataDir, { recursive: true });
      const { cmd, pre } = await findPython();
      const args = [
        ...pre,
        poolUsageScript,
        '--conv-dir', conversationsDir,
        '--cache', poolUsageCachePath,
      ];
      const out = await runCommand(cmd, args, 25000);
      const parsed = JSON.parse(out);
      poolUsageCache = { fetchedAt: Date.now(), data: parsed };
      return parsed;
    })();
    poolUsageInFlight = promise;
    try {
      return await promise;
    } catch (err) {
      console.error('[quota-cockpit] Pool usage scan unavailable:', err.message);
      return poolUsageCache.data;
    } finally {
      if (poolUsageInFlight === promise) {
        poolUsageInFlight = null;
      }
    }
  }

  async function refreshPools(force = false) {
    const now = Date.now();
    const ttl = poolsLastError ? poolsRetryMs : poolsTtlMs;
    if (!force && poolsCache && (now - poolsFetchedAt) < ttl) {
      return poolsCache;
    }
    if (poolsInFlight) return poolsInFlight;

    poolsInFlight = (async () => {
      try {
        const [modelsResult, summaryResult, usageResult] = await Promise.allSettled([
          lsRequest('GetAvailableModels', {
            metadata: { ideName: 'antigravity', extensionName: 'antigravity' },
          }),
          lsRequest('RetrieveUserQuotaSummary', {}),
          getPoolUsageReport(force),
        ]);

        const modelsResp = modelsResult.status === 'fulfilled' ? modelsResult.value : null;
        const summaryResp = summaryResult.status === 'fulfilled' ? summaryResult.value : null;
        const poolUsageResp = usageResult.status === 'fulfilled' ? usageResult.value : null;

        if (!modelsResp && !summaryResp) {
          const msg = modelsResult.reason?.message || summaryResult.reason?.message || 'RPC unavailable';
          throw new Error(msg);
        }

        if (summaryResult.status === 'rejected') {
          console.error('[quota-cockpit] RetrieveUserQuotaSummary unavailable:', summaryResult.reason?.message);
        }
        if (modelsResult.status === 'rejected') {
          console.error('[quota-cockpit] GetAvailableModels unavailable:', modelsResult.reason?.message);
        }

        const models = modelsResp?.response?.models || modelsResp?.models || {};
        const nowMs = Date.now();
        const nowSec = Math.floor(nowMs / 1000);
        const { catalog, payload } = buildPoolsPayload(models, summaryResp, nowMs);

        const snap = recordQuotaSnapshot(quotaHistoryStore.get(), payload, nowSec);
        if (snap.changed) {
          quotaHistoryStore.save(snap.history);
        }
        const enrichedPayload = enrichPoolsWithWeeklyRecovery(
          payload,
          quotaHistoryStore.get(),
          poolUsageResp,
          nowSec,
        );

        if (Object.keys(catalog).length > 0) {
          mkdirSync(catalogDataDir, { recursive: true });
          const serialized = JSON.stringify(catalog);
          let needsWrite = true;
          try {
            if (existsSync(catalogPath) && readFileSync(catalogPath, 'utf8') === serialized) {
              needsWrite = false;
            }
          } catch {
            needsWrite = true;
          }
          if (needsWrite) {
            const tmp = `${catalogPath}.tmp.${process.pid}`;
            writeFileSync(tmp, serialized, 'utf8');
            renameSync(tmp, catalogPath);
          }
        }

        poolsCache = enrichedPayload;
        poolsFetchedAt = Date.now();
        poolsLastError = '';
        return poolsCache;
      } catch (err) {
        poolsLastError = err.message;
        poolsFetchedAt = Date.now();
        console.error('[quota-cockpit] Quota RPC unavailable:', err.message);
        if (poolsCache) {
          return { ...poolsCache, stale: true, error: err.message };
        }
        return {
          fetchedAt: new Date().toISOString(),
          quotaSummaryAvailable: false,
          quotaSummaryDescription: '',
          pools: [],
          modelToPool: {},
          imageModelEntry: null,
          imageModelPoolNote: '',
          error: err.message,
        };
      } finally {
        poolsInFlight = null;
      }
    })();

    return poolsInFlight;
  }

  return { getPoolUsageReport, refreshPools };
}
