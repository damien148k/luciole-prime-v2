"""
Couche d'accès aux données — Module mail Luciole Prime.

CRUD complet sur toutes les tables mail.db.
Pas d'ORM, sqlite3 brut (cohérent avec le reste du projet).
"""
from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from loguru import logger

from .constants import DRAFT_EXPIRY_DAYS, DraftStatus, InboundStatus, OutboundStatus
from .db import db_cursor, now_utc, row_to_dict
from .models import (
    AuditLog,
    Attachment,
    ClassificationResult,
    DeadLetter,
    DraftApproval,
    InboundMessage,
    MailSettings,
    MailTestRun,
    MailThread,
    OutboundMessage,
)


# ─────────────────────────────────────────────────────────────────────────────
# MailSettings
# ─────────────────────────────────────────────────────────────────────────────

class MailSettingsRepo:
    """CRUD sur la table mail_settings (singleton id=1)."""

    @staticmethod
    def get() -> MailSettings:
        """Retourne les paramètres mail (crée le singleton s'il manque)."""
        with db_cursor() as (conn, cur):
            cur.execute("SELECT * FROM mail_settings WHERE id = 1")
            row = row_to_dict(cur.fetchone())
            if not row:
                cur.execute("INSERT OR IGNORE INTO mail_settings (id) VALUES (1)")
                conn.commit()
                cur.execute("SELECT * FROM mail_settings WHERE id = 1")
                row = row_to_dict(cur.fetchone())
        return _row_to_mail_settings(row)

    @staticmethod
    def update(settings: MailSettings, actor: str = "admin") -> None:
        """Met à jour les paramètres mail."""
        with db_cursor() as (conn, cur):
            cur.execute("""
                UPDATE mail_settings SET
                    mail_enabled = ?,
                    imap_host = ?, imap_port = ?, imap_use_ssl = ?,
                    imap_username = ?, imap_password_enc = ?,
                    imap_folder = ?, imap_poll_interval_seconds = ?,
                    smtp_host = ?, smtp_port = ?, smtp_use_tls = ?,
                    smtp_username = ?, smtp_password_enc = ?,
                    from_name = ?, from_address = ?, signature = ?,
                    auto_reply_enabled = ?,
                    confidence_threshold = ?, risk_threshold = ?,
                    allowed_sender_domains = ?, blocked_sender_domains = ?,
                    max_attachment_size_mb = ?, attachment_indexing_enabled = ?,
                    index_name = ?, sensitive_keywords = ?,
                    updated_at = ?, updated_by = ?
                WHERE id = 1
            """, (
                int(settings.mail_enabled),
                settings.imap_host, settings.imap_port, int(settings.imap_use_ssl),
                settings.imap_username, settings.imap_password_enc,
                settings.imap_folder, settings.imap_poll_interval_seconds,
                settings.smtp_host, settings.smtp_port, int(settings.smtp_use_tls),
                settings.smtp_username, settings.smtp_password_enc,
                settings.from_name, settings.from_address, settings.signature,
                int(settings.auto_reply_enabled),
                settings.confidence_threshold, settings.risk_threshold,
                settings.allowed_sender_domains, settings.blocked_sender_domains,
                settings.max_attachment_size_mb, int(settings.attachment_indexing_enabled),
                settings.index_name, settings.sensitive_keywords,
                now_utc(), actor,
            ))


# ─────────────────────────────────────────────────────────────────────────────
# MailThread
# ─────────────────────────────────────────────────────────────────────────────

class ThreadRepo:
    """CRUD sur la table mail_threads."""

    @staticmethod
    def get(thread_id: int) -> Optional[MailThread]:
        with db_cursor() as (_, cur):
            cur.execute("SELECT * FROM mail_threads WHERE id = ?", (thread_id,))
            row = row_to_dict(cur.fetchone())
        return _row_to_thread(row) if row else None

    @staticmethod
    def find_by_message_id(message_id: str) -> Optional[MailThread]:
        """Trouve le thread dont first_message_id = message_id."""
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM mail_threads WHERE first_message_id = ?", (message_id,)
            )
            row = row_to_dict(cur.fetchone())
        return _row_to_thread(row) if row else None

    @staticmethod
    def create(thread: MailThread) -> int:
        """Crée un nouveau thread et retourne son id."""
        with db_cursor() as (conn, cur):
            cur.execute("""
                INSERT INTO mail_threads
                    (subject_normalized, first_message_id, message_count,
                     reply_count, last_message_at, status, created_at, updated_at)
                VALUES (?, ?, 1, 0, ?, 'active', ?, ?)
            """, (
                thread.subject_normalized,
                thread.first_message_id,
                now_utc(), now_utc(), now_utc(),
            ))
            return cur.lastrowid

    @staticmethod
    def increment_message_count(thread_id: int) -> None:
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE mail_threads
                SET message_count = message_count + 1,
                    last_message_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (now_utc(), now_utc(), thread_id))

    @staticmethod
    def increment_reply_count(thread_id: int) -> None:
        """Incrémente le compteur de réponses de Luciole pour ce thread."""
        current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        with db_cursor() as (conn, cur):
            cur.execute(
                "SELECT last_reply_hour, luciole_reply_count_last_hour FROM mail_threads WHERE id = ?",
                (thread_id,),
            )
            row = cur.fetchone()
            if row:
                last_hour = row[0] or ""
                count = row[1] or 0
                new_count = (count + 1) if last_hour == current_hour else 1
                cur.execute("""
                    UPDATE mail_threads
                    SET reply_count = reply_count + 1,
                        last_reply_at = ?,
                        luciole_reply_count_last_hour = ?,
                        last_reply_hour = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (now_utc(), new_count, current_hour, now_utc(), thread_id))

    @staticmethod
    def get_reply_count_last_hour(thread_id: int) -> int:
        """Retourne le nombre de réponses Luciole dans l'heure en cours."""
        current_hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT last_reply_hour, luciole_reply_count_last_hour FROM mail_threads WHERE id = ?",
                (thread_id,),
            )
            row = cur.fetchone()
        if not row:
            return 0
        if row[0] != current_hour:
            return 0
        return row[1] or 0

    @staticmethod
    def list_recent(limit: int = 20) -> List[MailThread]:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM mail_threads ORDER BY last_message_at DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_thread(dict(r)) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# InboundMessage
# ─────────────────────────────────────────────────────────────────────────────

class InboundRepo:
    """CRUD sur la table inbound_messages."""

    @staticmethod
    def exists(message_id: str) -> bool:
        """Vérifie l'existence d'un message par son Message-ID (déduplication)."""
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT 1 FROM inbound_messages WHERE message_id = ?", (message_id,)
            )
            return cur.fetchone() is not None

    @staticmethod
    def create(msg: InboundMessage) -> int:
        with db_cursor() as (conn, cur):
            cur.execute("""
                INSERT INTO inbound_messages
                    (message_id, thread_id, from_address, from_name,
                     to_addresses, cc_addresses, reply_to, subject,
                     body_text, body_text_raw, body_html,
                     in_reply_to, references_header,
                     received_at, imap_uid, status,
                     has_attachments, attachment_count,
                     is_auto_reply, auto_reply_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                msg.message_id, msg.thread_id, msg.from_address, msg.from_name,
                msg.to_addresses, msg.cc_addresses, msg.reply_to, msg.subject,
                msg.body_text, msg.body_text_raw, msg.body_html,
                msg.in_reply_to, msg.references_header,
                now_utc(), msg.imap_uid, msg.status.value,
                int(msg.has_attachments), msg.attachment_count,
                int(msg.is_auto_reply), msg.auto_reply_reason,
            ))
            return cur.lastrowid

    @staticmethod
    def update_status(msg_id: int, status: InboundStatus) -> None:
        with db_cursor() as (_, cur):
            cur.execute(
                "UPDATE inbound_messages SET status = ? WHERE id = ?",
                (status.value, msg_id),
            )

    @staticmethod
    def get(msg_id: int) -> Optional[InboundMessage]:
        with db_cursor() as (_, cur):
            cur.execute("SELECT * FROM inbound_messages WHERE id = ?", (msg_id,))
            row = row_to_dict(cur.fetchone())
        return _row_to_inbound(row) if row else None

    @staticmethod
    def list_by_status(status: InboundStatus, limit: int = 50) -> List[InboundMessage]:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM inbound_messages WHERE status = ? ORDER BY received_at DESC LIMIT ?",
                (status.value, limit),
            )
            return [_row_to_inbound(dict(r)) for r in cur.fetchall()]

    @staticmethod
    def list_recent(limit: int = 50, offset: int = 0, status: Optional[str] = None) -> tuple[List[InboundMessage], int]:
        with db_cursor() as (_, cur):
            if status:
                cur.execute(
                    "SELECT COUNT(*) FROM inbound_messages WHERE status = ?", (status,)
                )
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT * FROM inbound_messages WHERE status = ? ORDER BY received_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                )
            else:
                cur.execute("SELECT COUNT(*) FROM inbound_messages")
                total = cur.fetchone()[0]
                cur.execute(
                    "SELECT * FROM inbound_messages ORDER BY received_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = [_row_to_inbound(dict(r)) for r in cur.fetchall()]
        return rows, total

    @staticmethod
    def update_thread(msg_id: int, thread_id: int) -> None:
        with db_cursor() as (_, cur):
            cur.execute(
                "UPDATE inbound_messages SET thread_id = ? WHERE id = ?",
                (thread_id, msg_id),
            )

    @staticmethod
    def update_attachment_count(msg_id: int, count: int) -> None:
        with db_cursor() as (_, cur):
            cur.execute(
                "UPDATE inbound_messages SET attachment_count = ?, has_attachments = ? WHERE id = ?",
                (count, int(count > 0), msg_id),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Attachments
# ─────────────────────────────────────────────────────────────────────────────

class AttachmentRepo:
    """CRUD sur la table attachments."""

    @staticmethod
    def create(att: Attachment) -> int:
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO attachments
                    (inbound_message_id, filename_original, filename_stored,
                     content_type_declared, content_type_detected,
                     size_bytes, sha256_hash,
                     is_allowed_type, is_size_ok, is_safe, scan_detail,
                     indexed_in_rag, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)
            """, (
                att.inbound_message_id, att.filename_original, att.filename_stored,
                att.content_type_declared, att.content_type_detected,
                att.size_bytes, att.sha256_hash,
                att.is_allowed_type, att.is_size_ok, att.is_safe, att.scan_detail,
                now_utc(),
            ))
            return cur.lastrowid

    @staticmethod
    def list_for_message(msg_id: int) -> List[dict]:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM attachments WHERE inbound_message_id = ?", (msg_id,)
            )
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# ClassificationResult
# ─────────────────────────────────────────────────────────────────────────────

class ClassificationRepo:
    """CRUD sur la table classification_results."""

    @staticmethod
    def create(result: ClassificationResult) -> int:
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO classification_results
                    (inbound_message_id, category, confidence_score, risk_score,
                     decision, decision_reason, classified_at)
                VALUES (?,?,?,?,?,?,?)
            """, (
                result.inbound_message_id,
                result.category.value,
                result.confidence_score,
                result.risk_score,
                result.decision.value,
                result.decision_reason,
                now_utc(),
            ))
            return cur.lastrowid

    @staticmethod
    def get_for_message(msg_id: int) -> Optional[dict]:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM classification_results WHERE inbound_message_id = ? ORDER BY id DESC LIMIT 1",
                (msg_id,),
            )
            return row_to_dict(cur.fetchone())


# ─────────────────────────────────────────────────────────────────────────────
# DraftApproval
# ─────────────────────────────────────────────────────────────────────────────

class DraftRepo:
    """CRUD sur la table draft_approvals."""

    @staticmethod
    def create(draft: DraftApproval) -> int:
        expires = (
            datetime.now(timezone.utc) + timedelta(days=DRAFT_EXPIRY_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO draft_approvals
                    (inbound_message_id, thread_id,
                     generated_response, sources_used, passages_used, rag_query,
                     confidence_score, risk_score,
                     classification, decision_reason,
                     status, created_at, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)
            """, (
                draft.inbound_message_id, draft.thread_id,
                draft.generated_response, draft.sources_used, draft.passages_used, draft.rag_query,
                draft.confidence_score, draft.risk_score,
                draft.classification, draft.decision_reason,
                now_utc(), expires,
            ))
            return cur.lastrowid

    @staticmethod
    def get(draft_id: int) -> Optional[DraftApproval]:
        with db_cursor() as (_, cur):
            cur.execute("SELECT * FROM draft_approvals WHERE id = ?", (draft_id,))
            row = row_to_dict(cur.fetchone())
        return _row_to_draft(row) if row else None

    @staticmethod
    def list_pending(limit: int = 50) -> List[DraftApproval]:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT * FROM draft_approvals WHERE status = 'pending' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [_row_to_draft(dict(r)) for r in cur.fetchall()]

    @staticmethod
    def count_pending() -> int:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT COUNT(*) FROM draft_approvals WHERE status = 'pending'"
            )
            return cur.fetchone()[0]

    @staticmethod
    def approve(draft_id: int, reviewer: str, final_response: Optional[str] = None) -> None:
        """Approuve un brouillon (avec ou sans modification)."""
        status = DraftStatus.MODIFIED_APPROVED.value if final_response else DraftStatus.APPROVED.value
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE draft_approvals
                SET status = ?, reviewer = ?, final_response = ?, reviewed_at = ?
                WHERE id = ? AND status = 'pending'
            """, (status, reviewer, final_response, now_utc(), draft_id))

    @staticmethod
    def reject(draft_id: int, reviewer: str, comment: Optional[str] = None) -> None:
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE draft_approvals
                SET status = 'rejected', reviewer = ?, reviewer_comment = ?, reviewed_at = ?
                WHERE id = ? AND status = 'pending'
            """, (reviewer, comment, now_utc(), draft_id))

    @staticmethod
    def expire_old() -> int:
        """Marque comme expirés les brouillons dépassant la date d'expiration."""
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE draft_approvals
                SET status = 'expired'
                WHERE status = 'pending' AND expires_at < ?
            """, (now_utc(),))
            return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# OutboundMessage
# ─────────────────────────────────────────────────────────────────────────────

class OutboundRepo:
    """CRUD sur la table outbound_messages."""

    @staticmethod
    def create(msg: OutboundMessage) -> int:
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO outbound_messages
                    (inbound_message_id, thread_id, draft_approval_id,
                     to_address, cc_addresses, subject, body_text, body_html,
                     message_id_header, in_reply_to, references_header,
                     sources_used, confidence_score, rag_query,
                     status, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ready',?)
            """, (
                msg.inbound_message_id, msg.thread_id, msg.draft_approval_id,
                msg.to_address, msg.cc_addresses, msg.subject, msg.body_text, msg.body_html,
                msg.message_id_header, msg.in_reply_to, msg.references_header,
                msg.sources_used, msg.confidence_score, msg.rag_query,
                now_utc(),
            ))
            return cur.lastrowid

    @staticmethod
    def lock_for_sending(outbound_id: int) -> bool:
        """
        Verrou optimiste : passe status 'ready' → 'sending'.
        Retourne True si le verrou a été acquis.
        """
        with db_cursor() as (conn, cur):
            cur.execute("""
                UPDATE outbound_messages
                SET status = 'sending'
                WHERE id = ? AND status = 'ready'
            """, (outbound_id,))
            return cur.rowcount > 0

    @staticmethod
    def mark_sent(outbound_id: int) -> None:
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE outbound_messages
                SET status = 'sent', sent_at = ?
                WHERE id = ?
            """, (now_utc(), outbound_id))

    @staticmethod
    def mark_failed(outbound_id: int, error: str, next_retry_at: Optional[str]) -> None:
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE outbound_messages
                SET status = CASE WHEN retry_count >= 3 THEN 'failed' ELSE 'ready' END,
                    retry_count = retry_count + 1,
                    last_error = ?,
                    next_retry_at = ?
                WHERE id = ?
            """, (error, next_retry_at, outbound_id))

    @staticmethod
    def list_ready() -> List[OutboundMessage]:
        """Retourne les messages prêts à envoyer (retry_at dépassé ou NULL)."""
        with db_cursor() as (_, cur):
            cur.execute("""
                SELECT * FROM outbound_messages
                WHERE status = 'ready'
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at
            """, (now_utc(),))
            return [_row_to_outbound(dict(r)) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# MailTestRun
# ─────────────────────────────────────────────────────────────────────────────

class TestRunRepo:
    """CRUD sur la table mail_test_runs."""

    @staticmethod
    def create(run: MailTestRun) -> int:
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO mail_test_runs
                    (test_type,
                     imap_status, imap_detail, imap_latency_ms, imap_error_code,
                     smtp_status, smtp_detail, smtp_latency_ms, smtp_error_code,
                     test_recipient, send_status,
                     triggered_by, total_duration_ms, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run.test_type,
                run.imap_status, run.imap_detail, run.imap_latency_ms, run.imap_error_code,
                run.smtp_status, run.smtp_detail, run.smtp_latency_ms, run.smtp_error_code,
                run.test_recipient, run.send_status,
                run.triggered_by, run.total_duration_ms, now_utc(),
            ))
            return cur.lastrowid

    @staticmethod
    def list_recent(test_type: Optional[str] = None, limit: int = 10) -> List[dict]:
        with db_cursor() as (_, cur):
            if test_type:
                cur.execute(
                    "SELECT * FROM mail_test_runs WHERE test_type = ? ORDER BY created_at DESC LIMIT ?",
                    (test_type, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM mail_test_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# AuditLog
# ─────────────────────────────────────────────────────────────────────────────

class AuditRepo:
    """CRUD sur la table audit_logs."""

    @staticmethod
    def log(
        action: str,
        actor: str = "system",
        outcome: Optional[str] = None,
        detail: Optional[dict] = None,
        inbound_id: Optional[int] = None,
        outbound_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        draft_id: Optional[int] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Insère une entrée d'audit (fire-and-forget, ne lève pas d'exception)."""
        try:
            detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
            with db_cursor() as (_, cur):
                cur.execute("""
                    INSERT INTO audit_logs
                        (action, inbound_message_id, outbound_message_id,
                         thread_id, draft_approval_id,
                         actor, outcome, detail, duration_ms, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    action, inbound_id, outbound_id, thread_id, draft_id,
                    actor, outcome, detail_json, duration_ms, now_utc(),
                ))
        except Exception as e:
            logger.warning(f"Erreur écriture audit log : {e}")

    @staticmethod
    def list_recent(
        limit: int = 100,
        offset: int = 0,
        action: Optional[str] = None,
        inbound_id: Optional[int] = None,
    ) -> tuple[List[dict], int]:
        with db_cursor() as (_, cur):
            wheres, params = [], []
            if action:
                wheres.append("action = ?")
                params.append(action)
            if inbound_id:
                wheres.append("inbound_message_id = ?")
                params.append(inbound_id)
            where = ("WHERE " + " AND ".join(wheres)) if wheres else ""

            cur.execute(f"SELECT COUNT(*) FROM audit_logs {where}", params)
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT * FROM audit_logs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            )
            return [dict(r) for r in cur.fetchall()], total


# ─────────────────────────────────────────────────────────────────────────────
# DeadLetter
# ─────────────────────────────────────────────────────────────────────────────

class DeadLetterRepo:
    """CRUD sur la table errors_dead_letters."""

    @staticmethod
    def create(
        error_type: str,
        error_message: str,
        inbound_id: Optional[int] = None,
        raw_payload: Optional[str] = None,
        stack: Optional[str] = None,
    ) -> int:
        with db_cursor() as (_, cur):
            cur.execute("""
                INSERT INTO errors_dead_letters
                    (error_type, inbound_message_id, raw_payload,
                     error_message, stack_trace, created_at)
                VALUES (?,?,?,?,?,?)
            """, (error_type, inbound_id, raw_payload, error_message, stack, now_utc()))
            return cur.lastrowid

    @staticmethod
    def list_active(limit: int = 50) -> List[dict]:
        with db_cursor() as (_, cur):
            cur.execute(
                """SELECT * FROM errors_dead_letters
                   WHERE status IN ('pending','retrying','exhausted')
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def mark_resolved(dl_id: int, by: str, note: str = "") -> None:
        with db_cursor() as (_, cur):
            cur.execute("""
                UPDATE errors_dead_letters
                SET status = 'resolved', resolved_by = ?, resolved_at = ?, resolution_note = ?
                WHERE id = ?
            """, (by, now_utc(), note, dl_id))

    @staticmethod
    def mark_ignored(dl_id: int, by: str) -> None:
        with db_cursor() as (_, cur):
            cur.execute(
                "UPDATE errors_dead_letters SET status = 'ignored', resolved_by = ?, resolved_at = ? WHERE id = ?",
                (by, now_utc(), dl_id),
            )

    @staticmethod
    def count_active() -> int:
        with db_cursor() as (_, cur):
            cur.execute(
                "SELECT COUNT(*) FROM errors_dead_letters WHERE status IN ('pending','retrying','exhausted')"
            )
            return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# Statistiques 24h
# ─────────────────────────────────────────────────────────────────────────────

def get_stats_24h() -> dict:
    """Retourne les compteurs d'activité des dernières 24h."""
    with db_cursor() as (_, cur):
        def count_since(table: str, date_col: str, extra_where: str = "") -> int:
            q = f"SELECT COUNT(*) FROM {table} WHERE {date_col} > datetime('now','-1 day')"
            if extra_where:
                q += f" AND {extra_where}"
            cur.execute(q)
            return cur.fetchone()[0]

        received    = count_since("inbound_messages", "received_at")
        classified  = count_since("inbound_messages", "received_at", "status != 'received'")
        quarantined = count_since("inbound_messages", "received_at", "status = 'quarantined'")
        drafts      = count_since("draft_approvals", "created_at")

        cur.execute(
            "SELECT COUNT(*) FROM outbound_messages WHERE sent_at > datetime('now','-1 day')"
        )
        sent = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM errors_dead_letters WHERE created_at > datetime('now','-1 day')"
        )
        errors = cur.fetchone()[0]

    return {
        "received": received,
        "classified": classified,
        "drafts_created": drafts,
        "sent": sent,
        "quarantined": quarantined,
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Convertisseurs row → dataclass
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_mail_settings(row: dict) -> MailSettings:
    return MailSettings(
        id=row.get("id", 1),
        mail_enabled=bool(row.get("mail_enabled", False)),
        imap_host=row.get("imap_host"),
        imap_port=row.get("imap_port", 993),
        imap_use_ssl=bool(row.get("imap_use_ssl", True)),
        imap_username=row.get("imap_username"),
        imap_password_enc=row.get("imap_password_enc"),
        imap_folder=row.get("imap_folder", "INBOX"),
        imap_poll_interval_seconds=row.get("imap_poll_interval_seconds", 60),
        smtp_host=row.get("smtp_host"),
        smtp_port=row.get("smtp_port", 465),
        smtp_use_tls=bool(row.get("smtp_use_tls", True)),
        smtp_username=row.get("smtp_username"),
        smtp_password_enc=row.get("smtp_password_enc"),
        from_name=row.get("from_name", "Luciole"),
        from_address=row.get("from_address"),
        signature=row.get("signature", ""),
        auto_reply_enabled=bool(row.get("auto_reply_enabled", False)),
        confidence_threshold=row.get("confidence_threshold", 0.75),
        risk_threshold=row.get("risk_threshold", 0.40),
        allowed_sender_domains=row.get("allowed_sender_domains", "[]"),
        blocked_sender_domains=row.get("blocked_sender_domains", "[]"),
        max_attachment_size_mb=row.get("max_attachment_size_mb", 25),
        attachment_indexing_enabled=bool(row.get("attachment_indexing_enabled", False)),
        index_name=row.get("index_name") or os.environ.get("MAIL_DEFAULT_INDEX", "documents"),
        sensitive_keywords=row.get("sensitive_keywords", "[]"),
        updated_at=_parse_dt(row.get("updated_at")),
        updated_by=row.get("updated_by", "system"),
    )


def _row_to_thread(row: dict) -> MailThread:
    from .constants import ThreadStatus
    return MailThread(
        id=row.get("id"),
        subject_normalized=row.get("subject_normalized", ""),
        first_message_id=row.get("first_message_id", ""),
        message_count=row.get("message_count", 1),
        reply_count=row.get("reply_count", 0),
        last_message_at=_parse_dt(row.get("last_message_at")),
        last_reply_at=_parse_dt(row.get("last_reply_at")),
        status=ThreadStatus(row.get("status", "active")),
        assigned_to=row.get("assigned_to"),
        thread_summary=row.get("thread_summary"),
        luciole_reply_count_last_hour=row.get("luciole_reply_count_last_hour", 0),
        last_reply_hour=row.get("last_reply_hour"),
        created_at=_parse_dt(row.get("created_at")),
        updated_at=_parse_dt(row.get("updated_at")),
    )


def _row_to_inbound(row: dict) -> InboundMessage:
    return InboundMessage(
        id=row.get("id"),
        message_id=row.get("message_id", ""),
        thread_id=row.get("thread_id"),
        from_address=row.get("from_address", ""),
        from_name=row.get("from_name"),
        to_addresses=row.get("to_addresses", "[]"),
        cc_addresses=row.get("cc_addresses", "[]"),
        reply_to=row.get("reply_to"),
        subject=row.get("subject", ""),
        body_text=row.get("body_text"),
        body_text_raw=row.get("body_text_raw"),
        body_html=None,  # Ne pas retourner body_html par défaut (sécurité XSS)
        in_reply_to=row.get("in_reply_to"),
        references_header=row.get("references_header"),
        received_at=_parse_dt(row.get("received_at")),
        imap_uid=row.get("imap_uid"),
        status=InboundStatus(row.get("status", "received")),
        has_attachments=bool(row.get("has_attachments", False)),
        attachment_count=row.get("attachment_count", 0),
        is_auto_reply=bool(row.get("is_auto_reply", False)),
        auto_reply_reason=row.get("auto_reply_reason"),
    )


def _row_to_draft(row: dict) -> DraftApproval:
    return DraftApproval(
        id=row.get("id"),
        inbound_message_id=row.get("inbound_message_id"),
        thread_id=row.get("thread_id"),
        generated_response=row.get("generated_response", ""),
        sources_used=row.get("sources_used", "[]"),
        passages_used=row.get("passages_used", "[]"),
        rag_query=row.get("rag_query"),
        confidence_score=row.get("confidence_score", 0.0),
        risk_score=row.get("risk_score", 0.0),
        classification=row.get("classification"),
        decision_reason=row.get("decision_reason", ""),
        status=DraftStatus(row.get("status", "pending")),
        reviewer=row.get("reviewer"),
        reviewer_comment=row.get("reviewer_comment"),
        final_response=row.get("final_response"),
        created_at=_parse_dt(row.get("created_at")),
        reviewed_at=_parse_dt(row.get("reviewed_at")),
        expires_at=_parse_dt(row.get("expires_at")),
    )


def _row_to_outbound(row: dict) -> OutboundMessage:
    return OutboundMessage(
        id=row.get("id"),
        inbound_message_id=row.get("inbound_message_id"),
        thread_id=row.get("thread_id"),
        draft_approval_id=row.get("draft_approval_id"),
        to_address=row.get("to_address", ""),
        cc_addresses=row.get("cc_addresses", "[]"),
        subject=row.get("subject", ""),
        body_text=row.get("body_text", ""),
        body_html=row.get("body_html"),
        message_id_header=row.get("message_id_header"),
        in_reply_to=row.get("in_reply_to"),
        references_header=row.get("references_header"),
        sources_used=row.get("sources_used", "[]"),
        confidence_score=row.get("confidence_score"),
        rag_query=row.get("rag_query"),
        status=OutboundStatus(row.get("status", "ready")),
        retry_count=row.get("retry_count", 0),
        last_error=row.get("last_error"),
        next_retry_at=_parse_dt(row.get("next_retry_at")),
        created_at=_parse_dt(row.get("created_at")),
        sent_at=_parse_dt(row.get("sent_at")),
    )


def _parse_dt(val) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except ValueError:
        return None
