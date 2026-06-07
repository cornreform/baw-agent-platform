"""BAW — Skills System

Modular YAML skill/workflow definitions.
Each skill is a reusable template that BAW can load and execute.

Skill YAML format (~/.baw/skills/<name>.yaml):
  name: daily-git-push
  description: 自動 push 所有本地 commit
  tools: [bash]
  config:
    mode: hybrid
  steps:
    - name: git-status
      prompt: run git status in ~/baw/, check for unpushed commits
      expects: unpushed count
    - name: git-push
      prompt: push all pending commits to origin/main
      depends_on: [git-status]
  error_handling:
    on_failure: retry  # retry | skip | abort
    max_retries: 2
"""

from __future__ import annotations
import os
import sys
import yaml
import json
from pathlib import Path
from typing import Optional


SKILLS_DIR = "skills"


# ── Skill definition ──

class Skill:
    """A loaded skill definition."""

    def __init__(self, name: str, description: str = "",
                 steps: list[dict] = None, tools: list[str] = None,
                 config: dict = None, error_handling: dict = None):
        self.name = name
        self.description = description
        self.steps = steps or []
        self.tools = tools or []
        self.config = config or {}
        self.error_handling = error_handling or {}

    @classmethod
    def from_yaml(cls, path: Path) -> "Skill":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not raw or not isinstance(raw, dict):
            raise ValueError(f"Invalid skill YAML: {path}")
        return cls(
            name=raw.get("name", path.stem),
            description=raw.get("description", ""),
            steps=raw.get("steps", []),
            tools=raw.get("tools", []),
            config=raw.get("config", {}),
            error_handling=raw.get("error_handling", {}),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": len(self.steps),
            "tools": self.tools,
        }

    def build_prompt(self, step_index: int = 0, user_args: str = "") -> str:
        """Build the execution prompt for a step or the whole skill."""
        if step_index >= len(self.steps):
            return ""

        step = self.steps[step_index]
        base = step.get("prompt", step.get("name", self.name))
        if user_args:
            base += f"\n\nUser args: {user_args}"
        # Add context of previous steps
        if step_index > 0:
            prev_names = [s.get("name", f"step_{i}") for i, s in enumerate(self.steps[:step_index])]
            base += f"\n\nContext: completed {', '.join(prev_names)}"
        return base

    def get_mode(self) -> str:
        return self.config.get("mode", "hybrid")

    def get_max_retries(self) -> int:
        return self.error_handling.get("max_retries", 2)

    def get_on_failure(self) -> str:
        return self.error_handling.get("on_failure", "retry")


# ── Skill registry ──

class SkillRegistry:
    """Loads and manages skills from the skills directory."""

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self._skills: dict[str, Skill] = {}
        self._load()

    def _skills_path(self) -> Path:
        return self.data_dir / SKILLS_DIR

    def _load(self):
        sp = self._skills_path()
        if not sp.exists():
            sp.mkdir(parents=True, exist_ok=True)
            self._create_sample_skills(sp)
        for f in sorted(sp.glob("*.yaml")):
            try:
                skill = Skill.from_yaml(f)
                self._skills[skill.name] = skill
            except Exception as e:
                print(f"  ⚠️  Failed to load skill {f.name}: {e}", file=sys.stderr)

    def _create_sample_skills(self, sp: Path):
        """Create example skills for the user."""
        samples = {
            "daily-git-push.yaml": """\
name: daily-git-push
description: 自動 push 所有本地 git commit
tools: [bash]
config:
  mode: hybrid
steps:
  - name: check-changes
    prompt: run git status in ~/baw, list unpushed commits
  - name: push
    prompt: push all pending commits to origin/main
error_handling:
  on_failure: retry
  max_retries: 2
""",
            "disk-check.yaml": """\
name: disk-check
description: 檢查磁碟空間，低於閾值就報警
tools: [bash]
config:
  mode: quick
steps:
  - name: check-disk
    prompt: run df -h /, parse used percentage
error_handling:
  on_failure: abort
""",
            "system-health.yaml": """\
name: system-health
description: 檢查系統健康狀態 (CPU/記憶體/磁碟)
tools: [bash]
config:
  mode: hybrid
steps:
  - name: cpu
    prompt: check cpu load with uptime and top
  - name: memory
    prompt: check memory usage with free -h
  - name: disk
    prompt: check disk space with df -h
  - name: report
    prompt: summarise all health data in a clean report
error_handling:
  on_failure: skip
  max_retries: 1
""",
        }
        for fname, content in samples.items():
            (sp / fname).write_text(content, encoding="utf-8")

    def list_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get_skill(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def reload(self):
        self._skills.clear()
        self._load()

    def status_report(self) -> str:
        lines = ["🧠 Available Skills:"]
        for s in self._skills.values():
            lines.append(f"  {s.name} — {s.description[:50]}")
        if not self._skills:
            lines.append("  (no skills installed)")
        return "\n".join(lines)
