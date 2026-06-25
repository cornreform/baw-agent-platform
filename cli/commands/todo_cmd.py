from __future__ import annotations
"""baw todo — task / thought / follow-up management from the CLI.

Subcommands:
  list     Show all items in the current session
  surface  Show current + carried-over follow-ups from previous sessions
  add      Add a new item (default type=task)
  thought  Add a self-reflection (shortcut for add --type thought)
  followup Schedule an action for a future turn/session
  done     Mark an item complete
  cancel   Mark an item cancelled
  remove   Delete an item
  stats    Show counters per type
"""
import argparse
import sys
from pathlib import Path

from core.todo_state import TodoState
from cli import console
from rich.panel import Panel
from rich.table import Table

BAW_HOME = Path.home() / ".baw"
SESSION_ID = "default"  # CLI uses the shared default session


def _get_state() -> TodoState:
    return TodoState(data_dir=BAW_HOME, session_id=SESSION_ID)


def _print_table(items):
    if not items:
        console.print("[baw.muted]No items.[/baw.muted]")
        return
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Type", width=9)
    table.add_column("Status", width=12)
    table.add_column("Content")
    table.add_column("Updated", style="dim", width=20)
    for it in items:
        type_color = {"task": "white", "thought": "cyan", "followup": "yellow"}[it.type]
        status_color = {
            "pending": "dim",
            "in_progress": "bold yellow",
            "completed": "green",
            "cancelled": "red",
        }[it.status]
        table.add_row(
            it.id[-6:],
            f"[{type_color}]{it.type}[/{type_color}]",
            f"[{status_color}]{it.status}[/{status_color}]",
            it.content,
            it.updated_at,
        )
    console.print(table)


def cmd_list(args):
    st = _get_state()
    items = st.list(active_only=args.active_only, type=args.type)
    title = f"📋 Todos — session {SESSION_ID}"
    if args.active_only:
        title += " (active only)"
    if args.type:
        title += f" — type={args.type}"
    console.print(Panel(_format_stats(st) + "\n" + _format_items(items),
                        title=title, border_style="magenta"))


def cmd_surface(args):
    st = _get_state()
    local = st.list(active_only=True)
    carried = st.load_pending_followups()
    if not local and not carried:
        console.print(Panel("✅ No pending items — clean slate.",
                            title="📋 Todo surface", border_style="green"))
        return
    parts = []
    if carried:
        parts.append("[bold yellow]📌 Carried over from previous sessions:[/bold yellow]")
        for it in carried:
            tag = f" [dim](from {it.session_id})[/dim]" if it.session_id else ""
            parts.append(f"  📌 [dim][{it.id[-6:]}][/dim]{tag} {it.content}")
    if local:
        if parts:
            parts.append("")
        parts.append(f"[bold magenta]📋 This session ({SESSION_ID}):[/bold magenta]")
        parts.append(_format_stats(st))
        parts.append(_format_items(local))
    console.print(Panel("\n".join(parts), title="📋 Todo surface", border_style="magenta"))


def cmd_add(args):
    st = _get_state()
    it = st.add(content=args.content, type=args.type, note=args.note or "")
    console.print(f"[green]Added[/green] [{it.type}] [{it.id[-6:]}] {it.content}")


def cmd_thought(args):
    st = _get_state()
    it = st.add(content=args.content, type="thought", note=args.note or "")
    console.print(f"[cyan]💭 captured[/cyan] [{it.id[-6:]}] {it.content}")


def cmd_followup(args):
    st = _get_state()
    it = st.add(content=args.content, type="followup", note=args.note or "")
    console.print(f"[yellow]📌 follow-up scheduled[/yellow] [{it.id[-6:]}] {it.content}")


def cmd_done(args):
    st = _get_state()
    full = _resolve(st, args.item_id)
    if not full:
        console.print(f"[red]Error: id '{args.item_id}' not found[/red]")
        sys.exit(1)
    it = st.complete(full)
    if not it:
        console.print(f"[red]Error: id '{args.item_id}' not found[/red]")
        sys.exit(1)
    console.print(f"[green]✅ done[/green] [{it.id[-6:]}] {it.content}")


def cmd_cancel(args):
    st = _get_state()
    full = _resolve(st, args.item_id)
    if not full:
        console.print(f"[red]Error: id '{args.item_id}' not found[/red]")
        sys.exit(1)
    it = st.cancel(full)
    if not it:
        console.print(f"[red]Error: id '{args.item_id}' not found[/red]")
        sys.exit(1)
    console.print(f"[red]❌ cancelled[/red] [{it.id[-6:]}] {it.content}")


def cmd_remove(args):
    st = _get_state()
    full = _resolve(st, args.item_id)
    if not full:
        console.print(f"[red]Error: id '{args.item_id}' not found[/red]")
        sys.exit(1)
    if st.remove(full):
        console.print(f"[dim]🗑 removed[/dim] [{full[-6:]}]")
    else:
        console.print(f"[red]Error: remove failed[/red]")


def cmd_stats(args):
    st = _get_state()
    console.print(Panel(_format_stats(st), title="📊 Todo stats", border_style="magenta"))


# ── helpers ───────────────────────────────────────────────────

def _resolve(st: TodoState, short_or_full: str) -> str | None:
    for it in st.list():
        if it.id == short_or_full or it.id.endswith(short_or_full):
            return it.id
    return None


def _format_items(items) -> str:
    icons = {
        ("task", "pending"): "⬜", ("task", "in_progress"): "🔄",
        ("task", "completed"): "✅", ("task", "cancelled"): "❌",
        ("thought", "pending"): "💭", ("thought", "in_progress"): "💭",
        ("thought", "completed"): "💭", ("thought", "cancelled"): "❌",
        ("followup", "pending"): "📌", ("followup", "in_progress"): "📌",
        ("followup", "completed"): "✅", ("followup", "cancelled"): "❌",
    }
    lines = []
    for it in items:
        icon = icons.get((it.type, it.status), "❓")
        type_tag = "" if it.type == "task" else f" [{it.type}]"
        note = f" — {it.note}" if it.note else ""
        lines.append(f"{icon} [{it.id[-6:]}]{type_tag} {it.content}{note}")
    return "\n".join(lines) if lines else "[dim](none)[/dim]"


def _format_stats(st: TodoState) -> str:
    s = st.stats()
    return (
        f"📊 task pending={s['task']['pending']} in_progress={s['task']['in_progress']} "
        f"completed={s['task']['completed']} cancelled={s['task']['cancelled']}\n"
        f"💭 thought pending={s['thought']['pending']} (always visible)\n"
        f"📌 followup pending={s['followup']['pending']} in_progress={s['followup']['in_progress']} "
        f"completed={s['followup']['completed']}"
    )


# ── arg parser ────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="baw todo",
                                description="Manage persistent todos, thoughts, and follow-ups.")
    sub = p.add_subparsers(dest="subcommand", required=True)

    sp_list = sub.add_parser("list", help="List todos in the current session")
    sp_list.add_argument("--active-only", action="store_true")
    sp_list.add_argument("--type", choices=["task", "thought", "followup"])
    sp_list.set_defaults(func=cmd_list)

    sp_surf = sub.add_parser("surface", help="Show this session + all carried-over follow-ups")
    sp_surf.set_defaults(func=cmd_surface)

    sp_add = sub.add_parser("add", help="Add a new task (default) or typed item")
    sp_add.add_argument("content")
    sp_add.add_argument("--type", choices=["task", "thought", "followup"], default="task")
    sp_add.add_argument("--note", default="")
    sp_add.set_defaults(func=cmd_add)

    sp_th = sub.add_parser("thought", help="Capture a self-reflection")
    sp_th.add_argument("content")
    sp_th.add_argument("--note", default="")
    sp_th.set_defaults(func=cmd_thought)

    sp_fu = sub.add_parser("followup", help="Schedule an action for a future session")
    sp_fu.add_argument("content")
    sp_fu.add_argument("--note", default="")
    sp_fu.set_defaults(func=cmd_followup)

    sp_done = sub.add_parser("done", help="Mark complete")
    sp_done.add_argument("item_id")
    sp_done.set_defaults(func=cmd_done)

    sp_can = sub.add_parser("cancel", help="Mark cancelled")
    sp_can.add_argument("item_id")
    sp_can.set_defaults(func=cmd_cancel)

    sp_rm = sub.add_parser("remove", help="Delete the item")
    sp_rm.add_argument("item_id")
    sp_rm.set_defaults(func=cmd_remove)

    sp_st = sub.add_parser("stats", help="Show counters per type")
    sp_st.set_defaults(func=cmd_stats)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
