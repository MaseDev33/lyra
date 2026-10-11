from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from lyra.services.feature_manager import DEFAULT_FEATURES, FeatureManager
from lyra.utils.embeds import LYRA_EMOJI, build_embed

FEATURE_CHOICES = [
    app_commands.Choice(name=name.replace("_", " ").title(), value=name)
    for name in DEFAULT_FEATURES
]


async def _defer(interaction: discord.Interaction) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except discord.InteractionResponded:
        return True
    except discord.HTTPException as error:
        if error.code == 40060:
            return True
        if error.code == 10062:
            return False
        raise


async def _respond(interaction: discord.Interaction, content: str) -> None:
    try:
        await interaction.followup.send(content, ephemeral=True)
    except discord.HTTPException as error:
        if error.code in (10062, 40060):
            return
        raise


class AdministrationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.feature_manager: FeatureManager = bot.feature_manager

    lyra = app_commands.Group(name="lyra", description="Lyra bot configuration")

    @lyra.command(name="settings")
    @app_commands.guild_only()
    async def lyra_settings(self, interaction: discord.Interaction):
        if not await _defer(interaction):
            return
        settings = await self.feature_manager.get_settings(str(interaction.guild_id))
        lines = [f"{name}: {'on' if value else 'off'}" for name, value in settings.items()]
        embed = build_embed("Lyra Settings", "\n".join(lines), color=0x3B82F6)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @lyra.command(name="enable")
    @app_commands.guild_only()
    @app_commands.describe(feature="Feature name to enable")
    @app_commands.choices(feature=FEATURE_CHOICES)
    async def lyra_enable(self, interaction: discord.Interaction, feature: str):
        if not await _defer(interaction):
            return
        feature_name = feature.lower()
        if feature_name not in DEFAULT_FEATURES:
            choices = ", ".join(DEFAULT_FEATURES)
            await _respond(
                interaction,
                f"{LYRA_EMOJI} Unknown feature '{feature}'. Available features: {choices}",
            )
            return
        await self.feature_manager.enable_feature(str(interaction.guild_id), feature_name)
        await _respond(
            interaction, f"{LYRA_EMOJI} Enabled {feature_name} for this server."
        )
        if feature_name == "gitfeed":
            github_cog = self.bot.get_cog("GitHubFeedsCog")
            if github_cog is not None:
                github_cog.service.request_poll()

    @lyra.command(name="disable")
    @app_commands.guild_only()
    @app_commands.describe(feature="Feature name to disable")
    @app_commands.choices(feature=FEATURE_CHOICES)
    async def lyra_disable(self, interaction: discord.Interaction, feature: str):
        if not await _defer(interaction):
            return
        feature_name = feature.lower()
        if feature_name not in DEFAULT_FEATURES:
            choices = ", ".join(DEFAULT_FEATURES)
            await _respond(
                interaction,
                f"{LYRA_EMOJI} Unknown feature '{feature}'. Available features: {choices}",
            )
            return
        await self.feature_manager.disable_feature(str(interaction.guild_id), feature_name)
        await _respond(
            interaction, f"{LYRA_EMOJI} Disabled {feature_name} for this server."
        )


async def setup(bot):
    await bot.add_cog(AdministrationCog(bot))
