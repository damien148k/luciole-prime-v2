"""
Parser MIME — Module mail Luciole Prime.

Décode les emails MIME bruts en structures ParsedEmail exploitables.
Gère le nettoyage du corps (suppression des citations/signatures),
la détection des auto-réponses et l'extraction des pièces jointes.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from email import message_from_bytes, policy
from email.header import decode_header
from email.message import Message
from pathlib import Path
from typing import List, Optional

from loguru import logger

from .constants import (
    AUTO_REPLY_FROM_PATTERNS,
    AUTO_REPLY_HEADERS,
    AUTO_REPLY_SUBJECT_PATTERNS,
    BLOCKED_ATTACHMENT_EXTENSIONS,
)
from .exceptions import AttachmentRejectedError, ParseError
from .models import ParsedAttachment, ParsedEmail


class EmailParser:
    """
    Décode un email MIME brut (bytes) en ParsedEmail.

    Thread-safe (aucun état mutable entre les appels).
    """

    def parse(self, raw: bytes) -> ParsedEmail:
        """
        Décode un email MIME brut en ParsedEmail.

        Lève ParseError si le message est invalide ou vide.
        """
        if not raw:
            raise ParseError("Email brut vide")

        try:
            msg = message_from_bytes(raw, policy=policy.default)
        except Exception as e:
            raise ParseError(f"Impossible de décoder le MIME : {e}") from e

        message_id   = self._decode_header_value(msg.get("Message-ID", "")).strip()
        if not message_id:
            # Générer un ID de secours pour la déduplication
            message_id = f"<generated-{hashlib.md5(raw[:200]).hexdigest()}@luciole>"
            logger.warning(f"Email sans Message-ID — ID généré : {message_id}")

        from_addr, from_name = self._parse_address(msg.get("From", ""))
        to_addrs             = self._parse_address_list(msg.get("To", ""))
        cc_addrs             = self._parse_address_list(msg.get("CC", ""))
        reply_to             = self._parse_address(msg.get("Reply-To", ""))[0] or None

        subject         = self._decode_header_value(msg.get("Subject", ""))
        in_reply_to     = self._decode_header_value(msg.get("In-Reply-To", "")).strip() or None
        references_raw  = self._decode_header_value(msg.get("References", ""))
        references      = [r.strip() for r in references_raw.split() if r.strip()]

        body_html, body_text_raw = self._extract_body(msg)
        body_text = self._clean_text_body(body_text_raw) if body_text_raw else None

        attachments    = self._extract_attachments(msg)
        is_auto_reply, auto_reason = self._detect_auto_reply(msg, from_addr, subject)

        return ParsedEmail(
            message_id      = message_id,
            from_address    = from_addr,
            from_name       = from_name,
            to_addresses    = to_addrs,
            cc_addresses    = cc_addrs,
            reply_to        = reply_to,
            subject         = subject,
            body_text       = body_text,
            body_text_raw   = body_text_raw,
            body_html       = body_html,
            in_reply_to     = in_reply_to,
            references      = references,
            attachments     = attachments,
            is_auto_reply   = is_auto_reply,
            auto_reply_reason = auto_reason,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Extraction du corps
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_body(self, msg: Message) -> tuple[Optional[str], Optional[str]]:
        """
        Extrait le corps texte brut et HTML d'un email MIME.

        Retourne (body_html, body_text_raw).
        """
        body_text: Optional[str] = None
        body_html: Optional[str] = None

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                disposition = str(part.get("Content-Disposition", ""))
                if "attachment" in disposition:
                    continue
                if ct == "text/plain" and body_text is None:
                    body_text = self._decode_payload(part)
                elif ct == "text/html" and body_html is None:
                    body_html = self._decode_payload(part)
        else:
            ct = msg.get_content_type()
            if ct == "text/plain":
                body_text = self._decode_payload(msg)
            elif ct == "text/html":
                body_html = self._decode_payload(msg)

        # Si pas de texte brut mais du HTML, extraire le texte depuis le HTML
        if not body_text and body_html:
            body_text = self._html_to_text(body_html)

        return body_html, body_text

    def _decode_payload(self, part: Message) -> Optional[str]:
        """Décode le payload d'une partie MIME en chaîne Unicode."""
        try:
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                return None
            return payload.decode(charset, errors="replace")
        except Exception as e:
            logger.warning(f"Erreur décodage payload MIME : {e}")
            return None

    def _html_to_text(self, html: str) -> str:
        """Conversion HTML → texte brut minimaliste (sans dépendance externe)."""
        # Supprimer les balises
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        # Décoder les entités HTML basiques
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        # Normaliser les espaces
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ─────────────────────────────────────────────────────────────────────────
    # Nettoyage du corps (anti-injection, suppression citations/signatures)
    # ─────────────────────────────────────────────────────────────────────────

    # Patterns de citation Outlook / Thunderbird / Gmail
    _QUOTE_PATTERNS = re.compile(
        r"^(-{3,}|_{3,}|>{1,}|"
        r"De\s*:.*?$|From\s*:.*?$|"
        r"Le\s+\w+.*?a écrit.*?:$|"
        r"On\s+\w+.*?wrote.*?:$|"
        r"-----\s*Message original\s*-----.*$)",
        re.IGNORECASE | re.MULTILINE,
    )

    _SIGNATURE_PATTERN = re.compile(
        r"^--\s*$",
        re.MULTILINE,
    )

    # Patterns d'injection de prompt (anti prompt-injection via email)
    _INJECTION_PATTERNS = re.compile(
        r"(ignore\s+(les|all)\s+(instructions?|pr[eé]c[eé]dentes?)|"
        r"oublie\s+tes\s+instructions?|"
        r"forget\s+(all\s+)?previous\s+instructions?|"
        r"system\s*:\s*|"
        r"<\|system\|>|"
        r"\[INST\]|\[/INST\]|"
        r"###\s*(Instruction|System|Human|Assistant)\s*:)",
        re.IGNORECASE,
    )

    def _clean_text_body(self, raw: str) -> str:
        """
        Nettoie le corps texte brut :
          - Supprime les citations de réponse
          - Supprime les signatures (lignes après "-- ")
          - Neutralise les tentatives d'injection de prompt
          - Normalise les espaces
        """
        if not raw:
            return ""

        lines = raw.splitlines()
        clean_lines = []

        for line in lines:
            # Arrêter à la signature
            if re.match(r"^--\s*$", line):
                break
            # Sauter les lignes citées (commençant par >)
            if line.strip().startswith(">"):
                continue
            clean_lines.append(line)

        text = "\n".join(clean_lines)

        # Supprimer les blocs de citation Outlook/Gmail
        text = self._QUOTE_PATTERNS.sub("", text)

        # Neutraliser les injections de prompt (remplacer par [CONTENU_FILTRE])
        def _replace_injection(m: re.Match) -> str:
            logger.warning(f"Injection de prompt détectée et filtrée : {m.group()!r}")
            return "[CONTENU_FILTRE]"

        text = self._INJECTION_PATTERNS.sub(_replace_injection, text)

        # Normaliser les espaces multiples et retours à la ligne
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ─────────────────────────────────────────────────────────────────────────
    # Pièces jointes
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_attachments(self, msg: Message) -> List[ParsedAttachment]:
        """Extrait les pièces jointes d'un message MIME."""
        attachments = []

        if not msg.is_multipart():
            return attachments

        for part in msg.walk():
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" not in disposition:
                continue

            filename_raw = part.get_filename() or ""
            filename = self._decode_header_value(filename_raw).strip()
            if not filename:
                continue

            # Vérifier l'extension (bloquage immédiat pour types dangereux)
            ext = Path(filename).suffix.lower()
            if ext in BLOCKED_ATTACHMENT_EXTENSIONS:
                logger.warning(
                    f"Pièce jointe bloquée (extension interdite) : {filename}"
                )
                continue  # Skip silencieux — loggué dans inbound_service

            content_type = part.get_content_type() or "application/octet-stream"

            try:
                data = part.get_payload(decode=True) or b""
            except Exception as e:
                logger.warning(f"Erreur décodage pièce jointe {filename} : {e}")
                continue

            attachments.append(
                ParsedAttachment(
                    filename=filename,
                    content_type=content_type,
                    data=data,
                    size_bytes=len(data),
                )
            )

        return attachments

    # ─────────────────────────────────────────────────────────────────────────
    # Détection auto-reply / boucles
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_auto_reply(
        self, msg: Message, from_addr: str, subject: str
    ) -> tuple[bool, Optional[str]]:
        """
        Détecte si un email est un auto-reply ou une boucle potentielle.

        Retourne (is_auto_reply, reason).
        """
        # Vérifier les headers spécifiques
        for header in AUTO_REPLY_HEADERS:
            val = msg.get(header, "")
            if val and val.strip().lower() not in ("no", "false", "0"):
                return True, f"Header {header}: {val}"

        # Vérifier l'adresse expéditeur
        from_lower = from_addr.lower()
        for pattern in AUTO_REPLY_FROM_PATTERNS:
            if pattern in from_lower:
                return True, f"Adresse expéditeur auto-reply : {pattern}"

        # Vérifier le sujet
        subject_lower = subject.lower()
        for pattern in AUTO_REPLY_SUBJECT_PATTERNS:
            if pattern in subject_lower:
                return True, f"Sujet auto-reply : {pattern}"

        return False, None

    # ─────────────────────────────────────────────────────────────────────────
    # Utilitaires de décodage des headers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _decode_header_value(raw: str) -> str:
        """Décode un header email (RFC 2047) en chaîne Unicode."""
        if not raw:
            return ""
        parts = decode_header(raw)
        result = []
        for part, encoding in parts:
            if isinstance(part, bytes):
                try:
                    result.append(part.decode(encoding or "utf-8", errors="replace"))
                except LookupError:
                    result.append(part.decode("utf-8", errors="replace"))
            else:
                result.append(str(part))
        return "".join(result).strip()

    def _parse_address(self, raw: str) -> tuple[str, Optional[str]]:
        """
        Extrait (adresse_email, nom_affiché) depuis un champ From/To.

        Retourne ("", None) si le champ est vide.
        """
        raw = self._decode_header_value(raw).strip()
        if not raw:
            return "", None

        # Format : "Nom Prénom <email@exemple.com>"
        match = re.match(r'^"?([^"<]+?)"?\s*<([^>]+)>', raw)
        if match:
            name = match.group(1).strip().strip('"')
            addr = match.group(2).strip().lower()
            return addr, name or None

        # Format simple : email@exemple.com
        match = re.match(r"[\w.+\-]+@[\w.\-]+\.\w+", raw)
        if match:
            return match.group(0).lower(), None

        return raw.lower(), None

    def _parse_address_list(self, raw: str) -> List[str]:
        """Extrait une liste d'adresses email depuis un champ To/CC."""
        if not raw:
            return []
        decoded = self._decode_header_value(raw)
        addresses = []
        for part in decoded.split(","):
            addr, _ = self._parse_address(part.strip())
            if addr:
                addresses.append(addr)
        return addresses

    # ─────────────────────────────────────────────────────────────────────────
    # Normalisation du sujet (pour regroupement en threads)
    # ─────────────────────────────────────────────────────────────────────────

    _SUBJECT_PREFIXES = re.compile(
        r"^(re|fw|fwd|tr|réf|ref|rép)\s*(\[\d+\])?\s*:\s*",
        re.IGNORECASE,
    )

    @classmethod
    def normalize_subject(cls, subject: str) -> str:
        """Supprime les préfixes Re:/Fwd:/Tr: pour normaliser le sujet."""
        subject = subject.strip()
        while True:
            new = cls._SUBJECT_PREFIXES.sub("", subject).strip()
            if new == subject:
                break
            subject = new
        return subject or "(sans sujet)"
