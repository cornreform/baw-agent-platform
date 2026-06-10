"""baw skill — list/install/manage BAW skills."""
import os
import subprocess
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box
from cli import console

BAW_HOME = Path.home() / ".baw"


def cmd_skill(subcommand: str | None = None, args: list[str] | None = None):
    if subcommand is None or subcommand == "list":
        _skill_list()
    elif subcommand == "install":
        _skill_install(args)
    elif subcommand == "remove":
        _skill_remove(args)
    else:
        console.print(f"[baw.error]Unknown:[/baw.error] {subcommand}")
        console.print("[baw.dim]Usage: baw skill [list|install <url>|remove <name>][/baw.dim]")


def _skill_list():
    skills_dir = BAW_HOME / "skills"
    if not skills_dir.exists():
        console.print("[baw.dim]No skills directory found.[/baw.dim]")
        return

    table = Table(
        title="[baw.gold]📦  BAW Skills[/baw.gold]",
        border_style="baw.accent",
        box=box.SIMPLE_HEAVY,
    )
    table.add_column("Name", style="baw.cmd", width=36)
    table.add_column("Type", style="baw.muted", width=12)
    table.add_column("Size", style="baw.dim", width=10, justify="right")
    table.add_column("Description", style="baw.dim")

    found = False
    for ext, typ in [(".yaml", "YAML"), (".yml", "YAML"), (".md", "Markdown")]:
        for f in sorted(skills_dir.glob(f"*{ext}")):
            found = True
            size = f.stat().st_size
            size_str = f"{size}B" if size < 1024 else f"{size/1024:.1f}KB"
            # Try to extract first line as description
            desc = ""
            try:
                first = f.read_text().splitlines()[0]
                if first.startswith("#"):
                    desc = first.lstrip("# ")[:40]
                elif first.startswith("---"):
                    # YAML frontmatter — skip to description
                    lines = f.read_text().splitlines()
                    for l in lines[1:]:
                        if l.startswith("description:"):
                            desc = l.split(":", 1)[1].strip()[:40]
                            break
            except Exception:
                pass
            table.add_row(f.name, typ, size_str, desc)

    if not found:
        console.print("[baw.dim]No skills installed.[/baw.dim]")
        console.print("[baw.dim]Install: baw skill install <url or repo/path>[/baw.dim]")
    else:
        console.print(table)


def _skill_install(args: list[str] | None = None):
    if not args:
        console.print("[baw.error]Usage:[/baw.error] baw skill install <url or repo/path>")
        console.print("[baw.dim]e.g. baw skill install https://github.com/user/repo/blob/main/skills/my-skill.yaml[/baw.dim]")
        return

    source = args[0]
    skills_dir = BAW_HOME / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Determine source type: URL, local path, or repo shorthand
    if source.startswith("http://") or source.startswith("https://"):
        _skill_install_url(source, skills_dir)
    elif os.path.exists(os.path.expanduser(source)):
        _skill_install_local(source, skills_dir)
    else:
        console.print(f"[baw.error]Cannot resolve:[/baw.error] {source}")
        console.print("[baw.dim]Provide a URL or local file path.[/baw.dim]")


def _skill_install_url(url: str, skills_dir: Path):
    import urllib.request

    console.print(f"[baw.muted]Downloading {url}...[/baw.muted]")
    try:
        filename = url.split("/")[-1] or "skill.yaml"
        dest = skills_dir / filename
        urllib.request.urlretrieve(url, str(dest))
        size = dest.stat().st_size
        console.print(f"[baw.success]✓ Installed {filename} ({size}B)[/baw.success]")
    except Exception as e:
        console.print(f"[baw.error]✗ Download failed:[/baw.error] {e}")


def _skill_install_local(path: str, skills_dir: Path):
    import shutil
    src = Path(os.path.expanduser(path))
    if not src.exists():
        console.print(f"[baw.error]File not found:[/baw.error] {path}")
        return
    dest = skills_dir / src.name
    shutil.copy2(str(src), str(dest))
    console.print(f"[baw.success]✓ Installed {src.name} ({src.stat().st_size}B)[/baw.success]")


def _skill_remove(args: list[str] | None = None):
    if not args:
        console.print("[baw.error]Usage:[/baw.error] baw skill remove <name>")
        return

    name = args[0]
    skills_dir = BAW_HOME / "skills"
    target = skills_dir / name

    if not target.exists():
        console.print(f"[baw.error]Skill not found:[/baw.error] {name}")
        return

    target.unlink()
    console.print(f"[baw.success]✓ Removed {name}[/baw.success]")
