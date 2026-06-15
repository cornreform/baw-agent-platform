"""P1-P2: Active Challenge + Skills Quality tests.

P2-1: BAW active challenge gate — Devil score threshold behavior
P2-2: Skills quality scanner — no --break-system-packages
"""
from __future__ import annotations

import pytest
import os
from pathlib import Path

pytestmark = [pytest.mark.integration]


class TestActiveChallenge:
    """P2-1: BAW must actively challenge user on high-risk requests."""

    def test_challenge_code_in_loop(self):
        """loop.py must contain ACTIVE CHALLENGE logic with Devil score thresholds."""
        loop_path = Path(__file__).resolve().parent.parent.parent / "core" / "loop.py"
        content = loop_path.read_text(encoding="utf-8")
        assert "ACTIVE CHALLENGE" in content, \
            "P2-1: loop.py must have ACTIVE CHALLENGE gate"
        assert "Devil Score" in content, \
            "P2-1: ACTIVE CHALLENGE must reference Devil Score"
        assert "_devil_score >= 9" in content, \
            "P2-1: Must have threshold at Devil score >= 9 (block execution)"
        assert "_devil_score >= 7" in content, \
            "P2-1: Must have threshold at Devil score >= 7 (warn + suggest alternative)"
        assert "_devil_score >= 4" in content, \
            "P2-1: Must have threshold at Devil score >= 4 (note concern)"


class TestSkillsQuality:
    """P2-2: Skills must be safe — no --break-system-packages, no dangerous patterns."""

    def test_no_break_system_packages_in_skills(self):
        """No skill must contain --break-system-packages."""
        import yaml
        skills_dir = Path.home() / ".baw" / "skills"
        if not skills_dir.exists():
            pytest.skip("No skills directory")

        dangerous = []
        for root, dirs, files in os.walk(skills_dir):
            for f in files:
                path = Path(root) / f
                try:
                    content = path.read_text(encoding="utf-8")
                    if "--break-system-packages" in content:
                        dangerous.append(str(path))
                except Exception:
                    continue

        assert not dangerous, (
            f"Skills contain --break-system-packages: {dangerous}. "
            "Fix: remove --break-system-packages flag"
        )

    def test_skills_have_required_fields(self):
        """Each YAML skill must have name, description, steps."""
        import yaml
        skills_dir = Path.home() / ".baw" / "skills"
        if not skills_dir.exists():
            pytest.skip("No skills directory")

        import os
        for root, dirs, files in os.walk(skills_dir):
            for f in files:
                if not f.endswith((".yaml", ".yml")):
                    continue
                path = Path(root) / f
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        assert "name" in data, f"{f}: missing 'name'"
                        assert "description" in data, f"{f}: missing 'description'"
                except Exception as e:
                    pytest.fail(f"Cannot parse {f}: {e}")

    def test_pptx_skill_has_references(self):
        """pptx-generator skill must have reference docs."""
        pptx_dir = Path.home() / ".baw" / "skills" / "pptx-generator"
        if not pptx_dir.exists():
            pytest.skip("pptx-generator skill not found")
        refs = list(pptx_dir.glob("references/*.md"))
        assert len(refs) >= 3, f"pptx-generator needs >=3 reference docs, has {len(refs)}"
