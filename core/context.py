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


@dataclass
class Context:
    system_prompt: str
    messages: list[Message] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = None

    def add_user(self, content: str):
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str, tool_calls: list[dict] | None = None):
        self.messages.append(Message(role="assistant", content=content, tool_calls=tool_calls))

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
                result.append({
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
                })
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result

    def count_tokens_approx(self) -> int:
        """Approximate token count (4 chars ≈ 1 token)."""
        total = len(self.system_prompt)
        for msg in self.messages:
            total += len(msg.content) + 50  # 50 overhead per message
        return total // 4
