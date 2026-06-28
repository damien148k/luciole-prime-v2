"""
Constantes du module mail — Luciole Prime.

Centralise tous les enums, valeurs par défaut, patterns
et listes de référence utilisés dans le module.
"""
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Statuts des messages
# ─────────────────────────────────────────────────────────────────────────────

class InboundStatus(str, Enum):
    RECEIVED    = "received"      # Reçu, pas encore traité
    CLASSIFYING = "classifying"   # Classification en cours
    CLASSIFIED  = "classified"    # Classifié, en attente de décision
    GENERATING  = "generating"    # Génération RAG en cours
    DRAFT_PENDING = "draft_pending"  # Brouillon créé, en attente de validation
    AUTO_QUEUED = "auto_queued"   # File d'envoi auto (V1 : jamais utilisé)
    PROCESSED   = "processed"     # Traitement terminé
    QUARANTINED = "quarantined"   # Mis en quarantaine (spam, boucle, etc.)
    ERROR       = "error"         # Erreur de traitement


class OutboundStatus(str, Enum):
    READY     = "ready"      # Prêt à envoyer (brouillon approuvé)
    SENDING   = "sending"    # Envoi en cours (verrou optimiste)
    SENT      = "sent"       # Envoyé avec succès
    FAILED    = "failed"     # Échec après tous les retry
    CANCELLED = "cancelled"  # Annulé manuellement


class DraftStatus(str, Enum):
    PENDING          = "pending"           # En attente de validation humaine
    APPROVED         = "approved"          # Approuvé tel quel
    MODIFIED_APPROVED = "modified_approved"  # Approuvé après modification
    REJECTED         = "rejected"          # Rejeté
    EXPIRED          = "expired"           # Délai d'expiration dépassé


class ThreadStatus(str, Enum):
    ACTIVE      = "active"
    CLOSED      = "closed"
    ESCALATED   = "escalated"    # Transmis à un humain
    QUARANTINED = "quarantined"


class TestStatus(str, Enum):
    OK      = "ok"
    ERROR   = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Catégories de classification
# ─────────────────────────────────────────────────────────────────────────────

class EmailCategory(str, Enum):
    QUESTION_DOCUMENTAIRE  = "question_documentaire"
    SUPPORT                = "support"
    DEMANDE_ADMINISTRATIVE = "demande_administrative"
    HORS_PERIMETRE         = "hors_perimetre"
    SPAM                   = "spam"
    SENSIBLE               = "sensible"
    BESOIN_HUMAIN          = "besoin_humain"


class RoutingDecision(str, Enum):
    AUTO_REPLY = "auto_reply"   # V1 : jamais déclenché (auto_reply_enabled=False)
    DRAFT      = "draft"        # Créer un brouillon pour validation
    ESCALATE   = "escalate"     # Transmettre à un humain
    QUARANTINE = "quarantine"   # Ignorer / quarantaine


# ─────────────────────────────────────────────────────────────────────────────
# Actions d'audit
# ─────────────────────────────────────────────────────────────────────────────

class AuditAction(str, Enum):
    EMAIL_RECEIVED             = "email_received"
    ANTI_LOOP_BLOCK            = "anti_loop_block"
    PARSE_ERROR                = "parse_error"
    ATTACHMENT_REJECTED        = "attachment_rejected"
    CLASSIFIED                 = "classified"
    RAG_QUERY                  = "rag_query"
    GUARDRAIL_BLOCK            = "guardrail_block"
    DRAFT_CREATED              = "draft_created"
    DRAFT_APPROVED             = "draft_approved"
    DRAFT_MODIFIED             = "draft_modified"
    DRAFT_REJECTED             = "draft_rejected"
    DRAFT_EXPIRED              = "draft_expired"
    EMAIL_SENT                 = "email_sent"
    SMTP_ERROR                 = "smtp_error"
    QUARANTINED                = "quarantined"
    SETTINGS_UPDATED           = "settings_updated"
    TEST_CONNECTION            = "test_connection"
    TEST_SEND                  = "test_send"
    ATTACHMENT_INDEX_REQUESTED = "attachment_index_requested"


class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


# ─────────────────────────────────────────────────────────────────────────────
# Types MIME et extensions autorisés pour les pièces jointes
# ─────────────────────────────────────────────────────────────────────────────

ALLOWED_ATTACHMENT_EXTENSIONS: frozenset = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
    ".txt", ".csv", ".jpg", ".jpeg", ".png", ".gif",
    ".bmp", ".webp", ".msg", ".eml",
})

BLOCKED_ATTACHMENT_EXTENSIONS: frozenset = frozenset({
    ".exe", ".bat", ".cmd", ".ps1", ".sh", ".msi", ".dll", ".com",
    ".js", ".vbs", ".wsf", ".scr", ".cpl", ".hta", ".jar",
    ".docm", ".xlsm", ".pptm",  # Documents avec macros Office
})

ALLOWED_MIME_TYPES: frozenset = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "text/plain",
    "text/csv",
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/bmp",
    "image/webp",
    "application/octet-stream",  # Fallback pour types non reconnus (contrôle extension)
})


# ─────────────────────────────────────────────────────────────────────────────
# Détection des auto-réponses / boucles
# ─────────────────────────────────────────────────────────────────────────────

AUTO_REPLY_HEADERS: frozenset = frozenset({
    "auto-submitted",
    "x-auto-response-suppress",
    "x-autorespond",
    "x-autoreply",
    "x-autorespond",
})

AUTO_REPLY_SUBJECT_PATTERNS: tuple = (
    "out of office",
    "absent du bureau",
    "automatic reply",
    "réponse automatique",
    "auto reply",
    "autoreply",
    "absence du bureau",
)

AUTO_REPLY_FROM_PATTERNS: tuple = (
    "noreply",
    "no-reply",
    "mailer-daemon",
    "postmaster",
    "donotreply",
    "do-not-reply",
    "daemon",
    "bounce",
)


# ─────────────────────────────────────────────────────────────────────────────
# Mots-clés sensibles par défaut (forcent brouillon)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SENSITIVE_KEYWORDS: tuple = (
    "licenciement",
    "contentieux",
    "plainte",
    "rgpd",
    "confidentiel",
    "disciplinaire",
    "juridique",
    "données personnelles",
    "harcèlement",
    "discrimination",
    "rupture conventionnelle",
    "procédure",
)


# ─────────────────────────────────────────────────────────────────────────────
# Retry / backoff SMTP (en secondes)
# ─────────────────────────────────────────────────────────────────────────────

RETRY_DELAYS_SECONDS: tuple = (60, 300, 900, 3600)   # 1min, 5min, 15min, 1h
MAX_REPLY_PER_THREAD_PER_HOUR: int = 3               # Anti-boucle


# ─────────────────────────────────────────────────────────────────────────────
# Valeurs par défaut des paramètres mail
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_IMAP_PORT: int = 993
DEFAULT_SMTP_PORT: int = 465
DEFAULT_POLL_INTERVAL: int = 60          # secondes
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.75
DEFAULT_RISK_THRESHOLD: float = 0.40
DEFAULT_MAX_ATTACHMENT_MB: int = 25
DRAFT_EXPIRY_DAYS: int = 7
IMAP_FETCH_BATCH: int = 50              # Nombre max d'emails récupérés par cycle
CONNECTION_TIMEOUT_SECONDS: int = 10
