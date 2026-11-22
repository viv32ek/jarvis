"""Lightweight async briefing & reminder scheduler for 24/7 Jarvis operation."""

from __future__ import annotations

from datetime import datetime

from jarvis.config import Settings
from jarvis.connectors.gmail import GmailClient
from jarvis.connectors.home_assistant import HomeAssistantClient
from jarvis.connectors.homelab import get_system_status
from jarvis.connectors.hub_app import HubAppClient
from jarvis.memory import MemoryStore


def build_morning_briefing(settings: Settings, memory: MemoryStore) -> str:
    """Assemble a concise, 100% free local morning briefing."""
    now = datetime.now().strftime("%A, %b %d · %I:%M %p")
    sections = [f"Good morning Vivek ({now})"]

    sections.append("System Status:\n" + get_system_status())

    hub = HubAppClient(settings.hub_app_dir, settings.hub_app_url, client_name="jarvis")
    if hub.is_available:
        sections.append("Hub-App Snapshot:\n" + hub.briefing_snapshot())

    reminders = memory.list_reminders(include_completed=False)
    if reminders:
        rem_lines = [f"- [{r.id}] {r.text}" + (f" (due: {r.due_at})" if r.due_at else "") for r in reminders]
        sections.append("Pending Reminders:\n" + "\n".join(rem_lines))
    else:
        sections.append("Pending Reminders:\n- None")

    ha = HomeAssistantClient(settings.home_assistant_url, settings.home_assistant_token)
    if ha.is_configured:
        try:
            sections.append("Home Assistant:\n" + ha.summary())
        except RuntimeError as exc:
            sections.append(f"Home Assistant: {exc}")

    gmail = GmailClient(settings.root_dir / "credentials.json", settings.root_dir / "token.json")
    if gmail.is_configured:
        try:
            sections.append("Gmail:\n" + gmail.unread_summary(limit=5))
        except RuntimeError as exc:
            sections.append(f"Gmail: {exc}")

    return "\n\n".join(sections)
