"""P3: Health Dashboard — 10-point system health check.

Produces a single score 0-10 with per-subsystem breakdown.
Callable via `baw --doctor` or programmatically.
"""
from __future__ import annotations

import time
import json
import os
from pathlib import Path
from datetime import datetime, timezone


def health_check() -> dict:
    """Run a 10-point health check. Returns {score, checks, timestamp}."""
    checks = []
    total_score = 0
    max_score = 0

    # 1. Config file (1 point)
    config_path = Path.home() / ".baw" / "config.yaml"
    if config_path.exists():
        try:
            import yaml
            yaml.safe_load(config_path.read_text(encoding="utf-8"))
            checks.append({"name": "config", "score": 1, "status": "ok"})
            total_score += 1
        except Exception as e:
            checks.append({"name": "config", "score": 0, "status": "error", "detail": str(e)[:100]})
    else:
        checks.append({"name": "config", "score": 0, "status": "missing"})
    max_score += 1

    # 2. Environment / API keys (1 point)
    env_path = Path.home() / ".baw" / ".env"
    if env_path.exists():
        env_text = env_path.read_text(encoding="utf-8")
        key_count = sum(1 for line in env_text.split("\n") if "=" in line and not line.startswith("#"))
        if key_count >= 2:
            checks.append({"name": "api_keys", "score": 1, "status": "ok", "detail": f"{key_count} keys"})
            total_score += 1
        else:
            checks.append({"name": "api_keys", "score": 0, "status": "warning", "detail": f"only {key_count} keys"})
    else:
        checks.append({"name": "api_keys", "score": 0, "status": "missing"})
    max_score += 1

    # 3. Memory store (1 point)
    mem_path = Path.home() / ".baw" / "memory" / "store.jsonl"
    if mem_path.exists():
        try:
            lines = mem_path.read_text(encoding="utf-8").strip().split("\n")
            entries = [l for l in lines if l.strip()]
            checks.append({"name": "memory", "score": 1, "status": "ok", "detail": f"{len(entries)} entries"})
            total_score += 1
        except Exception:
            checks.append({"name": "memory", "score": 0, "status": "error"})
    else:
        checks.append({"name": "memory", "score": 0, "status": "missing"})
    max_score += 1

    # 4. Cron daemon (1 point)
    schedule_path = Path.home() / ".baw" / "schedule.yaml"
    state_path = Path.home() / ".baw" / "schedule_state.json"
    if schedule_path.exists() and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if len(state) >= 2:
                checks.append({"name": "cron", "score": 1, "status": "ok", "detail": f"{len(state)} jobs"})
                total_score += 1
            else:
                checks.append({"name": "cron", "score": 0, "status": "warning", "detail": f"only {len(state)} jobs"})
        except Exception:
            checks.append({"name": "cron", "score": 0, "status": "error"})
    else:
        checks.append({"name": "cron", "score": 0, "status": "missing"})
    max_score += 1

    # 5. Model health — check last latency log (1 point)
    latency_path = Path.home() / ".baw" / "logs" / "latency.jsonl"
    if latency_path.exists():
        try:
            lines = latency_path.read_text(encoding="utf-8").strip().split("\n")
            recent = [json.loads(l) for l in lines[-20:] if l.strip()]
            ok_count = sum(1 for r in recent if r.get("status") == "ok")
            if ok_count >= len(recent) * 0.5:
                checks.append({"name": "models", "score": 1, "status": "ok", "detail": f"{ok_count}/{len(recent)} ok"})
                total_score += 1
            else:
                checks.append({"name": "models", "score": 0, "status": "warning", "detail": f"only {ok_count}/{len(recent)} ok"})
        except Exception:
            checks.append({"name": "models", "score": 0, "status": "error"})
    else:
        checks.append({"name": "models", "score": 0, "status": "missing", "detail": "no latency log yet"})
    max_score += 1

    # 6. Exceptions (1 point — fewer is better)
    exc_path = Path.home() / ".baw" / "logs" / "exceptions.jsonl"
    if exc_path.exists():
        try:
            lines = exc_path.read_text(encoding="utf-8").strip().split("\n")
            recent_24h = 0
            cutoff = time.time() - 86400
            for l in lines:
                if not l.strip():
                    continue
                try:
                    e = json.loads(l)
                    if e.get("ts", 0) > cutoff:
                        recent_24h += 1
                except Exception as _e:
                    import logging
                    logging.getLogger("baw.health").debug(f"exception log parse failed: {_e}")
            if recent_24h <= 10:
                checks.append({"name": "exceptions", "score": 1, "status": "ok", "detail": f"{recent_24h} in 24h"})
                total_score += 1
            elif recent_24h <= 50:
                checks.append({"name": "exceptions", "score": 0.5, "status": "warning", "detail": f"{recent_24h} in 24h"})
                total_score += 0.5
            else:
                checks.append({"name": "exceptions", "score": 0, "status": "error", "detail": f"{recent_24h} in 24h"})
        except Exception:
            checks.append({"name": "exceptions", "score": 0, "status": "error"})
    else:
        checks.append({"name": "exceptions", "score": 1, "status": "ok", "detail": "no exceptions"})
        total_score += 1
    max_score += 1

    # 7. Court system (1 point)
    court_dir = Path.home() / ".baw" / "court" / "cases"
    if court_dir.exists():
        case_files = list(court_dir.glob("*.json"))
        if case_files:
            checks.append({"name": "court", "score": 1, "status": "ok", "detail": f"{len(case_files)} cases"})
            total_score += 1
        else:
            # Court infrastructure exists but no cases yet — half credit
            checks.append({"name": "court", "score": 0.5, "status": "ok", "detail": "ready, no cases yet"})
            total_score += 0.5
    else:
        checks.append({"name": "court", "score": 0.5, "status": "ok", "detail": "court dir auto-created on first case"})
        total_score += 0.5
    max_score += 1

    # 8. Skills (1 point)
    skills_dir = Path.home() / ".baw" / "skills"
    if skills_dir.exists():
        skill_files = list(skills_dir.rglob("SKILL.md")) + list(skills_dir.rglob("*.yaml"))
        if len(skill_files) >= 1:
            checks.append({"name": "skills", "score": 1, "status": "ok", "detail": f"{len(skill_files)} skills"})
            total_score += 1
        else:
            checks.append({"name": "skills", "score": 0.5, "status": "warning", "detail": "no skills"})
            total_score += 0.5
    else:
        checks.append({"name": "skills", "score": 0, "status": "missing"})
    max_score += 1

    # 9. Backups (1 point)
    backup_dir = Path.home() / ".baw" / "backups"
    if backup_dir.exists():
        backups = list(backup_dir.glob("*.tar.gz"))
        if backups:
            newest = max(b.stat().st_mtime for b in backups)
            age_hours = (time.time() - newest) / 3600
            if age_hours < 48:
                checks.append({"name": "backups", "score": 1, "status": "ok", "detail": f"{len(backups)} backups"})
                total_score += 1
            else:
                checks.append({"name": "backups", "score": 0.5, "status": "warning", "detail": f"oldest: {age_hours:.0f}h"})
                total_score += 0.5
        else:
            checks.append({"name": "backups", "score": 0, "status": "missing", "detail": "no backups yet"})
    else:
        checks.append({"name": "backups", "score": 0, "status": "missing", "detail": "no backup dir"})
    max_score += 1

    # 10. Process alive (1 point) — we ARE the process, so this always passes
    checks.append({"name": "process", "score": 1, "status": "ok", "detail": f"pid {os.getpid()}"})
    total_score += 1
    max_score += 1

    # ═══════════════════════════════════════════════════════════════
    # NEW: Extended checks — cover recent system components
    # ═══════════════════════════════════════════════════════════════

    # 11. Tools registry (1 point) — verify all tools loadable
    _tools_ok = 0
    _tools_total = 0
    try:
        from core.tools import list_tools, _tools as _tool_registry
        _tools_total = len(_tool_registry) if _tool_registry else 0
        _tools_ok = sum(1 for t in (_tool_registry or {}).values() if t is not None)
        if _tools_ok >= 35:
            checks.append({"name": "tools", "score": 1, "status": "ok", "detail": f"{_tools_total} registered"})
            total_score += 1
        else:
            checks.append({"name": "tools", "score": 0.5, "status": "warning", "detail": f"only {_tools_ok}/{_tools_total} ok"})
            total_score += 0.5
    except Exception as _te:
        checks.append({"name": "tools", "score": 0, "status": "error", "detail": str(_te)[:80]})
    max_score += 1

    # 12. Docker socket (1 point) — verify docker access
    _dock = Path("/var/run/docker.sock")
    if _dock.exists():
        import stat as _st
        _mode = _dock.stat().st_mode
        try:
            _writ = bool(_mode & _st.S_IWGRP) or bool(_mode & _st.S_IWUSR)
            if _writ:
                checks.append({"name": "docker", "score": 1, "status": "ok"})
                total_score += 1
            else:
                checks.append({"name": "docker", "score": 0.5, "status": "warning", "detail": "socket exists but not writable"})
                total_score += 0.5
        except Exception:
            checks.append({"name": "docker", "score": 0.5, "status": "warning"})
            total_score += 0.5
    else:
        checks.append({"name": "docker", "score": 0, "status": "missing", "detail": "no docker.sock mounted"})
    max_score += 1

    # 13. Knowledge graph (1 point) — verify KG integrity
    _kg_path = Path.home() / ".baw" / "knowledge_graph.json"
    if _kg_path.exists():
        try:
            _kg = json.loads(_kg_path.read_text(encoding="utf-8"))
            _triples = len(_kg.get("triples", []))
            _entities = len(_kg.get("entities", {}))
            if _triples > 0:
                checks.append({"name": "knowledge_graph", "score": 1, "status": "ok", "detail": f"{_triples} triples, {_entities} entities"})
                total_score += 1
            else:
                checks.append({"name": "knowledge_graph", "score": 0.5, "status": "ok", "detail": "empty graph"})
                total_score += 0.5
        except Exception as _ke:
            checks.append({"name": "knowledge_graph", "score": 0, "status": "error", "detail": str(_ke)[:80]})
    else:
        checks.append({"name": "knowledge_graph", "score": 0, "status": "missing"})
    max_score += 1

    # 14. Output quality gate (1 point) — verify validator + HTML balancer
    try:
        from core.output_validator import validate_output, _balance_html
        # Test HTML balancing
        _test = _balance_html("<b>hello <i>world</b>")
        _balanced = "</i>" in _test  # should have closed the unclosed <i>
        if _balanced:
            checks.append({"name": "output_gate", "score": 1, "status": "ok", "detail": "HTML balancer active"})
            total_score += 1
        else:
            checks.append({"name": "output_gate", "score": 0.5, "status": "warning", "detail": "balancer loaded but test unexpected"})
            total_score += 0.5
    except Exception as _ve:
        checks.append({"name": "output_gate", "score": 0, "status": "error", "detail": str(_ve)[:80]})
    max_score += 1

    # 15. Config drift scan (1 point) — check known stale endpoints
    _drift_issues = []
    try:
        import yaml as _y
        _cfg = _y.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        _providers = _cfg.get("providers", {})
        # Check minimax — known .io issue
        _mmx = _providers.get("minimax", {})
        if "minimax.io" in _mmx.get("base_url", ""):
            _drift_issues.append("minimax base_url uses .io (should be minimaxi.com)")
        # Check stepfun — known /step_plan issue
        _sf = _providers.get("stepfun", {})
        if "/step_plan/" in _sf.get("base_url", ""):
            _drift_issues.append("stepfun base_url uses /step_plan (should be /v1)")
        if _drift_issues:
            checks.append({"name": "config_drift", "score": 0.5, "status": "warning", "detail": "; ".join(_drift_issues)})
            total_score += 0.5
        else:
            checks.append({"name": "config_drift", "score": 1, "status": "ok"})
            total_score += 1
    except Exception as _de:
        checks.append({"name": "config_drift", "score": 0.5, "status": "warning", "detail": str(_de)[:80]})
        total_score += 0.5
    max_score += 1

    return {
        "score": round(total_score, 1),
        "max_score": max_score,
        "percentage": round(total_score / max_score * 100, 0) if max_score else 0,
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def doctor_fix() -> dict:
    """Run health check + auto-fix everything fixable.

    Returns {fixes: [{check, issue, action, result}], remaining: int}.
    """
    report = health_check()
    fixes = []
    for c in report["checks"]:
        if c["score"] >= 1:
            continue  # already healthy
        _fix = _attempt_fix(c)
        if _fix:
            fixes.append(_fix)

    remaining = sum(1 for c in report["checks"] if c["score"] < 1)
    return {"fixes": fixes, "remaining": remaining, "before": report}


def _attempt_fix(check: dict) -> dict | None:
    """Try to fix a single health check issue. Returns fix record or None."""
    from pathlib import Path
    name = check["name"]
    detail = check.get("detail", "")

    # ── Config drift: fix known bad endpoints ──
    if name == "config_drift":
        try:
            import yaml
            _cfg_path = Path.home() / ".baw" / "config.yaml"
            if not _cfg_path.exists():
                return None
            _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8"))
            _providers = _cfg.get("providers", {})
            _changed = False
            # Fix minimax .io → minimaxi.com
            _mmx = _providers.get("minimax", {})
            if "minimax.io" in _mmx.get("base_url", ""):
                _mmx["base_url"] = _mmx["base_url"].replace("minimax.io", "minimaxi.com")
                _changed = True
            # Fix stepfun /step_plan/ → /v1
            _sf = _providers.get("stepfun", {})
            if "/step_plan/" in _sf.get("base_url", ""):
                _sf["base_url"] = _sf["base_url"].replace("/step_plan/", "/v1")
                _changed = True
            if _changed:
                import os as _os
                if _cfg_path.exists() and not _os.access(_cfg_path, _os.W_OK):
                    _cfg_path.chmod(0o644)
                _cfg_path.write_text(
                    yaml.dump(_cfg, allow_unicode=True, default_flow_style=False),
                    encoding="utf-8",
                )
                # Invalidate config cache
                try:
                    from core.config import load_config
                    load_config(reload=True)
                except Exception:
                    pass
                return {"check": name, "issue": detail, "action": "fixed base_url drift", "result": "ok"}
            return None
        except Exception as e:
            return {"check": name, "issue": detail, "action": "auto-fix attempted", "result": f"failed: {str(e)[:80]}"}

    # ── Docker socket not writable — report only (can't fix from inside container) ──
    if name == "docker":
        return {"check": name, "issue": detail, "action": "requires docker-compose restart with group_add", "result": "manual"}

    # ── Tools registry — re-register ──
    if name == "tools":
        try:
            from tools import register_all
            from core.tools import list_tools
            register_all()
            _count = len(list_tools())
            return {"check": name, "issue": detail, "action": "re-registered all tools", "result": f"ok ({_count} tools)"}
        except Exception as e:
            return {"check": name, "issue": detail, "action": "re-register failed", "result": f"error: {str(e)[:80]}"}

    # ── Knowledge graph — re-create if corrupted ──
    if name == "knowledge_graph" and "error" in check.get("status", ""):
        try:
            _kg_path = Path.home() / ".baw" / "knowledge_graph.json"
            _kg_path.write_text(
                json.dumps({"triples": [], "entities": {}}, ensure_ascii=False),
                encoding="utf-8",
            )
            return {"check": name, "issue": detail, "action": "re-created empty KG", "result": "ok"}
        except Exception as e:
            return {"check": name, "issue": detail, "action": "KG re-create failed", "result": f"error: {str(e)[:80]}"}

    # ── Output gate — re-import ──
    if name == "output_gate":
        try:
            import importlib
            import core.output_validator
            importlib.reload(core.output_validator)
            from core.output_validator import _balance_html
            _test = _balance_html("<b>test")
            if "</b>" in _test:
                return {"check": name, "issue": detail, "action": "reloaded output_validator", "result": "ok"}
            return {"check": name, "issue": detail, "action": "reloaded but test unexpected", "result": "warning"}
        except Exception as e:
            return {"check": name, "issue": detail, "action": "reload failed", "result": f"error: {str(e)[:80]}"}

    # ── Other checks — can't auto-fix ──
    return None


def format_doctor_report(result: dict) -> str:
    """Format doctor fix results as readable string."""
    lines = ["## 🔧 BAW Doctor — Auto-Fix Report", ""]
    if result["fixes"]:
        for f in result["fixes"]:
            lines.append(f"  {f['check']}: {f['action']} — {f['result']}")
    else:
        lines.append("  No fixable issues found.")

    lines.append("")
    _before = result.get("before", {})
    lines.append(f"Before: {_before.get('score', '?')}/{_before.get('max_score', '?')} ({_before.get('percentage', '?')}%)")
    lines.append(f"Remaining manual issues: {result['remaining']}")
    return "\n".join(lines)


def format_health_report(result: dict) -> str:
    """Format health check result as a readable string."""
    lines = [
        f"## 🏥 BAW Health: {result['score']}/{result['max_score']} ({result['percentage']:.0f}%)",
        f"*{result['timestamp'][:19]}*",
        "",
    ]
    for c in result["checks"]:
        emoji = {"ok": "[OK]", "warning": "[WARN]", "error": "[FAIL]", "missing": "[?]", "unknown": "[?]"}
        e = emoji.get(c["status"], "[?]")
        detail = f" — {c['detail']}" if c.get("detail") else ""
        lines.append(f"  {e} **{c['name']}**: {c['status']}{detail}")

    # Overall assessment
    score = result["score"]
    if score >= 9:
        assessment = "[OK] 系統健康，一切正常"
    elif score >= 7:
        assessment = "🟡 有小問題，建議檢查"
    elif score >= 5:
        assessment = "🟠 需要關注，部分系統異常"
    else:
        assessment = "[CRITICAL] 系統需要緊急修復"
    lines.append(f"\n**{assessment}**")

    return "\n".join(lines)
