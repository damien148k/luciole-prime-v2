"""
JobQueue — File d'attente persistante des jobs d'indexation.

Implémentée sur SQLite (table `jobs`). Les opérations critiques utilisent
des transactions BEGIN IMMEDIATE pour garantir qu'un seul worker traite
un job à la fois, même en cas d'accès concurrent.

Stratégie de retry :
- Backoff exponentiel : délai = base * (multiplier ** attempt)
- Après max_attempts : le job passe en statut 'dead' (dead-letter)
- Les jobs dead sont visibles via l'API et retentables manuellement
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from .constants import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_DEAD,
    JOB_STATUS_FAILED,
    JOB_STATUS_IN_PROGRESS,
    JOB_STATUS_PENDING,
    RETRY_BACKOFF_BASE,
    RETRY_BACKOFF_MULTIPLIER,
    RETRY_MAX_ATTEMPTS,
    RETRY_MAX_BACKOFF,
)
from .db import get_connection
from .models import Job, JobStatus


class JobQueue:
    """
    File d'attente persistante basée sur SQLite.

    Chaque instance maintient sa propre connexion — une instance par thread.
    """

    def __init__(
        self,
        db_path: str,
        retry_max_attempts: int = RETRY_MAX_ATTEMPTS,
        retry_backoff_base: float = RETRY_BACKOFF_BASE,
    ) -> None:
        """
        Args:
            db_path: Chemin vers watcher.db.
            retry_max_attempts: Nombre de tentatives avant dead-letter.
            retry_backoff_base: Délai de base du backoff exponentiel (secondes).
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection = get_connection(db_path)
        self._retry_max_attempts = retry_max_attempts
        self._retry_backoff_base = retry_backoff_base
        logger.debug(f"JobQueue connectée : {db_path}")

    # ─────────────────────────────────────────────────────────────────────
    # Enqueue
    # ─────────────────────────────────────────────────────────────────────

    def enqueue(
        self,
        file_path: str,
        action: str,
        old_path: Optional[str] = None,
        priority: int = 0,
        source: str = "watcher",
    ) -> str:
        """
        Ajoute un job dans la file d'attente.

        Si un job `pending` existe déjà pour ce path + action, il est remplacé
        (le nouveau écrase l'ancien pour éviter les doublons après debounce).

        Args:
            file_path: Chemin du fichier concerné.
            action: Action à effectuer ('upsert', 'delete', 'move').
            old_path: Ancien chemin (pour les moves).
            priority: Priorité (0=normal, 1=haute, -1=basse).
            source: Origine du job ('watcher', 'rescan', 'manual', 'startup').

        Returns:
            job_id (UUID) du job créé.
        """
        job_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        # Annuler les jobs pending existants pour ce même path + action
        # (le nouveau remplace — debounce au niveau de la queue)
        cancelled = self.cancel_pending_for_path(file_path, action)
        if cancelled:
            logger.debug(f"Debounce queue : {cancelled} job(s) annulé(s) pour {file_path}")

        self._conn.execute(
            """
            INSERT INTO jobs (
                job_id, file_path, action, old_path,
                status, priority, source,
                attempts, max_attempts,
                created_at
            ) VALUES (
                :job_id, :file_path, :action, :old_path,
                'pending', :priority, :source,
                0, :max_attempts,
                :now
            )
            """,
            {
                "job_id": job_id,
                "file_path": file_path,
                "action": action,
                "old_path": old_path,
                "priority": priority,
                "source": source,
                "max_attempts": self._retry_max_attempts,
                "now": now,
            },
        )
        self._conn.commit()
        logger.debug(f"Job enqueued : {job_id} | {action} | {file_path}")
        return job_id

    # ─────────────────────────────────────────────────────────────────────
    # Dequeue
    # ─────────────────────────────────────────────────────────────────────

    def dequeue(self) -> Optional[Job]:
        """
        Prend le prochain job disponible dans la file.

        Sélectionne le job pending avec la priorité la plus haute et la date
        de création la plus ancienne, en tenant compte du `next_retry_at`.
        Utilise BEGIN IMMEDIATE pour sérialiser l'accès même en cas de
        workers multiples.

        Returns:
            Job à traiter, ou None si la queue est vide.
        """
        now = datetime.utcnow().isoformat()

        try:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'pending'
                      AND (next_retry_at IS NULL OR next_retry_at <= :now)
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                    """,
                    {"now": now},
                ).fetchone()

                if row is None:
                    return None

                self._conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'in_progress',
                        started_at = :now,
                        attempts = attempts + 1
                    WHERE job_id = :job_id
                    """,
                    {"job_id": row["job_id"], "now": now},
                )

            job = _row_to_job(row)
            # Le statut dans la row est celui d'avant l'UPDATE — on le corrige
            job.status = JOB_STATUS_IN_PROGRESS
            return job

        except sqlite3.OperationalError as exc:
            logger.debug(f"dequeue : base verrouillée ({exc})")
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Finalisation
    # ─────────────────────────────────────────────────────────────────────

    def complete(self, job_id: str) -> None:
        """
        Marque un job comme terminé avec succès.

        Args:
            job_id: Identifiant du job.
        """
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'completed',
                completed_at = :now
            WHERE job_id = :job_id
            """,
            {"job_id": job_id, "now": now},
        )
        self._conn.commit()
        logger.debug(f"Job completed : {job_id}")

    def fail(self, job_id: str, error: str) -> None:
        """
        Marque un job en échec et calcule la prochaine tentative (backoff).

        Si le nombre maximum de tentatives est atteint, appelle `mark_dead()`.

        Args:
            job_id: Identifiant du job.
            error: Message d'erreur à persister.
        """
        row = self._conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()

        if row is None:
            logger.warning(f"fail() : job introuvable : {job_id}")
            return

        attempts = row["attempts"]
        max_attempts = row["max_attempts"]

        if attempts >= max_attempts:
            self.mark_dead(job_id)
            return

        # Calculer le délai de backoff exponentiel
        delay = min(
            self._retry_backoff_base * (RETRY_BACKOFF_MULTIPLIER ** (attempts - 1)),
            RETRY_MAX_BACKOFF,
        )
        next_retry = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()

        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                error_message = :error,
                next_retry_at = :next_retry
            WHERE job_id = :job_id
            """,
            {"job_id": job_id, "error": error, "next_retry": next_retry},
        )
        self._conn.commit()
        logger.warning(
            f"Job failed : {job_id} | tentative {attempts}/{max_attempts} "
            f"| prochaine tentative dans {delay:.0f}s"
        )

    def mark_dead(self, job_id: str) -> None:
        """
        Passe un job en dead-letter (statut 'dead').

        Le job n'est plus traité automatiquement. Il reste visible dans l'API
        et peut être relancé manuellement via `retry_job()`.

        Args:
            job_id: Identifiant du job.
        """
        self._conn.execute(
            "UPDATE jobs SET status = 'dead' WHERE job_id = ?",
            (job_id,),
        )
        self._conn.commit()
        logger.error(f"Job dead-lettered : {job_id}")

    # ─────────────────────────────────────────────────────────────────────
    # Consultation
    # ─────────────────────────────────────────────────────────────────────

    def get_pending_count(self) -> int:
        """Retourne le nombre de jobs en attente de traitement."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'failed')",
        ).fetchone()
        return row[0] if row else 0

    def get_failed_jobs(self, limit: int = 50) -> list[Job]:
        """
        Retourne les jobs en échec (statut 'failed' ou 'dead').

        Args:
            limit: Nombre maximum de résultats.

        Returns:
            Liste de Job.
        """
        rows = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('failed', 'dead')
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_job(r) for r in rows]

    def get_job(self, job_id: str) -> Optional[Job]:
        """
        Récupère un job par son identifiant.

        Args:
            job_id: UUID du job.

        Returns:
            Job ou None.
        """
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Job]:
        """
        Retourne une liste paginée de jobs.

        Args:
            status: Filtre sur le statut (None = tous les statuts).
            limit: Nombre maximum de résultats.
            offset: Décalage pour la pagination.

        Returns:
            Liste de Job.
        """
        if status:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs WHERE status = ?
                ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def get_counts_by_status(self) -> dict[str, int]:
        """Retourne le nombre de jobs par statut."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status",
        ).fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    # ─────────────────────────────────────────────────────────────────────
    # Actions
    # ─────────────────────────────────────────────────────────────────────

    def retry_job(self, job_id: str) -> None:
        """
        Remet un job en statut 'pending' pour une nouvelle tentative.

        Utilisable sur des jobs 'failed', 'dead' ou 'error'.

        Args:
            job_id: UUID du job à relancer.
        """
        self._conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                next_retry_at = NULL,
                error_message = NULL
            WHERE job_id = ?
            """,
            (job_id,),
        )
        self._conn.commit()
        logger.info(f"Job relancé manuellement : {job_id}")

    def retry_all_failed(self) -> int:
        """
        Remet tous les jobs 'failed' et 'dead' en statut 'pending'.

        Returns:
            Nombre de jobs relancés.
        """
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                next_retry_at = NULL,
                error_message = NULL
            WHERE status IN ('failed', 'dead')
            """
        )
        self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info(f"{count} job(s) en erreur relancés")
        return count

    def cancel_pending_for_path(
        self,
        file_path: str,
        action: Optional[str] = None,
    ) -> int:
        """
        Annule tous les jobs 'pending' pour un chemin donné.

        Utilisé par le debounce pour remplacer les événements redondants.

        Args:
            file_path: Chemin du fichier.
            action: Si fourni, filtre uniquement les jobs de cette action.

        Returns:
            Nombre de jobs annulés.
        """
        if action:
            cursor = self._conn.execute(
                """
                DELETE FROM jobs
                WHERE file_path = ? AND action = ? AND status = 'pending'
                """,
                (file_path, action),
            )
        else:
            cursor = self._conn.execute(
                "DELETE FROM jobs WHERE file_path = ? AND status = 'pending'",
                (file_path,),
            )
        self._conn.commit()
        return cursor.rowcount

    def recover_in_progress(self) -> int:
        """
        Au démarrage du worker, remet en 'pending' les jobs bloqués en
        'in_progress' (worker crashé pendant le traitement précédent).

        Returns:
            Nombre de jobs récupérés.
        """
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET status = 'pending',
                started_at = NULL
            WHERE status = 'in_progress'
            """
        )
        self._conn.commit()
        count = cursor.rowcount
        if count:
            logger.info(f"Reprise après crash : {count} job(s) 'in_progress' remis en 'pending'")
        return count

    def close(self) -> None:
        """Ferme la connexion SQLite."""
        try:
            self._conn.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────


def _row_to_job(row: sqlite3.Row) -> Job:
    """Convertit une ligne SQLite en objet Job."""
    return Job(
        job_id=row["job_id"],
        file_path=row["file_path"],
        action=row["action"],
        old_path=row["old_path"],
        status=row["status"],
        priority=row["priority"],
        source=row["source"] or "watcher",
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        next_retry_at=row["next_retry_at"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )
