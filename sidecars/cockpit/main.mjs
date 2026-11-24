// Sidecar backend for the Antigravity Quota & Context Cockpit panel.
//
// Combines three real-time views in a single zero-token AUX pane:
// 1. Image Generation Quota (`quota_report.py`): 5h rolling/429-locked window,
//    24h/7d stats, EOF tracking, empirical 429 ceiling calibration, and
//    optional workspace `api_call_ledger.json` linking.
// 2. Live Dual Quota Pools (`GetAvailableModels` + `RetrieveUserQuotaSummary` RPCs):
//    Pool A (Google Gemini) vs Pool B (Claude / OpenAI) 5-hour & weekly remaining %,
//    reset times, active model pool highlighting, and cross-pool routing advice.
// 3. Current Conversation Context (`context_report.py`): exact protobuf token
//    breakdown, 100-dot grid, checkpoints, and loaded files.

import { execFile } from 'node:child_process';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Response, SidecarApp } from 'sidecar_sdk';
import { buildPoolsPayload, resolveActivePool } from './pools_logic.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const CONTEXT_SCRIPT = join(HERE, 'context_report.py');
const QUOTA_SCRIPT = join(HERE, 'quota_report.py');
const GEMINI_DIR = process.env.ANTIGRAVITY_GEMINI_DIR || join(homedir(), '.gemini');
const CONVERSATIONS = join(GEMINI_DIR, 'antigravity', 'conversations');
const DATA_DIR = process.env.ANTIGRAVITY_EXECUTABLE_DATA_DIR || join(HERE, '.data');
const CATALOG_PATH = join(DATA_DIR, 'model-catalog.json');
const SETTINGS_PATH = join(DATA_DIR, 'settings.json');
const IMAGE_CACHE_PATH = join(DATA_DIR, 'image-scan-cache.json');

const POOLS_TTL_MS = 30 * 1000;
const POOLS_RETRY_MS = 5 * 1000;
const IMAGE_TTL_MS = 12 * 1000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const DEFAULT_SETTINGS = {
  softCap: 54,
  hardCap: 60,
  tokenCap: 0,
  countMode: 'attempts',
  anchorMode: 'auto',
};

const app = new SidecarApp();

// --- python runner ------------------------------------------------------------

const PYTHON_CANDIDATES = process.platform === 'win32'
  ? [['python', []], ['py', ['-3']], ['python3', []]]
  : [['python3', []], ['python', []]];
let python = null;

function run(cmd, args, timeout = 25000) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, {
      timeout,
      maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    }, (err, stdout, stderr) => {
      if (err) {
        reject(new Error((stderr || err.message || 'python error').trim()));
      } else {
        resolve(stdout);
      }
    });
  });
}

async function findPython() {
  if (python) return python;
  for (const [cmd, pre] of PYTHON_CANDIDATES) {
    try {
      const out = await run(cmd, [...pre, '--version'], 5000);
      if (/^Python 3\./.test(out.trim())) {
        python = { cmd, pre };
        return python;
      }
    } catch {
      // try next candidate
    }
  }
  throw new Error('未检测到 Python 3，请安装后重启 Antigravity。');
}

// --- persistent settings ------------------------------------------------------

function clampInt(val, min, max, fallback) {
  const n = Number(val);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

function normalizeSettings(raw) {
  const obj = raw && typeof raw === 'object' ? raw : {};
  const softCap = clampInt(obj.softCap ?? obj.soft_cap, 1, 2000, DEFAULT_SETTINGS.softCap);
  const hardCap = clampInt(obj.hardCap ?? obj.hard_cap, softCap, 2000, Math.max(softCap, DEFAULT_SETTINGS.hardCap));
  const tokenCap = clampInt(obj.tokenCap ?? obj.token_cap, 0, 50_000_000, DEFAULT_SETTINGS.tokenCap);
  const modeRaw = String(obj.countMode ?? obj.count_mode ?? DEFAULT_SETTINGS.countMode).trim();
  const countMode = modeRaw === 'ok_only' ? 'ok_only' : 'attempts';
  const anchorRaw = String(obj.anchorMode ?? obj.anchor_mode ?? DEFAULT_SETTINGS.anchorMode).trim();
  const anchorMode = anchorRaw === 'manual' ? 'manual' : 'auto';
  return { softCap, hardCap, tokenCap, countMode, anchorMode };
}

function loadSettings() {
  try {
    if (existsSync(SETTINGS_PATH)) {
      return normalizeSettings(JSON.parse(readFileSync(SETTINGS_PATH, 'utf8')));
    }
  } catch {
    // fallback to defaults
  }
  return { ...DEFAULT_SETTINGS };
}

function saveSettings(next) {
  const clean = normalizeSettings(next);
  mkdirSync(DATA_DIR, { recursive: true });
  const tmp = `${SETTINGS_PATH}.tmp.${process.pid}`;
  writeFileSync(tmp, JSON.stringify(clean, null, 2), 'utf8');
  renameSync(tmp, SETTINGS_PATH);
  return clean;
}

let currentSettings = loadSettings();

// --- language server RPC & live model pools -----------------------------------

function lsRequest(method, body) {
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

let poolsCache = null;
let poolsFetchedAt = 0;
let poolsLastError = '';
let poolsInFlight = null;

async function refreshPools(force = false) {
  const now = Date.now();
  const ttl = poolsLastError ? POOLS_RETRY_MS : POOLS_TTL_MS;
  if (!force && poolsCache && (now - poolsFetchedAt) < ttl) {
    return poolsCache;
  }
  if (poolsInFlight) return poolsInFlight;

  poolsInFlight = (async () => {
    try {
      const [modelsResult, summaryResult] = await Promise.allSettled([
        lsRequest('GetAvailableModels', {
          metadata: { ideName: 'antigravity', extensionName: 'antigravity' },
        }),
        lsRequest('RetrieveUserQuotaSummary', {}),
      ]);

      const modelsResp = modelsResult.status === 'fulfilled' ? modelsResult.value : null;
      const summaryResp = summaryResult.status === 'fulfilled' ? summaryResult.value : null;

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
      const { catalog, payload } = buildPoolsPayload(models, summaryResp);

      if (Object.keys(catalog).length > 0) {
        mkdirSync(DATA_DIR, { recursive: true });
        const serialized = JSON.stringify(catalog);
        let needsWrite = true;
        try {
          if (existsSync(CATALOG_PATH) && readFileSync(CATALOG_PATH, 'utf8') === serialized) {
            needsWrite = false;
          }
        } catch {
          needsWrite = true;
        }
        if (needsWrite) {
          const tmp = `${CATALOG_PATH}.tmp.${process.pid}`;
          writeFileSync(tmp, serialized, 'utf8');
          renameSync(tmp, CATALOG_PATH);
        }
      }

      poolsCache = payload;
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

// --- context report -----------------------------------------------------------

const contextCache = new Map(); // conversationId -> { key, report }

function workspacePath(uri) {
  if (!uri) return '';
  if (!uri.startsWith('file:')) return uri;
  let p = decodeURIComponent(uri.replace(/^file:\/\//, ''));
  if (/^\/[A-Za-z]:/.test(p)) p = p.slice(1);
  return p;
}

function fileStamp(path) {
  try {
    const s = statSync(path);
    return `${s.mtimeMs}:${s.size}`;
  } catch {
    return '-';
  }
}

async function getContextReport(conversationId, workspaceUri, force = false) {
  if (!UUID.test(conversationId || '')) {
    return { error: 'no-conversation' };
  }
  const db = join(CONVERSATIONS, `${conversationId}.db`);
  if (!existsSync(db)) {
    return { error: 'no-database' };
  }
  const workspace = workspacePath(workspaceUri);
  const key = [fileStamp(db), fileStamp(`${db}-wal`), workspace, fileStamp(CATALOG_PATH)].join('|');
  const cached = contextCache.get(conversationId);
  if (!force && cached && cached.key === key) return cached.report;

  const { cmd, pre } = await findPython();
  const args = [...pre, CONTEXT_SCRIPT, '--db', db, '--json'];
  if (existsSync(CATALOG_PATH)) args.push('--model-catalog', CATALOG_PATH);
  if (workspace) args.push('--workspace', workspace);
  const out = await run(cmd, args);
  const result = { ...JSON.parse(out), updatedAt: new Date().toISOString() };
  contextCache.set(conversationId, { key, report: result });
  return result;
}

// --- image quota report -------------------------------------------------------

let imageQuotaCache = { key: '', fetchedAt: 0, data: null };
let imageQuotaInFlight = null;

async function getImageQuotaReport(workspaceUri, force = false) {
  const workspace = workspacePath(workspaceUri);
  const s = currentSettings;
  const key = `${workspace}|${s.softCap}|${s.hardCap}|${s.tokenCap}|${s.countMode}|${s.anchorMode}`;
  const now = Date.now();

  if (!force && imageQuotaCache.data && imageQuotaCache.key === key && (now - imageQuotaCache.fetchedAt) < IMAGE_TTL_MS) {
    return imageQuotaCache.data;
  }
  if (!force && imageQuotaInFlight && imageQuotaInFlight.key === key) {
    return imageQuotaInFlight.promise;
  }

  const promise = (async () => {
    mkdirSync(DATA_DIR, { recursive: true });
    const { cmd, pre } = await findPython();
    const args = [
      ...pre,
      QUOTA_SCRIPT,
      '--conv-dir', CONVERSATIONS,
      '--soft-cap', String(s.softCap),
      '--hard-cap', String(s.hardCap),
      '--token-cap', String(s.tokenCap),
      '--count-mode', s.countMode,
      '--anchor-mode', s.anchorMode,
      '--cache', IMAGE_CACHE_PATH,
    ];
    if (workspace) args.push('--workspace', workspace);
    const out = await run(cmd, args, 30000);
    const parsed = JSON.parse(out);
    imageQuotaCache = { key, fetchedAt: Date.now(), data: parsed };
    return parsed;
  })();

  imageQuotaInFlight = { key, promise };
  try {
    return await promise;
  } finally {
    if (imageQuotaInFlight && imageQuotaInFlight.promise === promise) {
      imageQuotaInFlight = null;
    }
  }
}

// --- routes -------------------------------------------------------------------

app.page('/', () => new Response(readFileSync(join(HERE, 'index.html'), 'utf8'), { contentType: 'text/html' }));

app.api('/api/dashboard', async (data) => {
  const conversationId = String(data.conversationId || '').trim();
  const workspace = String(data.workspace || '').trim();
  const force = String(data.force || '') === '1' || data.force === true;

  const pools = await refreshPools(force);
  const [context, imageQuota] = await Promise.all([
    getContextReport(conversationId, workspace, force).catch((err) => ({ error: 'failed', message: err.message })),
    getImageQuotaReport(workspace, force).catch((err) => ({ error: 'failed', message: err.message })),
  ]);

  const activeModelId = context && !context.error ? (context.model_id || '') : '';
  const activeMeta = resolveActivePool(pools, activeModelId);

  return {
    serverNow: Math.floor(Date.now() / 1000),
    updatedAt: new Date().toISOString(),
    settings: currentSettings,
    context,
    pools: {
      ...pools,
      ...activeMeta,
    },
    imageQuota,
  };
}, 'GET');

app.api('/api/settings', async (data) => {
  try {
    currentSettings = saveSettings(data);
    imageQuotaCache = { key: '', fetchedAt: 0, data: null };
    const workspace = String(data.workspace || '').trim();
    const imageQuota = await getImageQuotaReport(workspace, true).catch((err) => ({ error: 'failed', message: err.message }));
    return {
      ok: true,
      settings: currentSettings,
      imageQuota,
      serverNow: Math.floor(Date.now() / 1000),
    };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}, 'POST');

// Backward-compatible alias for pure context report
app.api('/api/report', async (data) => {
  try {
    await refreshPools(false);
    return await getContextReport(data.conversationId, data.workspace);
  } catch (err) {
    return { error: 'failed', message: err.message };
  }
}, 'GET');

app.run();
refreshPools(true);
