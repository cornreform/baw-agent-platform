"""BAW built-in: register all built-in tools

Each tool module is imported individually so a single broken module
does not prevent the rest from loading.
"""
import logging
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from core.tools import register

logger = logging.getLogger("baw.tools")

# -- CORE TOOLS (always active, frequently used) --
_CORE_TOOLS = [
    "bash", "read_file", "write_file", "web_search", "patch",
    "search_files", "memory", "remember", "config", "mmx",
]

# -- MEDIA TOOLS (image, audio, vision) --
_MEDIA_TOOLS = [
    "image_generate", "tts", "vision",
]

# -- UTILITY TOOLS (infrastructure, monitoring) --
_UTILITY_TOOLS = [
    "git", "docker", "system", "todo", "install", "background",
    "cronjob", "list_files", "resource_monitor", "execute_code",
    "session_search", "self_diagnose", "codebase_doc",
    "workspace", "web_extract", "rss_feed",
]

# -- SPECIALIZED TOOLS (task-specific, less common) --
_SPECIALIZED_TOOLS = [
    "delegate_task", "batch_delegate", "knowledge_graph", "kg_curator", "memory_quality", "session_synthesis", "http_fetch",
    "self_capabilities", "self_migrate", "fusion_analyze",
    "ai_research",
]

# -- RARELY-USED / DEBUG TOOLS --
_RARE_TOOLS = [
    "tool_generate", "get_skill", "mcp",
    "document_structuring", "selftest",
]

# Combined list
_TOOL_MODULES = _CORE_TOOLS + _MEDIA_TOOLS + _UTILITY_TOOLS + _SPECIALIZED_TOOLS + _RARE_TOOLS

# Removed (overlapping or rarely used):
# petrestaurants, restaurant, scan_and_adopt, skill_import,
# self_discover, mmx, code_scan, web_extract, browser

_tool_modules = []
for mod_name in _TOOL_MODULES:
    try:
        mod = __import__("tools." + mod_name, fromlist=[mod_name])
        _tool_modules.append(mod)
    except Exception as e:
        logger.error("Failed to import tool module %s: %s", mod_name, e)


def register_all():
    for mod in _tool_modules:
        try:
            register(**mod.TOOL_DEF)
        except Exception as e:
            logger.error("Failed to register tool from %s: %s", mod.__name__, e)
