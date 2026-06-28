"""
Scheduler du module mail — Luciole Prime.

Exécute périodiquement :
  - La synchronisation IMAP (réception des emails)
  - L'envoi des messages sortants approuvés
  - L'expiration des brouillons anciens

Implémenté comme une tâche asyncio (run_in_executor pour les appels bloquants).
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from .inbound_service import InboundService
from .outbound_service import OutboundService
from .state import DraftRepo, MailSettingsRepo

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mail-worker")
_running = False


async def start_scheduler():
    """Démarre la boucle de scheduling en tâche asyncio."""
    global _running
    _running = True
    logger.info("Scheduler mail démarré")

    # Attendre 90s au démarrage pour laisser Ollama charger le modèle LLM
    logger.info("Scheduler mail : attente 90s (chargement modèle Ollama)...")
    await asyncio.sleep(90)
    logger.info("Scheduler mail : démarrage du polling IMAP")

    loop = asyncio.get_event_loop()

    while _running:
        try:
            settings = await loop.run_in_executor(
                _executor, MailSettingsRepo.get
            )

            if settings.mail_enabled:
                # Sync IMAP
                inbound_svc = InboundService()
                result = await loop.run_in_executor(_executor, inbound_svc.sync)
                if result.get("received", 0):
                    logger.info(f"Scheduler sync: {result}")

                # Envoi des sortants
                outbound_svc = OutboundService()
                send_result = await loop.run_in_executor(
                    _executor,
                    lambda: outbound_svc.send_pending(settings),
                )
                if send_result.get("sent", 0):
                    logger.info(f"Scheduler send: {send_result}")

                # Expiration des brouillons
                expired = await loop.run_in_executor(
                    _executor, DraftRepo.expire_old
                )
                if expired:
                    logger.info(f"Scheduler: {expired} brouillon(s) expiré(s)")

            await asyncio.sleep(settings.imap_poll_interval_seconds)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erreur scheduler mail : {e}")
            await asyncio.sleep(30)  # Backoff sur erreur inattendue

    logger.info("Scheduler mail arrêté")


def stop_scheduler():
    global _running
    _running = False


def is_running() -> bool:
    return _running
