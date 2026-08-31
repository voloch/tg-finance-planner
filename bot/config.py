"""Configuration loading.

Resolves .env from the project root first, then one directory up, so the
bot works whether the .env sits alongside the project or in a shared parent
folder (as it does today: /home/alex/Projects/.env).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_env_file() -> Path | None:
    for candidate in (PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"):
        if candidate.exists():
            return candidate
    return None


@dataclass
class Config:
    telegram_token: str
    openrouter_token: str
    openrouter_model: str
    openrouter_vision_model: str
    allowed_user_ids: set[int] = field(default_factory=set)
    tz_name: str = "America/Sao_Paulo"
    db_path: Path = PROJECT_ROOT / "data" / "finance.db"

    def is_allowed(self, user_id: int) -> bool:
        # Empty allowlist means "not configured yet" -- allow everyone but
        # the caller is expected to nag the user to lock it down via /whoami.
        if not self.allowed_user_ids:
            return True
        return user_id in self.allowed_user_ids


def load_config() -> Config:
    env_path = _find_env_file()
    if env_path is not None:
        load_dotenv(env_path)
        print(f"[config] loaded env from {env_path}")
    else:
        print("[config] no .env file found next to the project or its parent; "
              "relying on already-exported environment variables")

    try:
        telegram_token = os.environ["TELEGRAM_TOKEN"]
        openrouter_token = os.environ["OPENROUTER_TOKEN"]
    except KeyError as exc:
        raise SystemExit(
            f"Missing required environment variable {exc}. "
            f"Copy .env.example to .env (or check the parent-directory .env) and fill it in."
        ) from exc

    openrouter_model = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash-0731")
    openrouter_vision_model = os.environ.get(
        "OPENROUTER_VISION_MODEL", "deepseek/deepseek-v4-flash-vision-exp"
    )

    allowed_raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
    allowed_user_ids = {int(x) for x in allowed_raw.split(",") if x.strip()} if allowed_raw else set()
    if not allowed_user_ids:
        print("[config] WARNING: ALLOWED_USER_IDS is empty -- anyone who finds this bot can use it. "
              "Send /whoami once running, then set ALLOWED_USER_IDS in .env and restart.")

    tz_name = os.environ.get("TZ", "America/Sao_Paulo")

    db_path = Path(os.environ.get("DB_PATH", "data/finance.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    return Config(
        telegram_token=telegram_token,
        openrouter_token=openrouter_token,
        openrouter_model=openrouter_model,
        openrouter_vision_model=openrouter_vision_model,
        allowed_user_ids=allowed_user_ids,
        tz_name=tz_name,
        db_path=db_path,
    )
