"""Разделы API по областям (ARCHITECTURE.md, раздел 10)."""

from . import jobs, preview, scenarios, schemas, sources, system, themes

ROUTERS = [system.router, sources.router, scenarios.router, themes.router, preview.router, jobs.router, schemas.router]

__all__ = ["ROUTERS"]
