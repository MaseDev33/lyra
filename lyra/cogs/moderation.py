from __future__ import annotations

from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from lyra.services.moderation_service import ModerationService
from lyra.utils.embeds import LYRA_EMOJI


class ModerationCog(commands.Cog):
    moderation = app_commands.Group(name="moderation", description="Moderation tools")

    def __init__(self, bot):
        self.bot = bot
        self.service = ModerationService(bot.database_manager)

    async def _respond(
        self, interaction: discord.Interaction, message: str, *, ephemeral: bool = False
    ) -> None:
        branded_message = (
            message if message.startswith(LYRA_EMOJI) else f"{LYRA_EMOJI} {message}"
        )
        if interaction.response.is_done():
            try:
                await interaction.followup.send(branded_message, ephemeral=ephemeral)
            except discord.HTTPException as error:
                if error.code not in (10062, 40060):
                    raise
        else:
            await interaction.response.send_message(branded_message, ephemeral=ephemeral)

    async def _defer(self, interaction: discord.Interaction) -> bool:
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

    async def _check_available(self, interaction: discord.Interaction) -> bool:
        if not await self._defer(interaction):
            return False
        if interaction.guild is None:
            await self._respond(
                interaction,
                "Moderation commands can only be used in a server.", ephemeral=True
            )
            return False
        if not await self.bot.feature_manager.is_enabled(str(interaction.guild_id), "moderation"):
            await self._respond(
                interaction,
                "Moderation is currently turned off in this server.", ephemeral=True
            )
            return False
        return True

    @moderation.command(name="warn", description="Warn a member and record it")
    @app_commands.describe(member="Member to warn", reason="Reason for the warning")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "no reason provided",
    ) -> None:
        if not await self._check_available(interaction):
            return
        await self.service.warn_user(
            str(interaction.guild_id), str(member.id), str(interaction.user.id), reason
        )
        await self._respond(
            interaction,
            f"{member.mention} has been warned. Reason: {reason}"
        )

    @moderation.command(name="warnings", description="View a member's warning history")
    @app_commands.describe(member="Member whose warnings to view")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await self._check_available(interaction):
            return
        events = await self.service.get_warnings(str(interaction.guild_id), str(member.id))
        warnings = [event for event in events if event["action"] == "warn"]
        if not warnings:
            await self._respond(
                interaction,
                f"{member.display_name} has no recorded warnings.", ephemeral=True
            )
            return
        lines = [
            f"• {event['created_at'][:10]} — {event['reason'] or 'no reason provided'}"
            for event in warnings[:10]
        ]
        await self._respond(
            interaction,
            f"Warnings for {member.mention}:\n" + "\n".join(lines), ephemeral=True
        )

    @moderation.command(name="clear-warnings", description="Clear a member's warning history")
    @app_commands.describe(member="Member whose warnings to clear")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear_warnings(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        if not await self._check_available(interaction):
            return
        count = await self.service.clear_warnings(str(interaction.guild_id), str(member.id))
        await self._respond(
            interaction,
            f"Cleared {count} warning(s) for {member.mention}.", ephemeral=True
        )

    @moderation.command(name="timeout", description="Timeout a member")
    @app_commands.describe(
        member="Member to timeout",
        minutes="Timeout duration in minutes (1–40320)",
        reason="Reason for the timeout",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        minutes: app_commands.Range[int, 1, 40320],
        reason: str = "no reason provided",
    ) -> None:
        if not await self._check_available(interaction):
            return
        if member.id == interaction.user.id or member.id == self.bot.user.id:
            await self._respond(
                interaction,
                "You can't timeout yourself or Lyra.", ephemeral=True
            )
            return
        try:
            await member.timeout(timedelta(minutes=minutes), reason=reason)
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't timeout that member. Check my permissions and role position.",
                ephemeral=True,
            )
            return
        await self.service.record_action(
            str(interaction.guild_id), str(member.id), str(interaction.user.id), "timeout", reason
        )
        await self._respond(
            interaction,
            f"Timed out {member.mention} for {minutes} minute(s). Reason: {reason}"
        )

    @moderation.command(name="untimeout", description="Remove a member's timeout")
    @app_commands.describe(member="Member to remove the timeout from")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await self._check_available(interaction):
            return
        try:
            await member.timeout(None, reason=f"Timeout removed by {interaction.user}")
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't update that member. Check my permissions and role position.",
                ephemeral=True,
            )
            return
        await self.service.record_action(
            str(interaction.guild_id),
            str(member.id),
            str(interaction.user.id),
            "untimeout",
        )
        await self._respond(interaction, f"Removed {member.mention}'s timeout.")

    @moderation.command(name="kick", description="Kick a member from this server")
    @app_commands.describe(member="Member to kick", reason="Reason for the kick")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "no reason provided",
    ) -> None:
        if not await self._check_available(interaction):
            return
        try:
            await member.kick(reason=f"{reason} (by {interaction.user})")
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't kick that member. Check my permissions and role position.",
                ephemeral=True,
            )
            return
        await self.service.record_action(
            str(interaction.guild_id), str(member.id), str(interaction.user.id), "kick", reason
        )
        await self._respond(interaction, f"Kicked {member.mention}. Reason: {reason}")

    @moderation.command(name="ban", description="Ban a member from this server")
    @app_commands.describe(member="Member to ban", reason="Reason for the ban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "no reason provided",
    ) -> None:
        if not await self._check_available(interaction):
            return
        try:
            await member.ban(reason=f"{reason} (by {interaction.user})")
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't ban that member. Check my permissions and role position.",
                ephemeral=True,
            )
            return
        await self.service.record_action(
            str(interaction.guild_id), str(member.id), str(interaction.user.id), "ban", reason
        )
        await self._respond(interaction, f"Banned {member.mention}. Reason: {reason}")

    @moderation.command(name="unban", description="Unban a user by their Discord ID")
    @app_commands.describe(user_id="Discord user ID to unban", reason="Reason for the unban")
    @app_commands.checks.has_permissions(ban_members=True)
    async def unban(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str = "no reason provided",
    ) -> None:
        if not await self._check_available(interaction):
            return
        if not user_id.isdecimal():
            await self._respond(
                interaction,
                "Enter a valid Discord user ID.", ephemeral=True
            )
            return
        try:
            user = await interaction.guild.fetch_ban(discord.Object(id=int(user_id)))
            await interaction.guild.unban(user.user, reason=f"{reason} (by {interaction.user})")
        except discord.NotFound:
            await self._respond(
                interaction,
                "That user isn't currently banned.", ephemeral=True
            )
            return
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't unban that user. Check my Ban Members permission.", ephemeral=True
            )
            return
        await self.service.record_action(
            str(interaction.guild_id), user_id, str(interaction.user.id), "unban", reason
        )
        await self._respond(
            interaction,
            f"Unbanned <@{user_id}>. Reason: {reason}"
        )

    @moderation.command(name="purge", description="Delete recent messages in this channel")
    @app_commands.describe(amount="Number of recent messages to delete (1–100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(
        self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]
    ) -> None:
        if not await self._check_available(interaction):
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await self._respond(
                interaction,
                "Purge is only available in a server text channel.", ephemeral=True
            )
            return
        try:
            deleted = await interaction.channel.purge(limit=amount)
        except discord.Forbidden:
            await self._respond(
                interaction,
                "I can't delete messages here. Check my Manage Messages permission.",
                ephemeral=True,
            )
            return
        await self._respond(
            interaction, f"Deleted {len(deleted)} message(s).", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(ModerationCog(bot))
