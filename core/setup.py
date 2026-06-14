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
    print(f"  {C.DIM}Black And White — Agent Platform v0.20.1{C.RESET}")
    w, _ = shutil.get_terminal_size()
    print(f"  {C.DIM}{'─' * min(w-2, 40)}{C.RESET}")
    print()


# ── API Key validation ───────────────────────────────────────────────

def _validate_api_key(provider: str, base_url: str, api_key: str, model: str = "") -> tuple[bool, str]:
    """Send a test request to validate an API key. Returns (ok, message)."""
    import requests
    test_models = {
        "deepseek": "deepseek-v4-flash",
        "minimax": "MiniMax-M3",
        "openai": "gpt-4o-mini",
    }
    test_model = model or test_models.get(provider, "")
    if not test_model:
        return True, "Skipping validation (unknown provider)"

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": test_model,
        "messages": [{"role": "user", "content": "Say 'pong' and nothing else."}],
        "max_tokens": 5,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        if r.status_code == 200:
            return True, "Key valid — test request succeeded"
        elif r.status_code == 401:
            return False, "Invalid key (401 Unauthorized)"
        elif r.status_code == 402:
            return False, "Quota exceeded (402 Payment Required)"
        else:
            return False, f"HTTP {r.status_code}: {r.text[:100]}"
    except requests.exceptions.Timeout:
        return False, "Timeout — network or endpoint issue"
    except Exception as e:
        return False, f"Request failed: {str(e)[:100]}"


# ── Plan explanations ──────────────────────────────────────────────

_PLAN_GUIDES: dict[str, str] = {
    "stepfun": (
        "\n    Stepfun plans:\n"
        "      standard   — api.stepfun.ai/v1 (general API access)\n"
        "      step-plan  — api.stepfun.ai/step_plan/v1 (step-plan pricing)\n"
        "      china      — api.stepfun.com/v1 (mainland China endpoint)"
    ),
    "minimax": (
        "\n    MiniMax plans:\n"
        "      standard      — pay-per-use\n"
        "      subscription  — monthly subscription (better rates)"
    ),
    "moonshot": (
        "\n    Moonshot plans:\n"
        "      standard   — general API access\n"
        "      code-plan  — optimised for code generation"
    ),
}


def _explain_plan(provider: str) -> str:
    return _PLAN_GUIDES.get(provider, "")


def cmd_setup(data_dir: Path):
    cfg = load_config(data_dir)
    is_first_run = not cfg.get("model") and not cfg.get("providers")

    print()
    _print_logo()
    _print_header("Setup Wizard")
    if is_first_run:
        _print_note("Welcome! Let's get BAW running.")
        _print_note("Press Enter to accept defaults, type your own value to change.")
    else:
        _print_note("Updating existing configuration. Press Enter to keep current values.")
    print()

    # ── 1. Default Model ──
    _print_section("1. Default Model")
    _print_note("This is the main model BAW uses for chat and tool execution.")
    _print_note("Recommendations:")
    _print_note("  • deepseek-v4-flash  — fast, cheap, good for most tasks")
    _print_note("  • MiniMax-M3         — multimodal (vision + TTS + chat)")
    _print_note("  • claude-sonnet-4    — highest quality, most expensive")
    current_model = cfg.get("model", {}).get("default", "deepseek-v4-flash")
    model_id = _input("Default model ID", default=current_model)
    cfg.setdefault("model", {})["default"] = model_id

    _print_note("Fallback model (used when main model fails — must be DIFFERENT provider):")
    current_fb = cfg.get("model", {}).get("fallback", "MiniMax-M3")
    fb_id = _input("Fallback model ID", default=current_fb)
    if fb_id:
        cfg.setdefault("model", {})["fallback"] = fb_id

    # ── 2. API Keys (with validation) ──
    _print_section("2. API Keys")
    _print_note("Enter API keys one by one. Leave blank to skip.")
    _print_note("Each key will be tested immediately — you'll know if it works.")
    env_path = data_dir / ".env"
    existing_env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing_env[k.strip()] = v.strip()

    providers_prompt = [
        ("DEEPSEEK_API_KEY", "DeepSeek", "deepseek", "https://api.deepseek.com/v1"),
        ("MINIMAX_API_KEY", "MiniMax", "minimax", "https://api.minimax.io/v1"),
        ("OPENAI_API_KEY", "OpenAI", "openai", "https://api.openai.com/v1"),
        ("STEPFUN_API_KEY", "Stepfun", "stepfun", ""),
        ("MOONSHOT_API_KEY", "Moonshot/Kimi", "moonshot", "https://api.moonshot.ai/v1"),
        ("ANTHROPIC_API_KEY", "Anthropic", "anthropic", "https://api.anthropic.com/v1"),
        ("GEMINI_API_KEY", "Google Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta"),
    ]
    new_env = {}
    plan_choices = {}
    validated_providers: set[str] = set()

    for env_key, label, provider_key, default_base in providers_prompt:
        current_val = existing_env.get(env_key, "")
        hint = f" ({current_val[:8]}...)" if current_val and len(current_val) > 8 else ""
        val = input(f"  {C.CYAN}?{C.RESET} {label} ({env_key}){hint}: ").strip()
        if val:
            # Determine base URL
            base_url = default_base
            if provider_key == "stepfun":
                print(f"  {C.DIM}{_explain_plan('stepfun')}{C.RESET}")
                plan = _input("  Plan type", default="standard")
                plan_choices[env_key] = plan.lower().replace("-", "_")
                if plan.lower() in ("step_plan", "step-plan"):
                    base_url = "https://api.stepfun.ai/step_plan/v1"
                elif plan.lower() == "china":
                    base_url = "https://api.stepfun.com/v1"
                else:
                    base_url = "https://api.stepfun.ai/v1"
            elif provider_key == "minimax":
                print(f"  {C.DIM}{_explain_plan('minimax')}{C.RESET}")
                plan = _input("  Plan type", default="standard")
                plan_choices[env_key] = plan.lower()
            elif provider_key == "moonshot":
                print(f"  {C.DIM}{_explain_plan('moonshot')}{C.RESET}")
                plan = _input("  Plan type", default="standard")
                plan_choices[env_key] = plan.lower().replace("-", "_")

            # Validate key
            if base_url:
                print(f"  {C.DIM}⏳ Testing key...{C.RESET}", end="", flush=True)
                ok, msg = _validate_api_key(provider_key, base_url, val)
                print(f"\r  {' ' * 20}\r", end="")
                if ok:
                    _ok(msg)
                    validated_providers.add(provider_key)
                else:
                    _warn(msg)
                    if not _confirm("  Use this key anyway?", default=False):
                        continue
            new_env[env_key] = val
        elif current_val and not val:
            pass

    # Write .env
    if new_env:
        env_lines = []
        if env_path.exists():
            env_lines = env_path.read_text().splitlines()
            env_lines = [l for l in env_lines if not any(l.startswith(k + "=") for k in new_env)]
        for k, v in new_env.items():
            env_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(env_lines) + "\n")
        _ok(f"Saved {len(new_env)} key(s) to {env_path}")

    all_keys = {**existing_env, **new_env}

    # ── 3. Providers (auto-configure) ──
    _print_section("3. Providers")
    providers = cfg.setdefault("providers", {})

    if "DEEPSEEK_API_KEY" in all_keys and "deepseek" not in providers:
        providers["deepseek"] = {
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com/v1",
            "models": [
                {"id": "deepseek-v4-flash", "capabilities": ["chat"], "context_window": 65536,
                 "cost_per_1m_input": 0.30, "cost_per_1m_output": 1.20},
                {"id": "deepseek-v4-pro", "capabilities": ["chat"], "context_window": 65536},
            ],
        }
        _ok("DeepSeek provider configured")

    if "MINIMAX_API_KEY" in all_keys and "minimax" not in providers:
        providers["minimax"] = {
            "api_key_env": "MINIMAX_API_KEY",
            "base_url": "https://api.minimax.io/v1",
            "models": [
                {"id": "MiniMax-M3", "capabilities": ["chat", "vision", "tts"], "context_window": 1048576},
                {"id": "MiniMax-M2.5", "capabilities": ["chat"], "context_window": 1048576},
            ],
        }
        _ok("MiniMax provider configured")

    if "OPENAI_API_KEY" in all_keys and "openai" not in providers:
        providers["openai"] = {
            "api_key_env": "OPENAI_API_KEY",
            "base_url": "https://api.openai.com/v1",
            "models": [
                {"id": "gpt-4o", "capabilities": ["chat"], "context_window": 128000},
                {"id": "gpt-4o-mini", "capabilities": ["chat"], "context_window": 128000},
            ],
        }
        _ok("OpenAI provider configured")

    if not providers:
        _warn("No API keys configured — BAW needs at least one provider to work")
        _print_note("Run 'baw --setup' again after getting API keys")

    # ── 4. Capabilities ──
    _print_section("4. Capabilities")
    caps = cfg.setdefault("capabilities", {})
    changed_caps = False

    if not caps.get("chat", {}).get("model"):
        caps["chat"] = {"model": model_id}
        changed_caps = True

    has_minimax = "MINIMAX_API_KEY" in all_keys
    has_stepfun = "STEPFUN_API_KEY" in all_keys

    if not caps.get("stt"):
        if has_stepfun:
            plan = plan_choices.get("STEPFUN_API_KEY", "standard")
            stt_base = "https://api.stepfun.ai/step_plan/v1" if plan == "step_plan" else "https://api.stepfun.ai/v1"
            caps["stt"] = {
                "method": "auto-asr",
                "model": "stepaudio-2.5-asr",
                "base_url": stt_base,
                "api_key_env": "STEPFUN_API_KEY",
            }
            _ok("STT configured (Stepfun)")
        elif has_minimax:
            caps["stt"] = {"method": "model", "model": "MiniMax-M3"}
            _ok("STT configured (MiniMax)")
        changed_caps = True

    if not caps.get("tts"):
        if has_stepfun:
            plan = plan_choices.get("STEPFUN_API_KEY", "standard")
            tts_base = "https://api.stepfun.ai/step_plan/v1" if plan == "step_plan" else "https://api.stepfun.ai/v1"
            caps["tts"] = {"method": "model", "model": "stepaudio-2.5-tts", "voice": "Cantonese_GentleLady",
                          "config": {"api_model": "stepaudio-2.5-tts", "base_url": tts_base}}
            _ok("TTS configured (Stepfun)")
        elif has_minimax:
            caps["tts"] = {"model": "MiniMax-M3", "voice": "Cantonese_GentleLady",
                          "config": {"api_model": "speech-2.8-hd"}}
            _ok("TTS configured (MiniMax)")
        changed_caps = True

    if not caps.get("vision"):
        if has_minimax:
            caps["vision"] = {"model": "MiniMax-M3"}
            _ok("Vision configured (MiniMax-M3)")
        elif has_stepfun:
            caps["vision"] = {"model": "step-3.7-flash"}
            _ok("Vision configured (Stepfun)")

    if changed_caps:
        _print_note("Capabilities auto-configured. Adjust later: baw --cfg set capabilities.<name>.<key> <value>")

    # ── 5. Behaviour ──
    _print_section("5. Behaviour")
    _print_note("Execution mode determines how thoroughly BAW checks its work:")
    _print_note("  quick   — fastest, no court/plan, direct execution")
    _print_note("  hybrid  — balanced, plan + execute, light verification")
    _print_note("  tight   — most thorough, full court + plan + per-step verify (default)")
    current = cfg.get("mode", "tight")
    mode = _input("Mode", default=current)
    while mode not in ("quick", "hybrid", "tight"):
        print(f"{C.RED}  Must be: quick, hybrid, or tight{C.RESET}")
        mode = _input("Mode", default=current)
    cfg["mode"] = mode

    _print_note("Tone determines how BAW responds:")
    _print_note("  casual     — friendly, conversational")
    _print_note("  business   — professional, concise")
    _print_note("  teaching   — explanatory, step-by-step")
    _print_note("  client-doc — zero comments, direct artifact output")
    _print_note("  ot-rt      — rapid execution, report after completion")
    _print_note("  stepwise   — pause for confirmation at each step")
    current_tone = cfg.get("tone", {}).get("default", "casual")
    tone = _input("Tone", default=current_tone)
    cfg.setdefault("tone", {})["default"] = tone

    current_adv = str(cfg.get("adversarial", {}).get("enabled", True)).lower()
    _print_note("Angel/Devil court: Devil argues against every action before Angel executes.")
    adv = _input("Enable court? (true/false)", default=current_adv)
    cfg.setdefault("adversarial", {})["enabled"] = adv == "true"

    current_fc = cfg.get("fact_check", {}).get("mode", "normal")
    _print_note("Fact check: verifies claims against web search before reporting.")
    fc = _input("Fact check (off/normal/strict)", default=current_fc)
    while fc not in ("off", "normal", "strict"):
        print(f"{C.RED}  Must be: off, normal, or strict{C.RESET}")
        fc = _input("Fact check mode", default=current_fc)
    cfg.setdefault("fact_check", {})["mode"] = fc

    # ── 6. Messaging (optional) ──
    _print_section("6. Messaging Platform (optional)")
    _print_note("Connect BAW to Telegram, Discord, etc. Skip this for CLI-only use.")
    if _confirm("Configure Telegram bot?", default=False):
        token = os.environ.get("BAW_TELEGRAM_TOKEN", "")
        if not token:
            token = input(f"  {C.CYAN}?{C.RESET} Telegram Bot Token: ").strip()
        if token:
            cfg.setdefault("telegram", {})["token"] = token
            _ok("Telegram configured")
        else:
            _print_note("No token provided — skip Telegram for now")
    else:
        _print_note("Skipped. Add later: baw --cfg set telegram.token <token>")

    # ── Save ──
    save_config(data_dir, cfg)
    print()
    _print_header("Setup Complete")
    _ok(f"Config saved to {data_dir / 'config.yaml'}")
    if new_env:
        _ok(f"API keys saved to {env_path}")
    print()
    _print_note("Next steps:")
    _print_note("  1. Run 'baw --doctor'     — verify everything works")
    _print_note("  2. Run 'baw \"hello\"'      — test the agent")
    _print_note("  3. Run 'baw --cfg list'   — review all settings")
    if cfg.get("telegram", {}).get("token"):
        _print_note("  4. Send /start to your bot — test Telegram")
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