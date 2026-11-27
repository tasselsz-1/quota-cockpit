import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  CONTENT_TYPES,
  collectWebAssets,
  registerWebAssets,
  workspacePath,
} from '../sidecars/cockpit/lib/runner.mjs';
import {
  DEFAULT_SETTINGS,
  FLOW_POLICY_VERSION,
  clampInt,
  migrateLegacyFlowDefaults,
  normalizeSettings,
} from '../sidecars/cockpit/lib/settings.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const WEB_DIR = path.resolve(__dirname, '..', 'sidecars', 'cockpit', 'web');

const EXPECTED_JS_ROUTES = [
  '/web/js/state.js',
  '/web/js/formatters.js',
  '/web/js/render-header.js',
  '/web/js/render-pools.js',
  '/web/js/render-image.js',
  '/web/js/render-context.js',
  '/web/js/render-flow.js',
  '/web/js/render-overview.js',
  '/web/js/actions.js',
  '/web/js/app.js',
];

class MockResponse {
  constructor(body, init = {}) {
    this.body = body;
    this.contentType = init.contentType || '';
    this.headers = init.headers || {};
  }
}

test('collectWebAssets and registerWebAssets wire /, /web/index.html, styles.css, and all 10 web/js/*.js routes', () => {
  const collected = collectWebAssets(WEB_DIR);
  const collectedRoutes = collected.map((e) => e.route);

  assert.ok(collectedRoutes.includes('/'));
  assert.ok(collectedRoutes.includes('/web/index.html'));
  assert.ok(collectedRoutes.includes('/web/styles.css'));
  for (const route of EXPECTED_JS_ROUTES) {
    assert.ok(collectedRoutes.includes(route), `Missing collected route ${route}`);
  }

  const pages = new Map();
  const mockApp = {
    page(route, handler) {
      pages.set(route, handler);
    },
  };

  const registered = registerWebAssets(mockApp, WEB_DIR, MockResponse);
  assert.deepEqual(registered, collectedRoutes);

  const expectedTypes = {
    '/': CONTENT_TYPES['.html'],
    '/web/index.html': CONTENT_TYPES['.html'],
    '/web/styles.css': CONTENT_TYPES['.css'],
    '/web/js/app.js': CONTENT_TYPES['.js'],
  };

  for (const [route, expectedType] of Object.entries(expectedTypes)) {
    const handler = pages.get(route);
    assert.ok(handler, `Handler missing for ${route}`);
    const res = handler();
    assert.equal(res.contentType, expectedType);
    assert.equal(res.headers['Cache-Control'], 'no-store');
    assert.ok(typeof res.body === 'string' && res.body.trim().length > 0, `Empty body for ${route}`);
  }

  for (const route of EXPECTED_JS_ROUTES) {
    const handler = pages.get(route);
    assert.ok(handler, `Handler missing for ${route}`);
    const res = handler();
    assert.equal(res.contentType, 'application/javascript; charset=utf-8');
    assert.equal(res.headers['Cache-Control'], 'no-store');
    assert.ok(typeof res.body === 'string' && res.body.trim().length > 0, `Empty body for ${route}`);
  }
});

test('normalizeSettings incremental merge preserves flow settings when saving image settings and vice versa', () => {
  const afterImageSave = normalizeSettings(
    { softCap: 42, hardCap: 55, countMode: 'ok_only', anchorMode: 'manual' },
    DEFAULT_SETTINGS,
  );
  assert.equal(afterImageSave.softCap, 42);
  assert.equal(afterImageSave.hardCap, 55);
  assert.equal(afterImageSave.countMode, 'ok_only');
  assert.equal(afterImageSave.anchorMode, 'manual');
  assert.equal(afterImageSave.flowPolicyVersion, FLOW_POLICY_VERSION);
  assert.equal(afterImageSave.flowMaxRecaptcha10m, DEFAULT_SETTINGS.flowMaxRecaptcha10m);
  assert.equal(afterImageSave.flowMaxPerHour, DEFAULT_SETTINGS.flowMaxPerHour);
  assert.equal(afterImageSave.flowMaxPerDay, DEFAULT_SETTINGS.flowMaxPerDay);
  assert.equal(afterImageSave.flowMaxDailyCredits, DEFAULT_SETTINGS.flowMaxDailyCredits);
  assert.equal(afterImageSave.flowMinCredits, DEFAULT_SETTINGS.flowMinCredits);
  assert.equal(afterImageSave.flowMinGapSec, DEFAULT_SETTINGS.flowMinGapSec);

  const afterFlowSave = normalizeSettings(
    { flowMaxRecaptcha10m: 6, flowMaxPerHour: 0, flowMaxPerDay: 30, flowMaxDailyCredits: 500, flowMinCredits: 150, flowMinGapSec: 45 },
    afterImageSave,
  );
  assert.equal(afterFlowSave.softCap, 42);
  assert.equal(afterFlowSave.hardCap, 55);
  assert.equal(afterFlowSave.countMode, 'ok_only');
  assert.equal(afterFlowSave.anchorMode, 'manual');
  assert.equal(afterFlowSave.flowMaxRecaptcha10m, 6);
  assert.equal(afterFlowSave.flowMaxPerHour, 0);
  assert.equal(afterFlowSave.flowMaxPerDay, 30);
  assert.equal(afterFlowSave.flowMaxDailyCredits, 500);
  assert.equal(afterFlowSave.flowMinCredits, 150);
  assert.equal(afterFlowSave.flowMinGapSec, 45);
});

test('migrateLegacyFlowDefaults upgrades exact v1 defaults while preserving custom user settings', () => {
  const legacyExact = {
    softCap: 54,
    hardCap: 60,
    flowMaxPerHour: 6,
    flowMaxPerDay: 20,
    flowMaxDailyCredits: 400,
    flowMinCredits: 100,
    flowMinGapSec: 35,
  };
  const upgraded = normalizeSettings(migrateLegacyFlowDefaults(legacyExact), DEFAULT_SETTINGS);
  assert.equal(upgraded.flowPolicyVersion, FLOW_POLICY_VERSION);
  assert.equal(upgraded.flowMaxRecaptcha10m, 8);
  assert.equal(upgraded.flowMaxPerHour, 0);
  assert.equal(upgraded.flowMaxPerDay, 36);
  assert.equal(upgraded.flowMaxDailyCredits, 600);

  const legacyCustom = {
    flowMaxPerHour: 10,
    flowMaxPerDay: 20,
    flowMaxDailyCredits: 400,
  };
  const keptCustom = normalizeSettings(migrateLegacyFlowDefaults(legacyCustom), DEFAULT_SETTINGS);
  assert.equal(keptCustom.flowMaxPerHour, 10);
  assert.equal(keptCustom.flowMaxPerDay, 20);
  assert.equal(keptCustom.flowMaxDailyCredits, 400);

  const alreadyV2 = {
    flowPolicyVersion: FLOW_POLICY_VERSION,
    flowMaxRecaptcha10m: 6,
    flowMaxPerHour: 6,
    flowMaxPerDay: 20,
    flowMaxDailyCredits: 400,
  };
  const keptV2 = normalizeSettings(migrateLegacyFlowDefaults(alreadyV2), DEFAULT_SETTINGS);
  assert.equal(keptV2.flowMaxRecaptcha10m, 6);
  assert.equal(keptV2.flowMaxPerHour, 6);
  assert.equal(keptV2.flowMaxPerDay, 20);
  assert.equal(keptV2.flowMaxDailyCredits, 400);
});

test('clampInt clamps within bounds, rounds numbers, and falls back on non-finite input', () => {
  assert.equal(clampInt(12.6, 1, 100, 50), 13);
  assert.equal(clampInt(-5, 0, 100, 50), 0);
  assert.equal(clampInt(999, 0, 100, 50), 100);
  assert.equal(clampInt('not-a-number', 1, 100, 50), 50);
  assert.equal(clampInt(undefined, 1, 100, 50), 50);
});

test('workspacePath decodes Windows file:/// URIs cleanly', () => {
  assert.equal(workspacePath('file:///e%3A/projects/my%20project'), 'e:/projects/my project');
  assert.equal(workspacePath('file:///C%3A/Users/demo'), 'C:/Users/demo');
  assert.equal(workspacePath('e:/plain/path'), 'e:/plain/path');
  assert.equal(workspacePath(''), '');
});
