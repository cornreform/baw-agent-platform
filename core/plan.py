"""BAW — Plan Context Layer

Lightweight plan/project entity that groups related conversations,
tracks artifacts, and manages plan-level state.

Persisted as YAML files in ~/.baw/plans/<plan_id>.yaml
"""
import os
import re
import uuid
import time
from pathlib import Path
from typing import Optional, Any
from datetime import datetime, timezone


PLANS_DIR = Path.home() / ".baw" / "plans"

# Keywords that trigger plan auto-detection in user input
_PLAN_KEYWORDS = ["計劃", "project", "plan", "專案", "方案", "規劃", "計畫", "Project", "Plan"]


class Plan:
    """A plan/project entity that groups related conversations and artifacts.

    Fields are persisted to YAML on every mutation via save().
    """

    PLANS_DIR = PLANS_DIR

    def __init__(
        self,
        plan_id: str = "",
        name: str = "",
        created: str = "",
        updated: str = "",
        status: str = "active",
        sessions: Optional[list[str]] = None,
        artifacts: Optional[list[dict]] = None,
        steps: Optional[list[dict]] = None,
        summary: str = "",
    ):
        self.plan_id = plan_id or f"pln_{uuid.uuid4().hex[:12]}"
        self.name = name
        now = datetime.now(timezone.utc).isoformat()
        self.created = created or now
        self.updated = updated or now
        self.status = status
        self.sessions = sessions or []
        self.artifacts = artifacts or []
        self.steps = steps or []
        self.summary = summary

    # ── CRUD ─────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        name: str,
        artifacts: Optional[list[dict]] = None,
        sessions: Optional[list[str]] = None,
    ) -> "Plan":
        """Create a new plan, persist to YAML, return the instance."""
        p = cls(name=name, artifacts=artifacts or [], sessions=sessions or [])
        p.save()
        return p

    def save(self):
        """Persist plan to YAML file."""
        Plan.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        self.updated = datetime.now(timezone.utc).isoformat()
        import yaml
        path = Plan.PLANS_DIR / f"{self.plan_id}.yaml"
        path.write_text(yaml.dump(self._to_dict(), default_flow_style=False, allow_unicode=True), encoding="utf-8")

    @classmethod
    def load(cls, plan_id: str) -> Optional["Plan"]:
        """Load a plan from YAML by ID. Returns None if not found."""
        path = cls.PLANS_DIR / f"{plan_id}.yaml"
        if not path.exists():
            return None
        import yaml
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not data or not isinstance(data, dict):
                return None
            return cls._from_dict(data)
        except Exception:
            return None

    @classmethod
    def get_active(cls) -> Optional["Plan"]:
        """Return the first active plan found in plans dir."""
        cls.PLANS_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(cls.PLANS_DIR.glob("*.yaml")):
            try:
                import yaml
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if data and isinstance(data, dict) and data.get("status") == "active":
                    return cls._from_dict(data)
            except Exception:
                continue
        return None

    # ── Mutations ────────────────────────────────────────────────

    def add_artifact(self, name: str, type: str = "document", path: str = ""):
        """Add a file/reference to this plan and auto-save."""
        self.artifacts.append({
            "name": name,
            "type": type,
            "path": path,
            "received_at": datetime.now(timezone.utc).isoformat(),
        })
        self.save()

    def add_session(self, session_id: str):
        """Link a session to this plan."""
        if session_id not in self.sessions:
            self.sessions.append(session_id)
            self.save()

    def add_step(self, description: str):
        """Add a pending plan step."""
        self.steps.append({
            "id": f"step_{uuid.uuid4().hex[:8]}",
            "description": description,
            "status": "pending",
        })
        self.save()

    def complete_step(self, step_id: str):
        """Mark a step as completed."""
        for step in self.steps:
            if step["id"] == step_id:
                step["status"] = "completed"
                self.save()
                return

    def deactivate(self):
        """Archive this plan (no longer active)."""
        self.status = "archived"
        self.save()

    def pause(self):
        """Pause this plan (can be reactivated)."""
        self.status = "paused"
        self.save()

    # ── Context Building ─────────────────────────────────────────

    def summarize(self) -> str:
        """Short contextual summary for debugging/logging."""
        n_arts = len(self.artifacts)
        n_steps = len(self.steps)
        pending = sum(1 for s in self.steps if s["status"] == "pending")
        done = sum(1 for s in self.steps if s["status"] == "completed")
        parts = [f"📋 Plan: {self.name}"]
        if n_arts:
            parts.append(f"{n_arts} files")
        if pending:
            parts.append(f"{pending} pending steps")
        if done:
            parts.append(f"{done} completed")
        return " · ".join(parts)

    def status_block(self) -> str:
        """HTML-formatted block for system prompt injection."""
        if self.status != "active":
            return ""
        lines = [f"<b>[Active Plan]</b> {self.name}"]
        if self.artifacts:
            lines.append(f"  Artifacts ({len(self.artifacts)}): "
                         f"{', '.join(a['name'] for a in self.artifacts[:5])}")
        if self.steps:
            for s in self.steps:
                status_text = "✅ completed" if s["status"] == "completed" else "⬜ pending"
                lines.append(f"  {status_text} — {s['description']}")
        lines.append(
            "<b>IMPORTANT:</b> Messages here belong to this plan. "
            "Maintain plan context — do NOT treat as independent requests."
        )
        return "\n".join(lines)

    # ── Plan Detection ───────────────────────────────────────────

    @staticmethod
    def detect_plan(recent_prompts: list[str]) -> Optional[dict]:
        """Auto-detect if a sequence of inputs suggests a plan.

        Returns dict with keys for Plan.create() kwargs, or None.
        """
        if not recent_prompts:
            return None

        combined = " ".join(recent_prompts)

        # Check for plan keywords in the last prompt
        for kw in _PLAN_KEYWORDS:
            if kw.lower() in combined.lower():
                # Extract a name from the prompt (first meaningful phrase)
                name = ""
                for prompt in reversed(recent_prompts):
                    # Try to get the part after the keyword
                    idx = prompt.lower().find(kw.lower())
                    if idx >= 0:
                        after_kw = prompt[idx + len(kw):].strip().rstrip(".!，。")
                        if after_kw and len(after_kw) < 60:
                            name = after_kw
                            break
                if not name:
                    name = combined.strip()[:40] if combined.strip() else "Untitled Plan"
                return {"name": name.strip(), "artifacts": []}

        # Batch file upload with plan-like content — only if 5+ files (conservative)
        file_count = sum(1 for p in recent_prompts if p.startswith("[File:") or "<b>File" in p)
        if file_count >= 5:
            return {"name": "Untitled Plan", "artifacts": []}

        return None

    # ── Serialization ────────────────────────────────────────────

    def _to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "name": self.name,
            "created": self.created,
            "updated": self.updated,
            "status": self.status,
            "sessions": self.sessions,
            "artifacts": self.artifacts,
            "steps": self.steps,
            "summary": self.summary,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> "Plan":
        return cls(
            plan_id=data.get("plan_id", ""),
            name=data.get("name", ""),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            status=data.get("status", "active"),
            sessions=data.get("sessions", []),
            artifacts=data.get("artifacts", []),
            steps=data.get("steps", []),
            summary=data.get("summary", ""),
        )
