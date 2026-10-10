from __future__ import annotations


class LoggingService:
    def __init__(self, database_manager):
        self.database_manager = database_manager

    async def log_event(self, guild_id: str, message: str) -> str:
        return f"event:{guild_id}:{message}"
