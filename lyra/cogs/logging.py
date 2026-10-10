from __future__ import annotations

import discord
from discord.ext import commands


class LoggingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.logger.info("logging cog ready")


async def setup(bot):
    await bot.add_cog(LoggingCog(bot))
