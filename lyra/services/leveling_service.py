from __future__ import annotations

from lyra.database.repositories.leveling import get_level_data, set_level_data


class LevelingService:
    def __init__(self, database_manager):
        self.database_manager = database_manager

    async def award_xp(self, guild_id: str, user_id: str, xp_amount: int) -> dict:
        xp_amount = int(xp_amount)
        connection = await self.database_manager.connection()
        try:
            current = await get_level_data(connection, str(guild_id), str(user_id)) or {"xp": 0, "level": 1}
            total_xp = current["xp"] + xp_amount
            level = max(1, total_xp // 100 + 1)
            await set_level_data(connection, str(guild_id), str(user_id), total_xp, level)
            await connection.commit()
            return {"xp": total_xp, "level": level}
        finally:
            await connection.close()

    async def get_level(self, guild_id: str, user_id: str) -> dict:
        connection = await self.database_manager.connection()
        try:
            data = await get_level_data(connection, str(guild_id), str(user_id)) or {"xp": 0, "level": 1}
            return {"xp": int(data["xp"]), "level": int(data["level"]) }
        finally:
            await connection.close()
