(function (QC) {
  'use strict';

  const POLL_MS = 3000;
  const SEG_COLORS = ['var(--c-system)', 'var(--c-user)', 'var(--c-agent)', 'var(--c-tool)', 'var(--c-custom)'];
  const CAT_COLOR = {
    user: 'var(--c-user)', agent: 'var(--c-agent)', tool: 'var(--c-tool)',
    system_prompt: 'var(--c-system)', system_tools: 'var(--c-system)',
    system_overhead: 'var(--c-system)', system_messages: 'var(--c-sysmsg)',
    rules: 'var(--c-custom)', skills: 'var(--c-custom)', subagents: 'var(--c-custom)',
    legacy_workflows: 'var(--c-custom)', workflows: 'var(--c-custom)',
    plugins: 'var(--c-custom)', mcp_tools: 'var(--c-custom)',
  };
  const CAT_LABEL = {
    user: '用户消息', agent: '模型回复', tool: '工具调用',
    system_prompt: '系统提示词', system_tools: '系统工具定义',
    system_overhead: '系统开销', system_messages: '系统消息',
    rules: '规则 (Rules)', skills: '技能 (Skills)', subagents: '子代理',
    legacy_workflows: '旧工作流', workflows: '工作流',
    plugins: '插件', mcp_tools: 'MCP 工具',
  };
  const CTX_GROUPS = [
    { keys: ['user', 'agent', 'tool'], sq: false },
    { keys: ['system_prompt', 'system_tools', 'system_overhead', 'system_messages'], sq: true },
    { keys: ['rules', 'skills', 'subagents', 'legacy_workflows', 'workflows', 'plugins', 'mcp_tools'], sq: true },
  ];

  const state = {
    tab: 'overview',
    dash: null,
    workspace: '',
    clockOffsetSec: 0,
    lastKey: '',
    lastHdrQuotaKey: '',
    inFlight: false,
    toast: null,
    pinToast: null,
    calResetArmUntil: 0,
    flowSyncing: false,
    flowToast: null,
    flowSettingsToast: null,
  };

  const $ = (id) => document.getElementById(id);

  Object.assign(QC, {
    POLL_MS,
    SEG_COLORS,
    CAT_COLOR,
    CAT_LABEL,
    CTX_GROUPS,
    state,
    $,
  });
})(window.__QC__ = window.__QC__ || {});
