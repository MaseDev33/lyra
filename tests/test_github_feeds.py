import asyncio
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone

import discord
import lyra.services.github_api as github_api
from lyra.database.manager import DatabaseManager
from lyra.database.repositories import github_feeds
from lyra.services.feature_manager import FeatureManager
from lyra.services.github_api import GitHubClient
from lyra.services.github_feed_service import GitHubFeedService, _parse_time
from lyra.cogs.github_feeds import (
    GitHubFeedsCog,
    _defer_interaction,
    _error_response,
    _send_interaction,
)


@pytest.mark.asyncio
async def test_github_command_errors_are_specific_and_traceable():
    response = SimpleNamespace(sent=[])
    response.is_done = lambda: False

    async def send_message(**kwargs):
        response.sent.append(kwargs)

    response.send_message = send_message
    interaction = SimpleNamespace(
        id=123456,
        guild_id=987654,
        command=SimpleNamespace(qualified_name="gitfeed list"),
        response=response,
    )

    await GitHubFeedsCog.cog_app_command_error(
        SimpleNamespace(), interaction, RuntimeError("database unavailable")
    )
    content = response.sent[0]["content"]
    assert "GitHub command failed" in content
    assert "RuntimeError" in content
    assert "123456" in content
    assert "That command couldn't be completed" not in content


def test_github_api_error_response_uses_actionable_api_message():
    response = _error_response(
        github_api.GitHubAPIError("GitHub API is temporarily unreachable."),
        123456,
    )

    assert "GitHub API is temporarily unreachable" in response
    assert "That command couldn't be completed" not in response


@pytest.mark.asyncio
async def test_feed_creation_removal_and_guild_isolation(tmp_path):
    database = DatabaseManager(tmp_path / "feeds.db")
    await database.initialize()
    manager = FeatureManager(database)
    await manager.ensure_guild("guild-one")
    await manager.ensure_guild("guild-two")

    connection = await database.connection()
    try:
        first_id = await github_feeds.create_feed(
            connection,
            guild_id="guild-one",
            channel_id="channel-one",
            target_type="repository",
            target="owner/repo",
            activities=["commits", "issues"],
        )
        second_id = await github_feeds.create_feed(
            connection,
            guild_id="guild-two",
            channel_id="channel-two",
            target_type="repository",
            target="owner/repo",
            activities=["commits", "issues"],
        )
        await connection.commit()

        assert first_id != second_id
        assert [feed["feed_id"] for feed in await github_feeds.list_feeds(connection, "guild-one")] == [first_id]
        assert await github_feeds.delete_feed(connection, first_id, "guild-two") is False
        assert await github_feeds.delete_feed(connection, first_id, "guild-one") is True
        assert [feed["feed_id"] for feed in await github_feeds.list_feeds(connection, "guild-two")] == [second_id]
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_feed_deduplication_and_state_survive_database_reopen(tmp_path):
    database_path = tmp_path / "feeds.db"
    database = DatabaseManager(database_path)
    await database.initialize()
    manager = FeatureManager(database)
    await manager.ensure_guild("guild-one")
    await manager.enable_feature("guild-one", "gitfeed")

    connection = await database.connection()
    try:
        feed_id = await github_feeds.create_feed(
            connection,
            guild_id="guild-one",
            channel_id="channel-one",
            target_type="user",
            target="octocat",
            activities=["commits"],
        )
        activity = {
            "id": "commit:abc123",
            "type": "commits",
            "occurred_at": "2025-01-01T00:00:00+00:00",
            "repository": "octocat/hello-world",
        }
        assert await github_feeds.add_activity(connection, feed_id, activity) is True
        assert await github_feeds.add_activity(connection, feed_id, activity) is False
        await connection.commit()
    finally:
        await connection.close()

    reopened = DatabaseManager(database_path)
    connection = await reopened.connection()
    try:
        pollable = await github_feeds.get_pollable_feeds(connection)
        assert [feed["feed_id"] for feed in pollable] == [feed_id]
        cursor = await connection.execute(
            "SELECT COUNT(*) AS count FROM github_feed_activities WHERE feed_id = ?",
            (feed_id,),
        )
        assert (await cursor.fetchone())["count"] == 1
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_disabling_gitfeed_pauses_only_that_guild(tmp_path):
    database = DatabaseManager(tmp_path / "feeds.db")
    await database.initialize()
    manager = FeatureManager(database)
    for guild_id in ("guild-one", "guild-two"):
        await manager.ensure_guild(guild_id)
    await manager.enable_feature("guild-one", "gitfeed")

    connection = await database.connection()
    try:
        ids = []
        for guild_id in ("guild-one", "guild-two"):
            ids.append(
                await github_feeds.create_feed(
                    connection,
                    guild_id=guild_id,
                    channel_id=f"channel-{guild_id}",
                    target_type="user",
                    target="octocat",
                    activities=["commits"],
                )
            )
        await connection.commit()

        assert [feed["feed_id"] for feed in await github_feeds.get_pollable_feeds(connection)] == [ids[0]]
        await manager.enable_feature("guild-two", "gitfeed")
        await manager.disable_feature("guild-one", "gitfeed")
        assert {feed["feed_id"] for feed in await github_feeds.get_pollable_feeds(connection)} == {ids[1]}
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_delivery_failure_does_not_mark_activity_as_processed(tmp_path):
    database = DatabaseManager(tmp_path / "feeds.db")
    await database.initialize()
    feature_manager = FeatureManager(database)
    await feature_manager.ensure_guild("guild-one")
    await feature_manager.enable_feature("guild-one", "gitfeed")
    connection = await database.connection()
    try:
        feed_id = await github_feeds.create_feed(
            connection,
            guild_id="guild-one",
            channel_id="12345",
            target_type="repository",
            target="owner/repo",
            activities=["commits"],
        )
        await connection.commit()
        feed = await github_feeds.get_feed(connection, feed_id, "guild-one")
    finally:
        await connection.close()

    activity = {
        "id": "commit:retry-me",
        "type": "commits",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "repository": "owner/repo",
        "title": "Retry delivery",
    }
    attempts = 0

    class Channel:
        async def send(self, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary Discord send failure")

    channel = Channel()
    bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        fetch_channel=lambda channel_id: channel,
    )
    client = SimpleNamespace(configured=True)
    service = GitHubFeedService(bot, database, feature_manager, client, 300)

    async def fetch_activities(*args):
        return [activity], {}

    service._fetch_activities = fetch_activities

    await service.poll_feed(feed)
    connection = await database.connection()
    try:
        result = await connection.execute(
            "SELECT COUNT(*) FROM github_feed_activities WHERE feed_id = ?",
            (feed_id,),
        )
        assert (await result.fetchone())[0] == 0
    finally:
        await connection.close()

    await service.poll_feed(feed)
    connection = await database.connection()
    try:
        result = await connection.execute(
            "SELECT COUNT(*) FROM github_feed_activities WHERE feed_id = ?",
            (feed_id,),
        )
        assert (await result.fetchone())[0] == 1
    finally:
        await connection.close()
    assert attempts == 2


def test_user_event_monitor_reports_only_actor_activity():
    events = [
        {
            "id": "1",
            "type": "PushEvent",
            "created_at": "2025-01-01T00:00:00Z",
            "repo": {"name": "octocat/hello-world"},
            "payload": {"commits": [{"sha": "abcdef123", "message": "hello"}]},
        },
        {
            "id": "2",
            "type": "DeleteEvent",
            "created_at": "2025-01-01T00:01:00Z",
            "repo": {"name": "octocat/hello-world"},
        },
    ]
    activities = GitHubFeedService._user_activities(
        events,
        "octocat",
        _parse_time("2024-12-31T23:59:00+00:00"),
        {"commits", "issues"},
    )
    assert len(activities) == 1
    assert activities[0]["author"] == "octocat"
    assert activities[0]["url"] == "https://github.com/octocat/hello-world/commit/abcdef123"


@pytest.mark.asyncio
async def test_missing_github_token_is_reported_as_unavailable():
    client = GitHubClient(token="")
    assert client.configured is False
    assert await client.verify_authentication() == "unavailable"


def test_cog_loads_dotenv_before_reading_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def load_environment():
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    monkeypatch.setattr("lyra.cogs.github_feeds.load_environment", load_environment)
    monkeypatch.setattr("lyra.cogs.github_feeds.get_github_poll_interval", lambda: 300)
    bot = SimpleNamespace(database_manager=object(), feature_manager=object())

    cog = GitHubFeedsCog(bot)

    assert cog.client.configured


@pytest.mark.asyncio
async def test_github_client_sends_raw_token_as_bearer(monkeypatch):
    captured = {}

    class FakeSession:
        closed = False

        def __init__(self, *, timeout, headers):
            captured.update(headers)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(github_api.aiohttp, "ClientSession", FakeSession)
    client = GitHubClient(token="Bearer example-test-token")

    await client.start()

    assert captured["Authorization"] == "Bearer example-test-token"
    assert client.token == "example-test-token"
    await client.close()


@pytest.mark.asyncio
async def test_authentication_check_uses_authenticated_user_endpoint(monkeypatch):
    client = GitHubClient(token="test-token")
    paths = []

    async def get(path, **kwargs):
        paths.append(path)
        return {"resources": {}}, None

    monkeypatch.setattr(client, "get", get)

    assert await client.verify_authentication() == "healthy"
    assert paths == ["/user"]


@pytest.mark.asyncio
async def test_public_repository_validation_does_not_require_activity_endpoints(monkeypatch):
    client = GitHubClient(token="test-token")
    calls = []

    async def get(path, **kwargs):
        calls.append((path, kwargs.get("authenticated", True)))
        return {"full_name": "other-owner/repo", "private": False}, None

    monkeypatch.setattr(client, "get", get)
    result = await client.validate_target(
        "repository",
        "other-owner/repo",
        ["commits", "releases", "issues", "pull_requests", "stars"],
    )

    assert result["private"] is False
    assert calls == [("/repos/other-owner/repo", True)]


@pytest.mark.asyncio
async def test_public_repository_stars_use_watch_events_with_account_token():
    class Client:
        configured = True

    service = GitHubFeedService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        Client(),
        300,
    )
    request_args = []

    async def get_pages(path, params, key, etags, **kwargs):
        request_args.append((path, kwargs))
        return [
            {
                "id": "event-1",
                "type": "WatchEvent",
                "created_at": "2026-01-02T00:00:00Z",
                "actor": {"login": "stargazer"},
            },
            {
                "id": "event-2",
                "type": "PushEvent",
                "created_at": "2026-01-02T00:01:00Z",
                "actor": {"login": "committer"},
            },
        ], etags

    service._get_pages = get_pages
    activities, _ = await service._fetch_activities(
        {
            "target_type": "repository",
            "target": "other-owner/repo",
            "activities": ["stars"],
            "is_private": 0,
        },
        _parse_time("2026-01-01T00:00:00Z"),
        {},
    )

    assert request_args[0][0] == "/repos/other-owner/repo/events"
    assert request_args[0][1]["authenticated"] is True
    assert [activity["id"] for activity in activities] == ["star-event:event-1"]
    assert activities[0]["author"] == "stargazer"


@pytest.mark.asyncio
async def test_public_repository_commits_use_authenticated_api_requests():
    class Client:
        configured = True

    service = GitHubFeedService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        Client(),
        300,
    )
    request_args = []

    async def get_pages(path, params, key, etags, **kwargs):
        request_args.append((path, kwargs))
        return [], etags

    service._get_pages = get_pages
    await service._fetch_activities(
        {
            "target_type": "repository",
            "target": "public-owner/repo",
            "activities": ["commits"],
            "is_private": 0,
        },
        _parse_time("2026-01-01T00:00:00Z"),
        {},
    )

    assert request_args == [
        ("/repos/public-owner/repo/commits", {"authenticated": True})
    ]


@pytest.mark.asyncio
async def test_secondary_rate_limit_403_honors_retry_after_and_sanitizes_endpoint(monkeypatch):
    class Response:
        status = 403
        headers = {
            "Retry-After": "17",
            "X-RateLimit-Remaining": "500",
            "X-RateLimit-Resource": "core",
        }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self, **kwargs):
            return {"message": "You have exceeded a secondary rate limit."}

    class FakeSession:
        closed = False

        def __init__(self, *, timeout, headers):
            self.headers = headers

        def get(self, *args, **kwargs):
            return Response()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(github_api.aiohttp, "ClientSession", FakeSession)
    client = GitHubClient(token="test-token")

    with pytest.raises(github_api.GitHubAPIError) as exc_info:
        await client.get("/repos/private-owner/private-repo/commits")

    error = exc_info.value
    assert error.status == 403
    assert error.reason == "rate_limited"
    assert error.retry_after == 17
    assert error.endpoint == "/repos/{owner}/{repo}/commits"
    assert "private-owner" not in str(error)
    assert "private-repo" not in str(error)
    await client.close()


@pytest.mark.asyncio
async def test_secondary_rate_limit_without_retry_after_waits_at_least_one_minute(monkeypatch):
    class Response:
        status = 403
        headers = {"X-RateLimit-Remaining": "500"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self, **kwargs):
            return {"message": "You have exceeded a secondary rate limit."}

    class FakeSession:
        closed = False

        def __init__(self, *, timeout, headers):
            self.headers = headers

        def get(self, *args, **kwargs):
            return Response()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(github_api.aiohttp, "ClientSession", FakeSession)
    client = GitHubClient(token="test-token")

    with pytest.raises(github_api.GitHubAPIError) as exc_info:
        await client.get("/repos/owner/repo/issues")

    assert exc_info.value.reason == "rate_limited"
    assert exc_info.value.retry_after == 60
    await client.close()


@pytest.mark.asyncio
async def test_permission_403_is_distinguished_from_rate_limit(monkeypatch):
    class Response:
        status = 403
        headers = {
            "X-RateLimit-Remaining": "500",
            "X-GitHub-SSO": "required; url=https://github.com/orgs/example/sso",
        }

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def json(self, **kwargs):
            return {"message": "Resource not accessible by personal access token"}

    class FakeSession:
        closed = False

        def __init__(self, *, timeout, headers):
            self.headers = headers

        def get(self, *args, **kwargs):
            return Response()

        async def close(self):
            self.closed = True

    monkeypatch.setattr(github_api.aiohttp, "ClientSession", FakeSession)
    client = GitHubClient(token="test-token")

    with pytest.raises(github_api.GitHubAPIError) as exc_info:
        await client.get("/repos/owner/repo/issues")

    assert exc_info.value.reason == "permission_denied"
    assert exc_info.value.sso_required is True
    assert exc_info.value.endpoint == "/repos/{owner}/{repo}/issues"
    await client.close()


@pytest.mark.asyncio
async def test_authentication_check_distinguishes_permission_denial(monkeypatch):
    client = GitHubClient(token="test-token")

    async def get(path, **kwargs):
        from lyra.services.github_api import GitHubAPIError

        raise GitHubAPIError("permission denied", status=403)

    monkeypatch.setattr(client, "get", get)

    assert await client.verify_authentication() == "permission_error"


@pytest.mark.asyncio
async def test_github_command_deferral_accepts_already_acknowledged_interaction():
    class Response:
        def is_done(self):
            return False

        async def defer(self, **kwargs):
            raise discord.HTTPException(
                SimpleNamespace(status=400, reason="Bad Request"),
                {"code": 40060, "message": "Interaction has already been acknowledged."},
            )

    interaction = SimpleNamespace(response=Response())

    assert await _defer_interaction(interaction) is True


@pytest.mark.asyncio
async def test_github_command_response_falls_back_to_followup_after_ack_race():
    sent = []

    class Response:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            raise discord.HTTPException(
                SimpleNamespace(status=400, reason="Bad Request"),
                {"code": 40060, "message": "Interaction has already been acknowledged."},
            )

    class Followup:
        async def send(self, **kwargs):
            sent.append(kwargs)

    interaction = SimpleNamespace(response=Response(), followup=Followup())

    await _send_interaction(interaction, "done", ephemeral=True)

    assert sent == [{"ephemeral": True, "content": "done"}]


@pytest.mark.asyncio
async def test_requested_poll_runs_again_if_another_request_arrives_mid_poll():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def wait_until_ready():
        return None

    bot = SimpleNamespace(wait_until_ready=wait_until_ready)
    client = SimpleNamespace(configured=True)
    service = GitHubFeedService(bot, None, None, client, 300)

    async def poll_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()

    service.poll_once = poll_once
    service.request_poll()
    await entered.wait()
    service.request_poll()
    release.set()
    await service._poll_task

    assert calls == 2


def test_notification_content_suppresses_mentions_and_escapes_markdown():
    embed = GitHubFeedService._activity_embed(
        {
            "id": "issue:1",
            "type": "issues",
            "occurred_at": "2025-01-01T00:00:00Z",
            "repository": "owner/repo",
            "author": "octocat",
            "title": "@everyone [read this](https://example.invalid)",
            "details": "Issue #1",
            "url": "https://github.com/owner/repo/issues/1",
        }
    )
    assert "@\u200beveryone" in embed.description
    assert r"\[read this]" in embed.description
