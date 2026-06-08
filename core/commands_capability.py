"""BAW — Capability CLI Commands
Implements /capability for model routing.
"""
from __future__ import annotations
import yaml
from pathlib import Path


def handle_capability_command(arg: str, baw: dict) -> str:
    """Process /capability subcommands."""
    from core.capabilities import capability_help

    parts = arg.strip().split()
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1] if len(parts) > 1 else ""
    sub_arg2 = " ".join(parts[2:]) if len(parts) > 2 else ""

    config = baw["config"]
    data_dir = baw["data_dir"]
    cfg_path = data_dir / "config.yaml"

    if sub == "list" or not sub_arg:
        return capability_help(config)

    if sub == "set" and sub_arg and sub_arg2:
        # /capability set <func> <model_id>
        caps = config.setdefault("capabilities", {})
        cap_entry = caps.setdefault(sub_arg, {})
        cap_entry["model"] = sub_arg2
        cfg_path.write_text(
            yaml.dump(config, default_flow_style=False, allow_unicode=True)
        )
        return (
            f"✅ Capability **{sub_arg}** -> `{sub_arg2}`\n\n"
            f"用 /reload 套用新設定"
        )

    if sub == "method" and sub_arg and sub_arg2:
        # /capability method <func> <method_name>
        caps = config.setdefault("capabilities", {})
        cap_entry = caps.setdefault(sub_arg, {})
        cap_entry["method"] = sub_arg2
        cfg_path.write_text(
            yaml.dump(config, default_flow_style=False, allow_unicode=True)
        )
        return (
            f"✅ STT method for {sub_arg} -> `{sub_arg2}`\n\n"
            f"用 /reload 套用新設定"
        )

    if sub == "add" and sub_arg:
        # /capability add <model_id> [--provider X] [--caps a,b,c]
        model_id = sub_arg
        provider = ""
        caps_list = ["chat"]
        for i, part in enumerate(parts):
            if part == "--provider" and i + 1 < len(parts):
                provider = parts[i + 1]
            elif part == "--caps" and i + 1 < len(parts):
                caps_list = parts[i + 1].split(",")

        if not provider:
            provider = model_id.split("-")[0].lower()

        providers = config.setdefault("providers", {})
        if provider not in providers:
            providers[provider] = {
                "api_key_env": f"{provider.upper()}_API_KEY",
                "base_url": f"https://api.{provider}.com/v1",
                "models": [],
            }
        providers[provider]["models"].append({
            "id": model_id,
            "capabilities": caps_list,
            "context_window": 4096,
        })
        cfg_path.write_text(
            yaml.dump(config, default_flow_style=False, allow_unicode=True)
        )
        return (
            f"✅ 已新增模型 `{model_id}` -> {provider}\n"
            f"   能力: {', '.join(caps_list)}\n\n"
            f"用 /reload 套用新設定\n"
            f"如需修改 API base_url 或 api_key_env，手動編輯 ~/.baw/config.yaml"
        )

    return capability_help(config)
