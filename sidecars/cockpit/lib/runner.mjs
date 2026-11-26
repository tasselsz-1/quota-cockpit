// Python runner, path/file-stamp helpers, and static web asset registrar.

import { execFile } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, relative, sep } from 'node:path';

const PYTHON_CANDIDATES = process.platform === 'win32'
  ? [['python', []], ['py', ['-3']], ['python3', []]]
  : [['python3', []], ['python', []]];
let python = null;

export const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.svg': 'image/svg+xml; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
};

export function runCommand(cmd, args, timeout = 25000) {
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

export async function findPython() {
  if (python) return python;
  for (const [cmd, pre] of PYTHON_CANDIDATES) {
    try {
      const out = await runCommand(cmd, [...pre, '--version'], 5000);
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

export function workspacePath(uri) {
  if (!uri) return '';
  if (!uri.startsWith('file:')) return uri;
  let p = decodeURIComponent(uri.replace(/^file:\/\//, ''));
  if (/^\/[A-Za-z]:/.test(p)) p = p.slice(1);
  return p;
}

export function fileStamp(path) {
  try {
    const s = statSync(path);
    return `${s.mtimeMs}:${s.size}`;
  } catch {
    return '-';
  }
}

export function collectWebAssets(webDir) {
  if (!webDir || !existsSync(webDir)) return [];
  const entries = [];

  function walk(dir) {
    const items = readdirSync(dir, { withFileTypes: true })
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const item of items) {
      const fullPath = join(dir, item.name);
      if (item.isDirectory()) {
        walk(fullPath);
        continue;
      }
      if (!item.isFile()) continue;
      const ext = extname(item.name).toLowerCase();
      const contentType = CONTENT_TYPES[ext];
      if (!contentType) continue;
      const posixRelPath = relative(webDir, fullPath).split(sep).join('/');
      if (posixRelPath === 'index.html') {
        entries.push({ route: '/', filePath: fullPath, contentType });
      }
      entries.push({
        route: `/web/${posixRelPath}`,
        filePath: fullPath,
        contentType,
      });
    }
  }

  walk(webDir);
  return entries;
}

export function registerWebAssets(app, webDir, ResponseClass) {
  const entries = collectWebAssets(webDir);
  const routes = [];
  for (const entry of entries) {
    app.page(entry.route, () => new ResponseClass(
      readFileSync(entry.filePath, 'utf8'),
      {
        contentType: entry.contentType,
        headers: { 'Cache-Control': 'no-store' },
      },
    ));
    routes.push(entry.route);
  }
  return routes;
}
