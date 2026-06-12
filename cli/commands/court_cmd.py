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


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print()
        print("Subcommands:")
        print("  nightly  — print the previous-24h court activity summary")
        print("  docket   — show queue/running/completed counts")
        print("  pickup   — recover 'running' entries from a crashed process")
        return 0
    sub = argv[0]
    if sub == "nightly":
        cmd_court_nightly()
    elif sub == "docket":
        cmd_court_docket()
    elif sub == "pickup":
        cmd_court_pickup()
    else:
        print(f"Unknown subcommand: {sub}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
