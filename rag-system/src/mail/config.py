"""
Configuration du module mail — Luciole Prime.

Charge les paramètres depuis les variables d'environnement.
Gère le chiffrement Fernet des secrets IMAP/SMTP.

Stratégie de clé :
  1. Variable d'environnement MAIL_ENCRYPTION_KEY (recommandé)
  2. Fichier {MAIL_DB_PATH}/../.mail_key (généré automatiquement si absent)
  Avertissement visible si la clé n'est pas dans l'env var.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

from loguru import logger

# Fernet est dans le package 'cryptography' (à ajouter aux requirements)
try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    logger.error(
        "Package 'cryptography' manquant. "
        "Installez-le avec : pip install cryptography>=42.0.0\n"
        "Les mots de passe mail seront stockés EN CLAIR — NON RECOMMANDÉ en production."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Variables d'environnement
# ─────────────────────────────────────────────────────────────────────────────

MAIL_DB_PATH = os.environ.get(
    "MAIL_DB_PATH", "/app/feedbacks/mail.db"
)
MAIL_ATTACHMENTS_PATH = os.environ.get(
    "MAIL_ATTACHMENTS_PATH", "/app/feedbacks/mail_attachments"
)
MAIL_ENCRYPTION_KEY = os.environ.get("MAIL_ENCRYPTION_KEY", "")
MAIL_WORKER_PORT = int(os.environ.get("MAIL_WORKER_PORT", "8510"))
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000")
MAIL_DEFAULT_INDEX = os.environ.get("MAIL_DEFAULT_INDEX", "documents")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")


# ─────────────────────────────────────────────────────────────────────────────
# Gestion de la clé de chiffrement
# ─────────────────────────────────────────────────────────────────────────────

def _key_file_path() -> Path:
    """Chemin du fichier de clé de secours (même dossier que la DB)."""
    return Path(MAIL_DB_PATH).parent / ".mail_key"


def _get_or_create_key() -> Optional[bytes]:
    """
    Retourne la clé Fernet (bytes) selon la priorité :
      1. Env var MAIL_ENCRYPTION_KEY
      2. Fichier .mail_key
      3. Génération + sauvegarde fichier (avec avertissement)

    Retourne None si cryptography n'est pas disponible.
    """
    if not _FERNET_AVAILABLE:
        return None

    # Priorité 1 : env var
    if MAIL_ENCRYPTION_KEY:
        try:
            key = MAIL_ENCRYPTION_KEY.encode()
            Fernet(key)  # Valide le format
            return key
        except Exception:
            logger.error(
                "MAIL_ENCRYPTION_KEY invalide (doit être une clé Fernet base64 44 chars). "
                "Générez-en une avec : python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

    # Priorité 2 : fichier
    key_path = _key_file_path()
    if key_path.exists():
        try:
            key = key_path.read_bytes().strip()
            Fernet(key)
            logger.warning(
                f"Clé mail chargée depuis {key_path}. "
                "Définissez MAIL_ENCRYPTION_KEY en variable d'environnement pour plus de sécurité."
            )
            return key
        except Exception:
            logger.warning(f"Fichier de clé corrompu : {key_path}. Régénération.")

    # Priorité 3 : génération
    key = Fernet.generate_key()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        key_path.chmod(0o600)
        logger.warning(
            f"⚠️  Nouvelle clé de chiffrement mail générée et sauvegardée dans {key_path}. "
            "IMPORTANT : copiez-la dans MAIL_ENCRYPTION_KEY avant tout redéploiement "
            "pour ne pas perdre l'accès aux mots de passe stockés."
        )
    except OSError as e:
        logger.error(f"Impossible de sauvegarder la clé mail : {e}")

    return key


# Singleton de la clé (chargé une fois au démarrage du module)
_FERNET_KEY: Optional[bytes] = _get_or_create_key()
_fernet: Optional["Fernet"] = Fernet(_FERNET_KEY) if (_FERNET_KEY and _FERNET_AVAILABLE) else None


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions de chiffrement/déchiffrement
# ─────────────────────────────────────────────────────────────────────────────

def encrypt_secret(plaintext: str) -> str:
    """
    Chiffre un secret (mot de passe) avec Fernet.

    Retourne le token base64 encodé sous forme de str.
    Si cryptography est indisponible, retourne le texte en clair avec avertissement.
    """
    if not plaintext:
        return ""
    if _fernet:
        return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
    logger.warning("Chiffrement indisponible — stockage en clair (NON SÉCURISÉ)")
    return plaintext


def decrypt_secret(ciphertext: str) -> Optional[str]:
    """
    Déchiffre un secret stocké en base.

    Retourne le texte en clair ou None si déchiffrement impossible.
    """
    if not ciphertext:
        return None
    if _fernet:
        try:
            return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except Exception:
            if _FERNET_AVAILABLE:
                try:
                    from cryptography.fernet import InvalidToken
                    pass
                except ImportError:
                    pass
            # Fallback : le texte n'était peut-être pas chiffré (migration)
            logger.warning("Déchiffrement échoué — le secret est peut-être en clair")
            return ciphertext
    return ciphertext  # Fallback sans cryptography


def is_encryption_available() -> bool:
    """Indique si le chiffrement Fernet est opérationnel."""
    return _fernet is not None
