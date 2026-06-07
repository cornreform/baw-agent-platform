"""
BAW — Checkpoint System
State save/restore for self-improving agent loop.

Each state-changing tool call is preceded by a checkpoint.
On failure: restore checkpoint, try alternative approach.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import copy


@dataclass
class Checkpoint:
    """Snapshots of the agent's state at a point in time."""
    step_index: int
    messages_snapshot: list = field(default_factory=list)
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    attempts: int = 0
    strategies_used: list[str] = field(default_factory=list)


class Checkpointer:
    """Manage agent checkpoints for rollback recovery."""

    def __init__(self):
        self._stack: list[Checkpoint] = []
        self._step_count = 0

    def save(self, messages: list, tool_name: str = "",
             tool_args: dict = None, plan: list = None) -> Checkpoint:
        """Save a checkpoint before a state-changing operation."""
        self._step_count += 1
        cp = Checkpoint(
            step_index=self._step_count,
            messages_snapshot=_deep_copy_messages(messages),
            tool_name=tool_name,
            tool_args=tool_args or {},
            plan=copy.deepcopy(plan) if plan else [],
            attempts=0,
            strategies_used=[],
        )
        self._stack.append(cp)
        return cp

    def restore(self) -> Optional[Checkpoint]:
        """Restore the most recent checkpoint. Returns the checkpoint or None."""
        return self._stack[-1] if self._stack else None

    def pop_and_restore(self) -> Optional[Checkpoint]:
        """Pop and restore the most recent checkpoint. Returns the checkpoint."""
        if not self._stack:
            return None
        cp = self._stack.pop()
        self._step_count = cp.step_index
        return cp

    def record_attempt(self):
        """Record an attempt at the current step."""
        if self._stack:
            self._stack[-1].attempts += 1

    def record_strategy(self, strategy: str):
        """Record the strategy used for the current step."""
        if self._stack:
            self._stack[-1].strategies_used.append(strategy)

    @property
    def attempts(self) -> int:
        return self._stack[-1].attempts if self._stack else 0

    @property
    def strategies(self) -> list[str]:
        return self._stack[-1].strategies_used if self._stack else []

    def commit(self):
        """Commit the current step (keep the checkpoint, don't restore on fail)."""
        if self._stack:
            self._stack.pop()

    def clear(self):
        """Clear all checkpoints (new goal)."""
        self._stack.clear()
        self._step_count = 0


def _deep_copy_messages(messages: list) -> list:
    """Deep copy Message objects for checkpoint restore."""
    return copy.deepcopy(messages)
