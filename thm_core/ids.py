"""Opaque ids and timestamps for THM records.

Ids are a short type prefix plus eight hex characters. They are assigned once at
creation and never derived from names; the prefix only helps a human read a log.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

PROJECT = "p_"
BIN = "b_"  # bins upgraded from a schema-3 shot or item keep their s_ / it_ id
MEDIA = "m_"
REFERENCE = "r_"
GENERATION = "g_"
CHANGE = "c_"
MESSAGE = "msg_"
SESSION = "as_"


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def short_id(id_: str) -> str:
    """Last four characters, used by the handle collision rule."""
    return str(id_)[-4:]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
