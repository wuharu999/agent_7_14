"""Compatibility entry point: uvicorn cloud_app:app"""
from ecs.app.main import app

__all__ = ["app"]
