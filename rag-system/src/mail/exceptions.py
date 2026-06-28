"""
Exceptions du module mail — Luciole Prime.

Hiérarchie claire pour distinguer les catégories d'erreurs
et permettre un traitement différencié dans les services.
"""


class MailError(Exception):
    """Erreur de base du module mail."""


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

class MailConfigError(MailError):
    """Erreur de configuration (paramètres manquants ou invalides)."""


class MailNotConfiguredError(MailConfigError):
    """Le module mail n'est pas configuré (IMAP/SMTP non renseigné)."""


class MailEncryptionError(MailConfigError):
    """Erreur de chiffrement/déchiffrement des secrets mail."""


# ─────────────────────────────────────────────────────────────────────────────
# IMAP
# ─────────────────────────────────────────────────────────────────────────────

class IMAPError(MailError):
    """Erreur IMAP générique."""


class IMAPConnectionError(IMAPError):
    """Impossible de se connecter au serveur IMAP."""


class IMAPAuthError(IMAPError):
    """Échec d'authentification IMAP."""


class IMAPTimeoutError(IMAPError):
    """Timeout lors de la connexion ou de la commande IMAP."""


class IMAPTLSError(IMAPError):
    """Erreur TLS lors de la connexion IMAP."""


# ─────────────────────────────────────────────────────────────────────────────
# SMTP
# ─────────────────────────────────────────────────────────────────────────────

class SMTPError(MailError):
    """Erreur SMTP générique."""


class SMTPConnectionError(SMTPError):
    """Impossible de se connecter au serveur SMTP."""


class SMTPAuthError(SMTPError):
    """Échec d'authentification SMTP."""


class SMTPTimeoutError(SMTPError):
    """Timeout lors de la connexion ou de l'envoi SMTP."""


class SMTPTLSError(SMTPError):
    """Erreur TLS lors de la connexion SMTP."""


class SMTPSendError(SMTPError):
    """Erreur lors de l'envoi du message."""


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

class ParseError(MailError):
    """Erreur de parsing d'un email MIME."""


class AttachmentRejectedError(MailError):
    """Pièce jointe rejetée (type non autorisé, taille dépassée, etc.)."""

    def __init__(self, filename: str, reason: str):
        self.filename = filename
        self.reason = reason
        super().__init__(f"Pièce jointe rejetée — {filename}: {reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Traitement
# ─────────────────────────────────────────────────────────────────────────────

class ClassificationError(MailError):
    """Erreur lors de la classification d'un email."""


class RAGQueryError(MailError):
    """Erreur lors de l'appel au moteur RAG."""


class GuardrailError(MailError):
    """La réponse générée a été bloquée par les guardrails."""


class AntiLoopError(MailError):
    """Email détecté comme boucle de réponse automatique."""


class DraftNotFoundError(MailError):
    """Brouillon introuvable."""


class DraftAlreadyReviewedError(MailError):
    """Brouillon déjà approuvé ou rejeté."""


class DuplicateMessageError(MailError):
    """Email déjà reçu et traité (déduplication par Message-ID)."""


class OutboundAlreadySentError(MailError):
    """Tentative d'envoi d'un message déjà envoyé."""
