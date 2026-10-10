import asyncio

import pytest

from lyra.database.manager import DatabaseManager
from lyra.database.repositories.guilds import get_feature_state, set_feature_state


@pytest.mark.asyncio
async def test_database_initializes_and_tracks_guilds(tmp_path):
    db_path = tmp_path / "lyra.db"
    manager = DatabaseManager(db_path)
    await manager.initialize()

    connection = await manager.connection()
    try:
        await connection.execute(
            "INSERT INTO guilds (guild_id, created_at, updated_at) VALUES (?, ?, ?)",
            ("123", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
        )
        await set_feature_state(connection, "123", "moderation", False)
        await connection.commit()

        assert await get_feature_state(connection, "123", "moderation") is False
        row = await connection.execute("SELECT COUNT(*) AS count FROM guilds")
        count = await row.fetchone()
        assert count[0] == 1
    finally:
        await connection.close()
