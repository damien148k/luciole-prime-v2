"""
Point d'entrée FastAPI du service watcher standalone.

Ce module expose une application FastAPI minimale dédiée au watcher.
Elle peut être lancée en conteneur séparé ou intégrée dans l'admin-ui.

Démarrage :
    uvicorn src.watcher.main:app --host 0.0.0.0 --port 8090
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from .api import router, set_watcher_service
from .config import load_watcher_config
from .service import WatcherService

# Instance globale du WatcherService
_service: WatcherService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestion du cycle de vie FastAPI.

    Démarre le WatcherService au démarrage de l'application
    et l'arrête proprement à l'extinction.
    """
    global _service

    config = load_watcher_config()
    _service = WatcherService(config=config)

    await _service.start()
    set_watcher_service(_service)

    logger.info("Watcher API prête")
    yield

    logger.info("Arrêt du Watcher Service...")
    await _service.stop()


app = FastAPI(
    title="Luciole Prime — Watcher API",
    description="API d'administration du service de surveillance de fichiers RAG",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/watcher/docs",
    redoc_url="/api/watcher/redoc",
)

app.include_router(router)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Vérifie que le service est actif."""
    return {
        "status": "ok",
        "service": "watcher",
        "running": _service.is_running if _service else False,
    }
