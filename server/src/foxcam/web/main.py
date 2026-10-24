"""ASGI entry point: ``uvicorn foxcam.web.main:app``."""

import os

from .app import create_app

app = create_app(background=os.environ.get("FOXCAM_NO_BACKGROUND") != "1")
