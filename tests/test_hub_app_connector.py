"""Tests for the hub-app connector, /hub commands, /work UNI-* workspace routing, and guardrails."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from jarvis.config import Settings
from jarvis.connectors.hub_app import HubAppClient, HubResult, project_prefix_from_key
from jarvis.deploy_guard import check_command
from jarvis.orchestrator import JarvisOrchestrator


def test_project_prefix_and_default_uni_mapping(tmp_path: Path) -> None:
    assert project_prefix_from_key("uni-42") == "UNI"
    assert project_prefix_from_key("HUB-89") == "HUB"
    settings = Settings.load(root_dir=tmp_path)
    assert settings.hub_projects["UNI"] == "myschedlr-final"


def test_hub_formatters_and_exit_code_messages(tmp_path: Path) -> None:
    client = HubAppClient(tmp_path, "http://127.0.0.1:3100", client_name="jarvis")

    with patch.object(
        client,
        "run_issues",
        return_value=HubResult(
            True,
            0,
            data={"questions": [{"issueKey": "UNI-12", "question": "Use Stripe or Square?"}]},
        ),
    ):
        out = client.decisions_summary()
        assert "Open Decisions (1)" in out
        assert "UNI-12" in out
        assert "Stripe or Square" in out

    with patch.object(
        client,
        "run_issues",
        return_value=HubResult(
            True,
            0,
            data={
                "ready": [{"key": "UNI-10", "title": "Fix calendar timezone"}],
                "needsQa": [{"key": "UNI-11", "title": "Export PDF", "reason": "1 AC open"}],
            },
        ),
    ):
        qa = client.qa_ready_summary()
        assert "Ready for QA Passed: 1 | Needs QA: 1" in qa
        assert "UNI-10" in qa
        assert "UNI-11" in qa


def test_deploy_guard_blocks_hub_app_direct_db_and_ecs_scale() -> None:
    blocked_db, reason_db = check_command("sqlite3 ~/git/hub-app/data/issues.db 'SELECT * FROM issues'")
    assert blocked_db is True
    assert reason_db and "hub-issues" in reason_db

    blocked_ecs, reason_ecs = check_command("aws ecs update-service --cluster staging --desired-count 1")
    assert blocked_ecs is True
    assert reason_ecs and "lease" in reason_ecs


def test_personal_bot_blocks_work_command(tmp_path: Path) -> None:
    settings = Settings.load(root_dir=tmp_path)
    personal = JarvisOrchestrator(settings, fixed_mode="assistant")
    reply = asyncio.run(personal.chat("/work UNI-42"))
    assert "Jarvis Code" in reply


def test_hub_decision_poller_deduplicates(tmp_path: Path) -> None:
    from jarvis.scheduler import check_new_hub_decisions

    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)

    sample = HubResult(
        True,
        0,
        data={"questions": [{"id": "q_1", "issueKey": "UNI-7", "question": "Approve schema?"}]},
    )
    with patch("jarvis.scheduler.HubAppClient.is_available", True), patch(
        "jarvis.scheduler.HubAppClient.run_issues", return_value=sample
    ):
        first = check_new_hub_decisions(settings, orch.memory)
        assert len(first) == 1 and first[0]["id"] == "q_1"

        # Second check with same decision returns empty (no duplicate spam)
        second = check_new_hub_decisions(settings, orch.memory)
        assert second == []
