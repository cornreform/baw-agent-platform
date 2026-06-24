"""Tests for core/plan.py — Plan Context Layer."""
import json
import os
import tempfile
from pathlib import Path

import pytest

# ── Fixtures ──

@pytest.fixture
def plans_dir():
    """Create a temp plans directory for test isolation."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def plan_cls(plans_dir):
    """Patch Plan.PLANS_DIR to temp dir and return the class."""
    import core.plan as plan_mod
    original = plan_mod.Plan.PLANS_DIR
    plan_mod.Plan.PLANS_DIR = plans_dir
    yield plan_mod.Plan
    plan_mod.Plan.PLANS_DIR = original


# ── Tests ──

class TestPlanCreate:
    def test_create_returns_plan_with_id(self, plan_cls):
        p = plan_cls.create(name="Test Plan")
        assert p.plan_id.startswith("pln_")
        assert p.name == "Test Plan"
        assert p.status == "active"

    def test_create_saves_yaml(self, plan_cls, plans_dir):
        p = plan_cls.create(name="Disk Plan")
        yaml_path = plans_dir / f"{p.plan_id}.yaml"
        assert yaml_path.exists()
        content = yaml_path.read_text(encoding="utf-8")
        assert "Test Plan" not in content
        assert "Disk Plan" in content

    def test_create_accepts_artifacts(self, plan_cls):
        arts = [{"name": "doc.pdf", "type": "document", "path": "/tmp/doc.txt"}]
        p = plan_cls.create(name="With Artifacts", artifacts=arts)
        assert len(p.artifacts) == 1
        assert p.artifacts[0]["name"] == "doc.pdf"

    def test_create_accepts_sessions(self, plan_cls):
        s = ["ses_abc123"]
        p = plan_cls.create(name="With Sessions", sessions=s)
        assert s[0] in p.sessions


class TestPlanLoad:
    def test_load_returns_none_for_missing(self, plan_cls):
        p = plan_cls.load("pln_nonexistent")
        assert p is None

    def test_load_returns_matching_plan(self, plan_cls, plans_dir):
        created = plan_cls.create(name="Load Test")
        loaded = plan_cls.load(created.plan_id)
        assert loaded is not None
        assert loaded.name == "Load Test"
        assert loaded.plan_id == created.plan_id
        assert loaded.status == "active"

    def test_load_preserves_all_fields(self, plan_cls):
        created = plan_cls.create(
            name="Full Fields",
            artifacts=[{"name": "a.pdf", "type": "document", "path": ""}],
            sessions=["ses_1", "ses_2"],
        )
        created.add_step("Step 1")
        created.save()
        loaded = plan_cls.load(created.plan_id)
        assert len(loaded.artifacts) == 1
        assert len(loaded.sessions) == 2
        assert len(loaded.steps) == 1


class TestPlanGetActive:
    def test_no_active_returns_none(self, plan_cls):
        assert plan_cls.get_active() is None

    def test_returns_active_plan(self, plan_cls, plans_dir):
        p = plan_cls.create(name="Active One")
        assert plan_cls.get_active() is not None
        assert plan_cls.get_active().plan_id == p.plan_id

    def test_returns_only_first_active(self, plan_cls):
        _ = plan_cls.create(name="A")
        _ = plan_cls.create(name="B")
        active = plan_cls.get_active()
        assert active is not None
        assert active.status == "active"

    def test_paused_plan_not_returned(self, plan_cls):
        p = plan_cls.create(name="Paused")
        p.status = "paused"
        p.save()
        assert plan_cls.get_active() is None


class TestPlanArtifacts:
    def test_add_artifact(self, plan_cls):
        p = plan_cls.create(name="Artifact Test")
        assert len(p.artifacts) == 0
        p.add_artifact(name="test.pdf", type="document", path="/tmp/test.txt")
        assert len(p.artifacts) == 1
        assert p.artifacts[0]["name"] == "test.pdf"

    def test_add_artifact_auto_saves(self, plan_cls, plans_dir):
        p = plan_cls.create(name="Auto Save")
        name = "contract.docx"
        p.add_artifact(name=name, type="document", path="/tmp/c.docx")
        loaded = plan_cls.load(p.plan_id)
        assert any(a["name"] == name for a in loaded.artifacts)


class TestPlanSteps:
    def test_add_step(self, plan_cls):
        p = plan_cls.create(name="Steps")
        p.add_step("Review documents")
        assert len(p.steps) == 1
        assert p.steps[0]["description"] == "Review documents"
        assert p.steps[0]["status"] == "pending"

    def test_complete_step(self, plan_cls):
        p = plan_cls.create(name="Complete")
        p.add_step("Step A")
        p.add_step("Step B")
        p.complete_step(p.steps[0]["id"])
        assert p.steps[0]["status"] == "completed"
        assert p.steps[1]["status"] == "pending"


class TestPlanSummary:
    def test_summarize_returns_string(self, plan_cls):
        p = plan_cls.create(name="Summary Test")
        p.add_step("Do something")
        s = p.summarize()
        assert isinstance(s, str)
        assert len(s) > 0
        assert "Summary Test" in s

    def test_status_block_contains_html(self, plan_cls):
        p = plan_cls.create(name="Status Block")
        p.add_step("Step 1")
        block = p.status_block()
        assert "<b>" in block
        assert "Status Block" in block
        assert "pending" in block or "completed" in block


class TestPlanDetect:
    def test_detect_no_signals_returns_none(self, plan_cls):
        # Non-plan messages return None
        result = plan_cls.detect_plan(["hello"])
        assert result is None

    def test_detect_keyword_no_longer_matches(self, plan_cls):
        # Keyword-based detection removed — LLM handles plan detection via <!--plan:-->
        # Heuristic fallback only triggers on 5+ file batch uploads
        result = plan_cls.detect_plan(["我有一個計劃叫客戶入職"])
        assert result is None

    def test_detect_project_no_longer_matches(self, plan_cls):
        result = plan_cls.detect_plan(["Project Alpha — 3 deliverables"])
        assert result is None

    def test_detect_batch_plan(self, plan_cls):
        # 5+ batch files triggers plan detection (file-batch heuristic)
        prompts = [
            "[File: contract.pdf]",
            "[File: id_card.jpg]",
            "[File: address_proof.pdf]",
            "[File: bank_statement.pdf]",
            "[File: tax_return.pdf]",
        ]
        result = plan_cls.detect_plan(prompts)
        assert result is not None

    def test_detect_batch_fewer_than_5(self, plan_cls):
        # 3 files alone not enough to trigger heuristic
        prompts = [
            "[File: a.pdf]",
            "[File: b.jpg]",
            "[File: c.pdf]",
        ]
        result = plan_cls.detect_plan(prompts)
        assert result is None


class TestPlanDeactivate:
    def test_deactivate_archives(self, plan_cls):
        p = plan_cls.create(name="To Close")
        assert p.status == "active"
        p.deactivate()
        assert p.status == "archived"
        # Reload to confirm persisted
        loaded = plan_cls.load(p.plan_id)
        assert loaded.status == "archived"
