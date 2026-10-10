from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def ensure_guild(connection, guild_id: str) -> None:
    guild_id = str(guild_id)
    row = await connection.execute(
        "SELECT guild_id FROM guilds WHERE guild_id = ?",
        (guild_id,),
    )
    if await row.fetchone() is None:
        now = utc_now()
        await connection.execute(
            "INSERT INTO guilds (guild_id, created_at, updated_at) VALUES (?, ?, ?)",
            (guild_id, now, now),
        )


async def get_guild_settings(connection, guild_id: str) -> dict:
    await ensure_guild(connection, guild_id)
    row = await connection.execute(
        "SELECT * FROM guilds WHERE guild_id = ?",
        (str(guild_id),),
    )
    guild = await row.fetchone()
    if guild is None:
        return {}
    return dict(guild)


async def get_feature_state(connection, guild_id: str, feature_name: str) -> bool:
    await ensure_guild(connection, guild_id)
    row = await connection.execute(
        "SELECT enabled FROM guild_features WHERE guild_id = ? AND feature_name = ?",
        (str(guild_id), feature_name),
    )
    data = await row.fetchone()
    if data is None:
        return True
    return bool(data["enabled"])


async def set_feature_state(connection, guild_id: str, feature_name: str, enabled: bool) -> None:
    await ensure_guild(connection, guild_id)
    now = utc_now()
    await connection.execute(
        "INSERT INTO guild_features (guild_id, feature_name, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, feature_name) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
        (str(guild_id), feature_name, int(enabled), now, now),
    )


async def get_all_features(connection, guild_id: str) -> dict[str, bool]:
    await ensure_guild(connection, guild_id)
    rows = await connection.execute(
        "SELECT feature_name, enabled FROM guild_features WHERE guild_id = ?",
        (str(guild_id),),
    )
    features = {row["feature_name"]: bool(row["enabled"]) for row in await rows.fetchall()}
    return features
