from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def get_level_data(connection, guild_id: str, user_id: str) -> dict | None:
    row = await connection.execute(
        "SELECT guild_id, user_id, xp, level FROM user_levels WHERE guild_id = ? AND user_id = ?",
        (str(guild_id), str(user_id)),
    )
    result = await row.fetchone()
    if result is None:
        return None
    return dict(result)


async def set_level_data(connection, guild_id: str, user_id: str, xp: int, level: int) -> None:
    now = utc_now()
    await connection.execute(
        "INSERT INTO user_levels (guild_id, user_id, xp, level, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level, updated_at = excluded.updated_at",
        (str(guild_id), str(user_id), int(xp), int(level), now, now),
    )
