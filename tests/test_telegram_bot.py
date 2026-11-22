"""Tests for the free Telegram bot bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.config import Settings
from jarvis.orchestrator import JarvisOrchestrator
from jarvis.telegram_bot import (
    chunk_message,
    is_authorized,
    normalize_telegram_input,
    parse_allowed_user_ids,
)


def test_telegram_allowlist_fail_closed() -> None:
    empty = parse_allowed_user_ids("")
    assert empty == set()
    assert is_authorized(12345, empty) is False

    allowed = parse_allowed_user_ids("12345, 67890, notanumber")
    assert allowed == {12345, 67890}
    assert is_authorized(12345, allowed) is True
    assert is_authorized(99999, allowed) is False
    assert is_authorized(None, allowed) is False


def test_chunk_message_splits_cleanly() -> None:
    short = chunk_message("hello")
    assert short == ["hello"]

    long_text = ("line of code\n" * 500).strip()
    chunks = chunk_message(long_text, max_len=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)
    assert "\n".join(chunks).replace("\n\n", "\n").startswith("line of code")


def test_telegram_code_shortcut_and_deploy_gate(tmp_path: Path) -> None:
    settings = Settings.load(root_dir=tmp_path)
    orch = JarvisOrchestrator(settings)

    prompt, notice = normalize_telegram_input("/code deploy latest build to staging", orch)
    assert orch.brain.mode == "code"
    assert orch.brain.model == "gemini-4-neon-medium"
    assert prompt == "deploy latest build to staging"
    assert notice and "Code Mode" in notice

    reply = asyncio.run(orch.chat(prompt, channel="telegram"))
    assert "DEPLOYMENT GUARDRAIL" in reply
    assert orch.pending_deploy == "deploy latest build to staging"

    back_prompt, _ = normalize_telegram_input("/assistant what's on my reminders?", orch)
    assert orch.brain.mode == "assistant"
    assert back_prompt == "what's on my reminders?"

    # Dedicated fixed-mode bots stay locked to their role
    personal = JarvisOrchestrator(settings, fixed_mode="assistant")
    code = JarvisOrchestrator(settings, fixed_mode="code")
    assert "Jarvis Code" in asyncio.run(personal.chat("/mode code"))
    assert "Jarvis" in asyncio.run(code.chat("/mode assistant"))
