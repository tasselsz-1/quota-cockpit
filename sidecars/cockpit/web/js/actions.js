(function (QC) {
  'use strict';

  const { state, $, esc, fmtCredits } = QC;

  let renderFn = () => {};

  function setActionsCallbacks({ render } = {}) {
    if (typeof render === 'function') renderFn = render;
  }

  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = '已复制';
      setTimeout(() => { btn.textContent = prev; }, 1200);
    }
  }

  async function saveSettings(opts) {
    const { anchorMode, softCap, hardCap, tokenCap, countMode } = opts;
    try {
      const res = await window.sidecar.fetch('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          anchorMode,
          softCap,
          hardCap,
          tokenCap,
          countMode,
          workspace: state.workspace,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `HTTP ${res.status}`);
      if (state.dash) {
        state.dash.settings = json.settings;
        if (json.imageQuota) state.dash.imageQuota = json.imageQuota;
        if (json.flowQuota) state.dash.flowQuota = json.flowQuota;
        if (json.serverNow) state.clockOffsetSec = json.serverNow - Math.floor(Date.now() / 1000);
        state.dash.updatedAt = new Date().toISOString();
      }
      state.toast = { id: Date.now(), msg: '已保存并重新计算', err: false };
      renderFn(true);
    } catch (err) {
      state.toast = { id: Date.now(), msg: `保存失败：${err.message}`, err: true };
      renderFn(true);
    }
  }

  async function saveFlowSettings(opts) {
    try {
      const res = await window.sidecar.fetch('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          ...opts,
          workspace: state.workspace,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `HTTP ${res.status}`);
      if (state.dash) {
        state.dash.settings = json.settings;
        if (json.flowQuota) state.dash.flowQuota = json.flowQuota;
        if (json.serverNow) state.clockOffsetSec = json.serverNow - Math.floor(Date.now() / 1000);
        state.dash.updatedAt = new Date().toISOString();
      }
      state.flowSettingsToast = { id: Date.now(), msg: '已保存 Flow 阈值', err: false };
      renderFn(true);
    } catch (err) {
      state.flowSettingsToast = { id: Date.now(), msg: `保存失败：${err.message}`, err: true };
      renderFn(true);
    }
  }

  async function syncFlowCredits() {
    if (state.flowSyncing) return;
    state.flowSyncing = true;
    state.flowToast = null;
    renderFn(true);
    try {
      const res = await window.sidecar.fetch('/api/flow/sync-credits', {
        method: 'POST',
        body: JSON.stringify({ workspace: state.workspace }),
      });
      const json = await res.json();
      if (state.dash && json.flowQuota) {
        state.dash.flowQuota = json.flowQuota;
        if (json.serverNow) state.clockOffsetSec = json.serverNow - Math.floor(Date.now() / 1000);
        state.dash.updatedAt = new Date().toISOString();
      }
      if (!res.ok || !json.ok) {
        throw new Error(json.error || json.sync?.error || `HTTP ${res.status}`);
      }
      const tot = json.sync?.total_credits ?? json.flowQuota?.credits?.synced_total;
      state.flowToast = { id: Date.now(), msg: `已同步 Flow 真实余额：${fmtCredits(tot)} 积分`, err: false };
    } catch (err) {
      state.flowToast = { id: Date.now(), msg: `同步未完成：${err.message}`, err: true };
    } finally {
      state.flowSyncing = false;
      renderFn(true);
    }
  }

  async function pinPeak(calls, unpin = false) {
    try {
      const res = await window.sidecar.fetch('/api/calibration/pin', {
        method: 'POST',
        body: JSON.stringify({
          calls: unpin ? 0 : calls,
          unpin,
          workspace: state.workspace,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `HTTP ${res.status}`);
      if (state.dash) {
        if (json.imageQuota) state.dash.imageQuota = json.imageQuota;
        if (json.serverNow) state.clockOffsetSec = json.serverNow - Math.floor(Date.now() / 1000);
        state.dash.updatedAt = new Date().toISOString();
      }
      state.pinToast = {
        id: Date.now(),
        msg: unpin ? '已取消固定，恢复自动水位带学习' : `已永久固定 5h 峰值为 ${json.pinnedPeak?.calls || calls} 次`,
        err: false,
      };
      renderFn(true);
    } catch (err) {
      state.pinToast = { id: Date.now(), msg: `操作失败：${err.message}`, err: true };
      renderFn(true);
    }
  }

  async function resetCalibration() {
    try {
      const res = await window.sidecar.fetch('/api/calibration/reset', {
        method: 'POST',
        body: JSON.stringify({ workspace: state.workspace }),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `HTTP ${res.status}`);
      if (state.dash) {
        if (json.imageQuota) state.dash.imageQuota = json.imageQuota;
        if (json.serverNow) state.clockOffsetSec = json.serverNow - Math.floor(Date.now() / 1000);
        state.dash.updatedAt = new Date().toISOString();
      }
      state.calResetArmUntil = 0;
      state.toast = { id: Date.now(), msg: '已重置永久校准峰值并重算', err: false };
      renderFn(true);
    } catch (err) {
      state.calResetArmUntil = 0;
      state.toast = { id: Date.now(), msg: `重置失败：${err.message}`, err: true };
      renderFn(true);
    }
  }

  async function refresh(force = false) {
    if (state.inFlight && !force) return;
    state.inFlight = true;
    const btn = $('btn-refresh');
    if (force && btn) btn.disabled = true;
    try {
      const params = new URLSearchParams();
      const cid = window.sidecar?.conversationId || '';
      if (cid) params.set('conversationId', cid);
      if (state.workspace) params.set('workspace', state.workspace);
      if (force) params.set('force', '1');
      const res = await window.sidecar.fetch(`/api/dashboard?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.dash = data;
      if (data.serverNow) {
        state.clockOffsetSec = data.serverNow - Math.floor(Date.now() / 1000);
      }
      renderFn(force);
    } catch (err) {
      $('live-dot').className = 'live err';
      if (!state.dash) {
        $('view').innerHTML = `<p class="empty">读取面板数据失败：${esc(err.message)}</p>`;
      }
    } finally {
      state.inFlight = false;
      if (btn) btn.disabled = false;
    }
  }

  Object.assign(QC, {
    setActionsCallbacks,
    copyText,
    saveSettings,
    saveFlowSettings,
    syncFlowCredits,
    pinPeak,
    resetCalibration,
    refresh,
  });
})(window.__QC__ = window.__QC__ || {});
