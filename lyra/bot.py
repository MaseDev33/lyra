from __future__ import annotations

import asyncio
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands, tasks

from lyra.config import get_dev_guild_id, get_token
from lyra.services.feature_manager import FeatureManager
from lyra.database.manager import DatabaseManager
from lyra.utils.status import ROTATING_STATUS_MESSAGES


class LyraBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="/", intents=intents, help_command=None)
        self.logger = logging.getLogger("lyra")
        self.database_manager = DatabaseManager()
        self.feature_manager = FeatureManager(self.database_manager)
        self.tree.on_error = self.on_app_command_error
        self._command_sync_task: asyncio.Task | None = None

    async def setup_hook(self) -> None:
        self.logger.info("Initializing database")
        await self.database_manager.initialize()
        self.logger.info("Loading bot extensions")
        await self.load_extension("lyra.cogs.administration")
        await self.load_extension("lyra.cogs.logging")
        await self.load_extension("lyra.cogs.moderation")
        await self.load_extension("lyra.cogs.server_utils")
        await self.load_extension("lyra.cogs.leveling")
        await self.load_extension("lyra.cogs.minigames")

    async def _sync_application_commands(self) -> None:
        dev_guild_id = get_dev_guild_id()
        try:
            if dev_guild_id is not None:
                guild = discord.Object(id=dev_guild_id)
                global_commands = self.tree.get_commands()

                # Remove previously registered global copies before publishing
                # the development-only guild commands.
                self.tree.clear_commands(guild=None)
                try:
                    await asyncio.wait_for(self.tree.sync(), timeout=30)
                finally:
                    for command in global_commands:
                        self.tree.add_command(command)
                self.tree.copy_global_to(guild=guild)
                synced = await asyncio.wait_for(self.tree.sync(guild=guild), timeout=30)
                self.logger.info(
                    "Removed global command copies and synced %d commands to development guild %s",
                    len(synced),
                    dev_guild_id,
                )
            else:
                synced = await asyncio.wait_for(self.tree.sync(), timeout=30)
                self.logger.info("Synced %d global application commands", len(synced))
        except asyncio.TimeoutError:
            self.logger.error("Application command sync timed out; bot remains connected")
        except (discord.HTTPException, app_commands.CommandSyncFailure):
            self.logger.exception("Application command sync failed; bot remains connected")

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        if not self.rotate_presence.is_running():
            self.rotate_presence.start()
        if self._command_sync_task is None or self._command_sync_task.done():
            self._command_sync_task = asyncio.create_task(
                self._sync_application_commands(),
                name="lyra-application-command-sync",
            )

    @tasks.loop(seconds=7)
    async def rotate_presence(self) -> None:
        message = random.choice(ROTATING_STATUS_MESSAGES)
        await self.change_presence(
            activity=discord.CustomActivity(
                name=message,
                emoji=discord.PartialEmoji(name="Lyra", id=1558580804874010685),
            ),
            status=discord.Status.idle,
        )

    @rotate_presence.before_loop
    async def before_rotate_presence(self) -> None:
        await self.wait_until_ready()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            from lyra.utils.embeds import LYRA_EMOJI

            message = f"{LYRA_EMOJI} You don't have permission to use that command."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        self.logger.error(
            "Application command failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        from lyra.utils.embeds import LYRA_EMOJI

        message = f"{LYRA_EMOJI} That command couldn't be completed. Please try again later."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def close(self):
        self.rotate_presence.cancel()
        if self._command_sync_task is not None and not self._command_sync_task.done():
            self._command_sync_task.cancel()
        await super().close()


async def create_bot() -> LyraBot:
    bot = LyraBot()
    await bot.setup_hook()
    return bot


def main() -> None:
    token = get_token()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot = LyraBot()
    bot.run(token)
