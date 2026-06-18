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
