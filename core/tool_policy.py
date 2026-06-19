"""
Per-tool error policies for BAW.

Replaces the global "3 consecutive failures" rule with tool-specific retry
policies. Each tool has a pre-defined error-handling strategy based on the
kinds of errors it typically produces.

┌──────────────────────────────┬──────────┬─────────────────────────────────┐
│ Error Type                   │ Policy   │ Rationale                       │
├──────────────────────────────┼──────────┼─────────────────────────────────┤
│ Permission/block errors      │ retry=0  │ Retrying won't fix permission   │
│ (BLOCKED, Permission denied) │          │                                 │
├──────────────────────────────┼──────────┼─────────────────────────────────┤
│ Transient errors             │ retry=1  │ May resolve on retry            │
│ (timeout, 429, conn reset)   │          │                                 │
├──────────────────────────────┼──────────┼─────────────────────────────────┤
│ Logic errors                 │ retry=0  │ Same error will recur; report   │
│ (syntax error, not found)    │          │                                 │
├──────────────────────────────┼──────────┼─────────────────────────────────┤
│ Tool output errors           │ retry=1  │ Maybe BAW called it wrong       │
│ (Error in output, Traceback) │          │                                 │
└──────────────────────────────┴──────────┴─────────────────────────────────┘
"""

from __future__ import annotations

import logging

logger = logging.getLogger("baw.tool_policy")

# ── Error-type classification patterns ──────────────────────────

# Errors that will NEVER resolve on retry (permission, syntax, logic)
_PERMANENT_PATTERNS = [
    "[BLOCKED]",
    "BLOCKED",
    "Permission denied",
    "permission denied",
    "denied",
    "not found",
    "No such file",
    "FileNotFoundError",
    "syntax error",
    "Invalid syntax",
    "is not a valid",
    "not allowed",
    "not permitted",
    "access denied",
    "AccessDenied",
    "does not exist",
    "No such directory",
    "Is a directory",
    "PermissionError",
]

# Errors that MAY resolve on retry (transient network/server)
_TRANSIENT_PATTERNS = [
    "timeout",
    "Timeout",
    "429",
    "502",
    "503",
    "504",
    "connection reset",
    "ConnectionResetError",
    "Connection refused",
    "ConnectionError",
    "ConnectError",
    "rate limit",
    "RateLimit",
    "Too Many Requests",
    "too many requests",
    "temporarily unavailable",
    "Temporarily Unavailable",
    "server error",
    "Server Error",
    "Internal Server Error",
    "remote end",
    "RemoteEnd",
    "network is unreachable",
    "Network is unreachable",
    "Name or service not known",
    "name resolution",
    "NameResolutionError",
    "Failed to resolve",
    "failed to resolve",
    "SSL",
    "ssl",
    "SSLError",
    "Bad Gateway",
    "bad gateway",
    "Service Unavailable",
    "service unavailable",
    "Gateway Timeout",
    "gateway timeout",
    "Read timed out",
    "read timed out",
    "connect timed out",
    "connect timed out",
]


def classify_error(exe_result: str) -> str:
    """Classify a tool error string into a category.

    Returns one of:
      - ``"permanent"``  — will never resolve on retry
      - ``"transient"``  — may resolve on retry
      - ``"tool_error"`` — generic tool output error (may be worth retrying)
    """
    for pat in _PERMANENT_PATTERNS:
        if pat in exe_result:
            return "permanent"
    for pat in _TRANSIENT_PATTERNS:
        if pat in exe_result:
            return "transient"
    return "tool_error"


# ── Per-tool retry policies ──────────────────────────────────────
#
# ``max_retries`` = how many consecutive failures BAW is allowed before
# the loop injects a [SYSTEM] stop message:
#   0  = stop immediately on first error
#   1  = allow exactly one retry, stop on second consecutive error

TOOL_POLICIES: dict[str, int] = {
    # ── retry=0: permission/block/logic errors — never worth retrying ──
    "bash": 0,
    "read_file": 0,
    "write_file": 0,
    "patch": 0,
    "install": 0,
    "git": 0,
    "docker": 0,
    "system": 0,
    "cronjob": 0,
    "list_files": 0,
    "search_files": 0,
    "config": 0,
    "code_scan": 0,
    "execute_code": 0,
    "selftest": 0,
    "self_diagnose": 0,
    "resource_monitor": 0,
    # ── retry=1: transient/network tools — may resolve on retry ──
    "web_search": 1,
    "http_fetch": 1,
    "web_extract": 1,
    "browser": 1,
    "image_generate": 1,
    "tts": 1,
    "vision": 1,
    "get_skill": 1,
    "self_capabilities": 1,
    # ── retry=1: tools BAW may have called with wrong args ──
    "memory": 1,
    "remember": 1,
    "knowledge_graph": 1,
    "todo": 1,
    "petrestaurants": 1,
    "restaurant": 1,
    "mcp": 1,
    "delegate_task": 1,
    "background": 1,
    "mmx": 1,
    "tool_generate": 1,
    "self_migrate": 1,
    "skill_import": 1,
    "scan_and_adopt": 1,
    "self_discover": 1,
    "session_search": 1,
    "document_structuring": 1,
}

# Default policy for tools not explicitly listed
DEFAULT_MAX_RETRIES = 0


def get_max_retries(tool_name: str) -> int:
    """Return the maximum allowed retry count for a given tool name.

    This is the number of consecutive failures BAW may experience before
    the loop stops the tool path. ``0`` means stop on first failure.
    """
    return TOOL_POLICIES.get(tool_name, DEFAULT_MAX_RETRIES)
