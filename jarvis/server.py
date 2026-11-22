"""FastAPI core service for Jarvis (shared by Web UI, Voice, Telegram, and scripts)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jarvis.config import Settings
from jarvis.orchestrator import JarvisOrchestrator

WEB_DIR = Path(__file__).resolve().parent / "web"


class ChatRequest(BaseModel):
    message: str
    channel: str = "api"


class FactRequest(BaseModel):
    content: str
    category: str = "general"


class ReminderRequest(BaseModel):
    text: str
    due_at: str | None = None


class ModelRequest(BaseModel):
    model: str


class ModeRequest(BaseModel):
    mode: str


class WorkspaceRequest(BaseModel):
    path: str


def create_app(
    settings: Settings | None = None,
    enable_telegram: bool = False,
    telegram_bot_filter: str = "all",
) -> FastAPI:
    """Create the FastAPI application with separate Personal & Code orchestrators."""
    cfg = settings or Settings.load()
    personal_orch = JarvisOrchestrator(cfg, fixed_mode="assistant")
    code_orch = JarvisOrchestrator(cfg, fixed_mode="code")
    active_mode = {"value": "assistant"}
    active_telegram_roles: list[str] = []

    def current_orchestrator() -> JarvisOrchestrator:
        return code_orch if active_mode["value"] == "code" else personal_orch

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tg_bots: list[tuple[str, Any]] = []
        if enable_telegram:
            from jarvis.telegram_bot import build_telegram_bots

            tg_bots = build_telegram_bots(
                personal_orch,
                code_orch,
                settings=cfg,
                bot_filter=telegram_bot_filter,
            )
            for role, tg_app in tg_bots:
                await tg_app.initialize()
                await tg_app.start()
                if tg_app.updater is not None:
                    await tg_app.updater.start_polling()
                active_telegram_roles.append(role)
                label = "Jarvis Code" if role == "code" else "Jarvis"
                print(f"{label} Telegram bot attached to Core Service.")
        try:
            yield
        finally:
            for _, tg_app in tg_bots:
                if tg_app.updater is not None:
                    await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()

    app = FastAPI(title="Jarvis Core Service", version="0.2.0", lifespan=lifespan)
    app.state.personal_orchestrator = personal_orch
    app.state.code_orchestrator = code_orch
    app.state.orchestrator = personal_orch
    app.state.telegram_enabled = enable_telegram

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        orch = current_orchestrator()
        return {
            "status": "ok",
            "mode": active_mode["value"],
            "model": orch.brain.model,
            "workspace": str(code_orch.brain.workspace),
            "conversation_id": orch.brain.conversation_id,
            "pending_deploy": code_orch.pending_deploy,
            "telegram_enabled": enable_telegram,
            "telegram_bots": active_telegram_roles,
        }

    @app.post("/api/mode")
    async def set_mode(req: ModeRequest) -> dict[str, Any]:
        normalized = req.mode.strip().lower()
        if normalized not in ("assistant", "code"):
            raise HTTPException(status_code=400, detail="Mode must be 'assistant' or 'code'")
        active_mode["value"] = normalized
        orch = current_orchestrator()
        app.state.orchestrator = orch
        return {
            "mode": active_mode["value"],
            "model": orch.brain.model,
            "workspace": str(code_orch.brain.workspace),
        }

    @app.post("/api/workspace")
    async def set_workspace(req: WorkspaceRequest) -> dict[str, str]:
        try:
            ws = code_orch.brain.set_workspace(req.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"workspace": str(ws)}

    @app.post("/api/model")
    async def set_model(req: ModelRequest) -> dict[str, str]:
        model = req.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model cannot be empty")
        orch = current_orchestrator()
        orch.brain.model = model
        return {"mode": active_mode["value"], "model": orch.brain.model}

    @app.post("/api/reset")
    async def reset_conversation() -> dict[str, str]:
        orch = current_orchestrator()
        orch.brain.reset_conversation()
        return {"status": "reset", "mode": active_mode["value"]}

    @app.get("/api/history")
    async def get_history(limit: int = 50, mode: str | None = None) -> dict[str, Any]:
        msgs = personal_orch.memory.recent_messages(limit=min(max(limit, 1), 200), mode=mode)
        return {
            "messages": [
                {
                    "id": m.id,
                    "channel": m.channel,
                    "mode": m.mode,
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at,
                }
                for m in msgs
            ]
        }

    @app.get("/api/memory")
    async def get_memory() -> dict[str, Any]:
        facts = personal_orch.memory.list_facts(limit=50)
        reminders = personal_orch.memory.list_reminders(include_completed=False)
        return {
            "facts": [
                {"id": f.id, "content": f.content, "category": f.category, "created_at": f.created_at}
                for f in facts
            ],
            "reminders": [
                {"id": r.id, "text": r.text, "due_at": r.due_at, "created_at": r.created_at}
                for r in reminders
            ],
        }

    @app.post("/api/memory/facts")
    async def add_fact(req: FactRequest) -> dict[str, Any]:
        content = req.content.strip()
        if not content:
            raise HTTPException(status_code=400, detail="Fact content cannot be empty")
        fact_id = personal_orch.memory.add_fact(content, category=req.category)
        return {"id": fact_id, "content": content, "category": req.category}

    @app.delete("/api/memory/facts/{fact_id}")
    async def delete_fact(fact_id: int) -> dict[str, bool]:
        ok = personal_orch.memory.delete_fact(fact_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Fact not found")
        return {"deleted": True}

    @app.post("/api/memory/reminders")
    async def add_reminder(req: ReminderRequest) -> dict[str, Any]:
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Reminder text cannot be empty")
        rid = personal_orch.memory.add_reminder(text, due_at=req.due_at)
        return {"id": rid, "text": text, "due_at": req.due_at}

    @app.post("/api/memory/reminders/{reminder_id}/done")
    async def complete_reminder(reminder_id: int) -> dict[str, bool]:
        ok = personal_orch.memory.complete_reminder(reminder_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Reminder not found")
        return {"completed": True}

    @app.post("/api/chat")
    async def chat_http(req: ChatRequest) -> dict[str, Any]:
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        orch = current_orchestrator()
        reply = await orch.chat(text, channel=req.channel)
        return {
            "reply": reply,
            "mode": orch.brain.mode,
            "model": orch.brain.model,
            "conversation_id": orch.brain.conversation_id,
        }

    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                message = str(data.get("message", "")).strip()
                channel = str(data.get("channel", "web"))
                if not message:
                    continue

                orch = current_orchestrator()
                async for event in orch.chat_stream(message, channel=channel):
                    await websocket.send_json(
                        {
                            "kind": event.kind,
                            "text": event.text,
                            "tool_name": event.tool_name,
                            "conversation_id": event.conversation_id,
                            "duration_seconds": event.duration_seconds,
                        }
                    )
        except WebSocketDisconnect:
            return

    return app
