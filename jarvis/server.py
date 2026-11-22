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


def create_app(settings: Settings | None = None, enable_telegram: bool = False) -> FastAPI:
    """Create the FastAPI application with a shared JarvisOrchestrator."""
    cfg = settings or Settings.load()
    orchestrator = JarvisOrchestrator(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tg_app = None
        if enable_telegram:
            from jarvis.telegram_bot import build_telegram_application

            tg_app = build_telegram_application(orchestrator, settings=cfg)
            await tg_app.initialize()
            await tg_app.start()
            if tg_app.updater is not None:
                await tg_app.updater.start_polling()
            print("Telegram bot attached to Jarvis Core Service.")
        try:
            yield
        finally:
            if tg_app is not None:
                if tg_app.updater is not None:
                    await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()

    app = FastAPI(title="Jarvis Core Service", version="0.1.0", lifespan=lifespan)
    app.state.orchestrator = orchestrator
    app.state.telegram_enabled = enable_telegram

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": orchestrator.brain.mode,
            "model": orchestrator.brain.model,
            "workspace": str(orchestrator.brain.workspace),
            "conversation_id": orchestrator.brain.conversation_id,
            "pending_deploy": orchestrator.pending_deploy,
            "telegram_enabled": enable_telegram,
        }

    @app.post("/api/mode")
    async def set_mode(req: ModeRequest) -> dict[str, Any]:
        try:
            mode = orchestrator.brain.set_mode(req.mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "mode": mode,
            "model": orchestrator.brain.model,
            "workspace": str(orchestrator.brain.workspace),
        }

    @app.post("/api/workspace")
    async def set_workspace(req: WorkspaceRequest) -> dict[str, str]:
        try:
            ws = orchestrator.brain.set_workspace(req.path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"workspace": str(ws)}

    @app.post("/api/model")
    async def set_model(req: ModelRequest) -> dict[str, str]:
        model = req.model.strip()
        if not model:
            raise HTTPException(status_code=400, detail="Model cannot be empty")
        orchestrator.brain.model = model
        return {"mode": orchestrator.brain.mode, "model": orchestrator.brain.model}

    @app.post("/api/reset")
    async def reset_conversation() -> dict[str, str]:
        orchestrator.brain.reset_conversation()
        return {"status": "reset", "mode": orchestrator.brain.mode}

    @app.get("/api/history")
    async def get_history(limit: int = 50, mode: str | None = None) -> dict[str, Any]:
        msgs = orchestrator.memory.recent_messages(limit=min(max(limit, 1), 200), mode=mode)
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
        facts = orchestrator.memory.list_facts(limit=50)
        reminders = orchestrator.memory.list_reminders(include_completed=False)
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
        fact_id = orchestrator.memory.add_fact(content, category=req.category)
        return {"id": fact_id, "content": content, "category": req.category}

    @app.delete("/api/memory/facts/{fact_id}")
    async def delete_fact(fact_id: int) -> dict[str, bool]:
        ok = orchestrator.memory.delete_fact(fact_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Fact not found")
        return {"deleted": True}

    @app.post("/api/memory/reminders")
    async def add_reminder(req: ReminderRequest) -> dict[str, Any]:
        text = req.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Reminder text cannot be empty")
        rid = orchestrator.memory.add_reminder(text, due_at=req.due_at)
        return {"id": rid, "text": text, "due_at": req.due_at}

    @app.post("/api/memory/reminders/{reminder_id}/done")
    async def complete_reminder(reminder_id: int) -> dict[str, bool]:
        ok = orchestrator.memory.complete_reminder(reminder_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Reminder not found")
        return {"completed": True}

    @app.post("/api/chat")
    async def chat_http(req: ChatRequest) -> dict[str, Any]:
        text = req.message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        reply = await orchestrator.chat(text, channel=req.channel)
        return {
            "reply": reply,
            "mode": orchestrator.brain.mode,
            "model": orchestrator.brain.model,
            "conversation_id": orchestrator.brain.conversation_id,
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

                async for event in orchestrator.chat_stream(message, channel=channel):
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
