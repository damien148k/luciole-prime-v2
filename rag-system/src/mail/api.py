"""
API FastAPI du module mail — Luciole Prime.

Router monté dans feedback_ui.py sous le préfixe /api/mail.
Tous les endpoints requièrent l'authentification cookie existante
(transmise par le middleware auth de feedback_ui.py).

Règles de sécurité :
  - Les mots de passe ne sont JAMAIS renvoyés en clair (has_password=bool)
  - body_html des emails entrants n'est pas exposé (XSS)
  - L'auto-réponse reste désactivée par défaut (V1)
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from .approval_service import ApprovalService
from .config import decrypt_secret, encrypt_secret
from .constants import AuditAction, AuditOutcome, InboundStatus
from .db import init_tables
from .exceptions import (
    DraftAlreadyReviewedError,
    DraftNotFoundError,
    MailNotConfiguredError,
)
from .imap_client import IMAPClient
from .inbound_service import InboundService
from .models import MailSettings, MailTestRun
from .outbound_service import OutboundService
from .smtp_client import SMTPClient
from .state import (
    AuditRepo,
    DeadLetterRepo,
    DraftRepo,
    InboundRepo,
    MailSettingsRepo,
    TestRunRepo,
    ThreadRepo,
    get_stats_24h,
)

router = APIRouter(prefix="/api/mail", tags=["mail"])

# Initialiser les tables au chargement du module
try:
    init_tables()
except Exception as e:
    logger.warning(f"Init tables mail différée : {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Modèles Pydantic de requête/réponse
# ─────────────────────────────────────────────────────────────────────────────

class MailSettingsUpdate(BaseModel):
    mail_enabled: bool = False
    auto_reply_enabled: bool = False

    imap_host: Optional[str] = None
    imap_port: int = Field(993, ge=1, le=65535)
    imap_use_ssl: bool = True
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None    # Vide = conserver l'existant
    imap_folder: str = "INBOX"
    imap_poll_interval_seconds: int = Field(60, ge=10, le=3600)

    smtp_host: Optional[str] = None
    smtp_port: int = Field(465, ge=1, le=65535)
    smtp_use_tls: bool = True
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None    # Vide = conserver l'existant

    from_name: str = "Luciole"
    from_address: Optional[str] = None
    signature: str = ""

    confidence_threshold: float = Field(0.75, ge=0.0, le=1.0)
    risk_threshold: float = Field(0.40, ge=0.0, le=1.0)
    allowed_sender_domains: list = Field(default_factory=list)
    blocked_sender_domains: list = Field(default_factory=list)
    max_attachment_size_mb: int = Field(25, ge=1, le=100)
    index_name: str = Field(default_factory=lambda: os.environ.get("MAIL_DEFAULT_INDEX", "documents"))
    sensitive_keywords: list = Field(default_factory=list)


class TestSendRequest(BaseModel):
    recipient: str = Field(..., description="Adresse email de test")


class ApproveRequest(BaseModel):
    modified_response: Optional[str] = Field(None, description="Réponse modifiée (optionnel)")


class RejectRequest(BaseModel):
    comment: Optional[str] = Field(None, description="Raison du rejet")


# ─────────────────────────────────────────────────────────────────────────────
# Paramètres
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings():
    """
    Retourne les paramètres mail.
    Les mots de passe ne sont jamais inclus — uniquement has_password (bool).
    """
    settings = MailSettingsRepo.get()
    return settings.to_api_dict()


@router.post("/settings")
async def update_settings(request: Request, payload: MailSettingsUpdate):
    """
    Met à jour les paramètres mail.

    Si imap_password / smtp_password sont vides → conservation de l'existant.
    """
    actor = getattr(request.state, "username", "admin")
    current = MailSettingsRepo.get()

    # Chiffrement conditionnel des mots de passe
    if payload.imap_password:
        imap_enc = encrypt_secret(payload.imap_password)
    else:
        imap_enc = current.imap_password_enc  # Conserver l'existant

    if payload.smtp_password:
        smtp_enc = encrypt_secret(payload.smtp_password)
    else:
        smtp_enc = current.smtp_password_enc  # Conserver l'existant

    updated = MailSettings(
        mail_enabled                = payload.mail_enabled,
        imap_host                   = payload.imap_host,
        imap_port                   = payload.imap_port,
        imap_use_ssl                = payload.imap_use_ssl,
        imap_username               = payload.imap_username,
        imap_password_enc           = imap_enc,
        imap_folder                 = payload.imap_folder,
        imap_poll_interval_seconds  = payload.imap_poll_interval_seconds,
        smtp_host                   = payload.smtp_host,
        smtp_port                   = payload.smtp_port,
        smtp_use_tls                = payload.smtp_use_tls,
        smtp_username               = payload.smtp_username,
        smtp_password_enc           = smtp_enc,
        from_name                   = payload.from_name,
        from_address                = payload.from_address,
        signature                   = payload.signature,
        auto_reply_enabled          = payload.auto_reply_enabled,
        confidence_threshold        = payload.confidence_threshold,
        risk_threshold              = payload.risk_threshold,
        allowed_sender_domains      = json.dumps(payload.allowed_sender_domains),
        blocked_sender_domains      = json.dumps(payload.blocked_sender_domains),
        max_attachment_size_mb      = payload.max_attachment_size_mb,
        attachment_indexing_enabled = False,  # V1 : toujours False
        index_name                  = payload.index_name,
        sensitive_keywords          = json.dumps(payload.sensitive_keywords),
    )

    MailSettingsRepo.update(updated, actor=actor)
    AuditRepo.log(
        action=AuditAction.SETTINGS_UPDATED.value,
        actor=actor,
        outcome=AuditOutcome.SUCCESS.value,
        detail={"mail_enabled": payload.mail_enabled},
    )
    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests IMAP/SMTP
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/test-connection")
async def test_connection(request: Request):
    """
    Teste les connexions IMAP et SMTP sans envoyer de message.

    Insère le résultat dans mail_test_runs.
    """
    actor = getattr(request.state, "username", "admin")
    settings = MailSettingsRepo.get()

    t_total = time.monotonic()

    # Test IMAP
    imap_result = IMAPClient.test_connection(settings)

    # Test SMTP (indépendant)
    smtp_result = SMTPClient.test_connection(settings)

    total_ms = int((time.monotonic() - t_total) * 1000)

    run = MailTestRun(
        test_type       = "connection",
        imap_status     = imap_result.status.value,
        imap_detail     = imap_result.detail,
        imap_latency_ms = imap_result.latency_ms,
        imap_error_code = imap_result.error_code,
        smtp_status     = smtp_result.status.value,
        smtp_detail     = smtp_result.detail,
        smtp_latency_ms = smtp_result.latency_ms,
        smtp_error_code = smtp_result.error_code,
        triggered_by    = actor,
        total_duration_ms = total_ms,
    )
    run_id = TestRunRepo.create(run)

    overall = (
        "ok" if imap_result.status.value == "ok" and smtp_result.status.value == "ok"
        else "partial" if (imap_result.status.value == "ok" or smtp_result.status.value == "ok")
        else "error"
    )

    AuditRepo.log(
        action=AuditAction.TEST_CONNECTION.value,
        actor=actor,
        outcome=AuditOutcome.SUCCESS.value if overall == "ok" else AuditOutcome.FAILURE.value,
        detail={"overall": overall, "imap": imap_result.status.value, "smtp": smtp_result.status.value},
    )

    return {
        "imap": {
            "status":     imap_result.status.value,
            "detail":     imap_result.detail,
            "latency_ms": imap_result.latency_ms,
            "error_code": imap_result.error_code,
        },
        "smtp": {
            "status":     smtp_result.status.value,
            "detail":     smtp_result.detail,
            "latency_ms": smtp_result.latency_ms,
            "error_code": smtp_result.error_code,
        },
        "overall":     overall,
        "test_run_id": run_id,
    }


@router.post("/test-send")
async def test_send(request: Request, payload: TestSendRequest):
    """
    Envoie un email de test vers l'adresse indiquée.

    Insère le résultat dans mail_test_runs.
    Ne crée pas de message entrant/sortant en DB.
    """
    actor = getattr(request.state, "username", "admin")
    settings = MailSettingsRepo.get()

    if not settings.smtp_host:
        raise HTTPException(status_code=400, detail="SMTP non configuré")

    t_total = time.monotonic()
    client = SMTPClient(settings)
    result = client.send_test_email(payload.recipient)
    total_ms = int((time.monotonic() - t_total) * 1000)

    run = MailTestRun(
        test_type         = "send",
        imap_status       = "skipped",
        smtp_status       = "ok" if result["status"] == "sent" else "error",
        smtp_detail       = result.get("error") or "Email de test envoyé",
        smtp_latency_ms   = result.get("latency_ms"),
        test_recipient    = payload.recipient,
        send_status       = result["status"],
        triggered_by      = actor,
        total_duration_ms = total_ms,
    )
    run_id = TestRunRepo.create(run)

    AuditRepo.log(
        action=AuditAction.TEST_SEND.value,
        actor=actor,
        outcome=AuditOutcome.SUCCESS.value if result["status"] == "sent" else AuditOutcome.FAILURE.value,
        detail={"recipient": payload.recipient, "status": result["status"]},
    )

    return {
        "status":     result["status"],
        "recipient":  payload.recipient,
        "latency_ms": result.get("latency_ms"),
        "error":      result.get("error"),
        "test_run_id": run_id,
    }


@router.get("/test-runs")
async def list_test_runs(
    test_type: Optional[str] = Query(None, regex="^(connection|send)$"),
    limit: int = Query(10, ge=1, le=50),
):
    """Retourne les derniers tests de connexion / envoi."""
    return {"test_runs": TestRunRepo.list_recent(test_type=test_type, limit=limit)}


# ─────────────────────────────────────────────────────────────────────────────
# Santé du module mail
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    """
    Retourne l'état en temps réel du module mail.

    Utilisé par le tableau de bord et le badge de l'UI.
    """
    settings = MailSettingsRepo.get()
    stats = get_stats_24h()
    pending_drafts = DraftRepo.count_pending()
    dead_letters = DeadLetterRepo.count_active()

    last_conn_tests = TestRunRepo.list_recent(test_type="connection", limit=1)
    last_send_tests = TestRunRepo.list_recent(test_type="send", limit=1)

    return {
        "mail_enabled":    settings.mail_enabled,
        "configured":      bool(settings.imap_host and settings.smtp_host),
        "stats_24h":       stats,
        "drafts_pending":  pending_drafts,
        "dead_letters":    dead_letters,
        "last_test_connection": last_conn_tests[0] if last_conn_tests else None,
        "last_test_send":       last_send_tests[0] if last_send_tests else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Messages entrants
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/messages")
async def list_messages(
    status: Optional[str] = None,
    limit: int  = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Liste les messages entrants (paginé). body_html exclu."""
    msgs, total = InboundRepo.list_recent(limit=limit, offset=offset, status=status)
    return {
        "messages": [_inbound_to_dict(m) for m in msgs],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/messages/{msg_id}")
async def get_message(msg_id: int):
    """Détail d'un message entrant (sans body_html)."""
    msg = InboundRepo.get(msg_id)
    if not msg:
        raise HTTPException(status_code=404, detail="Message introuvable")
    return _inbound_to_dict(msg)


# ─────────────────────────────────────────────────────────────────────────────
# Threads
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/threads")
async def list_threads(limit: int = Query(20, ge=1, le=100)):
    """Liste les threads récents."""
    threads = ThreadRepo.list_recent(limit=limit)
    return {"threads": [_thread_to_dict(t) for t in threads]}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: int):
    """Détail d'un thread avec ses messages associés."""
    thread = ThreadRepo.get(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread introuvable")

    msgs, _ = InboundRepo.list_recent(limit=100, status=None)
    thread_msgs = [_inbound_to_dict(m) for m in msgs if m.thread_id == thread_id]

    return {
        "thread": _thread_to_dict(thread),
        "messages": thread_msgs,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Brouillons
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/drafts")
async def list_drafts(
    status: str = Query("pending", regex="^(pending|approved|modified_approved|rejected|expired)$"),
    limit: int  = Query(50, ge=1, le=200),
):
    """Liste les brouillons (par défaut : en attente de validation)."""
    if status == "pending":
        drafts = DraftRepo.list_pending(limit=limit)
    else:
        from .db import db_cursor
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM draft_approvals WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
            from .state import _row_to_draft
            drafts = [_row_to_draft(dict(r)) for r in cur.fetchall()]

    result = []
    for draft in drafts:
        d = _draft_to_dict(draft)
        # Enrichir avec le message entrant associé
        inbound = InboundRepo.get(draft.inbound_message_id) if draft.inbound_message_id else None
        d["inbound"] = _inbound_to_dict(inbound) if inbound else None
        result.append(d)

    return {"drafts": result, "count": len(result)}


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int):
    """Détail d'un brouillon avec le message entrant associé."""
    draft = DraftRepo.get(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Brouillon introuvable")

    d = _draft_to_dict(draft)
    inbound = InboundRepo.get(draft.inbound_message_id) if draft.inbound_message_id else None
    d["inbound"] = _inbound_to_dict(inbound) if inbound else None
    return d


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, request: Request, payload: ApproveRequest):
    """
    Approuve un brouillon.

    Si modified_response est fourni, la réponse modifiée est utilisée.
    Crée l'OutboundMessage prêt pour l'envoi.
    """
    reviewer = getattr(request.state, "username", "admin")
    svc = ApprovalService()
    try:
        outbound = svc.approve(draft_id, reviewer, payload.modified_response or None)
        return {
            "status": "approved",
            "outbound_id": outbound.id,
            "modified": bool(payload.modified_response),
        }
    except DraftNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DraftAlreadyReviewedError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int, request: Request, payload: RejectRequest):
    """Rejette un brouillon. Aucun email n'est envoyé."""
    reviewer = getattr(request.state, "username", "admin")
    svc = ApprovalService()
    try:
        svc.reject(draft_id, reviewer, payload.comment)
        return {"status": "rejected"}
    except DraftNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DraftAlreadyReviewedError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Erreurs / Dead-letters
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/errors")
async def list_errors(limit: int = Query(50, ge=1, le=200)):
    """Liste les dead-letters et erreurs actives."""
    return {"errors": DeadLetterRepo.list_active(limit=limit)}


@router.post("/errors/{error_id}/retry")
async def retry_error(error_id: int, request: Request):
    """Remet un dead-letter en file de traitement."""
    from .db import db_cursor, now_utc
    actor = getattr(request.state, "username", "admin")
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT status FROM errors_dead_letters WHERE id = ?", (error_id,)
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Erreur introuvable")
        if row[0] == "exhausted":
            raise HTTPException(status_code=409, detail="Épuisé — non retentable automatiquement")
        cur.execute(
            "UPDATE errors_dead_letters SET status='pending', next_retry_at=? WHERE id=?",
            (now_utc(), error_id),
        )
    return {"status": "queued"}


@router.post("/errors/{error_id}/ignore")
async def ignore_error(error_id: int, request: Request):
    """Marque une erreur comme ignorée."""
    actor = getattr(request.state, "username", "admin")
    DeadLetterRepo.mark_ignored(error_id, actor)
    return {"status": "ignored"}


# ─────────────────────────────────────────────────────────────────────────────
# Audit
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/audit")
async def list_audit(
    action: Optional[str] = None,
    inbound_id: Optional[int] = None,
    limit: int  = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Retourne les entrées d'audit (paginé)."""
    logs, total = AuditRepo.list_recent(
        limit=limit, offset=offset, action=action, inbound_id=inbound_id
    )
    return {"logs": logs, "total": total}


# ─────────────────────────────────────────────────────────────────────────────
# Déclenchement manuel de la sync IMAP (debug/admin)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sync")
async def manual_sync():
    """Déclenche manuellement une synchronisation IMAP."""
    svc = InboundService()
    result = svc.sync()
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Sérialiseurs
# ─────────────────────────────────────────────────────────────────────────────

def _inbound_to_dict(msg) -> dict:
    if msg is None:
        return {}
    return {
        "id":               msg.id,
        "message_id":       msg.message_id,
        "thread_id":        msg.thread_id,
        "from_address":     msg.from_address,
        "from_name":        msg.from_name,
        "to_addresses":     msg.get_to_addresses(),
        "subject":          msg.subject,
        "body_text":        (msg.body_text or "")[:2000],  # Tronqué pour l'UI
        # body_html exclu (XSS)
        "received_at":      msg.received_at.isoformat() if msg.received_at else None,
        "status":           msg.status.value if hasattr(msg.status, "value") else msg.status,
        "has_attachments":  msg.has_attachments,
        "attachment_count": msg.attachment_count,
        "is_auto_reply":    msg.is_auto_reply,
    }


def _thread_to_dict(thread) -> dict:
    return {
        "id":                   thread.id,
        "subject_normalized":   thread.subject_normalized,
        "message_count":        thread.message_count,
        "reply_count":          thread.reply_count,
        "status":               thread.status.value if hasattr(thread.status, "value") else thread.status,
        "last_message_at":      thread.last_message_at.isoformat() if thread.last_message_at else None,
        "last_reply_at":        thread.last_reply_at.isoformat() if thread.last_reply_at else None,
        "assigned_to":          thread.assigned_to,
    }


def _draft_to_dict(draft) -> dict:
    return {
        "id":                   draft.id,
        "inbound_message_id":   draft.inbound_message_id,
        "thread_id":            draft.thread_id,
        "generated_response":   draft.generated_response,
        "sources":              draft.get_sources(),
        "rag_query":            draft.rag_query,
        "confidence_score":     draft.confidence_score,
        "risk_score":           draft.risk_score,
        "classification":       draft.classification,
        "decision_reason":      draft.decision_reason,
        "status":               draft.status.value if hasattr(draft.status, "value") else draft.status,
        "reviewer":             draft.reviewer,
        "reviewer_comment":     draft.reviewer_comment,
        "created_at":           draft.created_at.isoformat() if draft.created_at else None,
        "reviewed_at":          draft.reviewed_at.isoformat() if draft.reviewed_at else None,
        "expires_at":           draft.expires_at.isoformat() if draft.expires_at else None,
    }
