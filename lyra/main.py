from __future__ import annotations

import os
import sys

if __package__ in (None, ""):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

from lyra.bot import LyraBot
from lyra.config import get_token


if __name__ == "__main__":
    token = get_token()
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    LyraBot().run(token)
