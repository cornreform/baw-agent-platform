"""BAW built-in: self_diagnose — comprehensive health check.

Runs all subsystem checks and reports a unified health score.
Covers: container health, providers, tools, memory, disk, cron.
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path


_BAW_HOME = Path(os.environ.get("BAW_HOME", "/app"))
_BAW_DATA = Path(os.environ.get("BAW_RUNTIME_HOME", Path.home() / ".baw"))
_DOCKER = shutil.which("docker")


def _run(cmd: list[str], timeout: int = 15) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {"ok": r.returncode == 0, "output": r.stdout.strip(), "error": r.stderr.strip() or None}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _check_container() -> dict:
    """Check container health status."""
    if not _DOCKER:
        return {"status": "unknown", "detail": "docker CLI not available"}
    r = _run([_DOCKER, "inspect", "baw-telegram", "--format",
              "{{.State.Status}}|{{.State.Health.Status}}|{{.State.StartedAt}}"])
    if r["ok"]:
        parts = r["output"].split("|")
        return {
            "status": "healthy" if "healthy" in r["output"] else "unhealthy",
            "detail": f"Status: {parts[0] if len(parts) > 0 else '?'}, "
                      f"Health: {parts[1] if len(parts) > 1 else '?'}, "
                      f"Started: {parts[2] if len(parts) > 2 else '?'}",
        }
    return {"status": "error", "detail": r.get("error", "unknown")}


def _check_providers() -> list[dict]:
    """Check all configured LLM providers."""
    config_path = _BAW_DATA / "config.yaml"
    if not config_path.exists():
        return []
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
    except Exception:
        return [{"name": "?", "status": "error", "detail": "config unreadable"}]

    results = []
    providers = cfg.get("providers", {})
    from core.llm import get_model, call_llm_with_fallback

    for pname, pcfg in providers.items():
        key_env = pcfg.get("api_key_env", "")
        has_key = bool(os.environ.get(key_env, ""))
        models = [m.get("id", "?") for m in pcfg.get("models", [])[:3]]
        results.append({
            "name": pname,
            "status": "key_ok" if has_key else "no_key",
            "models": models,
        })
    return results


def _check_tools() -> dict:
    """Check that core tools are registered."""
    try:
        from core.tools import list_tools
        tools = list_tools()
        return {"status": "ok", "tool_count": len(tools),
                "tool_names": [t.name for t in tools[:15]]}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def _check_memory() -> dict:
    """Check memory store health."""
    mem_file = _BAW_DATA / "memory" / "store.jsonl"
    if mem_file.exists():
        count = sum(1 for _ in mem_file.open() if _.strip())
        return {"status": "ok", "entries": count, "size_kb": round(mem_file.stat().st_size / 1024, 1)}
    return {"status": "ok", "entries": 0, "size_kb": 0}


def _check_disk() -> dict:
    """Check disk usage of key directories."""
    results = {}
    for path in [_BAW_DATA, Path("/tmp")]:
        p = Path(path) if isinstance(path, str) else path
        if p.exists():
            r = _run(["du", "-sh", str(p)])
            results[str(p)] = r["output"] if r["ok"] else "N/A"
    r2 = _run(["df", "-h", "/"])
    if r2["ok"]:
        results["/"] = r2["output"].split("\n")[1] if "\n" in r2["output"] else r2["output"]
    return results


def _check_config() -> dict:
    """Check config integrity."""
    config_path = _BAW_DATA / "config.yaml"
    if not config_path.exists():
        return {"status": "error", "detail": "config.yaml not found"}
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
        default_model = cfg.get("model", {}).get("default", "?")
        fallback = cfg.get("model", {}).get("fallback", "none")
        return {"status": "ok", "default_model": default_model, "fallback": fallback}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def _check_cron() -> dict:
    """Check cron jobs."""
    cron_file = _BAW_DATA / "cron" / "jobs.json"
    if cron_file.exists():
        try:
            jobs = json.loads(cron_file.read_text())
            count = len(jobs) if isinstance(jobs, list) else 0
            return {"status": "ok", "cron_jobs": count}
        except Exception:
            return {"status": "ok", "cron_jobs": 0}
    return {"status": "ok", "cron_jobs": 0}


def _handler(
    quick: bool = False,
) -> str:
    """Run comprehensive self-diagnosis.

    Checks: container health, LLM providers, registered tools,
    memory store, disk usage, config integrity, cron jobs.

    Args:
        quick: If True, skip provider checks (faster).
    """
    checks = {}

    # Fast checks
    checks["container"] = _check_container()
    checks["tools"] = _check_tools()
    checks["memory"] = _check_memory()
    checks["disk"] = _check_disk()
    checks["config"] = _check_config()
    checks["cron"] = _check_cron()

    # Slow checks (providers)
    if not quick:
        checks["providers"] = {"status": "ok", "details": _check_providers()}

    # Compute overall score
    score = 0
    max_score = len(checks) * 10
    for name, result in checks.items():
        if isinstance(result, dict):
            if result.get("status") == "healthy":
                score += 10
            elif result.get("status") == "ok":
                score += 10
            elif result.get("status") == "key_ok":
                score += 8
            elif result.get("status") in ("unknown", "no_key"):
                score += 5
            else:
                score += 2
        else:
            score += 5

    pct = round(score / max_score * 100, 1) if max_score > 0 else 0

    return json.dumps({
        "ok": True,
        "health_score": f"{pct}%",
        "checks": checks,
        "recommendations": _get_recommendations(checks),
    }, ensure_ascii=False, indent=2)


def _get_recommendations(checks: dict) -> list[str]:
    """Generate recommendations based on check results."""
    recs = []
    prov = checks.get("providers", {})
    if isinstance(prov, dict):
        details = prov.get("details", [])
        if isinstance(details, list):
            for p in details:
                if p.get("status") == "no_key":
                    recs.append(f"⚠️ {p['name']}: API key missing — set {p['name'].upper()}_API_KEY in .env")
    if isinstance(checks.get("memory"), dict) and checks["memory"].get("size_kb", 0) > 1024:
        recs.append("ℹ️ Memory store >1MB — consider compacting")
    return recs


TOOL_DEF = {
    "name": "self_diagnose",
    "description": (
        "[SELF-OPERATION] Run comprehensive self-diagnosis on BAW. "
        "Checks container health, LLM providers, tools registration, "
        "memory store, disk usage, config integrity, and cron jobs. "
        "Returns a health score (0-100%) with recommendations."
    ),
    "handler": _handler,
    "parameters": {
        "type": "object",
        "properties": {
            "quick": {
                "type": "boolean",
                "description": "Skip provider checks for faster diagnosis.",
                "default": False,
            },
        },
        "required": [],
    },
    "risk_level": "low",
}
