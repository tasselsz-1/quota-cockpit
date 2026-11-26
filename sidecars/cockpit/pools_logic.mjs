// Backward-compatible facade re-exporting model-pool and weekly-recovery helpers.

export {
  ROLE_RULES,
  classifyModelRole,
  poolMetaForProvider,
  baseFamilyName,
  pctToHealth,
  parseQuotaSummary,
  buildPoolsPayload,
  resolveActivePool,
} from './lib/pools_models.mjs';

export {
  WINDOW_7D_SEC,
  createEmptyQuotaHistory,
  normalizeQuotaHistory,
  recordQuotaSnapshot,
  buildWeeklyRecovery,
  enrichPoolsWithWeeklyRecovery,
} from './lib/pools_recovery.mjs';
