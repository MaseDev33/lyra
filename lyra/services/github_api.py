from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

GITHUB_API = "https://api.github.com"
USER_ACTIVITY_TYPES = ("commits", "releases", "issues", "pull_requests", "stars")


class GitHubAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
        endpoint: str | None = None,
        reason: str | None = None,
        rate_limit_remaining: int | None = None,
        rate_limit_reset: str | None = None,
        rate_limit_resource: str | None = None,
        sso_required: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.endpoint = endpoint
        self.reason = reason
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset
        self.rate_limit_resource = rate_limit_resource
        self.sso_required = sso_required


class GitHubClient:
    def __init__(self, token: str | None = None, *, timeout: float = 15):
        configured_token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        configured_token = configured_token.strip()
        if configured_token.lower().startswith("bearer "):
            configured_token = configured_token[7:].strip()
        self.token = configured_token
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: aiohttp.ClientSession | None = None
        self.public_session: aiohttp.ClientSession | None = None
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def start(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    **({"Authorization": f"Bearer {self.token}"} if self.configured else {}),
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Lyra-Discord-Bot",
                },
            )
        if self.public_session is None or self.public_session.closed:
            self.public_session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Lyra-Discord-Bot",
                },
            )

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()
        if self.public_session and not self.public_session.closed:
            await self.public_session.close()

    @staticmethod
    def _retry_delay(headers, attempt: int) -> float:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        if headers.get("X-RateLimit-Remaining") == "0":
            try:
                return max(
                    0.0,
                    int(headers["X-RateLimit-Reset"]) - datetime.now(timezone.utc).timestamp(),
                )
            except (KeyError, TypeError, ValueError):
                return 2**attempt
        return min(8.0, 0.5 * (2**attempt))

    @staticmethod
    def _safe_endpoint(path: str) -> str:
        parts = path.strip("/").split("/")
        if parts[0] == "repos" and len(parts) >= 3:
            return "/repos/{owner}/{repo}" + (
                "/" + "/".join(parts[3:]) if len(parts) > 3 else ""
            )
        if parts[0] == "users" and len(parts) >= 2:
            return "/users/{username}" + (
                "/" + "/".join(parts[2:]) if len(parts) > 2 else ""
            )
        return "/" + "/".join(parts)

    async def get(
        self,
        path: str,
        *,
        etag: str | None = None,
        params: dict | None = None,
        accept: str | None = None,
        authenticated: bool = True,
    ):
        if authenticated and not self.configured:
            raise GitHubAPIError("GitHub integration is unavailable: GITHUB_TOKEN is not configured.")
        await self.start()
        session = self.session if authenticated else self.public_session
        assert session is not None
        headers = {"If-None-Match": etag} if etag else {}
        if accept:
            headers["Accept"] = accept
        for attempt in range(3):
            try:
                async with session.get(
                    f"{GITHUB_API}{path}", headers=headers, params=params
                ) as response:
                    self._update_rate_limit(response.headers)
                    if response.status == 304:
                        return None, response.headers.get("ETag", etag)
                    if response.status == 200:
                        return await response.json(), response.headers.get("ETag")
                    if response.status == 401:
                        raise GitHubAPIError(
                            "GitHub rejected GITHUB_TOKEN. It may be expired, revoked, or copied incorrectly.",
                            status=401,
                            endpoint=self._safe_endpoint(path),
                            reason="authentication_failed",
                        )
                    if response.status == 404:
                        raise GitHubAPIError(
                            "GitHub target was not found or is not accessible to this account.",
                            status=404,
                            endpoint=self._safe_endpoint(path),
                            reason="not_found_or_inaccessible",
                        )
                    if response.status in (403, 429) or response.status >= 500:
                        is_rate_limited = response.status in (403, 429)
                        if is_rate_limited:
                            try:
                                error_data = await response.json(content_type=None)
                            except (aiohttp.ContentTypeError, ValueError):
                                error_data = {}
                            raw_message = (
                                error_data.get("message", "")
                                if isinstance(error_data, dict)
                                else ""
                            )
                            api_message = raw_message if isinstance(raw_message, str) else ""
                            secondary_limit = any(
                                marker in api_message.lower()
                                for marker in ("rate limit", "abuse detection")
                            )
                            is_rate_limited = (
                                response.status == 429
                                or response.headers.get("X-RateLimit-Remaining") == "0"
                                or bool(response.headers.get("Retry-After"))
                                or secondary_limit
                            )
                        if response.status == 403 and not is_rate_limited:
                            sso_header = response.headers.get("X-GitHub-SSO", "")
                            raise GitHubAPIError(
                                "GitHub denied this request. Check the account's repository permissions or organization SSO authorization.",
                                status=403,
                                endpoint=self._safe_endpoint(path),
                                reason="permission_denied",
                                rate_limit_remaining=self.rate_limit_remaining,
                                rate_limit_reset=self.rate_limit_reset,
                                rate_limit_resource=response.headers.get("X-RateLimit-Resource"),
                                sso_required="required" in sso_header.lower(),
                            )
                        delay = self._retry_delay(response.headers, attempt)
                        if (
                            is_rate_limited
                            and response.headers.get("X-RateLimit-Remaining") != "0"
                            and not response.headers.get("Retry-After")
                        ):
                            delay = max(delay, 60.0)
                        if is_rate_limited and delay > 30:
                            raise GitHubAPIError(
                                "GitHub API rate limit reached; retrying after the indicated delay.",
                                status=response.status,
                                retry_after=delay,
                                endpoint=self._safe_endpoint(path),
                                reason="rate_limited",
                                rate_limit_remaining=self.rate_limit_remaining,
                                rate_limit_reset=self.rate_limit_reset,
                                rate_limit_resource=response.headers.get("X-RateLimit-Resource"),
                            )
                        if attempt < 2:
                            await asyncio.sleep(delay)
                            continue
                        raise GitHubAPIError(
                            "GitHub API rate limit or service error; retry later.",
                            status=response.status,
                            retry_after=delay if is_rate_limited else None,
                            endpoint=self._safe_endpoint(path),
                            reason="rate_limited" if is_rate_limited else "service_error",
                            rate_limit_remaining=self.rate_limit_remaining,
                            rate_limit_reset=self.rate_limit_reset,
                            rate_limit_resource=response.headers.get("X-RateLimit-Resource"),
                        )
                    raise GitHubAPIError(
                        f"GitHub API request failed with HTTP {response.status}.",
                        status=response.status,
                        endpoint=self._safe_endpoint(path),
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                if attempt == 2:
                    logging.getLogger("lyra.github").warning(
                        "GitHub API request failed after retries: %s", type(error).__name__
                    )
                    raise GitHubAPIError("GitHub API is temporarily unreachable.") from error
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt)))
        raise GitHubAPIError("GitHub API request failed.")

    def _update_rate_limit(self, headers) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is not None:
            try:
                self.rate_limit_remaining = int(remaining)
            except ValueError:
                self.rate_limit_remaining = None
        if reset:
            try:
                self.rate_limit_reset = datetime.fromtimestamp(
                    int(reset), timezone.utc
                ).isoformat()
            except (ValueError, OverflowError, OSError):
                self.rate_limit_reset = None

    async def validate_target(
        self,
        target_type: str,
        target: str,
        activities: list[str] | None = None,
    ) -> dict:
        if target_type == "user":
            data, _ = await self.get(f"/users/{target}")
            await self.get(
                f"/users/{target}/events/public",
                params={"per_page": 1},
            )
        else:
            owner, repository = target.split("/", 1)
            path = f"/repos/{owner}/{repository}"
            try:
                data, _ = await self.get(path)
                private = bool(data.get("private"))
            except GitHubAPIError as error:
                if error.status != 404:
                    raise
                # Private repositories are intentionally invisible to anonymous
                # requests; retry with the account token without exposing details.
                try:
                    data, _ = await self.get(path)
                except GitHubAPIError as private_error:
                    if private_error.status in (403, 404):
                        raise GitHubAPIError(
                            "GitHub target was not found or is not accessible to this account.",
                            status=404,
                        ) from private_error
                    raise
                private = True
            if private and data.get("private") is not True:
                data, _ = await self.get(path)
        return data

    async def verify_authentication(self) -> str:
        if not self.configured:
            return "unavailable"
        try:
            # /rate_limit can succeed without authentication; /user confirms the
            # supplied credential itself was accepted by GitHub.
            await self.get("/user")
            return "healthy"
        except GitHubAPIError as error:
            if error.status == 401:
                return "invalid"
            if error.status == 403:
                return "permission_error"
            return "unavailable"
