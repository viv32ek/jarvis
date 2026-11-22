"""Lightweight async briefing & reminder scheduler for 24/7 Jarvis operation."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Awaitable, Callable

from jarvis.config import Settings
from jarvis.connectors.gmail import GmailClient
from jarvis.connectors.home_assistant import HomeAssistantClient
from jarvis.connectors.homelab import get_system_status
from jarvis.connectors.hub_app import HubAppClient, _extract_list
from jarvis.memory import MemoryStore

logger = logging.getLogger("jarvis.scheduler")


def check_new_hub_decisions(settings: Settings, memory: MemoryStore) -> list[dict[str, str]]:
    """Return newly seen open hub-app Decisions and record their IDs in SQLite state."""
    hub = HubAppClient(settings.hub_app_dir, settings.hub_app_url, client_name="jarvis")
    if not hub.is_available:
        return []
    res = hub.run_issues("questions")
    if not res.ok:
        return []

    items = _extract_list(res.data, "questions", "items")
    seen_raw = memory.get_state("hub_seen_decision_ids") or "[]"
    try:
        seen: set[str] = set(json.loads(seen_raw))
    except json.JSONDecodeError:
        seen = set()

    fresh: list[dict[str, str]] = []
    current_ids: set[str] = set()
    for q in items:
        qid = str(q.get("id") or q.get("ref") or q.get("issueKey") or "")
        if not qid:
            continue
        current_ids.add(qid)
        if qid not in seen:
            fresh.append(
                {
                    "id": qid,
                    "target": str(q.get("issueKey") or q.get("epicKey") or q.get("ref") or qid),
                    "text": str(q.get("question") or q.get("text") or ""),
                }
            )

    # Keep current open IDs so resolved decisions don't accumulate forever.
    memory.set_state("hub_seen_decision_ids", json.dumps(sorted(current_ids)))
    return fresh


async def run_hub_decision_poller(
    settings: Settings,
    memory: MemoryStore,
    notify: Callable[[str], Awaitable[None]],
    interval_seconds: int = 300,
) -> None:
    """Background task that pings Telegram when new hub-app Decisions appear."""
    # Seed initial state on startup so restarts don't re-alert existing decisions.
    await asyncio.to_thread(check_new_hub_decisions, settings, memory)
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            fresh = await asyncio.to_thread(check_new_hub_decisions, settings, memory)
            if fresh:
                lines = [f"🔔 New hub-app Decision(s) waiting on you ({len(fresh)}):"]
                for item in fresh[:8]:
                    lines.append(f"- [{item['target']}] {item['text']}")
                await notify("\n".join(lines))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Hub decision poller tick failed: %s", exc)


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
