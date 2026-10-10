import pytest

from lyra.database.manager import DatabaseManager
from lyra.services.leveling_service import LevelingService


@pytest.mark.asyncio
async def test_leveling_service_awards_xp_and_tracks_level(tmp_path):
    db = DatabaseManager(tmp_path / "lyra.db")
    await db.initialize()
    service = LevelingService(db)

    result = await service.award_xp("42", "87", 120)
    assert result["xp"] == 120
    assert result["level"] == 2

    level = await service.get_level("42", "87")
    assert level["xp"] == 120
    assert level["level"] == 2
