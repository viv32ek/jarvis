"""Free Telegram Bot bridge for Jarvis (Assistant + Code Mode + Deploy Approval buttons)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Iterable

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


def normalize_telegram_input(text: str, orchestrator: JarvisOrchestrator) -> tuple[str, str | None]:
    """Handle Telegram-specific shortcuts (`/code <task>`, `/assistant <task>`).

    Returns (prompt_to_run, optional_mode_notice).
    """
    stripped = (text or "").strip()
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

    if stripped in ("/start",):
        return "/help", None

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


def build_telegram_application(orchestrator: JarvisOrchestrator, settings: Settings | None = None):
    """Build and configure the python-telegram-bot Application."""
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
    if not cfg.telegram_bot_token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. Add it to /home/vivek/agy/.env (get a free token from @BotFather)."
        )

    allowed_ids = parse_allowed_user_ids(cfg.telegram_allowed_user_id)
    if not allowed_ids:
        logger.warning(
            "TELEGRAM_ALLOWED_USER_ID is empty! All messages will be rejected until you add your numeric Telegram user ID to .env."
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
        logger.warning("Rejected unauthorized Telegram user_id=%s username=%s", uid, getattr(user, "username", None))
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

        prompt, notice = normalize_telegram_input(update.message.text, orchestrator)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        reply = await orchestrator.chat(prompt, channel="telegram")
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

        reply = await orchestrator.chat(transcript, channel="telegram-voice")
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
            reply = await orchestrator.chat("/approve", channel="telegram")
            await _send_reply(update, reply)
        elif query.data == "deploy:deny":
            reply = await orchestrator.chat("/deny", channel="telegram")
            await _send_reply(update, reply)

    app = Application.builder().token(cfg.telegram_bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


def run_telegram_bot(orchestrator: JarvisOrchestrator, settings: Settings | None = None) -> None:
    """Run the Telegram bot with long polling (no public IP or webhook required)."""
    app = build_telegram_application(orchestrator, settings=settings)
    print(f"Jarvis Telegram bot online (mode: {orchestrator.brain.mode}, model: {orchestrator.brain.model}).")
    app.run_polling()
