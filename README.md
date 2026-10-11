# Lyra

Lyra is a friendly, slightly futuristic Discord bot built in Python with `discord.py`, SQLite, and `aiosqlite`.

### Lyra is currently in BETA. If you have any bugs, please make an issue so we can fix them ASAP.

## Features

- Feature toggles stored per Discord guild
- Shared embed styling with a blue palette
- Modular cogs for moderation, logging, utilities, leveling, and mini-games
- SQLite-backed persistence with a migration-ready structure
- Slash command interface
- Optional per-server GitHub activity feeds

## Run locally

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in the values.
3. Launch the bot:
   ```bash
   python -m lyra.main
   ```

Slash commands are globally registered at startup and may take time to appear in every server. For immediate development updates, set `LYRA_DEV_GUILD_ID` in `.env` to a test server ID; commands will also be synced to that server on startup.

## GitHub activity feeds

Set `GITHUB_TOKEN` to a GitHub personal access token and optionally set
`GITHUB_POLL_INTERVAL` (seconds; minimum 60, default 300). Feed polling uses the
token for both public and private repositories so requests use the dedicated
account's rate limit and permissions. Private repositories must be accessible
to that account. Enable the feature for a server with `/lyra enable gitfeed`,
then configure feeds with `/gitfeed add`.

GitHub's user activity endpoint contains only limited recent public events, and
repository events (including stars) are best-effort rather than a complete
history. Lyra polls the API and does not provide real-time or exhaustive
monitoring.

## Database persistence in Docker or containers

Set `LYRA_DB_PATH` to a mounted volume so the SQLite database survives container restarts, for example:

```bash
LYRA_DB_PATH=/data/lyra.db
```

Then mount a Docker volume or bind mount to `/data`.

## Branding

Lyra uses a restrained blue-forward design and a personality that feels warm, friendly, and slightly robotic.
