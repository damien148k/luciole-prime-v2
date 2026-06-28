"""
Ingestion Pipeline - Complete document ingestion workflow
Avec support de reprise après interruption
"""

# NOTE : IngestionTracker JSON supprimé (v2).
# L'état d'indexation est géré exclusivement par le StateStore SQLite du watcher.

import os
import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import List, Dict, Optional, Set
from datetime import datetime
import yaml
from loguru import logger

from .parsers import DocumentParser
from .chunker import Chunker, Chunk
from .embedder import Embedder

# Extensions d'images supportées pour indexation des métadonnées
IMAGE_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', 
    '.svg', '.eps', '.ai', '.psd', '.ico', '.webp'
}

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from opensearchpy import OpenSearch


class IngestionPipeline:
    """
    Complete ingestion pipeline:
    1. Parse documents
    2. Chunk text
    3. Generate embeddings
    4. Index in Qdrant (dense) and OpenSearch (BM25)
    """
    
    def __init__(self, config_path: str = "config/settings.yaml", custom_params: dict = None, index_name: str = None, enable_tracking: bool = True):
        """
        Initialize pipeline with configuration
        
        Args:
            config_path: Path to settings.yaml
            custom_params: Optional dict to override config values
                - chunk_size, chunk_overlap, batch_size, etc.
            index_name: Nom de l'index à utiliser (remplace le nom par défaut)
                        Si None, utilise le nom défini dans settings.yaml
            enable_tracking: Activer le tracking des fichiers indexés pour reprise
        """
        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        
        # Appliquer le nom d'index personnalisé si fourni
        if index_name:
            # Nettoyer le nom (remplacer caractères spéciaux)
            safe_name = self._sanitize_index_name(index_name)
            self.config["qdrant"]["collection_name"] = safe_name
            self.config["opensearch"]["index_name"] = safe_name.lower()  # OpenSearch requiert lowercase
            logger.info(f"Utilisation de l'index personnalisé: {safe_name}")
        
        # Initialiser le tracker pour la reprise
        self.enable_tracking = enable_tracking
        self.tracker = None
        if enable_tracking:
            final_index_name = self.config["qdrant"]["collection_name"]
            self.tracker = IngestionTracker(final_index_name)
            logger.info(f"Tracking activé pour index: {final_index_name}")
        
        # Apply custom params if provided
        if custom_params:
            if "chunk_size" in custom_params:
                self.config["chunking"]["chunk_size"] = custom_params["chunk_size"]
            # Accepter "overlap" OU "chunk_overlap" pour compatibilité avec l'UI
            if "overlap" in custom_params:
                self.config["chunking"]["chunk_overlap"] = custom_params["overlap"]
            elif "chunk_overlap" in custom_params:
                self.config["chunking"]["chunk_overlap"] = custom_params["chunk_overlap"]
            if "batch_size" in custom_params:
                self.config["embedding"]["batch_size"] = custom_params["batch_size"]
        
        # Initialize components
        self.parser = DocumentParser(pdf_config=self.config.get("pdf", {}))
        
        self.chunker = Chunker(
            chunk_size=self.config["chunking"]["chunk_size"],
            chunk_overlap=self.config["chunking"]["chunk_overlap"],
            strategy=self.config["chunking"]["strategy"],
            include_file_context=self.config["chunking"].get("include_file_context", True)
        )
        
        self.embedder = Embedder(
            model_name=self.config["embedding"]["model"],
            device=self.config["embedding"]["device"],
            batch_size=self.config["embedding"]["batch_size"]
        )
        
        # Initialize vector stores
        self._init_qdrant()
        self._init_opensearch()
        
        # Lock pour serialiser les swaps d'index lors d'appels ingest_file
        # avec un override `index_name`. Le watcher est single-threaded,
        # mais ce lock garantit la coherence si plusieurs threads partagent
        # un pipeline.
        self._index_swap_lock = threading.RLock()

        logger.info("Ingestion pipeline initialized")
    

    @contextmanager
    def _override_index(self, index_name):
        """
        Context manager : remplace temporairement la collection Qdrant et
        l'index OpenSearch utilises par self.config, et garantit que les
        structures existent (creation idempotente via _init_qdrant /
        _init_opensearch).

        Si `index_name` est None ou vide, c'est un no-op (config inchangee).
        A la sortie, les valeurs d'origine sont restaurees, meme en cas
        d'exception.

        Le lock `_index_swap_lock` serialise les swaps pour eviter qu'un
        thread concurrent ne voie une config intermediaire incoherente.
        """
        if not index_name:
            yield
            return

        safe_name = self._sanitize_index_name(index_name)
        qdrant_target = safe_name
        opensearch_target = safe_name.lower()

        with self._index_swap_lock:
            old_qdrant = self.config["qdrant"]["collection_name"]
            old_opensearch = self.config["opensearch"]["index_name"]

            already_on_target = (
                old_qdrant == qdrant_target
                and old_opensearch == opensearch_target
            )

            try:
                if not already_on_target:
                    self.config["qdrant"]["collection_name"] = qdrant_target
                    self.config["opensearch"]["index_name"] = opensearch_target
                    logger.debug(
                        f"Pipeline : swap index -> qdrant='{qdrant_target}', "
                        f"opensearch='{opensearch_target}'"
                    )
                # Creation idempotente des structures cibles si manquantes
                self._init_qdrant()
                self._init_opensearch()
                yield
            finally:
                if not already_on_target:
                    self.config["qdrant"]["collection_name"] = old_qdrant
                    self.config["opensearch"]["index_name"] = old_opensearch
                    logger.debug(
                        f"Pipeline : restore index -> qdrant='{old_qdrant}', "
                        f"opensearch='{old_opensearch}'"
                    )

    def _sanitize_index_name(self, name: str) -> str:
        """
        Nettoie le nom d'index pour le rendre compatible avec Qdrant et OpenSearch.
        - Remplace les espaces par des underscores
        - Supprime les caractères spéciaux
        - Limite la longueur
        """
        import re
        # Remplacer espaces par underscores
        sanitized = name.replace(" ", "_")
        # Garder uniquement lettres, chiffres, underscores et tirets
        sanitized = re.sub(r'[^a-zA-Z0-9_\-]', '', sanitized)
        # Limiter à 64 caractères
        sanitized = sanitized[:64]
        # S'assurer qu'il ne commence pas par un chiffre ou tiret
        if sanitized and (sanitized[0].isdigit() or sanitized[0] == '-'):
            sanitized = 'idx_' + sanitized
        return sanitized or "documents"
    
    def _init_qdrant(self):
        """Initialize Qdrant client and collection"""
        # Use environment variable for Docker, fallback to config
        qdrant_url = os.environ.get("QDRANT_URL")
        if qdrant_url:
            self.qdrant = QdrantClient(url=qdrant_url)
        else:
            self.qdrant = QdrantClient(
                host=self.config["qdrant"]["host"],
                port=self.config["qdrant"]["port"]
            )
        
        collection_name = self.config["qdrant"]["collection_name"]
        
        # Create collection if not exists
        collections = self.qdrant.get_collections().collections
        exists = any(c.name == collection_name for c in collections)
        
        if not exists:
            self.qdrant.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.embedder.embedding_dim,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"Created Qdrant collection: {collection_name}")
    
    def _init_opensearch(self):
        """
        Initialize OpenSearch client and index
        Index enrichi avec chemin et nom de fichier pour recherche BM25
        """
        # Use environment variable for Docker, fallback to config
        opensearch_url = os.environ.get("OPENSEARCH_URL")
        if opensearch_url:
            # Parse URL like http://luciole-opensearch:9200
            from urllib.parse import urlparse
            parsed = urlparse(opensearch_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 9200
        else:
            host = self.config["opensearch"]["host"]
            port = self.config["opensearch"]["port"]
        
        self.opensearch = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_compress=True,
            use_ssl=False,
            verify_certs=False
        )
        
        index_name = self.config["opensearch"]["index_name"]
        
        # Create index if not exists
        if not self.opensearch.indices.exists(index=index_name):
            index_body = {
                "settings": {
                    "index": {
                        "number_of_shards": 1,
                        "number_of_replicas": 0
                    },
                    "analysis": {
                        "analyzer": {
                            "french_analyzer": {
                                "type": "french"
                            },
                            "path_analyzer": {
                                "type": "custom",
                                "tokenizer": "path_hierarchy"
                            },
                            "filename_analyzer": {
                                "type": "custom",
                                "tokenizer": "standard",
                                "filter": ["lowercase", "asciifolding"]
                            }
                        }
                    }
                },
                "mappings": {
                    "properties": {
                        "chunk_id": {"type": "keyword"},
                        "document_id": {"type": "keyword"},
                        "text": {
                            "type": "text",
                            "analyzer": "french_analyzer"
                        },
                        "text_with_context": {
                            "type": "text",
                            "analyzer": "french_analyzer"
                        },
                        "file_path": {
                            "type": "text",
                            "analyzer": "path_analyzer",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "file_name": {
                            "type": "text",
                            "analyzer": "filename_analyzer",
                            "fields": {
                                "keyword": {"type": "keyword"}
                            }
                        },
                        "metadata": {"type": "object"}
                    }
                }
            }
            self.opensearch.indices.create(index=index_name, body=index_body)
            logger.info(f"Created OpenSearch index: {index_name} (with file path/name indexing)")
    
    def _is_image_file(self, file_path: str) -> bool:
        """Vérifie si le fichier est une image"""
        ext = Path(file_path).suffix.lower()
        return ext in IMAGE_EXTENSIONS
    
    def _create_image_metadata_chunk(self, file_path: str) -> Chunk:
        """
        Crée un chunk contenant les métadonnées d'un fichier image.
        Permet de retrouver les logos/images par nom de fichier et chemin.
        Retourne un objet Chunk (dataclass) compatible avec l'embedder.
        """
        path = Path(file_path)
        file_name = path.name
        
        # Extraire les 4 derniers niveaux de dossiers (comme pour les documents)
        folder_parts = list(path.parent.parts)[-4:] if len(path.parent.parts) >= 4 else list(path.parent.parts)
        folder_path = "/".join(folder_parts)
        
        # Garder aussi parent et grandparent pour compatibilité
        parent_folder = path.parent.name
        grandparent_folder = path.parent.parent.name if path.parent.parent else ""
        
        # Extraire des mots-clés du nom de fichier (sans extension ni date)
        import re
        name_without_ext = path.stem
        # Enlever les dates au format YYYYMMDD
        name_clean = re.sub(r'^\d{8}_?', '', name_without_ext)
        # Remplacer underscores et tirets par des espaces
        name_keywords = name_clean.replace('_', ' ').replace('-', ' ')
        
        # Extraire des mots-clés des noms de dossiers
        folder_keywords = " ".join(folder_parts).replace('_', ' ').replace('-', ' ')
        # Nettoyer les numéros de dossiers (ex: "01 Logos" -> "Logos")
        folder_keywords = re.sub(r'\b\d+\s*', '', folder_keywords)
        
        # Tous les mots-clés combinés
        all_keywords = f"{name_keywords} {folder_keywords}".strip()
        
        # Créer un texte descriptif riche pour la recherche
        text = f"""Fichier image: {file_name}
Type: {path.suffix.upper().replace('.', '')} (image/graphique/visuel)
Nom: {name_keywords}
Dossier: {folder_path}
Catégorie: {grandparent_folder}
Chemin complet: {file_path}

Ce fichier est une image, un logo, ou un élément graphique du service communication.
Mots-clés: {all_keywords}
Recherche: logo charte graphique visuel image {all_keywords}"""
        
        # Texte enrichi avec contexte pour l'embedding
        text_with_context = f"[Fichier: {file_name}] [Chemin: {folder_path}] [Mots-clés: {all_keywords}] {text}"
        
        chunk_id = f"{file_name}_metadata"
        
        # Créer un objet Chunk (dataclass) au lieu d'un dict
        return Chunk(
            text=text,
            text_with_context=text_with_context,
            chunk_id=chunk_id,
            document_id=file_name,
            file_path=str(file_path),
            file_name=file_name,
            start_char=0,
            end_char=len(text),
            metadata={
                "type": "image",
                "file_name": file_name,
                "file_path": str(file_path),
                "extension": path.suffix.lower(),
                "parent_folder": parent_folder,
                "folder_path": folder_path,
                "keywords": all_keywords,
                "chunk_index": 0,
                "is_metadata_only": True
            }
        )
    
    def ingest_image_file(self, file_path: str, skip_if_indexed: bool = True, index_name: Optional[str] = None) -> Dict:
        """
        Indexe les metadonnees d'un fichier image (logo, graphique, etc.)

        Args:
            file_path: Chemin vers le fichier image
            skip_if_indexed: Si True, ignore les fichiers deja indexes
            index_name: Si fourni, indexe dans cette collection Qdrant + index
                OpenSearch a la place du defaut. Cree les structures si manquantes.

        Returns:
            Dict avec les resultats de l'indexation
        """
        if skip_if_indexed and self.tracker and self.tracker.is_indexed(file_path):
            logger.info(f"Skipping already indexed image: {file_path}")
            return {"status": "skipped", "file": file_path, "chunks": 0, "reason": "already_indexed"}

        logger.info(f"Indexing image metadata: {file_path}")

        try:
            with self._override_index(index_name):
                chunk = self._create_image_metadata_chunk(file_path)
                embedded_chunks = self.embedder.embed_chunks([chunk])
                self._index_qdrant(embedded_chunks)
                self._index_opensearch(embedded_chunks)

                if self.tracker:
                    self.tracker.mark_indexed(file_path, 1)

                result = {
                    "status": "success",
                    "file": file_path,
                    "chunks": 1,
                    "document_id": chunk.document_id,
                    "type": "image_metadata",
                    "index_name": self.config["qdrant"]["collection_name"],
                }

                logger.info(f"Image metadata indexed: {result}")
                return result

        except Exception as e:
            logger.error(f"Failed to index image metadata {file_path}: {e}")
            return {"status": "error", "file": file_path, "error": str(e)}

    def ingest_file(self, file_path: str, skip_if_indexed: bool = True, index_name: Optional[str] = None) -> Dict:
        """
        Ingest a single file (document ou image)

        Args:
            file_path: Path to the file
            skip_if_indexed: Si True, ignore les fichiers deja indexes (pour reprise)
            index_name: Si fourni, indexe dans cette collection Qdrant + index
                OpenSearch a la place du defaut. Cree les structures si manquantes.
                Sanitise via `_sanitize_index_name` (memes regles que __init__).

        Returns:
            Dict with ingestion results
        """
        if skip_if_indexed and self.tracker and self.tracker.is_indexed(file_path):
            logger.info(f"Skipping already indexed file: {file_path}")
            return {"status": "skipped", "file": file_path, "chunks": 0, "reason": "already_indexed"}

        # Traitement special pour les images (metadonnees uniquement)
        if self._is_image_file(file_path):
            return self.ingest_image_file(
                file_path,
                skip_if_indexed=False,  # deja verifie ci-dessus
                index_name=index_name,
            )

        logger.info(f"Ingesting file: {file_path}")

        with self._override_index(index_name):
            document = self.parser.parse(file_path)
            chunks = self.chunker.chunk(document)

            if not chunks:
                logger.warning(f"No chunks generated for: {file_path}")
                if self.tracker:
                    self.tracker.mark_indexed(file_path, 0)
                return {"status": "empty", "file": file_path, "chunks": 0}

            embedded_chunks = self.embedder.embed_chunks(chunks)

            logger.info(
                f"Indexation Qdrant ({self.config['qdrant']['collection_name']}): "
                f"{len(embedded_chunks)} points..."
            )
            self._index_qdrant(embedded_chunks)

            logger.info(
                f"Indexation OpenSearch ({self.config['opensearch']['index_name']}): "
                f"{len(embedded_chunks)} documents..."
            )
            self._index_opensearch(embedded_chunks)

            if self.tracker:
                self.tracker.mark_indexed(file_path, len(chunks))

            result = {
                "status": "success",
                "file": file_path,
                "chunks": len(chunks),
                "document_id": document["metadata"].get("file_name"),
                "index_name": self.config["qdrant"]["collection_name"],
            }

            logger.info(f"Ingestion complete: {result}")
            return result

    def ingest_directory(self, dir_path: str, recursive: bool = True, resume: bool = True, include_images: bool = True) -> List[Dict]:
        """
        Ingest all supported files in a directory
        
        Args:
            dir_path: Path to directory
            recursive: Whether to search subdirectories
            resume: Si True, ignore les fichiers déjà indexés (reprise automatique)
            include_images: Si True, indexe aussi les métadonnées des fichiers images
            
        Returns:
            List of ingestion results
        """
        results = []
        path = Path(dir_path)
        
        pattern = "**/*" if recursive else "*"
        
        # Extensions supportées (documents + images si activé)
        supported_doc_extensions = set(self.parser.get_supported_extensions())
        supported_extensions = supported_doc_extensions.copy()
        if include_images:
            supported_extensions.update(IMAGE_EXTENSIONS)
        
        # Collecter tous les fichiers
        all_files = []
        for file_path in path.glob(pattern):
            if file_path.is_file():
                ext = file_path.suffix.lower()
                if ext in supported_extensions:
                    all_files.append(str(file_path))
        
        # Filtrer les fichiers déjà indexés si reprise activée
        if resume and self.tracker:
            pending_files = self.tracker.get_pending_files(all_files)
            skipped = len(all_files) - len(pending_files)
            if skipped > 0:
                logger.info(f"Reprise: {skipped} fichiers déjà indexés, {len(pending_files)} restants")
            files_to_process = pending_files
        else:
            files_to_process = all_files
        
        for file_path in files_to_process:
            try:
                result = self.ingest_file(file_path, skip_if_indexed=resume)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to ingest {file_path}: {e}")
                results.append({
                    "status": "error",
                    "file": file_path,
                    "error": str(e)
                })
        
        return results
    
    def get_tracking_stats(self) -> Dict:
        """Retourne les statistiques du tracker"""
        if self.tracker:
            return self.tracker.get_stats()
        return {"tracking": "disabled"}
    
    def reset_tracking(self):
        """Réinitialise le tracker pour réindexer tout"""
        if self.tracker:
            self.tracker.reset()
            logger.info("Tracking reset - all files will be reindexed")
    
    def _index_qdrant(self, embedded_chunks: List[Dict], batch_size: int = 100):
        """
        Index chunks in Qdrant avec métadonnées enrichies (par batch)
        Inclut: chemin, nom de fichier, texte brut et texte contextualisé
        """
        collection_name = self.config["qdrant"]["collection_name"]
        
        points = []
        for i, chunk in enumerate(embedded_chunks):
            point = PointStruct(
                id=hash(chunk["chunk_id"]) % (10**18),  # Convert to positive int
                vector=chunk["embedding"],
                payload={
                    "chunk_id": chunk["chunk_id"],
                    "document_id": chunk["document_id"],
                    "text": chunk["text"],                          # Texte brut pour affichage
                    "text_with_context": chunk["text_with_context"], # Texte avec contexte fichier
                    "file_path": chunk["file_path"],                 # Chemin complet du fichier
                    "file_name": chunk["file_name"],                 # Nom du fichier
                    "metadata": chunk["metadata"]
                }
            )
            points.append(point)
        
        # Upsert par batch pour éviter les dépassements de taille gRPC
        total = len(points)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = points[start:end]
            try:
                self.qdrant.upsert(
                    collection_name=collection_name,
                    points=batch
                )
                logger.info(f"Qdrant: indexé batch {start+1}-{end}/{total} points")
            except Exception as e:
                logger.error(f"Qdrant: erreur batch {start+1}-{end}/{total}: {e}")
                raise
        
        logger.info(f"Qdrant: {total} points indexés avec succès")
    
    def _index_opensearch(self, embedded_chunks: List[Dict], batch_size: int = 100):
        """
        Index chunks in OpenSearch for BM25 (par bulk batch)
        Inclut chemin et nom de fichier pour recherche lexicale enrichie
        """
        index_name = self.config["opensearch"]["index_name"]
        total = len(embedded_chunks)
        
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = embedded_chunks[start:end]
            
            try:
                # Utiliser le bulk API pour plus d'efficacité
                actions = []
                for chunk in batch:
                    action = {"index": {"_index": index_name, "_id": chunk["chunk_id"]}}
                    doc = {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": chunk["document_id"],
                        "text": chunk["text"],
                        "text_with_context": chunk["text_with_context"],
                        "file_path": chunk["file_path"],
                        "file_name": chunk["file_name"],
                        "metadata": chunk["metadata"]
                    }
                    actions.append(action)
                    actions.append(doc)
                
                if actions:
                    response = self.opensearch.bulk(body=actions)
                    if response.get("errors"):
                        error_items = [item for item in response["items"] if "error" in item.get("index", {})]
                        logger.warning(f"OpenSearch: {len(error_items)} erreurs dans batch {start+1}-{end}")
                
                logger.info(f"OpenSearch: indexé batch {start+1}-{end}/{total} documents")
            except Exception as e:
                logger.error(f"OpenSearch: erreur batch {start+1}-{end}/{total}: {e}")
                raise
        
        # Refresh index for immediate search
        self.opensearch.indices.refresh(index=index_name)
        logger.info(f"OpenSearch: {total} documents indexés avec succès")
    
    def get_stats(self) -> Dict:
        """Get pipeline statistics"""
        collection_name = self.config["qdrant"]["collection_name"]
        index_name = self.config["opensearch"]["index_name"]
        
        qdrant_count = self.qdrant.count(collection_name=collection_name).count
        
        opensearch_count = self.opensearch.count(index=index_name)["count"]
        
        return {
            "qdrant_vectors": qdrant_count,
            "opensearch_documents": opensearch_count,
            "embedding_model": self.config["embedding"]["model"],
            "chunk_size": self.config["chunking"]["chunk_size"]
        }

class IngestionTracker:
    """
    Tracker léger pour la reprise d'ingestion.
    Stocke l'état dans un fichier SQLite par index.
    Remplace l'ancien tracker JSON supprimé en v2.
    """

    def __init__(self, index_name: str, db_dir: str = "/app/data"):
        import sqlite3
        from pathlib import Path
        self.index_name = index_name
        db_path = Path(db_dir) / f"tracker_{index_name}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self._init_db()

    def _init_db(self):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    file_path TEXT PRIMARY KEY,
                    chunks INTEGER DEFAULT 0,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def is_indexed(self, file_path: str) -> bool:
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM indexed_files WHERE file_path = ?", (str(file_path),)
            )
            return cur.fetchone() is not None

    def mark_indexed(self, file_path: str, chunks: int = 0):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO indexed_files (file_path, chunks, indexed_at)
                   VALUES (?, ?, CURRENT_TIMESTAMP)""",
                (str(file_path), chunks)
            )
            conn.commit()

    def get_pending_files(self, files: list) -> list:
        """Retourne les fichiers non encore indexés."""
        return [f for f in files if not self.is_indexed(f)]

    def get_stats(self) -> dict:
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("SELECT file_path FROM indexed_files")
            indexed = [row[0] for row in cur.fetchall()]
        return {"indexed_files": indexed, "count": len(indexed)}

    def reset(self):
        import sqlite3
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM indexed_files")
            conn.commit()
        logger.info(f"IngestionTracker reset pour index: {self.index_name}")
