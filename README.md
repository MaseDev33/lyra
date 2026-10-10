# Lyra

Lyra is a friendly, slightly futuristic Discord bot built in Python with `discord.py`, SQLite, and `aiosqlite`.

## Features

- Feature toggles stored per Discord guild
- Shared embed styling with a blue palette
- Modular cogs for moderation, logging, utilities, leveling, and mini-games
- SQLite-backed persistence with a migration-ready structure
- Slash command interface

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

Slash commands are globally registered at startup and may take time to appear in every server. For immediate development updates, set `LYRA_DEV_GUILD_ID` in `.env` to a test server ID; commands will then be synced to that server on startup.

## Database persistence in Docker or containers

Set `LYRA_DB_PATH` to a mounted volume so the SQLite database survives container restarts, for example:

```bash
LYRA_DB_PATH=/data/lyra.db
```

Then mount a Docker volume or bind mount to `/data`.

## Branding

Lyra uses a restrained blue-forward design and a personality that feels warm, friendly, and slightly robotic.
