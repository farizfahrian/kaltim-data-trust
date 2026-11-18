"""FastAPI entry point forwarding to the verified kdt.api service."""
from kdt.api import app, create_app

__all__ = ["app", "create_app"]
