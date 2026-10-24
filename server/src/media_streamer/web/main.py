"""ASGI entry point: ``uvicorn media_streamer.web.main:app``."""

import os

from .app import create_app

app = create_app(background=os.environ.get("MEDIA_STREAMER_NO_BACKGROUND") != "1")
