"""
Client SMTP — Module mail Luciole Prime.

Gère la connexion au serveur SMTP, le test de connexion
et l'envoi des emails (test et brouillons validés).

Utilise smtplib (stdlib) de manière synchrone ;
appeler depuis asyncio via run_in_executor.
"""
from __future__ import annotations

import smtplib
import socket
import ssl
import time
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from loguru import logger

from .config import decrypt_secret
from .constants import CONNECTION_TIMEOUT_SECONDS, TestStatus
from .exceptions import (
    MailNotConfiguredError,
    SMTPAuthError,
    SMTPConnectionError,
    SMTPSendError,
    SMTPTLSError,
    SMTPTimeoutError,
)
from .models import ConnectionTestResult, MailSettings, OutboundMessage


class SMTPClient:
    """
    Client SMTP pour Luciole Prime.

    Ne garde pas de connexion persistante — une nouvelle connexion
    est ouverte pour chaque envoi (évite les timeouts d'inactivité).
    """

    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings

    # ─────────────────────────────────────────────────────────────────────────
    # Test de connexion (sans envoi)
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def test_connection(cls, settings: MailSettings) -> ConnectionTestResult:
        """
        Teste la connexion SMTP sans envoyer de message.

        Effectue : connexion TCP → TLS → AUTH → QUIT.
        """
        s = settings
        if not s.smtp_host or not s.smtp_username:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail="Hôte ou utilisateur SMTP non configuré",
                latency_ms=0,
                error_code="NOT_CONFIGURED",
            )

        password = decrypt_secret(s.smtp_password_enc or "")
        if not password:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail="Mot de passe SMTP non configuré",
                latency_ms=0,
                error_code="NO_PASSWORD",
            )

        t_start = time.monotonic()
        server = None

        try:
            server = cls._connect_smtp(s, password)
            latency = int((time.monotonic() - t_start) * 1000)
            return ConnectionTestResult(
                status=TestStatus.OK,
                detail=f"EHLO + AUTH OK ({s.smtp_host}:{s.smtp_port})",
                latency_ms=latency,
            )

        except SMTPTLSError as e:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="TLS_ERROR",
            )
        except SMTPAuthError as e:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="AUTH_FAILED",
            )
        except SMTPTimeoutError:
            return ConnectionTestResult(
                status=TestStatus.TIMEOUT,
                detail=f"Timeout ({CONNECTION_TIMEOUT_SECONDS}s)",
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="TIMEOUT",
            )
        except Exception as e:
            return ConnectionTestResult(
                status=TestStatus.ERROR,
                detail=str(e),
                latency_ms=int((time.monotonic() - t_start) * 1000),
                error_code="CONNECTION_REFUSED",
            )
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # Envoi d'un mail de test
    # ─────────────────────────────────────────────────────────────────────────

    def send_test_email(self, recipient: str) -> dict:
        """
        Envoie un email de test vers l'adresse indiquée.

        Retourne un dict { status, latency_ms, error }.
        Ce message ne passe pas par la table outbound_messages.
        """
        s = self._settings
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        body_text = (
            "Ceci est un email de test envoyé par Luciole Prime.\n\n"
            f"Serveur SMTP : {s.smtp_host}:{s.smtp_port}\n"
            f"Expéditeur   : {s.from_address}\n"
            f"Date/heure   : {now_str}\n\n"
            "Si vous recevez cet email, la configuration SMTP est opérationnelle."
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Luciole Test] Vérification envoi — {now_str}"
        msg["From"] = f"{s.from_name} <{s.from_address}>"
        msg["To"] = recipient
        msg["X-Mailer"] = "Luciole-Prime/1.0"
        msg.attach(MIMEText(body_text, "plain", "utf-8"))

        t_start = time.monotonic()
        server = None

        try:
            password = decrypt_secret(s.smtp_password_enc or "")
            server = self._connect_smtp(s, password)
            server.sendmail(s.from_address, [recipient], msg.as_bytes())
            latency = int((time.monotonic() - t_start) * 1000)
            logger.info(f"Mail de test envoyé à {recipient} ({latency}ms)")
            return {"status": "sent", "latency_ms": latency, "error": None}

        except Exception as e:
            latency = int((time.monotonic() - t_start) * 1000)
            logger.error(f"Échec envoi mail de test : {e}")
            return {"status": "failed", "latency_ms": latency, "error": str(e)}
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # Envoi d'un message sortant (brouillon approuvé)
    # ─────────────────────────────────────────────────────────────────────────

    def send(self, outbound: OutboundMessage) -> None:
        """
        Envoie un message sortant (brouillon approuvé).

        Construit un email MIME avec les headers de threading appropriés.
        Lève SMTPSendError en cas d'échec.
        """
        s = self._settings
        if not s.from_address:
            raise MailNotConfiguredError("Adresse expéditeur non configurée")

        # Construire l'email MIME
        msg = self._build_mime(outbound)

        try:
            password = decrypt_secret(s.smtp_password_enc or "")
            server = self._connect_smtp(s, password)
            try:
                refused = server.sendmail(
                    s.from_address,
                    [outbound.to_address],
                    msg.as_bytes(),
                )
                if refused:
                    raise SMTPSendError(f"Destinataires refusés : {refused}")
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
        except (SMTPConnectionError, SMTPAuthError, SMTPTLSError, SMTPTimeoutError):
            raise
        except smtplib.SMTPException as e:
            raise SMTPSendError(f"Erreur SMTP lors de l'envoi : {e}") from e

        logger.info(
            f"Email envoyé — to={outbound.to_address} "
            f"subject='{outbound.subject[:60]}'"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Méthodes internes
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _connect_smtp(
        settings: MailSettings,
        password: str,
    ) -> smtplib.SMTP | smtplib.SMTP_SSL:
        """
        Ouvre une connexion SMTP authentifiée.

        Retourne l'objet server prêt à l'emploi.
        Lève une exception typée en cas d'erreur.
        """
        host = settings.smtp_host
        port = settings.smtp_port
        timeout = CONNECTION_TIMEOUT_SECONDS

        try:
            if settings.smtp_use_tls and port in (465,):
                # SMTP sur SSL direct (port 465)
                ctx = ssl.create_default_context()
                server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
            else:
                server = smtplib.SMTP(host, port, timeout=timeout)
                if settings.smtp_use_tls:
                    ctx = ssl.create_default_context()
                    server.starttls(context=ctx)
        except ssl.SSLError as e:
            raise SMTPTLSError(f"Erreur TLS SMTP : {e}") from e
        except socket.timeout:
            raise SMTPTimeoutError(f"Timeout connexion SMTP {host}:{port}")
        except (ConnectionRefusedError, OSError) as e:
            raise SMTPConnectionError(f"Connexion SMTP refusée {host}:{port} — {e}") from e

        try:
            server.login(settings.smtp_username, password)
        except smtplib.SMTPAuthenticationError as e:
            try:
                server.quit()
            except Exception:
                pass
            raise SMTPAuthError(f"Authentification SMTP échouée : {e}") from e
        except smtplib.SMTPException as e:
            try:
                server.quit()
            except Exception:
                pass
            raise SMTPConnectionError(f"Erreur SMTP : {e}") from e

        return server

    def _build_mime(self, outbound: OutboundMessage) -> MIMEMultipart:
        """Construit un email MIME complet à partir d'un OutboundMessage."""
        s = self._settings

        # Générer un Message-ID unique si absent
        msg_id = outbound.message_id_header or f"<{uuid.uuid4().hex}@luciole-prime>"

        msg = MIMEMultipart("alternative")
        msg["Subject"]    = outbound.subject
        msg["From"]       = f"{s.from_name} <{s.from_address}>"
        msg["To"]         = outbound.to_address
        msg["Message-ID"] = msg_id
        msg["X-Mailer"]   = "Luciole-Prime/1.0"

        if outbound.in_reply_to:
            msg["In-Reply-To"] = outbound.in_reply_to
        if outbound.references_header:
            msg["References"] = outbound.references_header

        # Corps texte (toujours présent)
        full_body = outbound.body_text
        if s.signature:
            full_body += f"\n\n-- \n{s.signature}"

        msg.attach(MIMEText(full_body, "plain", "utf-8"))

        # Corps HTML optionnel (simple wrapper)
        if outbound.body_html:
            msg.attach(MIMEText(outbound.body_html, "html", "utf-8"))

        return msg
