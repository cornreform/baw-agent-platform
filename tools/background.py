"""BAW — Background Command Management

Run long-lived commands (servers, watchers) without blocking.
Three operations:
  - start: run command in background, get a session ID
  - output: get new output since last check
  - stop: terminate a background process
"""

import os
import signal
import subprocess
import time
import threading
from pathlib import Path


# ── Global process registry ──

_processes: dict[str, dict] = {}  # id -> {proc, cmd, stdout_lines, stderr_lines, ...}
_lock = threading.Lock()
_counter = 0


def _next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"bg{_counter}"


def start_bg(command: str, workdir: str | None = None) -> str:
    """Start a command in the background.

    Args:
        command: Shell command to run.
        workdir: Working directory (default: current).

    Returns:
        Confirmation with session ID.
    """
    if not command.strip():
        return "Error: command is required"

    bg_id = _next_id()

    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            cwd=workdir or os.getcwd(),
            text=True,
            preexec_fn=os.setsid,  # Create process group for easy kill
        )
    except Exception as e:
        return f"Error starting background process: {e}"

    entry = {
        "id": bg_id,
        "command": command,
        "proc": proc,
        "started": time.time(),
        "stdout_lines": [],
        "stderr_lines": [],
        "last_read": 0,
        "done": False,
        "exit_code": None,
    }

    with _lock:
        _processes[bg_id] = entry

    # Start collector threads
    def _collect_stdout():
        try:
            for line in iter(proc.stdout.readline, ""):
                with _lock:
                    entry["stdout_lines"].append(line.rstrip())
        except (ValueError, OSError):
            pass

    def _collect_stderr():
        try:
            for line in iter(proc.stderr.readline, ""):
                with _lock:
                    entry["stderr_lines"].append(line.rstrip())
        except (ValueError, OSError):
            pass

    def _wait_and_cleanup():
        exit_code = proc.wait()
        # Signal collectors to stop
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass
        with _lock:
            entry["done"] = True
            entry["exit_code"] = exit_code

    t1 = threading.Thread(target=_collect_stdout, daemon=True)
    t2 = threading.Thread(target=_collect_stderr, daemon=True)
    t3 = threading.Thread(target=_wait_and_cleanup, daemon=True)
    t1.start()
    t2.start()
    t3.start()

    return f"✅ Started background process [{bg_id}]: {command[:80]}"


def output_bg(bg_id: str, clear: bool = False) -> str:
    """Get output from a background process.

    Args:
        bg_id: Process ID (e.g., 'bg1').
        clear: If True, clear accumulated output after reading.

    Returns:
        Formatted output with status.
    """
    with _lock:
        entry = _processes.get(bg_id)
        if not entry:
            return f"Error: no background process '{bg_id}'. List with 'list_bg'."

        # Get new stdout lines since last read
        total_stdout = len(entry["stdout_lines"])
        new_stdout = entry["stdout_lines"][entry["last_read"]:]
        new_stderr = entry["stderr_lines"]

        if clear:
            entry["stdout_lines"] = []
            entry["stderr_lines"] = []
            entry["last_read"] = 0
        else:
            entry["last_read"] = total_stdout

    running_since = time.time() - entry["started"]
    status = "✅ running" if not entry["done"] else f"❌ exited (code: {entry['exit_code']})"

    lines = [f"[{bg_id}] {entry['command'][:60]} — {status} ({running_since:.0f}s)"]

    if new_stdout:
        lines.append(f"--- stdout ({len(new_stdout)} lines) ---")
        lines.extend(new_stdout[-50:])  # cap at 50 lines

    if new_stderr:
        lines.append(f"--- stderr ({len(new_stderr)} lines) ---")
        lines.extend(new_stderr[-20:])

    if not new_stdout and not new_stderr:
        lines.append("(no new output since last check)")

    return "\n".join(lines)


def stop_bg(bg_id: str) -> str:
    """Stop a background process.

    Args:
        bg_id: Process ID to stop.

    Returns:
        Confirmation.
    """
    with _lock:
        entry = _processes.get(bg_id)
        if not entry:
            return f"Error: no background process '{bg_id}'."

        proc = entry["proc"]
        done = entry["done"]

    if done:
        return f"[{bg_id}] already exited (code: {entry['exit_code']})."

    try:
        # Kill the entire process group
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        # Wait briefly
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)
            proc.wait(timeout=3)
    except ProcessLookupError:
        pass  # Already dead
    except Exception as e:
        return f"Error stopping [{bg_id}]: {e}"

    with _lock:
        entry["done"] = True
        exit_code = proc.poll() or -1
        entry["exit_code"] = exit_code

    return f"✅ Stopped [{bg_id}] (exit code: {exit_code})"


def list_bg() -> str:
    """List all background processes."""
    with _lock:
        if not _processes:
            return "No background processes running."

        lines = ["## Background Processes:"]
        for bg_id, entry in list(_processes.items()):
            status = "✅" if not entry["done"] else "❌"
            uptime = time.time() - entry["started"]
            cmd = entry["command"][:50]
            lines.append(f"  {status} [{bg_id}] cmd={cmd} uptime={uptime:.0f}s")
            if entry["done"]:
                lines[-1] += f" exit={entry['exit_code']}"

        return "\n".join(lines)


def cleanup_done() -> str:
    """Remove all completed processes from the registry."""
    with _lock:
        to_remove = [bg_id for bg_id, entry in list(_processes.items()) if entry["done"]]
        for bg_id in to_remove:
            del _processes[bg_id]

    if to_remove:
        return f"🧹 Removed {len(to_remove)} completed process(es): {', '.join(to_remove)}"
    return "No completed processes to clean."


def _dispatcher(action: str, command: str = "", bg_id: str = "",
                workdir: str = "", clear: bool = False) -> str:
    """Dispatch background actions."""
    actions = {
        "start": lambda: start_bg(command, workdir or None),  # type: ignore[arg-type]
        "output": lambda: output_bg(bg_id, clear),  # type: ignore[arg-type]
        "stop": lambda: stop_bg(bg_id),  # type: ignore[arg-type]
        "list": lambda: list_bg(),
        "cleanup": lambda: cleanup_done(),
    }
    fn = actions.get(action)
    if fn is None:
        avail = ", ".join(actions.keys())
        return f"Error: unknown action '{action}'. Available: {avail}"
    return fn()


TOOL_DEF = {
    "name": "background",
    "description": (
        "Run and manage background processes (long-lived commands). "
        "Actions: 'start' (run shell command in background), "
        "'output' (get output by bg_id), 'stop' (kill), "
        "'list' (show running/completed), 'cleanup' (remove finished entries). "
        "Use for servers, watchers, or any command that takes >60s."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "output", "stop", "list", "cleanup"],
                "description": "What to do.",
            },
            "command": {
                "type": "string",
                "description": "Shell command (required for 'start').",
            },
            "bg_id": {
                "type": "string",
                "description": "Process ID (required for 'output'/'stop').",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for 'start'.",
                "default": "",
            },
            "clear": {
                "type": "boolean",
                "description": "For 'output': if true, clear buffered output.",
                "default": False,
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
