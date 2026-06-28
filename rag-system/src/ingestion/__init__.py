# Ingestion Module
# Imports différés pour éviter de charger toutes les dépendances au démarrage

def __getattr__(name):
    """Import différé des composants"""
    if name == "DocumentParser":
        from .parsers import DocumentParser
        return DocumentParser
    elif name == "Chunker":
        from .chunker import Chunker
        return Chunker
    elif name == "Embedder":
        from .embedder import Embedder
        return Embedder
    elif name == "IngestionPipeline":
        from .pipeline import IngestionPipeline
        return IngestionPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["DocumentParser", "Chunker", "Embedder", "IngestionPipeline"]

