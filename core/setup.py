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


# ── OK/Warn helpers ──

def _ok(text: str):
    print(f"  {C.GREEN}✓{C.RESET} {text}")


def _warn(text: str):
    print(f"  {C.YELLOW}⚠{C.RESET} {text}")


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


# ── Interactive Setup Wizard (comprehensive first-time install) ──

def _print_logo():
    logo = r"""
{C.BOLD}{C.GREEN}██╗    ██╗ {C.CYAN}██████╗{C.RESET}
{C.BOLD}{C.GREEN}██║    ██║ {C.CYAN}██╔══██╗{C.RESET}
{C.BOLD}{C.GREEN}██║ █╗ ██║ {C.CYAN}██████╔╝{C.RESET}
{C.BOLD}{C.GREEN}██║███╗██║ {C.CYAN}██╔══██╗{C.RESET}
{C.BOLD}{C.GREEN}╚███╔███╔╝ {C.CYAN}██████╔╝{C.RESET}
 {C.BOLD}{C.GREEN}╚══╝╚══╝  {C.CYAN}╚═════╝{C.RESET}
    """
    formatted = ""
    for line in logo.strip().split("\n"):
        formatted += line.format(**globals()) + "\n"
    print(formatted)
    print(f"  {C.DIM}Black And White — Agent Platform v1.0.0{C.RESET}")
    w, _ = shutil.get_terminal_size()
    print(f"  {C.DIM}{'─' * min(w-2, 40)}{C.RESET}")
    print()


def cmd_setup(data_dir: Path):
    cfg = load_config(data_dir)
    is_first_run = not cfg.get("model") and not cfg.get("providers")

    print()  # Clear space
    _print_logo()
    _print_header("Setup Wizard")
    if is_first_run:
        _print_note(f"Welcome! Let's get BAW running. First-time setup at {data_dir}")
        _print_note(f"Press Enter to accept defaults, type your own value to change.")
    else:
        _print_note("Updating existing configuration. Press Enter to keep current values.")
    print()

    # ── 1. Platform (Telegram/Discord/etc) ──
    _print_section("Messaging Platform")
    token = os.environ.get("BAW_TELEGRAM_TOKEN", "")
    if not token:
        token = input(f"  {C.CYAN}?{C.RESET} Telegram Bot Token (or press Enter to skip): ").strip()
    if token:
        cfg.setdefault("telegram", {})["token"] = token
        _ok("Telegram token set")
    else:
        _print_note("No Telegram token — BAW runs CLI-only. Add token later with: baw --cfg set telegram.token <token>")

    # ── 2. Model ──
    _print_section("Default Model")
    _print_note("Main model for chat + tools. Step 3.7 Flash is recommended.")
    current_model = cfg.get("model", {}).get("default", "step-3.7-flash")
    model_id = _input("Default model ID", default=current_model)
    cfg.setdefault("model", {})["default"] = model_id
    _print_note("Fallback model (used when main fails):")
    current_fb = cfg.get("model", {}).get("fallback", "deepseek-v4-flash")
    fb_id = _input("Fallback model ID", default=current_fb)
    if fb_id:
        cfg.setdefault("model", {})["fallback"] = fb_id

    # ── 3. API Keys ──
    _print_section("API Keys")
    env_path = data_dir / ".env"
    existing_env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing_env[k.strip()] = v.strip()

    _print_note("Enter API keys one by one. Leave blank to skip.")
    providers_prompt = [
        ("STEPFUN_API_KEY", "Stepfun (chat models + TTS/ASR)"),
        ("MINIMAX_API_KEY", "MiniMax (fallback chat + TTS + vision)"),
        ("DEEPSEEK_API_KEY", "DeepSeek (fast cheap model)"),
        ("OPENAI_API_KEY", "OpenAI (GPT-4o, DALL-E)"),
        ("ANTHROPIC_API_KEY", "Anthropic (Claude models)"),
        ("GEMINI_API_KEY", "Google (Gemini models)"),
        ("AGNES_API_KEY", "Agnes AI (free image/video gen)"),
    ]
    new_env = {}
    for env_key, label in providers_prompt:
        current_val = existing_env.get(env_key, "")
        hint = f" ({current_val[:8]}...)" if current_val and len(current_val) > 8 else ""
        val = input(f"  {C.CYAN}?{C.RESET} {label} ({env_key}){hint}: ").strip()
        if val:
            new_env[env_key] = val
        elif current_val and not val:
            # Keep existing
            pass

    # Write .env
    if new_env:
        env_lines = []
        if env_path.exists():
            env_lines = env_path.read_text().splitlines()
            # Remove old entries we're replacing
            env_lines = [l for l in env_lines if not any(l.startswith(k + "=") for k in new_env)]
        for k, v in new_env.items():
            env_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(env_lines) + "\n")
        _print_note(f"Updated {env_path} ({len(new_env)} key(s))")

    # Detect which keys we have (existing + new)
    all_keys = {**existing_env, **new_env}

    # ── 4. Providers (auto-configure based on API keys) ──
    _print_section("Providers")
    providers = cfg.setdefault("providers", {})

    # Stepfun
    if "STEPFUN_API_KEY" in all_keys:
        if "stepfun" not in providers:
            providers["stepfun"] = {
                "api_key_env": "STEPFUN_API_KEY",
                "base_url": "https://api.stepfun.ai/v1",
                "models": [
                    {"id": "step-3.7-flash", "capabilities": ["chat", "vision"], "context_window": 65536},
                    {"id": "step-3.5-flash", "capabilities": ["chat"], "context_window": 65536},
                    {"id": "stepaudio-2.5-tts", "capabilities": ["tts"], "context_window": 4096},
                    {"id": "stepaudio-2.5-asr", "capabilities": ["stt"], "context_window": 4096},
                    {"id": "step-tts-2", "capabilities": ["tts"], "context_window": 4096},
                ],
            }
            _ok("Stepfun provider configured")

    # MiniMax
    if "MINIMAX_API_KEY" in all_keys:
        if "minimax" not in providers:
            providers["minimax"] = {
                "api_key_env": "MINIMAX_API_KEY",
                "base_url": "https://api.minimax.io/v1",
                "models": [
                    {"id": "MiniMax-M3", "capabilities": ["chat", "vision", "tts"], "context_window": 1048576},
                    {"id": "MiniMax-M2.5", "capabilities": ["chat"], "context_window": 1048576},
                ],
            }
            _ok("MiniMax provider configured")

    # DeepSeek
    if "DEEPSEEK_API_KEY" in all_keys:
        if "deepseek" not in providers:
            providers["deepseek"] = {
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": "https://api.deepseek.com/v1",
                "models": [
                    {"id": "deepseek-v4-flash", "capabilities": ["chat"], "context_window": 65536,
                     "cost_per_1m_input": 0.30, "cost_per_1m_output": 1.20},
                ],
            }
            _ok("DeepSeek provider configured")

    # OpenAI
    if "OPENAI_API_KEY" in all_keys:
        if "openai" not in providers:
            providers["openai"] = {
                "api_key_env": "OPENAI_API_KEY",
                "base_url": "https://api.openai.com/v1",
                "models": [
                    {"id": "gpt-4o", "capabilities": ["chat"], "context_window": 128000},
                    {"id": "dall-e-3", "capabilities": ["image_generation"], "context_window": 4096},
                ],
            }
            _ok("OpenAI provider configured")

    if not providers:
        _warn("No API keys set — BAW won't work until you configure at least one provider")
        _print_note("Run 'baw --setup' again after getting API keys")

    # ── 5. Capabilities ──
    _print_section("Capabilities")
    caps = cfg.setdefault("capabilities", {})
    changed_caps = False

    # Chat
    if not caps.get("chat", {}).get("model"):
        caps["chat"] = {"model": model_id}
        changed_caps = True

    # STT
    has_stt_key = any(k for k in all_keys if k in ("STEPFUN_API_KEY", "MINIMAX_API_KEY"))
    if has_stt_key and not caps.get("stt"):
        if "STEPFUN_API_KEY" in all_keys:
            caps["stt"] = {
                "method": "auto-asr",
                "model": "stepaudio-2.5-asr",
                "base_url": "https://api.stepfun.ai/v1",
                "api_key_env": "STEPFUN_API_KEY",
            }
            _ok("STT configured (Stepfun auto-asr)")
        elif "MINIMAX_API_KEY" in all_keys:
            caps["stt"] = {"method": "model", "model": "MiniMax-M3"}
            _ok("STT configured (MiniMax-M3)")
        changed_caps = True

    # TTS
    if not caps.get("tts"):
        if "STEPFUN_API_KEY" in all_keys:
            caps["tts"] = {"method": "model", "model": "stepaudio-2.5-tts", "voice": "Cantonese_GentleLady",
                          "config": {"api_model": "stepaudio-2.5-tts"}}
            _ok("TTS configured (Stepfun)")
        elif "MINIMAX_API_KEY" in all_keys:
            caps["tts"] = {"model": "MiniMax-M3", "voice": "Cantonese_GentleLady",
                          "config": {"api_model": "speech-2.8-hd"}}
            _ok("TTS configured (MiniMax)")
        changed_caps = True

    # Vision
    if not caps.get("vision"):
        if "MINIMAX_API_KEY" in all_keys:
            caps["vision"] = {"model": "MiniMax-M3"}
            _ok("Vision configured (MiniMax-M3)")
        elif "STEPFUN_API_KEY" in all_keys:
            caps["vision"] = {"model": "step-3.7-flash"}
            _ok("Vision configured (step-3.7-flash)")

    if changed_caps:
        _print_note("Capabilities auto-configured. Fine-tune with: baw --cfg set capabilities.<name>.<key> <value>")

    # ── 6. Mode / Tone / Adversarial / Fact Check ──
    _print_section("Behavior")
    current = cfg.get("mode", "tight")
    _print_note("Execution mode: quick = no court/plan, hybrid = plan only, tight = full court")
    mode = _input("Mode", default=current)
    while mode not in ("quick", "hybrid", "tight"):
        print(f"{C.RED}  Must be: quick, hybrid, or tight{C.RESET}")
        mode = _input("Mode", default=current)
    cfg["mode"] = mode

    current_tone = cfg.get("tone", {}).get("default", "casual")
    tone = _input("Tone (casual/business/teaching/client-doc/ot-rt/stepwise)", default=current_tone)
    cfg.setdefault("tone", {})["default"] = tone

    current_adv = str(cfg.get("adversarial", {}).get("enabled", True)).lower()
    adv = _input("Enable Angel/Devil court? (true/false)", default=current_adv)
    cfg.setdefault("adversarial", {})["enabled"] = adv == "true"

    current_fc = cfg.get("fact_check", {}).get("mode", "normal")
    fc = _input("Fact check mode (off/normal/strict)", default=current_fc)
    while fc not in ("off", "normal", "strict"):
        print(f"{C.RED}  Must be: off, normal, or strict{C.RESET}")
        fc = _input("Fact check mode", default=current_fc)
    cfg.setdefault("fact_check", {})["mode"] = fc

    # ── Save ──
    save_config(data_dir, cfg)
    print()
    _print_header("Setup Complete")
    _print_note("Next steps:")
    _print_note("  1. Run  'baw --doctor'                  — verify everything works")
    _print_note("  2. Run  'baw --version'                  — check build info")
    _print_note("  3. Run  'baw \"hello\"'                    — test the agent")
    if token:
        _print_note("  4. Send /start to your Telegram bot — test messaging")
    _print_note("  5. Run  'baw --update'                   — pull latest + rebuild")
    print()


# ── New config subcommands ──

def cmd_config_edit(data_dir: Path):
    """Open config.yaml in editor."""
    path = data_dir / "config.yaml"
    if not path.exists():
        print(f"{C.RED}  ✗ config.yaml not found{C.RESET}")
        return
    editor = os.environ.get("EDITOR", "")
    if not editor:
        for e in ["nano", "vim", "vi"]:
            if shutil.which(e):
                editor = e
                break
    if not editor:
        print(f"{C.RED}  ✗ No editor found. Set $EDITOR or install nano/vim.{C.RESET}")
        return
    print(f"  {C.YELLOW}⟳{C.RESET} Opening {path} with {editor}...")
    os.system(f"{editor} {path}")


def cmd_config_path(data_dir: Path):
    """Print config.yaml path."""
    path = data_dir / "config.yaml"
    print(f"  Config: {path}")
    if path.exists():
        print(f"  Size: {path.stat().st_size:,} bytes")


def cmd_config_env_path(data_dir: Path):
    """Print .env path."""
    path = data_dir / ".env"
    print(f"  Env file: {path}")
    if path.exists():
        print(f"  Size: {path.stat().st_size:,} bytes ({sum(1 for l in path.read_text().splitlines() if '=' in l)} keys)")


def cmd_config_check(data_dir: Path):
    """Validate config for required sections."""
    cfg = load_config(data_dir)
    issues = []

    checks = [
        ("model.default", cfg.get("model", {}).get("default", "")),
        ("providers (at least one)", bool(cfg.get("providers"))),
        ("capabilities.chat.model", bool(cfg.get("capabilities", {}).get("chat", {}).get("model"))),
        ("capabilities.stt", bool(cfg.get("capabilities", {}).get("stt"))),
        ("capabilities.tts", bool(cfg.get("capabilities", {}).get("tts"))),
        ("tone.default", bool(cfg.get("tone", {}).get("default"))),
    ]

    for name, ok in checks:
        if ok:
            val = ok if isinstance(ok, str) else "ok"
            print(f"  {C.GREEN}✓{C.RESET} {name}: {val}")
        else:
            print(f"  {C.YELLOW}⚠{C.RESET} {name}: missing")
            issues.append(name)

    if issues:
        print(f"\n  {C.YELLOW}Tip:{C.RESET} Run 'baw --setup' to configure interactively")