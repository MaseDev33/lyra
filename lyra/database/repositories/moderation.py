from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def add_event(connection, guild_id: str, user_id: str, moderator_id: str | None, action: str, reason: str | None) -> int:
    now = utc_now()
    cursor = await connection.execute(
        "INSERT INTO moderation_events (guild_id, user_id, moderator_id, action, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(guild_id), str(user_id), moderator_id and str(moderator_id), action, reason, now),
    )
    return cursor.lastrowid


async def get_events_for_user(connection, guild_id: str, user_id: str) -> list[dict]:
    rows = await connection.execute(
        "SELECT * FROM moderation_events WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC",
        (str(guild_id), str(user_id)),
    )
    return [dict(row) for row in await rows.fetchall()]


async def clear_warnings_for_user(connection, guild_id: str, user_id: str) -> int:
    cursor = await connection.execute(
        "DELETE FROM moderation_events WHERE guild_id = ? AND user_id = ? AND action = ?",
        (str(guild_id), str(user_id), "warn"),
    )
    return cursor.rowcount
