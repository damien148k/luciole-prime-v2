"""
Service de validation des brouillons — Module mail Luciole Prime.

Gère l'approbation, la modification et le rejet des brouillons
par les opérateurs humains depuis l'UI de feedback.

Après approbation, crée le message sortant (OutboundMessage)
prêt pour l'envoi par l'OutboundService.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

from loguru import logger

from .constants import AuditAction, AuditOutcome, DraftStatus
from .exceptions import DraftAlreadyReviewedError, DraftNotFoundError
from .html_renderer import render_email_html
from .models import DraftApproval, OutboundMessage
from .state import AuditRepo, DraftRepo, InboundRepo, OutboundRepo


class ApprovalService:
    """
    Service de validation humaine des brouillons.

    Opérations :
      - approve() : approuve tel quel, crée l'OutboundMessage
      - approve_with_changes() : approuve après modification du texte
      - reject() : rejette le brouillon sans envoi
    """

    def approve(
        self,
        draft_id: int,
        reviewer: str,
        modified_response: Optional[str] = None,
    ) -> OutboundMessage:
        """
        Approuve un brouillon et crée le message sortant correspondant.

        Args:
            draft_id: ID du brouillon à approuver.
            reviewer: Username de l'approbateur.
            modified_response: Texte modifié (optionnel). Si fourni,
                               remplace la réponse générée automatiquement.

        Returns:
            OutboundMessage créé et prêt pour l'envoi.

        Raises:
            DraftNotFoundError: Si le brouillon n'existe pas.
            DraftAlreadyReviewedError: Si le brouillon n'est plus en attente.
        """
        draft = self._get_pending_draft(draft_id)

        # Mettre à jour le brouillon
        DraftRepo.approve(draft_id, reviewer, modified_response)
        draft.status = (
            DraftStatus.MODIFIED_APPROVED if modified_response else DraftStatus.APPROVED
        )

        # Récupérer le message entrant pour les headers de threading
        inbound = InboundRepo.get(draft.inbound_message_id)
        if not inbound:
            raise DraftNotFoundError(f"Message entrant #{draft.inbound_message_id} introuvable")

        # Construire le message sortant
        final_text = modified_response or draft.generated_response
        outbound = self._build_outbound(draft, inbound, final_text)
        outbound_id = OutboundRepo.create(outbound)
        outbound.id = outbound_id

        action = (
            AuditAction.DRAFT_MODIFIED.value
            if modified_response
            else AuditAction.DRAFT_APPROVED.value
        )
        AuditRepo.log(
            action=action,
            actor=reviewer,
            outcome=AuditOutcome.SUCCESS.value,
            inbound_id=draft.inbound_message_id,
            thread_id=draft.thread_id,
            draft_id=draft_id,
            detail={
                "outbound_id": outbound_id,
                "modified": bool(modified_response),
                "response_length": len(final_text),
            },
        )

        logger.info(
            f"Brouillon #{draft_id} approuvé par {reviewer!r} "
            f"→ OutboundMessage #{outbound_id}"
        )
        return outbound

    def reject(
        self,
        draft_id: int,
        reviewer: str,
        comment: Optional[str] = None,
    ) -> None:
        """
        Rejette un brouillon. Aucun email n'est envoyé.

        Le message entrant est marqué comme traité (processed).
        """
        draft = self._get_pending_draft(draft_id)

        DraftRepo.reject(draft_id, reviewer, comment)

        # Marquer le message entrant comme traité (pas d'envoi)
        from .constants import InboundStatus
        from .state import InboundRepo as IR
        IR.update_status(draft.inbound_message_id, InboundStatus.PROCESSED)

        AuditRepo.log(
            action=AuditAction.DRAFT_REJECTED.value,
            actor=reviewer,
            outcome=AuditOutcome.SUCCESS.value,
            inbound_id=draft.inbound_message_id,
            thread_id=draft.thread_id,
            draft_id=draft_id,
            detail={"comment": comment},
        )

        logger.info(f"Brouillon #{draft_id} rejeté par {reviewer!r}")

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitaires internes
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _get_pending_draft(draft_id: int) -> DraftApproval:
        """Récupère un brouillon en attente ou lève une exception."""
        draft = DraftRepo.get(draft_id)
        if not draft:
            raise DraftNotFoundError(f"Brouillon #{draft_id} introuvable")
        if draft.status != DraftStatus.PENDING:
            raise DraftAlreadyReviewedError(
                f"Brouillon #{draft_id} déjà {draft.status.value}"
            )
        return draft

    @staticmethod
    def _build_outbound(
        draft: DraftApproval,
        inbound: "InboundMessage",
        response_text: str,
    ) -> OutboundMessage:
        """Construit un OutboundMessage à partir d'un brouillon approuvé."""
        # Headers de threading RFC 2822
        in_reply_to = inbound.message_id
        refs_list = []
        if inbound.references_header:
            refs_list = inbound.references_header.split()
        if inbound.message_id not in refs_list:
            refs_list.append(inbound.message_id)
        references_str = " ".join(refs_list)

        # Sujet de réponse
        subject = inbound.subject or ""
        if not subject.lower().startswith(("re:", "re :")):
            subject = f"Re: {subject}"

        # Message-ID unique pour le mail sortant
        msg_id_header = f"<{uuid.uuid4().hex}@luciole-prime>"

        # Ajouter les sources au corps de la réponse (version texte plain)
        # On garde l'original avant concaténation pour l'utiliser dans le HTML.
        original_response = response_text
        sources = draft.get_sources()
        passages = draft.get_passages()

        if sources:
            sources_lines = []
            seen = set()
            for s in sources:
                name = s.get("file_name") or s.get("file_path", "")
                if name and name not in seen:
                    seen.add(name)
                    # Extraire uniquement le nom de fichier (pas le chemin complet)
                    import os
                    short_name = os.path.basename(name)
                    sources_lines.append(f"  • {short_name}")

            if sources_lines:
                sources_section = (
                    "\n\n---\n"
                    "Documents consultés pour cette réponse :\n"
                    + "\n".join(sources_lines)
                )
                response_text = response_text + sources_section

        # Générer la version HTML stylisée (sources + passages)
        try:
            body_html = render_email_html(
                response_text = original_response,
                sources       = sources,
                passages      = passages,
            )
        except Exception as e:
            logger.warning(f"Rendu HTML échoué (inbound #{inbound.id}) : {e} — fallback texte seul")
            body_html = None

        return OutboundMessage(
            inbound_message_id = inbound.id,
            thread_id          = inbound.thread_id,
            draft_approval_id  = draft.id,
            to_address         = inbound.from_address,
            subject            = subject,
            body_text          = response_text,
            body_html          = body_html,
            message_id_header  = msg_id_header,
            in_reply_to        = in_reply_to,
            references_header  = references_str,
            sources_used       = draft.sources_used,
            confidence_score   = draft.confidence_score,
            rag_query          = draft.rag_query,
        )
