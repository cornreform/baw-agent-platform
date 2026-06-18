"""
BAW Token Killer — systemic tool output compression layer.

Applies RTK's four strategies (Filter, Group, Truncate, Deduplicate)
as a pre-context injection pipeline between tool execution and LLM context.

Design philosophy:
  LLMs don't need raw terminal output. They need information.
  - Filter: strip noise (ANSI, progress bars, boilerplate)
  - Group: aggregate similar items (lint by file, errors by type)
  - Truncate: keep head + tail, drop redundant middle
  - Deduplicate: collapse repeated lines with counts

Token savings target: 60-80% reduction on tool outputs.
"""
import re
import logging

logger = logging.getLogger("baw.token_killer")

# ─── Constants ───────────────────────────────────────────────────
MAX_TOOL_OUTPUT_CHARS = 12000   # hard cap per tool result in context
MAX_SCHEMATIC_CHARS = 24000     # higher cap for circuit/schematic content
MAX_TERMINAL_CHARS = 8000       # tighter cap for terminal output
MAX_SEARCH_PER_FILE = 3         # max matches per file in search
MAX_WEB_EXTRACT_CHARS = 2000    # per web_extract result

# Patterns that indicate schematic/circuit content — needs higher fidelity
SCHEMATIC_PATTERNS = [
    re.compile(r"(?:電路|電氣|線路|schematic|circuit|wiring|pinout|connector)", re.IGNORECASE),
    re.compile(r"(?:sensor|hall|transducer|analog|digital)\s+(?:input|output|signal|interface)", re.IGNORECASE),
    re.compile(r"(?:Vcc|GND|pull.up|current.sense|ADC|PWM|GPIO)", re.IGNORECASE),
    re.compile(r"(?:2-wire|3-wire|current.mode|voltage.mode|active.sensor)", re.IGNORECASE),
    re.compile(r"pin\s*(?:\d+|assignment|configuration|mapping)", re.IGNORECASE),
]

NOISE_PATTERNS = [
    re.compile(r"\x1b\[[0-9;]*[a-zA-Z]"),  # ANSI escape codes
    re.compile(r"^\s*(?:#+\s*)?=+\s*$", re.MULTILINE),  # separator lines
    re.compile(r"\[\s*[\d.]+\s*/\s*[\d.]+\s*\]"),  # progress counters
    re.compile(r"\b(?:ETA|elapsed):?\s*[\d:]+", re.IGNORECASE),
    re.compile(r"^\s*(?:Building|Compiling|Downloading|Installing|Processing)\b.*$", re.MULTILINE),
    re.compile(r"\[\s*[\d.]+\s*%\s*\].*$", re.MULTILINE),  # progress bars
]

BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*$", re.MULTILINE),  # empty lines (handled separately)
]

# Commands where we only care about failures
TEST_COMMANDS = {
    "pytest", "cargo test", "go test", "npm test", "yarn test",
    "jest", "vitest", "rspec", "mocha", "unittest",
}

# Commands where success is just "ok"
SHORT_CONFIRM_COMMANDS = {
    "git add", "git commit", "git push", "git pull", "git fetch",
    "docker compose build", "docker build", "pip install", "npm install",
    "cargo build", "cargo install",
}


def _filter_ansi(text: str) -> str:
    """Strip ANSI escape codes from text."""
    for pat in NOISE_PATTERNS:
        text = pat.sub("", text)
    return text


def _collapse_empty_lines(text: str) -> str:
    """Replace 3+ consecutive blank lines with 2."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _deduplicate_lines(text: str) -> str:
    """Collapse 3+ consecutive identical lines into 'line [xN]'."""
    lines = text.split("\n")
    if len(lines) < 3:
        return text

    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if i < len(lines) - 2 and line == lines[i + 1] == lines[i + 2]:
            count = 3
            i += 3
            while i < len(lines) and lines[i] == line:
                count += 1
                i += 1
            # Keep first occurrence + count marker
            result.append(f"{line}  [x{count}]")
        else:
            result.append(line)
            i += 1

    return "\n".join(result)


def _truncate_smart(text: str, max_chars: int) -> str:
    """Truncate text smartly: keep head (70%) + tail (30%) with skip marker."""
    if len(text) <= max_chars:
        return text
    head_size = int(max_chars * 0.7)
    tail_size = int(max_chars * 0.3)
    skipped = len(text) - max_chars
    head = text[:head_size]
    tail = text[-tail_size:]
    return f"{head}\n\n┈┈┈ [{skipped} chars skipped] ┈┈┈\n\n{tail}"


def _extract_failures(text: str, command: str) -> str:
    """For test commands, extract failure lines only."""
    failure_keywords = ["FAIL", "FAILED", "ERROR", "FAILURE", "Traceback",
                        "assert", "AssertionError", "panic", "✗", "✘", "❌"]
    lines = text.split("\n")
    failures = []
    in_traceback = False

    for line in lines:
        stripped = line.strip()
        if any(kw in stripped for kw in ["Traceback (most recent call last)", "thread '<"]):
            in_traceback = True
        if in_traceback:
            failures.append(line)
            if stripped == "" or "Error:" in stripped:
                in_traceback = False
            continue
        if any(kw in stripped for kw in failure_keywords):
            failures.append(line)

    if failures:
        return (
            f"[{command} failures only — {len(failures)} lines]\n" +
            "\n".join(failures)
        )
    # No failures found — compact summary
    total_lines = len(lines)
    return f"[{command}] All tests passed. ({total_lines} lines of output omitted)"


def _compress_terminal(name: str, output: str, args: dict) -> str:
    """Compress terminal/bash tool output."""
    command = args.get("command", "")

    # Filter ANSI + noise
    output = _filter_ansi(output)

    # Extract failures for test commands
    for tc in TEST_COMMANDS:
        if tc in command:
            return _extract_failures(output, command)

    # Short confirm for known commands
    for sc in SHORT_CONFIRM_COMMANDS:
        if command.strip().startswith(sc):
            # Return compact success summary
            lines = output.strip().split("\n")
            key_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
            if not key_lines:
                return f"[{sc}] ok"
            return f"[{sc}] ok — " + key_lines[-1][:200]

    # Git-specific compression
    if command.strip().startswith("git diff"):
        return _compress_git_diff(output)
    if command.strip().startswith("git status"):
        return _compress_git_status(output)
    if command.strip().startswith("git log"):
        return _compress_git_log(output)

    # Generic: deduplicate + truncate
    output = _collapse_empty_lines(output)
    output = _deduplicate_lines(output)
    if len(output) > MAX_TERMINAL_CHARS:
        output = _truncate_smart(output, MAX_TERMINAL_CHARS)
        logger.debug(f"[TokenKiller] terminal output compressed: {len(output)} → {MAX_TERMINAL_CHARS}")

    return output.strip()


def _compress_git_diff(output: str) -> str:
    """Compress git diff: keep file headers + changed line counts."""
    lines = output.split("\n")
    result = []
    for line in lines:
        # Keep diff stats and file headers
        if (line.startswith("diff --git") or
            line.startswith("---") or
            line.startswith("+++") or
            line.startswith("@@") or
            re.match(r"^\d+ files? changed", line) or
            re.match(r"^\d+ insertion", line) or
            re.match(r"^\d+ deletion", line)):
            result.append(line)
        # Skip actual code lines (+/- lines) — too much noise
    return "\n".join(result)


def _compress_git_status(output: str) -> str:
    """Compress git status: group by status type."""
    lines = output.strip().split("\n")
    if not lines:
        return output
    # Keep branch header + per-status counts
    staged = []
    unstaged = []
    untracked = []
    for line in lines:
        line = line.strip()
        if line.startswith("On branch"):
            pass  # skip
        elif line.startswith("modified:") or "modified:" in line:
            unstaged.append(line.strip())
        elif line.startswith("new file:") or "new file:" in line:
            staged.append(line.strip())
        elif line.startswith("deleted:") or "deleted:" in line:
            unstaged.append(line.strip())
        elif line and not line.startswith("(") and not line.startswith("no changes"):
            untracked.append(line.strip())

    parts = []
    if staged:
        parts.append(f"Staged: {', '.join(staged[:10])}")
    if unstaged:
        parts.append(f"Modified: {', '.join(unstaged[:10])}")
    if untracked:
        parts.append(f"Untracked: {', '.join(untracked[:10])}")
    return "\n".join(parts) if parts else "clean"


def _compress_git_log(output: str) -> str:
    """Compress git log: one line per commit."""
    lines = output.strip().split("\n")
    result = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("commit "):
            result.append(line)
    return "\n".join(result[:20])  # max 20 commits


def _compress_search_files(output: str, args: dict) -> str:
    """Compress search_files output: group by file, limit per-file matches."""
    lines = output.strip().split("\n")
    if len(lines) <= MAX_SEARCH_PER_FILE * 3:
        return output  # small enough

    # Group by file
    from collections import defaultdict
    file_groups = defaultdict(list)
    for line in lines:
        # Match "path:line:content" or "path:line:content" format
        if ":" in line:
            parts = line.split(":", 2)
            if len(parts) >= 2:
                fname = parts[0]
                file_groups[fname].append(line)

    result = []
    total_shown = 0
    for fname, matches in sorted(file_groups.items()):
        shown = matches[:MAX_SEARCH_PER_FILE]
        result.extend(shown)
        total_shown += len(shown)
        if len(matches) > MAX_SEARCH_PER_FILE:
            result.append(f"  ... ({len(matches) - MAX_SEARCH_PER_FILE} more in {fname})")

    if total_shown < len(lines):
        result.append(f"\n[Total: {len(lines)} matches → shown {total_shown}]")

    return "\n".join(result)


def _compress_web_search(output: str, args: dict) -> str:
    """Compress web search results: keep title + first 200 chars."""
    # web_search output is often pre-formatted by the tool
    # Just truncate if too long
    if len(output) > MAX_TERMINAL_CHARS:
        return _truncate_smart(output, MAX_TERMINAL_CHARS)
    return output


def _is_schematic_content(text: str) -> bool:
    """Check if text contains circuit/schematic content that needs higher fidelity."""
    sample = text[:3000]  # check first 3K chars
    return any(pat.search(sample) for pat in SCHEMATIC_PATTERNS)


# ─── Public API ──────────────────────────────────────────────────

def compress_tool_output(name: str, raw_output: str, args: dict | None = None) -> str:
    """Compress tool output before it enters LLM context.
    
    Args:
        name: Tool name (e.g., 'terminal', 'read_file', 'search_files')
        raw_output: Raw tool output string
        args: Tool arguments dict (for context-aware compression)
        
    Returns:
        Compressed output string
    """
    if args is None:
        args = {}

    # Skip compression for empty/short outputs
    if len(raw_output) < 500:
        return raw_output

    try:
        if name in ("terminal", "bash"):
            output = _compress_terminal(name, raw_output, args)
        elif name in ("search_files", "grep", "rg"):
            output = _compress_search_files(raw_output, args)
        elif name in ("web_search", "web_extract"):
            output = _compress_web_search(raw_output, args)
        elif name == "read_file":
            # Determine if this is schematic/circuit content — needs higher fidelity
            _cap = MAX_SCHEMATIC_CHARS if _is_schematic_content(raw_output) else MAX_TOOL_OUTPUT_CHARS
            if len(raw_output) > _cap:
                output = _truncate_smart(raw_output, _cap)
            else:
                output = raw_output
        else:
            # Generic: deduplicate + cap
            output = _collapse_empty_lines(raw_output)
            output = _deduplicate_lines(output)
            if len(output) > MAX_TOOL_OUTPUT_CHARS:
                output = _truncate_smart(output, MAX_TOOL_OUTPUT_CHARS)

        # Track savings
        saved = len(raw_output) - len(output)
        if saved > 1000:
            logger.debug(
                f"[TokenKiller] {name}: {len(raw_output)} → {len(output)} chars "
                f"({saved / len(raw_output) * 100:.0f}% saved)"
            )

        return output

    except Exception as e:
        logger.warning(f"[TokenKiller] compression error for {name}: {e}")
        # On error, return raw but capped
        if len(raw_output) > MAX_TOOL_OUTPUT_CHARS:
            return _truncate_smart(raw_output, MAX_TOOL_OUTPUT_CHARS)
        return raw_output


# ─── Utility: task complexity detection ──────────────────────────

# ─── Court Activation Logic ──────────────────────────────────────

# Actions that SHOULD trigger the adversarial court (Devil+Angel review)
COURT_ACTIVATE_PATTERNS = [
    # ── System modification ──
    "write_file", "patch", "delete", "remove", "rm ",
    "install", "uninstall", "pip install", "apt ", "brew ",
    "config", ".env", "systemd", "systemctl",
    "docker compose", "docker build", "docker run",
    "git push", "git commit", "git merge", "git rebase",
    "deploy", "publish", "release",
    "migrate", "upgrade", "downgrade",
    # ── Irreversible / dangerous ──
    "rm -rf", "drop table", "format", "truncate",
    "purge", "reset --hard", "clean -df",
    "chmod 777", "chown",
    "overwrite", "force push",
    # ── Architectural / design decisions ──
    "refactor", "restructure", "redesign", "rearchitect",
    "架構", "重構", "重新設計",
    # ── Financial / cost decisions ──
    "buy", "purchase", "subscribe",
    "deploy to production", "production deploy",
    "charge", "bill",
    # ── Physical/electrical safety (sensor tap, wiring modification) ──
    # Any recommendation that touches vehicle/hardware wiring MUST go through court
    "接線", "並聯", "串聯", "直插", "T-tap", "tap", "parallel tap",
    "駁線", "駁電", "駁 sensor", "飛線", "跳線",
    "剝皮", "剪斷", "剪線", "cut wire", "splice",
    "高壓", "電源線", "接地線", "signal wire",
    "sensor pin", "sensor 腳", "pin assignment",
    "直接串", "直接並", "直接接", "直駁",
    "chassis ground", "firewall grommet",
]

# Actions that are ALWAYS safe to bypass court
COURT_BYPASS_PATTERNS = [
    # ── Pure information gathering ──
    "what is", "how do i", "explain", "show me",
    "list", "check", "status", "query",
    "search", "find", "lookup",
    "read", "cat", "view",
    "列出", "顯示", "查詢", "搵", "睇", "點樣",
    "什麼是", "幫我查", "幫我搵",
    # ── Simple descriptive tasks ──
    "describe", "summarize", "translate",
    "what does", "how does", "why is",
    "分析", "解釋", "翻譯",
    # ── User explicitly wants quick mode ──
    "[quick]", "[btw]", "/btw", "/quick",
]


def should_activate_court(prompt: str) -> bool:
    """Determine if the adversarial court (Devil + Angel) should run.
    
    Court is activated when:
    1. System-modifying actions (write, delete, deploy, config change)
    2. Irreversible or dangerous operations
    3. Architectural / design decisions
    
    Court is bypassed when:
    1. Pure information gathering (read, search, explain)
    2. Simple single-step tasks
    3. User explicitly requests quick mode (/btw, [quick])
    
    Returns True if court should activate, False to bypass.
    """
    prompt_lower = prompt.lower()
    prompt_len = len(prompt)
    
    # ── Explicit bypass: user signals quick mode ──
    for bp in ["[quick]", "[btw]", "/btw", "/quick", "[fast]", "/fast"]:
        if bp in prompt_lower:
            return False
    
    # ── Very short prompts (≤100 chars): too simple for court ──
    if prompt_len < 100:
        return False
    
    # ── Check for court-activating actions ──
    activation_hits = []
    for pat in COURT_ACTIVATE_PATTERNS:
        if pat in prompt_lower:
            activation_hits.append(pat)
    
    if activation_hits:
        # At least one system-modifying action detected → activate
        return True
    
    # ── Check for bypass patterns (pure information) ──
    for pat in COURT_BYPASS_PATTERNS:
        if pat in prompt_lower:
            # Information-gathering task → bypass
            return False
    
    # ── Default: moderate complexity with no clear signal ──
    # Medium-length prompts (100-800 chars) with no modification signals
    # → activate court for safety, unless clearly informational
    if prompt_len < 400 and "?" in prompt:
        return False  # short questions → bypass
    
    return True  # default: activate for safety


def estimate_task_complexity(prompt: str) -> str:
    """Estimate task complexity to skip unnecessary overhead.
    
    Returns: 'simple', 'moderate', or 'complex'
    """
    prompt_lower = prompt.lower()
    prompt_len = len(prompt)

    complex_indicators = ["build", "deploy", "refactor", "migrate",
                          "implement", "架構", "重構", "部署", "設計",
                          "multi-step", "pipeline", "workflow",
                          "audit", "審計", "cleanup", "清理",
                          "優化", "optimize", "診斷", "diagnose",
                          "修復", "repair", "fix all", "全部",
                          "系統審計", "system audit", "全面", "comprehensive"]

    # Complex: modification + multi-step
    if prompt_len > 500 and any(ind in prompt_lower for ind in complex_indicators):
        return "complex"

    # Check court activation — if court not needed, task is simpler
    if not should_activate_court(prompt):
        return "simple"

    # Default to moderate
    return "moderate"
