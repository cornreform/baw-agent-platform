# BAW UX Audit — User Experience & Conversation Flow Issues

> **Audit Date**: 2026-06-12  
> **Auditor**: Hermes Agent (subagent delegation)  
> **Scope**: CLI (`cli/main.py`, `cli/commands/*.py`), Telegram Bot (`core/messaging/telegram.py`), README, Knowledge Base

---

## Executive Summary

This audit maps all user-visible commands and dialog flows across BAW's three interfaces (CLI chat, TUI chat, Telegram bot), identifying 7 critical UX issues with concrete redesign proposals.

| Priority | Issue | Files Affected | Severity |
|----------|-------|----------------|----------|
| 1 | No progress indicators for multi-step tasks | `chat.py:372-430`, `tui_chat.py:333-419` | High |
| 2 | No command discovery / tab completion | `main.py:223-313`, `tui_chat.py:436-451` | High |
| 3 | No undo / state rollback for destructive commands | `chat.py:428-434`, `tui_chat.py:428-434` | Medium |
| 4 | Dead-end error states with no recovery path | `telegram.py:197-199`, `chat.py:425-428` | Medium |
| 5 | Missing onboarding for first-time users | `main.py:349-352`, `telegram.py:103-131` | Medium |
| 6 | Inconsistent Telegram response patterns | `telegram.py:146-199` | Low |
| 7 | No cancel/abort for long-running tool calls | `chat.py:364-484`, `tui_chat.py:340-419` | Low |

---

## 1. User Command & Dialog Flow Map

### 1.1 CLI Entry Points

| Command | Entry File | Line | Description |
|---------|-----------|------|-------------|
| `baw` (default) | `main.py:349-352` | L349 | Opens `cmd_chat()` → `chat.py` |
| `baw --help` | `main.py:329-332` | L329 | Shows `_show_help()` |
| `baw <subcommand>` | `main.py:355-419` | L355 | Routes to command handler |
| `baw tui-chat` | `main.py:394-396` | L394 | Opens Textual TUI |

### 1.2 CLI Chat Slash Commands

**File**: `cli/commands/chat.py:341-343`

| Command | Handler | Line | Description |
|---------|---------|------|-------------|
| `/help` | display help table | L341 | Shows all slash commands |
| `/model` | switch model | L341 | Switch or list models |
| `/tone` | switch tone | L341 | Switch tone profile |
| `/config` | view config | L342 | Show config |
| `/soul` | view SOUL | L342 | Show identity rules |
| `/session` | session info | L342 | Show session stats |
| `/clear` | reset chat | L343 | Clear messages |
| `/exit` | quit | L343 | Exit chat |

### 1.3 TUI Chat Slash Commands

**File**: `cli/commands/tui_chat.py:421-500`

| Command | Handler | Line | Description |
|---------|---------|------|-------------|
| `/exit`, `/quit`, `/q` | exit app | L425-427 | Quit TUI |
| `/clear`, `/reset` | clear chat | L428-434 | Reset conversation |
| `/help` | show help | L436-452 | Display command table |
| `/status` | show status | L454-466 | Session + token stats |
| `/model <id>` | switch model | L468-480 | Change model |
| `/tone <name>` | switch tone | L482-491 | Change tone |
| `/model` (list) | list models | L493-500 | Show available models |

### 1.4 Telegram Bot Commands

**File**: `core/messaging/telegram.py:103-131`

| Command | Handler | Description |
|---------|---------|-------------|
| `/start` | welcome | Welcome message |
| `/help` | show all | Display all commands |
| `/status` | system status | BAW health + sessions |
| `/btw` | quick answer | No court, direct response |
| `/model` | model switch | Switch or show selector |
| `/mode` | mode switch | quick/hybrid/tight |
| `/tone` | tone switch | casual/business/teaching |
| `/set` | persist config | `/set key value` |
| `/court` | show verdict | Last Angel/Devil result |
| `/fresh` | raw model | No soul, no memories |
| `/memory` | save memory | Save entry |
| `/search` | search memories | Search store |
| `/board` | generate HTML | Dashboard |
| `/task` | session mgmt | new/list/resume/save/forget/info |
| `/new` | fresh session | Save + start new |
| `/reset` | hard reset | Clear without saving |
| `/resume` | resume session | Resume saved |
| `/summarize` | LLM summary | Summarize current |
| `/pickup` | resume last | Resume interrupted |
| `/reload` | hot reload | Reload tools/config |
| `/evolve` | self-evo stats | Evolution stats |
| `/tts` | toggle TTS | on/off/status |
| `/capability` | manage caps | Capability routing |
| `/update` | self-update | Git pull + restart |
| `/stop` | cancel request | Cancel running |
| `/restart` | restart engine | Restart BAW |

---

## 2. UX Issues Detailed

### Issue 1: No Progress Indicators for Multi-Step Tasks

**Location**: `cli/commands/chat.py:364-430`, `cli/commands/tui_chat.py:333-419`

**Problem**: During agent loop execution (tool calling turns), user sees only:
- A spinner (`"model_id thinking…"`) during streaming
- Tool icon + name when executing (`"🔍 web_search({...})"`)

There is **no step-by-step progress** showing:
- Which step of the plan is executing
- ETA or progress percentage
- What the tool is doing (e.g., "Searching DuckDuckGo for 'X'...")
- Success/failure per step

**Current Code** (`chat.py:365-372`):
```python
spinner = Spinner("dots2", text=f"[baw.muted]{model_id} thinking…[/]", style="baw.muted")
text_buffer = ""
...
with Live(spinner, console=plain_console, refresh_per_second=10, transient=True) as live:
```

**Proposed Redesign**:
```
🔍 [1/3] Searching DuckDuckGo for "Python async best practices"...
   ↓ Found 5 results
📄 [2/3] Reading python-doc.readthedocs.io...
   ↓ Extracted 2.3KB
✏️ [3/3] Writing /home/user/baw/notes.md...
   ✅ Complete
```

Add a progress callback to `_run_agent()`:
```python
def _progress(step: int, total: int, action: str, detail: str):
    """Callback for progress updates."""
    pct = step / total * 100
    bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
    plain_console.print(f"[baw.purple]{step}/{total}[/] [{bar}] {action}: {detail}")
```

---

### Issue 2: No Command Discovery / Tab Completion

**Location**: `cli/main.py:223-313` (help display), `cli/commands/tui_chat.py:436-452`

**Problem**: 
- Users must type `/help` or `baw --help` to discover commands
- No tab completion in any interface
- No fuzzy search for commands
- 26 Telegram commands but no inline keyboard hint after first message

**Current Code** (`main.py:292-312`):
```python
slash = Table(box=None, show_header=False, padding=(0, 2), expand=True)
slash.add_column(style="baw.cmd", width=16, no_wrap=True)
slash.add_column(style="baw.dim", width=28)
# ... static rows added manually
```

**Proposed Redesign**:

**A. Add inline command suggestions after /help** (Telegram):
```python
# After /help response, add inline keyboard
reply_markup = [
    ["/model", "/tone", "/status"],
    ["/clear", "/session", "/board"],
    ["/task new", "/memory", "/tts"]
]
# Show hint: "Tip: Tap a command to use it"
```

**B. Add shell tab completion** (new file `cli/completion.py`):
```python
#!/usr/bin/env python3
"""Shell completion for BAW."""
import sys

COMMANDS = {
    "chat": ["chat", "status", "models", "config", "soul", "skill", "memory",
             "sessions", "logs", "dashboard", "tui-chat", "restart", "rebuild"],
    "slash": ["/help", "/model", "/tone", "/status", "/clear", "/exit", "/task", "/memory"]
}

def completer(prefix: str, state: int) -> str | None:
    """Readline completer."""
    options = [c for c in COMMANDS.get("chat", []) if c.startswith(prefix)]
    if state < len(options):
        return options[state]
    return None

if __name__ == "__main__":
    print(f"complete -C '{__file__}' baw")
    print('complete -F _baw_completion baw')
```

**C. Add "Did you mean?" on unknown command** (`main.py:359-362`):
```python
# Current: just error
console.print(f"[baw.error]Unknown command:[/baw.error] {cmd}")

# Proposed: fuzzy match
from difflib import get_close_matches
candidates = list(COMMANDS.keys())
suggestions = get_close_matches(cmd, candidates, n=2, cutoff=0.6)
if suggestions:
    console.print(f"[baw.dim]Did you mean: {', '.join(suggestions)}?[/baw.dim]")
```

---

### Issue 3: No Undo / State Rollback for Destructive Commands

**Location**: `cli/commands/chat.py:428-434`, `cli/commands/tui_chat.py:428-434`

**Problem**:
- `/clear` or `/reset` immediately wipes conversation history
- No confirmation prompt
- No way to restore previous state
- `write_file` tool overwrites without backup

**Current Code** (`tui_chat.py:428-434`):
```python
if v in ("/clear", "/reset"):
    self._messages = [{"role": "system", "content": self._build_sysprompt()}]
    self._total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    log.clear()
    log.write("[dim]🧹 Cleared[/]")
    self._refresh_status()
    return
```

**Proposed Redesign**:

**A. Add confirmation for destructive commands**:
```python
if v in ("/clear", "/reset"):
    # Check if there's meaningful history
    if len(self._messages) > 2:
        log.write("[yellow]⚠️ This will clear all conversation history.[/]")
        log.write("[yellow]Type /confirm to proceed, or /cancel to keep.[/]")
        self._awaiting_confirm = True
        return
    # Proceed if minimal history
```

**B. Add session snapshot before clear**:
```python
# Before clear, auto-save to sessions/
def _snapshot_before_clear():
    import json
    from pathlib import Path
    sd = Path.home() / ".baw" / "sessions"
    sd.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = sd / f"auto_{ts}.jsonl"
    with open(path, "w") as f:
        for m in self._messages:
            f.write(json.dumps(m) + "\n")
    log.write(f"[dim]💾 Auto-saved to {path.name}[/]")
```

**C. Add "undo" for write_file tool**:
```python
# In tool executor, keep revision history
def _exec_write_file(path: str, content: str) -> str:
    p = Path(path).expanduser().resolve()
    if p.exists():
        # Backup before overwrite
        backup = p.with_suffix(p.suffix + ".bak")
        p.rename(backup)
    p.write_text(content)
    return json.dumps({"ok": True, "path": str(p), "backup": str(backup)})
```

---

### Issue 4: Dead-End Error States with No Recovery Path

**Location**: `core/messaging/telegram.py:197-199`, `cli/commands/chat.py:425-428`

**Problem**:
- API errors display just the exception message
- No recovery suggestions
- No "retry with different model" option
- No "view error details" toggle

**Current Code** (`telegram.py:197-199`):
```python
except Exception as e:
    logger.error(f"[Telegram] send error: {e}")
    return ""
```

**Proposed Redesign**:

**A. Rich error response in Telegram**:
```python
except Exception as e:
    logger.error(f"[Telegram] send error: {e}")
    # Determine error type
    error_type = _classify_error(e)
    recovery = {
        "rate_limit": "⏳ Rate limited. Try again in 30s or use /model switch.",
        "auth": "🔑 Auth failed. Check /config api_key or run /reload.",
        "timeout": "⏱️ Timeout. Try /model faster or /task cancel.",
        "unknown": "❌ Error: {e}. Try /status check health."
    }.get(error_type, f"❌ Error: {e}")
    return recovery
```

**B. Add /retry command**:
```python
async def _handle_retry(self, chat_id: str):
    """Retry last failed operation."""
    if not self._last_failed:
        return "No recent failure to retry."
    # Re-run with exponential backoff
    for attempt in range(3):
        try:
            result = await self._run_operation(self._last_failed)
            return result
        except Exception as e:
            wait = 2 ** attempt
            self.send(chat_id, f"⏳ Retry {attempt+1}/3 failed, waiting {wait}s...")
            time.sleep(wait)
    return "❌ All retries exhausted. Last error: {e}"
```

---

### Issue 5: Missing Onboarding for First-Time Users

**Location**: `cli/main.py:349-352`, `telegram.py:103-131`

**Problem**:
- Running `baw` without config goes directly to chat attempt
- No guided setup prompt
- API key missing errors are cryptic
- No "first run" detection

**Current Code** (`main.py:349-352`):
```python
if len(sys.argv) < 2:
    # Default: interactive chat
    from cli.commands.chat import cmd_chat
    cmd_chat()
    return
```

**Proposed Redesign**:

**A. First-run detection and wizard prompt**:
```python
def _check_first_run(cfg) -> bool:
    """Check if user is first-time."""
    return not (Path.home() / ".baw" / "config.yaml").exists()

if len(sys.argv) < 2:
    if _check_first_run(cfg):
        console.print(Panel(
            "[baw.gold]👋 Welcome to BAW![/]\n\n"
            "This appears to be your first run.\n"
            "Would you like to:\n"
            "  1. Run [baw.purple]baw --setup[/baw.purple] for guided setup\n"
            "  2. Run [baw.purple]baw --doctor[/baw.purple] to check health\n"
            "  3. Continue to chat (may fail without API key)",
            title="🚀 First Run Detected",
            border_style="baw.accent"
        ))
        return
    cmd_chat()
```

**B. Add welcome message to Telegram /start**:
```python
# In telegram.py, enhance /start
def _handle_start(self, chat_id: str):
    welcome = """🖤 Welcome to BAW — Black And White

I'm an AI agent platform with:
• 🤖 Multi-model support (DeepSeek, MiniMax, Kimi)
• 🔍 Web search + file tools
• 📁 Persistent memory
• 🎙️ Voice input (STT)

Quick commands:
/help — All commands
/status — System health
/model — Switch model
/tone — Switch tone

Get started: Send me a message or try /board for dashboard!
"""
    self.send(chat_id, welcome)
```

---

### Issue 6: Inconsistent Telegram Response Patterns

**Location**: `core/messaging/telegram.py:146-199`

**Problem**:
- Some responses use Markdown, some plain
- Error messages vary in format
- Inline edits use different patterns
- No consistent emoji prefixes

**Current Patterns**:
- Success: `✅ Done` or `✅ {action}`
- Error: `❌ Error: {e}` or just `{e}`
- Progress: Inline edits vs new messages

**Proposed Redesign**:

**A. Standard response wrapper**:
```python
def _format_response(self, msg_type: str, content: str, edit_msg_id: str = "") -> str:
    """Format consistent responses."""
    patterns = {
        "success": f"✅ {content}",
        "error": f"❌ Error: {content}",
        "info": f"ℹ️ {content}",
        "progress": f"⏳ {content}",
        "warning": f"⚠️ {content}",
    }
    prefix = patterns.get(msg_type, content)
    if edit_msg_id:
        return self._edit_text(chat_id, edit_msg_id, prefix)
    return self._send_text(chat_id, prefix)
```

**B. Apply consistently**:
```python
# Replace scattered patterns:
# OLD:
self.send(chat_id, f"📥 Downloading **{file_name}**...")

# NEW:
self._format_response("progress", f"Downloading **{file_name}**...", edit_msg_id=status_id)
```

---

### Issue 7: No Cancel/Abort for Long-Running Tool Calls

**Location**: `cli/commands/chat.py:364-484`, `cli/commands/tui_chat.py:340-419`

**Problem**:
- Once tool starts, cannot cancel
- No Ctrl+C equivalent during streaming
- Must wait for timeout or completion
- No "stop after this step" option

**Current Code** (`chat.py:372-384`):
```python
with Live(spinner, console=plain_console, refresh_per_second=10, transient=True) as live:
    try:
        for chunk in client.chat.completions.create(...):
            # Streaming - no way to interrupt
```

**Proposed Redesign**:

**A. Add /stop slash command**:
```python
# In chat.py, add flag
_running = True

def _handle_stop():
    """Stop current operation."""
    global _running
    _running = False
    console.print("[yellow]🛑 Stopping after current step...[/]")

# During streaming loop
for chunk in client.chat.completions.create(...):
    if not _running:
        console.print("[yellow]🛑 Stopped by user[/]")
        break
```

**B. Add keyboard interrupt handler**:
```python
def _interrupt_handler(signum, frame):
    console.print("\n[yellow]🛑 Interrupted. Saving state...[/]")
    # Auto-save current messages
    _snapshot_session()
    sys.exit(0)

signal.signal(signal.SIGINT, _interrupt_handler)
```

**C. Add Telegram /stop command**:
```python
# Already exists in telegram.py but not implemented
# Line 129: {"command": "stop", "description": "Cancel running request"},
async def _handle_stop(self, chat_id: str):
    """Cancel running request."""
    if self._current_task:
        self._current_task.cancel()
        return "🛑 Request cancelled."
    return "No running request to stop."
```

---

## 3. Summary of Redesign Proposals

### Priority 1 (High Impact)

| # | Proposal | Files to Modify | Lines |
|---|---------|-----------------|-------|
| 1.1 | Add progress callback to agent loop | `chat.py:360-484`, `tui_chat.py:333-419` | New function |
| 1.2 | Add shell tab completion | New file `cli/completion.py` | New |
| 1.3 | Add "Did you mean?" fuzzy match | `main.py:359-362` | +5 lines |

### Priority 2 (Medium Impact)

| # | Proposal | Files to Modify | Lines |
|---|---------|-----------------|-------|
| 2.1 | Add confirmation for /clear | `tui_chat.py:428-434` | +15 lines |
| 2.2 | Auto-snapshot before destructive commands | `tui_chat.py:428-434` | +10 lines |
| 2.3 | Add file backup before write_file | `chat.py:191-199` | +8 lines |
| 2.4 | Rich error responses | `telegram.py:197-199` | +30 lines |
| 2.5 | First-run detection + wizard prompt | `main.py:349-352` | +20 lines |

### Priority 3 (Low Impact)

| # | Proposal | Files to Modify | Lines |
|---|---------|-----------------|-------|
| 3.1 | Standardize Telegram response format | `telegram.py:146-199` | Refactor |
| 3.2 | Implement /stop in agent loop | `chat.py:364-484` | +15 lines |
| 3.3 | Add inline keyboard hints | `telegram.py:103-131` | +20 lines |

---

## 4. Appendix: File Reference

| File | Lines | Purpose |
|------|-------|---------|
| `cli/main.py` | 423 | CLI entry, command routing |
| `cli/commands/chat.py` | 699 | Interactive chat REPL |
| `cli/commands/tui_chat.py` | 506 | Textual TUI chat |
| `cli/commands/dashboard.py` | 305 | Live dashboard TUI |
| `core/messaging/telegram.py` | 1708 | Telegram bot connector |
| `README.md` | 520 | Documentation |
| `knowledge/INDEX.md` | 577 | Knowledge base |

---

*End of Audit*