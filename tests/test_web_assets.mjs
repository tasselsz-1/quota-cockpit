import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');
const COCKPIT_DIR = path.join(ROOT, 'sidecars', 'cockpit');
const WEB_DIR = path.join(COCKPIT_DIR, 'web');
const JS_DIR = path.join(WEB_DIR, 'js');

const EXPECTED_JS_FILES = [
  'state.js',
  'formatters.js',
  'render-header.js',
  'render-pools.js',
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

function parseExports(source) {
  const exported = new Set();
  const declRe = /export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][\w$]*)/g;
  for (const m of source.matchAll(declRe)) {
    exported.add(m[1]);
  }
  const listRe = /export\s*\{([^}]+)\}/g;
  for (const m of source.matchAll(listRe)) {
    for (const part of m[1].split(',')) {
      const cleaned = part.trim().split(/\s+as\s+/);
      const name = (cleaned[1] || cleaned[0] || '').trim();
      if (name) exported.add(name);
    }
  }
  return exported;
}

function parseImports(source) {
  const imports = [];
  const importRe = /import\s*\{([\s\S]*?)\}\s*from\s*['"](\.\/[^'"]+)['"]\s*;/g;
  for (const m of source.matchAll(importRe)) {
    const names = m[1]
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const parts = s.split(/\s+as\s+/);
        return { imported: parts[0].trim(), local: (parts[1] || parts[0]).trim() };
      });
    imports.push({ spec: m[2], names });
  }
  return imports;
}

function stripCommentsAndStrings(code) {
  return code
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/\/\/[^\n]*/g, ' ')
    .replace(/`(?:\\[\s\S]|\$\{[^}]*\}|[^`\\])*`/g, (tpl) => {
      // Preserve ${...} expressions so identifier references inside templates are still visible
      const exprs = [];
      const re = /\$\{([^}]*)\}/g;
      for (const m of tpl.matchAll(re)) exprs.push(m[1]);
      return ' ' + exprs.join(' ') + ' ';
    })
    .replace(/'(?:\\.|[^'\\])*'/g, ' ')
    .replace(/"(?:\\.|[^"\\])*"/g, ' ');
}

test('all expected web assets exist and are non-empty', () => {
  for (const rel of ['index.html', 'styles.css', ...EXPECTED_JS_FILES.map((f) => path.join('js', f))]) {
    const full = path.join(WEB_DIR, rel);
    assert.ok(fs.existsSync(full), `Missing web asset: ${rel}`);
    const content = fs.readFileSync(full, 'utf8');
    assert.ok(content.trim().length > 0, `Empty web asset: ${rel}`);
  }
});

test('every /web/... link and script in web/index.html exists on disk', () => {
  const html = readText('index.html');
  const refs = [...html.matchAll(/(?:href|src)=["'](\/web\/[^"']+)["']/g)].map((m) => m[1]);
  assert.ok(refs.includes('/web/styles.css'), 'index.html must load /web/styles.css');
  assert.ok(refs.includes('/web/js/app.js'), 'index.html must load /web/js/app.js');
  for (const ref of refs) {
    const rel = ref.replace(/^\/web\//, '');
    assert.ok(fs.existsSync(path.join(WEB_DIR, rel)), `Referenced asset missing on disk: ${ref}`);
  }
});

test('all web/js/*.js pass node --check syntax validation', () => {
  for (const file of EXPECTED_JS_FILES) {
    const full = path.join(JS_DIR, file);
    execFileSync(process.execPath, ['--check', full], { stdio: 'pipe' });
  }
});

test('relative ESM imports resolve, targets export every imported symbol, and dependency graph is acyclic', () => {
  const exportsByFile = new Map();
  const importsByFile = new Map();
  const graph = new Map();

  for (const file of EXPECTED_JS_FILES) {
    const src = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
    exportsByFile.set(file, parseExports(src));
    const imps = parseImports(src);
    importsByFile.set(file, imps);
    graph.set(file, []);

    for (const imp of imps) {
      const target = path.basename(imp.spec);
      const resolved = path.resolve(JS_DIR, imp.spec);
      assert.ok(fs.existsSync(resolved), `${file} imports missing module ${imp.spec}`);
      assert.ok(exportsByFile.has(target) || EXPECTED_JS_FILES.includes(target), `Unexpected module ${target}`);
      graph.get(file).push(target);
    }
  }

  for (const [file, imps] of importsByFile.entries()) {
    for (const imp of imps) {
      const target = path.basename(imp.spec);
      const targetExports = exportsByFile.get(target);
      for (const { imported } of imp.names) {
        assert.ok(
          targetExports.has(imported),
          `${file} imports '${imported}' from ${imp.spec}, but ${target} does not export it`,
        );
      }
    }
  }

  // Cycle detection via DFS
  const visitState = new Map(); // 0/undefined=unvisited, 1=visiting, 2=done
  function dfs(node, stack) {
    visitState.set(node, 1);
    for (const dep of graph.get(node) || []) {
      const st = visitState.get(dep);
      if (st === 1) {
        assert.fail(`Circular ESM dependency detected: ${[...stack, dep].join(' -> ')}`);
      }
      if (!st) dfs(dep, [...stack, dep]);
    }
    visitState.set(node, 2);
  }
  for (const file of EXPECTED_JS_FILES) {
    if (!visitState.get(file)) dfs(file, [file]);
  }
});

test('no bare undeclared cross-module symbol calls remain in web/js/*.js', () => {
  const exportsByFile = new Map();
  const allCrossModuleSymbols = new Set();

  for (const file of EXPECTED_JS_FILES) {
    const src = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
    const exps = parseExports(src);
    exportsByFile.set(file, exps);
    for (const sym of exps) {
      if (sym !== '$') allCrossModuleSymbols.add(sym);
    }
  }

  for (const file of EXPECTED_JS_FILES) {
    const src = fs.readFileSync(path.join(JS_DIR, file), 'utf8');
    const ownExports = exportsByFile.get(file);
    const importedLocals = new Set();
    for (const imp of parseImports(src)) {
      for (const { local } of imp.names) importedLocals.add(local);
    }
    // Strip import/export header lines and string/comment bodies for bare call/ref check
    const body = stripCommentsAndStrings(
      src.replace(/^\s*import\s+[\s\S]*?;\s*$/gm, ''),
    );
    for (const sym of allCrossModuleSymbols) {
      if (ownExports.has(sym) || importedLocals.has(sym)) continue;
      // Check bare function call `sym(` or uppercase constant reference
      const callRe = new RegExp(`(?<![.\\w$])\\b${sym}\\s*\\(`);
      assert.ok(!callRe.test(body), `${file} calls '${sym}(...)' without defining or importing it`);
      if (/^[A-Z][A-Z0-9_]+$/.test(sym)) {
        const constRe = new RegExp(`(?<![.\\w$])\\b${sym}\\b(?!\\s*:)`);
        assert.ok(!constRe.test(body), `${file} references constant '${sym}' without defining or importing it`);
      }
    }
  }
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

test('preserves live-plugin ledger text, actions callback wiring, and setTab order', () => {
  const imgSrc = readText(path.join('js', 'render-image.js'));
  assert.ok(
    imgSrc.includes('当前工作区未发现可选的 <span class="mono">api_call_ledger.json</span> 账本文件。'),
    'Missing expected ledger empty-state string',
  );

  const actionsSrc = readText(path.join('js', 'actions.js'));
  assert.ok(actionsSrc.includes('export function setActionsCallbacks'), 'actions.js must export setActionsCallbacks');
  assert.ok(!actionsSrc.includes('./app.js'), 'actions.js must not import app.js');

  const appSrc = readText(path.join('js', 'app.js'));
  assert.ok(
    appSrc.includes("['overview', 'image', 'pools', 'context', 'flow']"),
    'app.js setTab allowed list must match reordered tabs',
  );
  assert.ok(appSrc.includes('setActionsCallbacks({ render })'), 'app.js must wire render callback into actions.js');
});
