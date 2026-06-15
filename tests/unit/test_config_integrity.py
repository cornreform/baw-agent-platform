"""P1: Config integrity + drift detection tests.

Covers: config vs runtime drift, required fields, capability routing sanity,
duplicate model IDs, empty providers.
"""
from __future__ import annotations

import pytest
import os
from pathlib import Path

pytestmark = [pytest.mark.unit]


class TestConfigDrift:
    """Config drift between repo and runtime."""

    def test_runtime_config_exists(self):
        runtime = Path.home() / ".baw" / "config.yaml"
        assert runtime.exists(), "~/.baw/config.yaml must exist"

    def test_capability_routing_no_text_model_for_vision(self):
        """Vision must not route to a text-only model."""
        import yaml
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("No config")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        caps = config.get("capabilities", {})
        vision_model = caps.get("vision", {}).get("model", "")
        # Check the model in providers — must have vision: true
        providers = config.get("providers", {})
        for pname, pcfg in providers.items():
            for m in pcfg.get("models", []):
                if m.get("id") == vision_model:
                    if "vision" in m:
                        assert m["vision"], f"Vision model {vision_model} must have vision:true"

    def test_default_model_in_providers(self):
        """Default model must exist in providers list."""
        import yaml
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("No config")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        default = config.get("model", {}).get("default", "")
        providers = config.get("providers", {})
        found = False
        for pcfg in providers.values():
            for m in pcfg.get("models", []):
                if m.get("id") == default:
                    found = True
        assert found, f"Default model '{default}' not found in providers"

    def test_no_duplicate_model_ids(self):
        """No duplicate model IDs across providers."""
        import yaml
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("No config")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        ids = []
        for pcfg in config.get("providers", {}).values():
            for m in pcfg.get("models", []):
                ids.append(m.get("id"))
        assert len(ids) == len(set(ids)), f"Duplicate model IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_court_models_exist_in_providers(self):
        """Court judge models must exist in providers list."""
        import yaml
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("No config")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        judge = config.get("court", {}).get("judge_model", "")
        all_ids = []
        for pcfg in config.get("providers", {}).values():
            for m in pcfg.get("models", []):
                all_ids.append(m.get("id"))
        assert judge in all_ids, f"Judge model '{judge}' not in providers"

    def test_tribunal_models_exist_in_providers(self):
        """Tribunal bench models must exist in providers."""
        import yaml
        config_path = Path.home() / ".baw" / "config.yaml"
        if not config_path.exists():
            pytest.skip("No config")
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        all_ids = []
        for pcfg in config.get("providers", {}).values():
            for m in pcfg.get("models", []):
                all_ids.append(m.get("id"))
        bench = config.get("tribunal", {}).get("bench", [])
        for b in bench:
            assert b.get("name") in all_ids, \
                f"Tribunal model '{b.get('name')}' not in providers"


class TestConfigRequiredFields:
    """Minimum viable config structure."""

    def test_minimal_config_structure(self, temp_baw_home: Path):
        """A minimal config must have model.default and providers."""
        import yaml
        minimal = {
            "model": {"default": "test-model"},
            "providers": {
                "test": {
                    "api_key_env": "TEST_KEY",
                    "base_url": "https://test.api/v1",
                    "models": [{"id": "test-model", "context_window": 4096}],
                }
            },
        }
        cfg_path = temp_baw_home / "config.yaml"
        cfg_path.write_text(yaml.dump(minimal), encoding="utf-8")
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        assert data["model"]["default"] == "test-model"
        assert len(data["providers"]) >= 1

    def test_capabilities_section_optional(self):
        """Capabilities section should be optional (not crash)."""
        import yaml
        config = {"model": {"default": "x"}, "providers": {}}
        # Even without capabilities, resolve_capability should return None gracefully
        from core.capabilities import resolve_capability
        result = resolve_capability(config, "vision")
        assert result is None  # Should return None, not crash
