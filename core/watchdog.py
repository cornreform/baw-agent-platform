"""BAW — Health Watchdog

Lightweight periodic health checks that run every 5 minutes.
On failure, writes alerts to ~/.baw/alerts/ and attempts Telegram notification.

Checks:
  - API key presence & format
  - config.yaml syntax
  - Memory store accessibility
  - Tool registry completeness
  - Network reachability (provider endpoints)
"""
from __future__ import annotations
import os
import re
import time
import json
import yaml
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable


# ── Constants ──

ALERTS_DIR = Path.home() / ".baw" / "alerts"
POLL_INTERVAL = 300  # 5 minutes
MAX_ALERT_AGE_DAYS = 7

# Provider endpoint quick-check URLs (HEAD / GET, no auth needed)
PROVIDER_PING_URLS = {
    "deepseek": "https://api.deepseek.com/v1/models",
    "minimax": "https://api.minimax.io/v1/models",
    "openai": "https://api.openai.com/v1/models",
}


# ── Alert model ──

class HealthAlert:
    def __init__(self, check: str, status: str, detail: str):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.check = check      # e.g. "api_key", "config", "memory", "tools", "network"
        self.status = status    # "pass", "warn", "fail"
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "check": self.check,
            "status": self.status,
            "detail": self.detail,
        }

    def to_text(self) -> str:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "🚨"}.get(self.status, "❓")
        return f"{icon} [{self.status.upper()}] {self.check}: {self.detail}"


# ── Watchdog engine ──

class Watchdog:
    """Runs periodic lightweight health checks."""

    def __init__(self, data_dir: Path | str, notify_fn: Callable[[str], None] | None = None):
        self.data_dir = Path(data_dir)
        self._alerts_dir = self.data_dir / "alerts"
        self._alerts_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        self._thread: threading.Thread | None = None
        self._notify = notify_fn  # callback for Telegram notification
        self._last_results: list[dict] = []

    # ── Lifecycle ──

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.run_checks()
            except Exception as e:
                self._write_alert("watchdog_internal", "fail", f"Watchdog loop exception: {e}")
            time.sleep(POLL_INTERVAL)

    # ── Checks ──

    def run_checks(self) -> list[HealthAlert]:
        """Run all checks, write alerts, notify on failure. Returns results."""
        results: list[HealthAlert] = []

        results.append(self._check_config())
        results.append(self._check_api_keys())
        results.append(self._check_memory())
        results.append(self._check_tools())
        results.append(self._check_network())
        results.append(self._check_exceptions())
        results.append(self._check_latency())

        self._last_results = [r.to_dict() for r in results]

        # Write any non-pass alerts
        fails = [r for r in results if r.status != "pass"]
        for alert in fails:
            self._write_alert(alert.check, alert.status, alert.detail)

        # Notify if any fail
        if fails and self._notify:
            msg = "\n".join(a.to_text() for a in fails)
            try:
                self._notify(f"🚨 BAW Health Alert ({len(fails)} issue(s)):\n\n{msg}")
            except Exception:
                pass

        return results

    def _check_exceptions(self) -> HealthAlert:
        try:
            from core.exception_tracker import count_recent
            count_1h, _ = count_recent(hours=1)
            count_24h, entries = count_recent(hours=24)
            if count_1h >= 5:
                return HealthAlert("exceptions", "fail", f"{count_1h} exceptions in last 1h (threshold: 5)")
            if count_1h >= 2:
                return HealthAlert("exceptions", "warn", f"{count_1h} exceptions in last 1h")
            if count_24h >= 20:
                return HealthAlert("exceptions", "warn", f"{count_24h} exceptions in last 24h")
            return HealthAlert("exceptions", "pass", f"{count_24h} exceptions in 24h, {count_1h} in 1h")
        except Exception as e:
            return HealthAlert("exceptions", "warn", f"Could not check exceptions: {e}")

    def _check_latency(self) -> HealthAlert:
        try:
            import json
            log_path = self.data_dir / "logs" / "latency.jsonl"
            if not log_path.exists():
                return HealthAlert("latency", "pass", "No latency data yet")

            cutoff = time.time() - 3600  # last 1h
            slow_count = 0
            timeout_count = 0
            total = 0
            providers: dict[str, list[float]] = {}

            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("ts", 0) < cutoff:
                            continue
                        total += 1
                        prov = e.get("provider", "unknown")
                        latency = e.get("latency", 0)
                        status = e.get("status", "ok")
                        providers.setdefault(prov, []).append(latency)
                        if status == "timeout":
                            timeout_count += 1
                        elif latency > 30:
                            slow_count += 1
                    except json.JSONDecodeError:
                        continue

            if timeout_count >= 3:
                return HealthAlert("latency", "fail", f"{timeout_count} timeouts in last 1h")
            if slow_count >= 3:
                return HealthAlert("latency", "warn", f"{slow_count} calls >30s in last 1h")
            avg_str = ", ".join(f"{p}: {sum(v)/len(v):.1f}s" for p, v in providers.items() if v)
            return HealthAlert("latency", "pass", f"{total} calls, avg: {avg_str or 'N/A'}")
        except Exception as e:
            return HealthAlert("latency", "warn", f"Could not check latency: {e}")

    def _check_config(self) -> HealthAlert:
        cfg_path = self.data_dir / "config.yaml"
        if not cfg_path.exists():
            return HealthAlert("config", "fail", f"config.yaml not found at {cfg_path}")
        try:
            with open(cfg_path) as f:
                yaml.safe_load(f)
            return HealthAlert("config", "pass", "YAML syntax OK")
        except yaml.YAMLError as e:
            return HealthAlert("config", "fail", f"YAML syntax error: {e}")
        except Exception as e:
            return HealthAlert("config", "warn", f"Could not read config: {e}")

    def _check_api_keys(self) -> HealthAlert:
        env_path = self.data_dir / ".env"
        if not env_path.exists():
            return HealthAlert("api_key", "warn", ".env not found")

        missing = []
        required_patterns = {
            "DEEPSEEK_API_KEY": r"^sk-[a-zA-Z0-9]{20,}$",
            "MINIMAX_API_KEY": r"^.{10,}$",
        }
        env_text = env_path.read_text(encoding="utf-8")
        env_lines = {}
        for line in env_text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_lines[k.strip()] = v.strip()

        for key, pattern in required_patterns.items():
            val = env_lines.get(key, "")
            if not val:
                missing.append(key)
            elif not re.match(pattern, val):
                missing.append(f"{key} (invalid format)")

        if missing:
            return HealthAlert("api_key", "fail", f"Missing/invalid keys: {', '.join(missing)}")
        return HealthAlert("api_key", "pass", f"{len(required_patterns)} keys OK")

    def _check_memory(self) -> HealthAlert:
        mem_dir = self.data_dir / "memory"
        store = mem_dir / "store.jsonl"
        if not store.exists():
            return HealthAlert("memory", "warn", "store.jsonl not found (memory empty)")
        try:
            lines = store.read_text(encoding="utf-8").strip().split("\n")
            valid = sum(1 for ln in lines if ln.strip())
            return HealthAlert("memory", "pass", f"{valid} entries")
        except Exception as e:
            return HealthAlert("memory", "fail", f"Cannot read store: {e}")

    def _check_tools(self) -> HealthAlert:
        try:
            tools_dir = Path(__file__).resolve().parent.parent / "tools"
            py_files = [f for f in tools_dir.glob("*.py") if f.name != "__init__.py"]
            total = len(py_files)
            if total < 5:
                return HealthAlert("tools", "warn", f"Only {total} tool files found (suspicious)")
            return HealthAlert("tools", "pass", f"{total} tool files present")
        except Exception as e:
            return HealthAlert("tools", "warn", f"Could not inspect tools dir: {e}")

    def _check_network(self) -> HealthAlert:
        import socket
        failures = []
        # Simple TCP connectivity checks to well-known hosts
        checks = [
            ("deepseek", "api.deepseek.com", 443),
            ("minimax", "api.minimax.io", 443),
            ("google", "www.google.com", 443),
        ]
        for name, host, port in checks:
            try:
                sock = socket.create_connection((host, port), timeout=5)
                sock.close()
            except Exception as e:
                failures.append(f"{name}: {type(e).__name__}")
        if failures:
            return HealthAlert("network", "warn", f"Unreachable: {', '.join(failures)}")
        return HealthAlert("network", "pass", "All provider endpoints reachable")

    # ── Persistence ──

    def _write_alert(self, check: str, status: str, detail: str):
        alert = HealthAlert(check, status, detail)
        fname = f"alert-{int(time.time())}-{check}.json"
        (self._alerts_dir / fname).write_text(
            json.dumps(alert.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._prune_old_alerts()

    def _prune_old_alerts(self):
        now = time.time()
        max_age = MAX_ALERT_AGE_DAYS * 86400
        for f in self._alerts_dir.glob("alert-*.json"):
            try:
                if now - f.stat().st_mtime > max_age:
                    f.unlink()
            except Exception:
                pass

    # ── Query ──

    def recent_alerts(self, hours: int = 24) -> list[dict]:
        """Return alerts from the last N hours."""
        cutoff = time.time() - (hours * 3600)
        alerts = []
        for f in sorted(self._alerts_dir.glob("alert-*.json"), reverse=True):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
                alerts.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return alerts[:50]  # cap at 50

    def last_check_summary(self) -> str:
        if not self._last_results:
            return "No checks run yet."
        lines = ["🐾 BAW Health Check Summary:"]
        for r in self._last_results:
            icon = {"pass": "✅", "warn": "⚠️", "fail": "🚨"}.get(r["status"], "❓")
            lines.append(f"  {icon} {r['check']}: {r['status']} — {r['detail']}")
        return "\n".join(lines)
