from __future__ import annotations

import re

_DURATION_PARTS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


def parse_duration(value: str) -> int:
    """Parse strings such as '1h 30m' or '2d' into total seconds."""
    if value is None or not isinstance(value, str):
        raise ValueError("Duration must be a string")

    text = value.strip().lower()
    if not text:
        raise ValueError("Duration cannot be empty")

    matches = list(re.finditer(r"(\d+)\s*([a-zA-Z]+)", text))
    if not matches:
        raise ValueError(f"Could not parse duration: {value!r}")

    total = 0
    for match in matches:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit not in _DURATION_PARTS:
            raise ValueError(f"Unsupported unit: {unit!r}")
        total += amount * _DURATION_PARTS[unit]

    if total <= 0:
        raise ValueError("Duration must be greater than zero")
    return total
