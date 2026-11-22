"""Two-Bot Free Telegram Bridge: Jarvis (Personal) + Jarvis Code (Coding Projects)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from jarvis.config import Settings
from jarvis.orchestrator import JarvisOrchestrator

logger = logging.getLogger("jarvis.telegram")

TELEGRAM_MAX_LEN = 4000


def parse_allowed_user_ids(raw: str) -> set[int]:
    """Parse comma-separated Telegram numeric user IDs."""
    ids: set[int] = set()
    for part in (raw or "").split(","):
        cleaned = part.strip()
        if cleaned.isdigit():
            ids.add(int(cleaned))
    return ids


def is_authorized(user_id: int | None, allowed_ids: set[int]) -> bool:
    """Fail-closed authorization check: only explicitly allowed user IDs may talk to Jarvis."""
    if user_id is None or not allowed_ids:
        return False
    return user_id in allowed_ids


def chunk_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Split long replies cleanly under Telegram's 4096-char message limit."""
    stripped = (text or "").strip()
    if not stripped:
        return ["(empty response)"]
    if len(stripped) <= max_len:
        return [stripped]

    chunks: list[str] = []
    remaining = stripped
    while len(remaining) > max_len:
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at < max_len // 2:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at < max_len // 2:
            split_at = max_len
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def normalize_telegram_input(
    text: str,
    orchestrator: JarvisOrchestrator,
    bot_role: str = "personal",
) -> tuple[str, str | None]:
    """Handle Telegram shortcuts while respecting dedicated bot roles."""
    stripped = (text or "").strip()
    if stripped == "/start":
        return "/help", None

    # If the orchestrator is NOT locked to a fixed mode, preserve legacy /code & /assistant shortcuts.
    if orchestrator.fixed_mode is None:
        if stripped.startswith("/code"):
            rest = stripped[5:].strip()
            orchestrator.brain.set_mode("code")
            if not rest:
                return "/mode", None
            return rest, f"[Code Mode · {orchestrator.brain.model}]"
        if stripped.startswith("/assistant"):
            rest = stripped[10:].strip()
            orchestrator.brain.set_mode("assistant")
            if not rest:
                return "/mode", None
            return rest, f"[Assistant Mode · {orchestrator.brain.model}]"

    if bot_role == "code" and stripped.startswith("/code "):
        return stripped[6:].strip(), None

    return stripped, None


def _transcribe_voice_note(audio_path: Path) -> str:
    """Transcribe a Telegram voice note locally using faster-whisper (100% free)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Local voice transcription requires `faster-whisper` (Phase 3B). "
            "Install with: uv pip install --python .venv/bin/python -e '.[voice]'"
        ) from exc

    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path))
    return " ".join(seg.text.strip() for seg in segments).strip()


def build_telegram_application(
    orchestrator: JarvisOrchestrator,
    settings: Settings | None = None,
    bot_role: str = "personal",
    token: str | None = None,
):
    """Build and configure one python-telegram-bot Application (`personal` or `code`)."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ChatAction
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    cfg = settings or orchestrator.settings
    bot_token = token or (cfg.telegram_code_bot_token if bot_role == "code" else cfg.telegram_bot_token)
    env_var = "TELEGRAM_CODE_BOT_TOKEN" if bot_role == "code" else "TELEGRAM_BOT_TOKEN"
    bot_label = "Jarvis Code" if bot_role == "code" else "Jarvis"
    channel_name = "telegram-code" if bot_role == "code" else "telegram"

    if not bot_token:
        raise RuntimeError(
            f"{env_var} is not set. Add it to /home/vivek/agy/.env (get a free token from @BotFather)."
        )

    allowed_ids = parse_allowed_user_ids(cfg.telegram_allowed_user_id)
    if not allowed_ids:
        logger.warning(
            "[%s] TELEGRAM_ALLOWED_USER_ID is empty! All messages will be rejected until you add your numeric user ID to .env.",
            bot_label,
        )

    async def _send_reply(update: Update, reply_text: str) -> None:
        assert update.effective_chat is not None
        reply_markup = None
        if orchestrator.pending_deploy:
            reply_markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Approve Deploy", callback_data="deploy:approve"),
                        InlineKeyboardButton("❌ Deny", callback_data="deploy:deny"),
                    ]
                ]
            )
        chunks = chunk_message(reply_text)
        for idx, chunk in enumerate(chunks):
            markup = reply_markup if idx == len(chunks) - 1 else None
            await update.effective_chat.send_message(text=chunk, reply_markup=markup)

    async def _check_auth(update: Update) -> bool:
        user = update.effective_user
        uid = user.id if user else None
        if is_authorized(uid, allowed_ids):
            return True
        logger.warning("[%s] Rejected unauthorized Telegram user_id=%s", bot_label, uid)
        if update.effective_chat is not None:
            await update.effective_chat.send_message(
                f"Unauthorized. If you are the owner, add `TELEGRAM_ALLOWED_USER_ID={uid}` to `/home/vivek/agy/.env` and restart Jarvis."
            )
        return False

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _check_auth(update):
            return
        if not update.message or not update.message.text or not update.effective_chat:
            return

        prompt, notice = normalize_telegram_input(update.message.text, orchestrator, bot_role=bot_role)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        reply = await orchestrator.chat(prompt, channel=channel_name)
        if notice:
            reply = f"{notice}\n\n{reply}"
        await _send_reply(update, reply)

    async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await _check_auth(update):
            return
        if not update.message or not update.message.voice or not update.effective_chat:
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        tg_file = await context.bot.get_file(update.message.voice.file_id)
        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = Path(tmpdir) / "voice.ogg"
            await tg_file.download_to_drive(str(ogg_path))
            try:
                transcript = _transcribe_voice_note(ogg_path)
            except RuntimeError as exc:
                await update.effective_chat.send_message(str(exc))
                return

        if not transcript:
            await update.effective_chat.send_message("Couldn't catch any speech in that voice note.")
            return

        reply = await orchestrator.chat(transcript, channel=f"{channel_name}-voice")
        await _send_reply(update, f"🎙️ _{transcript}_\n\n{reply}")

    async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        user = update.effective_user
        if not is_authorized(user.id if user else None, allowed_ids):
            await query.answer("Unauthorized", show_alert=True)
            return

        await query.answer()
        if query.data == "deploy:approve":
            if update.effective_chat:
                await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            reply = await orchestrator.chat("/approve", channel=channel_name)
            await _send_reply(update, reply)
        elif query.data == "deploy:deny":
            reply = await orchestrator.chat("/deny", channel=channel_name)
            await _send_reply(update, reply)

    app = Application.builder().token(bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


def build_telegram_bots(
    personal_orch: JarvisOrchestrator,
    code_orch: JarvisOrchestrator,
    settings: Settings,
    bot_filter: str = "all",
) -> list[tuple[str, object]]:
    """Build configured Telegram bot applications (`personal`, `code`, or both)."""
    bots: list[tuple[str, object]] = []
    if bot_filter in ("all", "personal") and settings.telegram_bot_token:
        bots.append(("personal", build_telegram_application(personal_orch, settings=settings, bot_role="personal")))
    if bot_filter in ("all", "code") and settings.telegram_code_bot_token:
        bots.append(("code", build_telegram_application(code_orch, settings=settings, bot_role="code")))

    if not bots:
        if bot_filter == "code":
            raise RuntimeError("TELEGRAM_CODE_BOT_TOKEN is not set in /home/vivek/agy/.env.")
        raise RuntimeError(
            "No Telegram bot tokens configured. Set TELEGRAM_BOT_TOKEN and/or TELEGRAM_CODE_BOT_TOKEN in /home/vivek/agy/.env."
        )
    return bots


async def _run_bots_async(bots: list[tuple[str, object]]) -> None:
    for role, app in bots:
        await app.initialize()
        await app.start()
        if app.updater is not None:
            await app.updater.start_polling()
        label = "Jarvis Code" if role == "code" else "Jarvis"
        print(f"{label} Telegram bot online.")
    try:
        await asyncio.Event().wait()
    finally:
        for _, app in bots:
            if app.updater is not None:
                await app.updater.stop()
            await app.stop()
            await app.shutdown()


def run_telegram_bots(
    personal_orch: JarvisOrchestrator,
    code_orch: JarvisOrchestrator,
    settings: Settings,
    bot_filter: str = "all",
) -> None:
    """Run one or both Telegram bots concurrently with long polling."""
    bots = build_telegram_bots(personal_orch, code_orch, settings=settings, bot_filter=bot_filter)
    try:
        asyncio.run(_run_bots_async(bots))
    except KeyboardInterrupt:
        print("\nTelegram bots stopped.")
