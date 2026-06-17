"""BAW built-in: self_discover — detect capability gaps, suggest new tools.

Analyzes:
1. Recent task failures (from logs) — what couldn't BAW do?
2. User requests that required workarounds — what tool was missing?
3. External system capabilities vs BAW's own — what's missing?
4. Proactively suggests and can auto-generate new tools to fill gaps.
"""
import json
import os
import subprocess
import re
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_BAW_CONTAINER = os.environ.get("BAW_CONTAINER", "baw-telegram")


def _scan_logs_for_failures() -> list[dict]:
    """Scan recent logs for error/failure patterns that suggest missing tools."""
    failures = []
    log_sources = []

    # Check Docker logs
    try:
        r = subprocess.run(
            ["docker", "logs", _BAW_CONTAINER, "--tail", "200", "--timestamps"],
            capture_output=True, text=True, timeout=10,
        )
        if r.stdout:
            log_sources.append(("docker", r.stdout))
    except Exception:
        pass

    # Check error logs
    error_log = _BAW_DATA / "logs"
    if error_log.exists():
        for f in sorted(error_log.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:3]:
            try:
                content = f.read_text(errors="replace")[:5000]
                log_sources.append((f.name, content))
            except Exception:
                pass

    # Analyze for failure patterns
    failure_keywords = [
        (r"tool.*not found", "missing_tool"),
        (r"no tool.*for", "missing_tool"),
        (r"unknown.*tool", "missing_tool"),
        (r"ModuleNotFoundError", "missing_dependency"),
        (r"cannot execute", "capability_gap"),
        (r"not supported", "capability_gap"),
        (r"unable to.*find", "missing_resource"),
        (r"permission denied", "permission_gap"),
        (r"timeout", "performance_gap"),
        (r"no.*api.*key", "config_gap"),
    ]

    for source_name, text in log_sources:
        for pattern, gap_type in failure_keywords:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for m in matches:
                # Get context (10 lines around the match)
                start = max(0, text.rfind("\n", 0, m.start()) - 200)
                end = min(len(text), text.find("\n", m.end()) + 200)
                context = text[start:end].strip()
                failures.append({
                    "source": source_name,
                    "type": gap_type,
                    "pattern": pattern,
                    "context": context[:150],
                })

    # Deduplicate
    seen = set()
    unique = []
    for f in failures:
        key = f["type"] + f["context"][:50]
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique[:10]


def _inventory_tools() -> dict:
    """Inventory BAW's current tools and identify functional gaps."""
    try:
        from core.tools import list_tools
        tools = list_tools()
    except Exception:
        return {"count": 0, "names": [], "error": "cannot list tools"}

    names = [t.name for t in tools]

    # Known capability domains BAW should have
    expected_domains = {
        "data_access": ["web_search", "web_extract", "http_fetch", "read_file", "search_files"],
        "code_execution": ["bash", "execute_code"],
        "file_operations": ["write_file", "read_file", "patch", "search_files"],
        "system_management": ["system", "self_diagnose", "resource_monitor", "docker", "git"],
        "ai": ["image_generate", "tts", "vision"],
        "memory": ["memory", "remember", "session_search", "knowledge_graph"],
        "extension": ["tool_generate", "scan_and_adopt", "skill_import"],
        "deployment": ["self_migrate", "docker", "git"],
        "communication": ["install", "config", "cronjob", "background"],
    }

    gaps = {}
    for domain, expected in expected_domains.items():
        missing = [t for t in expected if t not in names]
        if missing:
            gaps[domain] = missing

    return {"count": len(names), "names": sorted(names), "domain_gaps": gaps}


def _suggest_new_tools(gaps: dict, failures: list[dict]) -> list[dict]:
    """Generate tool suggestions based on gaps and failures."""
    suggestions = []

    # From domain gaps
    tool_templates = {
        "web_search": lambda: {"name": "web_search", "priority": "high",
                                "reason": "No web search capability — cannot fetch real-time info"},
        "web_extract": lambda: {"name": "web_extract", "priority": "high",
                                 "reason": "No web page extraction — cannot read docs/URLs"},
        "http_fetch": lambda: {"name": "http_fetch", "priority": "high",
                                "reason": "No HTTP fetch — cannot call APIs directly"},
        "execute_code": lambda: {"name": "execute_code", "priority": "medium",
                                   "reason": "No safe code execution sandbox"},
        "image_generate": lambda: {"name": "image_generate", "priority": "low",
                                     "reason": "No image generation — cannot create visuals"},
        "system": lambda: {"name": "system", "priority": "medium",
                             "reason": "No system management — cannot check health"},
    }

    for domain, missing in gaps.items():
        for tool_name in missing:
            if tool_name in tool_templates:
                suggestions.append(tool_templates[tool_name]())

    # From failure analysis
    failure_to_tool = {
        "missing_tool": {"name": "auto_install_tool", "priority": "high",
                         "reason": "Tasks fail because tools not found — need auto-install"},
        "missing_dependency": {"name": "dependency_check", "priority": "medium",
                                "reason": "Missing Python deps causing crashes"},
        "capability_gap": {"name": "capability_bridge", "priority": "medium",
                            "reason": "BAW encounters things it cannot do — need generic executor"},
    }

    for f in failures:
        gap_type = f["type"]
        if gap_type in failure_to_tool:
            suggestion = failure_to_tool[gap_type]
            if suggestion not in suggestions:
                suggestions.append(suggestion)

    # Deduplicate
    seen = set()
    unique = []
    for s in suggestions:
        key = s["name"]
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique


def _handler(
    action: str = "audit",
    auto_fix: bool = False,
) -> str:
    """Analyze BAW's capabilities and detect gaps.

    Scans:
    - Current tool inventory (what tools exist)
    - Recent failure logs (what went wrong)
    - Domain coverage (which capability domains are missing)
    
    Proposes new tools to fill gaps, optionally auto-generating them.

    Args:
        action: 
          'audit' — report current state + gaps
          'fix'   — report + auto-generate missing tools
          'suggest' — just list suggestions
        auto_fix: If True and action='fix', auto-generate suggested tools
    """
    # Tool inventory
    inventory = _inventory_tools()
    gaps = inventory.get("domain_gaps", {})

    # Failures
    failures = _scan_logs_for_failures()

    # Suggestions
    suggestions = _suggest_new_tools(gaps, failures)

    result = {
        "tool_count": inventory["count"],
        "tools": inventory["names"],
        "domain_gaps": gaps,
        "failures_found": failures[:5],
        "suggestions": suggestions,
        "health_score": _calc_health(inventory, gaps, failures),
    }

    if auto_fix and action == "fix":
        # Auto-generate suggested high-priority tools
        generated = []
        for s in suggestions:
            if s["priority"] == "high":
                try:
                    from tools.tool_generate import _handler as tg
                    gen_result = json.loads(
                        tg(name=s["name"], description=s["reason"], what_it_does=f"Tool to address: {s['reason']}")
                    )
                    generated.append({"suggestion": s["name"], "result": gen_result.get("ok", False)})
                except Exception as e:
                    generated.append({"suggestion": s["name"], "result": False, "error": str(e)})
        result["auto_generated"] = generated

    return json.dumps(result, ensure_ascii=False, indent=2)


def _calc_health(inventory: dict, gaps: dict, failures: list) -> str:
    """Calculate a simple health score."""
    tool_count = inventory.get("count", 0)
    gap_count = sum(len(v) for v in gaps.values())
    failure_count = len(failures)

    if tool_count >= 30 and gap_count == 0 and failure_count == 0:
        return "excellent"
    elif tool_count >= 25 and gap_count <= 3 and failure_count <= 3:
        return "good"
    elif tool_count >= 20 and gap_count <= 5 and failure_count <= 10:
        return "fair"
    else:
        return "needs_attention"


TOOL_DEF = {
    "name": "self_discover",
    "description": (
        "[EXTENSIBILITY] Proactively detect BAW's capability gaps. "
        "Scans tool inventory, recent failures, and domain coverage. "
        "Suggests and optionally auto-generates new tools to fill gaps. "
        "BAW grows itself by identifying what it CANNOT do."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["audit", "fix", "suggest"],
                "description": "audit=report, fix=report+auto-gen, suggest=just list",
                "default": "audit",
            },
            "auto_fix": {
                "type": "boolean",
                "description": "Auto-generate high-priority missing tools (only with action='fix').",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "low",
}
