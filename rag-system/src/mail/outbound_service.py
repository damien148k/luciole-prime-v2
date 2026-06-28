"""
Service d'envoi des emails sortants — Module mail Luciole Prime.

N'envoie QUE les messages dont le statut est 'ready'
(brouillon approuvé par un humain).

Implémente un verrou optimiste pour éviter les doubles envois.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime, timedelta, timezone

from loguru import logger

from .constants import AuditAction, AuditOutcome, RETRY_DELAYS_SECONDS
from .exceptions import SMTPError
from .models import MailSettings, OutboundMessage
from .smtp_client import SMTPClient
from .state import AuditRepo, DeadLetterRepo, InboundRepo, OutboundRepo, ThreadRepo
from .constants import InboundStatus


class OutboundService:
    """
    Envoie les messages sortants approuvés.

    Appelé périodiquement par le scheduler (toutes les N secondes).
    """

    def send_pending(self, settings: MailSettings) -> dict:
        """
        Traite tous les messages en statut 'ready'.

        Retourne { sent, failed, skipped }.
        """
        if not settings.mail_enabled or not settings.smtp_host:
            return {"sent": 0, "failed": 0, "skipped": 0}

        ready = OutboundRepo.list_ready()
        stats = {"sent": 0, "failed": 0, "skipped": 0}

        for msg in ready:
            result = self._send_one(msg, settings)
            stats[result] += 1

        if stats["sent"] or stats["failed"]:
            logger.info(f"Envoi sortant — sent={stats['sent']} failed={stats['failed']}")

        return stats

    def send_one(self, outbound_id: int, settings: MailSettings) -> str:
        """
        Force l'envoi d'un message spécifique par son ID.

        Retourne "sent", "failed" ou "skipped" (déjà envoyé).
        """
        msg = OutboundRepo.list_ready()
        target = next((m for m in msg if m.id == outbound_id), None)
        if not target:
            return "skipped"
        return self._send_one(target, settings)

    # ─────────────────────────────────────────────────────────────────────────
    # Envoi unitaire avec verrou optimiste
    # ─────────────────────────────────────────────────────────────────────────

    def _send_one(self, outbound: OutboundMessage, settings: MailSettings) -> str:
        """
        Tente l'envoi d'un message.

        Verrou optimiste : UPDATE status='sending' WHERE status='ready'.
        Si 0 lignes affectées → déjà en cours d'envoi par un autre worker.
        """
        if not OutboundRepo.lock_for_sending(outbound.id):
            logger.debug(f"OutboundMessage #{outbound.id} déjà verrouillé — skipped")
            return "skipped"

        t_start = time.monotonic()
        client = SMTPClient(settings)

        try:
            client.send(outbound)
            duration = int((time.monotonic() - t_start) * 1000)

            OutboundRepo.mark_sent(outbound.id)

            # Mettre à jour le thread
            if outbound.thread_id:
                ThreadRepo.increment_reply_count(outbound.thread_id)

            # Marquer le message entrant comme traité
            if outbound.inbound_message_id:
                InboundRepo.update_status(
                    outbound.inbound_message_id, InboundStatus.PROCESSED
                )

            AuditRepo.log(
                action=AuditAction.EMAIL_SENT.value,
                actor="system",
                outcome=AuditOutcome.SUCCESS.value,
                inbound_id=outbound.inbound_message_id,
                outbound_id=outbound.id,
                thread_id=outbound.thread_id,
                duration_ms=duration,
                detail={
                    "to": outbound.to_address,
                    "subject": outbound.subject[:80],
                },
            )

            logger.info(
                f"Email envoyé #{outbound.id} → {outbound.to_address!r} ({duration}ms)"
            )
            return "sent"

        except SMTPError as e:
            duration = int((time.monotonic() - t_start) * 1000)
            retry_count = (outbound.retry_count or 0) + 1
            next_retry = None

            if retry_count <= len(RETRY_DELAYS_SECONDS):
                delay = RETRY_DELAYS_SECONDS[retry_count - 1]
                next_retry = (
                    datetime.now(timezone.utc) + timedelta(seconds=delay)
                ).strftime("%Y-%m-%d %H:%M:%S")
                logger.warning(
                    f"SMTP échec #{outbound.id} (tentative {retry_count}) : {e} "
                    f"— prochain retry dans {delay}s"
                )
            else:
                logger.error(
                    f"SMTP échec définitif #{outbound.id} après {retry_count} tentatives : {e}"
                )
                DeadLetterRepo.create(
                    error_type="smtp_send",
                    error_message=str(e),
                    inbound_id=outbound.inbound_message_id,
                    raw_payload=str(outbound.id),
                    stack=traceback.format_exc(),
                )

            OutboundRepo.mark_failed(outbound.id, str(e), next_retry)

            AuditRepo.log(
                action=AuditAction.SMTP_ERROR.value,
                actor="system",
                outcome=AuditOutcome.FAILURE.value,
                outbound_id=outbound.id,
                inbound_id=outbound.inbound_message_id,
                duration_ms=duration,
                detail={
                    "error": str(e),
                    "retry_count": retry_count,
                    "next_retry": next_retry,
                },
            )
            return "failed"
