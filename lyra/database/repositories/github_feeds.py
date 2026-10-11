from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_feed_id() -> str:
    return uuid.uuid4().hex[:12]


async def create_feed(
    connection,
    *,
    guild_id: str,
    channel_id: str,
    target_type: str,
    target: str,
    activities: list[str],
    is_private: bool = False,
) -> str:
    feed_id = new_feed_id()
    now = utc_now()
    await connection.execute(
        """
        INSERT INTO github_feeds
            (feed_id, guild_id, channel_id, target_type, target, activities,
             is_private, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feed_id,
            str(guild_id),
            str(channel_id),
            target_type,
            target,
            json.dumps(sorted(set(activities))),
            int(is_private),
            now,
        ),
    )
    return feed_id


def feed_from_row(row) -> dict:
    feed = dict(row)
    feed["activities"] = json.loads(feed["activities"])
    feed["etags"] = json.loads(feed["etags"])
    return feed


async def get_feed(connection, feed_id: str, guild_id: str) -> dict | None:
    cursor = await connection.execute(
        "SELECT * FROM github_feeds WHERE feed_id = ? AND guild_id = ?",
        (feed_id, str(guild_id)),
    )
    row = await cursor.fetchone()
    return feed_from_row(row) if row else None


async def list_feeds(connection, guild_id: str) -> list[dict]:
    cursor = await connection.execute(
        "SELECT * FROM github_feeds WHERE guild_id = ? ORDER BY created_at, feed_id",
        (str(guild_id),),
    )
    return [feed_from_row(row) for row in await cursor.fetchall()]


async def get_pollable_feeds(connection, now: str | None = None) -> list[dict]:
    cursor = await connection.execute(
        """
        SELECT f.*
        FROM github_feeds f
        JOIN guild_features gf
          ON gf.guild_id = f.guild_id
         AND gf.feature_name = 'gitfeed'
         AND gf.enabled = 1
        WHERE f.status IN ('active', 'unavailable', 'channel_error')
          AND (f.next_poll_at IS NULL OR f.next_poll_at <= ?)
        ORDER BY f.created_at
        """,
        (now or utc_now(),),
    )
    return [feed_from_row(row) for row in await cursor.fetchall()]


async def delete_feed(connection, feed_id: str, guild_id: str) -> bool:
    cursor = await connection.execute(
        "DELETE FROM github_feeds WHERE feed_id = ? AND guild_id = ?",
        (feed_id, str(guild_id)),
    )
    return cursor.rowcount > 0


async def add_activity(connection, feed_id: str, activity: dict) -> bool:
    cursor = await connection.execute(
        """
        INSERT OR IGNORE INTO github_feed_activities
            (feed_id, activity_id, activity_type, occurred_at, payload)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            feed_id,
            activity["id"],
            activity["type"],
            activity["occurred_at"],
            json.dumps(activity, ensure_ascii=False),
        ),
    )
    return cursor.rowcount > 0


async def update_poll_state(
    connection,
    feed_id: str,
    *,
    successful: bool,
    etags: dict | None = None,
    error_count: int = 0,
    next_poll_at: str | None = None,
    status: str = "active",
    last_activity_at: str | None = None,
) -> None:
    if successful:
        await connection.execute(
            """
            UPDATE github_feeds
            SET last_successful_poll = ?, error_count = 0, next_poll_at = NULL,
                status = 'active', etags = ?
            WHERE feed_id = ?
            """,
            (utc_now(), json.dumps(etags or {}), feed_id),
        )
    else:
        await connection.execute(
            """
            UPDATE github_feeds
            SET error_count = ?, next_poll_at = ?, status = ?, etags = ?
            WHERE feed_id = ?
            """,
            (error_count, next_poll_at, status, json.dumps(etags or {}), feed_id),
        )
    if last_activity_at:
        await connection.execute(
            "UPDATE github_feeds SET last_activity_at = ? WHERE feed_id = ?",
            (last_activity_at, feed_id),
        )
