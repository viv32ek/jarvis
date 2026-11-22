"""Tests for Phase 4 & 5 connectors (Home Assistant, Gmail, Homelab, Morning Briefing)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.config import Settings
from jarvis.connectors.gmail import GmailClient
from jarvis.connectors.home_assistant import HomeAssistantClient
from jarvis.connectors.homelab import get_system_status
from jarvis.orchestrator import JarvisOrchestrator


def test_homelab_status_and_briefing(tmp_path: Path) -> None:
    status = get_system_status()
    assert "Disk (/):" in status

    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)
    orch.memory.add_reminder("Check Penpot backups")

    briefing = asyncio.run(orch.chat("/briefing"))
    assert "Good morning Vivek" in briefing
    assert "Check Penpot backups" in briefing
    assert "Disk (/):" in briefing


def test_unconfigured_connectors_fail_gracefully(tmp_path: Path) -> None:
    ha = HomeAssistantClient("", "")
    assert ha.is_configured is False

    gmail = GmailClient(tmp_path / "credentials.json", tmp_path / "token.json")
    assert gmail.is_configured is False

    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)

    ha_reply = asyncio.run(orch.chat("/ha status"))
    assert "HOME_ASSISTANT_URL" in ha_reply or "not configured" in ha_reply or "Cannot reach" in ha_reply

    gmail_reply = asyncio.run(orch.chat("/gmail unread"))
    assert "credentials.json" in gmail_reply or "not installed" in gmail_reply
