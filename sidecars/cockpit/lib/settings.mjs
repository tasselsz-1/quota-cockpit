// Persistent settings and official quota snapshot history stores.

import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from 'node:fs';
import { dirname } from 'node:path';

export const FLOW_POLICY_VERSION = 2;

export const DEFAULT_SETTINGS = {
  softCap: 54,
  hardCap: 60,
  tokenCap: 0,
  countMode: 'attempts',
  anchorMode: 'auto',
  flowPolicyVersion: FLOW_POLICY_VERSION,
  flowMaxRecaptcha10m: 8,
  flowMaxPerHour: 0,
  flowMaxPerDay: 36,
  flowMaxDailyCredits: 600,
  flowMinCredits: 100,
  flowMinGapSec: 35,
};

export function clampInt(val, min, max, fallback) {
  const n = Number(val);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

export function migrateLegacyFlowDefaults(raw) {
  if (!raw || typeof raw !== 'object') return raw;
  const ver = Number(raw.flowPolicyVersion ?? raw.flow_policy_version ?? 0);
  if (Number.isFinite(ver) && ver >= FLOW_POLICY_VERSION) return raw;
  const hr = raw.flowMaxPerHour ?? raw.flow_max_per_hour;
  const day = raw.flowMaxPerDay ?? raw.flow_max_per_day;
  const cr = raw.flowMaxDailyCredits ?? raw.flow_max_daily_credits;
  // Auto-upgrade only exact v1 defaults (6 / 20 / 400); preserve any custom thresholds.
  if (Number(hr) === 6 && Number(day) === 20 && Number(cr) === 400) {
    return {
      ...raw,
      flowPolicyVersion: FLOW_POLICY_VERSION,
      flowMaxRecaptcha10m: raw.flowMaxRecaptcha10m ?? raw.flow_max_recaptcha_10m ?? DEFAULT_SETTINGS.flowMaxRecaptcha10m,
      flowMaxPerHour: DEFAULT_SETTINGS.flowMaxPerHour,
      flowMaxPerDay: DEFAULT_SETTINGS.flowMaxPerDay,
      flowMaxDailyCredits: DEFAULT_SETTINGS.flowMaxDailyCredits,
    };
  }
  return raw;
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
  const flowMaxRecaptcha10m = clampInt(
    obj.flowMaxRecaptcha10m ?? obj.flow_max_recaptcha_10m,
    1,
    10,
    b.flowMaxRecaptcha10m ?? DEFAULT_SETTINGS.flowMaxRecaptcha10m,
  );
  const flowMaxPerHour = clampInt(
    obj.flowMaxPerHour ?? obj.flow_max_per_hour,
    0,
    200,
    b.flowMaxPerHour ?? DEFAULT_SETTINGS.flowMaxPerHour,
  );
  const minFlowDay = Math.max(1, flowMaxPerHour);
  const flowMaxPerDay = clampInt(
    obj.flowMaxPerDay ?? obj.flow_max_per_day,
    minFlowDay,
    1000,
    Math.max(minFlowDay, b.flowMaxPerDay ?? DEFAULT_SETTINGS.flowMaxPerDay),
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
    flowPolicyVersion: FLOW_POLICY_VERSION,
    flowMaxRecaptcha10m,
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
        const parsed = JSON.parse(readFileSync(settingsPath, 'utf8'));
        return normalizeSettings(migrateLegacyFlowDefaults(parsed));
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
