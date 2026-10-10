from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    load_dotenv()


def get_db_path() -> Path:
    load_environment()
    configured = os.getenv("LYRA_DB_PATH", "./data/lyra.db")
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_token() -> str:
    load_environment()
    return os.getenv("DISCORD_TOKEN", "")


def get_dev_guild_id() -> int | None:
    load_environment()
    configured = os.getenv("LYRA_DEV_GUILD_ID")
    if not configured:
        return None
    try:
        guild_id = int(configured)
    except ValueError as error:
        raise ValueError("LYRA_DEV_GUILD_ID must be a Discord server ID") from error
    if guild_id <= 0:
        raise ValueError("LYRA_DEV_GUILD_ID must be a positive Discord server ID")
    return guild_id
