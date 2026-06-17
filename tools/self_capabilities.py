"""BAW built-in: self_capabilities — scan and report own capabilities.

Scans BAW's tools, providers, config, and SOUL to answer
"What can you do?" without relying on built-in knowledge.
"""
import json
import os
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))


def _handler() -> str:
    """Scan BAW's capabilities and return a structured report.

    Reports: registered tools, LLM providers, config summary,
    system information, and known limitations.
    """
    result = {
        "tools": _scan_tools(),
        "providers": _scan_providers(),
        "config_summary": _scan_config(),
        "system_info": _system_info(),
        "known_limitations": [
            "Cannot run outside Docker container (needs Docker socket bind mount)",
            "LLM provider failures may cause tasks to stall",
            "No direct filesystem access to host (only mounted volumes)",
            "Cannot install system packages (pip packages only)",
        ],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


def _scan_tools() -> list[dict]:
    """Scan registered tools and categorize them."""
    try:
        from core.tools import list_tools
        tools = list_tools()
        categorized = {"communication": [], "development": [], "system": [], "data": [], "ai": []}
        for t in tools:
            name = t.name
            desc = t.description[:100]
            risk = t.risk_level
            entry = {"name": name, "risk": risk, "description": desc}
            if name in ("bash", "write_file", "read_file", "search_files", "patch", "execute_code"):
                categorized["development"].append(entry)
            elif name in ("git", "docker", "system", "self_diagnose", "resource_monitor", "install", "config", "background", "selftest"):
                categorized["system"].append(entry)
            elif name in ("web_search", "web_extract", "http_fetch", "browser", "mcp"):
                categorized["data"].append(entry)
            elif name in ("memory", "remember", "knowledge_graph", "session_search", "cronjob", "todo", "code_scan"):
                categorized["data"].append(entry)
            elif name in ("image_generate", "tts", "vision"):
                categorized["ai"].append(entry)
            else:
                categorized["development"].append(entry)
        return categorized
    except Exception as e:
        return [{"error": str(e)}]


def _scan_providers() -> list[dict]:
    """Scan configured LLM providers."""
    config_path = _BAW_DATA / "config.yaml"
    if not config_path.exists():
        return []
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
    except Exception:
        return []

    result = []
    default_model = cfg.get("model", {}).get("default", "?")
    fallback = cfg.get("model", {}).get("fallback", "none")
    result.append({"role": "default", "model": default_model, "fallback": fallback})

    providers = cfg.get("providers", {})
    for pname, pcfg in providers.items():
        key_env = pcfg.get("api_key_env", "")
        has_key = bool(os.environ.get(key_env, ""))
        models = [m.get("id", "?") for m in pcfg.get("models", [])[:3]]
        result.append({
            "provider": pname,
            "key_configured": has_key,
            "available_models": models,
        })
    return result


def _scan_config() -> dict:
    """Return a brief config summary."""
    config_path = _BAW_DATA / "config.yaml"
    if not config_path.exists():
        return {"error": "config.yaml not found"}
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
    except Exception:
        return {"error": "config unreadable"}

    model_cfg = cfg.get("model", {})
    return {
        "default_model": model_cfg.get("default", "?"),
        "fallback_model": model_cfg.get("fallback", "none"),
        "executor_model": cfg.get("executor", {}).get("model", "?"),
        "language": cfg.get("display", {}).get("language", "?"),
        "providers_count": len(cfg.get("providers", {})),
    }


def _system_info() -> dict:
    """Return basic system information."""
    import subprocess

    info = {
        "baw_home": str(_BAW_HOME),
        "baw_data": str(_BAW_DATA),
        "container": "yes" if _BAW_HOME == Path("/app") else "no",
        "python_version": "",
    }

    try:
        import sys
        info["python_version"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    except Exception:
        pass

    return info


TOOL_DEF = {
    "name": "self_capabilities",
    "description": (
        "[SELF-KNOWLEDGE] Scan BAW's own capabilities and report "
        "what tools are available, which LLM providers are configured, "
        "config summary, and known limitations. "
        "Answer 'what can you do?' without hardcoded lists."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "risk_level": "low",
}
