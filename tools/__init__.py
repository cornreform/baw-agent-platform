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

_TOOL_MODULES = [
    "bash", "read_file", "write_file", "web_search", "image_generate", "tts", "todo",
    "petrestaurants", "http_fetch", "restaurant", "memory", "install",
    "get_skill", "remember", "knowledge_graph", "mcp", "background", "mmx", "code_scan",
    "config", "execute_code", "session_search", "cronjob", "git", "docker",
    "system", "self_diagnose", "resource_monitor", "self_capabilities",
    "tool_generate", "self_migrate", "scan_and_adopt", "skill_import",
    "self_discover", "list_files",
]

_tool_modules = []
for mod_name in _TOOL_MODULES:
    try:
        mod = __import__(f"tools.{mod_name}", fromlist=[mod_name])
        _tool_modules.append(mod)
    except Exception as e:
        logger.error("Failed to import tool module %s: %s", mod_name, e)


def register_all():
    for mod in _tool_modules:
        try:
            register(**mod.TOOL_DEF)
        except Exception as e:
            logger.error("Failed to register tool from %s: %s", mod.__name__, e)
