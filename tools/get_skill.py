"""BAW built-in: Progressive Disclosure — load skill content on-demand.

Level 1 (system prompt): skill names + descriptions only.
Level 2 (this tool): full skill content loaded when needed.
Keeps system prompt lean — saves context tokens.
"""

import sys
from pathlib import Path


def get_skill(skill_name: str) -> str:
    """Get full content of a skill by name.

    Skills live under ~/.baw/skills/. Each skill is a YAML file.
    Returns the full content: description + steps + config + error handling.

    Args:
        skill_name: Name of the skill (without .yaml extension).

    Returns:
        Full skill content as formatted text.
    """
    skills_dir = Path.home() / ".baw" / "skills"
    if not skills_dir.exists():
        return "No skills directory found at ~/.baw/skills/"

    skill_path = skills_dir / f"{skill_name}.yaml"
    if not skill_path.exists():
        # Try .md extension too (for SKILL.md format)
        skill_path = skills_dir / f"{skill_name}.md"
        if not skill_path.exists():
            available = ", ".join(sorted(
                p.stem for p in skills_dir.glob("*.yaml")
            )) or "none"
            return f"Skill '{skill_name}' not found. Available: {available}"

    content = skill_path.read_text(encoding="utf-8")

    # Format nicely
    lines = [f"## Skill: {skill_name}", "", content.strip(), ""]
    return "\n".join(lines)


def list_skills() -> str:
    """List all available skills with their descriptions.

    Returns:
        Formatted list of skill names and descriptions.
    """
    skills_dir = Path.home() / ".baw" / "skills"
    if not skills_dir.exists():
        return "No skills directory found at ~/.baw/skills/"

    results = []
    for f in sorted(skills_dir.glob("*.yaml")):
        import yaml
        try:
            raw = yaml.safe_load(f.read_text(encoding="utf-8"))
            desc = raw.get("description", "") if raw else ""
            results.append(f"- `{f.stem}` — {desc[:100]}")
        except Exception:
            results.append(f"- `{f.stem}` — (unable to parse)")

    # Also check .md files (SKILL.md format)
    for f in sorted(skills_dir.glob("*.md")):
        if f.name != "SKILL.md":
            content = f.read_text(encoding="utf-8")
            # Extract description from first line or frontmatter
            desc = ""
            if content.startswith("---"):
                import yaml
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if fm:
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            if not desc:
                desc = content.split("\n")[0][:80]
            results.append(f"- `{f.stem}` — {desc[:100]}")

    if not results:
        return "No skills found in ~/.baw/skills/"

    return "## Available Skills\n" + "\n".join(results)


def _dispatcher(action: str, skill_name: str = "") -> str:
    """Dispatch get_skill actions."""
    if action == "list":
        return list_skills()
    elif action == "get":
        if not skill_name:
            return "Error: 'skill_name' is required for 'get' action"
        return get_skill(skill_name)
    else:
        return f"Error: unknown action '{action}'. Use 'list' or 'get'."


TOOL_DEF = {
    "name": "get_skill",
    "description": (
        "Load a skill's full content on-demand (Progressive Disclosure Level 2). "
        "Use 'action=list' to see available skills with descriptions. "
        "Use 'action=get' with 'skill_name' to load the complete skill content. "
        "Always check list_skills first to see what's available."
    ),
    "handler": _dispatcher,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get"],
                "description": "'list' shows available skills, 'get' loads full content.",
            },
            "skill_name": {
                "type": "string",
                "description": "Required for 'get' action. Name of the skill to load (e.g., 'github-research').",
            },
        },
        "required": ["action"],
    },
    "risk_level": "low",
}
