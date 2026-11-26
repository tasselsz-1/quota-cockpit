// Persistent settings and official quota snapshot history stores.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { dirname } from 'node:path';

export const DEFAULT_SETTINGS = {
  softCap: 54,
  hardCap: 60,
  tokenCap: 0,
  countMode: 'attempts',
  anchorMode: 'auto',
  flowMaxPerHour: 6,
  flowMaxPerDay: 20,
  flowMaxDailyCredits: 400,
  flowMinCredits: 100,
  flowMinGapSec: 35,
};

export function clampInt(val, min, max, fallback) {
  const n = Number(val);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

export function normalizeSettings(raw, base = DEFAULT_SETTINGS) {
  const b = base && typeof base === 'object' ? base : DEFAULT_SETTINGS;
  const obj = raw && typeof raw === 'object' ? raw : {};
  const softCap = clampInt(obj.softCap ?? obj.soft_cap, 1, 2000, b.softCap ?? DEFAULT_SETTINGS.softCap);
  const hardCap = clampInt(
    obj.hardCap ?? obj.hard_cap,
    softCap,
    2000,
    Math.max(softCap, b.hardCap ?? DEFAULT_SETTINGS.hardCap),
  );
  const tokenCap = clampInt(obj.tokenCap ?? obj.token_cap, 0, 50_000_000, b.tokenCap ?? DEFAULT_SETTINGS.tokenCap);
  const modeRaw = String(obj.countMode ?? obj.count_mode ?? b.countMode ?? DEFAULT_SETTINGS.countMode).trim();
  const countMode = modeRaw === 'ok_only' ? 'ok_only' : 'attempts';
  const anchorRaw = String(obj.anchorMode ?? obj.anchor_mode ?? b.anchorMode ?? DEFAULT_SETTINGS.anchorMode).trim();
  const anchorMode = anchorRaw === 'manual' ? 'manual' : 'auto';
  const flowMaxPerHour = clampInt(
    obj.flowMaxPerHour ?? obj.flow_max_per_hour,
    1,
    200,
    b.flowMaxPerHour ?? DEFAULT_SETTINGS.flowMaxPerHour,
  );
  const flowMaxPerDay = clampInt(
    obj.flowMaxPerDay ?? obj.flow_max_per_day,
    flowMaxPerHour,
    1000,
    Math.max(flowMaxPerHour, b.flowMaxPerDay ?? DEFAULT_SETTINGS.flowMaxPerDay),
  );
  const flowMaxDailyCredits = clampInt(
    obj.flowMaxDailyCredits ?? obj.flow_max_daily_credits,
    10,
    50000,
    b.flowMaxDailyCredits ?? DEFAULT_SETTINGS.flowMaxDailyCredits,
  );
  const flowMinCredits = clampInt(
    obj.flowMinCredits ?? obj.flow_min_credits,
    0,
    10000,
    b.flowMinCredits ?? DEFAULT_SETTINGS.flowMinCredits,
  );
  const flowMinGapSec = clampInt(
    obj.flowMinGapSec ?? obj.flow_min_gap_sec,
    0,
    600,
    b.flowMinGapSec ?? DEFAULT_SETTINGS.flowMinGapSec,
  );
  return {
    softCap,
    hardCap,
    tokenCap,
    countMode,
    anchorMode,
    flowMaxPerHour,
    flowMaxPerDay,
    flowMaxDailyCredits,
    flowMinCredits,
    flowMinGapSec,
  };
}

export function createSettingsStore(settingsPath) {
  const dir = dirname(settingsPath);

  function load() {
    try {
      if (existsSync(settingsPath)) {
        return normalizeSettings(JSON.parse(readFileSync(settingsPath, 'utf8')));
      }
    } catch {
      // fallback to defaults
    }
    return { ...DEFAULT_SETTINGS };
  }

  let current = load();

  function save(next, base = current) {
    const clean = normalizeSettings(next, base);
    mkdirSync(dir, { recursive: true });
    const tmp = `${settingsPath}.tmp.${process.pid}`;
    writeFileSync(tmp, JSON.stringify(clean, null, 2), 'utf8');
    renameSync(tmp, settingsPath);
    current = clean;
    return clean;
  }

  return {
    get: () => current,
    load,
    save,
  };
}

export function createQuotaHistoryStore(
  historyPath,
  { createEmptyQuotaHistory, normalizeQuotaHistory },
) {
  const dir = dirname(historyPath);

  function load() {
    try {
      if (existsSync(historyPath)) {
        return normalizeQuotaHistory(JSON.parse(readFileSync(historyPath, 'utf8')));
      }
    } catch {
      // fallback to empty history
    }
    return createEmptyQuotaHistory();
  }

  let current = load();

  function save(hist) {
    current = hist;
    try {
      mkdirSync(dir, { recursive: true });
      const tmp = `${historyPath}.tmp.${process.pid}.${Date.now()}`;
      writeFileSync(tmp, JSON.stringify(hist), 'utf8');
      renameSync(tmp, historyPath);
    } catch (err) {
      console.error('[quota-cockpit] Failed to persist quota history:', err.message);
    }
  }

  return {
    get: () => current,
    load,
    save,
  };
}
