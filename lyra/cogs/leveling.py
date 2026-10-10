from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from lyra.utils.embeds import LYRA_EMOJI


class LevelingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="level", description="Check the current level status for a member")
    @app_commands.describe(member="Member to check")
    async def level(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        await interaction.response.send_message(
            f"{LYRA_EMOJI} {target.mention} is active in the leveling system for this server"
        )


async def setup(bot):
    await bot.add_cog(LevelingCog(bot))
