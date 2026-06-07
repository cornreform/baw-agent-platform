"""BAW — Self-Learning Skill Acquisition

Takes any skill description (natural language, URL, or spec from another platform)
and autonomously: analyzes, designs, generates, and tests a BAW-compatible skill.

Core philosophy: NEVER say "our platform can't do this". Instead, break down
what the skill does, extract what's achievable, and adapt.
"""

from __future__ import annotations
import os
import sys
import yaml
import re
from pathlib import Path
from typing import Optional

from .llm import get_model, call_llm_with_fallback, calculate_cost
from .tools import list_tools


# ── Analysis prompt ──

ANALYSIS_PROMPT = """你係 BAW Skill Engineer。

用戶提供咗一個技能描述/規格，可能來自 OpenClaude、Hermes、Codex 或者其他平台。
你需要：

1. **分析** — 呢個技能想做啲咩？核心目標係乜？
2. **拆解** — 唔需要嘅直接複製。BAW 有自己嘅工具同模式。諗清楚，呢個技能係 BAW 上點樣做先合理。
3. **設計** — 寫出一個 BAW-compatible 嘅 skill YAML。
   - 每個 step 要細 (1-2 tool calls)
   - Step 嘅 prompt 係 natural language，唔係 raw command
   - 如果原始技能有用到 BAW 冇嘅功能，諗替代方案，唔好放棄
4. **驗證** — 確保每個 step 喺 BAW 上係可行嘅

回應格式：
```
## Analysis
<簡短分析：呢個技能做咩>

## BAW Skill Design
<解釋點樣 adapt 去 BAW 平台>

## YAML
<填入實際 YAML 內容，用 yaml code block>
```"""


def learn_from_description(description: str, data_dir: Path, config: dict,
                           verbose: bool = False, dry_run: bool = False) -> dict:
    """Take a skill description and autonomously create a BAW skill.

    Returns: {
        "name": str,
        "file": str or None,
        "steps": int,
        "analysis": str,
        "yaml": str,
        "error": str or None,
    }
    """
    model = get_model(config)
    tools_list = [t.name for t in list_tools()]

    # Build context about BAW's capabilities
    system_context = (
        f"你係 BAW (Black And White) Agent Platform 嘅 Skill Engineer。\n"
        f"BAW 有以下 tools: {', '.join(tools_list)}\n"
        f"BAW 支援 mode: quick (直接答), hybrid (plan + execute, default), tight (plan + court + verify)\n"
        f"每 step 應該用 hybrid mode，除非好簡單先用 quick。\n"
        f"每個 step prompt 要用自然語言描述做咩，唔好寫 raw command。\n"
        f"如果原始技能用咗 BAW 冇嘅功能，諗 system command / bash 替代方案。\n"
        f"記住：永遠唔可以話「無辦法做到」，要搵替代方法。"
    )

    # Step 1: Analyze and design
    msgs = [
        {"role": "system", "content": system_context},
        {"role": "user", "content": f"{ANALYSIS_PROMPT}\n\n---\n\n{description}"},
    ]

    if verbose:
        print("  🧠 Analyzing skill description...")

    fb = call_llm_with_fallback(config, msgs, temperature=0.7)
    analysis = fb.response.content or ""

    cost = calculate_cost(model, fb.response.input_tokens, fb.response.output_tokens)
    if verbose:
        print(f"  Analysis: {fb.response.input_tokens}↑{fb.response.output_tokens}↓ ${cost:.4f}")

    # Step 2: Extract YAML from the response
    yaml_content = _extract_yaml(analysis)
    if not yaml_content:
        # If no YAML block, ask the LLM to output just the YAML
        if verbose:
            print("  ⚠️  No YAML block found, requesting direct YAML output...")
        msgs2 = [
            {"role": "system", "content": system_context},
            {"role": "user", "content": (
                f"Based on this analysis, output ONLY the BAW skill YAML file.\n"
                f"No explanations, no markdown, just valid YAML.\n\n"
                f"---\n{description}\n---"
            )},
        ]
        fb2 = call_llm_with_fallback(config, msgs2, temperature=0.7)
        yaml_content = _extract_yaml(fb2.response.content or "")
        if not yaml_content:
            yaml_content = fb2.response.content or ""

    # Step 3: Validate and normalize YAML
    skill_name = "learned-skill"
    parsed = None
    try:
        parsed = yaml.safe_load(yaml_content)
        if parsed and isinstance(parsed, dict):
            skill_name = parsed.get("name", "learned-skill")
            # Validate basic structure
            if "steps" not in parsed or not parsed["steps"]:
                return {
                    "name": skill_name,
                    "file": None,
                    "steps": 0,
                    "analysis": analysis,
                    "yaml": yaml_content,
                    "error": "YAML has no 'steps'",
                }
            # Normalize: remove duplicate mode
            parsed.pop("mode", None)
            # Slugify the name for filename
            import re as _slug_re
            _raw = skill_name.replace('_', ' ').replace('-', ' ')
            skill_slug = _slug_re.sub(r'[^a-z0-9-]', '', _raw.lower().strip().replace(' ', '-'))
            while '--' in skill_slug:
                skill_slug = skill_slug.replace('--', '-')
            if not skill_slug:
                skill_slug = "learned-skill"
            # Ensure config has mode
            parsed.setdefault("config", {})
            parsed["config"].setdefault("mode", "hybrid")
            # Inherit tools from steps
            if not parsed.get("tools"):
                tools_used = set()
                for step in parsed["steps"]:
                    if isinstance(step, dict):
                        t = step.pop("tool", None) or step.pop("tools", None)
                        if t:
                            if isinstance(t, str):
                                tools_used.add(t)
                            elif isinstance(t, list):
                                for _item in t:
                                    if isinstance(_item, str):
                                        tools_used.add(_item)
                            elif isinstance(t, dict):
                                # Handle tool as dict like {name: args}
                                for _key in t:
                                    if isinstance(_key, str):
                                        tools_used.add(_key)
                parsed["tools"] = sorted(tools_used) if tools_used else []
            # Normalize step format: convert 'parameters' and 'command' to inline
            for step in parsed["steps"]:
                if isinstance(step, dict):
                    step.setdefault("name", step.get("prompt", "step")[:40])
                    # Convert parameters.command to prompt context
                    params = step.pop("parameters", None)
                    if params and isinstance(params, dict):
                        cmd = params.get("command", "")
                        if cmd:
                            step["prompt"] = step.get("prompt", "") + f"\n\nCommand: {cmd[:200]}"
                    # Convert top-level command to prompt context
                    cmd = step.pop("command", None)
                    if cmd and isinstance(cmd, str) and "command" not in (step.get("prompt", "") or "").lower():
                        step["prompt"] = step.get("prompt", "") + f"\n\nCommand: {cmd[:200]}"
            # Write the skill file
            file_path = None
            if not dry_run:
                skills_dir = data_dir / "skills"
                skills_dir.mkdir(parents=True, exist_ok=True)
                file_path = skills_dir / f"{skill_slug}.yaml"
                clean_yaml = yaml.dump(parsed, default_flow_style=False, allow_unicode=True)
                file_path.write_text(clean_yaml, encoding="utf-8")
                if verbose:
                    print(f"  💾 Saved: {file_path}")
            return {
                "name": skill_name,
                "slug": skill_slug,
                "file": str(file_path) if file_path else None,
                "steps": len(parsed["steps"]),
                "analysis": analysis,
                "yaml": yaml.dump(parsed, default_flow_style=False, allow_unicode=True),
                "error": None,
            }
        else:
            return {
                "name": skill_name,
                "file": None,
                "steps": 0,
                "analysis": analysis,
                "yaml": yaml_content,
                "error": "Invalid YAML structure",
            }
    except yaml.YAMLError as e:
        return {
            "name": skill_name,
            "file": None,
            "steps": 0,
            "analysis": analysis,
            "yaml": yaml_content,
            "error": f"YAML parse error: {e}",
        }


def _extract_yaml(text: str) -> str:
    """Extract YAML content from markdown code blocks or raw text."""
    # Try yaml code block first
    m = re.search(r'```(?:yaml|yml)\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try any code block
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: try to parse the whole text as YAML
    # (return empty — the caller will handle it)
    return ""


def learn_from_url(url: str, data_dir: Path, config: dict,
                   verbose: bool = False, dry_run: bool = False) -> dict:
    """Fetch content from a URL and learn a skill from it."""
    try:
        import httpx
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        content = resp.text[:5000]  # Limit to 5K chars
        description = f"Source: {url}\n\n{content}"
        return learn_from_description(description, data_dir, config, verbose, dry_run)
    except Exception as e:
        return {
            "name": "error",
            "file": None,
            "steps": 0,
            "analysis": "",
            "yaml": "",
            "error": f"Failed to fetch {url}: {e}",
        }
