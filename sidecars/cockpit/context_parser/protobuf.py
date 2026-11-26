"""Protobuf wire-format reader and formatting helpers for context_parser."""

from pathlib import Path
from urllib.parse import unquote, urlparse

# --- protobuf ----------------------------------------------------------------


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
    raise ValueError("Truncated varint")


class Msg:
    """Lazily decoded protobuf message: field number -> list of raw values."""

    def __init__(self, buf: bytes | None):
        self.fields: dict[int, list] = {}
        pos, n = 0, len(buf or b"")
        while pos < n:
            try:
                tag, pos = read_varint(buf, pos)
                fn, wt = tag >> 3, tag & 7
                if wt == 0:
                    val, pos = read_varint(buf, pos)
                elif wt == 2:
                    ln, pos = read_varint(buf, pos)
                    val = buf[pos : pos + ln]
                    pos += ln
                elif wt == 1:
                    val, pos = buf[pos : pos + 8], pos + 8
                elif wt == 5:
                    val, pos = buf[pos : pos + 4], pos + 4
                else:
                    break
            except ValueError:
                break
            self.fields.setdefault(fn, []).append(val)

    def has(self, fn: int) -> bool:
        return fn in self.fields

    def int(self, fn: int, signed32: bool = False) -> int:
        v = self.fields.get(fn, [0])[-1]
        v = v if isinstance(v, int) else 0
        if signed32 and v >= 1 << 63:
            v -= 1 << 64
        return v

    def bytes(self, fn: int) -> bytes:
        v = self.fields.get(fn, [b""])[-1]
        return v if isinstance(v, bytes) else b""

    def str(self, fn: int) -> str:
        return self.bytes(fn).decode("utf-8", errors="replace")

    def msg(self, fn: int) -> "Msg | None":
        return Msg(self.bytes(fn)) if self.has(fn) else None

    def msgs(self, fn: int) -> list["Msg"]:
        return [Msg(v) for v in self.fields.get(fn, []) if isinstance(v, bytes)]


# CortexStep oneof payload field numbers.
STEP_VIEW_FILE = 14
STEP_USER_INPUT = 19
STEP_PLANNER_RESPONSE = 20
STEP_CHECKPOINT = 30
STEP_CODE_ACTION = 10

STATUS_DONE = 3

# TokenType values of TopLevelTokenGroup.type.
TOKEN_SYSTEM_PROMPT, TOKEN_TOOLS, TOKEN_RULES, TOKEN_MCP_TOOLS, TOKEN_SKILLS = 1, 2, 3, 4, 5
TOKEN_SUBAGENTS, TOKEN_CHAT_MESSAGES, TOKEN_WORKFLOWS, TOKEN_PLUGINS, TOKEN_FLOWS = 6, 7, 8, 9, 10

# CortexStepType names (exa.cortex_pb), used to parse "N: TYPE" chat item names.
STEP_TYPES = {
    "UNSPECIFIED": 0, "DUMMY": 1, "FINISH": 2, "PLAN_INPUT": 3, "MQUERY": 4, "CODE_ACTION": 5,
    "GIT_COMMIT": 6, "GREP_SEARCH": 7, "VIEW_FILE": 8, "LIST_DIRECTORY": 9, "COMPILE": 10,
    "VIEW_CODE_ITEM": 13, "USER_INPUT": 14, "PLANNER_RESPONSE": 15, "ERROR_MESSAGE": 17,
    "RUN_COMMAND": 21, "CHECKPOINT": 23, "PROPOSE_CODE": 24, "FIND": 25, "SUGGESTED_RESPONSES": 27,
    "COMMAND_STATUS": 28, "MEMORY": 29, "READ_URL_CONTENT": 31, "VIEW_CONTENT_CHUNK": 32,
    "SEARCH_WEB": 33, "RETRIEVE_MEMORY": 34, "MCP_TOOL": 38, "MANAGER_FEEDBACK": 39,
    "TOOL_CALL_PROPOSAL": 40, "TOOL_CALL_CHOICE": 41, "TRAJECTORY_CHOICE": 42, "CLIPBOARD": 45,
    "VIEW_FILE_OUTLINE": 47, "POST_PR_REVIEW": 49, "LIST_RESOURCES": 51, "READ_RESOURCE": 52,
    "LINT_DIFF": 53, "FIND_ALL_REFERENCES": 54, "BRAIN_UPDATE": 55, "OPEN_BROWSER_URL": 56,
    "RUN_EXTENSION_CODE": 57, "PROPOSAL_FEEDBACK": 59, "TRAJECTORY_SEARCH": 60,
    "EXECUTE_BROWSER_JAVASCRIPT": 61, "LIST_BROWSER_PAGES": 62, "CAPTURE_BROWSER_SCREENSHOT": 63,
    "CLICK_BROWSER_PIXEL": 64, "READ_TERMINAL": 65, "CAPTURE_BROWSER_CONSOLE_LOGS": 66,
    "READ_BROWSER_PAGE": 67, "BROWSER_GET_DOM": 68, "CODE_SEARCH": 73, "BROWSER_INPUT": 74,
    "BROWSER_MOVE_MOUSE": 75, "BROWSER_SELECT_OPTION": 76, "BROWSER_SCROLL_UP": 77,
    "BROWSER_SCROLL_DOWN": 78, "BROWSER_CLICK_ELEMENT": 79, "BROWSER_PRESS_KEY": 80,
    "TASK_BOUNDARY": 81, "NOTIFY_USER": 82, "CODE_ACKNOWLEDGEMENT": 83, "INTERNAL_SEARCH": 84,
    "BROWSER_SUBAGENT": 85, "FILE_CHANGE": 86, "MOVE": 87, "BROWSER_SCROLL": 88,
    "KNOWLEDGE_GENERATION": 89, "EPHEMERAL_MESSAGE": 90, "GENERATE_IMAGE": 91,
    "DELETE_DIRECTORY": 92, "COMPILE_APPLET": 93, "INSTALL_APPLET_DEPENDENCIES": 94,
    "INSTALL_APPLET_PACKAGE": 95, "BROWSER_RESIZE_WINDOW": 96, "BROWSER_DRAG_PIXEL_TO_PIXEL": 97,
    "CONVERSATION_HISTORY": 98, "KNOWLEDGE_ARTIFACTS": 99, "SEND_COMMAND_INPUT": 100,
    "SYSTEM_MESSAGE": 101, "WAIT": 102, "AGENCY_TOOL_CALL": 103, "CIDER_AGENT_DUMMY": 104,
    "BUILD_CLEANER": 105, "BLAZE_BUILD_TARGETS": 106, "BLAZE_TEST_TARGETS": 107,
    "SET_UP_FIREBASE": 108, "MOMA": 109, "RESTART_DEV_SERVER": 110, "DEPLOY_FIREBASE": 111,
    "SHELL_EXEC": 112, "BROWSER_MOUSE_WHEEL": 113, "LINT_APPLET": 114, "KI_INSERTION": 116,
    "RETRIEVE_CONTENT": 117, "CRITIQUE": 118, "FINDINGS": 119, "BROWSER_MOUSE_UP": 120,
    "BROWSER_MOUSE_DOWN": 121, "WORKSPACE_API": 122, "BROWSER_LIST_NETWORK_REQUESTS": 123,
    "BROWSER_GET_NETWORK_REQUEST": 124, "BROWSER_REFRESH_PAGE": 125, "EDIT_NOTEBOOK": 126,
    "INVOKE_SUBAGENT": 127, "WRITE_BLOB": 128, "READ_NOTEBOOK": 129, "PROPOSE_AI_COMMENTS": 130,
    "START_CODE_REVIEW": 131, "GENERIC": 132, "SET_UP_CLOUD_SQL": 133, "EXECUTE_NOTEBOOK": 134,
    "CLOUD_SQL_UPDATE_SCHEMA": 135, "RPC_ACTION": 136, "CLOUD_SQL_EXECUTE_SQL": 137,
    "ASK_QUESTION": 138, "DIRECTORY_RULES": 139, "TOOL_SEARCH": 140,
}

CAT_USER, CAT_AGENT, CAT_TOOL, CAT_CHECKPOINT, CAT_SYSTEM = 0, 1, 2, 3, 4

# --- formatting --------------------------------------------------------------


def fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000.0
        return f"{v:.0f}M" if v.is_integer() else f"{v:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000.0:.1f}k"
    return str(n)


def fmt_ctx_limit(n: int) -> str:
    return fmt_tok(n)


def go_div4(n: int) -> int:
    """Go's int division by 4 (truncates toward zero)."""
    return int(n / 4)


def shorten_path(uri: str) -> str:
    p = uri
    if p.startswith("file:"):
        parsed = urlparse(p)
        p = unquote(parsed.path)
        if len(p) > 2 and p[0] == "/" and p[2] == ":":
            p = p[1:]
    home = str(Path.home())
    p = p.replace("\\", "/")
    home = home.replace("\\", "/")
    if p.startswith(home):
        return "~" + p[len(home) :]
    return p
