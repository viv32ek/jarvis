"""Antigravity CLI streaming brain adapter (Assistant + Code Assistant modes)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from jarvis.config import Settings
from jarvis.memory import MemoryStore

ASSISTANT_PERSONA = """\
You are Jarvis, Vivek's self-hosted personal AI assistant running 24/7 on his Linux server.
Style guidelines:
- Be concise, direct, and genuinely helpful.
- When answering for voice or quick chat, prefer short natural sentences over long walls of text.
- Respect Vivek's preference for 100% free, open-source, self-hosted tools.
- Never run destructive actions (deleting files, stopping services, sending external emails) without explicit confirmation.
"""

CODE_PERSONA = """\
You are Jarvis in Code Assistant Mode — a senior software engineer working directly on Vivek's local codebase.
Guidelines:
- Make clean, minimal, well-tested code changes and run local tests/linters to verify your work.
- Explain changes concisely with clear file paths and summaries.
- Prefer free, open-source, self-hosted tools and libraries.
- STRICT DEPLOYMENT GUARDRAIL: You may edit code and run local tests/builds automatically, \
but ANY deployment to staging or production (including staging deploys, docker/k8s rollouts, \
terraform apply, CI release triggers, or pushing to staging/main/release branches) \
MUST ALWAYS pause and ask Vivek for explicit permission before executing.
"""

VALID_MODES = ("assistant", "code")

STRIPPED_ENV_PREFIXES = ("TELEGRAM_", "HOME_ASSISTANT_", "GMAIL_", "JARVIS_", "PENPOT_", "HUB_")
STRIPPED_ENV_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY", "_PASSWORD")


def build_clean_child_env(
    base_env: dict[str, str] | None = None,
    deploy_approved: bool = False,
    hub_client: str | None = None,
) -> dict[str, str]:
    """Strip personal/service secrets from `agy` child process environments."""
    source = base_env if base_env is not None else os.environ
    cleaned: dict[str, str] = {}
    for key, val in source.items():
        upper = key.upper()
        if upper.startswith(STRIPPED_ENV_PREFIXES) or upper.endswith(STRIPPED_ENV_SUFFIXES):
            continue
        cleaned[key] = val
    if deploy_approved:
        cleaned["JARVIS_DEPLOY_APPROVED"] = "1"
    if hub_client:
        cleaned["HUB_ISSUES_CLIENT"] = hub_client
    return cleaned


@dataclass(frozen=True)
class BrainEvent:
    kind: str  # "init" | "delta" | "tool" | "result" | "error"
    text: str = ""
    conversation_id: str | None = None
    tool_name: str | None = None
    duration_seconds: float | None = None


def parse_stream_line(raw_line: str) -> BrainEvent | None:
    """Parse one NDJSON line from `agy --output-format stream-json`."""
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    event_type = payload.get("event")

    if event_type == "init":
        return BrainEvent(
            kind="init",
            conversation_id=payload.get("conversation_id"),
        )

    if event_type == "step_update":
        step = payload.get("step_update") or {}
        step_type = step.get("step_type", "")
        conv_id = step.get("conversation_id")
        if step_type == "agent_response":
            delta = step.get("text_delta") or ""
            if delta:
                return BrainEvent(kind="delta", text=delta, conversation_id=conv_id)
        elif step_type and step_type not in ("user_input", "agent_response"):
            if step.get("state") == "ACTIVE":
                return BrainEvent(kind="tool", tool_name=step_type, conversation_id=conv_id)
        return None

    if event_type == "result":
        res = payload.get("result") or {}
        status = res.get("status", "")
        if status and status != "SUCCESS":
            return BrainEvent(
                kind="error",
                text=res.get("error") or f"Antigravity returned status: {status}",
                conversation_id=res.get("conversation_id"),
            )
        return BrainEvent(
            kind="result",
            text=res.get("response") or "",
            conversation_id=res.get("conversation_id"),
            duration_seconds=res.get("duration_seconds"),
        )

    return None


class AntigravityBrain:
    """Streams turns through the local `agy` CLI using existing account AI credits."""

    def __init__(self, settings: Settings, memory: MemoryStore) -> None:
        self.settings = settings
        self.memory = memory
        self._mode: str = memory.get_state("jarvis_mode") or "assistant"
        if self._mode not in VALID_MODES:
            self._mode = "assistant"
        self._models: dict[str, str] = {
            "assistant": settings.model,
            "code": settings.code_model,
        }
        saved_workspace = memory.get_state("jarvis_workspace")
        self._workspace: Path = Path(saved_workspace) if saved_workspace else settings.root_dir

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(f"Mode must be one of: {', '.join(VALID_MODES)}")
        self._mode = normalized
        self.memory.set_state("jarvis_mode", normalized)
        return self._mode

    @property
    def model(self) -> str:
        return self._models[self._mode]

    @model.setter
    def model(self, value: str) -> None:
        self._models[self._mode] = value.strip()

    @property
    def workspace(self) -> Path:
        return self._workspace

    def set_workspace(self, path: str | Path) -> Path:
        raw = Path(path).expanduser()
        if not raw.is_absolute():
            candidate = (self.settings.workspaces_dir / raw).resolve()
            if candidate.exists() and candidate.is_dir():
                resolved = candidate
            else:
                resolved = raw.resolve()
        else:
            resolved = raw.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ValueError(f"Workspace directory does not exist: {resolved}")
        self._workspace = resolved
        self.memory.set_state("jarvis_workspace", str(resolved))
        return self._workspace

    @property
    def _conv_state_key(self) -> str:
        if self._mode == "code":
            ws_hash = hashlib.sha256(str(self._workspace).encode("utf-8")).hexdigest()[:12]
            return f"agy_conversation_id_code_{ws_hash}"
        return f"agy_conversation_id_{self._mode}"

    @property
    def conversation_id(self) -> str | None:
        return self.memory.get_state(self._conv_state_key)

    def reset_conversation(self) -> None:
        self.memory.delete_state(self._conv_state_key)

    def _active_agy_mode(self) -> str:
        return self.settings.code_agy_mode if self._mode == "code" else self.settings.agy_mode

    def _active_cwd(self) -> Path:
        return self._workspace if self._mode == "code" else self.settings.root_dir

    def _build_prompt(self, user_text: str, channel: str, is_new_conversation: bool) -> str:
        context_block = self.memory.build_context_block(user_query=user_text)
        parts: list[str] = []
        if is_new_conversation:
            persona = CODE_PERSONA if self._mode == "code" else ASSISTANT_PERSONA
            parts.append(persona.strip())
        if context_block:
            parts.append(f"<jarvis_memory>\n{context_block}\n</jarvis_memory>")
        header = f"[Mode: {self._mode} | Channel: {channel}"
        if self._mode == "code":
            header += f" | Workspace: {self._workspace}"
        header += "]"
        parts.append(f"{header}\nUser: {user_text}")
        return "\n\n".join(parts)

    async def stream_turn(
        self,
        user_text: str,
        channel: str = "cli",
        deploy_approved: bool = False,
    ) -> AsyncIterator[BrainEvent]:
        conv_id = self.conversation_id
        prompt = self._build_prompt(user_text, channel=channel, is_new_conversation=(conv_id is None))

        cmd = [
            self.settings.agy_bin,
            "-p",
            prompt,
            "--model",
            self.model,
            "--output-format",
            "stream-json",
            "--disable-slash-commands",
        ]
        agy_mode = self._active_agy_mode()
        if agy_mode:
            cmd.extend(["--mode", agy_mode])
        if conv_id:
            cmd.extend(["--conversation", conv_id])

        hub_client = "jarvis-code" if self._mode == "code" else "jarvis"
        env = build_clean_child_env(deploy_approved=deploy_approved, hub_client=hub_client)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._active_cwd()),
            env=env,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        got_delta = False
        got_result = False

        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            event = parse_stream_line(line)
            if event is None:
                continue
            if event.conversation_id:
                self.memory.set_state(self._conv_state_key, event.conversation_id)
            if event.kind == "delta":
                got_delta = True
            elif event.kind == "result":
                got_result = True
            yield event

        stderr_bytes = await proc.stderr.read()
        rc = await proc.wait()

        if rc != 0 and not got_result:
            err_msg = stderr_bytes.decode("utf-8", errors="replace").strip()
            yield BrainEvent(
                kind="error",
                text=err_msg or f"`agy` exited with status {rc}",
            )
        elif not got_delta and not got_result:
            yield BrainEvent(kind="error", text="No response received from Antigravity.")
