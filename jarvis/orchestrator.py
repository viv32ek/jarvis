"""Shared Jarvis orchestrator used by CLI, Web UI, Voice, and Telegram."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from jarvis.brain import AntigravityBrain, BrainEvent
from jarvis.config import Settings
from jarvis.connectors.gmail import GmailClient
from jarvis.connectors.home_assistant import HomeAssistantClient
from jarvis.connectors.homelab import get_system_status
from jarvis.connectors.hub_app import HubAppClient, project_prefix_from_key
from jarvis.deploy_guard import is_deploy_intent
from jarvis.memory import MemoryStore
from jarvis.scheduler import build_morning_briefing

HELP_TEXT = """\
Jarvis Commands:
  /mode [assistant|code]  Show or switch mode (code uses Gemini 4 Neon in auto mode)
  /project [path]         Show or set active workspace directory for Code Mode
  /work <ISSUE-KEY>       Load a hub-app issue (e.g. UNI-42), auto-select repo, and work on it
  /hub [dashboard|decisions|qa|issue <KEY>|search <q>|lease]  Query hub-app tracker
  /approve                Approve a pending staging/production deployment request
  /deny                   Cancel a pending staging/production deployment request
  /status                 Show local Linux server & Docker status
  /briefing               Generate your morning briefing (system, hub-app, reminders, HA, Gmail)
  /ha [status|on|off|toggle <entity>]  Control Home Assistant locally
  /gmail [unread|search <q>]           Check Gmail via free direct OAuth
  /remember <fact>        Save a long-term fact to memory
  /facts                  List remembered facts
  /forget <id>            Delete a remembered fact by ID
  /remind <text>          Add a reminder
  /reminders              List pending reminders
  /done <id>              Mark a reminder completed
  /model [model-id]       Show or switch the active model for the current mode
  /reset                  Start a fresh conversation for the current mode/workspace
  /help                   Show this help message
"""


class JarvisOrchestrator:
    """Coordinates memory, local commands, deploy guardrails, and the Antigravity brain."""

    def __init__(self, settings: Settings | None = None, fixed_mode: str | None = None) -> None:
        self.settings = settings or Settings.load()
        self.memory = MemoryStore(self.settings.db_path, self.settings.profile_path)
        self.brain = AntigravityBrain(self.settings, self.memory)
        self.fixed_mode = fixed_mode
        if fixed_mode:
            self.brain.set_mode(fixed_mode)
        self._lock = asyncio.Lock()
        self._pending_deploy: tuple[str, str] | None = None  # (user_text, channel)

    @property
    def pending_deploy(self) -> str | None:
        return self._pending_deploy[0] if self._pending_deploy else None

    def handle_command(self, text: str) -> str | None:
        """Handle local instant slash commands. Returns reply text or None if not a command."""
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            return HELP_TEXT.strip()

        if cmd == "/status":
            return "Server Status:\n" + get_system_status()

        if cmd == "/briefing":
            return build_morning_briefing(self.settings, self.memory)

        if cmd == "/hub":
            hub_client = "jarvis-code" if self.brain.mode == "code" else "jarvis"
            hub = HubAppClient(self.settings.hub_app_dir, self.settings.hub_app_url, client_name=hub_client)
            sub = arg.split(maxsplit=1)
            action = sub[0].lower() if sub else "dashboard"
            rest = sub[1].strip() if len(sub) > 1 else ""
            if action == "dashboard":
                return hub.dashboard_summary(rest or None)
            if action == "decisions":
                return hub.decisions_summary(rest or None)
            if action == "qa":
                return hub.qa_ready_summary(rest or None)
            if action == "issue" and rest:
                return hub.issue_summary(rest)
            if action == "search" and rest:
                return hub.search_summary(rest)
            if action == "lease":
                return hub.lease_summary()
            return "Usage: /hub [dashboard [P] | decisions [P] | qa [P] | issue <KEY> | search <text> | lease]"

        if cmd == "/work":
            # Handled asynchronously in chat_stream so it can load the issue and run Code Mode.
            return None

        if cmd == "/ha":
            ha = HomeAssistantClient(self.settings.home_assistant_url, self.settings.home_assistant_token)
            try:
                sub = arg.split()
                if not sub or sub[0].lower() == "status":
                    return ha.summary()
                action = sub[0].lower()
                if action in ("on", "off", "toggle") and len(sub) >= 2:
                    entity = sub[1]
                    if action == "on":
                        ha.turn_on(entity)
                    elif action == "off":
                        ha.turn_off(entity)
                    else:
                        ha.toggle(entity)
                    return f"Home Assistant: {action} `{entity}` succeeded."
                return "Usage: /ha [status | on <entity_id> | off <entity_id> | toggle <entity_id>]"
            except RuntimeError as exc:
                return str(exc)

        if cmd == "/gmail":
            gmail = GmailClient(self.settings.root_dir / "credentials.json", self.settings.root_dir / "token.json")
            try:
                sub = arg.split(maxsplit=1)
                if not sub or sub[0].lower() == "unread":
                    return gmail.unread_summary()
                if sub[0].lower() == "search" and len(sub) == 2:
                    msgs = gmail.search(query=sub[1], max_results=8)
                    if not msgs:
                        return f"No emails matching `{sub[1]}`."
                    return "\n".join(f"- **{m['subject']}** — {m['from']}" for m in msgs)
                return "Usage: /gmail [unread | search <query>]"
            except RuntimeError as exc:
                return str(exc)

        if cmd == "/deny":
            if self._pending_deploy is None:
                return "No pending deployment request to cancel."
            cancelled = self._pending_deploy[0]
            self._pending_deploy = None
            return f"Cancelled pending deployment request: `{cancelled}`"

        if cmd == "/approve":
            # Handled asynchronously in chat_stream so it can execute the approved turn.
            return None

        if cmd == "/mode":
            if not arg:
                return f"Active mode: {self.brain.mode} (model: {self.brain.model})"
            if self.fixed_mode and arg.strip().lower() != self.fixed_mode:
                other = "Jarvis Code" if self.fixed_mode == "assistant" else "Jarvis"
                return f"This bot is locked to `{self.fixed_mode}` mode. Message **{other}** for `{arg.strip().lower()}` tasks."
            try:
                new_mode = self.brain.set_mode(arg)
            except ValueError as exc:
                return str(exc)
            extra = f", workspace: {self.brain.workspace}" if new_mode == "code" else ""
            return f"Switched to {new_mode} mode (model: {self.brain.model}{extra})."

        if cmd == "/project":
            if not arg:
                return f"Active code workspace: {self.brain.workspace}"
            try:
                ws = self.brain.set_workspace(arg)
            except ValueError as exc:
                return str(exc)
            return f"Code workspace set to: {ws}"

        if cmd == "/remember":
            if not arg:
                return "Usage: /remember <fact to remember>"
            fact_id = self.memory.add_fact(arg)
            return f"Remembered [{fact_id}]: {arg}"

        if cmd == "/facts":
            facts = self.memory.list_facts(limit=25)
            if not facts:
                return "No saved facts yet. Use `/remember <fact>` to save one."
            return "\n".join(f"[{f.id}] ({f.category}) {f.content}" for f in facts)

        if cmd == "/forget":
            if not arg.isdigit():
                return "Usage: /forget <fact_id>"
            ok = self.memory.delete_fact(int(arg))
            return f"Forgot fact [{arg}]." if ok else f"Fact [{arg}] not found."

        if cmd == "/remind":
            if not arg:
                return "Usage: /remind <reminder text>"
            rid = self.memory.add_reminder(arg)
            return f"Reminder added [{rid}]: {arg}"

        if cmd == "/reminders":
            reminders = self.memory.list_reminders(include_completed=False)
            if not reminders:
                return "No pending reminders."
            return "\n".join(
                f"[{r.id}] {r.text}" + (f" (due: {r.due_at})" if r.due_at else "")
                for r in reminders
            )

        if cmd == "/done":
            if not arg.isdigit():
                return "Usage: /done <reminder_id>"
            ok = self.memory.complete_reminder(int(arg))
            return f"Completed reminder [{arg}]." if ok else f"Reminder [{arg}] not found."

        if cmd == "/model":
            if not arg:
                return f"Active model ({self.brain.mode} mode): {self.brain.model}"
            self.brain.model = arg
            return f"Switched {self.brain.mode} mode model to: {self.brain.model}"

        if cmd == "/reset":
            self.brain.reset_conversation()
            return f"Started a fresh {self.brain.mode} conversation. Long-term memory and reminders are preserved."

        return f"Unknown command `{cmd}`. Type `/help` to see available commands."

    async def chat_stream(self, user_text: str, channel: str = "cli") -> AsyncIterator[BrainEvent]:
        """Stream a single turn, enforcing deploy guardrails and serializing conversation access."""
        stripped = user_text.strip()
        deploy_approved = False

        if stripped.lower() == "/approve":
            if self._pending_deploy is None:
                msg = "No pending deployment request to approve."
                yield BrainEvent(kind="delta", text=msg)
                yield BrainEvent(kind="result", text=msg)
                return
            user_text, channel = self._pending_deploy
            self._pending_deploy = None
            deploy_approved = True
        elif stripped.lower().startswith("/work"):
            parts = stripped.split(maxsplit=2)
            if len(parts) < 2:
                msg = "Usage: /work <ISSUE-KEY> [optional extra instructions]  (e.g. /work UNI-42)"
                yield BrainEvent(kind="delta", text=msg)
                yield BrainEvent(kind="result", text=msg)
                return
            if self.fixed_mode == "assistant":
                msg = "Use **Jarvis Code** for `/work <KEY>` coding tasks."
                yield BrainEvent(kind="delta", text=msg)
                yield BrainEvent(kind="result", text=msg)
                return
            issue_key = parts[1].strip().upper()
            extra = parts[2].strip() if len(parts) > 2 else ""
            if self.brain.mode != "code":
                self.brain.set_mode("code")

            prefix = project_prefix_from_key(issue_key)
            mapped_repo = self.settings.hub_projects.get(prefix)
            if mapped_repo:
                try:
                    self.brain.set_workspace(mapped_repo)
                except ValueError as exc:
                    err = f"Mapped workspace for `{prefix}` (`{mapped_repo}`) not found: {exc}"
                    yield BrainEvent(kind="delta", text=err)
                    yield BrainEvent(kind="result", text=err)
                    return

            hub = HubAppClient(self.settings.hub_app_dir, self.settings.hub_app_url, client_name="jarvis-code")
            ok, brief = hub.build_work_brief(issue_key)
            if not ok:
                yield BrainEvent(kind="delta", text=brief)
                yield BrainEvent(kind="result", text=brief)
                return
            user_text = brief + (f"\n\nExtra instructions from Vivek: {extra}" if extra else "")
        else:
            command_reply = self.handle_command(user_text)
            if command_reply is not None:
                yield BrainEvent(kind="delta", text=command_reply)
                yield BrainEvent(kind="result", text=command_reply)
                return

            if is_deploy_intent(user_text):
                self._pending_deploy = (user_text, channel)
                gate_msg = (
                    "DEPLOYMENT GUARDRAIL: This looks like a staging/production deployment request:\n"
                    f"> {user_text}\n\n"
                    f"Workspace: `{self.brain.workspace}` | Mode: `{self.brain.mode}`\n"
                    "Reply with `/approve` to run it once, or `/deny` to cancel."
                )
                yield BrainEvent(kind="delta", text=gate_msg)
                yield BrainEvent(kind="result", text=gate_msg)
                return

        async with self._lock:
            active_mode = self.brain.mode
            self.memory.log_message(channel=channel, role="user", content=user_text, mode=active_mode)
            collected: list[str] = []
            final_text = ""

            async for event in self.brain.stream_turn(
                user_text,
                channel=channel,
                deploy_approved=deploy_approved,
            ):
                if event.kind == "delta":
                    collected.append(event.text)
                elif event.kind == "result":
                    final_text = event.text or "".join(collected)
                yield event

            reply = final_text or "".join(collected)
            if reply.strip():
                self.memory.log_message(
                    channel=channel,
                    role="assistant",
                    content=reply.strip(),
                    mode=active_mode,
                )

    async def chat(self, user_text: str, channel: str = "cli") -> str:
        """Run a full turn and return the complete response text."""
        chunks: list[str] = []
        final: str | None = None
        async for event in self.chat_stream(user_text, channel=channel):
            if event.kind == "delta":
                chunks.append(event.text)
            elif event.kind == "result":
                final = event.text
            elif event.kind == "error":
                return f"Error: {event.text}"
        return (final if final is not None else "".join(chunks)).strip()
