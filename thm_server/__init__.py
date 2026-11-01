"""THM server: FastAPI over ``thm_core`` (design sections 6, 9, 10).

REST over the records and commands with actor tagging, the agent inbox, media
serving with Range support, thumbnails, a minimal SSE change feed, and static
hosting of the built ``web/dist``. The server is the only process that writes
project files; every write goes through ``thm_core.commands``.
"""

from .app import create_app

__all__ = ["create_app"]
