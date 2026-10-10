from __future__ import annotations

from lyra.database.repositories.guilds import get_all_features, get_feature_state, set_feature_state

DEFAULT_FEATURES = {
    "moderation": True,
    "logging": True,
    "server_utils": True,
    "leveling": True,
    "minigames": False,
}


class FeatureManager:
    def __init__(self, database_manager):
        self.database_manager = database_manager

    async def ensure_guild(self, guild_id: str) -> None:
        connection = await self.database_manager.connection()
        try:
            await connection.execute("INSERT OR IGNORE INTO guilds (guild_id, created_at, updated_at) VALUES (?, ?, ?)", (str(guild_id), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()))
            for feature_name, enabled in DEFAULT_FEATURES.items():
                await connection.execute(
                    "INSERT OR IGNORE INTO guild_features (guild_id, feature_name, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (str(guild_id), feature_name, int(enabled), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()),
                )
            await connection.commit()
        finally:
            await connection.close()

    async def get_settings(self, guild_id: str) -> dict[str, bool]:
        connection = await self.database_manager.connection()
        try:
            features = await get_all_features(connection, str(guild_id))
            for name, enabled in DEFAULT_FEATURES.items():
                features.setdefault(name, enabled)
            return features
        finally:
            await connection.close()

    async def is_enabled(self, guild_id: str, feature_name: str) -> bool:
        await self.ensure_guild(guild_id)
        connection = await self.database_manager.connection()
        try:
            return await get_feature_state(connection, str(guild_id), feature_name)
        finally:
            await connection.close()

    async def enable_feature(self, guild_id: str, feature_name: str) -> bool:
        feature_name = feature_name.lower()
        if feature_name not in DEFAULT_FEATURES:
            raise ValueError(f"Unsupported feature: {feature_name}")
        connection = await self.database_manager.connection()
        try:
            await set_feature_state(connection, str(guild_id), feature_name, True)
            await connection.commit()
            return True
        finally:
            await connection.close()

    async def disable_feature(self, guild_id: str, feature_name: str) -> bool:
        feature_name = feature_name.lower()
        if feature_name not in DEFAULT_FEATURES:
            raise ValueError(f"Unsupported feature: {feature_name}")
        connection = await self.database_manager.connection()
        try:
            await set_feature_state(connection, str(guild_id), feature_name, False)
            await connection.commit()
            return True
        finally:
            await connection.close()
