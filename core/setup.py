"""BAW — Interactive CLI Setup & Config

Provides:
- `baw setup` — interactive guided configuration wizard
- `baw config list` — show current settings
- `baw config get <key>` — show one setting
- `baw config set <key> <value>` — change a setting
"""

from __future__ import annotations
import os, sys, yaml, json, shutil, readline, textwrap
from pathlib import Path
from typing import Optional
from datetime import datetime


# ── ANSI colors ──

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GREY = "\033[90m"


# ── Config file I/O ──

def load_config(data_dir: Path) -> dict:
    path = data_dir / "config.yaml"
    if path.exists():
        return yaml.safe_load(path.read_text()) or {}
    return {}


def save_config(data_dir: Path, config: dict):
    path = data_dir / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))


# ── Pretty print ──

def _print_header(title: str):
    w, _ = shutil.get_terminal_size()
    print(f"\n{C.BOLD}{C.CYAN}{'─' * w}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.CYAN}{'─' * w}{C.RESET}\n")


def _print_section(title: str):
    print(f"\n{C.BOLD}{C.YELLOW}◆ {title}{C.RESET}")


def _print_item(key: str, value: str, indent: int = 2):
    print(f"{' ' * indent}{C.GREEN}{key}{C.RESET}: {value}")


def _print_note(text: str):
    print(f"  {C.DIM}{C.ITALIC}{text}{C.RESET}")


def _input(prompt: str, default: str = "") -> str:
    """Prompt with color and optional default."""
    if default:
        full = f"{C.CYAN}?{C.RESET} {prompt} "
        full += f"{C.DIM}[{default}]{C.RESET} "
        val = input(full).strip()
        return val if val else default
    val = input(f"{C.CYAN}?{C.RESET} {prompt} ").strip()
    return val


def _confirm(prompt: str, default: bool = True) -> bool:
    suffix = f"{C.DIM}[Y/n]{C.RESET}" if default else f"{C.DIM}[y/N]{C.RESET}"
    val = input(f"{C.CYAN}?{C.RESET} {prompt} {suffix} ").strip().lower()
    if not val:
        return default
    return val in ("y", "yes", "Y")


# ── Config commands ──

def cmd_config_list(data_dir: Path):
    cfg = load_config(data_dir)
    _print_header("BAW Configuration")

    sections = {
        "Model": lambda c: (c.get("model", {}), [
            ("default", c.get("model", {}).get("default", "?")),
            ("fallback", c.get("model", {}).get("fallback", "?")),
        ]),
        "Mode": lambda c: ({"mode": c.get("mode", "tight")}, [
            ("mode", c.get("mode", "tight")),
        ]),
        "Tone": lambda c: ({"tone": c.get("tone", {})}, [
            ("default", c.get("tone", {}).get("default", "casual")),
        ]),
        "Fact Check": lambda c: ({"fact_check": c.get("fact_check", {})}, [
            ("mode", c.get("fact_check", {}).get("mode", "normal")),
        ]),
        "Adversarial": lambda c: ({"adversarial": c.get("adversarial", {})}, [
            ("enabled", str(c.get("adversarial", {}).get("enabled", True))),
        ]),
        "Verify": lambda c: ({"verify": c.get("verify", {})}, [
            ("enabled", str(c.get("verify", {}).get("enabled", False))),
        ]),
    }

    for section_name, fn in sections.items():
        _, items = fn(cfg)
        _print_section(section_name)
        for k, v in items:
            _print_item(k, v)

    # Show providers
    _print_section("Providers")
    providers = cfg.get("providers", {})
    for name, p in providers.items():
        models = [m["id"] for m in p.get("models", [])]
        _print_item(name, f"{', '.join(models)} ({p.get('protocol', 'openai-chat')})")

    print()


def cmd_config_get(data_dir: Path, key: str):
    cfg = load_config(data_dir)
    # Support dotted keys like "model.default"
    parts = key.split(".")
    val = cfg
    for p in parts:
        if isinstance(val, dict) and p in val:
            val = val[p]
        else:
            print(f"{C.RED}❌ Key '{key}' not found{C.RESET}")
            return
    print(f"{C.GREEN}{key}{C.RESET}: ", end="")
    if isinstance(val, (dict, list)):
        print(json.dumps(val, ensure_ascii=False, indent=2))
    else:
        print(val)


def cmd_config_set(data_dir: Path, key: str, value: str):
    cfg = load_config(data_dir)
    parts = key.split(".")
    target = cfg
    for p in parts[:-1]:
        if p not in target:
            target[p] = {}
        target = target[p]

    # Try to parse as JSON (numbers, booleans, null)
    try:
        parsed = json.loads(value)
        target[parts[-1]] = parsed
    except (json.JSONDecodeError, ValueError):
        target[parts[-1]] = value

    save_config(data_dir, cfg)
    print(f"{C.GREEN}✅ {key} = {value}{C.RESET}")


def cmd_config_help():
    _print_header("Config Keys")
    keys = [
        ("mode", "Execution mode: quick | hybrid | tight"),
        ("model.default", "Default LLM model"),
        ("model.fallback", "Fallback model"),
        ("tone.default", "Tone: casual | business | teaching | client-doc | ot-rt | stepwise"),
        ("adversarial.enabled", "Enable Angel/Devil court: true | false"),
        ("verify.enabled", "Enable per-step LLM verify: true | false"),
        ("fact_check.mode", "Fact check: off | normal | strict"),
    ]
    for key, desc in keys:
        print(f"  {C.GREEN}{key}{C.RESET}")
        print(f"    {C.DIM}{desc}{C.RESET}")
    print()


# ── Interactive Setup Wizard ──

def cmd_setup(data_dir: Path):
    cfg = load_config(data_dir)

    _print_header("BAW Setup Wizard")
    _print_note("This will guide you through the basic configuration.")
    _print_note("Press Enter to keep current values.")
    print()

    # ── Mode ──
    _print_section("Execution Mode")
    current = cfg.get("mode", "tight")
    _print_note("Choose how BAW executes tasks:")
    _print_note(f"  quick  = no court, no plan (fastest)")
    _print_note(f"  hybrid = plan only, no court (balanced)")
    _print_note(f"  tight  = full court + plan + verify (most thorough)")
    mode = _input("Execution mode", default=current)
    while mode not in ("quick", "hybrid", "tight"):
        print(f"{C.RED}  Must be: quick, hybrid, or tight{C.RESET}")
        mode = _input("Execution mode", default=current)
    cfg["mode"] = mode

    # ── Tone ──
    _print_section("Tone Profile")
    current_tone = cfg.get("tone", {}).get("default", "casual")
    _print_note("How should BAW speak?")
    _print_note(f"  casual     = 日常粵語")
    _print_note(f"  business   = 客戶文件 tone")
    _print_note(f"  teaching   = 教學文件")
    _print_note(f"  client-doc = Client facing")
    _print_note(f"  ot-rt      = 快速執行")
    _print_note(f"  stepwise   = 逐步執行")
    tone = _input("Tone", default=current_tone)
    while tone not in ("casual", "business", "teaching", "client-doc", "ot-rt", "stepwise"):
        print(f"{C.RED}  Must be one of the listed tones{C.RESET}")
        tone = _input("Tone", default=current_tone)
    cfg.setdefault("tone", {})["default"] = tone

    # ── Adversarial ──
    _print_section("Adversarial Court")
    current_adv = str(cfg.get("adversarial", {}).get("enabled", True)).lower()
    _print_note("Angel/Devil court checks goals before execution.")
    adv = _input("Enable adversarial court? (true/false)", default=current_adv)
    while adv not in ("true", "false"):
        print(f"{C.RED}  Must be true or false{C.RESET}")
        adv = _input("Enable adversarial court?", default=current_adv)
    cfg.setdefault("adversarial", {})["enabled"] = adv == "true"

    # ── Fact check ──
    _print_section("Fact Checking")
    current_fc = cfg.get("fact_check", {}).get("mode", "normal")
    _print_note("How strictly should BAW verify claims?")
    _print_note(f"  off    = no checking")
    _print_note(f"  normal = flag suspicious claims")
    _print_note(f"  strict = block unverifiable claims")
    fc = _input("Fact check mode", default=current_fc)
    while fc not in ("off", "normal", "strict"):
        print(f"{C.RED}  Must be: off, normal, or strict{C.RESET}")
        fc = _input("Fact check mode", default=current_fc)
    cfg.setdefault("fact_check", {})["mode"] = fc

    # ── Model ──
    _print_section("Default Model")
    current_model = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    _print_note("Available models depend on your providers.")
    _print_note("Set the default:")
    model_id = _input("Default model ID", default=current_model)
    cfg.setdefault("model", {})["default"] = model_id

    # ── Save ──
    save_config(data_dir, cfg)
    print()
    print(f"{C.GREEN}{'=' * 50}{C.RESET}")
    print(f"{C.GREEN}✅ Configuration saved!{C.RESET}")
    print(f"{C.GREEN}{'=' * 50}{C.RESET}")
    print()

    # Show summary
    cmd_config_list(data_dir)
