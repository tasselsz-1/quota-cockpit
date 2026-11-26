#!/usr/bin/env python3
"""Context window usage report for Antigravity 2.0 conversations.

A port of the agy CLI `/context` command (cli/model.ContextModel in agy 1.4.4).
The CLI reads the live trajectory from memory; this script reads the same
protobufs from the conversation database (`steps` and `gen_metadata` tables).
"""

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from context_parser import (  # noqa: E402, F401
    CATEGORIES,
    CAT_AGENT,
    CAT_CHECKPOINT,
    CAT_SYSTEM,
    CAT_TOOL,
    CAT_USER,
    EFFORT_LEVELS,
    GENERIC_TYPE,
    GRID_COLORS,
    Msg,
    NATIVE_CONTEXT_WINDOWS,
    ORIGINAL_STEP_TYPE_URL,
    STATUS_DONE,
    STEP_CHECKPOINT,
    STEP_CODE_ACTION,
    STEP_GENERIC,
    STEP_PLANNER_RESPONSE,
    STEP_TYPES,
    STEP_USER_INPUT,
    STEP_VIEW_FILE,
    Step,
    TOKEN_CHAT_MESSAGES,
    TOKEN_FLOWS,
    TOKEN_MCP_TOOLS,
    TOKEN_PLUGINS,
    TOKEN_RULES,
    TOKEN_SKILLS,
    TOKEN_SUBAGENTS,
    TOKEN_SYSTEM_PROMPT,
    TOKEN_TOOLS,
    TOKEN_WORKFLOWS,
    WORD_LABELS,
    _summarize_cache_turns,
    _varint,
    chat_tokens_by_category,
    checkpoint_newer_than_generation,
    checkpoint_summary_tokens,
    checkpoint_view,
    collect_cache_stats,
    collect_stats,
    compaction_tokens,
    context_limit,
    detect_system_files,
    estimate_step_tokens,
    fmt_ctx_limit,
    fmt_tok,
    go_div4,
    grid_segments,
    is_skill_file,
    load,
    main,
    model_id,
    model_label,
    model_name,
    packed_original_step,
    pct,
    read_varint,
    render,
    render_checkpoints,
    render_file_section,
    render_grid,
    render_stats,
    request_budget,
    shorten_path,
    stats_heading,
    step_category,
    step_timestamp,
    step_tokens,
)

if __name__ == "__main__":
    main()
