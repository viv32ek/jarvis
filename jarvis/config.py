"""Configuration loader for Jarvis (zero external dependencies)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    memory_dir: Path
    workspaces_dir: Path
    db_path: Path
    profile_path: Path
    agy_bin: str
    model: str
    code_model: str
    agy_mode: str
    code_agy_mode: str
    host: str
    port: int
    telegram_bot_token: str
    telegram_code_bot_token: str
    telegram_allowed_user_id: str
    home_assistant_url: str
    home_assistant_token: str

    @classmethod
    def load(cls, root_dir: Path = ROOT_DIR) -> "Settings":
        _load_dotenv(root_dir / ".env")
        data_dir = root_dir / "data"
        memory_dir = root_dir / "memory"
        workspaces_dir = root_dir / "workspaces"
        data_dir.mkdir(parents=True, exist_ok=True)
        memory_dir.mkdir(parents=True, exist_ok=True)
        workspaces_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            root_dir=root_dir,
            data_dir=data_dir,
            memory_dir=memory_dir,
            workspaces_dir=workspaces_dir,
            db_path=data_dir / "jarvis.db",
            profile_path=memory_dir / "profile.md",
            agy_bin=os.environ.get("JARVIS_AGY_BIN", "agy"),
            model=os.environ.get("JARVIS_MODEL", "gemini-3.8-flash-medium"),
            code_model=os.environ.get("JARVIS_CODE_MODEL", "gemini-4-neon-medium"),
            agy_mode=os.environ.get("JARVIS_AGY_MODE", "auto"),
            code_agy_mode=os.environ.get("JARVIS_CODE_MODE", "auto"),
            host=os.environ.get("JARVIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("JARVIS_PORT", "8765")),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_code_bot_token=os.environ.get("TELEGRAM_CODE_BOT_TOKEN", ""),
            telegram_allowed_user_id=os.environ.get("TELEGRAM_ALLOWED_USER_ID", ""),
            home_assistant_url=os.environ.get("HOME_ASSISTANT_URL", ""),
            home_assistant_token=os.environ.get("HOME_ASSISTANT_TOKEN", ""),
        )
