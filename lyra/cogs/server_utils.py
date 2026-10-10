from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from lyra.utils.embeds import LYRA_EMOJI


class ServerUtilsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check Lyra's response time")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"{LYRA_EMOJI} pong! latency {self.bot.latency:.2f}s")


async def setup(bot):
    await bot.add_cog(ServerUtilsCog(bot))
