"""
BAW — Risk-based Permission Engine
Linux + macOS compatible. No platform-specific calls.
"""

import fnmatch
from pathlib import Path
from typing import Optional


class PermissionEngine:
    def __init__(self, config: dict):
        self.config = config
        perm_cfg = config.get("permissions", {})
        risk_levels = perm_cfg.get("risk_levels", {})
        
        self.high_rules = risk_levels.get("high", [])
        self.medium_rules = risk_levels.get("medium", [])
        self.low_rules = risk_levels.get("low", [])
        
        # Session-level overrides
        self._session_allows: set[str] = set()
        self._session_denies: set[str] = set()

    def _match_rule(self, rule: dict, tool_name: str, params: dict) -> bool:
        """Check if a permission rule matches a tool call."""
        # Tool-based rule
        if "tool" in rule and rule["tool"] == tool_name:
            return True
        
        # Path-based rule
        if "path" in rule:
            target = params.get("path", params.get("target", ""))
            if fnmatch.fnmatch(target, rule["path"]):
                return True
        
        # Command prefix rule (for bash)
        if "cmd_prefix" in rule:
            cmd = params.get("command", "")
            if cmd.strip().startswith(rule["cmd_prefix"]):
                return True
        
        return False

    def check(self, tool_name: str, params: dict) -> dict:
        """
        Check permission for a tool call.
        Returns: {"decision": "allow"|"prompt"|"deny", "reason": str}
        """
        # Session override
        key = f"{tool_name}:{params}"
        if key in self._session_denies:
            return {"decision": "deny", "reason": f"Session-denied: {tool_name}"}
        if key in self._session_allows:
            return {"decision": "allow", "reason": "Session-approved"}

        # Check high risk
        for rule in self.high_rules:
            if self._match_rule(rule, tool_name, params):
                return {"decision": "deny", "reason": f"High risk: {rule}"}

        # Check medium risk
        for rule in self.medium_rules:
            if self._match_rule(rule, tool_name, params):
                return {
                    "decision": "prompt",
                    "reason": f"Medium risk: {rule}. Allow? (y/N/s=always this session)"
                }

        # Low risk = allow
        return {"decision": "allow", "reason": "Low risk"}

    def session_allow(self, tool_name: str, params: dict):
        """Remember a one-time or session-level approval."""
        self._session_allows.add(f"{tool_name}:{params}")

    def session_deny(self, tool_name: str, params: dict):
        """Remember a one-time or session-level denial."""
        self._session_denies.add(f"{tool_name}:{params}")
