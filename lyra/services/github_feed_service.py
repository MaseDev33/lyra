from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import tasks

from lyra.database.repositories import github_feeds as feeds
from lyra.services.github_api import GitHubAPIError, GitHubClient
from lyra.utils.embeds import build_embed

LOGGER = logging.getLogger("lyra.github")
def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return datetime.now(timezone.utc)


def _safe_text(value: str | None, limit: int = 1000) -> str:
    value = discord.utils.escape_markdown(value or "")
    return value.replace("@", "@\u200b")[:limit]


def _date(value: str | None) -> str | None:
    return value


class GitHubFeedService:
    def __init__(self, bot, database_manager, feature_manager, client: GitHubClient, interval: int):
        self.bot = bot
        self.database_manager = database_manager
        self.feature_manager = feature_manager
        self.client = client
        self.interval = interval
        self._semaphore = asyncio.Semaphore(5)
        self._poll_lock = asyncio.Lock()
        self._poll_task: asyncio.Task | None = None
        self._poll_requested = False
        self._global_retry_at: datetime | None = None
        self._global_error_count = 0

    async def start(self) -> None:
        await self.client.start()
        if not self.poll_feeds.is_running():
            self.poll_feeds.change_interval(seconds=self.interval)
            self.poll_feeds.start()

    async def close(self) -> None:
        self.poll_feeds.cancel()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        await self.client.close()

    @tasks.loop(seconds=300)
    async def poll_feeds(self) -> None:
        try:
            await self.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("GitHub feed polling cycle failed")

    def request_poll(self) -> None:
        self._poll_requested = True
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(
                self._run_requested_poll(),
                name="lyra-github-feed-poll",
            )

    async def _run_requested_poll(self) -> None:
        try:
            await self.bot.wait_until_ready()
            while self._poll_requested:
                self._poll_requested = False
                await self.poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Unexpected failure during requested GitHub feed poll")

    async def poll_once(self) -> None:
        if (
            not self.client.configured
            or (
                self._global_retry_at is not None
                and datetime.now(timezone.utc) < self._global_retry_at
            )
        ):
            return
        async with self._poll_lock:
            connection = await self.database_manager.connection()
            try:
                pending = await feeds.get_pollable_feeds(connection)
            finally:
                await connection.close()
            results = await asyncio.gather(
                *(self._poll_limited(feed) for feed in pending),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    LOGGER.error(
                        "Unexpected failure in GitHub feed poll",
                        exc_info=(type(result), result, result.__traceback__),
                    )

    @poll_feeds.before_loop
    async def _before_polling(self) -> None:
        await self.bot.wait_until_ready()

    async def _poll_limited(self, feed: dict) -> None:
        async with self._semaphore:
            await self.poll_feed(feed)

    async def poll_feed(self, feed: dict) -> None:
        etags = dict(feed["etags"])
        successful = False
        last_activity_at: str | None = None
        try:
            if not await self.feature_manager.is_enabled(feed["guild_id"], "gitfeed"):
                return
            cursor = _parse_time(feed["last_successful_poll"] or feed["created_at"])
            activities, etags = await self._fetch_activities(feed, cursor, etags)
            channel = self.bot.get_channel(int(feed["channel_id"]))
            if channel is None:
                channel = await self.bot.fetch_channel(int(feed["channel_id"]))
            for activity in sorted(activities, key=lambda item: item["occurred_at"]):
                if _parse_time(activity["occurred_at"]) < _parse_time(feed["created_at"]):
                    continue
                connection = await self.database_manager.connection()
                try:
                    cursor = await connection.execute(
                        "SELECT 1 FROM github_feed_activities "
                        "WHERE feed_id = ? AND activity_id = ?",
                        (feed["feed_id"], activity["id"]),
                    )
                    already_sent = await cursor.fetchone() is not None
                finally:
                    await connection.close()
                if already_sent:
                    continue
                if not await self.feature_manager.is_enabled(feed["guild_id"], "gitfeed"):
                    return
                await channel.send(
                    embed=self._activity_embed(activity),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                connection = await self.database_manager.connection()
                try:
                    await feeds.add_activity(connection, feed["feed_id"], activity)
                    await connection.commit()
                finally:
                    await connection.close()
                last_activity_at = max(
                    last_activity_at or activity["occurred_at"], activity["occurred_at"]
                )
            successful = True
        except GitHubAPIError as error:
            was_globally_backed_off = (
                self._global_retry_at is not None
                and datetime.now(timezone.utc) < self._global_retry_at
            )
            self._apply_global_backoff(error)
            if error.reason == "rate_limited":
                LOGGER.warning(
                    "GitHub feed %s API rate limited: endpoint=%s resource=%s remaining=%s retry_after=%s",
                    feed["feed_id"],
                    error.endpoint or "unknown",
                    error.rate_limit_resource or "unknown",
                    error.rate_limit_remaining,
                    error.retry_after,
                )
            elif error.status in (401, 403):
                if not was_globally_backed_off or error.status == 403:
                    LOGGER.warning(
                        "GitHub feed %s API request denied: status=%s endpoint=%s reason=%s sso_required=%s rate_limit_resource=%s rate_limit_remaining=%s rate_limit_reset=%s",
                        feed["feed_id"],
                        error.status,
                        error.endpoint or "unknown",
                        error.reason or "authorization_error",
                        error.sso_required,
                        error.rate_limit_resource or "unknown",
                        error.rate_limit_remaining,
                        error.rate_limit_reset,
                    )
            elif error.status in (404, 410) or not was_globally_backed_off:
                LOGGER.warning("GitHub feed %s poll failed: %s", feed["feed_id"], error)
            await self._record_error(feed, error.status in (404, 410), etags)
            return
        except discord.NotFound:
            LOGGER.info("GitHub feed %s destination channel no longer exists", feed["feed_id"])
            await self._set_status(feed["feed_id"], "channel_missing")
            return
        except discord.Forbidden:
            LOGGER.warning("GitHub feed %s cannot post to its destination channel", feed["feed_id"])
            await self._record_error(feed, False, etags, status="channel_error")
            return
        except sqlite3.Error:
            LOGGER.info("GitHub feed %s was removed during polling", feed["feed_id"])
            return
        except (discord.HTTPException, OSError, asyncio.TimeoutError) as error:
            LOGGER.warning("GitHub feed %s delivery failed: %s", feed["feed_id"], type(error).__name__)
            await self._record_error(feed, False, etags)
            return
        except Exception:
            LOGGER.exception("Unexpected failure polling GitHub feed %s", feed["feed_id"])
            await self._record_error(feed, False, etags)
            return

        if successful:
            self._global_error_count = 0
            connection = await self.database_manager.connection()
            try:
                await feeds.update_poll_state(
                    connection,
                    feed["feed_id"],
                    successful=True,
                    etags=etags,
                    last_activity_at=last_activity_at,
                )
                await connection.commit()
            finally:
                await connection.close()

    async def _fetch_activities(
        self, feed: dict, cursor: datetime, etags: dict
    ) -> tuple[list[dict], dict]:
        if feed["target_type"] == "user":
            path = f"/users/{feed['target']}/events/public"
            events, etags = await self._get_pages(
                path, {}, "user-events", etags, authenticated=True
            )
            return self._user_activities(events, feed["target"], cursor, set(feed["activities"])), etags

        owner, repository = feed["target"].split("/", 1)
        base = f"/repos/{owner}/{repository}"
        selected = set(feed["activities"])
        collected: list[dict] = []
        if "commits" in selected:
            records, etags = await self._get_pages(
                f"{base}/commits",
                {"since": cursor.isoformat(), "per_page": 100},
                "commits",
                etags,
                authenticated=True,
            )
            for record in records:
                commit = record.get("commit", {})
                commit_data = commit.get("author") or commit.get("committer") or {}
                occurred = _date(commit_data.get("date"))
                if occurred and _parse_time(occurred) > cursor:
                    message_lines = (commit.get("message") or "").splitlines()
                    collected.append(
                        self._activity(
                            f"commit:{record.get('sha')}",
                            "commits",
                            occurred,
                            repository=feed["target"],
                            author=(record.get("author") or {}).get("login")
                            or commit_data.get("name"),
                            title=message_lines[0] if message_lines else None,
                            details=commit.get("message", ""),
                            url=record.get("html_url"),
                            sha=record.get("sha"),
                        )
                    )
        if "releases" in selected:
            records, etags = await self._get_pages(
                f"{base}/releases",
                {"per_page": 100},
                "releases",
                etags,
                authenticated=True,
            )
            for record in records:
                occurred = _date(record.get("published_at") or record.get("created_at"))
                if not occurred or record.get("draft") or _parse_time(occurred) <= cursor:
                    continue
                collected.append(
                    self._activity(
                        f"release:{record.get('id')}",
                        "releases",
                        occurred,
                        repository=feed["target"],
                        author=(record.get("author") or {}).get("login"),
                        title=record.get("name") or record.get("tag_name"),
                        details=record.get("tag_name"),
                        url=record.get("html_url"),
                    )
                )
        if "issues" in selected:
            records, etags = await self._get_pages(
                f"{base}/issues",
                {"state": "all", "since": cursor.isoformat(), "per_page": 100},
                "issues",
                etags,
                authenticated=True,
            )
            for record in records:
                occurred = _date(record.get("created_at"))
                if not occurred or "pull_request" in record or _parse_time(occurred) <= cursor:
                    continue
                collected.append(
                    self._activity(
                        f"issue:{record.get('id')}",
                        "issues",
                        occurred,
                        repository=feed["target"],
                        author=(record.get("user") or {}).get("login"),
                        title=record.get("title"),
                        details=f"Issue #{record.get('number')}",
                        url=record.get("html_url"),
                        number=record.get("number"),
                    )
                )
        if "pull_requests" in selected:
            records, etags = await self._get_pages(
                f"{base}/pulls",
                {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100},
                "pull-requests",
                etags,
                authenticated=True,
            )
            for record in records:
                occurred = _date(record.get("created_at"))
                if not occurred or _parse_time(occurred) <= cursor:
                    continue
                collected.append(
                    self._activity(
                        f"pull:{record.get('id')}",
                        "pull_requests",
                        occurred,
                        repository=feed["target"],
                        author=(record.get("user") or {}).get("login"),
                        title=record.get("title"),
                        details=f"Pull request #{record.get('number')}",
                        url=record.get("html_url"),
                        number=record.get("number"),
                    )
                )
        if "stars" in selected:
            records, etags = await self._get_pages(
                f"{base}/events",
                {"per_page": 100},
                "repository-events",
                etags,
                stop_before=cursor,
                authenticated=True,
            )
            for record in records:
                if record.get("type") != "WatchEvent":
                    continue
                occurred = _date(record.get("created_at"))
                actor = record.get("actor") or {}
                if not occurred or _parse_time(occurred) <= cursor:
                    continue
                collected.append(
                    self._activity(
                        f"star-event:{record.get('id')}",
                        "stars",
                        occurred,
                        repository=feed["target"],
                        author=actor.get("login"),
                        title="New star",
                        url=f"https://github.com/{feed['target']}",
                    )
                )
        return collected, etags

    async def _get_pages(
        self,
        path: str,
        params: dict,
        key: str,
        etags: dict,
        *,
        star_listing: bool = False,
        stop_before: datetime | None = None,
        authenticated: bool = True,
    ) -> tuple[list[dict], dict]:
        records: list[dict] = []
        for page in range(1, 11):
            request_params = {**params, "page": page}
            page_key = f"{key}:{page}"
            request_etag = etags.get(page_key)
            if star_listing:
                data, etag = await self._get_star_page(
                    path, request_params, request_etag, authenticated=authenticated
                )
            else:
                data, etag = await self.client.get(
                    path,
                    params=request_params,
                    etag=request_etag,
                    authenticated=authenticated,
                )
            if data is None:
                break
            if not isinstance(data, list):
                raise GitHubAPIError("GitHub returned an unexpected collection response.")
            if stop_before is not None:
                reached_cursor = False
                for record in data:
                    occurred = _date(record.get("starred_at") or record.get("created_at"))
                    if not occurred or _parse_time(occurred) <= stop_before:
                        reached_cursor = True
                        break
                    records.append(record)
                if reached_cursor:
                    break
            else:
                records.extend(data)
            if etag:
                etags[page_key] = etag
            if len(data) < 100:
                break
            if page == 10:
                raise GitHubAPIError("GitHub activity pagination limit reached; retrying without advancing cursor.")
        return records, etags

    async def _get_star_page(
        self, path: str, params: dict, etag: str | None, *, authenticated: bool
    ):
        return await self.client.get(
            path,
            params=params,
            etag=etag,
            accept="application/vnd.github.star+json",
            authenticated=authenticated,
        )

    def _apply_global_backoff(self, error: GitHubAPIError) -> None:
        should_back_off = (
            error.status == 401
            or error.status in (429, 500, 502, 503, 504)
            or error.retry_after is not None
            or error.status is None
        )
        if not should_back_off:
            return
        self._global_error_count += 1
        delay = error.retry_after or min(1800, 30 * 2 ** min(self._global_error_count - 1, 6))
        self._global_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)

    @staticmethod
    def _user_activities(events: list[dict], username: str, cursor: datetime, selected: set[str]) -> list[dict]:
        result = []
        for event in events:
            kind = {
                "PushEvent": "commits",
                "ReleaseEvent": "releases",
                "IssuesEvent": "issues",
                "PullRequestEvent": "pull_requests",
                "WatchEvent": "stars",
            }.get(event.get("type"))
            if kind not in selected:
                continue
            occurred = _date(event.get("created_at"))
            if not occurred or _parse_time(occurred) <= cursor:
                continue
            repo = (event.get("repo") or {}).get("name", "unknown repository")
            payload = event.get("payload") or {}
            common = dict(repository=repo, author=username)
            if kind == "commits":
                for commit in payload.get("commits", []):
                    sha = commit.get("sha")
                    if not sha:
                        continue
                    message_lines = (commit.get("message") or "").splitlines()
                    result.append(
                        GitHubFeedService._activity(
                            f"user-push:{event.get('id')}:{sha}",
                            kind,
                            occurred,
                            title=message_lines[0] if message_lines else None,
                            details=commit.get("message", ""),
                            url=f"https://github.com/{repo}/commit/{sha}",
                            sha=sha,
                            **common,
                        )
                    )
            elif kind == "releases":
                release = payload.get("release") or {}
                result.append(
                    GitHubFeedService._activity(
                        f"user-release:{event.get('id')}",
                        kind,
                        occurred,
                        title=release.get("name") or release.get("tag_name"),
                        details=release.get("tag_name"),
                        url=release.get("html_url"),
                        **common,
                    )
                )
            else:
                item = payload.get("issue") or payload.get("pull_request") or {}
                is_star = kind == "stars"
                number = item.get("number")
                result.append(
                    GitHubFeedService._activity(
                        f"user-{kind}:{event.get('id')}",
                        kind,
                        occurred,
                        title="New star" if is_star else item.get("title"),
                        details=f"{'Pull request' if kind == 'pull_requests' else 'Issue'} #{number}"
                        if number
                        else None,
                        url=item.get("html_url")
                        or (f"https://github.com/{repo}/stargazers" if is_star else None),
                        number=number,
                        **common,
                    )
                )
        return result

    @staticmethod
    def _activity(activity_id: str, kind: str, occurred_at: str, **data) -> dict:
        return {"id": activity_id, "type": kind, "occurred_at": occurred_at, **data}

    @staticmethod
    def _activity_embed(activity: dict) -> discord.Embed:
        labels = {
            "commits": "new commit",
            "releases": "new release",
            "issues": "new issue",
            "pull_requests": "new pull request",
            "stars": "new star",
        }
        description = f"`{_safe_text(activity.get('repository'), 200)}`"
        if activity.get("author"):
            description += f"\nby **{_safe_text(activity['author'], 100)}**"
        if activity.get("sha"):
            description += f"\n\n`{_safe_text(activity['sha'][:7], 7)}`"
        if activity.get("title"):
            description += f"\n\n{_safe_text(activity['title'], 500)}"
        if activity.get("details") and activity["type"] != "commits":
            description += f"\n{_safe_text(activity['details'], 200)}"
        embed = build_embed(labels.get(activity["type"], "GitHub activity"), description)
        if activity.get("url", "").startswith("https://github.com/"):
            embed.url = activity["url"]
        embed.timestamp = _parse_time(activity["occurred_at"])
        return embed

    async def _record_error(
        self, feed: dict, unavailable: bool, etags: dict, *, status: str = "active"
    ) -> None:
        attempts = int(feed["error_count"]) + 1
        delay = min(21600, 60 * 2 ** min(attempts, 8))
        next_poll = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        await self._set_status(
            feed["feed_id"],
            "unavailable" if unavailable else status,
            error_count=attempts,
            next_poll_at=next_poll,
            etags=etags,
        )

    async def _set_status(
        self,
        feed_id: str,
        status: str,
        *,
        error_count: int = 0,
        next_poll_at: str | None = None,
        etags: dict | None = None,
    ) -> None:
        connection = await self.database_manager.connection()
        try:
            await feeds.update_poll_state(
                connection,
                feed_id,
                successful=False,
                error_count=error_count,
                next_poll_at=next_poll_at,
                status=status,
                etags=etags or {},
            )
            await connection.commit()
        finally:
            await connection.close()
