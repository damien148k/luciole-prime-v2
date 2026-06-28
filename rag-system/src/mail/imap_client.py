"""
Client IMAP — Module mail Luciole Prime.

Gère la connexion au serveur IMAP, la récupération des emails
non lus et le marquage des messages traités.

Utilise imaplib (stdlib) de manière synchrone ;
appeler depuis asyncio via run_in_executor.
"""
from __future__ import annotations

import imaplib
import socket
import ssl
import time
from typing import List, Optional

from loguru import logger

from .config import MAIL_ATTACHMENTS_PATH, decrypt_secret
from .constants import CONNECTION_TIMEOUT_SECONDS, IMAP_FETCH_BATCH, TestStatus
from .exceptions import (
    IMAPAuthError,
    IMAPConnectionError,
    IMAPTLSError,
    IMAPTimeoutError,
    MailNotConfiguredError,
)
from .models import ConnectionTestResult, MailSettings, RawEmail


class IMAPClient:
    """
    Client IMAP pour Luciole Prime.

    Cycle de vie recommandé :
        client = IMAPClient(settings)
        client.connect()
        emails = client.fetch_unseen()
        # traiter les emails
        client.mark_as_seen(uid)
        client.disconnect()
    """

    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings
        self._conn: Optional[imaplib.IMAP4 | imaplib.IMAP4_SSL] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Connexion / déconnexion
    # ─────────────────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """
        Ouvre la connexion IMAP et s'authentifie.

        Lève IMAPConnectionError / IMAPAuthError / IMAPTLSError selon le cas.
        """
        s = self._settings
        if not s.imap_host or not s.imap_username:
            raise MailNotConfiguredError("Hôte ou utilisateur IMAP non configuré")

        password = decrypt_secret(s.imap_password_enc or "")
        if not password:
            raise MailNotConfiguredError("Mot de passe IMAP non configuré")

        timeout = CONNECTION_TIMEOUT_SECONDS
        host = s.imap_host
        port = s.imap_port

        try:
            if s.imap_use_ssl:
                ctx = ssl.create_default_context()
                self._conn = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
            else:
                # Plain IMAP sans TLS — mode LAN / dev
                self._conn = imaplib.IMAP4(host, port)
        except ssl.SSLError as e:
            raise IMAPTLSError(f"Erreur TLS IMAP {host}:{port} — {e}") from e
        except socket.timeout:
            raise IMAPTimeoutError(f"Timeout connexion IMAP {host}:{port}") from None
        except (ConnectionRefusedError, OSError) as e:
            raise IMAPConnectionError(f"Connexion IMAP refusée {host}:{port} — {e}") from e

        try:
            self._conn.login(s.imap_username, password)
        except imaplib.IMAP4.error as e:
            self.disconnect()
            raise IMAPAuthError(f"Authentification IMAP échouée : {e}") from e

        logger.debug(f"IMAP connecté : {host}:{port} ({s.imap_username})")

    def disconnect(self) -> None:
        """Ferme proprement la connexion IMAP."""
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def _ensure_connected(self) -> None:
        """Vérifie que la connexion est active."""
        if self._conn is None:
            raise IMAPConnectionError("Client IMAP non connecté — appelez connect() d'abord")

    # ─────────────────────────────────────────────────────────────────────────
    # Test de connexion (sans lire de messages)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def test_connection(cls, settings: MailSettings) -> ConnectionTestResult:
        """
        Teste la connexion IMAP sans lire de messages.

        Effectue : connexion TCP → TLS → LOGIN → SELECT INBOX → LOGOUT.
        Retourne un ConnectionTestResult avec le statut et la latence.
        """
        s = settings
        if not s.imap_host or not s.imap_username:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail="Hôte ou utilisateur IMAP non configuré",
                latency_ms=0,
                error_code="NOT_CONFIGURED",
            )

        password = decrypt_secret(s.imap_password_enc or "")
        if not password:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail="Mot de passe IMAP non configuré",
                latency_ms=0,
                error_code="NO_PASSWORD",
            )

        t_start = time.monotonic()
        conn = None

        try:
            if s.imap_use_ssl:
                ctx = ssl.create_default_context()
                conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, ssl_context=ctx)
            else:
                conn = imaplib.IMAP4(s.imap_host, s.imap_port)

            conn.login(s.imap_username, password)

            # Sélectionner le dossier pour vérifier son accès
            typ, data = conn.select(s.imap_folder, readonly=True)
            if typ != "OK":
                msg_count = "?"
            else:
                msg_count = data[0].decode() if data and data[0] else "0"

            latency = int((time.monotonic() - t_start) * 1000)
            return ConnectionTestResult(
                status=TestStatus.OK,
                detail=f"LOGIN OK — {s.imap_folder}: {msg_count} messages",
                latency_ms=latency,
            )

        except ssl.SSLError as e:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="TLS_ERROR",
            )
        except socket.timeout:
            return ConnectionTestResult(
                status=TestStatus.TIMEOUT,
                detail=f"Timeout ({CONNECTION_TIMEOUT_SECONDS}s)",
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="TIMEOUT",
            )
        except imaplib.IMAP4.error as e:
            code = "AUTH_FAILED" if "auth" in str(e).lower() else "IMAP_ERROR"
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code=code,
            )
        except Exception as e:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="CONNECTION_REFUSED",
            )
        finally:
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # Lecture des emails non lus
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_unseen(self) -> List[RawEmail]:
        """
        Récupère les emails non lus (UNSEEN) du dossier configuré.

        Retourne une liste de RawEmail (uid + bytes bruts du message).
        Ne marque PAS les messages comme lus — c'est fait après commit DB.
        """
        self._ensure_connected()
        folder = self._settings.imap_folder

        try:
            typ, _ = self._conn.select(folder)
            if typ != "OK":
                logger.warning(f"IMAP SELECT échoué pour {folder}")
                return []
        except imaplib.IMAP4.error as e:
            raise IMAPConnectionError(f"Impossible de sélectionner {folder} : {e}") from e

        try:
            typ, uid_list = self._conn.uid("search", None, "UNSEEN")
            if typ != "OK" or not uid_list or not uid_list[0]:
                return []

            uids = uid_list[0].split()
            if not uids:
                return []

            # Limiter le batch pour éviter les surcharges
            batch_uids = uids[:IMAP_FETCH_BATCH]
            uid_str = b",".join(batch_uids)

            typ, messages = self._conn.uid("fetch", uid_str, "(RFC822)")
            if typ != "OK":
                return []

        except imaplib.IMAP4.error as e:
            raise IMAPConnectionError(f"Erreur lecture IMAP : {e}") from e

        raw_emails: List[RawEmail] = []
        for i in range(0, len(messages), 2):
            try:
                meta = messages[i]
                if not isinstance(meta, tuple) or len(meta) < 2:
                    continue
                # Extraire l'UID depuis la réponse IMAP
                meta_str = meta[0].decode("ascii", errors="ignore")
                uid = meta_str.split("UID ")[1].split(" ")[0] if "UID " in meta_str else str(i)
                raw_bytes = meta[1]
                raw_emails.append(RawEmail(uid=uid, raw_bytes=raw_bytes))
            except Exception as e:
                logger.warning(f"Erreur extraction email IMAP à index {i} : {e}")
                continue

        logger.info(f"IMAP : {len(raw_emails)} email(s) non lu(s) récupéré(s)")
        return raw_emails

    def mark_as_seen(self, uid: str) -> None:
        r"""
        Marque un message comme lu (\Seen) sur le serveur IMAP.

        Appelé APRÈS que le message a été commité en DB avec succès.
        """
        self._ensure_connected()
        try:
            self._conn.uid("store", uid, "+FLAGS", r"(\Seen)")
            logger.debug(f"IMAP UID {uid} marqué comme lu")
        except imaplib.IMAP4.error as e:
            # Non critique : logguer mais ne pas propager
            logger.warning(f"Impossible de marquer UID {uid} comme lu : {e}")

    def get_mailbox_stats(self) -> dict:
        r"""Retourne des statistiques basiques sur la boîte (total, non lus)."""
        self._ensure_connected()
        folder = self._settings.imap_folder
        try:
            self._conn.select(folder, readonly=True)
            _, total = self._conn.search(None, "ALL")
            _, unseen = self._conn.search(None, "UNSEEN")
            total_count = len(total[0].split()) if total[0] else 0
            unseen_count = len(unseen[0].split()) if unseen[0] else 0
            return {
                "folder": folder,
                "total": total_count,
                "unseen": unseen_count,
            }
        except Exception as e:
            logger.warning(f"Impossible de lire les stats IMAP : {e}")
            return {"folder": folder, "total": 0, "unseen": 0, "error": str(e)}
