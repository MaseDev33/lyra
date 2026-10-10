import pytest

from lyra.database.manager import DatabaseManager
from lyra.services.feature_manager import FeatureManager


@pytest.mark.asyncio
async def test_feature_manager_enable_disable_and_defaults(tmp_path):
    db = DatabaseManager(tmp_path / "lyra.db")
    await db.initialize()
    manager = FeatureManager(db)

    await manager.ensure_guild("999")
    settings = await manager.get_settings("999")
    assert settings["moderation"] is True
    assert settings["minigames"] is False
    assert await manager.is_enabled("999", "minigames") is False

    await manager.disable_feature("999", "moderation")
    assert await manager.is_enabled("999", "moderation") is False

    await manager.enable_feature("999", "moderation")
    assert await manager.is_enabled("999", "moderation") is True
