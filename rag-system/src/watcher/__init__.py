"""
Watcher — Service de surveillance de fichiers pour Luciole Prime.

Détecte les changements dans les dossiers surveillés et déclenche
la mise à jour incrémentale de l'index RAG (Qdrant + OpenSearch).

Composants principaux :
- FileWatcher   : surveillance du filesystem (watchdog PollingObserver)
- JobQueue      : file d'attente persistante (SQLite)
- IndexWorker   : traitement des jobs d'indexation
- StateStore    : état des documents indexés
- Reconciler    : scan périodique de réconciliation
- WatcherService: orchestrateur principal
"""

from .service import WatcherService
from .config import WatcherConfig, WatchedPath
from .models import DocumentState, Job, WatcherEvent, DocumentStatus, JobStatus
from .exceptions import (
    WatcherError,
    FileNotStableError,
    IndexingError,
    CleanupError,
    ConfigurationError,
)

__all__ = [
    "WatcherService",
    "WatcherConfig",
    "WatchedPath",
    "DocumentState",
    "Job",
    "WatcherEvent",
    "DocumentStatus",
    "JobStatus",
    "WatcherError",
    "FileNotStableError",
    "IndexingError",
    "CleanupError",
    "ConfigurationError",
]
