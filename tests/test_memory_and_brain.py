"""Unit tests for Jarvis memory, stream-json parser, and orchestrator commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.brain import parse_stream_line
from jarvis.config import Settings
from jarvis.memory import MemoryStore
from jarvis.orchestrator import JarvisOrchestrator


def test_memory_facts_reminders_and_context(tmp_path: Path) -> None:
    profile = tmp_path / "profile.md"
    profile.write_text("# Profile\n- User: Vivek\n", encoding="utf-8")
    store = MemoryStore(tmp_path / "jarvis.db", profile)

    fid = store.add_fact("Penpot runs on port 9001 on this server", category="homelab")
    assert fid >= 1
    assert len(store.search_facts("penpot")) == 1

    rid = store.add_reminder("Check Home Assistant backup")
    assert len(store.list_reminders()) == 1

    ctx = store.build_context_block("Where is penpot?")
    assert "User: Vivek" in ctx
    assert "Penpot runs on port 9001" in ctx
    assert "Check Home Assistant backup" in ctx

    assert store.complete_reminder(rid) is True
    assert store.list_reminders() == []

    assert store.delete_fact(fid) is True
    assert store.list_facts() == []


def test_parse_stream_line_events() -> None:
    init_ev = parse_stream_line('{"event":"init","conversation_id":"abc-123","init":{"model":"gemini-3.8-flash-medium"}}')
    assert init_ev is not None
    assert init_ev.kind == "init"
    assert init_ev.conversation_id == "abc-123"

    delta_ev = parse_stream_line(
        '{"event":"step_update","step_update":{"conversation_id":"abc-123","step_type":"agent_response","state":"ACTIVE","text_delta":"Hello"}}'
    )
    assert delta_ev is not None
    assert delta_ev.kind == "delta"
    assert delta_ev.text == "Hello"

    result_ev = parse_stream_line(
        '{"event":"result","result":{"conversation_id":"abc-123","status":"SUCCESS","response":"Hello world","duration_seconds":1.2}}'
    )
    assert result_ev is not None
    assert result_ev.kind == "result"
    assert result_ev.text == "Hello world"


def test_orchestrator_local_commands(tmp_path: Path) -> None:
    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)

    reply = asyncio.run(orch.chat("/remember My homelab IP is 192.168.1.50"))
    assert "Remembered [1]" in reply

    facts = asyncio.run(orch.chat("/facts"))
    assert "192.168.1.50" in facts

    rem = asyncio.run(orch.chat("/remind Renew SSL cert"))
    assert "Reminder added [1]" in rem

    done = asyncio.run(orch.chat("/done 1"))
    assert "Completed reminder [1]" in done

    model_reply = asyncio.run(orch.chat("/model gemini-3.8-flash-low"))
    assert "gemini-3.8-flash-low" in model_reply

    # Code mode switching defaults to Gemini 4 Neon + staging deploy guardrail
    code_reply = asyncio.run(orch.chat("/mode code"))
    assert "code mode" in code_reply
    assert orch.brain.model == "gemini-4-neon-medium"
    assert orch.brain._active_agy_mode() == "auto"
    prompt = orch.brain._build_prompt("deploy to staging", channel="cli", is_new_conversation=True)
    assert "staging" in prompt.lower()
    assert "permission" in prompt.lower()

    # Switching back to assistant restores assistant model & separate conversation thread
    back = asyncio.run(orch.chat("/mode assistant"))
    assert "assistant mode" in back
    assert orch.brain.model == "gemini-3.8-flash-low"
