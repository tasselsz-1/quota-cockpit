"""Conversation database step & cache token collectors."""

import json
from pathlib import Path
import re

from .protobuf import (
    CAT_AGENT,
    CAT_CHECKPOINT,
    CAT_SYSTEM,
    CAT_TOOL,
    CAT_USER,
    Msg,
    STATUS_DONE,
    STEP_CHECKPOINT,
    STEP_CODE_ACTION,
    STEP_PLANNER_RESPONSE,
    STEP_TYPES,
    STEP_USER_INPUT,
    STEP_VIEW_FILE,
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
    go_div4,
    shorten_path,
)

# --- stats (model.ContextModel.collectStats) ---------------------------------


def step_category(step_type: int) -> int:
    if step_type == 14:
        return CAT_USER
    if step_type == 15:
        return CAT_AGENT
    if step_type == 23:
        return CAT_CHECKPOINT
    if step_type in (0, 2, 101):
        return CAT_SYSTEM
    return CAT_TOOL


STEP_GENERIC = 140
GENERIC_TYPE = 132
ORIGINAL_STEP_TYPE_URL = "/gemini_coder.Step"


def packed_original_step(generic: Msg) -> bytes | None:
    """The typed step a GENERIC step wraps (rehydration.originalStepAny)."""
    result = generic.msg(2)
    if result is None:
        return None
    payload = result.msg(6)
    if payload is not None and payload.str(1).endswith(ORIGINAL_STEP_TYPE_URL):
        return payload.bytes(2)
    for entry in result.msgs(5):
        if entry.str(1) == "__original_cortex_step":
            value = entry.msg(2)
            if value is not None and value.str(1).endswith(ORIGINAL_STEP_TYPE_URL):
                return value.bytes(2)
    return None


def is_skill_file(path: str) -> bool:
    return path.replace("\\", "/").endswith("/SKILL.md")


class Step:
    def __init__(self, pos: int, payload: bytes, step_type: int, status: int, metadata: bytes | None):
        self.pos = pos
        self.pb = Msg(payload)
        self.type = self.pb.int(1) if self.pb.has(1) else step_type
        self.status = self.pb.int(4) if self.pb.has(4) else status
        self.md = self.pb.msg(5) or Msg(metadata)
        self.view_file_override: Msg | None = None
        if self.type == GENERIC_TYPE:
            self.rehydrate()

    def rehydrate(self):
        """Turn a GENERIC tool step back into its typed step, like the CLI's rehydration.RehydrateStep."""
        generic = self.pb.msg(STEP_GENERIC)
        if generic is None:
            return
        original = packed_original_step(generic)
        if original is not None:
            self.pb = Msg(original)
            self.type = self.pb.int(1)
            self.md = self.pb.msg(5) or Msg(b"")
            return
        tc = self.md.msg(4)
        if tc is None or tc.str(2) not in ("view_file", "read_file"):
            return
        try:
            args = json.loads(tc.str(3) or "{}")
        except json.JSONDecodeError:
            return
        path = next((args[k] for k in ("AbsolutePath", "Path", "FilePath") if isinstance(args.get(k), str) and args[k]), "")
        if path:
            self.type = 8  # VIEW_FILE
            self.view_file_override = Msg(
                b"\x0a" + _varint(len(path.encode())) + path.encode() + (b"\x88\x01\x01" if is_skill_file(path) else b"")
            )

    def payload(self, fn: int) -> Msg | None:
        if fn == STEP_VIEW_FILE and self.view_file_override is not None:
            return self.view_file_override
        return self.pb.msg(fn)


def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def estimate_step_tokens(step: Step) -> int:
    ui = step.payload(STEP_USER_INPUT)
    pr = step.payload(STEP_PLANNER_RESPONSE)
    if ui is not None:
        n = len(ui.bytes(2))  # user_response
    elif pr is not None:
        n = len(pr.bytes(1))  # response
    else:
        tc = step.md.msg(4)  # tool_call
        n = len(tc.bytes(3)) if tc else 0  # arguments_json
        out = step.md.int(23, signed32=True)  # tool_call_output_tokens
        if out > 0:
            return out
    if n == 0:
        return 0
    return max(1, go_div4(n))


def step_tokens(step: Step) -> int:
    usage = step.md.msg(9)  # model_usage
    if usage is not None:
        return usage.int(3)  # output_tokens
    return estimate_step_tokens(step)


def compaction_tokens(step: Step) -> int:
    for ri in reversed(step.md.msgs(28)):  # retry_infos
        usage = ri.msg(2)
        if usage is None:
            continue
        resp = usage.int(10)
        if resp:
            return resp
        out, thinking = usage.int(3), usage.int(9)
        return out - thinking if thinking < out else 0
    return 0


def checkpoint_summary_tokens(step: Step) -> int:
    t = compaction_tokens(step)
    if t:
        return t
    ck = step.payload(STEP_CHECKPOINT) or Msg(b"")
    return go_div4(len(ck.bytes(4)) + len(ck.bytes(5)) + len(ck.bytes(6)))


def checkpoint_newer_than_generation(steps: list[Step], chat_start: Msg | None) -> tuple[int, Step | None]:
    i = len(steps) - 1
    while i >= 0:
        s = steps[i]
        if s.payload(STEP_CHECKPOINT) is not None and s.status == STATUS_DONE:
            break
        i -= 1
    if i < 0:
        return -1, None
    for s in steps[i + 1 :]:
        if s.payload(STEP_PLANNER_RESPONSE) is not None and s.status == STATUS_DONE:
            return -1, None
    gen_ck = chat_start.int(2, signed32=True) if chat_start is not None else -1
    ck_idx = steps[i].payload(STEP_CHECKPOINT).int(1)
    if gen_ck >= ck_idx:
        return -1, None
    return i, steps[i]


def chat_tokens_by_category(items: list[Msg]) -> tuple[bool, list[int]]:
    out = [0, 0, 0, 0]  # user, agent, tool, system
    if not items:
        return False, out
    for it in items:
        left, sep, right = it.str(1).partition(": ")
        if not sep or not re.fullmatch(r"[+-]?[0-9]+", left) or right not in STEP_TYPES:
            return False, [0, 0, 0, 0]
        cat = step_category(STEP_TYPES[right])
        tok = it.int(3, signed32=True)
        if cat == CAT_USER:
            out[0] += tok
        elif cat in (CAT_AGENT, CAT_CHECKPOINT):
            out[1] += tok
        elif cat == CAT_TOOL:
            out[2] += tok
        else:
            out[3] += tok
    return True, out


def detect_system_files(home: str, workspace: str | None, app_dir: str) -> list[tuple[str, int]]:
    if not home:
        return []
    gemini = Path(home) / ".gemini"
    disp = "~/" + gemini.name
    found = []
    f = gemini / "GEMINI.md"
    if f.exists() and not f.is_dir():
        found.append(disp + "/GEMINI.md")
    for sub in ("skills", "rules", "agents", "global_workflows"):
        if (gemini / sub).is_dir():
            found.append(f"{disp}/{sub}/")
    if app_dir:
        for sub in ("rules", "skills", "agents", "global_workflows"):
            if (gemini / app_dir / sub).is_dir():
                found.append(f"{disp}/{app_dir}/{sub}/")
    if workspace:
        for root in (".agents", "_agents"):
            for sub in ("rules", "skills", "agents", "workflows"):
                if (Path(workspace) / root / sub).is_dir():
                    found.append(f"{root}/{sub}/")
            hooks = Path(workspace) / root / "hooks.json"
            if hooks.exists() and not hooks.is_dir():
                found.append(f"{root}/hooks.json")
    return [(p, 0) for p in found]


def step_timestamp(step: Step) -> int:
    ts = 0
    for fn in (7, 6, 1):  # completed_at, response_at, created_at
        m = step.md.msg(fn)
        if m is not None:
            v = m.int(1)
            if v > ts:
                ts = v
    return ts


def _summarize_cache_turns(turns: list[dict]) -> dict:
    if not turns:
        return {
            "turns": 0,
            "cache_read_tokens": 0,
            "uncached_input_tokens": 0,
            "total_input_tokens": 0,
            "output_tokens": 0,
            "hit_rate_pct": 0.0,
            "misses": 0,
        }
    c_read = sum(t["cache_read_tokens"] for t in turns)
    uncached = sum(t["uncached_input_tokens"] for t in turns)
    tot_in = c_read + uncached
    out_tok = sum(t["output_tokens"] for t in turns)
    misses = sum(1 for t in turns if t["is_miss"])
    return {
        "turns": len(turns),
        "cache_read_tokens": c_read,
        "uncached_input_tokens": uncached,
        "total_input_tokens": tot_in,
        "output_tokens": out_tok,
        "hit_rate_pct": round((c_read / tot_in) * 100.0, 1) if tot_in > 0 else 0.0,
        "misses": misses,
    }


def collect_cache_stats(steps: list[Step], gen: Msg | None, recent_limit: int = 16) -> dict:
    """Extract exact per-turn and cumulative Prompt Cache hit telemetry from protobufs.

    In Antigravity's model_usage message (step.md.msg(9) / gen.msg(1).msg(4)):
      - field 2 = uncached input tokens (fresh input + cache write)
      - field 3 = output tokens
      - field 5 = cache read tokens (prompt cache hit)
    """
    turns: list[dict] = []
    seg_start_idx = 0
    after_compact = False
    prev_total_input = 0

    for pos, step in enumerate(steps):
        if step.payload(STEP_CHECKPOINT) is not None and step.status == STATUS_DONE:
            after_compact = True
            seg_start_idx = len(turns)
            prev_total_input = 0
            continue

        if step.type != 15:  # PLANNER_RESPONSE
            continue

        usage = step.md.msg(9)
        if usage is None:
            continue
        uncached = max(0, usage.int(2))
        cache_read = max(0, usage.int(5))
        total_input = uncached + cache_read
        if total_input <= 0:
            continue

        output_tok = max(0, usage.int(3))
        hit_rate = round((cache_read / total_input) * 100.0, 1)
        is_cold = (len(turns) == 0) or after_compact
        prefix_retention: float | None = None
        if not is_cold and prev_total_input > 0:
            prefix_retention = round(min(100.0, (cache_read / prev_total_input) * 100.0), 1)

        # True mid-segment cache miss: prior prefix existed in same segment, yet read dropped to 0 or <50% of prior prefix
        is_miss = (
            not is_cold
            and prev_total_input > 0
            and (cache_read == 0 or (hit_rate < 40.0 and cache_read < prev_total_input * 0.5))
        )

        if is_cold:
            status = "cold"
        elif is_miss:
            status = "miss"
        elif hit_rate >= 80.0:
            status = "high"
        elif hit_rate >= 40.0:
            status = "mid"
        else:
            status = "low"

        turns.append({
            "step_pos": pos,
            "ts": step_timestamp(step),
            "cache_read_tokens": cache_read,
            "uncached_input_tokens": uncached,
            "total_input_tokens": total_input,
            "output_tokens": output_tok,
            "hit_rate_pct": hit_rate,
            "prefix_retention_pct": prefix_retention,
            "status": status,
            "is_cold": is_cold,
            "is_miss": is_miss,
            "after_compact": after_compact,
        })
        after_compact = False
        prev_total_input = total_input

    # Fallback: synthesize single latest entry from gen_metadata if steps carried no usage
    if not turns and gen is not None:
        chat = gen.msg(1)
        usage = chat.msg(4) if chat is not None else None
        if usage is not None:
            uncached = max(0, usage.int(2))
            cache_read = max(0, usage.int(5))
            total_input = uncached + cache_read
            if total_input > 0:
                hit_rate = round((cache_read / total_input) * 100.0, 1)
                status = "high" if hit_rate >= 80.0 else ("mid" if hit_rate >= 40.0 else ("cold" if cache_read == 0 else "low"))
                turns.append({
                    "step_pos": -1,
                    "ts": 0,
                    "cache_read_tokens": cache_read,
                    "uncached_input_tokens": uncached,
                    "total_input_tokens": total_input,
                    "output_tokens": max(0, usage.int(3)),
                    "hit_rate_pct": hit_rate,
                    "prefix_retention_pct": None,
                    "status": status,
                    "is_cold": cache_read == 0,
                    "is_miss": False,
                    "after_compact": False,
                })

    compacted_pending = after_compact and len(turns) > 0
    seg_turns = turns[seg_start_idx:]
    has_compaction = seg_start_idx > 0 or compacted_pending

    return {
        "has_data": len(turns) > 0,
        "turns": len(turns),
        "has_compaction": has_compaction,
        "compacted_pending": compacted_pending,
        "latest": turns[-1] if turns else None,
        "segment": _summarize_cache_turns(seg_turns),
        "session": _summarize_cache_turns(turns),
        "recent": turns[-recent_limit:] if recent_limit > 0 else [],
    }


def collect_stats(steps: list[Step], gen: Msg | None, limit: int) -> dict:
    st = {k: 0 for k in (
        "user", "agent", "tool", "checkpoint", "system_messages", "system_prompt", "system_tools",
        "system_overhead", "rules", "skills", "subagents", "legacy_workflows", "workflows",
        "plugins", "mcp_tools",
    )}
    st["has_breakdown"] = False
    st["active_checkpoint"] = -1
    checkpoints, knowledge, artifacts, skill_files = [], [], [], []
    seen_k, seen_a, seen_s = set(), set(), set()

    chat = gen.msg(1) if gen is not None else None
    chat_start = chat.msg(9) if chat is not None else None
    newer_pos, newer_step = checkpoint_newer_than_generation(steps, chat_start)
    has_newer = newer_step is not None
    st["compacted"] = has_newer

    for pos, step in enumerate(steps):
        cat = step_category(step.type)
        tok = step_tokens(step)
        counted = 0 if has_newer and newer_pos > pos else tok
        if cat == CAT_USER:
            st["user"] += counted
        elif cat == CAT_AGENT:
            st["agent"] += counted
        elif cat == CAT_TOOL:
            st["tool"] += counted
        elif cat == CAT_CHECKPOINT:
            st["checkpoint"] += tok
            ck = step.payload(STEP_CHECKPOINT)
            if ck is not None:
                checkpoints.append({
                    "index": ck.int(1),
                    "start": ck.int(11),
                    "end": ck.int(12),
                    "intent": ck.str(4),
                })
        else:
            st["system_messages"] += counted

        ca = step.payload(STEP_CODE_ACTION)
        result = ca.msg(2) if ca is not None else None
        edit = result.msg(1) if result is not None else None
        if edit is not None:
            uri = edit.str(8)  # absolute_uri
            short = shorten_path(uri)
            if ca.int(21) and uri not in seen_a:  # is_artifact_file
                seen_a.add(uri)
                artifacts.append((short, tok))
            if ca.int(24) and uri not in seen_k:  # is_knowledge_file
                seen_k.add(uri)
                knowledge.append((short, tok))

        vf = step.payload(STEP_VIEW_FILE)
        if vf is not None and vf.int(17):  # is_skill_file
            uri = vf.str(1)
            if uri not in seen_s:
                seen_s.add(uri)
                skill_files.append((shorten_path(uri), tok))

    cwm = chat_start.msg(10) if chat_start is not None else None
    breakdown = cwm.msg(3) if cwm is not None else None
    if breakdown is not None:
        st["has_breakdown"] = True
        seen_prompt = seen_tools = False
        chat_tok, chat_items = 0, []
        for g in breakdown.msgs(1):
            gtype, gtok = g.int(2), g.int(4, signed32=True)
            if gtype == TOKEN_SYSTEM_PROMPT:
                extracted = 0
                for child in g.msgs(5):
                    name, ctok = child.str(1), child.int(3, signed32=True)
                    if name == "flows":
                        st["workflows"] += ctok
                    elif name == "skills":
                        st["skills"] += ctok
                    elif name == "subagents":
                        st["subagents"] += ctok
                    else:
                        continue
                    extracted += ctok
                st["system_prompt"] = gtok - extracted
                seen_prompt = True
            elif gtype == TOKEN_TOOLS:
                st["system_tools"] = gtok
                seen_tools = True
            elif gtype == TOKEN_RULES:
                st["rules"] += gtok
            elif gtype == TOKEN_MCP_TOOLS:
                st["mcp_tools"] += gtok
            elif gtype == TOKEN_SKILLS:
                st["skills"] += gtok
            elif gtype == TOKEN_SUBAGENTS:
                st["subagents"] += gtok
            elif gtype == TOKEN_CHAT_MESSAGES:
                chat_tok, chat_items = gtok, g.msgs(5)
            elif gtype == TOKEN_WORKFLOWS:
                st["legacy_workflows"] += gtok
            elif gtype == TOKEN_PLUGINS:
                st["plugins"] += gtok
            elif gtype == TOKEN_FLOWS:
                st["workflows"] += gtok

        if not seen_prompt and not seen_tools and chat_tok:
            usage = chat.msg(4)
            api_input = usage.int(2) + usage.int(5) if usage is not None else 0
            if chat_tok < api_input:
                st["system_overhead"] = api_input - chat_tok

        if chat_tok and not has_newer:
            ok, cats = chat_tokens_by_category(chat_items)
            if ok:
                st["user"], st["agent"], st["tool"], st["system_messages"] = cats
            else:
                est = st["user"] + st["agent"] + st["tool"]
                if est == 0:
                    st["agent"] = chat_tok
                else:
                    f = chat_tok / est
                    st["user"] = int(st["user"] * f)
                    st["agent"] = int(st["agent"] * f)
                    st["tool"] = int(st["tool"] * f)

    if chat_start is not None:
        st["active_checkpoint"] = chat_start.int(2, signed32=True)
    if has_newer:
        st["agent"] += checkpoint_summary_tokens(newer_step)
        st["active_checkpoint"] = newer_step.payload(STEP_CHECKPOINT).int(1)

    st["total"] = sum(st[k] for k in (
        "user", "agent", "tool", "system_messages", "system_prompt", "system_tools", "system_overhead",
        "rules", "skills", "subagents", "legacy_workflows", "workflows", "plugins", "mcp_tools",
    ))
    st["limit"] = limit
    st["checkpoints"] = checkpoints
    st["knowledge_files"] = knowledge
    st["artifact_files"] = artifacts
    st["skill_files"] = skill_files
    st["cache"] = collect_cache_stats(steps, gen)
    return st
