from __future__ import annotations

from lyra.database.repositories.moderation import (
    add_event,
    clear_warnings_for_user,
    get_events_for_user,
)


class ModerationService:
    def __init__(self, database_manager):
        self.database_manager = database_manager

    async def warn_user(self, guild_id: str, user_id: str, moderator_id: str | None, reason: str | None = None) -> int:
        connection = await self.database_manager.connection()
        try:
            event_id = await add_event(connection, str(guild_id), str(user_id), moderator_id, "warn", reason)
            await connection.commit()
            return int(event_id)
        finally:
            await connection.close()

    async def get_warnings(self, guild_id: str, user_id: str) -> list[dict]:
        connection = await self.database_manager.connection()
        try:
            return await get_events_for_user(connection, str(guild_id), str(user_id))
        finally:
            await connection.close()

    async def record_action(
        self,
        guild_id: str,
        user_id: str,
        moderator_id: str,
        action: str,
        reason: str | None = None,
    ) -> int:
        connection = await self.database_manager.connection()
        try:
            event_id = await add_event(
                connection, str(guild_id), str(user_id), str(moderator_id), action, reason
            )
            await connection.commit()
            return int(event_id)
        finally:
            await connection.close()

    async def clear_warnings(self, guild_id: str, user_id: str) -> int:
        connection = await self.database_manager.connection()
        try:
            count = await clear_warnings_for_user(connection, str(guild_id), str(user_id))
            await connection.commit()
            return count
        finally:
            await connection.close()
