"""
BAW — HTML Report Renderer
Converts all BAW output to HTML format for user-facing reports.

Default output format for BAW is HTML, not Markdown.
Telegram supports HTML natively: <b>, <i>, <code>, <pre>, <a>, <blockquote>
"""

from __future__ import annotations
from typing import Optional


def bold(text: str) -> str:
    return f"<b>{_e(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{_e(text)}</i>"


def code(text: str) -> str:
    return f"<code>{_e(text)}</code>"


def pre(text: str, lang: str = "") -> str:
    if lang:
        return f"<pre><code class=\"language-{lang}\">{_e(text)}</code></pre>"
    return f"<pre>{_e(text)}</pre>"


def link(url: str, text: str = "") -> str:
    display = text or url
    return f"<a href=\"{_e(url)}\">{_e(display)}</a>"


def blockquote(text: str) -> str:
    return f"<blockquote>{_e(text)}</blockquote>"


def spoiler(text: str) -> str:
    return f"<tg-spoiler>{_e(text)}</tg-spoiler>"


def _e(text: str) -> str:
    """Escape HTML entities (only &, <, >)."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── BAW-specific renderers ─────────────────────────────────────

def render_adversarial_court(devil_score: float, devil_content: str,
                              angel_score: float, angel_content: str,
                              decision: str, should_stop: bool = False) -> str:
    """Render the Angel/Devil court output as HTML."""
    parts = []

    # Devil
    parts.append(
        f"👿 <b>Devil (Opposing Counsel)</b> — Risk: {devil_score}/10"
    )
    parts.append(f"<blockquote>{_e(devil_content)}</blockquote>")

    # Angel
    parts.append(
        f"😇 <b>Angel (Executor)</b> — Feasibility: {angel_score}/10"
    )

    # Decision
    if should_stop:
        parts.append(
            f"\n⚠️ <b>Devil ({devil_score}/10) &gt; Angel ({angel_score}/10)</b>\n"
            f"⛔ BAW has significant concerns. Stopped before any action."
        )
    elif decision == "warn":
        parts.append(
            f"\n⚠️ Devil close to Angel — proceeding with caution"
        )
    else:
        parts.append(f"\n━━━ Proceeding ───")

    parts.append(angel_content)
    return "\n".join(parts)


def render_cost(calls: list[dict], total: float) -> str:
    """Render cost summary as HTML."""
    if not calls:
        return ""
    calls_info = " | ".join(
        f"{c['tokens_in']}↑{c['tokens_out']}↓"
        f"<code>${c['cost']:.4f}</code>"
        for c in calls
    )
    return (
        f"📊 <b>[{len(calls)} LLM calls]</b> {calls_info} | "
        f"<b>total: ${total:.4f}</b>"
    )


def render_fact_check(action: str, claims: list, message: str = "",
                       verbose: bool = False) -> str:
    """Render fact checker output as HTML."""
    if action == "pass":
        return ""
    if action == "block":
        return f"\n⚠️ <b>Blocked by Fact Checker</b>\n{_e(message)}"
    if action == "flag":
        note = f"\n⚠️ <i>{len(claims)} unverified claims flagged</i>"
        if verbose and claims:
            details = "\n".join(
                f"<code>{c['claim']}</code>" for c in claims[:3]
            )
            note += f"\n{details}"
        return note
    return ""


def render_strategy_report(strategies: list[str], attempts: int,
                             final_decision: str) -> str:
    """Render a strategy recovery report as HTML."""
    parts = ["<b>🔄 Strategy Recovery</b>"]
    if strategies:
        parts.append("Strategies tried:")
        for i, s in enumerate(strategies, 1):
            parts.append(f"  {i}. {_e(s)}")
    parts.append(f"Attempts: {attempts}")
    parts.append(f"<b>Decision:</b> {_e(final_decision)}")
    return "<br>".join(parts)


def render_timestamp() -> str:
    """Render current UTC timestamp."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"<i>Report generated: {ts}</i>"


def wrap_html(body: str) -> str:
    """Wrap content in a full HTML document structure."""
    return f"""<html>
<head><meta charset="utf-8"></head>
<body>
{body}
</body>
</html>"""
