from __future__ import annotations

import asyncio
import inspect
import logging
import os
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
        await self.load_extension("lyra.cogs.github_feeds")
        await self.load_extension("lyra.cogs.logging")
        await self.load_extension("lyra.cogs.moderation")
        await self.load_extension("lyra.cogs.server_utils")
        await self.load_extension("lyra.cogs.leveling")
        await self.load_extension("lyra.cogs.minigames")
        command_names = sorted(command.name for command in self.tree.get_commands())
        if "gitfeed" not in command_names or self.get_cog("GitHubFeedsCog") is None:
            raise RuntimeError(
                "GitHub feeds failed to load; refusing to start with an incomplete command tree"
            )
        self.logger.warning(
            "LYRA_RUNTIME_READY pid=%s source=%s commands=%s",
            os.getpid(),
            inspect.getfile(type(self)),
            ",".join(command_names),
        )
        if not await self._sync_application_commands():
            raise RuntimeError("Discord application commands could not be synchronized")
        self.logger.warning("LYRA_COMMAND_SYNC_READY")

    async def _sync_application_commands(self) -> bool:
        dev_guild_id = get_dev_guild_id()
        try:
            synced = await asyncio.wait_for(self.tree.sync(), timeout=90)
            self.logger.info(
                "Synced %d global application commands: %s",
                len(synced),
                ", ".join(command.name for command in self.tree.get_commands()),
            )
            if dev_guild_id is not None:
                guild = discord.Object(id=dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await asyncio.wait_for(self.tree.sync(guild=guild), timeout=90)
                self.logger.info(
                    "Synced %d commands to development guild %s",
                    len(synced),
                    dev_guild_id,
                )
            return True
        except asyncio.TimeoutError:
            self.logger.error("Application command sync timed out")
        except (discord.HTTPException, app_commands.CommandSyncFailure):
            self.logger.exception("Application command sync failed")
        return False

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        if not self.rotate_presence.is_running():
            self.rotate_presence.start()

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
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original
        if isinstance(error, app_commands.CommandNotFound):
            interaction_data = interaction.data
            command_name = (
                interaction_data.get("name", "unknown")
                if isinstance(interaction_data, dict)
                else "unknown"
            )
            self.logger.warning(
                "Discord sent unregistered command %s (interaction_id=%s guild_id=%s); scheduling command sync",
                command_name,
                interaction.id,
                interaction.guild_id,
            )
            if self._command_sync_task is None or self._command_sync_task.done():
                self._command_sync_task = asyncio.create_task(
                    self._sync_application_commands(),
                    name="lyra-application-command-sync",
                )
            await self._send_command_error(
                interaction,
                f"That command isn't registered in Lyra yet. I'm refreshing slash commands; "
                f"please try again shortly. Reference: `{interaction.id}`.",
            )
            return
        if isinstance(error, app_commands.MissingPermissions):
            from lyra.utils.embeds import LYRA_EMOJI

            message = f"{LYRA_EMOJI} You don't have permission to use that command."
            await self._send_command_error(interaction, message)
            return
        self.logger.error(
            "Application command failed (command=%s raw_data=%r interaction_id=%s guild_id=%s)",
            getattr(interaction.command, "qualified_name", None),
            interaction.data,
            interaction.id,
            interaction.guild_id,
            exc_info=(type(error), error, error.__traceback__),
        )
        from lyra.utils.embeds import LYRA_EMOJI

        message = (
            f"{LYRA_EMOJI} That command failed ({type(error).__name__}). "
            f"Reference: `{interaction.id}`. Check Lyra's log for the matching traceback."
        )
        await self._send_command_error(interaction, message)

    async def _send_command_error(
        self, interaction: discord.Interaction, message: str
    ) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.InteractionResponded:
            try:
                await interaction.followup.send(message, ephemeral=True)
            except discord.HTTPException as error:
                if error.code not in (10062, 40060):
                    raise
        except discord.HTTPException as response_error:
            if response_error.code == 40060:
                try:
                    await interaction.followup.send(message, ephemeral=True)
                except discord.HTTPException as followup_error:
                    if followup_error.code not in (10062, 40060):
                        raise
            elif response_error.code != 10062:
                raise

    async def close(self):
        self.rotate_presence.cancel()
        if self._command_sync_task is not None and not self._command_sync_task.done():
            self._command_sync_task.cancel()
        github_feeds = self.get_cog("GitHubFeedsCog")
        if github_feeds is not None:
            await github_feeds.service.close()
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
