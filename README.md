# Jarvis — Self-Hosted Dual-Mode Personal & Code Assistant

100% free, self-hosted personal & coding assistant running 24/7 on Linux inside `/home/vivek/agy`, powered by **Gemini 3.8 Flash** (Assistant Mode) and **Gemini 4 Neon** (Code Mode) through your existing Antigravity CLI (`agy`) account credits.

## Quickstart

```bash
cd /home/vivek/agy
cp .env.example .env

# Run Local Web UI + Telegram Bot together in one process (http://127.0.0.1:8765)
.venv/bin/python -m jarvis --serve --telegram --host 0.0.0.0 --port 8765

# Interactive CLI chat
.venv/bin/python -m jarvis

# Code Assistant Mode (Gemini 4 Neon · auto local edits, staging deploys ask permission)
.venv/bin/python -m jarvis --mode code --project my-repo

# Hands-Free Local Voice Daemon (connected to running Core Service)
.venv/bin/python -m jarvis --voice --api-url http://127.0.0.1:8765
```

## Checking Out Repos for Code Mode (Keeping Everything Inside `/home/vivek/agy`)

Clone any project repo into `/home/vivek/agy/workspaces/` (gitignored so it never collides with Jarvis's git history):

```bash
cd /home/vivek/agy/workspaces
git clone https://github.com/<user>/<repo>.git
```

Then switch Jarvis into that repo from CLI, Web UI, or Telegram:

```text
/mode code
/project <repo>
```

## Built-in Commands

- `/mode [assistant|code]` — Switch between Assistant Mode (`gemini-3.8-flash-medium`) and Code Mode (`gemini-4-neon-medium`)
- `/project [name-or-path]` — Set active Code Mode workspace (short names resolve inside `/home/vivek/agy/workspaces/`)
- `/approve` / `/deny` — One-shot approval for staging/production deployments blocked by the deployment guardrail
- `/status` — Local Linux CPU load, disk space, and running Docker containers
- `/briefing` — Morning briefing (server status, pending reminders, Home Assistant, Gmail)
- `/ha [status|on|off|toggle <entity>]` — Direct local Home Assistant control
- `/gmail [unread|search <query>]` — Direct free Google Gmail OAuth
- `/remember <fact>` / `/facts` / `/forget <id>` — Local SQLite long-term memory
- `/remind <text>` / `/reminders` / `/done <id>` — Local SQLite reminders
- `/model [id]` / `/reset` / `/help`

## 24/7 Linux Auto-Start (`systemd --user`)

```bash
mkdir -p ~/.config/systemd/user
cp /home/vivek/agy/systemd/jarvis.service.template ~/.config/systemd/user/jarvis.service
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
loginctl enable-linger $USER
```
