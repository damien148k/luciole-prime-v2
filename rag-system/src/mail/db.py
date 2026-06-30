"""
Initialisation et accès à la base SQLite du module mail — Luciole Prime.

Toutes les tables mail sont dans un fichier mail.db distinct
(cohérent avec feedbacks.db et watcher.db du projet).

Les fonctions get_db_connection() et init_tables() sont thread-safe
pour une utilisation dans le contexte asyncio + run_in_executor.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from loguru import logger

from .config import MAIL_DB_PATH

# Verrou pour les opérations d'initialisation
_init_lock = threading.Lock()
_initialized = False


# ─────────────────────────────────────────────────────────────────────────────
# Connexion
# ─────────────────────────────────────────────────────────────────────────────

def get_db_connection() -> sqlite3.Connection:
    """
    Retourne une connexion SQLite configurée (WAL mode, row_factory).

    Crée le répertoire parent si nécessaire.
    Chaque connexion est indépendante (thread-safe en mode WAL).
    """
    db_path = Path(MAIL_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db_cursor() -> Generator[tuple[sqlite3.Connection, sqlite3.Cursor], None, None]:
    """Context manager : connexion + cursor, commit auto ou rollback."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        yield conn, cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation des tables
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- ── Paramètres mail (singleton) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mail_settings (
    id                          INTEGER PRIMARY KEY DEFAULT 1,
    mail_enabled                BOOLEAN NOT NULL DEFAULT 0,

    imap_host                   TEXT,
    imap_port                   INTEGER DEFAULT 993,
    imap_use_ssl                BOOLEAN DEFAULT 1,
    imap_username               TEXT,
    imap_password_enc           TEXT,
    imap_folder                 TEXT DEFAULT 'INBOX',
    imap_poll_interval_seconds  INTEGER DEFAULT 60,

    smtp_host                   TEXT,
    smtp_port                   INTEGER DEFAULT 465,
    smtp_use_tls                BOOLEAN DEFAULT 1,
    smtp_username               TEXT,
    smtp_password_enc           TEXT,

    from_name                   TEXT DEFAULT 'Luciole',
    from_address                TEXT,
    signature                   TEXT DEFAULT '',

    auto_reply_enabled          BOOLEAN NOT NULL DEFAULT 0,
    confidence_threshold        REAL DEFAULT 0.75,
    risk_threshold              REAL DEFAULT 0.40,
    allowed_sender_domains      TEXT DEFAULT '[]',
    blocked_sender_domains      TEXT DEFAULT '[]',
    max_attachment_size_mb      INTEGER DEFAULT 25,
    attachment_indexing_enabled BOOLEAN DEFAULT 0,
    index_name                  TEXT DEFAULT 'documents',
    sensitive_keywords          TEXT DEFAULT '["licenciement","contentieux","plainte","rgpd","confidentiel","disciplinaire","juridique","donn\u00e9es personnelles","harc\u00e8lement","discrimination"]',

    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by                  TEXT DEFAULT 'system'
);

CREATE UNIQUE INDEX IF NOT EXISTS mail_settings_singleton ON mail_settings(id);

-- Insérer le singleton par défaut s'il n'existe pas
INSERT OR IGNORE INTO mail_settings (id) VALUES (1);

-- ── Threads / conversations ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mail_threads (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_normalized              TEXT NOT NULL DEFAULT '',
    first_message_id                TEXT NOT NULL,

    message_count                   INTEGER DEFAULT 1,
    reply_count                     INTEGER DEFAULT 0,
    last_message_at                 TIMESTAMP,
    last_reply_at                   TIMESTAMP,

    status                          TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','closed','escalated','quarantined')),

    assigned_to                     TEXT,
    thread_summary                  TEXT,

    luciole_reply_count_last_hour   INTEGER DEFAULT 0,
    last_reply_hour                 TEXT,

    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Messages entrants ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inbound_messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          TEXT NOT NULL UNIQUE,
    thread_id           INTEGER REFERENCES mail_threads(id),

    from_address        TEXT NOT NULL,
    from_name           TEXT,
    to_addresses        TEXT NOT NULL DEFAULT '[]',
    cc_addresses        TEXT DEFAULT '[]',
    reply_to            TEXT,
    subject             TEXT DEFAULT '',

    body_text           TEXT,
    body_text_raw       TEXT,
    body_html           TEXT,

    in_reply_to         TEXT,
    references_header   TEXT,

    received_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    imap_uid            TEXT,

    status              TEXT NOT NULL DEFAULT 'received'
        CHECK(status IN ('received','classifying','classified','generating',
                         'draft_pending','auto_queued','processed',
                         'quarantined','error')),

    has_attachments     BOOLEAN DEFAULT 0,
    attachment_count    INTEGER DEFAULT 0,

    is_auto_reply       BOOLEAN DEFAULT 0,
    auto_reply_reason   TEXT
);

CREATE INDEX IF NOT EXISTS idx_inbound_message_id
    ON inbound_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_inbound_status
    ON inbound_messages(status);
CREATE INDEX IF NOT EXISTS idx_inbound_thread
    ON inbound_messages(thread_id);

-- ── Pièces jointes ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attachments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_message_id      INTEGER NOT NULL REFERENCES inbound_messages(id),

    filename_original       TEXT NOT NULL,
    filename_stored         TEXT NOT NULL,
    content_type_declared   TEXT,
    content_type_detected   TEXT,
    size_bytes              INTEGER NOT NULL DEFAULT 0,
    sha256_hash             TEXT NOT NULL DEFAULT '',

    is_allowed_type         BOOLEAN,
    is_size_ok              BOOLEAN,
    is_safe                 BOOLEAN,
    scan_detail             TEXT,

    indexed_in_rag              BOOLEAN DEFAULT 0,
    indexing_requested_by       TEXT,
    indexing_requested_at       TIMESTAMP,
    indexing_done_at            TIMESTAMP,

    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ── Résultats de classification ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classification_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_message_id      INTEGER NOT NULL REFERENCES inbound_messages(id),

    category                TEXT NOT NULL,
    confidence_score        REAL NOT NULL DEFAULT 0.0,
    risk_score              REAL NOT NULL DEFAULT 0.0,

    decision                TEXT NOT NULL,
    decision_reason         TEXT NOT NULL DEFAULT '',

    classified_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_classif_inbound
    ON classification_results(inbound_message_id);

-- ── Brouillons pour validation humaine ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS draft_approvals (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_message_id      INTEGER NOT NULL REFERENCES inbound_messages(id),
    thread_id               INTEGER REFERENCES mail_threads(id),

    generated_response      TEXT NOT NULL,
    sources_used            TEXT DEFAULT '[]',
    passages_used           TEXT DEFAULT '[]',
    rag_query               TEXT,

    confidence_score        REAL NOT NULL DEFAULT 0.0,
    risk_score              REAL NOT NULL DEFAULT 0.0,
    classification          TEXT,
    decision_reason         TEXT NOT NULL DEFAULT '',

    status                  TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','modified_approved','rejected','expired')),
    reviewer                TEXT,
    reviewer_comment        TEXT,
    final_response          TEXT,

    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP,
    expires_at  TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_draft_status
    ON draft_approvals(status);
CREATE INDEX IF NOT EXISTS idx_draft_inbound
    ON draft_approvals(inbound_message_id);

-- ── Messages sortants ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outbound_messages (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    inbound_message_id      INTEGER NOT NULL REFERENCES inbound_messages(id),
    thread_id               INTEGER REFERENCES mail_threads(id),
    draft_approval_id       INTEGER REFERENCES draft_approvals(id),

    to_address              TEXT NOT NULL,
    cc_addresses            TEXT DEFAULT '[]',
    subject                 TEXT NOT NULL,
    body_text               TEXT NOT NULL,
    body_html               TEXT,

    message_id_header       TEXT,
    in_reply_to             TEXT,
    references_header       TEXT,

    sources_used            TEXT DEFAULT '[]',
    confidence_score        REAL,
    rag_query               TEXT,

    status                  TEXT NOT NULL DEFAULT 'ready'
        CHECK(status IN ('ready','sending','sent','failed','cancelled')),
    retry_count             INTEGER DEFAULT 0,
    last_error              TEXT,
    next_retry_at           TIMESTAMP,

    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at     TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_outbound_status
    ON outbound_messages(status);
CREATE INDEX IF NOT EXISTS idx_outbound_inbound
    ON outbound_messages(inbound_message_id);

-- ── Tests IMAP/SMTP ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS mail_test_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    test_type           TEXT NOT NULL CHECK(test_type IN ('connection','send')),

    imap_status         TEXT CHECK(imap_status IN ('ok','error','timeout','skipped',NULL)),
    imap_detail         TEXT,
    imap_latency_ms     INTEGER,
    imap_error_code     TEXT,

    smtp_status         TEXT CHECK(smtp_status IN ('ok','error','timeout','skipped',NULL)),
    smtp_detail         TEXT,
    smtp_latency_ms     INTEGER,
    smtp_error_code     TEXT,

    test_recipient      TEXT,
    send_status         TEXT CHECK(send_status IN ('sent','failed',NULL)),

    triggered_by        TEXT NOT NULL DEFAULT 'admin',
    total_duration_ms   INTEGER,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_test_runs_type
    ON mail_test_runs(test_type, created_at);

-- ── Dead-letter queue ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS errors_dead_letters (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    error_type              TEXT NOT NULL,
    inbound_message_id      INTEGER,
    raw_payload             TEXT,
    error_message           TEXT NOT NULL,
    stack_trace             TEXT,

    retry_count             INTEGER DEFAULT 0,
    max_retries             INTEGER DEFAULT 3,
    next_retry_at           TIMESTAMP,

    status                  TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','retrying','exhausted','resolved','ignored')),

    resolved_by             TEXT,
    resolved_at             TIMESTAMP,
    resolution_note         TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dead_status
    ON errors_dead_letters(status);

-- ── Audit logs ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    action                  TEXT NOT NULL,
    inbound_message_id      INTEGER,
    outbound_message_id     INTEGER,
    thread_id               INTEGER,
    draft_approval_id       INTEGER,

    actor                   TEXT NOT NULL DEFAULT 'system',
    outcome                 TEXT CHECK(outcome IN ('success','failure','skipped','blocked',NULL)),
    detail                  TEXT,
    duration_ms             INTEGER
);

CREATE INDEX IF NOT EXISTS idx_audit_created
    ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_inbound
    ON audit_logs(inbound_message_id);
"""


def init_tables() -> None:
    """
    Crée toutes les tables si elles n'existent pas.

    Appelé au démarrage du service. Idempotent.
    """
    global _initialized
    with _init_lock:
        if _initialized:
            return
        try:
            conn = get_db_connection()
            conn.executescript(_SCHEMA_SQL)
            # Migration : ajouter passages_used si la colonne n'existe pas
            # (pour les bases créées avant cette colonne)
            cur = conn.execute("PRAGMA table_info(draft_approvals)")
            cols = {row[1] for row in cur.fetchall()}
            if "passages_used" not in cols:
                conn.execute(
                    "ALTER TABLE draft_approvals "
                    "ADD COLUMN passages_used TEXT DEFAULT '[]'"
                )
                logger.info("Migration DB mail : colonne passages_used ajoutée")
            conn.commit()
            conn.close()
            _initialized = True
            logger.info(f"Base de données mail initialisée : {MAIL_DB_PATH}")
        except Exception as e:
            logger.error(f"Erreur initialisation DB mail : {e}")
            raise


def now_utc() -> str:
    """Retourne l'horodatage UTC actuel au format ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """Convertit un sqlite3.Row en dict, ou retourne None."""
    return dict(row) if row is not None else None
