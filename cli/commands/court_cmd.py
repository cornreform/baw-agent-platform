"""M4: `baw court` CLI subcommands — nightly, docket, recent."""

import sys
from pathlib import Path


def cmd_court_nightly():
    """`baw court nightly` — print the previous-24h court activity summary.

    Wired to cron at 03:00 via the install script. Output goes to
    stdout; the cron job pipes it to Telegram.
    """
    from core.night_court import format_nightly_summary
    print(format_nightly_summary())


def cmd_court_docket():
    """`baw court docket` — show the current docket status."""
    try:
        from core.docket import get_status
        st = get_status()
        print("⚖️ 法庭排程:")
        print(f"  排隊中: {st['queued']}")
        print(f"  執行中: {st['running']}")
        print(f"  過去 24h 完成: {st['done_today']}")
        print(f"  系統並行上限: {st['max_concurrent_system']} sub-agents")
        print(f"  每用戶並行上限: {st['max_concurrent_per_user']} 案件")
        if st['users_currently_running']:
            print(f"  現時執行緊嘅用戶: {', '.join(st['users_currently_running'])}")
    except Exception as e:
        print(f"⚖️ docket status unavailable: {e}")


def cmd_court_pickup():
    """`baw court pickup` — recover 'running' entries from a crashed process."""
    try:
        from core.docket import pickup_crashed
        n = pickup_crashed()
        print(f"♻️ 恢復咗 {n} 個因 crash 殘留嘅 running case 落 queue。")
    except Exception as e:
        print(f"❌ pickup failed: {e}")


def cmd_court_recent(argv=None):
    """`baw court recent [N]` — show last N court verdicts (default: 5)."""
    from core.court import recent_cases
    n = 5
    if argv and len(argv) > 1 and argv[1].isdigit():
        n = int(argv[1])
    cases = recent_cases(limit=n)
    if not cases:
        print("⚖️ 暫無已歸檔案件。")
        return
    print(f"⚖️ 最近 {len(cases)} 宗案件:\n")
    for c in cases:
        verdict_icon = {
            "approved": "✅",
            "retry": "🔁",
            "appeal": "📤",
            "dismissed": "🚫",
            "stay": "⏸️",
        }.get(c.get("verdict", ""), "❓")
        print(
            f"  {verdict_icon} #{c['case_id']} | "
            f"Tier {c.get('tier', '?')} | "
            f"得分 {c.get('score', '?')}/10 | "
            f"用時 {c.get('elapsed_sec', 0):.1f}s | "
            f"{c.get('goal', '?')[:40]}"
        )


def cmd_court_detail(case_id: str):
    """`baw court detail <case_id>` — show full case record."""
    try:
        from core.court import get_case, render_verdict
    except ImportError:
        print(f"⚖️ 無法載入 court 模組")
        return
    case = get_case(case_id)
    if not case:
        print(f"⚖️ 案件 #{case_id} 唔存在")
        return
    print(f"⚖️ 案件 #{case_id} 完整記錄:\n")
    print(f"  目標: {case.get('goal', '?')[:200]}")
    print(f"  Tier: {case.get('tier', '?')}")
    print(f"  狀態: {case.get('verdict', '?')}")
    print(f"  得分: {case.get('score', '?')}/10")
    print(f"  原因: {case.get('reason', '?')[:200]}")
    print(f"  用時: {case.get('elapsed_sec', 0):.1f}s")
    print(f"  重試: {case.get('retry_count', 0)}x")
    print(f"  上訴: {case.get('appeal_count', 0)}x")
    print(f"  Defendant: {case.get('defendant_model', '?')}")
    print(f"  Judge: {case.get('judge_model', '?')}")
    print(f"  Prosecutor: {case.get('prosecutor_model', '?')}")
    print(f"\n  最終摘要: {case.get('final_summary', '?')[:300]}")
    print(f"\n  證物 ({len(case.get('evidence', []))} 件):")
    for ev in case.get("evidence", [])[:10]:
        print(f"    [{ev.get('role', '?')}] {ev.get('content', '')[:150]}")
    if len(case.get("evidence", [])) > 10:
        print(f"    ... 尚有 {len(case['evidence']) - 10} 件證物")


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print()
        print("Subcommands:")
        print("  nightly  — print the previous-24h court activity summary")
        print("  docket   — show queue/running/completed counts")
        print("  pickup   — recover 'running' entries from a crashed process")
        print("  recent   — show last N court verdicts (default: 5)")
        print("  detail   — show full case record by ID")
        return 0
    sub = argv[0]
    if sub == "nightly":
        cmd_court_nightly()
    elif sub == "docket":
        cmd_court_docket()
    elif sub == "pickup":
        cmd_court_pickup()
    elif sub == "recent":
        cmd_court_recent(argv)
    elif sub == "detail":
        if len(argv) < 2:
            print("Usage: baw court detail <case_id>")
            return 1
        cmd_court_detail(argv[1])
    else:
        print(f"Unknown subcommand: {sub}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
