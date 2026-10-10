import pytest

from lyra.database.manager import DatabaseManager
from lyra.services.moderation_service import ModerationService


@pytest.mark.asyncio
async def test_moderation_service_warn_user_and_fetch_history(tmp_path):
    db = DatabaseManager(tmp_path / "lyra.db")
    await db.initialize()
    service = ModerationService(db)

    warning_id = await service.warn_user("guild-1", "user-1", "mod-1", "spamming")
    warnings = await service.get_warnings("guild-1", "user-1")

    assert warning_id > 0
    assert warnings[0]["action"] == "warn"
    assert warnings[0]["reason"] == "spamming"
