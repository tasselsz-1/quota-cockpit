"""Model catalog, context limits, request budget, and CLI entrypoint."""

import argparse
import json
import os
from pathlib import Path
import sys

from .collector import collect_stats, detect_system_files
from .protobuf import Msg
from .renderer import (
    CATEGORIES,
    checkpoint_view,
    grid_segments,
    load,
    pct,
    render,
    stats_heading,
)

# Native context windows from the model catalog, which the CLI fetches from the server
# (ModelInfo.max_tokens) and never writes to disk. Matched by model ID prefix; the first
# match wins. Add a row when a new model family ships.
NATIVE_CONTEXT_WINDOWS = [
    ("claude-opus-4-6", 250_000),
    ("claude-sonnet-4-6", 250_000),
    ("claude-", 1_000_000),
    ("gpt-oss-", 131_072),
    ("gemini-", 1_048_576),
]

EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
WORD_LABELS = {"gpt": "GPT", "oss": "OSS"}


def model_id(gen: Msg | None) -> str:
    planner = gen.msg(3) if gen is not None else None
    return planner.str(28) if planner is not None else ""  # planner_config.model_name


def model_label(slug: str) -> str:
    """Catalog-style label from a model ID: gemini-3.8-flash-medium -> Gemini 3.8 Flash (Medium)."""
    parts = slug.split("-")
    effort = parts.pop().capitalize() if len(parts) > 1 and parts[-1] in EFFORT_LEVELS else ""
    words: list[str] = []
    for p in parts:
        if p.isdigit() and words and words[-1][-1:].isdigit():
            words[-1] += "." + p  # claude-opus-5-5 -> 5.5
        elif p in WORD_LABELS:
            words.append(WORD_LABELS[p])
        elif p[:1].isdigit():
            words.append(p.upper())  # 120b -> 120B
        else:
            words.append(p.capitalize())
    label = " ".join(words).replace("GPT OSS", "GPT-OSS")
    return f"{label} ({effort})" if effort else label


def model_name(gen: Msg | None) -> str:
    chat = gen.msg(1) if gen is not None else None
    if chat is not None and chat.str(21):  # model_display_name
        return chat.str(21)
    if model_id(gen):
        return model_label(model_id(gen))
    if chat is not None and chat.str(19):  # response_model
        return chat.str(19)
    return "Unknown Model"


def context_limit(gen: Msg | None) -> int:
    """The model's native window, like the CLI; else the request budget (max_context_tokens)."""
    slug = model_id(gen)
    for prefix, tokens in NATIVE_CONTEXT_WINDOWS:
        if slug.startswith(prefix):
            return tokens
    return request_budget(gen)


def request_budget(gen: Msg | None) -> int:
    chat = gen.msg(1) if gen is not None else None
    start = chat.msg(9) if chat is not None else None
    cwm = start.msg(10) if start is not None else None
    return cwm.int(4, signed32=True) if cwm is not None else 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Analyze Antigravity 2.0 conversation context usage.")
    parser.add_argument("--conversation-id", help="Conversation UUID (auto-detected if omitted)")
    parser.add_argument("--db", help="Path to a conversation .db file (e.g. one from ~/.gemini/antigravity-cli/conversations)")
    parser.add_argument("--workspace", help="Active workspace path", default=None)
    parser.add_argument("--limit", type=int, default=0, help="Context window token limit override")
    parser.add_argument("--app-dir", default="antigravity", help="App data folder under ~/.gemini (default: antigravity)")
    parser.add_argument("--width", type=int, default=100, help="Terminal width used for the grid layout")
    parser.add_argument("--color", action="store_true", help="Color the grid segments with ANSI escapes")
    parser.add_argument("--json", action="store_true", help="Output JSON data")
    parser.add_argument("--model-catalog", help='JSON file of {"<model id>": {"maxTokens": n, "displayName": "..."}} '
                        "from the app's live model catalog; overrides the built-in limits and labels")
    args = parser.parse_args()

    if args.db:
        db_path = Path(args.db).expanduser()
        conv_id = db_path.stem
    else:
        conv_dir = Path.home() / ".gemini" / "antigravity" / "conversations"
        conv_id = args.conversation_id
        if not conv_id:
            dbs = sorted(conv_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not dbs:
                raise SystemExit("No conversation databases found.")
            conv_id = dbs[0].stem
        db_path = conv_dir / f"{conv_id}.db"
    if not db_path.is_file():
        raise SystemExit(f"Conversation database not found: {db_path}")

    steps, gen = load(db_path)
    catalog_entry = {}
    if args.model_catalog:
        with open(args.model_catalog, encoding="utf-8") as fh:
            catalog_entry = json.load(fh).get(model_id(gen)) or {}
    limit = args.limit if args.limit > 0 else int(catalog_entry.get("maxTokens") or 0) or context_limit(gen)
    st = collect_stats(steps, gen, limit)
    model = catalog_entry.get("displayName") or model_name(gen)
    system_files = detect_system_files(os.path.expanduser("~"), args.workspace, args.app_dir)

    if args.json:
        print(json.dumps({
            "conversation_id": conv_id,
            "model": model,
            "heading": stats_heading(st),
            "grid_segments": grid_segments(st),
            "checkpoint_view": checkpoint_view(st),
            "text": render(st, model, system_files, args.width, False),
            "model_id": model_id(gen),
            "request_budget_tokens": request_budget(gen),
            "used_tokens": st["total"],
            "limit_tokens": limit,
            "used_pct": round(min(100.0, pct(st["total"], limit)), 1),
            "free_tokens": limit - st["total"] if st["total"] < limit else 0,
            "has_breakdown": st["has_breakdown"],
            "compacted": st["compacted"],
            "checkpoint_buffer": st["checkpoint"],
            "categories": [
                {"icon": icon, "name": name, "key": key, "tokens": st[key], "pct": round(pct(st[key], limit), 1)}
                for icon, name, key, always in CATEGORIES
                if always or st[key]
            ],
            "active_checkpoint": st["active_checkpoint"],
            "checkpoints": st["checkpoints"],
            "knowledge_files": [{"path": p, "tokens": t} for p, t in st["knowledge_files"]],
            "artifact_files": [{"path": p, "tokens": t} for p, t in st["artifact_files"]],
            "skill_files": [{"path": p, "tokens": t} for p, t in st["skill_files"]],
            "system_files": [{"path": p} for p, _ in system_files],
            "cache": st["cache"],
            "steps": st["steps"],
        }, indent=2))
        return

    print(render(st, model, system_files, args.width, args.color))
