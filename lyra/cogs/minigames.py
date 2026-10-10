from __future__ import annotations

import json
import random
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from lyra.utils.embeds import LYRA_EMOJI, build_embed


TRIVIA_FILE = Path(__file__).resolve().parents[1] / "data" / "trivia.json"


class RevealAnswerView(discord.ui.View):
    def __init__(self, answer: str, owner_id: int):
        super().__init__(timeout=60)
        self.answer = answer
        self.owner_id = owner_id

    @discord.ui.button(label="Reveal answer", style=discord.ButtonStyle.primary)
    async def reveal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                f"{LYRA_EMOJI} Only the person who started this round can reveal the answer.",
                ephemeral=True,
            )
            return
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            f"{LYRA_EMOJI} The answer is: **{self.answer}**", ephemeral=True
        )


class MinigamesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="trivia", description="Start a quick trivia round")
    async def trivia(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                f"{LYRA_EMOJI} Trivia can only be played in a server.", ephemeral=True
            )
            return
        if not await self.bot.feature_manager.is_enabled(str(interaction.guild_id), "minigames"):
            await interaction.response.send_message(
                f"{LYRA_EMOJI} Mini-games are currently turned off in this server.",
                ephemeral=True,
            )
            return
        with TRIVIA_FILE.open(encoding="utf-8") as trivia_file:
            questions = json.load(trivia_file)
        if not questions:
            raise RuntimeError(f"Trivia question bank is empty: {TRIVIA_FILE}")
        question = random.choice(questions)
        embed = build_embed("Trivia", question["question"])
        await interaction.response.send_message(
            embed=embed,
            view=RevealAnswerView(question["answer"], interaction.user.id),
        )


async def setup(bot):
    await bot.add_cog(MinigamesCog(bot))
