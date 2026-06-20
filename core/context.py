"""
BAW — Conversation Context
Serializable message history, cross-platform.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Message:
    role: str  # user | assistant | tool
    content: str
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    reasoning_content: str | None = None  # DeepSeek thinking mode


@dataclass
class Context:
    system_prompt: str
    messages: list[Message] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = None
    audit_required: bool = False
    task_type: str = ""  # TYPE_A–TYPE_E from _classify_task_type()

    def add_user(self, content: str):
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls: list[dict] | None = None,
                      reasoning_content: str | None = None):
        self.messages.append(Message(
            role="assistant", content=content, tool_calls=tool_calls,
            reasoning_content=reasoning_content,
        ))

    def add_tool_result(self, tool_call_id: str, name: str, content: str):
        self.messages.append(Message(
            role="tool", content=content, tool_call_id=tool_call_id, name=name
        ))

    def to_openai_messages(self) -> list[dict]:
        """Convert to OpenAI API message format."""
        result = [{"role": "system", "content": self.system_prompt}]
        for msg in self.messages:
            if msg.role == "tool":
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
            elif msg.role == "assistant" and msg.tool_calls:
                entry = {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
                if msg.reasoning_content:
                    entry["reasoning_content"] = msg.reasoning_content
                result.append(entry)
            else:
                entry = {"role": msg.role, "content": msg.content}
                if msg.reasoning_content:
                    entry["reasoning_content"] = msg.reasoning_content
                result.append(entry)
        return result

    def count_tokens_approx(self) -> int:
        """Approximate token count (4 chars ≈ 1 token)."""
        total = len(self.system_prompt)
        for msg in self.messages:
            total += len(msg.content) + 50  # 50 overhead per message
        return total // 4

    def total_chars(self) -> int:
        """Total character count across all messages (excluding system prompt)."""
        return sum(len(m.content) for m in self.messages)

    def compact(self, threshold_chars: int = 30000, keep_recent_turns: int = 5) -> tuple[int, str, str]:
        """Compact old conversation turns when context exceeds threshold.

        Instead of blind dropping (trim), summarizes old turns into compact form:
        '[User: <goal>] → [BAW: <result>]'

        **Instruction protection**: turns whose user message contains instruction
        keywords (用/開/改用/設定/請/改用/轉/切換/幫我/重新/繼續/分析/檢查/查)
        are NOT compacted — they are preserved in full to prevent user intent loss.

        Args:
            threshold_chars: Trigger compaction when total chars > this value.
            keep_recent_turns: Number of most recent turns to keep untouched.

        Returns:
            (messages_compacted, notification_text)
            Returns (0, '', '') if no compaction needed.
        """
        total = self.total_chars()
        if total <= threshold_chars:
            return 0, "", ""

        # Keywords that identify a turn as containing user instructions
        # Turns matching these will NOT be compacted
        _INSTRUCTION_KEYWORDS = {
            "用", "開", "改用", "設定", "請", "改用", "轉", "切換",
            "幫我", "重新", "繼續", "分析", "檢查", "查", "研究",
            "建立", "製作", "生成", "寫", "改", "修復",
            "use", "set", "switch", "change", "enable", "please",
            "analyze", "check", "research", "find", "create", "build",
        }

        # Group messages into turns (user+assistant cycles)
        # A turn = 1 user msg + possibly multiple assistant+tool cycles
        # We identify turns by user messages
        turn_boundaries: list[int] = []  # indices where REAL user messages start
        for i, msg in enumerate(self.messages):
            if msg.role == "user" and not msg.content.startswith("[SYSTEM]"):
                turn_boundaries.append(i)

        if len(turn_boundaries) <= keep_recent_turns:
            # Not enough turns to compact meaningfully
            return 0, "", ""

        # Keep the most recent N turns untouched
        keep_from = turn_boundaries[-keep_recent_turns]
        old_messages = self.messages[:keep_from]
        self.messages = self.messages[keep_from:]

        # Group old messages into conversation turns (user→assistant→tool... cycles)
        old_turns: list[list[Message]] = []
        current_turn: list[Message] = []
        for msg in old_messages:
            if msg.role == "user" and current_turn:
                old_turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)
        if current_turn:
            old_turns.append(current_turn)

        # Classify turns: instruction turns go to front (preserved), rest are compressed
        preserved_turns: list[list[Message]] = []
        compressed_turns: list[list[Message]] = []

        for turn in old_turns:
            is_instruction = False
            for msg in turn:
                if msg.role == "user" and not msg.content.startswith("[SYSTEM]"):
                    content_lower = msg.content.lower()
                    for kw in _INSTRUCTION_KEYWORDS:
                        if kw in content_lower:
                            is_instruction = True
                            break
                    break
            if is_instruction:
                preserved_turns.append(turn)
            else:
                compressed_turns.append(turn)

        # Compress non-instruction turns
        compressed_lines: list[str] = []
        for turn in compressed_turns:
            user_content = ""
            assistant_content = ""
            tool_failures = 0
            for msg in turn:
                if msg.role == "user" and not user_content:
                    # Preserve more of instructions: first 300 chars, not first 120
                    user_text = msg.content.split("\n")[0][:300].strip()
                    # If user_text is still just a snippet and original has more,
                    # try to include key context
                    if len(msg.content.split("\n")[0]) > 300:
                        user_text += "…"
                    user_content = user_text
                elif msg.role == "assistant" and msg.content and not assistant_content:
                    assistant_content = msg.content.split("\n")[0][:200].strip()
                elif msg.role == "tool" and ("Error" in str(msg.content) or "FAIL" in str(msg.content)):
                    tool_failures += 1

            line = f"[壓縮] User: {user_content or '?'}"
            if assistant_content:
                line += f" → BAW: {assistant_content}"
            if tool_failures > 0:
                line += f" [✖ {tool_failures} errors]"
            compressed_lines.append(line)

        # Build new message list: preserved turns + recent turns + compression summary
        preserved_msgs: list[Message] = []
        for turn in preserved_turns:
            preserved_msgs.extend(turn)

        compact_summary = "\n".join(compressed_lines)
        summary_msg = Message(
            role="system",
            content=(
                f"[CONTEXT COMPACTION] The following {len(compressed_turns)} old conversation turns\n"
                f"have been compacted into summaries to save space.\n"
                f"{len(preserved_turns)} instruction turns are preserved in full.\n"
                f"The last {keep_recent_turns} turns are preserved in full.\n\n"
                f"{compact_summary}"
            ),
        )

        # Final order: preserved_turns → summary → recent_turns
        self.messages = preserved_msgs + [summary_msg] + self.messages

        notification = (
            f"✅ 對話歷史壓縮完成 — "
            f"{len(compressed_turns)} 條舊訊息壓縮為摘要，"
            f"{len(preserved_turns)} 條指令完整保留，"
            f"保留最新 {keep_recent_turns} 輪完整內容"
        )

        return len(compressed_turns), notification, compact_summary

    def trim(self, max_messages: int = 60) -> int:
        """Trim message history to prevent unbounded session growth.
        
        Keeps system prompt + most recent messages. Strips oldest tool results
        first (tool messages), then oldest user+assistant pairs if still needed.
        
        Returns number of messages trimmed.
        """
        if len(self.messages) <= max_messages:
            return 0
        
        _trimmed = 0
        # Phase 1: remove oldest tool messages (they're largest)
        _keep = []
        _tool_indices = [i for i, m in enumerate(self.messages) if m.role == "tool"]
        _to_remove = set(_tool_indices[:len(_tool_indices) // 2])  # remove oldest 50%
        
        for i, msg in enumerate(self.messages):
            if i in _to_remove:
                _trimmed += 1
                continue
            _keep.append(msg)
        
        self.messages = _keep
        
        # Phase 2: if still over limit, trim oldest messages (keep last max_messages)
        if len(self.messages) > max_messages:
            _cut = len(self.messages) - max_messages
            self.messages = self.messages[_cut:]
            _trimmed += _cut
        
        return _trimmed
