from types import SimpleNamespace
import asyncio

import discord
import pytest
from discord import app_commands

from lyra.cogs.administration import FEATURE_CHOICES, _defer, _respond
from lyra.bot import LyraBot


def test_gitfeed_is_an_available_slash_command_feature():
    assert "gitfeed" in {choice.value for choice in FEATURE_CHOICES}


@pytest.mark.asyncio
async def test_defer_acknowledges_interaction_before_database_work():
    calls = []

    class Response:
        def is_done(self):
            return False

        async def defer(self, **kwargs):
            calls.append(kwargs)

    assert await _defer(SimpleNamespace(response=Response()))
    assert calls == [{"ephemeral": True, "thinking": True}]


@pytest.mark.asyncio
async def test_defer_handles_expired_interaction_without_raising():
    class Response:
        def is_done(self):
            return False

        async def defer(self, **kwargs):
            raise discord.HTTPException(
                SimpleNamespace(status=404, reason="Not Found"),
                {"code": 10062, "message": "Unknown interaction"},
            )

    assert await _defer(SimpleNamespace(response=Response())) is False


@pytest.mark.asyncio
async def test_deferred_admin_response_uses_followup():
    sent = []

    class Followup:
        async def send(self, content, **kwargs):
            sent.append((content, kwargs))

    await _respond(SimpleNamespace(followup=Followup()), "Enabled gitfeed.")
    assert sent == [("Enabled gitfeed.", {"ephemeral": True})]


@pytest.mark.asyncio
async def test_global_error_handler_unwraps_and_reports_underlying_exception():
    sent = []

    class Response:
        def is_done(self):
            return False

        async def send_message(self, content, **kwargs):
            sent.append(content)

    bot = SimpleNamespace(
        logger=SimpleNamespace(error=lambda *args, **kwargs: None),
        _send_command_error=lambda interaction, message: _capture(sent, message),
    )
    interaction = SimpleNamespace(
        response=Response(),
        id=123,
        guild_id=456,
        data={"name": "lyra"},
        command=SimpleNamespace(qualified_name="lyra enable"),
    )
    error = app_commands.CommandInvokeError(
        SimpleNamespace(name="enable"), RuntimeError("database failed")
    )
    await LyraBot.on_app_command_error(bot, interaction, error)
    assert "RuntimeError" in sent[0]
    assert "123" in sent[0]


@pytest.mark.asyncio
async def test_development_command_sync_publishes_globally_and_to_guild(monkeypatch):
    commands = [SimpleNamespace(name="gitfeed")]
    calls = []

    class Tree:
        def get_commands(self):
            return commands

        async def sync(self, guild=None):
            assert commands == [SimpleNamespace(name="gitfeed")]
            calls.append(guild)
            await asyncio.sleep(0)
            return commands

        def copy_global_to(self, *, guild):
            calls.append(("copy", guild.id))

    bot = SimpleNamespace(
        tree=Tree(),
        logger=SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr("lyra.bot.get_dev_guild_id", lambda: 123)

    await LyraBot._sync_application_commands(bot)

    assert calls[0] is None
    assert calls[1] == ("copy", 123)
    assert calls[2].id == 123


async def _capture(sent, message):
    sent.append(message)
