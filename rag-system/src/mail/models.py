"""
Modèles de données du module mail — Luciole Prime.

Dataclasses Python (pas d'ORM) conformément aux conventions
du projet (sqlite3 brut, cohérent avec feedbacks.db / watcher.db).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from .constants import (
    DraftStatus,
    EmailCategory,
    InboundStatus,
    OutboundStatus,
    RoutingDecision,
    TestStatus,
    ThreadStatus,
)


# ─────────────────────────────────────────────────────────────────────────────
# Paramètres mail (singleton en DB)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MailSettings:
    """
    Paramètres IMAP/SMTP persistés en base (id=1, singleton).

    Les champs *_password_enc contiennent le mot de passe chiffré par Fernet.
    Ils ne sont JAMAIS sérialisés dans les réponses API.
    """
    id: int = 1

    mail_enabled: bool = False

    # IMAP
    imap_host: Optional[str] = None
    imap_port: int = 993
    imap_use_ssl: bool = True
    imap_username: Optional[str] = None
    imap_password_enc: Optional[str] = None   # Fernet-chiffré, usage interne
    imap_folder: str = "INBOX"
    imap_poll_interval_seconds: int = 60

    # SMTP
    smtp_host: Optional[str] = None
    smtp_port: int = 465
    smtp_use_tls: bool = True
    smtp_username: Optional[str] = None
    smtp_password_enc: Optional[str] = None   # Fernet-chiffré, usage interne

    # Identité sortante
    from_name: str = "Luciole"
    from_address: Optional[str] = None
    signature: str = ""

    # Politique
    auto_reply_enabled: bool = False        # V1 : toujours False
    confidence_threshold: float = 0.75
    risk_threshold: float = 0.40
    allowed_sender_domains: str = "[]"      # JSON array
    blocked_sender_domains: str = "[]"      # JSON array
    max_attachment_size_mb: int = 25
    attachment_indexing_enabled: bool = False
    index_name: str = field(default_factory=lambda: os.environ.get("MAIL_DEFAULT_INDEX", "documents"))
    sensitive_keywords: str = json.dumps([  # JSON array
        "licenciement", "contentieux", "plainte", "rgpd",
        "confidentiel", "disciplinaire", "juridique",
        "données personnelles", "harcèlement", "discrimination",
    ])

    updated_at: Optional[datetime] = None
    updated_by: str = "system"

    # ── helpers ────────────────────────────────────────────────────────────

    def get_allowed_domains(self) -> List[str]:
        try:
            return json.loads(self.allowed_sender_domains or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def get_blocked_domains(self) -> List[str]:
        try:
            return json.loads(self.blocked_sender_domains or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def get_sensitive_keywords(self) -> List[str]:
        try:
            return json.loads(self.sensitive_keywords or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def to_api_dict(self) -> dict:
        """Sérialisation sûre pour l'API (mots de passe masqués)."""
        return {
            "mail_enabled": self.mail_enabled,
            "imap_host": self.imap_host,
            "imap_port": self.imap_port,
            "imap_use_ssl": self.imap_use_ssl,
            "imap_username": self.imap_username,
            "imap_has_password": bool(self.imap_password_enc),
            "imap_folder": self.imap_folder,
            "imap_poll_interval_seconds": self.imap_poll_interval_seconds,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_use_tls": self.smtp_use_tls,
            "smtp_username": self.smtp_username,
            "smtp_has_password": bool(self.smtp_password_enc),
            "from_name": self.from_name,
            "from_address": self.from_address,
            "signature": self.signature,
            "auto_reply_enabled": self.auto_reply_enabled,
            "confidence_threshold": self.confidence_threshold,
            "risk_threshold": self.risk_threshold,
            "allowed_sender_domains": self.get_allowed_domains(),
            "blocked_sender_domains": self.get_blocked_domains(),
            "max_attachment_size_mb": self.max_attachment_size_mb,
            "attachment_indexing_enabled": self.attachment_indexing_enabled,
            "index_name": self.index_name,
            "sensitive_keywords": self.get_sensitive_keywords(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Thread / conversation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MailThread:
    """Regroupe les messages d'une même conversation email."""
    id: Optional[int] = None
    subject_normalized: str = ""        # Sujet sans Re:/Fwd:/Tr:
    first_message_id: str = ""          # Message-ID du 1er email

    message_count: int = 1
    reply_count: int = 0                # Réponses envoyées par Luciole
    last_message_at: Optional[datetime] = None
    last_reply_at: Optional[datetime] = None

    status: ThreadStatus = ThreadStatus.ACTIVE
    assigned_to: Optional[str] = None  # Username si escalade
    thread_summary: Optional[str] = None

    luciole_reply_count_last_hour: int = 0
    last_reply_hour: Optional[str] = None   # Format YYYY-MM-DD-HH

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Email entrant
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InboundMessage:
    """Email reçu par Luciole."""
    id: Optional[int] = None
    message_id: str = ""                # Header Message-ID (déduplication)
    thread_id: Optional[int] = None

    from_address: str = ""
    from_name: Optional[str] = None
    to_addresses: str = "[]"            # JSON array
    cc_addresses: str = "[]"            # JSON array
    reply_to: Optional[str] = None
    subject: str = ""

    body_text: Optional[str] = None     # Corps nettoyé (sans quotes/sigs)
    body_text_raw: Optional[str] = None # Corps avant nettoyage
    body_html: Optional[str] = None     # HTML original

    in_reply_to: Optional[str] = None
    references_header: Optional[str] = None

    received_at: Optional[datetime] = None
    imap_uid: Optional[str] = None

    status: InboundStatus = InboundStatus.RECEIVED

    has_attachments: bool = False
    attachment_count: int = 0

    is_auto_reply: bool = False
    auto_reply_reason: Optional[str] = None

    def get_to_addresses(self) -> List[str]:
        try:
            return json.loads(self.to_addresses or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def get_cc_addresses(self) -> List[str]:
        try:
            return json.loads(self.cc_addresses or "[]")
        except (json.JSONDecodeError, TypeError):
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Pièce jointe
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Attachment:
    """Pièce jointe d'un email entrant."""
    id: Optional[int] = None
    inbound_message_id: Optional[int] = None

    filename_original: str = ""
    filename_stored: str = ""           # UUID4 + extension nettoyée
    content_type_declared: Optional[str] = None
    content_type_detected: Optional[str] = None  # Via magic bytes
    size_bytes: int = 0
    sha256_hash: str = ""

    is_allowed_type: Optional[bool] = None
    is_size_ok: Optional[bool] = None
    is_safe: Optional[bool] = None
    scan_detail: Optional[str] = None

    indexed_in_rag: bool = False
    indexing_requested_by: Optional[str] = None
    indexing_requested_at: Optional[datetime] = None
    indexing_done_at: Optional[datetime] = None

    created_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Résultat de classification
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Résultat de la classification d'un email entrant."""
    id: Optional[int] = None
    inbound_message_id: Optional[int] = None

    category: EmailCategory = EmailCategory.HORS_PERIMETRE
    confidence_score: float = 0.0
    risk_score: float = 0.0

    decision: RoutingDecision = RoutingDecision.QUARANTINE
    decision_reason: str = ""

    classified_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Brouillon pour validation humaine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DraftApproval:
    """Brouillon de réponse généré par Luciole, en attente de validation."""
    id: Optional[int] = None
    inbound_message_id: Optional[int] = None
    thread_id: Optional[int] = None

    generated_response: str = ""
    sources_used: str = "[]"            # JSON: [{file_name, score}]
    passages_used: str = "[]"           # JSON: [{text, file_name, score, page?, section?}]
    rag_query: Optional[str] = None

    confidence_score: float = 0.0
    risk_score: float = 0.0
    classification: Optional[str] = None
    decision_reason: str = ""

    status: DraftStatus = DraftStatus.PENDING
    reviewer: Optional[str] = None
    reviewer_comment: Optional[str] = None
    final_response: Optional[str] = None  # Réponse après modification éventuelle

    created_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    def get_sources(self) -> list:
        try:
            return json.loads(self.sources_used or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def get_passages(self) -> list:
        try:
            return json.loads(self.passages_used or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def effective_response(self) -> str:
        """Réponse à envoyer : modifiée si disponible, sinon générée."""
        return self.final_response or self.generated_response


# ─────────────────────────────────────────────────────────────────────────────
# Email sortant
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OutboundMessage:
    """Email à envoyer, créé après approbation d'un brouillon."""
    id: Optional[int] = None
    inbound_message_id: Optional[int] = None
    thread_id: Optional[int] = None
    draft_approval_id: Optional[int] = None

    to_address: str = ""
    cc_addresses: str = "[]"            # JSON array
    subject: str = ""
    body_text: str = ""
    body_html: Optional[str] = None

    message_id_header: Optional[str] = None  # Message-ID généré pour le mail sortant
    in_reply_to: Optional[str] = None
    references_header: Optional[str] = None

    sources_used: str = "[]"            # JSON
    confidence_score: Optional[float] = None
    rag_query: Optional[str] = None

    status: OutboundStatus = OutboundStatus.READY
    retry_count: int = 0
    last_error: Optional[str] = None
    next_retry_at: Optional[datetime] = None

    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Résultat de test IMAP/SMTP
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MailTestRun:
    """Historique d'un test de connexion ou d'envoi."""
    id: Optional[int] = None
    test_type: str = "connection"   # "connection" ou "send"

    imap_status: Optional[str] = None
    imap_detail: Optional[str] = None
    imap_latency_ms: Optional[int] = None
    imap_error_code: Optional[str] = None

    smtp_status: Optional[str] = None
    smtp_detail: Optional[str] = None
    smtp_latency_ms: Optional[int] = None
    smtp_error_code: Optional[str] = None

    test_recipient: Optional[str] = None
    send_status: Optional[str] = None

    triggered_by: str = "admin"
    total_duration_ms: Optional[int] = None
    created_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Entrée d'audit
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditLog:
    """Trace d'une action dans le cycle de traitement mail."""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    action: str = ""
    inbound_message_id: Optional[int] = None
    outbound_message_id: Optional[int] = None
    thread_id: Optional[int] = None
    draft_approval_id: Optional[int] = None

    actor: str = "system"
    outcome: Optional[str] = None
    detail: Optional[str] = None    # JSON libre
    duration_ms: Optional[int] = None


# ─────────────────────────────────────────────────────────────────────────────
# Dead-letter / erreur
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeadLetter:
    """Message en erreur après épuisement des retry."""
    id: Optional[int] = None

    error_type: str = ""
    inbound_message_id: Optional[int] = None
    raw_payload: Optional[str] = None
    error_message: str = ""
    stack_trace: Optional[str] = None

    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: Optional[datetime] = None

    status: str = "pending"     # pending | retrying | exhausted | resolved | ignored
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None

    created_at: Optional[datetime] = None


# ─────────────────────────────────────────────────────────────────────────────
# Objets intermédiaires (non persistés directement)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawEmail:
    """Email brut récupéré par IMAP avant parsing."""
    uid: str
    raw_bytes: bytes


@dataclass
class ParsedEmail:
    """Email parsé depuis le MIME, avant stockage en base."""
    message_id: str
    from_address: str
    from_name: Optional[str]
    to_addresses: List[str]
    cc_addresses: List[str]
    reply_to: Optional[str]
    subject: str
    body_text: Optional[str]        # Texte nettoyé
    body_text_raw: Optional[str]    # Texte brut avant nettoyage
    body_html: Optional[str]
    in_reply_to: Optional[str]
    references: List[str]
    attachments: List["ParsedAttachment"] = field(default_factory=list)
    is_auto_reply: bool = False
    auto_reply_reason: Optional[str] = None


@dataclass
class ParsedAttachment:
    """Pièce jointe extraite du MIME avant stockage."""
    filename: str
    content_type: str
    data: bytes
    size_bytes: int


@dataclass
class ConnectionTestResult:
    """Résultat d'un test de connexion IMAP ou SMTP."""
    status: TestStatus
    detail: str
    latency_ms: int
    error_code: Optional[str] = None
