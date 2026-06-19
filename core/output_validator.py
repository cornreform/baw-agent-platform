"""
BAW Output Validator — unified pre-delivery quality gate.

All post-processing that was scattered across _run_baw() is now
centralised here. Add new quality rules in one place.
"""

import re
import logging

logger = logging.getLogger("baw.validator")

# ── Anti-duplication: section headers that signal LLM is
#     about to repeat earlier content ──
_DEDUP_PATTERNS = [
    (r'\n*#{1,3}\s*總結\s*\n', '總結'),
    (r'\n*#{1,3}\s*Summary\s*\n', 'Summary'),
    (r'\n*#{1,3}\s*总结\s*\n', '总结'),
    (r'\n*以下係總結內容[：:]\s*\n', '以下係總結內容'),
    (r'\n*以下係總結[：:]\s*\n', '以下係總結'),
    (r'\n*以上係總結[：:]\s*\n', '以上係總結'),
    (r'\n*\*\*總結\*\*[：:]?\s*\n', '**總結**'),
    (r'\n*\*\*Summary\*\*[：:]?\s*\n', '**Summary**'),
    (r'\n*總結[：:]\s*\n', '總結：'),
]

# ── Credential patterns: API keys that must never reach user ──
_CRED_PATTERNS = [
    (r'(sk-[A-Za-z0-9]{20,})', 'sk-***REDACTED***'),
    (r'([A-Z_]{3,30}_(?:API_)?(?:KEY|SECRET|TOKEN)\s*=\s*)([\S]{20,})',
     r'\1***REDACTED***'),
    (r'(Bearer\s+)([A-Za-z0-9_\-\.]{20,})', r'\1***REDACTED***'),
    (r'("api_key"\s*:\s*")([^"]{20,})(")', r'\1***REDACTED***\3'),
    (r'([A-Za-z0-9+/]{40,}={0,2})', None),  # base64-like (skip if no replacement)
]

# ── Hallucination: LLM claiming it can't do things it can ──
_HALLUCINATION_PHRASES = [
    "無法直接讀取", "無法直接讀取你電腦上的檔案",
    "cannot access local files", "i cannot access", "我無法直接",
    "我無法讀取", "無法讀取你電腦",
    "唔支援直接 attach", "唔支援直接傳送", "唔支援直接",
    "唔支援上傳", "不能直接 attach", "cannot attach files",
    "cannot directly attach", "does not support attaching",
    "don't support sending files", "can't send files directly",
    "cannot send files", "can't attach files",
    "呢個 chat interface 唔支援", "chat interface 唔支援",
]

# ── Stale status prefixes: LLM fabricating empty status lines ──
_STALE_PREFIXES = [
    r'^[📊📋🔧🧠⚙️⏰🎨🔍🌐📖📝💻⚡✅❌❗⏳ℹ️🔄]\s*$',
    r'^\[\w+\]\s*$',
]


def validate_output(output: str, *, prompt: str = "") -> str:
    """Run all quality gates. Returns cleaned output.

    Args:
        output: Raw LLM/agent output to validate
        prompt: Original user prompt (for hallucination context)

    Returns:
        Cleaned output string
    """
    if not output or not output.strip():
        return _empty_fallback()

    result = output

    # Phase 1: Strip HTML tags (Telegram doesn't render them)
    result = _strip_html(result)

    # Phase 2: Normalise whitespace
    result = _compress_blank_lines(result)
    result = result.strip()

    # Phase 3: Security — redact credentials BEFORE anything else
    result = _redact_credentials(result)

    # Phase 4: Anti-duplication — strip trailing summary sections
    result = _strip_summary_sections(result)

    # Phase 5: Hallucination guard
    result = _check_hallucination(result, prompt)

    # Phase 6: Stale line cleanup — remove empty emoji-only lines
    result = _strip_stale_lines(result)

    # Phase 6b: Verbosity compression — collapse excessive sections
    result = _compress_verbose(result)

    # Phase 7: Length enforcement
    result = _enforce_length(result)

    # Phase 8: Final sanity — output must not be empty
    if not result.strip():
        return _empty_fallback()

    return result.strip()


# ── Internal helpers ──

def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', '', text)


def _compress_blank_lines(text: str) -> str:
    return re.sub(r'\n{3,}', '\n\n', text)


def _redact_credentials(text: str) -> str:
    redacted_count = 0
    for pattern, replacement in _CRED_PATTERNS:
        if replacement:
            new_text, n = re.subn(pattern, replacement, text)
            if n > 0:
                text = new_text
                redacted_count += n
    if redacted_count > 0:
        text = f"[SECURITY] {redacted_count} credential(s) redacted\n\n{text}"
    return text


def _strip_summary_sections(text: str) -> str:
    for pattern, label in _DEDUP_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        before = text[:match.start()]
        after = text[match.end():]
        pos_ratio = match.start() / max(len(text), 1)
        after_clean = after.strip()
        is_near_end = pos_ratio > 0.6
        is_empty_after = len(after_clean) < 20
        is_repeat = (
            after_clean
            and len(after_clean) < len(before) * 0.3
            and after_clean[:50] in before[-200:]
        )
        if is_near_end or is_empty_after or is_repeat:
            text = before.strip()
            break
    return text


def _check_hallucination(text: str, prompt: str) -> str:
    if not any(phrase in text.lower() for phrase in _HALLUCINATION_PHRASES):
        return text

    # Try to extract file path from prompt and read it
    file_match = re.search(
        r'((?:/tmp/|/home/|/app/|~/.baw/|/etc/|/var/)[^\s"\'`\u3002\uff0c？]+)',
        prompt
    )
    if not file_match:
        return "[FAIL] LLM claimed inability — no file path to auto-resolve"

    fpath = file_match.group(1)
    try:
        from pathlib import Path
        pp = Path(fpath).expanduser().resolve()
        if not pp.exists():
            return f"[FAIL] 無法讀取 `{fpath}`：檔案不存在"
        if pp.is_dir():
            items = [p.name + ('/' if p.is_dir() else '') for p in pp.iterdir()]
            return f"📂 **{fpath}**\n" + "\n".join(sorted(items))
        content = pp.read_text(encoding="utf-8")
        return f"📄 **{fpath}**\n```\n{content[:2000]}\n```"
    except Exception as e:
        return f"[FAIL] 讀檔錯誤: {e}"


def _strip_stale_lines(text: str) -> str:
    lines = text.split('\n')
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        is_stale = any(re.match(p, stripped) for p in _STALE_PREFIXES)
        if not is_stale:
            kept.append(line)
    return '\n'.join(kept)


def _enforce_length(text: str) -> str:
    # Telegram has a hard 4096-char limit per message.
    # Use 8000 for analysis/verbose tasks — Telegram splits long messages.
    if len(text) > 8000:
        text = text[:7997] + "..."
    return text


def _empty_fallback() -> str:
    return "✅ 任務已完成。（無額外輸出）"


# ── Verbosity patterns: section headers that signal bloated output ──
_SECTION_HEADER = re.compile(r'^(?:#{1,3}\s+|\*\*)([^*#\n]{3,60})(?:\*\*)?\s*$', re.MULTILINE)
_DIAGNOSIS_HEADER = re.compile(r'\n*\[PLAN\] Diagnosis.*$', re.DOTALL)
# ── Token footer: LLM per-call token breakdown ──
_TOKEN_FOOTER = re.compile(r'\n*📊\s*\*\*\d+\s+LLM calls?\*\*\s*[—\-]\s*total:\s*[\d.,]+\s*tokens\s*', re.DOTALL)
# ── Model attribution footer ──
_MODEL_FOOTER = re.compile(r'\n*📊\s*模型[：:]\s*`[^`]+`\s*(?:·\s*`[^`]+`\s*)*\n*')

def _compress_verbose(text: str) -> str:
    """Aggressively compress output that has too many sections or is too long.

    Detects:
    - >3 bold/markdown section headers → collapse to compact format
    - [PLAN] Diagnosis sections at end → strip
    - Token footer per-call breakdown → strip (keep summary line)
    """
    # Strip diagnosis sections — they're noise after a failure report
    text = _DIAGNOSIS_HEADER.sub('', text).strip()
    # Strip token footers — per-call breakdown is useless for users
    text = _TOKEN_FOOTER.sub('', text).strip()
    # Strip model attribution footers
    text = _MODEL_FOOTER.sub('', text).strip()

    # Count section headers
    sections = _SECTION_HEADER.findall(text)
    if len(sections) <= 3:
        return text  # Reasonable number of sections

    # Too many sections — extract key info and compress
    lines = text.split('\n')
    result_lines = []
    first_result_written = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result_lines.append(line)
            continue

        # Keep the first non-header, non-empty line as the lead result
        if not first_result_written and not _SECTION_HEADER.match(stripped):
            if not stripped.startswith(('[', '📊', '╔')):
                result_lines.insert(0, stripped)
                result_lines.insert(1, '')
                first_result_written = True
                continue

        # Keep only essential headers (max 2), skip the rest
        header_match = _SECTION_HEADER.match(stripped)
        if header_match:
            # Only keep if we haven't exceeded max sections
            current_headers = sum(1 for l in result_lines if _SECTION_HEADER.match(l.strip()))
            if current_headers >= 2:
                continue  # Skip this section header
            result_lines.append(line)
            continue

        # Keep bullet points under kept headers only
        if stripped.startswith(('- ', '• ', '* ')):
            # Check if previous kept line was a header
            result_lines.append(line)
            continue

        # Skip everything else
        pass

    if len(result_lines) < 3:
        return text  # Compression produced nothing useful, keep original

    return '\n'.join(result_lines).strip()


def score_output(output: str) -> dict:
    """Score output quality for self-diagnosis. Returns dict with scores."""
    scores = {
        "has_content": bool(output.strip()),
        "length": len(output),
        "has_redactions": "[SECURITY]" in output,
        "has_summary_section": any(
            re.search(p[0], output) for p in _DEDUP_PATTERNS
        ),
        "has_hallucination": any(
            phrase in output.lower() for phrase in _HALLUCINATION_PHRASES
        ),
        "blank_line_ratio": output.count('\n\n') / max(output.count('\n'), 1),
    }
    scores["quality_issue"] = (
        scores["has_summary_section"]
        or scores["has_hallucination"]
    )
    return scores
