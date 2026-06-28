"""
Service de réception et de traitement des emails entrants — Module mail Luciole Prime.

Orchestre le pipeline complet :
  IMAP fetch → parse → anti-boucle → thread → pièces jointes
  → classification → décision → génération RAG → brouillon
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

from .classifier import EmailClassifier
from .config import MAIL_ATTACHMENTS_PATH
from .constants import (
    ALLOWED_ATTACHMENT_EXTENSIONS,
    AuditAction,
    AuditOutcome,
    EmailCategory,
    InboundStatus,
    RoutingDecision,
)
from .db import init_tables
from .draft_service import DraftService
from .exceptions import DuplicateMessageError, ParseError
from .imap_client import IMAPClient
from .models import (
    Attachment,
    InboundMessage,
    MailSettings,
    MailThread,
    ParsedAttachment,
    ParsedEmail,
    RawEmail,
)
from .parser import EmailParser
from .state import (
    AttachmentRepo,
    AuditRepo,
    ClassificationRepo,
    DeadLetterRepo,
    InboundRepo,
    MailSettingsRepo,
    ThreadRepo,
)


class InboundService:
    """
    Synchronise et traite les emails entrants.

    Méthode principale : sync() — appelée par le scheduler.
    """

    def __init__(self) -> None:
        self._parser = EmailParser()
        self._classifier = EmailClassifier()
        self._draft_svc = DraftService()

    # ─────────────────────────────────────────────────────────────────────────
    # Synchronisation IMAP
    # ─────────────────────────────────────────────────────────────────────────

    def sync(self) -> dict:
        """
        Récupère les nouveaux emails depuis le serveur IMAP et les traite.

        Retourne un résumé du cycle : { received, processed, errors, skipped }.
        """
        init_tables()
        settings = MailSettingsRepo.get()

        if not settings.mail_enabled:
            return {"received": 0, "processed": 0, "errors": 0, "skipped": 0, "reason": "disabled"}

        if not settings.imap_host:
            return {"received": 0, "processed": 0, "errors": 0, "skipped": 0, "reason": "not_configured"}

        stats = {"received": 0, "processed": 0, "errors": 0, "skipped": 0}
        client = IMAPClient(settings)

        try:
            client.connect()
            raw_emails: list[RawEmail] = client.fetch_unseen()
            stats["received"] = len(raw_emails)

            for raw in raw_emails:
                try:
                    result = self._process_one(raw, settings, client)
                    if result == "skipped":
                        stats["skipped"] += 1
                    else:
                        stats["processed"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Erreur traitement email UID={raw.uid} : {e}")
                    DeadLetterRepo.create(
                        error_type="inbound_pipeline",
                        error_message=str(e),
                        stack=traceback.format_exc(),
                    )

        except Exception as e:
            logger.error(f"Erreur connexion IMAP dans sync() : {e}")
            stats["errors"] += 1
        finally:
            client.disconnect()

        logger.info(
            f"Sync IMAP terminé — reçus={stats['received']} "
            f"traités={stats['processed']} erreurs={stats['errors']}"
        )
        return stats

    # ─────────────────────────────────────────────────────────────────────────
    # Traitement d'un email unique
    # ─────────────────────────────────────────────────────────────────────────

    def _process_one(
        self,
        raw: RawEmail,
        settings: MailSettings,
        imap_client: Optional[IMAPClient] = None,
    ) -> str:
        """
        Traite un email brut à travers le pipeline complet.

        Retourne "skipped" si l'email est un doublon, "ok" sinon.
        """
        # ── 1. Parsing MIME ───────────────────────────────────────────────
        try:
            parsed = self._parser.parse(raw.raw_bytes)
        except ParseError as e:
            AuditRepo.log(
                action=AuditAction.PARSE_ERROR.value,
                outcome=AuditOutcome.FAILURE.value,
                detail={"uid": raw.uid, "error": str(e)},
            )
            raise

        # ── 2. Déduplication ─────────────────────────────────────────────
        if InboundRepo.exists(parsed.message_id):
            logger.debug(f"Doublon ignoré : {parsed.message_id!r}")
            if imap_client:
                imap_client.mark_as_seen(raw.uid)
            return "skipped"

        # ── 3. Résolution du thread ───────────────────────────────────────
        thread = self._resolve_thread(parsed)

        # ── 4. Stocker le message entrant ─────────────────────────────────
        inbound = InboundMessage(
            message_id       = parsed.message_id,
            thread_id        = thread.id if thread else None,
            from_address     = parsed.from_address,
            from_name        = parsed.from_name,
            to_addresses     = json.dumps(parsed.to_addresses),
            cc_addresses     = json.dumps(parsed.cc_addresses),
            reply_to         = parsed.reply_to,
            subject          = parsed.subject,
            body_text        = parsed.body_text,
            body_text_raw    = parsed.body_text_raw,
            body_html        = parsed.body_html,
            in_reply_to      = parsed.in_reply_to,
            references_header = " ".join(parsed.references) if parsed.references else None,
            imap_uid         = raw.uid,
            status           = InboundStatus.RECEIVED,
            is_auto_reply    = parsed.is_auto_reply,
            auto_reply_reason = parsed.auto_reply_reason,
        )
        inbound_id = InboundRepo.create(inbound)
        inbound.id = inbound_id

        if thread and thread.id:
            ThreadRepo.increment_message_count(thread.id)

        # ── 5. Marquer comme lu sur IMAP (après commit DB) ────────────────
        if imap_client:
            imap_client.mark_as_seen(raw.uid)

        AuditRepo.log(
            action=AuditAction.EMAIL_RECEIVED.value,
            outcome=AuditOutcome.SUCCESS.value,
            inbound_id=inbound_id,
            thread_id=thread.id if thread else None,
            detail={"from": parsed.from_address, "subject": parsed.subject[:80]},
        )

        # ── 6. Anti-boucle ────────────────────────────────────────────────
        if parsed.is_auto_reply:
            InboundRepo.update_status(inbound_id, InboundStatus.QUARANTINED)
            AuditRepo.log(
                action=AuditAction.ANTI_LOOP_BLOCK.value,
                outcome=AuditOutcome.BLOCKED.value,
                inbound_id=inbound_id,
                detail={"reason": parsed.auto_reply_reason},
            )
            logger.info(f"Email #{inbound_id} mis en quarantaine (auto-reply)")
            return "ok"

        # ── 7. Traitement des pièces jointes ─────────────────────────────
        att_count = self._handle_attachments(parsed.attachments, inbound_id, settings)
        if att_count > 0:
            InboundRepo.update_attachment_count(inbound_id, att_count)
            inbound.has_attachments = True
            inbound.attachment_count = att_count

        # ── 8. Classification ─────────────────────────────────────────────
        InboundRepo.update_status(inbound_id, InboundStatus.CLASSIFYING)

        thread_replies = 0
        if thread and thread.id:
            thread_replies = ThreadRepo.get_reply_count_last_hour(thread.id)

        classif = self._classifier.classify(parsed, settings, thread_replies)
        classif.inbound_message_id = inbound_id
        ClassificationRepo.create(classif)

        InboundRepo.update_status(inbound_id, InboundStatus.CLASSIFIED)
        AuditRepo.log(
            action=AuditAction.CLASSIFIED.value,
            outcome=AuditOutcome.SUCCESS.value,
            inbound_id=inbound_id,
            detail={
                "category": classif.category.value,
                "confidence": classif.confidence_score,
                "risk": classif.risk_score,
                "decision": classif.decision.value,
            },
        )

        # ── 9. Routage ────────────────────────────────────────────────────
        if classif.decision == RoutingDecision.QUARANTINE:
            InboundRepo.update_status(inbound_id, InboundStatus.QUARANTINED)
            AuditRepo.log(
                action=AuditAction.QUARANTINED.value,
                outcome=AuditOutcome.SKIPPED.value,
                inbound_id=inbound_id,
                detail={"reason": classif.decision_reason},
            )
            return "ok"

        if classif.decision == RoutingDecision.ESCALATE:
            InboundRepo.update_status(inbound_id, InboundStatus.PROCESSED)
            if thread and thread.id:
                from .db import db_cursor
                with db_cursor() as (_, cur):
                    cur.execute(
                        "UPDATE mail_threads SET status='escalated', updated_at=? WHERE id=?",
                        (time.strftime("%Y-%m-%d %H:%M:%S"), thread.id),
                    )
            return "ok"

        # ── 10. Génération du brouillon (V1 : toujours DRAFT) ─────────────
        if classif.decision in (RoutingDecision.DRAFT, RoutingDecision.AUTO_REPLY):
            try:
                draft = self._draft_svc.create_draft(
                    inbound            = inbound,
                    thread             = thread,
                    settings           = settings,
                    classification_category = classif.category.value,
                    decision_reason    = classif.decision_reason,
                    confidence_score   = classif.confidence_score,
                    risk_score         = classif.risk_score,
                )

                # Auto-approbation si activée
                # Quand auto_reply=True : on approuve toujours (confiance/risque ignorés)
                # Les cas spam/quarantaine et besoin_humain sont déjà traités avant ce point
                if settings.auto_reply_enabled:
                    from .approval_service import ApprovalService
                    ApprovalService().approve(draft.id, reviewer="luciole-auto")
                    logger.info(
                        f"Auto-réponse : envoi direct pour inbound #{inbound_id} "
                        f"(conf={classif.confidence_score:.2f}, risque={classif.risk_score:.2f})"
                    )

            except Exception as e:
                logger.error(f"Erreur génération brouillon (inbound #{inbound_id}) : {e}")
                InboundRepo.update_status(inbound_id, InboundStatus.ERROR)
                DeadLetterRepo.create(
                    error_type="rag_query",
                    error_message=str(e),
                    inbound_id=inbound_id,
                    stack=traceback.format_exc(),
                )

        return "ok"

    # ─────────────────────────────────────────────────────────────────────────
    # Résolution / création du thread
    # ─────────────────────────────────────────────────────────────────────────

    def _resolve_thread(self, parsed: ParsedEmail) -> Optional[MailThread]:
        """
        Trouve ou crée le thread pour cet email.

        Cherche par In-Reply-To, puis par References.
        Si aucun thread trouvé, en crée un nouveau.
        """
        # Recherche par In-Reply-To
        if parsed.in_reply_to:
            thread = ThreadRepo.find_by_message_id(parsed.in_reply_to)
            if thread:
                return thread

        # Recherche par References (dernier Message-ID de la chaîne)
        for ref_id in reversed(parsed.references):
            thread = ThreadRepo.find_by_message_id(ref_id)
            if thread:
                return thread

        # Nouveau thread
        subject_normalized = EmailParser.normalize_subject(parsed.subject)
        thread = MailThread(
            subject_normalized = subject_normalized,
            first_message_id   = parsed.message_id,
        )
        thread_id = ThreadRepo.create(thread)
        thread.id = thread_id
        return thread

    # ─────────────────────────────────────────────────────────────────────────
    # Traitement des pièces jointes
    # ─────────────────────────────────────────────────────────────────────────

    def _handle_attachments(
        self,
        attachments: list[ParsedAttachment],
        inbound_id: int,
        settings: MailSettings,
    ) -> int:
        """
        Stocke et analyse les pièces jointes de manière sécurisée.

        Retourne le nombre de pièces jointes traitées (y compris rejetées).
        """
        if not attachments:
            return 0

        base_path = Path(MAIL_ATTACHMENTS_PATH)
        from datetime import datetime
        now = datetime.utcnow()
        storage_dir = base_path / str(now.year) / f"{now.month:02d}"
        storage_dir.mkdir(parents=True, exist_ok=True)

        max_bytes = settings.max_attachment_size_mb * 1024 * 1024
        count = 0

        for att in attachments:
            count += 1
            ext = Path(att.filename).suffix.lower()

            # Vérification extension (bloquage définitif)
            is_allowed_type = ext in ALLOWED_ATTACHMENT_EXTENSIONS
            is_size_ok = att.size_bytes <= max_bytes

            if not is_allowed_type:
                AuditRepo.log(
                    action=AuditAction.ATTACHMENT_REJECTED.value,
                    outcome=AuditOutcome.BLOCKED.value,
                    inbound_id=inbound_id,
                    detail={"filename": att.filename, "reason": f"Extension non autorisée: {ext}"},
                )
                scan_detail = f"Extension non autorisée : {ext}"
                is_safe = False
            elif not is_size_ok:
                scan_detail = f"Taille dépassée : {att.size_bytes} > {max_bytes}"
                is_safe = False
            else:
                scan_detail = "OK (vérification extension + taille)"
                is_safe = True

            # Nom de fichier sécurisé
            safe_ext = ext if is_allowed_type else ".blocked"
            stored_name = f"{uuid.uuid4().hex}{safe_ext}"
            stored_path = storage_dir / stored_name

            # Stocker seulement si safe (pas de fichiers dangereux sur disque)
            if is_safe:
                try:
                    stored_path.write_bytes(att.data)
                    stored_path.chmod(0o640)
                except OSError as e:
                    logger.error(f"Impossible de stocker la pièce jointe {att.filename} : {e}")
                    is_safe = False
                    scan_detail = f"Erreur stockage : {e}"

            sha256 = hashlib.sha256(att.data).hexdigest()
            relative_path = str(stored_path.relative_to(base_path)) if is_safe else ""

            attachment_record = Attachment(
                inbound_message_id   = inbound_id,
                filename_original    = att.filename,
                filename_stored      = relative_path,
                content_type_declared = att.content_type,
                content_type_detected = att.content_type,  # V2 : python-magic
                size_bytes           = att.size_bytes,
                sha256_hash          = sha256,
                is_allowed_type      = is_allowed_type,
                is_size_ok           = is_size_ok,
                is_safe              = is_safe,
                scan_detail          = scan_detail,
                indexed_in_rag       = False,  # V1 : jamais indexé automatiquement
            )
            AttachmentRepo.create(attachment_record)

        return count
