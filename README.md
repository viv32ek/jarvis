# Jarvis — Self-Hosted Personal AI Assistant

100% free, self-hosted personal assistant running on Linux, powered by **Gemini 3.8 Flash** through your existing Antigravity CLI (`agy`) account credits.

## Quickstart (Phase 1 & 2)

```bash
# Copy example config (optional — defaults work out of the box)
cp .env.example .env

# Interactive CLI chat
.venv/bin/python -m jarvis

# Launch Core Service + Local Web UI (http://127.0.0.1:8765)
.venv/bin/python -m jarvis --serve

# Bind to LAN so your phone/other devices on Wi-Fi can open it too
.venv/bin/python -m jarvis --serve --host 0.0.0.0 --port 8765

# One-shot prompt
.venv/bin/python -m jarvis -p "Check my disk space and memory usage"

# Override model (e.g. fastest flash tier)
.venv/bin/python -m jarvis --model gemini-3.8-flash-low
```

## Built-in Commands

- `/remember <fact>` — Save a persistent fact to SQLite memory
- `/facts` — List saved facts
- `/forget <id>` — Remove a fact
- `/remind <text>` — Add a reminder
- `/reminders` — List pending reminders
- `/done <id>` — Mark a reminder complete
- `/model [id]` — View or switch active model
- `/reset` — Start a fresh conversation (keeps long-term memory)
- `/help` — Show all commands

## Roadmap

- [x] **Phase 1**: Core Antigravity streaming brain (`gemini-3.8-flash-medium`) + CLI
- [x] **Phase 2**: Local SQLite + Markdown (`memory/profile.md`) persistent memory
- [ ] **Phase 3**: Unified FastAPI Core + Local Web UI + Hands-Free Voice (`openWakeWord` / `faster-whisper` / local TTS) + Telegram Bot
- [ ] **Phase 4**: Free connectors — Gmail OAuth, Home Assistant local API, local system helpers
- [ ] **Phase 5**: Proactive briefings scheduler + 24/7 `systemd --user` daemon
