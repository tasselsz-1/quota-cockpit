(function (QC) {
  'use strict';

  const {
    POLL_MS, state, $, tickCountdowns, updateHeader,
    renderOverview, renderImage, renderPools, renderContext, renderFlow,
    setActionsCallbacks, copyText, saveSettings, saveFlowSettings,
    syncFlowCredits, pinPeak, resetCalibration, refresh,
  } = QC;

  function render(force = false) {
    const d = state.dash;
    if (!d) return;
    updateHeader(d);

    // Avoid overwriting form inputs while the user is editing settings or pinned peak
    if (!force && (state.tab === 'image' || state.tab === 'flow') && document.activeElement?.closest('#settings-form, #pin-peak-form, #flow-settings-form')) {
      return;
    }

    const iq = d.imageQuota || {};
    const w5 = iq.window_5h || {};
    const fq = d.flowQuota || {};
    const fcr = fq.credits || {};
    const frl = fq.rolling || {};
    const fbr = fq.breaker || {};
    const ctx = d.context || {};
    const po = d.pools || {};
    const key = JSON.stringify([
      state.tab,
      d.settings,
      state.toast?.id || 0,
      state.pinToast?.id || 0,
      state.flowSyncing,
      state.flowToast?.id || 0,
      state.flowSettingsToast?.id || 0,
      po.fetchedAt, po.activePoolKey, po.stale, po.error, po.quotaSummaryAvailable,
      (po.pools || []).map((p) => [
        p.key, p.remainingPct, p.bottleneckWindow,
        (p.windows || []).map((w) => [w.window, w.remainingPct, w.resetTs, w.isFull]),
        p.weeklyRecovery?.source, p.weeklyRecovery?.recoverNext24hPct, p.weeklyRecovery?.buckets?.length,
        p.weeklyRecovery?.observedRecovery?.sampleCount, p.weeklyRecovery?.observedRecovery?.recovered24hPct,
      ]),
      iq.error, w5.mode, w5.health, w5.metric_used, w5.ok, w5.eof, w5.err, w5.rate_limited,
      w5.weekly_cooldown_active, w5.weekly_reset_ts,
      w5.tokens_total, w5.next_recover_ts, w5.full_clear_ts,
      w5.window_end, w5.soft_cap, w5.hard_cap, w5.token_cap, w5.anchor_mode, w5.last_429_at,
      iq.anchor?.status, iq.anchor?.source, iq.anchor?.ref_calls, iq.anchor?.ref_tokens,
      iq.anchor?.calls_pct, iq.anchor?.tokens_pct, iq.anchor?.used_pct_basis, iq.anchor?.remaining_calls,
      iq.anchor?.remaining_tokens, iq.anchor?.est_remaining_imgs_with_refs,
      iq.anchor?.pinned_calls, iq.calibration?.pinned_peak?.calls,
      iq.calibration?.persisted_safe_peak_used, iq.calibration?.truncated_lock_count, iq.scan_meta?.archived_dbs,
      Date.now() < state.calResetArmUntil,
      iq.per_image_avg?.with_refs?.avg_tokens_total,
      iq.rolling_schedule?.buckets?.length,
      iq.weekly_rolling_schedule?.total_calls, iq.weekly_rolling_schedule?.cooldown_active, iq.weekly_rolling_schedule?.buckets?.length,
      iq.window_24h?.attempts, iq.window_7d?.attempts,
      (iq.workspace_ledgers || []).map((l) => [l.path, l.updated_at]),
      iq.recent_events?.[0]?.ts,
      fq.error, fq.available, fq.health, fq.status_label,
      fcr.synced, fcr.synced_at, fcr.estimated_total, fcr.spent_since_sync, fcr.spendable, fcr.last_error, fcr.profile_busy,
      frl.hour_used, frl.hour_limit, frl.day_used, frl.day_limit, frl.day_credits_used, frl.day_credits_limit,
      frl.hour_next_recover_ts, frl.day_next_recover_ts, frl.min_gap_wait_sec, frl.untracked_db_24h,
      fbr.open, fbr.requires_manual_reset, fbr.until_ts, fbr.reason,
      fq.usage?.window_7d?.total, fq.usage?.window_30d?.total,
      fq.recent_events?.[0]?.ts, (fq.workspace_runs || []).map((r) => [r.path, r.completed, r.updated_at]),
      ctx.updatedAt, ctx.error, ctx.used_tokens,
    ]);
    if (!force && key === state.lastKey) return;
    state.lastKey = key;

    const view = $('view');
    if (state.tab === 'overview') view.innerHTML = renderOverview(d);
    else if (state.tab === 'image') view.innerHTML = renderImage(d);
    else if (state.tab === 'flow') view.innerHTML = renderFlow(d);
    else if (state.tab === 'pools') view.innerHTML = renderPools(d);
    else view.innerHTML = renderContext(d);

    tickCountdowns();
  }

  function setTab(nextTab) {
    if (!['overview', 'image', 'pools', 'context', 'flow'].includes(nextTab)) return;
    state.tab = nextTab;
    document.querySelectorAll('.tab').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === nextTab);
    });
    render(true);
  }

  QC.render = render;
  QC.setTab = setTab;

  setActionsCallbacks({ render });

  // Wire UI interactions
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => setTab(btn.dataset.tab));
  });
  $('hdr-pool').addEventListener('click', () => setTab('pools'));
  $('hdr-quota').addEventListener('click', () => setTab('pools'));
  $('btn-refresh').addEventListener('click', () => refresh(true));

  $('view').addEventListener('click', (e) => {
    const jump = e.target.closest('[data-jump]');
    if (jump) {
      setTab(jump.dataset.jump);
      return;
    }
    const flowSyncBtn = e.target.closest('[data-flow-sync]');
    if (flowSyncBtn) {
      syncFlowCredits();
      return;
    }
    const flowResetBtn = e.target.closest('#btn-flow-reset-default');
    if (flowResetBtn) {
      saveFlowSettings({
        flowMaxPerHour: 6,
        flowMaxPerDay: 20,
        flowMaxDailyCredits: 400,
        flowMinCredits: 100,
        flowMinGapSec: 35,
      });
      return;
    }
    const copyBtn = e.target.closest('#btn-copy-ctx');
    if (copyBtn && state.dash?.context?.text) {
      copyText(state.dash.context.text, copyBtn);
      return;
    }
    const unpinBtn = e.target.closest('#btn-unpin-peak');
    if (unpinBtn) {
      pinPeak(0, true);
      return;
    }
    const pinObsBtn = e.target.closest('#btn-pin-observed');
    if (pinObsBtn) {
      const c = Number(pinObsBtn.dataset.calls || 0);
      if (c > 0) pinPeak(c, false);
      return;
    }
    const resetBtn = e.target.closest('#btn-reset-auto');
    if (resetBtn) {
      saveSettings({ anchorMode: 'auto', softCap: 54, hardCap: 60, tokenCap: 0, countMode: 'attempts' });
      return;
    }
    const resetCalBtn = e.target.closest('#btn-reset-cal');
    if (resetCalBtn) {
      if (Date.now() < state.calResetArmUntil) {
        resetCalibration();
      } else {
        state.calResetArmUntil = Date.now() + 5000;
        render(true);
        setTimeout(() => {
          if (state.calResetArmUntil && Date.now() >= state.calResetArmUntil) {
            state.calResetArmUntil = 0;
            render(true);
          }
        }, 5100);
      }
    }
  });

  $('view').addEventListener('change', (e) => {
    if (e.target && e.target.id === 'inp-anchor') {
      const box = $('manual-fields');
      if (box) box.classList.toggle('dimmed', e.target.value !== 'manual');
    }
  });

  $('view').addEventListener('submit', (e) => {
    const pinForm = e.target.closest('#pin-peak-form');
    if (pinForm) {
      e.preventDefault();
      const calls = Math.round(Number(pinForm.elements.calls.value));
      if (!Number.isFinite(calls) || calls < 1 || calls > 5000) {
        state.pinToast = { id: Date.now(), msg: '请输入 1–5000 之间的峰值次数', err: true };
        render(true);
        return;
      }
      pinPeak(calls, false);
      return;
    }
    const flowForm = e.target.closest('#flow-settings-form');
    if (flowForm) {
      e.preventDefault();
      const flowMaxPerHour = Math.max(1, Number(flowForm.elements.flowMaxPerHour.value) || 6);
      const flowMaxPerDay = Math.max(flowMaxPerHour, Number(flowForm.elements.flowMaxPerDay.value) || 20);
      const flowMaxDailyCredits = Math.max(10, Number(flowForm.elements.flowMaxDailyCredits.value) || 400);
      const flowMinCredits = Math.max(0, Number(flowForm.elements.flowMinCredits.value) || 0);
      const flowMinGapSec = Math.max(0, Number(flowForm.elements.flowMinGapSec.value) || 0);
      saveFlowSettings({
        flowMaxPerHour,
        flowMaxPerDay,
        flowMaxDailyCredits,
        flowMinCredits,
        flowMinGapSec,
      });
      return;
    }
    const form = e.target.closest('#settings-form');
    if (!form) return;
    e.preventDefault();
    const anchorMode = form.elements.anchorMode.value;
    const softCap = Number(form.elements.softCap.value);
    const hardCap = Math.max(softCap, Number(form.elements.hardCap.value));
    const tokenCap = Math.max(0, Number(form.elements.tokenCap.value) || 0);
    const countMode = form.elements.countMode.value;
    saveSettings({ anchorMode, softCap, hardCap, tokenCap, countMode });
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh(false);
  });

  // Subscribe to workspace URIs without blocking initial render
  if (window.sidecar?.getWorkspaceUris) {
    window.sidecar.getWorkspaceUris().then((uris) => {
      const first = (uris && uris[0]) || '';
      if (first && first !== state.workspace) {
        state.workspace = first;
        refresh(true);
      }
    }).catch(() => {});
  }
  if (window.sidecar?.onWorkspaceChange) {
    window.sidecar.onWorkspaceChange((uris) => {
      const first = (uris && uris[0]) || '';
      if (first !== state.workspace) {
        state.workspace = first;
        refresh(true);
      }
    });
  }

  refresh(false);
  setInterval(() => { if (!document.hidden) refresh(false); }, POLL_MS);
  setInterval(() => { if (!document.hidden) tickCountdowns(); }, 1000);
})(window.__QC__ = window.__QC__ || {});
