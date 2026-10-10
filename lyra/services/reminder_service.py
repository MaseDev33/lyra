from __future__ import annotations


class ReminderService:
    def __init__(self, database_manager):
        self.database_manager = database_manager

    async def schedule(self, *, guild_id: str, user_id: str, delay_seconds: int, message: str) -> dict:
        return {"guild_id": guild_id, "user_id": user_id, "delay_seconds": delay_seconds, "message": message}
