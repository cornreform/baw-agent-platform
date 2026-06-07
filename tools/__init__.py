"""BAW built-in: register all built-in tools"""

from . import bash, read_file, write_file
from ..core.tools import register

def register_all():
    register(**bash.TOOL_DEF)
    register(**read_file.TOOL_DEF)
    register(**write_file.TOOL_DEF)
