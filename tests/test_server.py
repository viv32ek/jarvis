"""Tests for the FastAPI Core Service + WebSocket + Local Web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from jarvis.config import Settings
from jarvis.server import create_app


def test_server_endpoints_and_ws(tmp_path: Path) -> None:
    settings = Settings.load(root_dir=tmp_path)
    app = create_app(settings)
    client = TestClient(app)

    # Web UI index
    res = client.get("/")
    assert res.status_code == 200
    assert "JARVIS" in res.text

    # Health & mode/model/workspace switching
    h = client.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["mode"] == "assistant"

    mode_res = client.post("/api/mode", json={"mode": "code"}).json()
    assert mode_res["mode"] == "code"
    assert mode_res["model"] == "gemini-4-neon-medium"

    ws_res = client.post("/api/workspace", json={"path": str(tmp_path)}).json()
    assert ws_res["workspace"] == str(tmp_path.resolve())

    client.post("/api/mode", json={"mode": "assistant"})
    m = client.post("/api/model", json={"model": "gemini-3.8-flash-low"}).json()
    assert m["model"] == "gemini-3.8-flash-low"

    # Memory CRUD
    f = client.post("/api/memory/facts", json={"content": "Penpot on 9001"}).json()
    assert f["id"] == 1
    r = client.post("/api/memory/reminders", json={"text": "Update HA"}).json()
    assert r["id"] == 1

    mem = client.get("/api/memory").json()
    assert len(mem["facts"]) == 1
    assert len(mem["reminders"]) == 1

    assert client.post(f"/api/memory/reminders/{r['id']}/done").status_code == 200
    assert client.delete(f"/api/memory/facts/{f['id']}").status_code == 200

    # Per-mode history separation
    orch = app.state.orchestrator
    orch.memory.log_message("web", "user", "hello assistant", mode="assistant")
    orch.memory.log_message("web", "user", "fix bug in repo", mode="code")

    hist_assistant = client.get("/api/history?mode=assistant").json()["messages"]
    hist_code = client.get("/api/history?mode=code").json()["messages"]
    assert len(hist_assistant) == 1 and hist_assistant[0]["content"] == "hello assistant"
    assert len(hist_code) == 1 and hist_code[0]["content"] == "fix bug in repo"

    # WebSocket local command streaming
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "/remember Server stays on 24/7", "channel": "web"})
        ev1 = ws.receive_json()
        ev2 = ws.receive_json()
        assert ev1["kind"] == "delta"
        assert "Remembered" in ev1["text"]
        assert ev2["kind"] == "result"
