from __future__ import annotations

import sqlite3


CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS guilds (
        guild_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guild_features (
        guild_id TEXT NOT NULL,
        feature_name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, feature_name),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guild_config (
        guild_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, key),
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_levels (
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        xp INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS moderation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        moderator_id TEXT,
        action TEXT NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_guild_features_guild_id ON guild_features(guild_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_levels_guild_id ON user_levels(guild_id)
    """,
]


GITHUB_FEED_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS github_feeds (
        feed_id TEXT PRIMARY KEY,
        guild_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        target_type TEXT NOT NULL CHECK (target_type IN ('user', 'repository')),
        target TEXT NOT NULL COLLATE NOCASE,
        activities TEXT NOT NULL,
        is_private INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_successful_poll TEXT,
        last_activity_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        error_count INTEGER NOT NULL DEFAULT 0,
        next_poll_at TEXT,
        etags TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_github_feeds_unique_subscription
    ON github_feeds(guild_id, target_type, target, activities)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_github_feeds_polling
    ON github_feeds(status, next_poll_at, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS github_feed_activities (
        feed_id TEXT NOT NULL,
        activity_id TEXT NOT NULL,
        activity_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (feed_id, activity_id),
        FOREIGN KEY (feed_id) REFERENCES github_feeds(feed_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_github_feed_activities_time
    ON github_feed_activities(feed_id, occurred_at)
    """,
]

SCHEMA_VERSION = 2


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    current_version = connection.execute("SELECT version FROM schema_version").fetchone()
    if current_version is None:
        connection.execute("INSERT INTO schema_version (version) VALUES (?)", (0,))
        current_version = (0,)

    version = int(current_version[0])
    if version < 1:
        for statement in CREATE_TABLES_SQL:
            connection.execute(statement)
        version = 1
    if version < 2:
        for statement in GITHUB_FEED_TABLES_SQL:
            connection.execute(statement)
        version = 2
    connection.execute("UPDATE schema_version SET version = ?", (version,))
    connection.commit()
