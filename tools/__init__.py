"""BAW built-in: register all built-in tools"""

# M5-D6 cleanup: `from ..core.tools` only works if `tools/` is nested
# inside another package. In the BAW layout, `tools/` is a sibling of
# `core/` at the repo root, so we import `core.tools` via a sys.path
# based absolute import. The "baw" CLI adds the repo root to sys.path
# (and uv-run does the same), so a plain `from core.tools` import
# works everywhere the tool chain is exercised.
import os
import sys
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from core.tools import register

from . import bash, read_file, write_file, web_search, image_generate, tts, todo, petrestaurants


def register_all():
    register(**bash.TOOL_DEF)
    register(**read_file.TOOL_DEF)
    register(**write_file.TOOL_DEF)
    register(**web_search.TOOL_DEF)
    register(**image_generate.TOOL_DEF)
    register(**tts.TOOL_DEF)
    register(**todo.TOOL_DEF)
    register(**petrestaurants.TOOL_DEF)
