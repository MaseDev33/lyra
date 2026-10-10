from __future__ import annotations

from datetime import datetime, timezone

import discord

LYRA_EMOJI = "<:Lyra:1558580804874010685>"
PRIMARY_BLUE = 0x3B82F6
DEEP_BLUE = 0x1D4ED8
SOFT_BLUE = 0x93C5FD
DARK_BLUE = 0x0F172A
GREY = 0x94A3B8
ERROR_RED = 0xDC2626
SUCCESS_GREEN = 0x16A34A


def build_embed(title: str, description: str | None = None, *, color: int = PRIMARY_BLUE, **kwargs) -> discord.Embed:
    branded_title = title if title.startswith(LYRA_EMOJI) else f"{LYRA_EMOJI} {title}"
    embed = discord.Embed(title=branded_title, description=description, color=color, **kwargs)
    embed.timestamp = datetime.now(timezone.utc)
    return embed
