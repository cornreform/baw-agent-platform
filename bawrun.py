""""BAW agent-mode runner — called by scheduler for LLM-driven cron tasks.

Usage (by scheduler):
    python3 -m bawrun <task_dir> <prompt>

Runs the prompt through run_agent, captures output, writes to task_dir.
"""
import sys
import json
from pathlib import Path

# ── Boot ──
BAW_ROOT = Path("/app")
sys.path.insert(0, str(BAW_ROOT))

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 -m bawrun <task_dir> <prompt>", file=sys.stderr)
        sys.exit(1)

    task_dir = Path(sys.argv[1])
    prompt = sys.argv[2]

    task_dir.mkdir(parents=True, exist_ok=True)

    # ── Load config ──
    from core.config import load_config
    config = load_config()

    # Determine model: default from config, or step-3.7-flash
    model_id = config.get("model", {}).get("default", None)

    # ── Register tools ──
    from tools import register_all
    register_all()

    # ── Run agent ──
    from core.loop import run_agent
    (task_dir / "status.txt").write_text("running", encoding="utf-8")

    try:
        response, info = run_agent(
            prompt=prompt,
            config=config,
            model_id=model_id,
            mode="hybrid",
            interactive=False,
            max_tool_turns=15,
        )
        (task_dir / "stdout.txt").write_text(str(response), encoding="utf-8")
        (task_dir / "status.txt").write_text("completed", encoding="utf-8")
        print(f"[BAW-RUN] ✅ completed: {prompt[:60]}...")
        if info:
            (task_dir / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    except Exception as e:
        err = str(e)
        (task_dir / "stderr.txt").write_text(err, encoding="utf-8")
        (task_dir / "status.txt").write_text(f"error: {err[:100]}", encoding="utf-8")
        print(f"[BAW-RUN] ❌ error: {err[:200]}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
