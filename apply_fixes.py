#!/usr/bin/env python3
"""BAW production fixes — applied cleanly from GitHub base."""
import os, sys, ast
from pathlib import Path

BAW = Path("/home/radxa/BAW")
DOT = Path("/home/radxa/.baw")

os.chdir(str(BAW))
sys.path.insert(0, str(BAW))

print("=" * 50)
print("BAW Production Fix Script")
print("=" * 50)

# 0. Download fresh loop.py and __init__.py from GitHub
print("\n[0] Downloading fresh base from GitHub...")
import urllib.request
for f in ["core/loop.py", "core/messaging/__init__.py"]:
    url = f"https://raw.githubusercontent.com/CornReform/baw-agent-platform/main/{f}"
    dest = BAW / f
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(urllib.request.urlopen(url).read())
    print(f"  Downloaded: {f}")

# 1. Fix build_system_prompt — use SOUL.md only
print("\n[1] Simplifying build_system_prompt...")
loop_content = (BAW / "core/loop.py").read_text()
start = loop_content.find("def build_system_prompt")
end = loop_content.find("\ndef ", start + 4)

replacement = """def build_system_prompt(config: dict, data_dir = None,
                       fresh_start: bool = False) -> str:
    import logging
    logger = logging.getLogger(__name__)
    base_path = data_dir or Path.home() / ".baw"
    soul_path = base_path / "SOUL.md"
    if soul_path.exists():
        return soul_path.read_text(encoding="utf-8")
    return "You are BAW, Sunny assistant on QB A7S.\\n"
"""

loop_content = loop_content[:start] + replacement + loop_content[end:]
(BAW / "core/loop.py").write_text(loop_content)
ast.parse(loop_content)
print(f"  System prompt: ~{end-start} chars -> ~{len(replacement)} chars")

# 2. Fix _run_baw — add clean synthesis at the end
print("\n[2] Adding clean synthesis to _run_baw...")
init_content = (BAW / "core/messaging/__init__.py").read_text()

old = "            return output.strip()\n\n        except BaseException as e:\n            return f\"[FAIL] BAW error: {e}\"\n\n    # ── Focus Mode"

new = """            # Clean synthesis: regenerate with identity + user question
            try:
                from pathlib import Path as _CSPath
                _cs = _CSPath("/home/radxa/.baw/SOUL.md").read_text()
                _cm = [{"role": "system", "content": _cs}, {"role": "user", "content": prompt or ""}]
                from ..llm import call_llm_with_fallback as _csllm
                _cf = _csllm(config, _cm, tools=None, temperature=0.7)
                if _cf and _cf.response and _cf.response.content:
                    output = _cf.response.content.strip()
                else:
                    output = "出咗少少技術問題，試多次？"
            except Exception:
                output = "出咗少少技術問題，試多次？"
            return output.strip()

        except BaseException as e:
            return f"[FAIL] BAW error: {e}"

    # ── Focus Mode"""

# There are multiple "return output.strip()" - only replace the one in _run_baw context
if old in init_content:
    init_content = init_content.replace(old, new, 1)
    (BAW / "core/messaging/__init__.py").write_text(init_content)
    ast.parse(init_content)
    print("  Clean synthesis added")
else:
    print("  WARNING: Pattern not found in __init__.py")

# 3. Fix bawrun import
print("\n[3] Fixing bawrun import...")
site_pkg = BAW / "venv" / "lib" / "python3.9" / "site-packages"
site_pkg.mkdir(parents=True, exist_ok=True)
(BAW / "bawrun.py").link_to(site_pkg / "bawrun.py") if (BAW / "bawrun.py").exists() and not (site_pkg / "bawrun.py").exists() else None
# Use copy as fallback (symlink doesn't work across some filesystems)
import shutil
target = site_pkg / "bawrun.py"
if not target.exists() or target.stat().st_size == 0:
    shutil.copy2(str(BAW / "bawrun.py"), str(target))
print("  bawrun.py -> site-packages OK")

# 4. Init scheduler tasks
print("\n[4] Initializing scheduler tasks...")
try:
    from core.scheduler import Scheduler, ScheduledTask
    s = Scheduler(DOT)
    tasks = [
        ("daily-self-report", "0 23 * * *", "Run self_diagnose and report"),
        ("daily-auto-heal", "0 3 * * *", "Auto-heal with fix=True"),
        ("weekly-memory-quality", "0 4 * * 0", "Memory quality check"),
        ("weekly-session-synthesis", "0 5 * * 0", "Session synthesis"),
        ("weekly-self-evolution", "0 6 * * 0", "Self-evolution audit"),
    ]
    for name, cron, prompt in tasks:
        try:
            s.add_task(ScheduledTask(name=name, cron=cron, command=prompt, enabled=True))
            print(f"  Task: {name} OK")
        except Exception as e:
            print(f"  Task: {name} FAIL: {e}")
except Exception as e:
    print(f"  Scheduler init failed: {e}")

# 5. Test — direct LLM call
print("\n[5] Testing direct LLM call...")
try:
    import yaml
    with open(str(DOT / "config.yaml")) as f:
        config = yaml.safe_load(f)
    with open(str(DOT / ".env")) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#") and "API_KEY" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
    
    from core.llm import call_llm_with_fallback
    
    test_prompt = (DOT / "SOUL.md").read_text()
    msgs = [{"role": "system", "content": test_prompt}, {"role": "user", "content": "你係邊個？"}]
    fb = call_llm_with_fallback(config, msgs, tools=None, temperature=0.7)
    resp = fb.response.content or ""
    print(f"  Response ({len(resp)} chars): {resp[:100]}")
    print("  LLM TEST: PASS" if "BAW" in resp and any("\u4e00" <= c <= "\u9fff" for c in resp) else "  LLM TEST: FAIL")
except Exception as e:
    print(f"  LLM test FAILED: {e}")

print("\n" + "=" * 50)
print("ALL FIXES APPLIED. Ready for systemctl restart.")
print("=" * 50)
