from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from lyra.services.feature_manager import DEFAULT_FEATURES, FeatureManager
from lyra.utils.embeds import LYRA_EMOJI, build_embed


class AdministrationCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.feature_manager: FeatureManager = bot.feature_manager

    lyra = app_commands.Group(name="lyra", description="Lyra bot configuration")

    @lyra.command(name="settings")
    async def lyra_settings(self, interaction: discord.Interaction):
        settings = await self.feature_manager.get_settings(str(interaction.guild_id))
        lines = [f"{name}: {'on' if value else 'off'}" for name, value in settings.items()]
        embed = build_embed("Lyra Settings", "\n".join(lines), color=0x3B82F6)
        await interaction.response.send_message(embed=embed)

    @lyra.command(name="enable")
    @app_commands.describe(feature="Feature name to enable")
    async def lyra_enable(self, interaction: discord.Interaction, feature: str):
        feature_name = feature.lower()
        if feature_name not in DEFAULT_FEATURES:
            choices = ", ".join(DEFAULT_FEATURES)
            await interaction.response.send_message(f"{LYRA_EMOJI} Unknown feature '{feature}'. Available features: {choices}", ephemeral=True)
            return
        await self.feature_manager.enable_feature(str(interaction.guild_id), feature_name)
        await interaction.response.send_message(f"{LYRA_EMOJI} Enabled {feature_name} for this server.")

    @lyra.command(name="disable")
    @app_commands.describe(feature="Feature name to disable")
    async def lyra_disable(self, interaction: discord.Interaction, feature: str):
        feature_name = feature.lower()
        if feature_name not in DEFAULT_FEATURES:
            choices = ", ".join(DEFAULT_FEATURES)
            await interaction.response.send_message(f"{LYRA_EMOJI} Unknown feature '{feature}'. Available features: {choices}", ephemeral=True)
            return
        await self.feature_manager.disable_feature(str(interaction.guild_id), feature_name)
        await interaction.response.send_message(f"{LYRA_EMOJI} Disabled {feature_name} for this server.")


async def setup(bot):
    await bot.add_cog(AdministrationCog(bot))
