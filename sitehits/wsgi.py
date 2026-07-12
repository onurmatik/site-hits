"""Expose the Django WSGI application under the StageOps project name."""

from config.wsgi import application


__all__ = ["application"]
