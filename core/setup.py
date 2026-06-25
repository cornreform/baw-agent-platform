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
    """Write config dict to config.yaml with managed key protection."""
    from core.managed_config import strip_managed_keys
    config = strip_managed_keys(config)
    path = data_dir / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(config, default_flow_style=False, allow_unicode=True))


# ── Pretty print ──

def _print_header(title: str):
    w, _ = shutil.get_terminal_size()
    print(f"\n{C.BOLD}{C.MAGENTA}{'─' * w}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.MAGENTA}{'─' * w}{C.RESET}\n")


def _print_section(title: str):
    print(f"\n{C.BOLD}{C.YELLOW}◆ {title}{C.RESET}")


def _print_item(key: str, value: str, indent: int = 2):
    print(f"{' ' * indent}{C.GREEN}{key}{C.RESET}: {value}")


def _print_note(text: str):
    print(f"  {C.DIM}{C.ITALIC}{text}{C.RESET}")


def _input(prompt: str, default: str = "") -> str:
    """Prompt with color and optional default."""
    if default:
        full = f"{C.MAGENTA}?{C.RESET} {prompt} "
        full += f"{C.DIM}[{default}]{C.RESET} "
        val = input(full).strip()
        return val if val else default
    val = input(f"{C.MAGENTA}?{C.RESET} {prompt} ").strip()
    return val


# ── OK/Warn helpers ──

def _ok(text: str):
    print(f"  {C.GREEN}✓{C.RESET} {text}")


def _warn(text: str):
    print(f"  {C.YELLOW}⚠{C.RESET} {text}")


def _confirm(prompt: str, default: bool = True) -> bool:
    suffix = f"{C.DIM}[Y/n]{C.RESET}" if default else f"{C.DIM}[y/N]{C.RESET}"
    val = input(f"{C.MAGENTA}?{C.RESET} {prompt} {suffix} ").strip().lower()
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
{C.BOLD}{C.MAGENTA}██████╗ {C.YELLOW} █████╗ {C.MAGENTA}██╗    ██╗{C.RESET}
{C.BOLD}{C.MAGENTA}██╔══██╗{C.YELLOW}██╔══██╗{C.MAGENTA}██║    ██║{C.RESET}
{C.BOLD}{C.MAGENTA}██████╔╝{C.YELLOW}██████╔╝{C.MAGENTA}██║ █╗ ██║{C.RESET}
{C.BOLD}{C.MAGENTA}██╔══██╗{C.YELLOW}██╔══██╗{C.MAGENTA}██║███╗██║{C.RESET}
{C.BOLD}{C.MAGENTA}██████╔╝{C.YELLOW}██║  ██║{C.MAGENTA}╚███╔███╔╝{C.RESET}
{C.BOLD}{C.MAGENTA}╚═════╝ {C.YELLOW}╚═╝  ╚═╝{C.MAGENTA} ╚══╝╚══╝ {C.RESET}
    """
    formatted = ""
    for line in logo.strip().split("\n"):
        formatted += line.format(**globals()) + "\n"
    print(formatted)
    print(f"  {C.DIM}Black And White — Agent Platform v1.14.17{C.RESET}")
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


def _collect_models_by_capability(providers: dict, capability: str = "chat") -> list[tuple[str, str, str]]:
    """Collect model IDs from providers filtered by capability.

    Returns list of (model_id, provider_key, label).
    """
    results = []
    for pkey, pcfg in providers.items():
        for m in pcfg.get("models", []):
            mid = m["id"] if isinstance(m, dict) else m
            caps = m.get("capabilities", []) if isinstance(m, dict) else []
            if capability in caps or not caps:
                results.append((mid, pkey, f"{pkey}/{mid}"))
    return results


def _pick_model_menu(
    providers: dict,
    prompt: str = "Pick a model",
    exclude_provider: str | None = None,
    capability: str = "chat",
    current_model: str | None = None,
    extra_models: list[str] | None = None,
    only_providers: set[str] | None = None,
) -> str:
    """Show a numbered menu of models from configured providers, return chosen model ID."""
    options = []
    for pkey, pcfg in providers.items():
        if only_providers and len(only_providers) > 0 and pkey not in only_providers:
            continue
        if exclude_provider and pkey == exclude_provider:
            continue
        model_list = pcfg.get("models", [])
        # Cap at 10 models per provider to keep menu usable
        if len(model_list) > 10:
            shown = model_list[:10]
        else:
            shown = model_list
        for m in shown:
            mid = m["id"] if isinstance(m, dict) else m
            caps = m.get("capabilities", []) if isinstance(m, dict) else []
            # Filter by capability: show matching caps or models with no caps (generic)
            if caps and capability not in caps:
                continue
            label = f"{pkey}: {mid}"
            cw = m.get("context_window", "?") if isinstance(m, dict) else "?"
            options.append((mid, label, cw, pkey))

    if not options:
        # Fallback: show all models (still filtered)
        for pkey, pcfg in providers.items():
            if only_providers and len(only_providers) > 0 and pkey not in only_providers:
                continue
            if exclude_provider and pkey == exclude_provider:
                continue
            for m in pcfg.get("models", []):
                mid = m["id"] if isinstance(m, dict) else m
                label = f"{pkey}: {mid}"
                options.append((mid, label, "?", pkey))

    # Prepend current model if it's not already in the list
    if current_model and not any(mid == current_model for mid, _, _, _ in options):
        options.insert(0, (current_model, f"(current) {current_model}", "—", ""))

    # Prepend extra models (e.g. auto-detect) not in the list
    for em in (extra_models or []):
        if em and not any(mid == em for mid, _, _, _ in options):
            options.insert(0, (em, f"(auto) {em}", "—", ""))

    if not options:
        print(f"  {C.DIM}  No models available. Press Enter to skip.{C.RESET}")
        raw = input(f"  {C.MAGENTA}> {C.RESET}").strip()
        return raw if raw else ""  # empty = skip

    print()
    print(f"  {C.MAGENTA}?{C.RESET} {prompt}:")
    for i, (mid, label, cw, pkey) in enumerate(options, 1):
        print(f"     {C.GREEN}{i:>2}{C.RESET}) {label}  {C.DIM}({cw} ctx){C.RESET}")
    print(f"     {C.GREEN} 0{C.RESET}) Type manually")

    raw = input(f"  {C.MAGENTA}> {C.RESET}").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return options[int(raw) - 1][0]
    if raw == "0":
        return _input(f"  Enter model ID manually", default=options[0][0])
    return options[0][0] if options else ""


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
    _print_section("1. API Keys")
    _print_note("Each key will be tested immediately — you'll know if it works.")
    env_path = data_dir / ".env"
    existing_env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing_env[k.strip()] = v.strip()

    all_providers = [
        # (idx, env_key, label, provider_key, base_url, models_for_auto_config)
        (1,  "DEEPSEEK_API_KEY", "DeepSeek",      "deepseek", "https://api.deepseek.com/v1",
         [{"id": "deepseek-v4-flash", "capabilities": ["chat"], "context_window": 65536,
           "cost_per_1m_input": 0.30, "cost_per_1m_output": 1.20},
          {"id": "deepseek-v4-pro", "capabilities": ["chat"], "context_window": 65536}]),
        (2,  "MINIMAX_API_KEY", "MiniMax",        "minimax", "https://api.minimax.io/v1",  # 國際版可改 https://api.minimaxi.com/v1
         [{"id": "MiniMax-M3", "capabilities": ["chat", "vision", "tts", "image_generation"], "context_window": 1048576},
          {"id": "MiniMax-M2.7-highspeed", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2.7", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2.5-highspeed", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2.5", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2.1-highspeed", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2.1", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "MiniMax-M2", "capabilities": ["chat"], "context_window": 1048576}]),
        (3,  "OPENAI_API_KEY", "OpenAI",          "openai", "https://api.openai.com/v1",
         [{"id": "gpt-4o", "capabilities": ["chat", "vision"], "context_window": 128000},
          {"id": "gpt-4o-mini", "capabilities": ["chat", "vision"], "context_window": 128000},
          {"id": "o3", "capabilities": ["chat"], "context_window": 200000},
          {"id": "o4-mini", "capabilities": ["chat"], "context_window": 200000}]),
        (4,  "STEPFUN_API_KEY", "Stepfun",        "stepfun", "",
         [{"id": "step-3.7-flash", "capabilities": ["chat", "vision"], "context_window": 65536},
          {"id": "step-3.7-pro", "capabilities": ["chat"], "context_window": 65536}]),
        (5,  "MOONSHOT_API_KEY", "Moonshot/Kimi",  "moonshot", "https://api.moonshot.ai/v1",
         [{"id": "moonshot-v1", "capabilities": ["chat"], "context_window": 131072}]),
        (6,  "ANTHROPIC_API_KEY", "Anthropic",     "anthropic", "https://api.anthropic.com/v1",
         [{"id": "claude-sonnet-4", "capabilities": ["chat"], "context_window": 200000},
          {"id": "claude-haiku-3.5", "capabilities": ["chat"], "context_window": 200000}]),
        (7,  "GEMINI_API_KEY", "Google Gemini",   "gemini", "https://generativelanguage.googleapis.com/v1beta",
         [{"id": "gemini-2.0-flash", "capabilities": ["chat"], "context_window": 1048576},
          {"id": "gemini-2.5-pro", "capabilities": ["chat"], "context_window": 1048576}]),
        (8,  "XAI_API_KEY", "Grok / xAI",         "xai", "https://api.x.ai/v1",
         [{"id": "grok-3", "capabilities": ["chat", "vision"], "context_window": 131072},
          {"id": "grok-3-mini", "capabilities": ["chat"], "context_window": 131072}]),
        (9,  "OPENROUTER_API_KEY", "OpenRouter",   "openrouter", "https://openrouter.ai/api/v1",
         [{"id": "openrouter/auto", "capabilities": ["chat"], "context_window": 128000}]),
        (10, "TOGETHER_API_KEY", "Together AI",   "together", "https://api.together.xyz/v1",
         [{"id": "meta-llama/Llama-4-Scout-17B-16E-Instruct", "capabilities": ["chat"], "context_window": 131072}]),
        (11, "GROQ_API_KEY", "Groq",               "groq", "https://api.groq.com/openai/v1",
         [{"id": "llama-4-scout-17b-16e-instruct", "capabilities": ["chat"], "context_window": 131072},
          {"id": "deepseek-r1-distill-llama-70b", "capabilities": ["chat"], "context_window": 131072}]),
        (12, "MISTRAL_API_KEY", "Mistral AI",      "mistral", "https://api.mistral.ai/v1",
         [{"id": "mistral-large-latest", "capabilities": ["chat"], "context_window": 131072},
          {"id": "mistral-small-latest", "capabilities": ["chat"], "context_window": 131072}]),
        (13, "PERPLEXITY_API_KEY", "Perplexity",   "perplexity", "https://api.perplexity.ai",
         [{"id": "sonar-pro", "capabilities": ["chat", "search"], "context_window": 131072}]),
        (14, "__CUSTOM__", "Custom (input your own)", "custom", "",
         []),
    ]

    # Show existing keys
    if existing_env:
        _print_note("Already configured:")
        for idx, env_key, label, *_ in all_providers:
            if env_key != "__CUSTOM__" and env_key in existing_env:
                _print_item(label, f"✓ saved ({existing_env[env_key][:8]}...)")
        print()

    # Show available providers
    print(f"  {C.DIM}Add API keys one at a time — Enter when done:{C.RESET}")
    for idx, _, label, _, _, _ in all_providers:
        has_key = "✓" if any(env_key in existing_env for i, env_key, l, *_ in [p for p in all_providers if p[0]==idx]) else " "
        print(f"     {C.GREEN}{idx:>2}{C.RESET}) {has_key} {label}")

    new_env = {}
    plan_choices = {}
    validated_providers: set[str] = set()
    selected_providers = []  # track for auto-config later

    # Loop: pick provider → enter key → repeat
    while True:
        raw = input(f"  {C.MAGENTA}?{C.RESET} Add provider (number, Enter=done): ").strip().lower()
        if raw in ("", "d", "done"):
            break
        if raw == "a":
            for p in all_providers:
                if p[1] != "__CUSTOM__" and p[1] not in existing_env:
                    selected_providers.append(p)
            break
        for part in raw.replace(",", " ").split():
            if not part.isdigit():
                continue
            idx = int(part)
            provider = next((p for p in all_providers if p[0] == idx), None)
            if not provider:
                continue
            selected_providers.append(provider)
            # Immediately prompt for key
            _, env_key, label, provider_key, default_base, _ = provider
            _print_item(label, "")
            if env_key in existing_env:
                _print_item(label, f"already set ({existing_env[env_key][:8]}...), keeping")
                validated_providers.add(provider_key)
                continue
            val = input(f"  {C.MAGENTA}?{C.RESET} {label} ({env_key}): ").strip()
            if not val:
                continue
            # Validate and store
            base_url = default_base
            if base_url:
                print(f"  {C.DIM}Testing key...{C.RESET} ", end="", flush=True)
                ok, msg = _validate_api_key(provider_key, base_url, val)
                print()
                if ok:
                    _ok(msg)
                    validated_providers.add(provider_key)
                else:
                    _warn(msg)
                    if not _confirm("  Use anyway?", default=False):
                        continue
                    validated_providers.add(provider_key)
            new_env[env_key] = val
            _ok(f"{label} key saved")
        print()  # blank line before next prompt

    # Auto-detect from pre-existing keys not yet added
    for idx, env_key, label, provider_key, default_base, _ in all_providers:
        if env_key in existing_env and provider_key not in [v for p in selected_providers for v in [p[3]]]:
            validated_providers.add(provider_key)

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
    # ── 3. Providers (auto-configure from keys) ──
    _print_section("2. Providers")
    providers = cfg.setdefault("providers", {})

    # Build env_key -> provider data map
    _provider_map = {}
    for _idx, _ek, _label, _pk, _base, _models in all_providers:
        if _ek != "__CUSTOM__":
            _provider_map[_ek] = (_pk, _base, _models, _label)
        else:
            _provider_map["__custom__"] = ("custom", "", [], "Custom")

    configured_any = False
    for env_key, (provider_key, base_url, models, label) in _provider_map.items():
        if env_key == "__custom__":
            # Handle custom providers stored in plan_choices
            for _ck, _cv in plan_choices.items():
                if _ck.startswith("__custom__"):
                    _name = _ck.replace("__custom__", "")
                    if _name and _name not in providers:
                        providers[_name] = {
                            "api_key_env": _cv["env"],
                            "base_url": _cv["base_url"],
                            "protocol": "openai-chat",
                            "models": ([{"id": _cv["model"], "capabilities": ["chat"], "context_window": 131072}]
                                       if _cv["model"] else [{"id": "custom-model", "capabilities": ["chat"], "context_window": 131072}]),
                        }
                        _ok(f"Custom provider '{_name}' configured")
                        configured_any = True
            continue

        if env_key not in all_keys:
            continue
        if provider_key in providers:
            continue

        providers[provider_key] = {
            "api_key_env": env_key,
            "base_url": base_url,
            "models": models or [{"id": provider_key, "capabilities": ["chat"], "context_window": 131072}],
        }
        _ok(f"{label} provider configured")
        configured_any = True

    if not configured_any and existing_env:
        configured_any = True
    if not configured_any:
        _warn("No API keys configured — BAW needs at least one provider to work")
        _print_note("Run 'baw --setup' again after getting API keys")

    # ── 3. Default Model (dropdown from configured providers) ──
    _print_section("3. Default Model")
    # Ensure all configured providers are in validated set
    if providers and not validated_providers:
        validated_providers = set(providers.keys())
    current_model = cfg.get("model", {}).get("default", "")
    if providers:
        model_id = _pick_model_menu(providers, "Default model (main model for chat/tools)", only_providers=validated_providers)
        if not model_id:
            model_id = current_model or "deepseek-v4-flash"
        cfg.setdefault("model", {})["default"] = model_id

        # Find default provider for exclusion
        _default_provider = ""
        for _pk, _pc in providers.items():
            for _m in _pc.get("models", []):
                _mid = _m["id"] if isinstance(_m, dict) else _m
                if _mid == model_id:
                    _default_provider = _pk
                    break
            if _default_provider:
                break

        # Check if other providers exist for fallback
        _other_providers = [pk for pk in validated_providers if pk != _default_provider] if validated_providers else []
        if _other_providers:
            _print_note("Fallback model (different provider, Enter to skip):")
            fb_id = _pick_model_menu(providers, "Fallback model", exclude_provider=_default_provider, only_providers=validated_providers)
            if fb_id:
                cfg.setdefault("model", {})["fallback"] = fb_id
        else:
            _print_note("No other providers configured — add more API keys for fallback support")
            # Keep existing fallback if any
            if not cfg.get("model", {}).get("fallback"):
                cfg.setdefault("model", {})["fallback"] = ""
    else:
        _print_note("No providers configured yet. Set model manually or configure providers first.")
        model_id = _input("Default model ID", default=current_model or "deepseek-v4-flash")
        cfg.setdefault("model", {})["default"] = model_id

    # ── 4. Capabilities (interactive model picker per tool) ──
    _print_section("4. Capabilities")
    caps = cfg.setdefault("capabilities", {})
    changed_caps = False

    caps.setdefault("chat", {})["model"] = model_id
    changed_caps = True

    # Default auto-detect logic
    has_minimax = "MINIMAX_API_KEY" in all_keys
    has_stepfun = "STEPFUN_API_KEY" in all_keys
    has_xai = "XAI_API_KEY" in all_keys

    def _probe_stt_endpoint(base_url: str, api_key_env: str, model_id: str) -> bool:
        """Quick probe: check if an STT endpoint is reachable.
        Returns True if endpoint responds (any valid API response, not 404)."""
        import httpx as _hx
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            return False
        b_url = base_url.rstrip("/")
        # Try common STT endpoint paths
        for path in ["/stt", "/audio/transcriptions", "/audio/asr/sse"]:
            url = f"{b_url}{path}"
            try:
                resp = _hx.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    # Send tiny valid JSON to see if endpoint recognises the format
                    json={"model": model_id, "test": True},
                    timeout=5,
                )
                # 404 = endpoint doesn't exist at this path
                # 400/422 = endpoint exists but rejects our request format (probe succeeded)
                # 200 = endpoint exists and accepted (unlikely without real audio)
                if resp.status_code != 404:
                    print(f"  {C.DIM}  Probe {path}: HTTP {resp.status_code}{C.RESET}")
                    return True
            except Exception:
                continue
        return False

    def _auto_stt() -> dict | None:
        # Priority 1: Stepfun (proven working ASR endpoint)
        if has_stepfun:
            plan = plan_choices.get("STEPFUN_API_KEY", "standard")
            stt_base = "https://api.stepfun.ai/step_plan/v1" if plan == "step_plan" else "https://api.stepfun.ai/v1"
            print(f"  {C.DIM}  Probing Stepfun ASR...{C.RESET}")
            if _probe_stt_endpoint(stt_base, "STEPFUN_API_KEY", "stepaudio-2.5-asr"):
                return {"method": "auto-asr", "model": "stepaudio-2.5-asr", "base_url": stt_base, "api_key_env": "STEPFUN_API_KEY"}

        # Priority 2: xAI/Grok (uses /v1/stt, not /v1/audio/transcriptions)
        if has_xai:
            print(f"  {C.DIM}  Probing xAI/Grok STT...{C.RESET}")
            if _probe_stt_endpoint("https://api.x.ai/v1", "XAI_API_KEY", "grok-stt"):
                return {"method": "auto-asr", "model": "grok-stt", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"}

        # Priority 3: MiniMax (claims model-level STT)
        if has_minimax:
            print(f"  {C.DIM}  MiniMax model STT (no endpoint needed){C.RESET}")
            return {"method": "model", "model": "MiniMax-M3"}

        # Fallback: faster-whisper if installed
        try:
            import faster_whisper as _fw
            _ = _fw  # suppress unused
            print(f"  {C.DIM}  Using faster-whisper (local, free){C.RESET}")
            return {"method": "faster-whisper", "model": "base"}
        except ImportError:
            pass

        return None

    def _auto_tts() -> dict | None:
        if has_stepfun:
            plan = plan_choices.get("STEPFUN_API_KEY", "standard")
            tts_base = "https://api.stepfun.ai/step_plan/v1" if plan == "step_plan" else "https://api.stepfun.ai/v1"
            return {"method": "model", "model": "stepaudio-2.5-tts", "voice": "Cantonese_GentleLady",
                    "config": {"api_model": "stepaudio-2.5-tts", "base_url": tts_base}}
        if has_minimax:
            return {"model": "MiniMax-M3", "voice": "Cantonese_GentleLady",
                    "config": {"api_model": "speech-2.8-hd"}}
        if has_xai:
            return {"method": "model", "model": "grok-tts"}
        return None

    def _auto_vision() -> dict | None:
        if has_minimax:
            return {"model": "MiniMax-M3"}
        if has_stepfun:
            return {"model": "step-3.7-flash"}
        if has_xai:
            return {"model": "grok-3"}
        return None

    def _auto_image_gen() -> dict | None:
        if "OPENAI_API_KEY" in all_keys:
            return {"model": "dall-e-3"}
        if has_minimax:
            return {"model": "MiniMax-M3"}
        if has_xai:
            return {"model": "grok-3"}
        return None

    def _auto_browser() -> dict | None:
        # Browser uses a chat model — prefer default model or any available
        if model_id:
            return {"model": model_id}
        return None

    def _configure_cap(name: str, key: str, auto_fn, providers_cfg: dict, auto_models: list[str] | None = None):
        """Interactive capability config: show auto-detect, let user accept/pick/skip."""
        nonlocal changed_caps

        # Show current config if exists
        existing = caps.get(key)
        if existing:
            desc = existing.get("model", existing.get("method", "?"))
            print(f"  {C.DIM}Current {name}: {desc}{C.RESET}")

        auto_cfg = auto_fn()
        if auto_cfg:
            desc = auto_cfg.get("model", auto_cfg.get("method", "auto"))
            print(f"  {C.DIM}Auto-detect: {desc}{C.RESET}")
            choice = input(f"  {C.MAGENTA}> {C.RESET}{name}: (a)ccept auto / (p)ick model / Enter=keep current [{C.DIM}Enter{C.RESET}]: ").strip().lower()
            if choice in ("a", "auto", "accept"):
                caps[key] = auto_cfg
                _ok(f"{name} configured ({desc})")
                changed_caps = True
            elif choice == "" and existing:
                pass  # Enter = keep existing
            elif choice == "p":
                cur = existing.get("model") if existing else None
                extras = list(dict.fromkeys(filter(None, [auto_cfg.get("model")] + (auto_models or []))))
                mid = _pick_model_menu(providers_cfg, f"Pick model for {name}", capability=key, current_model=cur, extra_models=extras)
                if mid:
                    # Merge auto-cfg's infrastructure fields (method, base_url, api_key_env, config)
                    # with user's chosen model. Without this, e.g. STT gets only model="grok-stt"
                    # but missing method, base_url, api_key_env — and fails at runtime.
                    result = dict(auto_cfg)  # copy all auto-detected fields
                    result["model"] = mid    # override model with user's choice
                    caps[key] = result
                    _ok(f"{name} configured ({mid})")
                    changed_caps = True
        elif providers_cfg:
            if existing:
                print(f"  {C.DIM}No auto-detect for {name}{C.RESET}")
                choice = input(f"  {C.MAGENTA}> {C.RESET}{name}: (k)eep current / (p)ick model / (s)kip [{C.DIM}K{C.RESET}]: ").strip().lower()
                if choice in ("", "k", "keep"):
                    pass  # keep existing
                elif choice == "p":
                    cur = existing.get("model") if existing else None
                    extras = auto_models or None
                    mid = _pick_model_menu(providers_cfg, f"Pick model for {name}", capability=key, current_model=cur, extra_models=extras)
                    if mid:
                        # When no auto-cfg, keep existing fields + swap model
                        result = dict(existing) if existing else {}
                        result["model"] = mid
                        caps[key] = result
                        _ok(f"{name} configured ({mid})")
                        changed_caps = True
            else:
                choice = input(f"  {C.MAGENTA}> {C.RESET}{name}: (p)ick model / (s)kip [{C.DIM}S{C.RESET}]: ").strip().lower()
                if choice == "p":
                    extras = auto_models or None
                    mid = _pick_model_menu(providers_cfg, f"Pick model for {name}", capability=key, extra_models=extras)
                    if mid:
                        caps[key] = {"model": mid}
                        _ok(f"{name} configured ({mid})")
                        changed_caps = True
        else:
            _print_note(f"{name}: skipped (no providers configured)")

    if providers:
        # Known special model IDs not in any provider's model list
        _stt_extra = ["grok-stt"] if has_xai else []
        _tts_extra = ["grok-tts"] if has_xai else []
        _vision_extra = ["grok-3"] if has_xai and not has_minimax and not has_stepfun else []
        _image_gen_extra = []
        if "OPENAI_API_KEY" in all_keys:
            _image_gen_extra.append("dall-e-3")
        if has_xai:
            _image_gen_extra.append("grok-3")
        _configure_cap("STT (Speech-to-Text)", "stt", _auto_stt, providers, auto_models=_stt_extra)
        _configure_cap("TTS (Text-to-Speech)", "tts", _auto_tts, providers, auto_models=_tts_extra)
        _configure_cap("Vision", "vision", _auto_vision, providers, auto_models=_vision_extra)
        _configure_cap("Image Generation", "image_generation", _auto_image_gen, providers, auto_models=_image_gen_extra)
        _configure_cap("Browser", "browser", _auto_browser, providers)
    else:
        _print_note("No providers — capabilities skipped. Add API keys and re-run setup to configure.")

    # ── 5. Behaviour ──
    _print_section("5. Behaviour")
    _print_note("Execution mode determines how thoroughly BAW checks its work:")
    _print_note("  auto    — automatic, BAW picks best mode per task (default)")
    _print_note("  quick   — fastest, no court/plan, direct execution")
    _print_note("  hybrid  — balanced, plan + execute, light verification")
    _print_note("  tight   — most thorough, full court + plan + per-step verify")
    current = cfg.get("mode", "auto")
    if current not in ("quick", "hybrid", "tight", "auto"):
        current = "auto"  # fix invalid stored value
    mode = _input("Mode", default=current)
    while mode not in ("quick", "hybrid", "tight", "auto"):
        print(f"{C.RED}  Must be: quick, hybrid, tight, or auto{C.RESET}")
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

    # ── 6. Messaging Platforms (optional) ──
    _print_section("6. Messaging Platforms (optional)")
    _print_note("Connect BAW to chat platforms. Skip for CLI-only use.")
    _print_note("You can configure multiple platforms. BAW will run all of them.")

    platforms_configured = []

    while True:
        _print_note("")
        _print_note("Available platforms:")
        _print_note("  1. Telegram")
        _print_note("  2. Discord")
        _print_note("  3. Slack (Socket Mode)")
        _print_note("  4. Matrix")
        _print_note("  5. Signal")
        _print_note("  6. WhatsApp")
        _print_note("  0. Done / Skip")
        choice = _input("Configure platform (0-6)", default="0")

        if choice == "0" or choice == "":
            break
        elif choice == "1":
            token = os.environ.get("BAW_TELEGRAM_TOKEN", "")
            if not token:
                token = input(f"  {C.MAGENTA}?{C.RESET} Telegram Bot Token: ").strip()
            if token:
                cfg.setdefault("telegram", {})["token"] = token
                # Configure allowed_users
                current_users = cfg.get("telegram", {}).get("allowed_users", [])
                if current_users:
                    current_str = ", ".join(str(u) for u in current_users)
                    _print_item("allowed_users", current_str)
                    change = input(f"  {C.MAGENTA}?{C.RESET} Change allowed users? (y/N): ").strip().lower()
                    if change == "y":
                        users_raw = input(f"  {C.MAGENTA}?{C.RESET} Allowed user IDs (comma-separated): ").strip()
                        if users_raw:
                            users = []
                            for part in users_raw.replace(",", " ").split():
                                part = part.strip()
                                try:
                                    users.append(int(part))
                                except ValueError:
                                    _warn(f"Invalid user ID: {part} — skipped")
                            if users:
                                cfg["telegram"]["allowed_users"] = users
                                _ok(f"Allowed users set: {users}")
                else:
                    users_raw = input(f"  {C.MAGENTA}?{C.RESET} Allowed Telegram user IDs (comma-separated, or blank for all): ").strip()
                    if users_raw:
                        users = []
                        for part in users_raw.replace(",", " ").split():
                            part = part.strip()
                            try:
                                users.append(int(part))
                            except ValueError:
                                _warn(f"Invalid user ID: {part} — skipped")
                        if users:
                            cfg.setdefault("telegram", {})["allowed_users"] = users
                        else:
                            cfg.setdefault("telegram", {})["allowed_users"] = []
                    else:
                        cfg.setdefault("telegram", {})["allowed_users"] = []
                _ok("Telegram configured")
                platforms_configured.append("Telegram")
            else:
                _print_note("No token — skipped")
        elif choice == "2":
            token = os.environ.get("BAW_DISCORD_TOKEN", "")
            if not token:
                token = input(f"  {C.MAGENTA}?{C.RESET} Discord Bot Token: ").strip()
            if token:
                prefix = _input("  Command prefix (e.g. 'baw ')", default="baw ")
                cfg.setdefault("discord", {})["token"] = token
                cfg["discord"]["prefix"] = prefix
                _ok("Discord configured")
                platforms_configured.append("Discord")
            else:
                _print_note("No token — skipped")
        elif choice == "3":
            bot_token = os.environ.get("BAW_SLACK_BOT_TOKEN", "")
            if not bot_token:
                bot_token = input(f"  {C.MAGENTA}?{C.RESET} Slack Bot Token (xoxb-...): ").strip()
            app_token = os.environ.get("BAW_SLACK_APP_TOKEN", "")
            if not app_token:
                app_token = input(f"  {C.MAGENTA}?{C.RESET} Slack App Token (xapp-...): ").strip()
            if bot_token and app_token:
                cfg.setdefault("slack", {})["bot_token"] = bot_token
                cfg["slack"]["app_token"] = app_token
                _ok("Slack configured (Socket Mode)")
                platforms_configured.append("Slack")
            else:
                _print_note("Both tokens required — skipped")
        elif choice == "4":
            homeserver = _input("Matrix homeserver", default="https://matrix.org")
            username = input(f"  {C.MAGENTA}?{C.RESET} Matrix username (@user:matrix.org): ").strip()
            token = input(f"  {C.MAGENTA}?{C.RESET} Access token (or leave blank for password): ").strip()
            if username:
                cfg.setdefault("matrix", {})["homeserver"] = homeserver
                cfg["matrix"]["username"] = username
                if token:
                    cfg["matrix"]["access_token"] = token
                else:
                    pwd = input(f"  {C.MAGENTA}?{C.RESET} Password: ").strip()
                    if pwd:
                        cfg["matrix"]["password"] = pwd
                _ok("Matrix configured")
                platforms_configured.append("Matrix")
            else:
                _print_note("No username — skipped")
        elif choice == "5":
            phone = input(f"  {C.MAGENTA}?{C.RESET} Signal phone number (+1555...): ").strip()
            if phone:
                cfg.setdefault("signal", {})["phone"] = phone
                _ok("Signal configured (requires signal-cli daemon)")
                platforms_configured.append("Signal")
            else:
                _print_note("No phone — skipped")
        elif choice == "6":
            token = input(f"  {C.MAGENTA}?{C.RESET} WhatsApp Cloud API token: ").strip()
            phone_id = input(f"  {C.MAGENTA}?{C.RESET} Phone Number ID: ").strip()
            if token and phone_id:
                cfg.setdefault("whatsapp", {})["token"] = token
                cfg["whatsapp"]["phone_number_id"] = phone_id
                _ok("WhatsApp configured (requires public webhook)")
                platforms_configured.append("WhatsApp")
            else:
                _print_note("Both token and phone ID required — skipped")
        else:
            _warn("Invalid choice — enter 0-6")

    if platforms_configured:
        _print_note(f"Configured: {', '.join(platforms_configured)}")
    else:
        _print_note("No messaging platforms configured. Add later: baw --cfg set <platform>.<key> <value>")

    # ── Save ──
    save_config(data_dir, cfg)

    # ── Init default schedule.yaml if missing ──
    _sched_dst = data_dir / "schedule.yaml"
    if not _sched_dst.exists():
        _sched_src = Path(__file__).parent.parent / "schedule.yaml"
        if _sched_src.exists():
            import shutil
            shutil.copy2(str(_sched_src), str(_sched_dst))
            _sched_dst.chmod(0o600)
            _ok(f"Default schedule copied to {_sched_dst}")
    # Ensure reports directory exists
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)

    print()
    _print_header("Setup Complete")
    _ok(f"Config saved to {data_dir / 'config.yaml'}")
    if new_env:
        _ok(f"API keys saved to {env_path}")
    print()

    # ── Self-test: verify each configured capability is complete ──
    _print_section("Self-Test")
    _all_good = True
    cap_checks = {
        "stt": ["method", "model", "base_url", "api_key_env"],
        "tts": ["method", "model"],
        "vision": ["model"],
        "image_generation": ["model"],
        "browser": ["model"],
    }
    for cap_name, required_fields in cap_checks.items():
        cap_cfg = cfg.get("capabilities", {}).get(cap_name, {})
        if not cap_cfg:
            continue  # not configured, skip
        missing = [f for f in required_fields if f not in cap_cfg or not str(cap_cfg.get(f, "")).strip()]
        if missing:
            _warn(f"{cap_name}: missing fields {missing}")
            _print_note(f"  Run: baw config set capabilities.{cap_name}.<field> <value>")
            _all_good = False
        else:
            # Check api_key_env actually set in env
            env_key = cap_cfg.get("api_key_env", "")
            if env_key and env_key not in os.environ and env_key not in existing_env:
                # Also check the loaded env file
                _warn(f"{cap_name}: {env_key} not found in env")
                _all_good = False
    if _all_good:
        _ok("All capabilities have complete configuration")
    print()

    _print_note("Next steps:")
    _print_note("  1. Run 'baw --doctor'     — verify everything works")
    _print_note("  2. Run 'baw \"hello\"'      — test the agent")
    _print_note("  3. Run 'baw --cfg list'   — review all settings")
    if platforms_configured:
        _print_note(f"  4. Start messaging: baw-bot — run {', '.join(platforms_configured)}")
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