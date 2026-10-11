from __future__ import annotations

import logging
import re
import sqlite3

import discord
from discord import app_commands
from discord.ext import commands

from lyra.config import get_github_poll_interval, load_environment
from lyra.database.repositories import github_feeds as feed_repository
from lyra.services.github_api import GitHubAPIError, GitHubClient, USER_ACTIVITY_TYPES
from lyra.services.github_feed_service import GitHubFeedService
from lyra.utils.embeds import LYRA_EMOJI, PRIMARY_BLUE, build_embed

LOGGER = logging.getLogger("lyra.github")
ACTIVITY_CHOICES = [
    app_commands.Choice(name="All supported activity", value="all"),
    app_commands.Choice(name="Commits", value="commits"),
    app_commands.Choice(name="Releases", value="releases"),
    app_commands.Choice(name="Issues", value="issues"),
    app_commands.Choice(name="Pull requests", value="pull_requests"),
    app_commands.Choice(name="Stars", value="stars"),
]
ACTIVITY_LABELS = {
    "commits": "commits",
    "releases": "releases",
    "issues": "issues",
    "pull_requests": "pull requests",
    "stars": "stars",
}
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


async def _defer_interaction(interaction: discord.Interaction) -> bool:
    if interaction.response.is_done():
        return True
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        return True
    except discord.InteractionResponded:
        return True
    except discord.HTTPException as error:
        if error.code == 40060:
            LOGGER.debug("GitHub command interaction was already acknowledged")
            return True
        if error.code == 10062:
            LOGGER.debug("GitHub command interaction expired before acknowledgement")
            return False
        raise


async def _send_interaction(
    interaction: discord.Interaction, content: str | None = None, **kwargs
) -> None:
    if content is not None:
        kwargs["content"] = content
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.InteractionResponded:
        try:
            await interaction.followup.send(**kwargs)
        except discord.HTTPException as followup_error:
            if followup_error.code in (10062, 40060):
                LOGGER.debug(
                    "GitHub interaction response was unavailable (%s)",
                    followup_error.code,
                )
                return
            raise
    except discord.HTTPException as error:
        if error.code == 40060:
            try:
                await interaction.followup.send(**kwargs)
            except discord.HTTPException as followup_error:
                if followup_error.code in (10062, 40060):
                    LOGGER.debug(
                        "GitHub interaction response was unavailable (%s)",
                        followup_error.code,
                    )
                    return
                raise
        elif error.code == 10062:
            LOGGER.debug("GitHub interaction expired before response")
        else:
            LOGGER.exception(
                "GitHub response failed (interaction_id=%s, status=%s, code=%s)",
                interaction.id,
                error.status,
                error.code,
            )


async def _edit_interaction(interaction: discord.Interaction, **kwargs) -> None:
    try:
        await interaction.response.edit_message(**kwargs)
    except discord.InteractionResponded:
        try:
            await interaction.edit_original_response(**kwargs)
        except discord.HTTPException as edit_error:
            if edit_error.code in (10062, 40060):
                LOGGER.debug("GitHub component interaction could not be edited (%s)", edit_error.code)
                return
            raise
    except discord.HTTPException as error:
        if error.code == 40060:
            try:
                await interaction.edit_original_response(**kwargs)
            except discord.HTTPException as edit_error:
                if edit_error.code in (10062, 40060):
                    LOGGER.debug("GitHub component interaction could not be edited (%s)", edit_error.code)
                    return
                raise
        elif error.code == 10062:
            LOGGER.debug("GitHub component interaction expired before edit")
        else:
            raise


def _activities_text(activities: list[str]) -> str:
    return ", ".join(ACTIVITY_LABELS.get(item, item) for item in activities)


def _error_response(error: Exception, interaction_id: int) -> str:
    if isinstance(error, GitHubAPIError):
        reason = str(error)
    elif isinstance(error, discord.HTTPException):
        reason = f"Discord API error {error.status} (code {error.code})"
    else:
        reason = f"internal error: {type(error).__name__}"
    return (
        f"{LYRA_EMOJI} GitHub command failed ({reason}). "
        f"Reference: `{interaction_id}`. The bot log contains the traceback."
    )


async def _report_command_error(
    interaction: discord.Interaction, error: Exception, command_name: str
) -> None:
    LOGGER.error(
        "GitHub %s failed (interaction_id=%s guild_id=%s)",
        command_name,
        interaction.id,
        interaction.guild_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    await _send_interaction(
        interaction,
        _error_response(error, interaction.id),
        ephemeral=True,
    )


def _feed_target(feed: dict) -> str:
    return feed["target"]


def _feed_detail(feed: dict) -> str:
    status = feed["status"].replace("_", " ")
    return (
        f"**Target:** `{_feed_target(feed)}`\n"
        f"**Activities:** {_activities_text(feed['activities'])}\n"
        f"**Channel:** <#{feed['channel_id']}>\n"
        f"**Status:** {status}\n"
        f"**Last successful poll:** {feed['last_successful_poll'] or 'Not yet polled'}\n"
        f"**Last activity:** {feed['last_activity_at'] or 'None detected'}"
    )


class GitHubView(discord.ui.View):
    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        await _report_command_error(interaction, error, "confirmation")


class FeedConfirmationView(GitHubView):
    def __init__(self, cog: "GitHubFeedsCog", owner_id: int, action: str, feed: dict | None = None):
        super().__init__(timeout=120)
        self.cog = cog
        self.owner_id = owner_id
        self.action = action
        self.feed = feed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _send_interaction(
                interaction,
                "Only the administrator who started this request can confirm it.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.action == "remove":
            assert self.feed is not None
            removed = await self.cog._remove_feed(
                self.feed["feed_id"], str(interaction.guild_id)
            )
            message = (
                f"{LYRA_EMOJI} Feed `{self.feed['feed_id']}` has been removed."
                if removed
                else f"{LYRA_EMOJI} That feed was already removed."
            )
            await _edit_interaction(interaction, content=message, embed=None, view=None)
            return

        try:
            feed_id = await self.cog._save_pending_feed(self.feed)
        except (GitHubAPIError, sqlite3.IntegrityError) as error:
            message = str(error) if isinstance(error, GitHubAPIError) else "An equivalent feed already exists."
            await _edit_interaction(
                interaction,
                content=f"{LYRA_EMOJI} {message}", embed=None, view=None
            )
            return
        self.cog.service.request_poll()
        await _edit_interaction(
            interaction,
            content=(
                f"{LYRA_EMOJI} you're all set — i'll keep an eye on that "
                f"{'repo' if self.feed['target_type'] == 'repository' else 'user'} "
                f"and post new activity in <#{self.feed['channel_id']}>. "
                f"Feed ID: `{feed_id}`"
            ),
            embed=build_embed(
                "GitHub feed started",
                f"**Target:** `{self.feed['target']}`\n"
                f"**Activity:** {_activities_text(self.feed['activities'])}\n"
                f"**Channel:** <#{self.feed['channel_id']}>\n"
                f"**Feed ID:** `{feed_id}`\n\nMonitoring has started.",
                color=PRIMARY_BLUE,
            ),
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _edit_interaction(
            interaction,
            content=f"{LYRA_EMOJI} No changes were made.", embed=None, view=None
        )


class FeedListView(GitHubView):
    def __init__(self, feeds: list[dict], owner_id: int):
        super().__init__(timeout=180)
        self.feeds = feeds
        self.owner_id = owner_id
        self.page = 0
        self.page_size = 10
        self._update_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _send_interaction(
                interaction,
                "Only the person who opened this feed list can navigate it.",
                ephemeral=True,
            )
            return False
        return True

    def _update_buttons(self):
        self.previous.disabled = self.page == 0
        self.next.disabled = (self.page + 1) * self.page_size >= len(self.feeds)

    def make_embed(self) -> discord.Embed:
        start = self.page * self.page_size
        current = self.feeds[start : start + self.page_size]
        embed = build_embed(
            f"GitHub feeds ({len(self.feeds)})",
            f"Page {self.page + 1}/{max(1, (len(self.feeds) + self.page_size - 1) // self.page_size)}",
        )
        for feed in current:
            embed.add_field(
                name=f"Feed `{feed['feed_id']}` — {feed['target']}",
                value=_feed_detail(feed),
                inline=False,
            )
        if not current:
            embed.description = "No GitHub feeds are configured for this server."
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await _edit_interaction(interaction, embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await _edit_interaction(interaction, embed=self.make_embed(), view=self)


class GitHubFeedsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        load_environment()
        try:
            interval = get_github_poll_interval()
        except ValueError as error:
            LOGGER.warning("%s; using the 300-second default", error)
            interval = 300
        self.client = GitHubClient()
        self.service = GitHubFeedService(
            bot, bot.database_manager, bot.feature_manager, self.client, interval
        )

    async def cog_load(self) -> None:
        await self.service.start()

    async def cog_unload(self) -> None:
        await self.service.close()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CommandInvokeError):
            error = error.original
        command_name = getattr(interaction.command, "qualified_name", "gitfeed")
        await _report_command_error(interaction, error, command_name)

    gitfeed = app_commands.Group(name="gitfeed", description="Manage GitHub activity feeds")

    @gitfeed.command(name="add", description="Subscribe this server to GitHub activity")
    @app_commands.describe(
        target="GitHub username or repository in owner/repo format",
        channel="Text channel for feed updates",
        activity="Activity type to monitor",
    )
    @app_commands.choices(activity=ACTIVITY_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def add_feed(
        self,
        interaction: discord.Interaction,
        target: str,
        channel: discord.TextChannel,
        activity: app_commands.Choice[str] | None = None,
    ):
        if not await _defer_interaction(interaction):
            return
        if not self._is_manager(interaction):
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} You need Manage Server permission to configure feeds.",
                ephemeral=True,
            )
            return
        if not self.client.configured:
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} GitHub integration is unavailable because `GITHUB_TOKEN` is not configured.",
                ephemeral=True,
            )
            return
        if not self._can_post(interaction, channel):
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} I need permission to send messages and embeds in {channel.mention}.",
                ephemeral=True,
            )
            return

        target = target.strip()
        if "/" in target:
            pieces = target.split("/")
            if (
                len(pieces) != 2
                or not USERNAME_PATTERN.fullmatch(pieces[0])
                or not REPOSITORY_PATTERN.fullmatch(pieces[1])
            ):
                await _send_interaction(
                    interaction,
                    f"{LYRA_EMOJI} Use a repository in `owner/repo` format.",
                    ephemeral=True,
                )
                return
            target_type = "repository"
            target = f"{pieces[0]}/{pieces[1]}"
        else:
            if not USERNAME_PATTERN.fullmatch(target):
                await _send_interaction(
                    interaction,
                    f"{LYRA_EMOJI} Enter a valid GitHub username or `owner/repo`.",
                    ephemeral=True,
                )
                return
            target_type = "user"

        selected = activity.value if activity else "all"
        selected_activities = list(USER_ACTIVITY_TYPES) if selected == "all" else [selected]
        try:
            metadata = await self.client.validate_target(
                target_type, target, selected_activities
            )
        except GitHubAPIError as error:
            message = str(error)
            if error.status == 404:
                message = "That GitHub target was not found or is not accessible to Lyra's GitHub account."
            await _send_interaction(interaction, content=f"{LYRA_EMOJI} {message}", ephemeral=True)
            return

        if target_type == "repository":
            target = metadata.get("full_name") or target
            is_private = bool(metadata.get("private"))
        else:
            is_private = False

        duplicate = await self._find_duplicate(
            str(interaction.guild_id), target_type, target, selected_activities
        )
        if duplicate:
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} This server already has an equivalent feed: `{duplicate}`.",
                ephemeral=True,
            )
            return

        pending = {
            "guild_id": str(interaction.guild_id),
            "channel_id": str(channel.id),
            "target_type": target_type,
            "target": target,
            "activities": selected_activities,
            "is_private": is_private,
        }
        activity_summary = _activities_text(selected_activities)
        if target_type == "user":
            note = (
                "User feeds use GitHub's public events endpoint and only report "
                "public activity performed by that user. Events may be delayed or unavailable."
            )
        else:
            note = "Repository feeds report activity occurring in this repository."
        if is_private:
            note = (
                "⚠️ This is a private repository. Its activity will be visible to everyone "
                "who can read the destination channel. Confirm only if this server and "
                "channel are authorized to receive it."
            )
        embed = build_embed(
            "Confirm GitHub feed",
            f"**Target:** `{target}`\n"
            f"**Activity:** {activity_summary}\n"
            f"**Channel:** {channel.mention}\n\n{note}",
            color=PRIMARY_BLUE,
        )
        if is_private:
            await _send_interaction(
                interaction,
                embed=embed,
                view=FeedConfirmationView(self, interaction.user.id, "add", pending),
                ephemeral=True,
            )
            return
        try:
            feed_id = await self._save_pending_feed(pending)
        except sqlite3.IntegrityError:
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} This server already has an equivalent feed.",
                ephemeral=True,
            )
            return
        await _send_interaction(
            interaction,
            f"{LYRA_EMOJI} you're all set — i'll keep an eye on that "
            f"{'repo' if target_type == 'repository' else 'user'} and post new activity here "
            f"({channel.mention}). Feed ID: `{feed_id}`",
            embed=build_embed(
                "GitHub feed started",
                f"**Target:** `{target}`\n"
                f"**Activity:** {activity_summary}\n"
                f"**Channel:** {channel.mention}\n"
                f"**Feed ID:** `{feed_id}`\n\nMonitoring has started.",
                color=PRIMARY_BLUE,
            ),
            ephemeral=True,
        )
        self.service.request_poll()

    @gitfeed.command(name="remove", description="Review and remove one of this server's feeds")
    @app_commands.describe(feed_id="Identifier of the feed to remove")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def remove_feed(self, interaction: discord.Interaction, feed_id: str):
        if not await _defer_interaction(interaction):
            return
        if not self._is_manager(interaction):
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} You need Manage Server permission to remove feeds.",
                ephemeral=True,
            )
            return
        feeds = await self._list_feeds(str(interaction.guild_id))
        selected = next((feed for feed in feeds if feed["feed_id"] == feed_id), None)
        if selected is None:
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} No feed with ID `{feed_id}` belongs to this server.",
                ephemeral=True,
            )
            return
        embed = build_embed(
            f"Remove feed `{feed_id}`?",
            f"{_feed_detail(selected)}\n\n"
            "Current feeds are shown in full by `/gitfeed list`."
            if len(feeds) > 1
            else _feed_detail(selected),
            color=PRIMARY_BLUE,
        )
        await _send_interaction(
            interaction,
            embed=embed,
            view=FeedConfirmationView(self, interaction.user.id, "remove", selected),
            ephemeral=True,
        )

    @gitfeed.command(name="list", description="List this server's GitHub feeds")
    @app_commands.guild_only()
    async def list_feeds_command(self, interaction: discord.Interaction):
        if not await _defer_interaction(interaction):
            return
        current = await self._list_feeds(str(interaction.guild_id))
        view = FeedListView(current, interaction.user.id)
        await _send_interaction(
            interaction,
            embed=view.make_embed(),
            view=view,
            ephemeral=True,
        )

    @gitfeed.command(name="test", description="Send a test message to a feed's channel")
    @app_commands.describe(feed_id="Identifier of the feed to test")
    @app_commands.guild_only()
    async def test_feed(self, interaction: discord.Interaction, feed_id: str):
        if not await _defer_interaction(interaction):
            return
        if not self._is_manager(interaction):
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} You need Manage Server permission to test a feed.",
                ephemeral=True,
            )
            return
        feed = await self._get_feed(feed_id, str(interaction.guild_id))
        if feed is None:
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} No feed with ID `{feed_id}` belongs to this server.",
                ephemeral=True,
            )
            return
        channel = self.bot.get_channel(int(feed["channel_id"]))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(feed["channel_id"]))
            except discord.NotFound:
                await _send_interaction(
                    interaction,
                    f"{LYRA_EMOJI} The feed's destination channel no longer exists.",
                    ephemeral=True,
                )
                return
        try:
            await channel.send(
                embed=build_embed(
                    "GitHub feed test",
                    f"This is a test for feed `{feed_id}` (`{feed['target']}`). "
                    "No GitHub activity or polling cursor was changed.",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await _send_interaction(
                interaction,
                f"{LYRA_EMOJI} I couldn't post to the feed's destination channel.",
                ephemeral=True,
            )
            return
        await _send_interaction(
            interaction,
            f"{LYRA_EMOJI} Test message sent to <#{feed['channel_id']}>.",
            ephemeral=True,
        )

    @gitfeed.command(name="status", description="Show this server's GitHub integration status")
    @app_commands.guild_only()
    async def status_feed(self, interaction: discord.Interaction):
        if not await _defer_interaction(interaction):
            return
        current = await self._list_feeds(str(interaction.guild_id))
        enabled = await self.bot.feature_manager.is_enabled(str(interaction.guild_id), "gitfeed")
        authentication = await self.client.verify_authentication()
        auth_labels = {
            "healthy": "healthy",
            "invalid": "authentication failed — check GITHUB_TOKEN",
            "permission_error": "token accepted, but GitHub denied this request",
            "unavailable": (
                "not configured — check GITHUB_TOKEN in .env"
                if not self.client.configured
                else "could not verify — GitHub API is temporarily unavailable"
            ),
        }
        description = (
            f"**Feature:** {'enabled' if enabled else 'disabled'}\n"
            f"**Feeds:** {len(current)}\n"
            f"**GitHub authentication:** {auth_labels[authentication]}\n"
        )
        if self.client.rate_limit_remaining is not None:
            description += f"**API requests remaining:** {self.client.rate_limit_remaining}\n"
        if self.client.rate_limit_reset:
            description += f"**Rate-limit reset:** {self.client.rate_limit_reset}\n"
        embed = build_embed("GitHub feed status", description)
        for feed in current[:10]:
            embed.add_field(name=f"`{feed['feed_id']}` — {feed['target']}", value=feed["status"], inline=True)
        if len(current) > 10:
            embed.set_footer(text="Use /gitfeed list to browse all feeds.")
        await _send_interaction(interaction, embed=embed, ephemeral=True)

    @staticmethod
    def _is_manager(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.permissions.manage_guild)

    @staticmethod
    def _can_post(interaction: discord.Interaction, channel: discord.TextChannel) -> bool:
        bot_member = interaction.guild.me
        permissions = channel.permissions_for(bot_member)
        return permissions.send_messages and permissions.embed_links

    async def _find_duplicate(
        self, guild_id: str, target_type: str, target: str, activities: list[str]
    ) -> str | None:
        for feed in await self._list_feeds(guild_id):
            if (
                feed["target_type"] == target_type
                and feed["target"].casefold() == target.casefold()
                and feed["activities"] == sorted(activities)
            ):
                return feed["feed_id"]
        return None

    async def _save_pending_feed(self, pending: dict) -> str:
        await self.bot.feature_manager.ensure_guild(pending["guild_id"])
        connection = await self.bot.database_manager.connection()
        try:
            feed_id = await feed_repository.create_feed(connection, **pending)
            await connection.commit()
            return feed_id
        finally:
            await connection.close()

    async def _list_feeds(self, guild_id: str) -> list[dict]:
        connection = await self.bot.database_manager.connection()
        try:
            return await feed_repository.list_feeds(connection, guild_id)
        finally:
            await connection.close()

    async def _get_feed(self, feed_id: str, guild_id: str) -> dict | None:
        connection = await self.bot.database_manager.connection()
        try:
            return await feed_repository.get_feed(connection, feed_id, guild_id)
        finally:
            await connection.close()

    async def _remove_feed(self, feed_id: str, guild_id: str) -> bool:
        connection = await self.bot.database_manager.connection()
        try:
            removed = await feed_repository.delete_feed(connection, feed_id, guild_id)
            await connection.commit()
            return removed
        finally:
            await connection.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(GitHubFeedsCog(bot))
