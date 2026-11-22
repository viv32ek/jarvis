"""Tests for the programmatic staging/production deployment guardrail & workspace isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.config import Settings
from jarvis.deploy_guard import check_command, evaluate_hook_payload, is_deploy_intent
from jarvis.orchestrator import JarvisOrchestrator


def test_deploy_guard_blocks_staging_and_prod_commands() -> None:
    blocked_cmds = [
        "./deploy.sh staging",
        "git push origin staging",
        "git push origin main",
        "kubectl apply -f k8s/staging.yaml",
        "helm upgrade myapp ./chart",
        "terraform apply -auto-approve",
        "fly deploy",
        "vercel --prod",
        "docker compose -f docker-compose.staging.yml up -d",
    ]
    for cmd in blocked_cmds:
        blocked, reason = check_command(cmd)
        assert blocked is True, f"Expected to block: {cmd}"
        assert reason and "DEPLOYMENT GUARDRAIL" in reason


def test_deploy_guard_allows_local_dev_and_tests() -> None:
    safe_cmds = [
        "pytest -v tests/",
        "npm test",
        "git status",
        "git diff",
        "git commit -m 'fix bug'",
        "git commit -m 'Initial Jarvis v0.1: add deploy guard'",
        "git push origin feature/my-branch",
        "python3 -m jarvis --serve",
        "docker compose up -d",
    ]
    for cmd in safe_cmds:
        blocked, reason = check_command(cmd)
        assert blocked is False, f"Expected to allow: {cmd} (got {reason})"


def test_pre_tool_use_hook_contract() -> None:
    payload = {
        "hookEventName": "PreToolUse",
        "toolCall": {
            "id": "t1",
            "name": "run_command",
            "args": {"CommandLine": "git push origin staging"},
        },
    }
    denied = evaluate_hook_payload(payload, approved=False)
    assert denied["decision"] == "deny"
    assert "DEPLOYMENT GUARDRAIL" in denied["reason"]

    allowed = evaluate_hook_payload(payload, approved=True)
    assert allowed["decision"] == "allow"


def test_orchestrator_deploy_gate_and_workspace_isolation(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)
    orch.brain.set_mode("code")

    # Separate conversation keys per workspace + short-name lookup inside workspaces/
    orch.brain.set_workspace(repo_a)
    key_a = orch.brain._conv_state_key
    orch.brain.set_workspace(repo_b)
    key_b = orch.brain._conv_state_key
    assert key_a != key_b

    ws_repo = settings.workspaces_dir / "sample-app"
    ws_repo.mkdir()
    assert orch.brain.set_workspace("sample-app") == ws_repo.resolve()

    # Staging deploy intent pauses and requires /approve or /deny
    assert is_deploy_intent("Please deploy the latest build to staging") is True
    reply = asyncio.run(orch.chat("Please deploy the latest build to staging"))
    assert "DEPLOYMENT GUARDRAIL" in reply
    assert "/approve" in reply
    assert orch.pending_deploy == "Please deploy the latest build to staging"

    # /deny clears it cleanly
    denied_reply = asyncio.run(orch.chat("/deny"))
    assert "Cancelled" in denied_reply
    assert orch.pending_deploy is None
